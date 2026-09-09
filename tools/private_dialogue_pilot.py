import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlparse

import requests
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


def preflight():
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
    headers = {'Authorization': 'Bearer ' + os.environ['REPLICATE_API_TOKEN']}
    model = request('GET', f'{API}/models/{VIDEO_MODEL}', headers=headers).json()
    version = model.get('latest_version') or {}
    schema = version.get('openapi_schema', {}).get('components', {}).get('schemas', {}).get('Input', {})
    required = {'video', 'audio', 'enable_dynamic_duration', 'disable_music_track', 'enable_speech_enhancement'}
    if not required.issubset(schema.get('properties', {})):
        raise ValueError('HeyGen Precision schema changed; no generation')
    save('model-preflight-private.json', {'model': VIDEO_MODEL, 'version': version.get('id'),
                                          'license_url': model.get('license_url'), 'input_schema': schema})
    return {'voice': voice_check, 'voice_characters': len(SCRIPT), 'existing_characters_remaining': remaining,
            'video_model': VIDEO_MODEL, 'video_model_version': version.get('id'),
            'pricing_usd_per_output_second': 0.0667,
            'max_video_estimate_usd': 1.334, 'pricing_is_estimate_not_invoice': True}


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
    if not 12 <= duration <= 20:
        raise ValueError('Generated full sentence is outside the 12–20s bound; preserve audio, do not submit video')
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
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    key = shared.validate_inputs(args.source_key, 20, 5, os.environ['PILOT_PUBLIC_KEY'])
    if not re.fullmatch('[0-9a-f]{64}', args.source_sha256):
        raise ValueError('Reviewed source SHA-256 required')
    if args.execute and os.environ.get('GITHUB_RUN_ATTEMPT', '1') != '1':
        raise ValueError('A workflow rerun cannot repeat paid generation; inspect the first encrypted artifact')
    OUT.mkdir(exist_ok=False)
    meta = {'private_preview': True, 'ai_face': True, 'ai_voice': True, 'script': SCRIPT,
            'source_key': args.source_key, 'source_sha256': args.source_sha256,
            'mode': 'execute' if args.execute else 'preflight_only', 'music': 'none'}
    result = 1
    try:
        import boto3
        boto3.client('s3').download_file('bulkplaintshirt.com', args.source_key, str(OUT / 'source.mp4'))
        if hashlib.sha256((OUT / 'source.mp4').read_bytes()).hexdigest() != args.source_sha256:
            raise ValueError('Source differs from the reviewed real speaking video')
        duration = shared.probe(OUT / 'source.mp4', 'video')
        if not 19.9 <= duration <= 20.1:
            raise ValueError('Expected the reviewed twenty-second source')
        meta['preflight'] = preflight()
        save('metadata.json', meta)
        if args.execute:
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
