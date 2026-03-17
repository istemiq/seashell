"""
Seashell Telegram bot — entry point.

Flow:
  1. /start, /restart → show main menu (Vocabulary, Speaking Practice, Restart, Settings).
  2. Callbacks → route by callback_data to vocabulary, speaking_practice, restart, or settings.
  3. Text/voice messages → if user is in "add word" (vocabulary) mode, save word; else handle as Speaking Practice input.
"""

import os
import logging
import telebot
from telebot import types
from dotenv import load_dotenv


def get_main_menu_markup():
    """Single source for main menu buttons (no Learning progress)."""
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("VOCABULARY", callback_data="vocabulary"),
        types.InlineKeyboardButton("SPEAKING PRACTICE", callback_data="speaking"),
    )
    markup.add(
        types.InlineKeyboardButton("Restart", callback_data="restart"),
        types.InlineKeyboardButton("SETTINGS", callback_data="settings"),
    )
    return markup

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Environment
# -----------------------------------------------------------------------------
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
    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
    import telebot.apihelper
    telebot.apihelper.READ_TIMEOUT = 60
    telebot.apihelper.CONNECT_TIMEOUT = 30
    logger.info("Bot initialized.")
except Exception as e:
    logger.exception("Failed to init bot: %s", e)
    exit(1)

# -----------------------------------------------------------------------------
# Database: create tables on startup (vocabulary)
# -----------------------------------------------------------------------------
try:
    from db import init_db
    init_db()
except Exception as e:
    logger.warning("DB init skipped or failed: %s", e)


# -----------------------------------------------------------------------------
# Handlers
# -----------------------------------------------------------------------------

@bot.message_handler(commands=["start", "restart"])
def handle_start(message):
    """Show main menu (Vocabulary, Speaking, Restart, Settings)."""
    logger.info("%s from user_id=%s", message.text or "/start", message.from_user.id)
    bot.reply_to(message, "Welcome. Choose an action:", reply_markup=get_main_menu_markup())


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
        bot.answer_callback_query(call.id)
        from vocabulary import show_vocabulary_menu
        show_vocabulary_menu(bot, call.message)
        return

    if data.startswith("vocab_"):
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
        from vocabulary import cancel_add_word_mode
        cancel_add_word_mode(call.message.chat.id)
        # Also drop active Speaking session, if any
        try:
            from speaking_practice import user_speaking_state
            user_speaking_state.pop(call.message.chat.id, None)
        except Exception:
            pass
        bot.send_message(
            call.message.chat.id,
            "Welcome. Choose an action:",
            reply_markup=get_main_menu_markup(),
        )
        return

    if data == "settings":
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "App settings.",
            reply_markup=get_main_menu_markup(),
        )
        return

    if data.startswith("speaking_"):
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
            consume_add_word(bot, chat_id, message.text)
            return

    from speaking_practice import handle_speaking_input
    handle_speaking_input(bot, message)


# -----------------------------------------------------------------------------
# Run
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting polling.")
    try:
        bot.infinity_polling()
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
    except Exception as e:
        logger.exception("Fatal: %s", e)
