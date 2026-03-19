"""
Vocabulary feature (save words -> study them -> get examples + audio).

What user can do:
  - Add a word (optionally with meaning/translation)
  - Browse the list of saved words (paginated)
  - Open a "word card" (Study / Delete)
  - Study a word:
      - bot shows one example sentence
      - bot also sends audio for that example (TTS)
      - examples are cached in DB in batches (up to 10) to save tokens

How Telegram UI maps to our code:
  - Every inline button sends `callback_data` back to the bot.
  - This module "owns" callback_data that starts with `vocab_...`
  - `main.py` routes those callbacks here: `handle_vocabulary_callback`.

Persistence:
  - `db.py` uses PostgreSQL if DATABASE_URL is set.
  - otherwise it uses a local SQLite file `seashell.db` (fallback).

FAQ:
  Q: I pressed "Add word". Why does the bot start waiting for my next message?
  A: We put your chat_id into `_vocab_add_state`. The next text message is saved as a word.

  Q: Why do you cache up to 10 examples but show only one?
  A: Token economy. One AI call generates a batch. Then "Next example" uses DB cache.

  Q: What does "Refresh example" do?
  A: It behaves like "next": it uses cache first. If cache is empty, it generates a new batch.

  Q: Where is the translation shown?
  A: In the word card: `MEANING:`. If it's empty, we auto-translate using your native language
     from Settings and store the result to DB.

  Q: Why can examples fail to generate?
  A: If GIGACHAT_API_KEY / model is missing or the network is down, `_generate_examples_via_ai()`
     returns an empty list and you will see an error message.
"""

import logging
from telebot import types

from i18n import t
from db import (
    get_words,
    add_word,
    get_database_url,
    get_word,
    delete_word,
    pop_next_example,
    count_unserved_examples,
    add_examples_batch,
    get_native_language,
    update_word_meaning,
    increment_usage,
    set_vocab_active_word,
    get_vocab_active_word,
    clear_vocab_active_word,
)

logger = logging.getLogger(__name__)

# In-memory state (NOT persisted):
# - if chat_id is in this set, the next text message is treated as a "new word" input.
_vocab_add_state = set()

# In-memory state for study screen:
# - remembers which word_id user is currently studying.
_study_word_by_chat = {}

WORDS_PER_PAGE = 10

_COOLDOWN_SECONDS = 3
_vocab_cooldowns = {}


def _cooldown_ok(user_id: int, key: str) -> bool:
    """Simple in-memory anti-spam cooldown for expensive actions."""
    import time

    now = time.time()
    cooldown_key = (user_id, key)
    until = _vocab_cooldowns.get(cooldown_key, 0)
    if now < until:
        return False
    _vocab_cooldowns[cooldown_key] = now + _COOLDOWN_SECONDS
    return True


def _show_words_page(bot, chat_id: int, message_id: int, page: int):
    """
    Render one page of the "My words" list as inline keyboard buttons.

    Why "buttons per word" instead of a big text list:
      - user can tap a word directly
      - we get the word_id in callback_data and can open the word card
    """
    if page < 0:
        page = 0

    lang = get_native_language(chat_id)

    offset = page * WORDS_PER_PAGE
    # db.py falls back to local SQLite when DATABASE_URL is missing
    words = get_words(chat_id, limit=WORDS_PER_PAGE + 1, offset=offset)

    markup = types.InlineKeyboardMarkup()
    if not words:
        # Empty state: user has no words yet.
        markup.add(types.InlineKeyboardButton(f"← {t(lang, 'back')}", callback_data="vocab_menu"))
        markup.add(types.InlineKeyboardButton(t(lang, "main_menu"), callback_data="restart"))
        bot.edit_message_text(
            t(lang, "no_words_yet"),
            chat_id,
            message_id,
            reply_markup=markup,
        )
        return

    has_next = len(words) > WORDS_PER_PAGE
    words = words[:WORDS_PER_PAGE]

    for (word_id, word, meaning, _) in words:
        # Button label is short; callback_data contains the word_id (DB primary key).
        label = word if not meaning else f"{word} — {meaning}"
        if len(label) > 50:
            label = label[:47] + "..."
        markup.add(types.InlineKeyboardButton(label, callback_data=f"vocab_word_{word_id}"))

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(t(lang, "prev_page"), callback_data=f"vocab_list_p{page-1}"))
    if has_next:
        nav.append(types.InlineKeyboardButton(t(lang, "next_page"), callback_data=f"vocab_list_p{page+1}"))
    if nav:
        markup.row(*nav)

    markup.add(types.InlineKeyboardButton(f"← {t(lang, 'back')}", callback_data="vocab_menu"))
    markup.add(types.InlineKeyboardButton(t(lang, "main_menu"), callback_data="restart"))
    bot.edit_message_text(
        t(lang, "your_words_page", p=page + 1),
        chat_id,
        message_id,
        reply_markup=markup,
    )


