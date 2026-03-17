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

from db import get_native_language, set_native_language

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

    # Inline keyboard: user taps buttons and we receive callback queries.
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Native language", callback_data="settings_native_lang"))
    # Both Back and Main menu currently go to restart (main menu). Back is kept for UX consistency.
    markup.add(types.InlineKeyboardButton("← Back", callback_data="restart"))
    markup.add(types.InlineKeyboardButton("Main menu", callback_data="restart"))

    bot.send_message(chat_id, f"SETTINGS\n\nNative language: {label}", reply_markup=markup)


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
        for code, name in LANG_OPTIONS:
            markup.add(types.InlineKeyboardButton(name, callback_data=f"settings_set_lang_{code}"))
        markup.add(types.InlineKeyboardButton("← Back", callback_data="settings"))
        markup.add(types.InlineKeyboardButton("Main menu", callback_data="restart"))
        bot.send_message(chat_id, "Choose your native language (used for translations).", reply_markup=markup)
        return

    if data.startswith("settings_set_lang_"):
        # Save the new language to the database.
        lang = data.split("_")[-1]
        ok = set_native_language(chat_id, lang)
        bot.send_message(chat_id, "Saved." if ok else "Could not save.")
        # After saving, show the settings home screen again.
        show_settings_menu(bot, call.message)
        return

