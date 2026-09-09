import base64
import hashlib
import json
import os
from pathlib import Path
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import requests

from tools import private_video_pilot as pilot


MODEL = 'lyria-3.5'
ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/interactions'
OUT = Path('music-pair-output')
DESTINATION = Path('private-music-pair.enc')


def validate_inputs(prompt_a, prompt_b, public_pem):
    prompts = [prompt_a.strip(), prompt_b.strip()]
    if any(not 30 <= len(prompt) <= 6000 for prompt in prompts):
        raise ValueError('Each music direction requires an explicit 30 to 6000 character prompt')
    if prompts[0] == prompts[1]:
        raise ValueError('The two music prompts must differ')
    key = serialization.load_pem_public_key(public_pem.encode())
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 3072:
        raise ValueError('A recipient RSA public key of at least 3072 bits is required')
    return prompts, key


def generate_track(prompt, destination, api_key):
    response = requests.post(ENDPOINT, headers={'x-goog-api-key': api_key},
                             json={'model': MODEL, 'input': prompt},
                             timeout=(15, 240), allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError(f'Provider HTTP {response.status_code}; no retry made')
    data = response.json()
    blocks = list(pilot.audio_blocks(data))
    if not blocks:
        raise RuntimeError('Provider returned no inline audio')
    block = blocks[-1]
    encoded = block['data']
    if not isinstance(encoded, str) or len(encoded) > 40_000_000:
        raise ValueError('Generated audio exceeds the private-preview size limit')
    raw = base64.b64decode(encoded, validate=True)
    mime = block.get('mime_type', block.get('mimeType', 'audio/mpeg'))
    suffix = '.wav' if mime in ('audio/wav', 'audio/x-wav', 'audio/wave') else '.mp3'
    file = destination.with_suffix(suffix)
    file.write_bytes(raw)
    duration = pilot.probe(file, 'audio')
    if duration > 240:
        file.unlink()
        raise ValueError('Generated audio exceeds four minutes')
    return {'file': file.name, 'seconds': duration, 'bytes': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest(), 'mime_type': mime,
            'interaction_id': data.get('id')}


def generate_pair(prompt_a, prompt_b, public_pem, api_key, output_dir=OUT,
                  destination=DESTINATION):
    prompts, public_key = validate_inputs(prompt_a, prompt_b, public_pem)
    if not api_key:
        raise ValueError('Existing GOOGLE_API_KEY is required')
    output_dir, destination = Path(output_dir), Path(destination)
    if output_dir.exists() or destination.exists():
        raise ValueError('Output already exists; refusing to repeat generation')
    output_dir.mkdir(parents=True)
    meta = {'private_preview': True, 'model': MODEL, 'attempt_limit': 2,
            'automatic_retries': 0, 'created_unix': time.time(),
            'terms_url': 'https://ai.google.dev/gemini-api/terms', 'tracks': []}
    metadata_file = output_dir / 'metadata.json'
    try:
        for label, prompt in zip(('a', 'b'), prompts):
            row = {'label': label, 'prompt': prompt, 'status': 'request-started', 'attempts': 1}
            meta['tracks'].append(row)
            metadata_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
            try:
                row.update(generate_track(prompt, output_dir / ('music-' + label), api_key))
                row['status'] = 'succeeded'
            except Exception as exc:
                row['status'] = 'failed'
                row['error'] = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__
            metadata_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
            print('music-' + label + ': ' + row['status'], flush=True)
    finally:
        metadata_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        previous = pilot.OUT
        try:
            pilot.OUT = output_dir
            pilot.encrypt_output(public_key, destination)
        finally:
            pilot.OUT = previous
    return all(row['status'] == 'succeeded' for row in meta['tracks'])


def main():
    succeeded = generate_pair(os.environ.get('MUSIC_PROMPT_A', ''),
                              os.environ.get('MUSIC_PROMPT_B', ''),
                              os.environ.get('PILOT_PUBLIC_KEY', ''),
                              os.environ.get('GOOGLE_API_KEY', ''))
    raise SystemExit(0 if succeeded else 1)


if __name__ == '__main__':
    main()
