"""
Small standalone script to sanity-check gTTS.

This file is NOT used by the Telegram bot at runtime.
You can run it manually to verify that:
  - `gtts` is installed
  - Google TTS endpoint is reachable from your network

It creates a file `tts_test.mp3` in the current working directory.
"""

from gtts import gTTS

text = "This is a test from Seashell bot."
# Create a TTS object. `lang="en"` means English.
tts = gTTS(text=text, lang="en", slow=False)
# Save MP3 to disk (overwrites if file exists).
tts.save("tts_test.mp3")
print("OK: tts_test.mp3 created")