"""
Vocabulary module: user word list (stub UI + PostgreSQL).

Flow:
  - VOCABULARY from main menu → show_vocabulary_menu (My words / Add word / Back).
  - My words → list from db.get_words(chat_id); Back returns to this menu.
  - Add word → set _vocab_add_state; next text message is saved via consume_add_word (word or "word — meaning").
  - Back → return to main menu (same inline buttons as main.py).

Callbacks handled: vocab_list, vocab_add, vocab_back. "vocabulary" is handled in main and calls show_vocabulary_menu.
"""

import logging
from telebot import types

from db import (
    get_words,
    add_word,
    get_database_url,
    get_word,
    delete_word,
    pop_next_example,
    count_unserved_examples,
    add_examples_batch,
)

logger = logging.getLogger(__name__)

# Chat IDs that are in "add word" mode; next text message is stored as a word.
_vocab_add_state = set()

# Simple per-chat UI state for study screen navigation
_study_word_by_chat = {}

WORDS_PER_PAGE = 10


def _show_words_page(bot, chat_id: int, message_id: int, page: int):
    if page < 0:
        page = 0

    offset = page * WORDS_PER_PAGE
    # db.py falls back to local SQLite when DATABASE_URL is missing
    words = get_words(chat_id, limit=WORDS_PER_PAGE + 1, offset=offset)

    markup = types.InlineKeyboardMarkup()
    if not words:
        markup.add(types.InlineKeyboardButton("← Back", callback_data="vocab_menu"))
        markup.add(types.InlineKeyboardButton("Main menu", callback_data="restart"))
        bot.edit_message_text(
            "No words yet. Tap «Add word» to add one.",
            chat_id,
            message_id,
            reply_markup=markup,
        )
        return

    has_next = len(words) > WORDS_PER_PAGE
    words = words[:WORDS_PER_PAGE]

    for (word_id, word, meaning, _) in words:
        label = word if not meaning else f"{word} — {meaning}"
        if len(label) > 50:
            label = label[:47] + "..."
        markup.add(types.InlineKeyboardButton(label, callback_data=f"vocab_word_{word_id}"))

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("← Prev", callback_data=f"vocab_list_p{page-1}"))
    if has_next:
        nav.append(types.InlineKeyboardButton("Next →", callback_data=f"vocab_list_p{page+1}"))
    if nav:
        markup.row(*nav)

    markup.add(types.InlineKeyboardButton("← Back", callback_data="vocab_menu"))
    markup.add(types.InlineKeyboardButton("Main menu", callback_data="restart"))
    bot.edit_message_text(
        f"Your words (page {page+1}). Tap one to manage/study.",
        chat_id,
        message_id,
        reply_markup=markup,
    )


def show_vocabulary_menu(bot, message):
    """
    Entry point: show Vocabulary menu (stub).
    Called from main when user taps VOCABULARY.
    """
    chat_id = message.chat.id
    logger.info("Showing vocabulary menu for chat_id=%s", chat_id)

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("My words", callback_data="vocab_list"))
    markup.add(types.InlineKeyboardButton("Add word", callback_data="vocab_add"))
    markup.add(types.InlineKeyboardButton("← Back", callback_data="vocab_back"))
    markup.add(types.InlineKeyboardButton("← Main menu", callback_data="restart"))

    text = "VOCABULARY\n\nYour saved words for practice. (Stub: full UI coming soon.)"
    bot.reply_to(message, text, reply_markup=markup)