def show_vocabulary_menu(bot, message):
    """
    Entry point: show Vocabulary home menu.
    Called from `main.py` when user taps the main menu button "VOCABULARY".
    """
    chat_id = message.chat.id
    lang = get_native_language(chat_id)
    logger.info("Showing vocabulary menu for chat_id=%s", chat_id)

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(t(lang, "vocab_my_words"), callback_data="vocab_list"))
    markup.add(types.InlineKeyboardButton(t(lang, "vocab_add_word"), callback_data="vocab_add"))
    markup.add(types.InlineKeyboardButton(f"← {t(lang, 'back')}", callback_data="vocab_back"))
    markup.add(types.InlineKeyboardButton(f"← {t(lang, 'main_menu')}", callback_data="restart"))

    text = f"{t(lang, 'vocab_title')}\n\n{t(lang, 'vocab_title')} (Stub UI)."
    bot.reply_to(message, text, reply_markup=markup)


def handle_vocabulary_callback(bot, call):
    """
    Router for the whole vocabulary UI.

    All callback_data values this function handles start with:
      - vocab_...
    Examples:
      - vocab_list
      - vocab_list_p1
      - vocab_word_123
      - vocab_study_123
      - vocab_next_example
      - vocab_refresh_example
    """
    chat_id = call.message.chat.id
    data = call.data
    logger.info("Vocabulary callback chat_id=%s data=%s", chat_id, data)
    bot.answer_callback_query(call.id)

    if data == "vocab_back":
        # Back from Vocabulary feature to global main menu.
        from main import get_main_menu_markup
        from db import get_native_language
        lang = get_native_language(chat_id)
        bot.send_message(chat_id, t(lang, "welcome_choose_action"), reply_markup=get_main_menu_markup(chat_id))
        return

    if data == "vocab_menu":
        # Vocabulary "home" screen; also cancels add-word mode.
        _vocab_add_state.discard(chat_id)
        lang = get_native_language(chat_id)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(t(lang, "vocab_my_words"), callback_data="vocab_list"))
        markup.add(types.InlineKeyboardButton(t(lang, "vocab_add_word"), callback_data="vocab_add"))
        markup.add(types.InlineKeyboardButton(f"← {t(lang, 'back')}", callback_data="vocab_back"))
        markup.add(types.InlineKeyboardButton(f"← {t(lang, 'main_menu')}", callback_data="restart"))
        bot.edit_message_text(
            f"{t(lang, 'vocab_title')}\n\n{t(lang, 'vocab_title')} (Stub UI).",
            chat_id,
            call.message.message_id,
            reply_markup=markup,
        )
        return

    if data == "vocab_list":
        # Page 0 of saved words.
        _show_words_page(bot, chat_id, call.message.message_id, page=0)
        return

    if data.startswith("vocab_list_p"):
        # Pagination: `vocab_list_pN` where N is page index.
        try:
            page = int(data.split("p", 1)[1])
        except Exception:
            page = 0
        _show_words_page(bot, chat_id, call.message.message_id, page=page)
        return

    if data.startswith("vocab_word_"):
        # Open a word card (Study/Delete) for a specific word_id.
        try:
            word_id = int(data.split("_")[2])
        except Exception:
            bot.send_message(chat_id, "Invalid word id.")
            return
        row = get_word(chat_id, word_id)
        if not row:
            lang = get_native_language(chat_id)
            bot.send_message(chat_id, t(lang, "word_not_found"))
            return
        _, word, meaning, _ = row
        if not meaning:
            # If translation/meaning is missing, generate it automatically
            # using the user's native language from Settings.
            native_lang = get_native_language(chat_id)
            translated = None
            if _cooldown_ok(chat_id, f"translate_{word_id}"):
                translated = _translate_word_via_ai(word, native_lang)
            if translated:
                meaning = translated
                # Persist translation so we don't spend tokens next time.
                update_word_meaning(chat_id, word_id, translated)

        lang = get_native_language(chat_id)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(t(lang, "study"), callback_data=f"vocab_study_{word_id}"))
        markup.add(types.InlineKeyboardButton(t(lang, "delete"), callback_data=f"vocab_delete_{word_id}"))
        if not meaning:
            markup.add(
                types.InlineKeyboardButton(
                    t(lang, "update_translation"),
                    callback_data=f"vocab_update_translation_{word_id}",
                )
            )
        markup.add(types.InlineKeyboardButton(f"← {t(lang, 'back')}", callback_data="vocab_list_p0"))
        markup.add(types.InlineKeyboardButton(t(lang, "main_menu"), callback_data="restart"))

        text = f"{t(lang, 'word')}: {word}"
        if meaning:
            text += f"\n{t(lang, 'meaning')}: {meaning}"
        else:
            text += f"\n{t(lang, 'meaning')}: {t(lang, 'translation_missing')}"
        bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup)
        return

    if data.startswith("vocab_update_translation_"):
        # User explicitly requested "regenerate translation" for this word.
        try:
            word_id = int(data.split("_")[3])
        except Exception:
            bot.send_message(chat_id, "Invalid word id.")
            return
        if not _cooldown_ok(chat_id, f"translate_{word_id}"):
            bot.send_message(chat_id, t(get_native_language(chat_id), "waiting_retry"))
            return

        row = get_word(chat_id, word_id)
        if not row:
            bot.send_message(chat_id, t(get_native_language(chat_id), "word_not_found"))
            return
        _, word, _meaning, _ = row
        native_lang = get_native_language(chat_id)
        translated = _translate_word_via_ai(word, native_lang)
        if translated:
            update_word_meaning(chat_id, word_id, translated)
            lang = native_lang
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton(t(lang, "study"), callback_data=f"vocab_study_{word_id}")
            )
            markup.add(types.InlineKeyboardButton(t(lang, "delete"), callback_data=f"vocab_delete_{word_id}"))
            markup.add(types.InlineKeyboardButton(f"← {t(lang, 'back')}", callback_data="vocab_list_p0"))
            markup.add(types.InlineKeyboardButton(t(lang, "main_menu"), callback_data="restart"))
            bot.edit_message_text(
                f"{t(lang, 'word')}: {word}\n{t(lang, 'meaning')}: {translated}",
                chat_id,
                call.message.message_id,
                reply_markup=markup,
            )
        else:
            bot.send_message(chat_id, t(native_lang, "translation_not_ready"))
        return

    if data.startswith("vocab_delete_") and not data.startswith("vocab_delete_confirm_"):
        # Delete flow step 1: show confirmation prompt.
        try:
            word_id = int(data.split("_")[2])
        except Exception:
            bot.send_message(chat_id, "Invalid word id.")
            return
        row = get_word(chat_id, word_id)
        word_label = row[1] if row else "this word"
        lang = get_native_language(chat_id)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(t(lang, "yes_delete"), callback_data=f"vocab_delete_confirm_{word_id}"))
        markup.add(types.InlineKeyboardButton(t(lang, "cancel"), callback_data=f"vocab_word_{word_id}"))
        markup.add(types.InlineKeyboardButton(f"← {t(lang, 'back')}", callback_data=f"vocab_word_{word_id}"))
        markup.add(types.InlineKeyboardButton(t(lang, "main_menu"), callback_data="restart"))
        bot.edit_message_text(
            t(lang, "delete_confirm", w=word_label),
            chat_id,
            call.message.message_id,
            reply_markup=markup,
        )
        return

    if data.startswith("vocab_delete_confirm_"):
        # Delete flow step 2: user confirmed.
        try:
            word_id = int(data.split("_")[3])
        except Exception:
            bot.send_message(chat_id, t(get_native_language(chat_id), "invalid_word_id"))
            return
        ok = delete_word(chat_id, word_id)
        lang = get_native_language(chat_id)
        bot.send_message(chat_id, t(lang, "deleted") if ok else t(lang, "could_not_delete"))
        _show_words_page(bot, chat_id, call.message.message_id, page=0)
        return

    if data.startswith("vocab_study_"):
        # Enter study mode for a word_id.
        try:
            word_id = int(data.split("_")[2])
        except Exception:
            bot.send_message(chat_id, "Invalid word id.")
            return
        _study_word_by_chat[chat_id] = word_id
        try:
            set_vocab_active_word(chat_id, word_id)
        except Exception:
            pass
        # Serve first example: uses cache if present, otherwise generates a new batch.
        _show_next_study_example(bot, chat_id, call.message, force_refresh=False)
        return

    if data == "vocab_next_example":
        # Serve next cached example (generates new batch only if cache is empty).
        word_id = _study_word_by_chat.get(chat_id)
        if not word_id:
            # After bot restart we may have lost in-memory state, so restore from DB.
            word_id = get_vocab_active_word(chat_id)
            if word_id:
                _study_word_by_chat[chat_id] = word_id
        if not word_id:
            bot.send_message(chat_id, "No active word. Pick a word first.")
            return
        _show_next_study_example(bot, chat_id, call.message, force_refresh=False)
        return

    if data == "vocab_refresh_example":
        # Token-saving behavior:
        # - If cache is not empty -> just serve next cached example
        # - If cache is empty -> generate a new batch and serve from it
        word_id = _study_word_by_chat.get(chat_id)
        if not word_id:
            word_id = get_vocab_active_word(chat_id)
            if word_id:
                _study_word_by_chat[chat_id] = word_id
        if not word_id:
            bot.send_message(chat_id, "No active word. Pick a word first.")
            return
        # Token-saving: use cache first; generate only when cache is empty
        _show_next_study_example(bot, chat_id, call.message, force_refresh=False, generate_if_empty=True)
        return

    if data == "vocab_add":
        # Enter "add word" mode: next text message will be saved as vocabulary.
        _vocab_add_state.add(chat_id)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Cancel", callback_data="vocab_menu"))
        markup.add(types.InlineKeyboardButton("← Main menu", callback_data="restart"))
        bot.edit_message_text(
            "Send the word you want to add (e.g. «apple» or «apple — a fruit»).",
            chat_id,
            call.message.message_id,
            reply_markup=markup,
        )
        return


