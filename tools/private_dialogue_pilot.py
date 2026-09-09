import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import urlparse

import requests
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import private_video_pilot as shared


OUT = Path('dialogue-output')
shared.OUT = OUT
VOICE_ID = 'cejtKjfE9sHUZ1FnUYEV'
VOICE_MODEL = 'eleven_multilingual_v2'
VIDEO_MODEL = 'heygen/lipsync-precision'
API = 'https://api.replicate.com/v1'
SCRIPT = ('बल्क ऑर्डर से पहले एक-दो सैंपल मंगाकर कपड़ा, फिटिंग और सिलाई चेक करो। '
          'प्रिंटिंग करते हो, तो उसी सैंपल पर अपना प्रिंट भी टेस्ट करो। '
          'सब देखकर बड़ा ऑर्डर तय करो। आप सबसे पहले क्या चेक करते हो?')
SESSION = requests.Session()
RESUME_FORMAT = 'private-dialogue-resume-v1'
RESUME_MAGIC = b'DIALOGUERESUME1\n'
RESUME_ORIGINAL_RUN = '34377197796'
MAX_RESUME_BYTES = 8 * 1024 * 1024


def save(name, data):
    path = OUT / name
    with path.open('w', encoding='utf-8') as target:
        json.dump(data, target, indent=2, ensure_ascii=False)
        target.flush()
        os.fsync(target.fileno())


def request(method, url, **kwargs):
    response = SESSION.request(method, url, timeout=kwargs.pop('timeout', 120),
                               allow_redirects=False, **kwargs)
    if response.status_code not in (200, 201, 202, 204):
        save('provider-error-private.json', {'status': response.status_code, 'body': response.text[:16000]})
        raise RuntimeError(f'Provider HTTP {response.status_code}; private diagnostic saved')
    return response


def preflight(skip_voice=False):
    checks = {'tts': 'reuse_verified_speech' if skip_voice else 'generate_once'}
    if not skip_voice:
        eleven_headers = {'xi-api-key': os.environ['ELEVENLABS_API_KEY']}
        voice = request('GET', f'https://api.elevenlabs.io/v1/voices/{VOICE_ID}', headers=eleven_headers).json()
        fine_tuning = (voice.get('fine_tuning') or {}).get('state') or {}
        voice_check = {'voice_id': voice.get('voice_id'), 'name': voice.get('name'),
                       'category': voice.get('category'), 'fine_tuning': fine_tuning}
        save('voice-preflight-private.json', voice_check)
        if voice.get('voice_id') != VOICE_ID or fine_tuning.get(VOICE_MODEL) != 'fine_tuned':
            raise ValueError('The existing Ketu professional voice is not ready for Multilingual v2; no generation')
        usage = request('GET', 'https://api.elevenlabs.io/v1/user/subscription', headers=eleven_headers).json()
        save('voice-usage-before-private.json', usage)
        remaining = usage.get('character_limit', 0) - usage.get('character_count', 0)
        if remaining < len(SCRIPT):
            raise ValueError('Insufficient existing ElevenLabs character allowance; no purchase or generation')
        checks.update({'voice': voice_check, 'voice_characters': len(SCRIPT),
                       'existing_characters_remaining': remaining})
    headers = {'Authorization': 'Bearer ' + os.environ['REPLICATE_API_TOKEN']}
    model = request('GET', f'{API}/models/{VIDEO_MODEL}', headers=headers).json()
    version = model.get('latest_version') or {}
    schema = version.get('openapi_schema', {}).get('components', {}).get('schemas', {}).get('Input', {})
    required = {'video', 'audio', 'enable_dynamic_duration', 'disable_music_track', 'enable_speech_enhancement'}
    if not required.issubset(schema.get('properties', {})):
        raise ValueError('HeyGen Precision schema changed; no generation')
    save('model-preflight-private.json', {'model': VIDEO_MODEL, 'version': version.get('id'),
                                          'license_url': model.get('license_url'), 'input_schema': schema})
    checks.update({'video_model': VIDEO_MODEL, 'video_model_version': version.get('id'),
                   'pricing_usd_per_output_second': 0.0667,
                   'max_video_estimate_usd': 1.334, 'pricing_is_estimate_not_invoice': True})
    return checks


