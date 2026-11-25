"""The plan's spoken lines in Ketu's cloned voice, with word and sentence times read from the voice itself.

Same voice, model and settings as every other voice job (tools/voice_runtime.production_voice_config). The times come
from ElevenLabs' character timestamps, never from a guess; a voice whose timestamps do not cover the exact text is
refused before any video is made.
"""

import json
from pathlib import Path
import subprocess

from tools import tts_alignment, voice_runtime


class VoiceHold(RuntimeError):
    pass


def duration_of(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def speak(plan, out_dir, key, normalize=None, synth=tts_alignment.speech_with_timestamps):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    normalize = normalize or voice_runtime.load_normalize_for_tts()
    config = voice_runtime.production_voice_config()
    says = [line['say'].strip() for line in plan['lines']]
    script = ' '.join(says)
    text = normalize(script)
    audio, alignment, request_id = synth(key, config['voice_id'], config['model_id'], text, config['voice_settings'])
    mp3 = out_dir / 'voice.mp3'
    mp3.write_bytes(audio)
    duration = duration_of(mp3)
    evidence = tts_alignment.alignment_evidence(text, alignment, duration)
    if not evidence.get('exact'):
        raise VoiceHold(f"voice timestamps do not cover the script ({evidence.get('reason')})")
    spans = tts_alignment.sentence_spans(text, alignment)
    sentences = tts_alignment.SENTENCE_BREAK.split(script)
    if not spans or len(spans) != len(sentences) or len(sentences) != len(says):
        raise VoiceHold(f'{len(says)} lines but {len(spans or [])} timed sentences')
    raw = script.split()
    display = [token.strip('.,!?…"') or token for token in raw]
    words, info = tts_alignment.aligned_words(raw, display, text, alignment, normalize)
    if not info.get('highlight_verified'):
        raise VoiceHold(f'caption words could not be timed from the voice ({info})')
    kept = [token for token, shown in zip(raw, display) if shown.strip() and tts_alignment.UNIT.findall(normalize(token))]
    if len(kept) != len(words):
        raise VoiceHold('caption words and timed words differ in number')
    for word, token in zip(words, kept):
        word['raw'] = token
    timed = [{'text': say, 'start': span['start'], 'end': span['end']} for say, span in zip(says, spans)]
    result = {'duration': duration, 'model_id': config['model_id'], 'voice_id': config['voice_id'], 'request_id': request_id,
              'spoken_text': text, 'sentences': timed, 'words': words}
    (out_dir / 'voice.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    return result