def cancel_add_word_mode(chat_id: int) -> None:
    """Leave 'add word' mode (e.g. when user goes to main menu)."""
    _vocab_add_state.discard(chat_id)
    # Clear active study word to avoid stale Next/Refresh after navigation/restart.
    try:
        _study_word_by_chat.pop(chat_id, None)
        clear_vocab_active_word(chat_id)
    except Exception:
        pass


def is_adding_word(chat_id: int) -> bool:
    """True if user is in 'add word' flow and we should treat next text as a word."""
    return chat_id in _vocab_add_state


def consume_add_word(bot, chat_id, text: str) -> bool:
    """
    If user was in add-word mode, save the text as word (and optional meaning after « — »).
    Returns True if we consumed the message (saved a word), False otherwise.
    """
    if chat_id not in _vocab_add_state:
        return False
    _vocab_add_state.discard(chat_id)

    part = text.strip()
    if not part:
        bot.send_message(chat_id, "Empty message. Word not added.")
        return True

    # Optional input format: "word — meaning" (dash is ' — ').
    word, meaning = part, None
    if " — " in part:
        word, _, meaning = part.partition(" — ")
        word = word.strip()
        meaning = meaning.strip() or None

    if not word:
        bot.send_message(chat_id, "Word not added.")
        return True

    ok = add_word(chat_id, word, meaning)
    if ok:
        bot.send_message(chat_id, f"Added: {word}" + (f" — {meaning}" if meaning else ""))
        increment_usage(chat_id, "words_added", 1)
    else:
        bot.send_message(chat_id, "Could not add word. Check database connection.")
    return True


