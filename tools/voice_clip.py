#!/usr/bin/env python3
"""One short clip in Ketu's ElevenLabs clone (the one voice for all his AI voice work), for app sounds and the like.
Usage: ELEVENLABS_API_KEY=... python3 tools/voice_clip.py "text" out.mp3"""
import json
import os
import sys
import urllib.request

VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "cejtKjfE9sHUZ1FnUYEV")
MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_v4")

text, out = sys.argv[1], sys.argv[2]
req = urllib.request.Request(
    f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}?output_format=mp3_44100_128",
    data=json.dumps({"text": text, "model_id": MODEL, "voice_settings": {"stability": 0.5, "similarity_boost": 0.9}}).encode(),
    headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=120) as r, open(out, "wb") as f:
    f.write(r.read())
print(f"wrote {out}: {os.path.getsize(out)} bytes, voice {VOICE}, model {MODEL}")
