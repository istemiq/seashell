"""
Speaking Practice: voice/text chat with an AI coach (GigaChat).

Logic:
  - Menu: start practice, speech speed settings, end session.
  - State: user_speaking_state[chat_id] = { speed, conversation_history, chat_id }.
  - Incoming voice → download, transcribe (Whisper), then same path as text.
  - Incoming text → build messages from system prompt + history + new message → GigaChat → reply text + TTS voice.
"""

import os
import re
import subprocess
import tempfile
import logging
import io
import requests
import urllib3
from telebot import types
import uuid

logger = logging.getLogger(__name__)

# Whisper: loaded on first voice message
_whisper_model = None
WHISPER_MODEL_SIZE = "base"
WHISPER_LANGUAGE = "en"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

GIGACHAT_API_KEY = os.getenv("GIGACHAT_API_KEY")
GIGACHAT_MODEL_NAME = os.getenv("GIGACHAT_MODEL_NAME")

# Active sessions: chat_id -> { speed, conversation_history, chat_id }
user_speaking_state = {}

SPEECH_SPEEDS = {"slow": "Slow", "normal": "Normal", "fast": "Fast"}

# Keep last N messages in history for API context window
MAX_HISTORY_MESSAGES = 20
MAX_TTS_CHARS = 4000
# Shorter text for voice = fewer gTTS requests and faster upload
MAX_VOICE_TTS_CHARS = 450


# ---------------------------------------------------------------------------
# GigaChat: OAuth token and prompt file
# ---------------------------------------------------------------------------

