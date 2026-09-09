import argparse
import base64
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import time
import zipfile
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import requests


OUT = Path('pilot-output')
SESSION = requests.Session()


def run(*args):
    return subprocess.run(args, check=True, capture_output=True).stdout


def probe(path, media_type):
    result = json.loads(run('ffprobe', '-v', 'error', '-show_streams', '-show_format',
                            '-of', 'json', str(path)))
    if not any(s.get('codec_type') == media_type for s in result.get('streams', [])):
        raise ValueError('Generated media is missing the expected stream')
    duration = float(result.get('format', {}).get('duration', 0))
    if duration <= 0:
        raise ValueError('Generated media has no positive duration')
    return duration


def request(method, url, **kwargs):
    response = SESSION.request(method, url, timeout=kwargs.pop('timeout', 180), **kwargs)
    if not response.ok:
        if OUT.exists():
            (OUT / 'provider-error-private.json').write_text(json.dumps({
                'status': response.status_code, 'body': response.text[:16000]}))
        raise RuntimeError(f'Provider HTTP {response.status_code}')
    return response


def audio_blocks(value):
    if isinstance(value, dict):
        if value.get('type') == 'audio' and value.get('data'):
            yield value
        for child in value.values():
            yield from audio_blocks(child)
    elif isinstance(value, list):
        for child in value:
            yield from audio_blocks(child)


def music():
    prompt = ('A 30-second instrumental underscore for a Hindi garment-business explainer. '
              '84 BPM, warm muted electric piano, soft plucked strings, gentle brushed percussion, '
              'a sparse steady groove. Friendly and quietly confident. Minimal melody, no vocals, '
              'no spoken words, no dramatic risers or drops. Keep room for a close recorded voice. '
              'Start gently, remain steady, resolve cleanly.')
    data = request('POST', 'https://generativelanguage.googleapis.com/v1beta/interactions',
                   headers={'x-goog-api-key': os.environ['GOOGLE_API_KEY']},
                   json={'model': 'lyria-3-clip-preview', 'input': prompt}).json()
    blocks = list(audio_blocks(data))
    if not blocks:
        raise RuntimeError('Music response did not contain audio')
    (OUT / 'music.mp3').write_bytes(base64.b64decode(blocks[-1]['data'], validate=True))
    duration = probe(OUT / 'music.mp3', 'audio')
    return {'model': 'lyria-3-clip-preview', 'prompt': prompt, 'attempts': 1,
            'seconds': duration,
            'terms': 'https://ai.google.dev/gemini-api/terms'}


def data_url(path, mime):
    return f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode()


def avatar(duration):
    headers = {'Authorization': 'Key ' + os.environ['FAL_KEY'], 'X-Fal-No-Retry': '1',
               'x-app-fal-disable-fallback': 'true',
               'X-Fal-Object-Lifecycle-Preference': json.dumps({'expiration_duration_seconds': 3600})}
    payload = {'image_url': data_url(OUT / 'portrait.jpg', 'image/jpeg'),
               'audio_url': data_url(OUT / 'voice.wav', 'audio/wav'),
               'resolution': '720p', 'turbo_mode': False,
               'prompt': ('Natural restrained delivery to camera. Preserve the exact person, face, '
                          'clothes and room in the reference. Minimal head movement, natural blinking '
                          'and accurate Hindi lip sync. Fixed camera. No added objects or text.')}
    queued = request('POST', 'https://queue.fal.run/fal-ai/bytedance/omnihuman/v1.5',
                     headers=headers, json=payload).json()
    for field in ('status_url', 'response_url', 'cancel_url'):
        if not queued.get(field, '').startswith('https://queue.fal.run/'):
            raise RuntimeError('Unexpected queue endpoint')
    (OUT / 'avatar-queue-private.json').write_text(json.dumps(queued))
    deadline = time.monotonic() + 720
    while time.monotonic() < deadline:
        status = request('GET', queued['status_url'], headers=headers, timeout=45).json()
        if status.get('status') == 'COMPLETED':
            result = request('GET', queued['response_url'], headers=headers).json()
            url = result.get('video', {}).get('url', '')
            if not url.startswith('https://') or result.get('error'):
                raise RuntimeError('Avatar completed without a usable video')
            (OUT / 'avatar.mp4').write_bytes(request('GET', url).content)
            actual = probe(OUT / 'avatar.mp4', 'video')
            if abs(actual - duration) > 2:
                raise ValueError('Avatar duration differs from the input')
            return {'model': 'fal-ai/bytedance/omnihuman/v1.5', 'attempts': 1,
                    'input_seconds': duration, 'output_seconds': actual,
                    'billed_seconds': result.get('duration'),
                    'request_id': queued.get('request_id'),
                    'voice': 'Original recorded voice, not cloned speech',
                    'output_lifetime_requested_seconds': 3600}
        time.sleep(12)
    request('PUT', queued['cancel_url'], headers=headers, timeout=30)
    raise RuntimeError('Avatar exceeded the 12-minute wait; cancellation requested')