def _show_next_study_example(
    bot,
    chat_id: int,
    message,
    force_refresh: bool,
    generate_if_empty: bool = True,
):
    """
    Serve cached example if available. If none (or force_refresh), generate a new batch and cache it.
    Shows one example + audio, and buttons: Next / Refresh / Back / Main menu.
    """
    word_id = _study_word_by_chat.get(chat_id)
    row = get_word(chat_id, word_id) if word_id else None
    if not row:
        lang = get_native_language(chat_id)
        bot.send_message(chat_id, t(lang, "word_not_found"))
        return
    _, word, meaning, _ = row
    lang = get_native_language(chat_id)

    # How many cached examples we have BEFORE consuming one.
    remaining_before = count_unserved_examples(chat_id, word_id)
    if force_refresh:
        picked = None
    else:
        # Consume one cached example (mark as served in DB).
        picked = pop_next_example(chat_id, word_id)

    if picked is None and generate_if_empty:
        # Cache is empty: ask the AI to generate a batch (up to 10),
        # store them in DB, then pop the first one.
        if not _cooldown_ok(chat_id, f"examples_{word_id}"):
            bot.send_message(chat_id, "Подождите пару секунд и попробуйте ещё раз.")
            return
        examples = _generate_examples_via_ai(word, meaning, count=10)
        if examples:
            batch_id = add_examples_batch(chat_id, word_id, examples)
            if batch_id:
                increment_usage(chat_id, "examples_generated", len(examples))
        picked = pop_next_example(chat_id, word_id)

    remaining = count_unserved_examples(chat_id, word_id)

    if not picked:
        bot.send_message(chat_id, t(lang, "could_not_get_example"))
        return

    _, example_text, _batch_id = picked

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(t(lang, "next_example"), callback_data="vocab_next_example"))
    markup.add(types.InlineKeyboardButton(t(lang, "refresh_example"), callback_data="vocab_refresh_example"))
    markup.add(types.InlineKeyboardButton(f"← {t(lang, 'back')}", callback_data=f"vocab_word_{word_id}"))
    markup.add(types.InlineKeyboardButton(t(lang, "main_menu"), callback_data="restart"))

    header = t(lang, "study_header", w=word)
    if meaning:
        header += f" — {meaning}"
    used_cache = remaining_before > 0
    source = "cache" if used_cache else "new batch"
    text = (
        f"{header}\n\n"
        f"{t(lang, 'example')}\n"
        f"{example_text}\n\n"
        f"{t(lang, 'cached_remaining', n=remaining, src=source)}"
    )

    # Send text
    bot.send_message(chat_id, text, reply_markup=markup)
    # Example was successfully shown to the user.
    increment_usage(chat_id, "examples_served", 1)

    # Send audio (gTTS+ffmpeg pipeline from speaking_practice)
    try:
        from speaking_practice import send_voice_reply
        # Always normal speed for vocabulary examples.
        send_voice_reply(bot, chat_id, example_text, speed="normal")
    except Exception as e:
        logger.warning("Failed to send example audio: %s", e)


