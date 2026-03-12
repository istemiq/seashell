"""
Точка входа Telegram-бота Seashell.
Обрабатывает команды, callback-кнопки и передаёт голос/текст в модуль speaking_practice.
"""

import os
import logging
import telebot
from telebot import types
from dotenv import load_dotenv

# --- Настройка логирования ---
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# --- Загрузка переменных окружения ---
logger.info("Загрузка .env файла...")
result = load_dotenv()
logger.debug("Результат load_dotenv: %s", result)

# Логируем наличие критичных переменных (без вывода самих значений из соображений безопасности)
env_keys = ["TELEGRAM_BOT_TOKEN", "GIGACHAT_API_KEY", "GIGACHAT_MODEL_NAME"]
for key in env_keys:
    value = os.getenv(key)
    logger.debug("%s: %s", key, "SET" if value else "NOT SET")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GIGACHAT_API_KEY = os.getenv("GIGACHAT_API_KEY")
GIGACHAT_MODEL_NAME = os.getenv("GIGACHAT_MODEL_NAME")

logger.info("TELEGRAM_BOT_TOKEN загружен: %s", bool(TELEGRAM_BOT_TOKEN))
logger.info("GIGACHAT_API_KEY загружен: %s", bool(GIGACHAT_API_KEY))
logger.info("GIGACHAT_MODEL_NAME: %s", GIGACHAT_MODEL_NAME or "(не задан)")

# Проверка обязательного токена перед запуском бота
if not TELEGRAM_BOT_TOKEN:
    logger.critical("TELEGRAM_BOT_TOKEN отсутствует. Завершение.")
    exit(1)

# --- Инициализация бота ---
try:
    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
    logger.info("Бот успешно инициализирован.")
except Exception as e:
    logger.exception("Не удалось инициализировать бота: %s", e)
    exit(1)


@bot.message_handler(commands=["start"])
def handle_start(message):
    """
    Обработчик команды /start.
    Показывает главное меню с кнопками: словарь, speaking, прогресс, настройки.
    """
    user_id = message.from_user.id
    logger.info("Команда /start от пользователя user_id=%s", user_id)

    markup = types.InlineKeyboardMarkup()
    btn_vocabulary = types.InlineKeyboardButton("VOCABULARY", callback_data="vocabulary")
    btn_speaking = types.InlineKeyboardButton("SPEAKING PRACTICE", callback_data="speaking")
    btn_progress = types.InlineKeyboardButton("LEARNING PROGRESS", callback_data="progress")
    btn_settings = types.InlineKeyboardButton("SETTINGS", callback_data="settings")

    markup.add(btn_vocabulary, btn_speaking)
    markup.add(btn_progress, btn_settings)

    bot.reply_to(message, "Welcome. Choose an action:", reply_markup=markup)
    logger.debug("Главное меню отправлено пользователю user_id=%s", user_id)


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """
    Обработчик нажатий на inline-кнопки.
    Маршрутизирует по callback_data: vocabulary, speaking, progress, settings, speaking_*.
    """
    user_id = call.from_user.id
    callback_data = call.data
    logger.info("Callback от user_id=%s: %s", user_id, callback_data)

    if callback_data == "vocabulary":
        logger.debug("Открытие раздела Vocabulary для user_id=%s", user_id)
        bot.edit_message_text(
            "Vocabulary section is loading. Preparing materials...",
            call.message.chat.id,
            call.message.message_id,
        )
    elif callback_data == "speaking":
        logger.debug("Открытие раздела Speaking для user_id=%s", user_id)
        from speaking_practice import show_speaking_menu
        show_speaking_menu(bot, call.message)
    elif callback_data == "progress":
        logger.debug("Открытие раздела Progress для user_id=%s", user_id)
        bot.edit_message_text(
            "Your learning progress.",
            call.message.chat.id,
            call.message.message_id,
        )
    elif callback_data == "settings":
        logger.debug("Открытие раздела Settings для user_id=%s", user_id)
        bot.edit_message_text(
            "App settings.",
            call.message.chat.id,
            call.message.message_id,
        )
    elif callback_data.startswith("speaking_"):
        logger.debug("Обработка speaking-callback для user_id=%s: %s", user_id, callback_data)
        from speaking_practice import handle_speaking_callback
        handle_speaking_callback(bot, call)


@bot.message_handler(content_types=["voice", "text"])
def handle_all_messages(message):
    """
    Обработчик текстовых и голосовых сообщений.
    Вся логика (проверка сессии, распознавание, GigaChat) делегируется speaking_practice.
    """
    user_id = message.from_user.id
    content_type = message.content_type
    logger.info("Сообщение от user_id=%s, content_type=%s", user_id, content_type)

    from speaking_practice import handle_speaking_input
    handle_speaking_input(bot, message)


if __name__ == "__main__":
    logger.info("Запуск long polling бота...")
    try:
        bot.infinity_polling()
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем (Ctrl+C).")
    except Exception as e:
        logger.exception("Критическая ошибка при работе бота: %s", e)