def replicate_avatar(duration):
    model_name = 'bytedance/omni-human-1.5'
    base = 'https://api.replicate.com/v1'
    headers = {'Authorization': 'Bearer ' + os.environ['REPLICATE_API_TOKEN']}
    model = request('GET', f'{base}/models/{model_name}', headers=headers).json()
    version = model.get('latest_version') or {}
    properties = version.get('openapi_schema', {}).get('components', {}).get('schemas', {}).get('Input', {}).get('properties', {})
    if not {'image', 'audio', 'fast_mode'}.issubset(properties):
        raise RuntimeError('Replicate input schema changed; no prediction submitted')
    run('ffmpeg', '-v', 'error', '-y', '-i', str(OUT / 'voice.wav'), '-c:a', 'libmp3lame',
        '-b:a', '96k', str(OUT / 'voice-small.mp3'))
    run('ffmpeg', '-v', 'error', '-y', '-i', str(OUT / 'portrait.jpg'),
        '-vf', 'scale=720:-2', '-q:v', '5', '-frames:v', '1', str(OUT / 'portrait-small.jpg'))
    queued = request('POST', f'{base}/models/{model_name}/predictions',
                     headers={**headers, 'Cancel-After': '12m'}, json={'input': {
                         'image': data_url(OUT / 'portrait-small.jpg', 'image/jpeg'),
                         'audio': data_url(OUT / 'voice-small.mp3', 'audio/mpeg'),
                         'fast_mode': False,
                         'prompt': 'Static camera. Natural restrained movement. Preserve the exact person, clothing and background. Accurate Hindi lip sync.'}}).json()
    (OUT / 'avatar-queue-private.json').write_text(json.dumps(queued))
    urls = queued.get('urls', {})
    for key in ('get', 'cancel'):
        if not urls.get(key, '').startswith(base + '/predictions/'):
            raise RuntimeError('Unexpected prediction endpoint')
    deadline = time.monotonic() + 720
    while time.monotonic() < deadline:
        result = request('GET', urls['get'], headers=headers, timeout=45).json()
        if result.get('status') == 'succeeded':
            url = result.get('output')
            parsed = urlparse(url) if isinstance(url, str) else None
            if not parsed or parsed.scheme != 'https' or not (parsed.hostname == 'replicate.delivery' or
                                                             (parsed.hostname or '').endswith('.replicate.delivery')):
                raise RuntimeError('Unexpected prediction output host')
            response = request('GET', url, headers=headers, allow_redirects=False)
            if response.status_code != 200:
                raise RuntimeError('Prediction output download requires separate redirect review')
            (OUT / 'avatar.mp4').write_bytes(response.content)
            actual = probe(OUT / 'avatar.mp4', 'video')
            if abs(actual - duration) > 2:
                raise ValueError('Avatar duration differs from the input')
            return {'model': model_name, 'provider': 'replicate', 'attempts': 1,
                    'version': version.get('id'), 'license_url': model.get('license_url'),
                    'commercial_publishing_clearance': 'not established; private review only',
                    'request_id': queued.get('id'), 'input_seconds': duration, 'output_seconds': actual,
                    'voice': 'Original recorded voice, not cloned speech'}
        if result.get('status') in ('failed', 'canceled'):
            (OUT / 'avatar-result-private.json').write_text(json.dumps(result))
            raise RuntimeError('Replicate prediction ' + result['status'])
        time.sleep(12)
    request('POST', urls['cancel'], headers=headers, timeout=30)
    raise RuntimeError('Avatar exceeded the 12-minute wait; cancellation requested')