def get_access_token():
    """Get GigaChat OAuth token (Basic base64 client_id:secret). Returns None on error."""
    if not GIGACHAT_API_KEY:
        logger.error("GIGACHAT_API_KEY не задан в окружении")
        return None

    headers = {
        "Authorization": f"Basic {GIGACHAT_API_KEY}",
        "RqUID": str(uuid.uuid4()),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = {"scope": "GIGACHAT_API_PERS"}

    try:
        logger.debug("Запрос access_token к GigaChat OAuth...")
        response = requests.post(
            "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
            headers=headers,
            data=body,
            verify=False,
        )
        if response.status_code == 200:
            result = response.json()
            token = result.get("access_token")
            logger.info("access_token GigaChat успешно получен")
            return token
        logger.error(
            "Ошибка получения токена GigaChat: status=%s, body=%s",
            response.status_code,
            response.text[:200],
        )
        return None
    except Exception as e:
        logger.exception("Исключение при запросе токена GigaChat: %s", e)
        return None


def read_prompt(prompt_file_name):
    """Load system prompt from prompts/<prompt_file_name>. Returns None if missing."""
    project_root = os.path.dirname(os.path.abspath(__file__))
    prompt_path = os.path.join(project_root, "prompts", prompt_file_name)
    logger.debug("Чтение промпта: %s", prompt_path)

    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        logger.info("Промпт '%s' загружен, длина=%s символов", prompt_file_name, len(content))
        return content
    except FileNotFoundError:
        logger.error("Файл промпта не найден: %s", prompt_path)
        return None
    except Exception as e:
        logger.exception("Ошибка при чтении промпта '%s': %s", prompt_file_name, e)
        return None


# ---------------------------------------------------------------------------
# TTS: text → OGG/OPUS voice (gTTS + ffmpeg)
# ---------------------------------------------------------------------------

def _sanitize_text_for_tts(text):
    """Single line, no control chars, max MAX_TTS_CHARS."""
    if not text:
        return ""
    t = " ".join(str(text).split())
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", t)[:MAX_TTS_CHARS]


def _text_to_voice_ogg(text, speed):
    """
    Build temp .ogg with gTTS + ffmpeg (libopus).
    Returns (path_ogg, None) on success, (None, error_message) on failure.
    Caller must unlink path_ogg when done.
    """
    text = _sanitize_text_for_tts(text)
    if not text:
        return None, "empty text"
    try:
        from gtts import gTTS
        slow = speed == "slow"
        fd_mp3, path_mp3 = tempfile.mkstemp(suffix=".mp3")
        os.close(fd_mp3)
        tts = gTTS(text=text, lang="en", slow=slow)
        tts.save(path_mp3)

        fd_ogg, path_ogg = tempfile.mkstemp(suffix=".ogg")
        os.close(fd_ogg)
        # On Windows use a single quoted command so PATH and paths with spaces work
        if os.name == "nt":
            cmd = f'ffmpeg -y -i "{path_mp3}" -acodec libopus -b:a 64k "{path_ogg}"'
            ret = subprocess.run(cmd, shell=True, capture_output=True, timeout=60, text=False)
        else:
            ret = subprocess.run(
                ["ffmpeg", "-y", "-i", path_mp3, "-acodec", "libopus", "-b:a", "64k", path_ogg],
                capture_output=True,
                timeout=60,
            )
        try:
            os.unlink(path_mp3)
        except OSError:
            pass
        if ret.returncode != 0:
            err = (ret.stderr or b"").decode("utf-8", errors="replace")[:300]
            logger.warning("ffmpeg TTS failed (code=%s): %s", ret.returncode, err)
            try:
                os.unlink(path_ogg)
            except OSError:
                pass
            return None, err or f"ffmpeg exit code {ret.returncode}"
        return path_ogg, None
    except FileNotFoundError as e:
        logger.warning("ffmpeg or gTTS not found: %s", e)
        return None, "ffmpeg or gTTS not found"
    except Exception as e:
        logger.exception("TTS error: %s", e)
        return None, str(e)


def send_voice_reply(bot, chat_id, text, speed):
    """Send one voice message (TTS). Returns True if sent, False if skipped or failed."""
    speed = speed or "normal"
    text_for_voice = _sanitize_text_for_tts(text)[:MAX_VOICE_TTS_CHARS]
    path, err = _text_to_voice_ogg(text_for_voice, speed)
    if not path and text_for_voice:
        path, err = _text_to_voice_ogg(text_for_voice[:300], speed)
    if not path:
        logger.warning("TTS produced no file for chat_id=%s: %s", chat_id, err)
        return False
    try:
        with open(path, "rb") as f:
            ogg_bytes = f.read()
        try:
            os.unlink(path)
        except OSError:
            pass
        # Long timeout for uploading voice file (TTS can be 100–500 KB; slow links need time)
        bot.send_voice(chat_id, voice=io.BytesIO(ogg_bytes), timeout=90)
        logger.info("Voice reply sent to chat_id=%s", chat_id)
        return True
    except Exception as e:
        logger.warning("Failed to send voice to chat_id=%s: %s", chat_id, e)
        try:
            os.unlink(path)
        except OSError:
            pass
        return False


# ---------------------------------------------------------------------------
# ASR: download voice from Telegram → transcribe with Whisper
# ---------------------------------------------------------------------------

def _get_whisper_model():
    """Lazy-load faster-whisper model (once per process)."""
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            logger.info("Загрузка модели Whisper: size=%s, device=cpu", WHISPER_MODEL_SIZE)
            _whisper_model = WhisperModel(
                WHISPER_MODEL_SIZE, device="cpu", compute_type="int8"
            )
            logger.info("Модель Whisper успешно загружена")
        except Exception as e:
            logger.exception("Ошибка загрузки модели Whisper: %s", e)
    return _whisper_model


def download_voice_to_file(bot, file_id):
    """Download voice to temp .ogg. Caller must unlink after use."""
    try:
        logger.debug("Скачивание голосового сообщения file_id=%s", file_id)
        file_info = bot.get_file(file_id)
        data = bot.download_file(file_info.file_path)
        fd, path = tempfile.mkstemp(suffix=".ogg")
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        logger.debug("Голос сохранён во временный файл: %s, размер=%s байт", path, len(data))
        return path
    except Exception as e:
        logger.exception("Ошибка скачивания голосового сообщения: %s", e)
        return None


def transcribe_voice_file(audio_path):
    """Transcribe .ogg to text (Whisper). Deletes audio_path in finally."""
    model = _get_whisper_model()
    if model is None:
        logger.warning("Модель Whisper недоступна, транскрипция пропущена")
        return None
    try:
        logger.debug("Транскрипция файла: %s, язык=%s", audio_path, WHISPER_LANGUAGE)
        segments, info = model.transcribe(
            audio_path, language=WHISPER_LANGUAGE, beam_size=1
        )
        text = " ".join(s.text for s in segments).strip()
        if text:
            logger.info("Транскрипция успешна, длина текста=%s", len(text))
        else:
            logger.warning("Транскрипция вернула пустой текст")
        return text if text else None
    except Exception as e:
        logger.exception("Ошибка транскрипции: %s", e)
        return None
    finally:
        try:
            os.unlink(audio_path)
            logger.debug("Временный аудиофайл удалён: %s", audio_path)
        except OSError as err:
            logger.warning("Не удалось удалить временный файл %s: %s", audio_path, err)


def recognize_voice_message(bot, message):
    """Download voice → transcribe → return text. Sends status/error messages to user."""
    file_id = message.voice.file_id
    chat_id = message.chat.id
    user_id = message.from_user.id

    logger.info("Начало распознавания голоса для user_id=%s, chat_id=%s", user_id, chat_id)
    bot.send_message(chat_id, "Recognizing speech...")

    path = download_voice_to_file(bot, file_id)
    if not path:
        logger.warning("Не удалось скачать голосовое для user_id=%s", user_id)
        bot.send_message(chat_id, "Could not download the voice message.")
        return None

    text = transcribe_voice_file(path)
    if text is None:
        logger.warning("Распознавание речи не дало результата для user_id=%s", user_id)
        bot.send_message(
            chat_id,
            "Could not recognize speech. Try again or type your message.",
        )
        return None

    logger.debug("Распознанный текст для user_id=%s: %s", user_id, text[:100])
    return text


# ---------------------------------------------------------------------------
# Speaking Practice UI and session lifecycle
# ---------------------------------------------------------------------------

def show_speaking_menu(bot, message):
    """Show Start practice / Speech speed; we use chat_id as user id for state."""
    user_id = message.chat.id
    logger.info("Показ меню Speaking Practice для user_id/chat_id=%s", user_id)

    markup = types.InlineKeyboardMarkup()
    btn_start = types.InlineKeyboardButton("Start practice", callback_data="speaking_start")
    markup.add(btn_start)
    markup.add(
        types.InlineKeyboardButton(
            "Speech speed settings", callback_data="speaking_speed_settings"
        )
    )
    markup.add(
        types.InlineKeyboardButton("← Back", callback_data="restart"),
        types.InlineKeyboardButton("Main menu", callback_data="restart"),
    )

    bot.reply_to(
        message,
        "SPEAKING PRACTICE\n\nTap the button to start.",
        reply_markup=markup,
    )
    logger.debug("Меню Speaking отправлено user_id=%s", user_id)


def handle_speaking_callback(bot, call):
    """Route: speaking_start, speaking_speed_*, speaking_end."""
    user_id = call.from_user.id
    data = call.data
    logger.info("Обработка speaking callback user_id=%s: %s", user_id, data)

    if data == "speaking_start":
        start_speaking_practice(bot, call.message)
    elif data == "speaking_speed_settings":
        show_speed_settings(bot, call.message)
    elif data.startswith("speaking_speed_"):
        speed = data.split("_")[2]
        logger.debug("Смена скорости речи для user_id=%s на %s", user_id, speed)
        change_speech_speed(bot, call.message, speed)
    elif data == "speaking_end":
        end_speaking_practice(bot, call.message)


def show_speed_settings(bot, message):
    """Show Slow / Normal / Fast and Back. Explains that speed affects voice (TTS) only."""
    markup = types.InlineKeyboardMarkup()
    for speed, name in SPEECH_SPEEDS.items():
        btn = types.InlineKeyboardButton(name, callback_data=f"speaking_speed_{speed}")
        markup.add(btn)
    markup.add(types.InlineKeyboardButton("← Back", callback_data="speaking"))
    markup.add(types.InlineKeyboardButton("← Main menu", callback_data="restart"))

    text = (
        "Speech speed for voice messages:\n"
        "• Slow = clearer, easier to follow\n"
        "• Normal / Fast = quicker\n"
        "Requires ffmpeg installed; otherwise you get text only."
    )
    bot.reply_to(message, text, reply_markup=markup)


def start_speaking_practice(bot, message):
    """Create session in user_speaking_state, send welcome + buttons, first GigaChat reply if configured."""
    user_id = message.chat.id
    chat_id = message.chat.id

    user_speaking_state[user_id] = {
        "speed": "normal",
        "conversation_history": [],
        "chat_id": chat_id,
    }
    logger.info("Сессия Speaking Practice начата: user_id/chat_id=%s", user_id)

    welcome_text = (
        "Speaking practice started.\n"
        "Send text or voice. I'll reply with text and voice (voice needs ffmpeg installed)."
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("← Main menu", callback_data="speaking_end"))
    markup.add(types.InlineKeyboardButton("Speed settings", callback_data="speaking_speed_settings"))

    bot.send_message(chat_id, welcome_text, reply_markup=markup)

    if GIGACHAT_API_KEY:
        system_prompt = read_prompt("speaking_intermediate.txt")
        if system_prompt is None:
            logger.error("Промпт speaking_intermediate.txt не загружен для user_id=%s", user_id)
            bot.send_message(
                chat_id,
                "Prompt file not found. Please add prompts/speaking_intermediate.txt",
            )
            return

        access_token = get_access_token()
        if not access_token:
            logger.warning("Не удалось получить токен GigaChat при старте сессии user_id=%s", user_id)
            bot.send_message(chat_id, "Could not get API token. Check your API keys.")
            return

        logger.debug("Отправка приветственного запроса в GigaChat для user_id=%s", user_id)
        send_gigachat_response(
            bot,
            user_id,
            system_prompt,
            "Greet the learner and suggest a topic to discuss.",
            access_token,
        )
    else:
        logger.warning("GIGACHAT_API_KEY not set — using test mode for user_id=%s", user_id)
        bot.send_message(chat_id, "GigaChat API is not set up. Using test mode.")
        simulate_gigachat_response(bot, user_id)


def end_speaking_practice(bot, message):
    """Drop session and send main menu (Vocabulary, Speaking, Progress, Settings)."""
    user_id = message.chat.id
    chat_id = message.chat.id

    if user_id in user_speaking_state:
        del user_speaking_state[user_id]
        logger.info("Сессия Speaking Practice завершена: user_id/chat_id=%s", user_id)
    else:
        logger.debug("Завершение сессии для user_id=%s — сессия не была активна", user_id)

    from main import get_main_menu_markup
    bot.send_message(
        chat_id,
        "Back to main menu.",
        reply_markup=get_main_menu_markup(),
    )


def change_speech_speed(bot, message, speed):
    """Update session speed; only works inside an active Speaking session."""
    user_id = message.chat.id
    chat_id = message.chat.id

    if user_id in user_speaking_state:
        user_speaking_state[user_id]["speed"] = speed
        logger.info("Скорость речи изменена: user_id/chat_id=%s, speed=%s", user_id, speed)
        bot.send_message(chat_id, f"Speech speed set to: {SPEECH_SPEEDS[speed]}")
    else:
        logger.debug("Попытка изменить скорость без активной сессии: user_id=%s", user_id)
        bot.send_message(chat_id, "Please start a Speaking Practice session first.")


def handle_speaking_input(bot, message):
    """Handle text/voice: ensure session (auto-start if missing), then GigaChat + voice reply."""
    user_id = message.chat.id
    chat_id = message.chat.id
    content_type = message.content_type

    logger.info(
        "Вход в handle_speaking_input: user_id=%s, chat_id=%s, content_type=%s",
        user_id,
        chat_id,
        content_type,
    )

    if user_id not in user_speaking_state:
        # Auto-start session so user doesn't have to tap menu again
        logger.info(
            "Сообщение без активной сессии Speaking, авто-запуск. user_id=%s", user_id
        )
        start_speaking_practice(bot, message)
        return

    if content_type == "voice":
        recognized_text = recognize_voice_message(bot, message)
        if recognized_text is None:
            return
        bot.send_message(chat_id, f"You said: {recognized_text}")
        user_message_for_ai = f"Пользователь сказал (голос): {recognized_text}"
    else:
        user_text = message.text or ""
        logger.debug("Текстовое сообщение от user_id=%s: %s", user_id, user_text[:100])
        user_message_for_ai = f"Пользователь написал: {user_text}"

    # Общая ветка: отправка в GigaChat или тестовый ответ
    if GIGACHAT_API_KEY:
        system_prompt = read_prompt("speaking_intermediate.txt")
        if system_prompt is None:
            logger.warning("Промпт не загружен при ответе для user_id=%s", user_id)
            bot.send_message(chat_id, "Prompt file not loaded.")
            return
        access_token = get_access_token()
        if not access_token:
            logger.warning("Токен GigaChat недоступен при ответе для user_id=%s", user_id)
            bot.send_message(chat_id, "Could not get API token. Check your API keys.")
            return
        send_gigachat_response(bot, user_id, system_prompt, user_message_for_ai, access_token)
    else:
        logger.debug("Тестовый ответ (GigaChat не настроен) для user_id=%s", user_id)
        simulate_gigachat_response(bot, user_id)


def simulate_gigachat_response(bot, user_id):
    """Fallback when GigaChat is not configured: fixed phrase + TTS."""
    state = user_speaking_state.get(user_id, {})
    chat_id = state.get("chat_id", user_id)
    speed = state.get("speed", "normal")

    logger.debug("Тестовый ответ GigaChat для user_id=%s, speed=%s", user_id, speed)

    base_response = "Hello! Let's talk. What would you like to discuss today?"
    speed_suffix = {"slow": " (speaking slowly)", "normal": "", "fast": " (speaking quickly)"}
    response = base_response + speed_suffix.get(speed, "")
    bot.send_message(chat_id, response)
    send_voice_reply(bot, chat_id, response, speed)


# ---------------------------------------------------------------------------
# GigaChat API: build messages (system + history + user), send, append to history, reply + voice
# ---------------------------------------------------------------------------

def send_gigachat_response(bot, user_id, system_prompt, user_message, access_token):
    """Request GigaChat with full context; send text + voice; append turn to conversation_history."""
    state = user_speaking_state.get(user_id, {})
    chat_id = state.get("chat_id", user_id)
    history = state.get("conversation_history", [])

    # Собираем messages: system (промпт из файла) + последние N сообщений истории + текущее user
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-MAX_HISTORY_MESSAGES:])
    messages.append({"role": "user", "content": user_message})

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    data = {
        "model": GIGACHAT_MODEL_NAME,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 500,
    }
    url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

    try:
        logger.debug(
            "Запрос к GigaChat: user_id=%s, history_len=%s, user_message_len=%s",
            user_id,
            len(history),
            len(user_message),
        )
        response = requests.post(
            url,
            headers=headers,
            json=data,
            timeout=30,
            verify=False,
        )
        logger.info("Ответ GigaChat: status=%s для user_id=%s", response.status_code, user_id)

        if response.status_code == 200:
            result = response.json()
            ai_message = result["choices"][0]["message"]["content"]
            logger.debug("Текст ответа GigaChat (начало): %s", ai_message[:100])
            bot.send_message(chat_id, ai_message)
            speed = state.get("speed", "normal")
            try:
                voice_ok = send_voice_reply(bot, chat_id, ai_message, speed)
                if not voice_ok and not user_speaking_state.get(user_id, {}).get("voice_hint_sent"):
                    bot.send_message(
                        chat_id,
                        "Voice not sent (install ffmpeg for voice replies).",
                    )
                    if user_id in user_speaking_state:
                        user_speaking_state[user_id]["voice_hint_sent"] = True
            except Exception as voice_err:
                logger.warning("Voice reply failed for chat_id=%s: %s", chat_id, voice_err)
                bot.send_message(chat_id, "Voice not sent. Check server logs.")

            # Добавляем текущий обмен в историю, чтобы следующий запрос продолжал беседу
            if user_id in user_speaking_state:
                user_speaking_state[user_id].setdefault("conversation_history", []).append(
                    {"role": "user", "content": user_message}
                )
                user_speaking_state[user_id]["conversation_history"].append(
                    {"role": "assistant", "content": ai_message}
                )
                # Оставляем только последние MAX_HISTORY_MESSAGES пар
                h = user_speaking_state[user_id]["conversation_history"]
                if len(h) > MAX_HISTORY_MESSAGES:
                    user_speaking_state[user_id]["conversation_history"] = h[-MAX_HISTORY_MESSAGES:]
                logger.debug("История обновлена, сообщений в истории: %s", len(user_speaking_state[user_id]["conversation_history"]))
        else:
            logger.error(
                "GigaChat вернул ошибку: status=%s, body=%s",
                response.status_code,
                response.text[:300],
            )
            bot.send_message(
                chat_id,
                f"Connection error ({response.status_code}). Please try again later.",
            )

    except requests.exceptions.Timeout:
        logger.error("Таймаут запроса к GigaChat для user_id=%s", user_id)
        bot.send_message(chat_id, "Connection timeout. Please try again later.")
    except Exception as e:
        logger.exception("Исключение при запросе к GigaChat: %s", e)
        bot.send_message(chat_id, f"Error: {str(e)}")