"""The ElevenLabs model each kind of voice job speaks with, read from voice_models.json. Never raises."""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_ID = re.compile(r'eleven_[a-z0-9_]+')


def _read(kind, default, root):
    try:
        with open(os.path.join(str(root or ROOT), 'voice_models.json'), encoding='utf-8') as handle:
            value = json.load(handle)[kind]
        return value if isinstance(value, str) and MODEL_ID.fullmatch(value) else default
    except Exception:
        return default


def flagship(default='eleven_v4', root=None):
    return _read('flagship', default, root)


def realtime(default='eleven_v4_turbo', root=None):
    return _read('realtime', default, root)
