#!/usr/bin/env python3
import argparse
import json
import os
import re
import urllib.error
import urllib.request


VOICE_ID = 'cejtKjfE9sHUZ1FnUYEV'
MODEL_ID = 'eleven_v3'


def safe_code(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', value) else None


def request(path, key, payload=None):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request('https://api.elevenlabs.io/v1/' + path, data=body,
                                 headers={'xi-api-key': key, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=75) as response:
            return response.status, response.headers.get('Content-Type', ''), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get('Content-Type', ''), error.read(65536)
    except Exception as error:
        print(json.dumps({'request_failed': type(error).__name__}))
        return 0, '', b''


def object_body(body):
    try:
        value = json.loads(body)
        return value if isinstance(value, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def error_code(body):
    value = object_body(body)
    detail = value.get('detail')
    return safe_code(detail.get('status')) if isinstance(detail, dict) else None


def subscription_summary(value):
    result = {key: value.get(key) for key in (
        'tier', 'status', 'character_count', 'character_limit', 'has_open_invoices',
        'can_use_professional_voice_cloning')}
    result['has_open_invoices_type'] = type(value.get('has_open_invoices')).__name__
    result['tier'] = safe_code(result['tier'])
    result['status'] = safe_code(result['status'])
    for key in ('character_count', 'character_limit'):
        if type(result[key]) is not int:
            result[key] = None
    for key in ('has_open_invoices', 'can_use_professional_voice_cloning'):
        if type(result[key]) is not bool:
            result[key] = None
    used, limit = result['character_count'], result['character_limit']
    result['chars_left'] = max(0, limit - used) if used is not None and limit is not None else None
    invoices = value.get('open_invoices')
    invoices = invoices if isinstance(invoices, list) else []
    result['open_invoice_count'] = len(invoices)
    result['invoice_payment_statuses'] = []
    for invoice in invoices:
        if not isinstance(invoice, dict):
            continue
        attempts = invoice.get('payment_intent_statusses')
        attempts = attempts if isinstance(attempts, list) else []
        result['invoice_payment_statuses'].append({
            'latest': safe_code(invoice.get('payment_intent_status')),
            'attempts': [safe_code(status) for status in attempts if safe_code(status)]})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read dependency state; optional tiny private TTS capability probe.')
    parser.add_argument('--tts-probe', action='store_true')
    args = parser.parse_args(argv)
    key = os.environ.get('ELEVENLABS_API_KEY', '').strip()
    if not key:
        parser.error('ELEVENLABS_API_KEY is required')
    status, _, body = request('user/subscription', key)
    print(json.dumps({'subscription_http_status': status,
                      **subscription_summary(object_body(body)), 'error_code': error_code(body)}))
    voice_status, _, voice_body = request('voices/' + VOICE_ID, key)
    voice = object_body(voice_body)
    print(json.dumps({'voice_http_status': voice_status, 'voice_available': voice_status == 200,
                      'voice_category': safe_code(voice.get('category')),
                      'error_code': error_code(voice_body)}))
    if not args.tts_probe:
        print(json.dumps({'tts_probe': 'not_requested'}))
        return 0 if status == 200 and voice_status == 200 else 1
    status, content_type, audio = request('text-to-speech/' + VOICE_ID + '?output_format=mp3_44100_128', key, {
        'text': 'यह आवाज़ की जाँच है।', 'model_id': MODEL_ID,
        'voice_settings': {'stability': 0.50, 'similarity_boost': 0.75,
                           'style': 0.00, 'use_speaker_boost': True}})
    valid_audio = status == 200 and len(audio) > 100 and (
        audio.startswith(b'ID3') or (audio[0] == 255 and audio[1] & 224 == 224))
    print(json.dumps({'tts_probe': 'passed' if valid_audio else 'failed', 'http_status': status,
                      'model': MODEL_ID, 'audio_bytes': len(audio) if valid_audio else 0,
                      'response_content_type': content_type.split(';')[0] if '/' in content_type else None,
                      'error_code': error_code(audio) if not valid_audio else None,
                      'attempts': 1}))
    return 0 if valid_audio else 1


if __name__ == '__main__':
    raise SystemExit(main())
