"""
Seashell Telegram bot — main entry point (start this file).

Mental model (high level):
  - Telegram sends us two kinds of events:
      1) messages (text, voice, commands like /start)
      2) callback queries (when user taps an inline keyboard button)

  - This file wires those events to the right feature modules:
      - `vocabulary.py`      -> adding/listing/studying saved words
      - `speaking_practice.py` -> chat practice (text/voice) + TTS/ASR + GigaChat
      - `settings.py`       -> user preferences (native language etc.)
      - `db.py`             -> database + persistence

Important UX rule we follow:
  - From any screen the user should be able to reach:
      - Back
      - Main menu

FAQ:
  Q: What file do I run to start the bot?
  A: Run this file: `python main.py`.

  Q: I tap buttons but Telegram shows a loading spinner forever. Why?
  A: Usually we forgot to call `bot.answer_callback_query(call.id)` for that callback.
     This file tries to answer callbacks before routing to modules.

  Q: Why is my bot "slow" sometimes?
  A: Network + Telegram API. We increased default timeouts via `telebot.apihelper.*_TIMEOUT`.
     If Telegram is blocked or unstable, responses can still be slow.

  Q: Where does the bot decide "Vocabulary vs Speaking" for normal text messages?
  A: In `handle_all_messages()`:
     - if we are in "add word" mode -> Vocabulary consumes the message
     - otherwise -> message goes to `speaking_practice.handle_speaking_input()`
"""

import os
import logging
import telebot
from telebot import types
from dotenv import load_dotenv
from i18n import t


def get_main_menu_markup(user_id: int | None = None):
    """
    Build the main menu keyboard.

    We keep this in one function so all modules can import and reuse it.
    That prevents "drifting" UIs where every module has a different main menu.
    """
    native_lang = "ru"
    if user_id is not None:
        try:
            from db import get_native_language

            native_lang = get_native_language(user_id)
        except Exception:
            native_lang = "ru"

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(t(native_lang, "main_menu_vocabulary"), callback_data="vocabulary"),
        types.InlineKeyboardButton(t(native_lang, "main_menu_speaking"), callback_data="speaking"),
    )
    markup.add(
        types.InlineKeyboardButton(t(native_lang, "main_menu_restart"), callback_data="restart"),
        types.InlineKeyboardButton(t(native_lang, "main_menu_settings"), callback_data="settings"),
    )
    return markup

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
# Configure root logging once at process start.
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Environment
# -----------------------------------------------------------------------------
# Load `.env` file from project root (if present).
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GIGACHAT_API_KEY = os.getenv("GIGACHAT_API_KEY")
GIGACHAT_MODEL_NAME = os.getenv("GIGACHAT_MODEL_NAME")

for key in ("TELEGRAM_BOT_TOKEN", "GIGACHAT_API_KEY", "GIGACHAT_MODEL_NAME"):
    logger.debug("%s: %s", key, "SET" if os.getenv(key) else "NOT SET")

if not TELEGRAM_BOT_TOKEN:
    logger.critical("TELEGRAM_BOT_TOKEN missing. Exiting.")
    exit(1)

# -----------------------------------------------------------------------------
# Bot instance
# -----------------------------------------------------------------------------
try:
    # Create the TeleBot client. This object performs all Telegram API requests.
    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
    # Increase default request timeouts.
    # This helps when Telegram is slow, blocked, or network is unstable.
    import telebot.apihelper
    telebot.apihelper.READ_TIMEOUT = 120
    telebot.apihelper.CONNECT_TIMEOUT = 60
    logger.info("Bot initialized.")
except Exception as e:
    logger.exception("Failed to init bot: %s", e)
    exit(1)

# -----------------------------------------------------------------------------
# Database: create tables on startup (vocabulary)
# -----------------------------------------------------------------------------
try:
    # Ensure DB schema exists. `db.py` will use PostgreSQL if DATABASE_URL is set,
    # otherwise it falls back to local SQLite file.
    from db import init_db
    init_db()
except Exception as e:
    logger.warning("DB init skipped or failed: %s", e)


# -----------------------------------------------------------------------------
# Handlers
# -----------------------------------------------------------------------------