def validate_complete_speech(duration, alignment):
    if not 10 <= duration <= 20:
        raise ValueError('Full speech is outside the 10–20s bound; do not submit video')
    if not isinstance(alignment, dict) or alignment.get('characters') != list(SCRIPT):
        raise ValueError('Speech alignment does not contain the exact complete script')
    starts = alignment.get('character_start_times_seconds')
    ends = alignment.get('character_end_times_seconds')
    if not isinstance(starts, list) or not isinstance(ends, list) or len(starts) != len(SCRIPT) or len(ends) != len(SCRIPT):
        raise ValueError('Speech alignment timestamps are incomplete')
    previous_start = previous_end = 0
    for start, end in zip(starts, ends):
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start <= end <= duration + 0.1
                or start < previous_start or end < previous_end):
            raise ValueError('Speech alignment timing differs from the complete audio')
        previous_start, previous_end = start, end
    if duration - ends[-1] > 1:
        raise ValueError('Speech alignment ends too far before the audio')


def resume_speech(s3, key, expected_sha256, source_sha256, meta):
    response = s3.get_object(Bucket='bulkplaintshirt.com', Key=key)
    body = response['Body']
    try:
        if response.get('ContentLength', MAX_RESUME_BYTES + 1) > MAX_RESUME_BYTES:
            raise ValueError('Encrypted resume pack exceeds the size bound')
        encrypted = body.read(MAX_RESUME_BYTES + 1)
    finally:
        body.close()
    if len(encrypted) > MAX_RESUME_BYTES or hashlib.sha256(encrypted).hexdigest() != expected_sha256:
        raise ValueError('Encrypted resume pack differs from the reviewed hash')
    if not encrypted.startswith(RESUME_MAGIC) or len(encrypted) < len(RESUME_MAGIC) + 28:
        raise ValueError('Unsupported encrypted resume pack')
    try:
        secret = base64.b64decode(os.environ['PRIVATE_DIALOGUE_RESUME_KEY'], validate=True)
        if len(secret) != 32:
            raise ValueError('Resume key must contain exactly 32 bytes')
        offset = len(RESUME_MAGIC)
        plain = AESGCM(secret).decrypt(encrypted[offset:offset + 12], encrypted[offset + 12:],
                                      RESUME_FORMAT.encode())
        pack = json.loads(plain)
    except (KeyError, ValueError, InvalidTag) as exc:
        raise ValueError('Resume key or authenticated payload is invalid') from exc
    if (not isinstance(pack, dict) or pack.get('format') != RESUME_FORMAT
            or pack.get('original_run_id') != RESUME_ORIGINAL_RUN
            or type(pack.get('original_video_attempts')) is not int or pack['original_video_attempts'] != 0
            or pack.get('source_sha256') != source_sha256 or pack.get('script') != SCRIPT):
        raise ValueError('Resume pack does not match the reviewed original speech-only run')
    receipt = pack.get('original_tts_receipt')
    if (not isinstance(receipt, dict) or not receipt.get('request_id')
            or receipt.get('attempts') != 1 or receipt.get('characters_requested') != len(SCRIPT)):
        raise ValueError('Original TTS receipt is missing or inconsistent')
    try:
        audio = base64.b64decode(pack['speech_wav_base64'], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Resume WAV is invalid') from exc
    if (hashlib.sha256(audio).hexdigest() != pack.get('speech_wav_sha256')
            or audio[:4] != b'RIFF' or audio[8:12] != b'WAVE'):
        raise ValueError('Resume WAV differs from the original reviewed audio')
    (OUT / 'speech.wav').write_bytes(audio)
    duration = shared.probe(OUT / 'speech.wav', 'audio')
    validate_complete_speech(duration, pack.get('alignment'))
    save('speech-timestamps.json', {'alignment': pack['alignment']})
    meta.update({'tts_status': 'reused', 'tts_requests_this_run': 0,
                 'tts_receipt': receipt, 'original_tts_run_id': pack['original_run_id'],
                 'resume_audio_key': key, 'resume_audio_sha256': expected_sha256,
                 'speech_wav_sha256': pack['speech_wav_sha256'], 'speech_seconds': duration,
                 'estimated_video_cost_usd': round(duration * 0.0667, 4)})
    save('metadata.json', meta)
    return duration


def make_speech(meta):
    meta['tts_status'] = 'started'
    save('metadata.json', meta)
    response = request('POST', f'https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/with-timestamps',
        headers={'xi-api-key': os.environ['ELEVENLABS_API_KEY']}, params={'output_format': 'mp3_44100_128'},
        json={'text': SCRIPT, 'model_id': VOICE_MODEL, 'language_code': 'hi',
              'voice_settings': {'stability': 0.5, 'similarity_boost': 0.75, 'style': 0.0,
                                 'use_speaker_boost': True}})
    data = response.json()
    (OUT / 'speech-raw.mp3').write_bytes(base64.b64decode(data.pop('audio_base64'), validate=True))
    save('speech-timestamps.json', data)
    meta['tts_status'] = 'succeeded'
    meta['tts_receipt'] = {'request_id': response.headers.get('request-id'),
                           'character_cost': response.headers.get('character-cost'),
                           'characters_requested': len(SCRIPT), 'attempts': 1}
    save('metadata.json', meta)
    shared.run('ffmpeg', '-v', 'error', '-y', '-i', str(OUT / 'speech-raw.mp3'),
               '-af', 'highpass=f=60,loudnorm=I=-16:TP=-1.5:LRA=11', '-ar', '44100',
               '-ac', '1', str(OUT / 'speech.wav'))
    duration = shared.probe(OUT / 'speech.wav', 'audio')
    validate_complete_speech(duration, data.get('alignment'))
    meta['speech_seconds'] = duration
    meta['estimated_video_cost_usd'] = round(duration * 0.0667, 4)
    save('metadata.json', meta)
    return duration


def upload_file(path, mime, headers):
    with path.open('rb') as source:
        result = request('POST', f'{API}/files', headers=headers,
                         files={'content': (path.name, source, mime)}).json()
    save(path.stem + '-upload-private.json', result)
    url = (result.get('urls') or {}).get('get', '')
    if not url.startswith(API + '/files/'):
        raise ValueError('Unexpected Replicate file URL')
    return url


def make_video(duration, meta):
    shared.run('ffmpeg', '-v', 'error', '-y', '-i', str(OUT / 'source.mp4'), '-t', str(duration),
               '-map', '0:v:0', '-c:v', 'copy', '-an', '-movflags', '+faststart',
               str(OUT / 'source-for-lipsync.mp4'))
    input_seconds = shared.probe(OUT / 'source-for-lipsync.mp4', 'video')
    if abs(input_seconds - duration) > 0.1:
        raise ValueError('Source and full speech durations do not match')
    headers = {'Authorization': 'Bearer ' + os.environ['REPLICATE_API_TOKEN']}
    video_url = upload_file(OUT / 'source-for-lipsync.mp4', 'video/mp4', headers)
    audio_url = upload_file(OUT / 'speech.wav', 'audio/wav', headers)
    meta['video_status'] = 'started'
    save('metadata.json', meta)
    queued = request('POST', f'{API}/models/{VIDEO_MODEL}/predictions',
        headers={**headers, 'Cancel-After': '12m'}, json={'input': {'video': video_url, 'audio': audio_url,
            'enable_dynamic_duration': False, 'disable_music_track': False,
            'enable_speech_enhancement': False}}).json()
    save('video-queue-private.json', queued)
    meta.update({'prediction_id': queued.get('id'), 'video_status': queued.get('status', 'submitted')})
    save('metadata.json', meta)
    urls = queued.get('urls') or {}
    if not all(urls.get(k, '').startswith(API + '/predictions/') for k in ('get', 'cancel')):
        raise ValueError('Unexpected Replicate prediction endpoint; no resubmission')
    deadline = time.monotonic() + 720
    while time.monotonic() < deadline:
        result = request('GET', urls['get'], headers=headers, timeout=45).json()
        save('video-result-private.json', result)
        status = result.get('status')
        if status == 'succeeded':
            url = result.get('output')
            parsed = urlparse(url) if isinstance(url, str) else None
            if not parsed or parsed.scheme != 'https' or not (parsed.hostname == 'replicate.delivery' or
                                                        (parsed.hostname or '').endswith('.replicate.delivery')):
                raise ValueError('Unexpected output host')
            with request('GET', url, headers=headers, stream=True) as response, (OUT / 'avatar-v2.mp4').open('wb') as output:
                size = 0
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > 200 * 1024 * 1024:
                        raise ValueError('Output exceeded bounded download size')
                    output.write(chunk)
            actual = shared.probe(OUT / 'avatar-v2.mp4', 'video')
            if abs(actual - duration) > 0.25 or actual > 20.25:
                raise ValueError('Generated output duration changed unexpectedly; inspect privately')
            meta.update({'video_status': 'succeeded', 'output_seconds': actual,
                         'video_generation_attempts': 1, 'provider_metrics': result.get('metrics')})
            return
        if status in ('failed', 'canceled'):
            meta['video_status'] = status
            raise RuntimeError('HeyGen Precision prediction ' + status)
        time.sleep(12)
    request('POST', urls['cancel'], headers=headers, timeout=30)
    meta['video_status'] = 'cancellation_requested_after_timeout'
    raise RuntimeError('12-minute wait exceeded; cancellation requested, no second prediction')


def main():
    parser = argparse.ArgumentParser(description='One private real-video dialogue replacement using the existing Ketu voice')
    parser.add_argument('--source-key', required=True)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--resume-audio-key', default='')
    parser.add_argument('--resume-audio-sha256', default='')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    key = shared.validate_inputs(args.source_key, 20, 5, os.environ['PILOT_PUBLIC_KEY'])
    if not re.fullmatch('[0-9a-f]{64}', args.source_sha256):
        raise ValueError('Reviewed source SHA-256 required')
    if bool(args.resume_audio_key) != bool(args.resume_audio_sha256):
        raise ValueError('Resume requires both the encrypted S3 key and its SHA-256')
    if args.resume_audio_key and (not re.fullmatch(r'p/[A-Za-z0-9_.-]+\.enc', args.resume_audio_key)
                                 or not re.fullmatch('[0-9a-f]{64}', args.resume_audio_sha256)):
        raise ValueError('Resume must use a reviewed encrypted pack under the owned p/ prefix')
    if args.execute and os.environ.get('GITHUB_RUN_ATTEMPT', '1') != '1':
        raise ValueError('A workflow rerun cannot repeat paid generation; inspect the first encrypted artifact')
    OUT.mkdir(exist_ok=False)
    meta = {'private_preview': True, 'ai_face': True, 'ai_voice': True, 'script': SCRIPT,
            'source_key': args.source_key, 'source_sha256': args.source_sha256,
            'mode': 'execute' if args.execute else 'preflight_only', 'music': 'none'}
    result = 1
    try:
        import boto3
        s3 = boto3.client('s3')
        s3.download_file('bulkplaintshirt.com', args.source_key, str(OUT / 'source.mp4'))
        if hashlib.sha256((OUT / 'source.mp4').read_bytes()).hexdigest() != args.source_sha256:
            raise ValueError('Source differs from the reviewed real speaking video')
        duration = shared.probe(OUT / 'source.mp4', 'video')
        if not 19.9 <= duration <= 20.1:
            raise ValueError('Expected the reviewed twenty-second source')
        speech_seconds = None
        if args.resume_audio_key:
            speech_seconds = resume_speech(s3, args.resume_audio_key, args.resume_audio_sha256,
                                           args.source_sha256, meta)
        meta['preflight'] = preflight(skip_voice=bool(args.resume_audio_key))
        save('metadata.json', meta)
        if args.execute:
            if not args.resume_audio_key:
                speech_seconds = make_speech(meta)
            make_video(speech_seconds, meta)
        meta['status'] = 'succeeded'
        result = 0
        print('Private dialogue pilot: succeeded; all details are in the encrypted artifact', flush=True)
    except Exception as exc:
        meta['status'] = 'failed'
        meta['error'] = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__
        print('Private dialogue pilot: stopped; inspect encrypted diagnostic before any retry', flush=True)
    finally:
        save('metadata.json', meta)
        shared.encrypt_output(key, 'private-dialogue-pilot.enc')
    return result


if __name__ == '__main__':
    raise SystemExit(main())