def encrypt_output(public_key, destination):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(OUT.iterdir()):
            if file.is_file():
                archive.write(file, file.name)
    key, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
    wrapped = public_key.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                                 algorithm=hashes.SHA256(), label=None))
    header = json.dumps({'v': 1, 'key': base64.b64encode(wrapped).decode(),
                         'nonce': base64.b64encode(nonce).decode()}).encode()
    encrypted = AESGCM(key).encrypt(nonce, buf.getvalue(), header)
    Path(destination).write_bytes(struct.pack('>I', len(header)) + header + encrypted)


def validate_inputs(source_key, duration, portrait_at, public_pem):
    if not re.fullmatch(r'p/[A-Za-z0-9_.-]+\.mp4', source_key):
        raise ValueError('Source must be an existing MP4 under the own p/ prefix')
    if not 3 <= duration <= 20:
        raise ValueError('Preview duration must be 3 to 20 seconds')
    if not 0.5 <= portrait_at <= 20:
        raise ValueError('Portrait time must be within the first 20 seconds')
    key = serialization.load_pem_public_key(public_pem.encode())
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 3072:
        raise ValueError('A recipient RSA public key of at least 3072 bits is required')
    return key


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-key', required=True)
    ap.add_argument('--seconds', type=float, default=18.6)
    ap.add_argument('--portrait-at', type=float, default=5)
    ap.add_argument('--avatar', action='store_true')
    ap.add_argument('--avatar-provider', choices=['fal', 'replicate'], default='fal')
    ap.add_argument('--skip-music', action='store_true')
    args = ap.parse_args()
    public_key = validate_inputs(args.source_key, args.seconds, args.portrait_at,
                                 os.environ['PILOT_PUBLIC_KEY'])
    OUT.mkdir(exist_ok=True)
    meta = {'source_key': args.source_key, 'seconds': args.seconds, 'private_preview': True}
    try:
        import boto3
        boto3.client('s3').download_file('bulkplaintshirt.com', args.source_key, str(OUT / 'source.mp4'))
        actual = float(run('ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                           '-of', 'default=nw=1:nk=1', str(OUT / 'source.mp4')))
        if actual < args.seconds + 0.5 or actual < args.portrait_at:
            raise ValueError('Source is shorter than requested preview')
        run('ffmpeg', '-v', 'error', '-y', '-ss', '0.5', '-i', str(OUT / 'source.mp4'),
            '-t', str(args.seconds), '-vn', '-ac', '1', '-ar', '24000', str(OUT / 'voice.wav'))
        probe(OUT / 'voice.wav', 'audio')
        run('ffmpeg', '-v', 'error', '-y', '-ss', str(args.portrait_at), '-i', str(OUT / 'source.mp4'),
            '-frames:v', '1', '-q:v', '2', str(OUT / 'portrait.jpg'))
        tasks = [] if args.skip_music else [('music', music)]
        if args.avatar:
            generator = replicate_avatar if args.avatar_provider == 'replicate' else avatar
            tasks.append(('avatar', lambda: generator(args.seconds)))
        for name, fn in tasks:
            try:
                meta[name] = {'status': 'succeeded', **fn()}
                print(name + ': succeeded', flush=True)
            except Exception as exc:
                message = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__
                meta[name] = {'status': 'failed', 'error': message}
                print(name + ': failed (' + message + ')', flush=True)
    finally:
        (OUT / 'metadata.json').write_text(json.dumps(meta, indent=2))
        encrypt_output(public_key, 'private-pilot.enc')
    if any(meta.get(name, {}).get('status') != 'succeeded' for name, _ in tasks):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