def handle_vocabulary_callback(bot, call):
    """
    Route callback_data: vocab_list, vocab_add, vocab_back, vocab_menu.
    """
    chat_id = call.message.chat.id
    data = call.data
    logger.info("Vocabulary callback chat_id=%s data=%s", chat_id, data)
    bot.answer_callback_query(call.id)

    if data == "vocab_back":
        from main import get_main_menu_markup
        bot.send_message(chat_id, "Choose an action:", reply_markup=get_main_menu_markup())
        return

    if data == "vocab_menu":
        _vocab_add_state.discard(chat_id)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("My words", callback_data="vocab_list"))
        markup.add(types.InlineKeyboardButton("Add word", callback_data="vocab_add"))
        markup.add(types.InlineKeyboardButton("← Back", callback_data="vocab_back"))
        markup.add(types.InlineKeyboardButton("← Main menu", callback_data="restart"))
        bot.edit_message_text(
            "VOCABULARY\n\nYour saved words for practice. (Stub: full UI coming soon.)",
            chat_id,
            call.message.message_id,
            reply_markup=markup,
        )
        return

    if data == "vocab_list":
        _show_words_page(bot, chat_id, call.message.message_id, page=0)
        return

    if data.startswith("vocab_list_p"):
        try:
            page = int(data.split("p", 1)[1])
        except Exception:
            page = 0
        _show_words_page(bot, chat_id, call.message.message_id, page=page)
        return

    if data.startswith("vocab_word_"):
        try:
            word_id = int(data.split("_")[2])
        except Exception:
            bot.send_message(chat_id, "Invalid word id.")
            return
        row = get_word(chat_id, word_id)
        if not row:
            bot.send_message(chat_id, "Word not found (maybe deleted).")
            return
        _, word, meaning, _ = row

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Study", callback_data=f"vocab_study_{word_id}"))
        markup.add(types.InlineKeyboardButton("Delete", callback_data=f"vocab_delete_{word_id}"))
        markup.add(types.InlineKeyboardButton("← Back", callback_data="vocab_list_p0"))
        markup.add(types.InlineKeyboardButton("Main menu", callback_data="restart"))

        text = f"WORD: {word}"
        if meaning:
            text += f"\nMEANING: {meaning}"
        bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup)
        return

    if data.startswith("vocab_delete_") and not data.startswith("vocab_delete_confirm_"):
        try:
            word_id = int(data.split("_")[2])
        except Exception:
            bot.send_message(chat_id, "Invalid word id.")
            return
        row = get_word(chat_id, word_id)
        word_label = row[1] if row else "this word"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Yes, delete", callback_data=f"vocab_delete_confirm_{word_id}"))
        markup.add(types.InlineKeyboardButton("No", callback_data=f"vocab_word_{word_id}"))
        markup.add(types.InlineKeyboardButton("← Back", callback_data=f"vocab_word_{word_id}"))
        markup.add(types.InlineKeyboardButton("Main menu", callback_data="restart"))
        bot.edit_message_text(
            f"Delete '{word_label}'?",
            chat_id,
            call.message.message_id,
            reply_markup=markup,
        )
        return

    if data.startswith("vocab_delete_confirm_"):
        try:
            word_id = int(data.split("_")[3])
        except Exception:
            bot.send_message(chat_id, "Invalid word id.")
            return
        ok = delete_word(chat_id, word_id)
        bot.send_message(chat_id, "Deleted." if ok else "Could not delete (not found).")
        _show_words_page(bot, chat_id, call.message.message_id, page=0)
        return

    if data.startswith("vocab_study_"):
        try:
            word_id = int(data.split("_")[2])
        except Exception:
            bot.send_message(chat_id, "Invalid word id.")
            return
        _study_word_by_chat[chat_id] = word_id
        _show_next_study_example(bot, chat_id, call.message, force_refresh=False)
        return

    if data == "vocab_next_example":
        word_id = _study_word_by_chat.get(chat_id)
        if not word_id:
            bot.send_message(chat_id, "No active word. Pick a word first.")
            return
        _show_next_study_example(bot, chat_id, call.message, force_refresh=False)
        return

    if data == "vocab_refresh_example":
        word_id = _study_word_by_chat.get(chat_id)
        if not word_id:
            bot.send_message(chat_id, "No active word. Pick a word first.")
            return
        # Token-saving: use cache first; generate only when cache is empty
        _show_next_study_example(bot, chat_id, call.message, force_refresh=False, generate_if_empty=True)
        return

    if data == "vocab_add":
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

    # Optional: "word — meaning"
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
        bot.send_message(chat_id, "Word not found. Go back to Vocabulary.")
        return
    _, word, meaning, _ = row

    remaining_before = count_unserved_examples(chat_id, word_id)
    if force_refresh:
        picked = None
    else:
        picked = pop_next_example(chat_id, word_id)

    if picked is None and generate_if_empty:
        examples = _generate_examples_via_ai(word, meaning, count=10)
        if examples:
            add_examples_batch(chat_id, word_id, examples)
        picked = pop_next_example(chat_id, word_id)

    remaining = count_unserved_examples(chat_id, word_id)

    if not picked:
        bot.send_message(chat_id, "Could not get an example right now. Try again.")
        return

    _, example_text, _batch_id = picked

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Next example", callback_data="vocab_next_example"))
    markup.add(types.InlineKeyboardButton("Refresh example", callback_data="vocab_refresh_example"))
    markup.add(types.InlineKeyboardButton("← Back", callback_data=f"vocab_word_{word_id}"))
    markup.add(types.InlineKeyboardButton("Main menu", callback_data="restart"))

    header = f"STUDY: {word}"
    if meaning:
        header += f" — {meaning}"
    used_cache = remaining_before > 0
    source = "cache" if used_cache else "new batch"
    text = f"{header}\n\nExample:\n{example_text}\n\nCached remaining: {remaining} (source: {source})"

    # Send text
    bot.send_message(chat_id, text, reply_markup=markup)

    # Send audio (gTTS+ffmpeg pipeline from speaking_practice)
    try:
        from speaking_practice import send_voice_reply
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
