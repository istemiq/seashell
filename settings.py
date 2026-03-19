"""
Settings UI for the Telegram bot.

Right now we store only one user preference:
  - native language (used to translate saved vocabulary words)

How it works:
  - main menu has a `SETTINGS` button -> callback_data = "settings"
  - `main.py` routes that callback here (show_settings_menu)
  - inside settings we use callback_data that starts with "settings_"

FAQ:
  Q: Why do I need to choose a native language?
  A: Vocabulary words get an automatic translation shown as `MEANING:`. We need to know
     what language to translate into.

  Q: Where is this setting stored?
  A: In DB table `user_settings` (SQLite `seashell.db` by default).

  Q: Why are there only a few language choices?
  A: It's a simple MVP list. You can add more codes to `LANG_OPTIONS`.
"""

import logging
from telebot import types

from db import get_native_language, set_native_language, get_usage_stats
from i18n import t

logger = logging.getLogger(__name__)


LANG_OPTIONS = [
    ("ru", "Русский"),
    ("uk", "Українська"),
    ("es", "Español"),
    ("fr", "Français"),
    ("de", "Deutsch"),
]


def show_settings_menu(bot, message):
    """
    Show settings home screen.

    We send a new message (instead of editing old one) because:
      - it is more reliable with Telegram API
      - user can scroll back and see previous screens
    """
    chat_id = message.chat.id
    lang = get_native_language(chat_id)
    label = next((name for code, name in LANG_OPTIONS if code == lang), lang)
    stats = get_usage_stats(chat_id) or {}
    words_added = stats.get("words_added", 0)
    examples_generated = stats.get("examples_generated", 0)
    examples_served = stats.get("examples_served", 0)
    speaking_turns = stats.get("speaking_turns", 0)

    # Inline keyboard: user taps buttons and we receive callback queries.
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(t(lang, "native_language"), callback_data="settings_native_lang")
    )
    markup.add(types.InlineKeyboardButton(f"← {t(lang, 'back')}", callback_data="restart"))
    markup.add(types.InlineKeyboardButton(t(lang, "main_menu"), callback_data="restart"))

    words_added = stats.get("words_added", 0)
    examples_generated = stats.get("examples_generated", 0)
    examples_served = stats.get("examples_served", 0)
    speaking_turns = stats.get("speaking_turns", 0)
    bot.send_message(
        chat_id,
        f"{t(lang, 'settings_title')}\n\n"
        f"{t(lang, 'native_language')}: {label}\n\n"
        f"{t(lang, 'usage_words_added', n=words_added)}\n"
        f"{t(lang, 'usage_examples_generated', n=examples_generated)}\n"
        f"{t(lang, 'usage_examples_served', n=examples_served)}\n"
        f"{t(lang, 'usage_speaking_turns', n=speaking_turns)}",
        reply_markup=markup,
    )


def handle_settings_callback(bot, call):
    """
    Handle settings-related callback buttons.

    `call.data` examples:
      - "settings_native_lang"
      - "settings_set_lang_ru"
    """
    chat_id = call.message.chat.id
    data = call.data
    logger.info("Settings callback chat_id=%s data=%s", chat_id, data)

    if data == "settings_native_lang":
        # Show list of supported native languages.
        markup = types.InlineKeyboardMarkup()
        current_lang = get_native_language(chat_id)
        for code, name in LANG_OPTIONS:
            markup.add(types.InlineKeyboardButton(name, callback_data=f"settings_set_lang_{code}"))
        markup.add(types.InlineKeyboardButton(f"← {t(current_lang, 'back')}", callback_data="settings"))
        markup.add(types.InlineKeyboardButton(t(current_lang, "main_menu"), callback_data="restart"))
        bot.send_message(chat_id, t(current_lang, "choose_native_language"), reply_markup=markup)
        return

    if data.startswith("settings_set_lang_"):
        # Save the new language to the database.
        lang = data.split("_")[-1]
        ok = set_native_language(chat_id, lang)
        bot.send_message(chat_id, t(lang if ok else get_native_language(chat_id), "saved") if ok else "Could not save.")
        # After saving, show the settings home screen again.
        show_settings_menu(bot, call.message)
        return

