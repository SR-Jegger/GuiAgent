import requests
import os
audio_path = "voice.m4a"
url = "http://192.168.137.2:8686/transcribe"

print("exists:", os.path.exists(audio_path))
print("size:", os.path.getsize(audio_path))

with open(audio_path, "rb") as f:
    r = requests.post(
        url,
        files={
            "file": ("voice.m4a", f, "audio/mp4")
        },
        timeout=120
    )

print(r.status_code)
print(r.text)