def _generate_examples_via_ai(word: str, meaning: str | None, count: int = 10) -> list[str]:
    """
    Use GigaChat to generate very simple examples with the target word.
    Returns a list of examples (strings). Tries to keep them short and easy.
    """
    try:
        import os
        import requests
        from speaking_practice import get_access_token

        api_key = os.getenv("GIGACHAT_API_KEY")
        model = os.getenv("GIGACHAT_MODEL_NAME")
        if not api_key or not model:
            return []

        access_token = get_access_token()
        if not access_token:
            return []

        meaning_part = f" Meaning: {meaning}." if meaning else ""
        system_prompt = (
            "You are an English tutor. Generate VERY SIMPLE A1-A2 examples for a learner.\n"
            "Rules:\n"
            "- Use the target word EXACTLY as given.\n"
            "- One short sentence per example.\n"
            "- No difficult grammar.\n"
            "- No quotation marks.\n"
            "- Output as a numbered list, one sentence per line.\n"
        )
        user_prompt = (
            f"Target word: {word}.{meaning_part}\n"
            f"Generate {min(max(count, 3), 10)} different example sentences."
        )

        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        data = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 450,
        }
        url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
        resp = requests.post(url, headers=headers, json=data, timeout=30, verify=False)
        if resp.status_code != 200:
            logger.warning("GigaChat examples error: status=%s body=%s", resp.status_code, resp.text[:200])
            return []
        content = resp.json()["choices"][0]["message"]["content"]
        lines = []
        for raw in (content or "").splitlines():
            s = raw.strip()
            if not s:
                continue
            # strip leading numbering like "1) " or "1. "
            s = s.lstrip("-•").strip()
            s = s.split(" ", 1)[1] if s[:2].isdigit() and s[1] in (".", ")", ":") and " " in s else s
            if word.lower() not in s.lower():
                continue
            if len(s) > 140:
                s = s[:140].rstrip() + "…"
            lines.append(s)
        # Deduplicate while preserving order
        seen = set()
        out = []
        for s in lines:
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        return out[:10]
    except Exception as e:
        logger.exception("generate_examples failed: %s", e)
        return []


def _translate_word_via_ai(word: str, native_lang: str) -> str | None:
    """Translate a single word into user's native language. Returns short translation."""
    try:
        import os
        import requests
        from speaking_practice import get_access_token

        api_key = os.getenv("GIGACHAT_API_KEY")
        model = os.getenv("GIGACHAT_MODEL_NAME")
        if not api_key or not model:
            return None

        access_token = get_access_token()
        if not access_token:
            return None

        system_prompt = (
            "You are a translator. Return ONLY the translation, no extra words.\n"
            "If there are multiple common translations, separate them with '; '.\n"
            "Keep it short."
        )
        user_prompt = f"Translate this English word to language '{native_lang}': {word}"

        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        data = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 60,
        }
        url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
        resp = requests.post(url, headers=headers, json=data, timeout=30, verify=False)
        if resp.status_code != 200:
            return None
        content = (resp.json()["choices"][0]["message"]["content"] or "").strip()
        if not content:
            return None
        # One line only
        return content.splitlines()[0].strip()[:120]
    except Exception as e:
        logger.warning("translate_word failed: %s", e)
        return None
