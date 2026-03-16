from gtts import gTTS

text = "This is a test from Seashell bot."
tts = gTTS(text=text, lang="en", slow=False)
tts.save("tts_test.mp3")
print("OK: tts_test.mp3 created")