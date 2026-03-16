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

from db import get_words, add_word, get_database_url

logger = logging.getLogger(__name__)

# Chat IDs that are in "add word" mode; next text message is stored as a word.
_vocab_add_state = set()


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
        # List words from DB (or placeholder)
        words = get_words(chat_id) if get_database_url() else []
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("← Back to Vocabulary", callback_data="vocab_menu"))
        markup.add(types.InlineKeyboardButton("← Main menu", callback_data="restart"))
        if not words:
            bot.edit_message_text(
                "No words yet. Tap «Add word» to add one.",
                chat_id,
                call.message.message_id,
                reply_markup=markup,
            )
        else:
            lines = []
            for row in words[:50]:
                _, word, meaning, _ = row
                line = f"• {word}"
                if meaning:
                    line += f" — {meaning}"
                lines.append(line)
            text = "Your words:\n\n" + "\n".join(lines)
            if len(words) > 50:
                text += "\n\n(Showing first 50.)"
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup)
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