@bot.message_handler(commands=["start", "restart"])
def handle_start(message):
    """
    Handle /start and /restart commands typed by the user.
    We answer by replying to that message with our main menu.
    """
    logger.info("%s from user_id=%s", message.text or "/start", message.from_user.id)
    from_user_id = message.from_user.id

    # /restart should always go directly to main menu (no onboarding gates).
    if (message.text or "").strip().lower() == "/restart":
        bot.reply_to(
            message,
            t("ru", "welcome_choose_action"),
            reply_markup=get_main_menu_markup(from_user_id),
        )
        return

    # /start: show onboarding if user never saw it.
    try:
        from db import is_onboarding_done, set_onboarding_done
    except Exception:
        is_onboarding_done = None

    if is_onboarding_done and not is_onboarding_done(from_user_id):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("1) Язык", callback_data="settings"))
        markup.add(types.InlineKeyboardButton("2) Добавить слово", callback_data="vocab_add"))
        markup.add(types.InlineKeyboardButton("3) Понял", callback_data="onboarding_done"))
        markup.add(types.InlineKeyboardButton(t("ru", "main_menu"), callback_data="restart"))
        bot.reply_to(
            message,
            "Welcome to Seashell.\n\nКоротко:\n1) Выберите родной язык в SETTINGS\n2) Add word — добавьте слово\n3) Study — бот выдаст пример + голос\n\nДавайте начнем?",
            reply_markup=markup,
        )
        return

    # /start main reply (translated).
    try:
        from db import get_native_language

        lang = get_native_language(from_user_id)
    except Exception:
        lang = "ru"
    bot.reply_to(
        message,
        t(lang, "welcome_choose_action"),
        reply_markup=get_main_menu_markup(from_user_id),
    )


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """
    Route by callback_data:
      vocabulary, vocab_* → vocabulary module
      speaking, speaking_* → speaking_practice module
      progress, settings → placeholder text
    """
    data = call.data
    logger.info("Callback user_id=%s data=%s", call.from_user.id, data)

    if data == "vocabulary":
        # Always answer callback, otherwise Telegram shows a "loading spinner".
        bot.answer_callback_query(call.id)
        from vocabulary import show_vocabulary_menu
        show_vocabulary_menu(bot, call.message)
        return

    if data.startswith("vocab_"):
        # Vocabulary module handles its own callback namespace.
        from vocabulary import handle_vocabulary_callback
        handle_vocabulary_callback(bot, call)
        return

    if data == "speaking":
        bot.answer_callback_query(call.id)
        from speaking_practice import show_speaking_menu
        show_speaking_menu(bot, call.message)
        return

    if data == "restart":
        bot.answer_callback_query(call.id)
        # When user returns to main menu we also cancel "add word" mode
        # and stop any active Speaking session (so user doesn't get "stuck").
        from vocabulary import cancel_add_word_mode
        cancel_add_word_mode(call.message.chat.id)
        # Also drop active Speaking session, if any
        try:
            from speaking_practice import user_speaking_state
            user_speaking_state.pop(call.message.chat.id, None)
        except Exception:
            pass
        # Send a clean main menu message.
        try:
            from db import get_native_language

            lang = get_native_language(call.message.chat.id)
        except Exception:
            lang = "ru"
        # Try to update the same message containing the inline keyboard.
        # This avoids confusion with "old" keyboards still visible behind.
        try:
            bot.edit_message_text(
                t(lang, "welcome_choose_action"),
                call.message.chat.id,
                call.message.message_id,
                reply_markup=get_main_menu_markup(call.message.chat.id),
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                t(lang, "welcome_choose_action"),
                reply_markup=get_main_menu_markup(call.message.chat.id),
            )
        return

    if data == "settings":
        bot.answer_callback_query(call.id)
        from settings import show_settings_menu
        show_settings_menu(bot, call.message)
        return

    if data == "onboarding_done":
        bot.answer_callback_query(call.id)
        try:
            from db import set_onboarding_done
            set_onboarding_done(call.from_user.id, True)
        except Exception:
            pass
        # Reuse same-place UI update.
        try:
            from db import get_native_language

            lang = get_native_language(call.from_user.id)
        except Exception:
            lang = "ru"
        try:
            bot.edit_message_text(
                t(lang, "welcome_choose_action"),
                call.message.chat.id,
                call.message.message_id,
                reply_markup=get_main_menu_markup(call.message.chat.id),
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                t(lang, "welcome_choose_action"),
                reply_markup=get_main_menu_markup(call.message.chat.id),
            )
        return

    if data.startswith("settings_"):
        # Settings module handles its own callback namespace.
        bot.answer_callback_query(call.id)
        from settings import handle_settings_callback
        handle_settings_callback(bot, call)
        return

    if data.startswith("speaking_"):
        # Speaking module handles its own callback namespace.
        bot.answer_callback_query(call.id)
        from speaking_practice import handle_speaking_callback
        handle_speaking_callback(bot, call)


@bot.message_handler(content_types=["voice", "text"])
def handle_all_messages(message):
    """
    Text/voice: if user is in Vocabulary "add word" flow, save word and exit.
    Otherwise pass to Speaking Practice (session check and GigaChat/voice there).
    """
    chat_id = message.chat.id
    content_type = message.content_type
    logger.info("Message chat_id=%s content_type=%s", chat_id, content_type)

    # Vocabulary "add word" mode: next text is saved as a word
    if content_type == "text":
        from vocabulary import is_adding_word, consume_add_word
        if is_adding_word(chat_id):
            # consume_add_word returns True when it "uses" the message
            # (so we should NOT pass it to speaking practice).
            consume_add_word(bot, chat_id, message.text)
            return

    # Default path: message belongs to Speaking practice.
    from speaking_practice import handle_speaking_input
    handle_speaking_input(bot, message)


# -----------------------------------------------------------------------------
# Run
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting polling.")
    try:
        # Long polling: Telegram holds the connection up to timeout seconds,
        # then we reconnect. `infinity_polling()` auto-restarts on recoverable errors.
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
    except Exception as e:
        logger.exception("Fatal: %s", e)
