# Seashell

A Telegram bot for **English speaking practice** (intermediate level). It chats with you in text and voice: you can send text or voice messages, and the bot replies with both text and voice using an AI coach and TTS.

## Features

- **Speaking Practice**: Start a session and chat with an AI coach (GigaChat). The bot keeps conversation history so the dialogue stays coherent.
- **Voice in, voice out**: Send voice messages (speech-to-text via Whisper); get replies as text **and** voice (TTS via gTTS + ffmpeg).
- **Vocabulary**: Save words (with optional meaning) in your list; stored in PostgreSQL. UI is a stub and will be expanded.
- **Speed settings**: Choose slow / normal / fast for the bot’s voice.
- **English-only UI**: All bot messages and buttons are in simple English.

## Requirements

- Python 3.10+
- **PostgreSQL** (for vocabulary and future data)
- **ffmpeg** in PATH (for converting TTS audio to Telegram voice format)
- Telegram Bot Token
- GigaChat API credentials (for the AI coach)

## Setup

1. Clone the repo and go to the project folder:
   ```bash
   cd seashell
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   # source .venv/bin/activate   # Linux/macOS
   pip install -r requirements.txt
   ```

3. Install **ffmpeg** if you don’t have it (needed for voice replies):
   - Windows: [ffmpeg.org](https://ffmpeg.org/download.html) or `winget install ffmpeg`
   - macOS: `brew install ffmpeg`
   - Linux: `sudo apt install ffmpeg` (or your distro’s package manager)

4. Create a PostgreSQL database and set its URL. Create a `.env` file in the project root (do **not** commit it):
   ```env
   TELEGRAM_BOT_TOKEN=your_bot_token_from_BotFather
   GIGACHAT_API_KEY=your_gigachat_base64_client_id_secret
   GIGACHAT_MODEL_NAME=GigaChat
   DATABASE_URL=postgresql://user:password@host:5432/dbname
   ```
   If `DATABASE_URL` is missing, the bot runs but the Vocabulary section will not persist words.

5. Run the bot:
   ```bash
   python main.py
   ```

## Project structure

- `main.py` – Entry point: /start, callback routing (vocabulary / speaking / progress / settings), text and voice message dispatch
- `speaking_practice.py` – Speaking session: Whisper ASR, GigaChat, TTS, conversation history
- `vocabulary.py` – Vocabulary stub UI: My words, Add word (saved to DB)
- `db.py` – PostgreSQL: connection helper, `vocabulary` table, `add_word` / `get_words` / `delete_word`
- `prompts/speaking_intermediate.txt` – System prompt for the AI coach
- `requirements.txt` – Python dependencies (includes `psycopg2-binary` for PostgreSQL)

## License

MIT (or your choice).
