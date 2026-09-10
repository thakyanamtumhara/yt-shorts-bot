import argparse
import base64
import io
import json
import math
import os
from pathlib import Path
import re
import wave

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import private_dialogue_episodes as episodes


OUT = Path('delivery-audition-output')
FORMAT = 'private-delivery-audition-v1'
MAGIC = b'DELIVERYAUDITION1\n'
VOICE_ID = 'cejtKjfE9sHUZ1FnUYEV'
MODEL_ID = 'eleven_multilingual_v2'
IDS = ('fit-open', 'fit-close-a', 'fit-close-b', 'print-close-a', 'print-close-b')
GROUPS = ('fit', 'print-sample')
MAX_PACK = 20 * 1024 * 1024
FILTER = 'highpass=f=60,loudnorm=I=-16:TP=-1.5:LRA=11'
shared = episodes.shared
digest, encoded = episodes.digest, episodes.encoded


def save(name, data):
    with (OUT / name).open('wb') as target:
        target.write(encoded(data))
        target.flush()
        os.fsync(target.fileno())


def plain_text(text, maximum, minimum=0):
    if (not isinstance(text, str) or not minimum <= len(text) <= maximum
            or text != text.strip() or re.search(r'[<>\[\]\x00-\x1f]|https?://|www\.', text)):
        raise ValueError('Exact plain text without markup or URLs is required')


def wav_seconds(raw):
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('Only a small baseline WAV is allowed')
    with wave.open(io.BytesIO(raw), 'rb') as audio:
        if audio.getcomptype() != 'NONE' or audio.getnchannels() != 1 or audio.getsampwidth() != 2:
            raise ValueError('Expected mono PCM16 baseline WAV')
        frames, rate = audio.getnframes(), audio.getframerate()
        if not 16000 <= rate <= 48000 or not 0.2 <= frames / rate <= 45:
            raise ValueError('Baseline must contain a bounded spoken ending')
        if len(audio.readframes(frames)) != frames * 2:
            raise ValueError('Baseline WAV is truncated')
        return frames / rate


def validate_pack(pack):
    fields = {'format', 'voice_id', 'model_id', 'snippets', 'baselines'}
    if (not isinstance(pack, dict) or set(pack) != fields or pack['format'] != FORMAT
            or pack['voice_id'] != VOICE_ID or pack['model_id'] != MODEL_ID):
        raise ValueError('Only the existing approved professional voice and v2 are allowed')
    snippets = pack['snippets']
    if (not isinstance(snippets, list) or len(snippets) != 5
            or any(not isinstance(s, dict) for s in snippets)
            or [s.get('id') for s in snippets] != list(IDS)):
        raise ValueError('Exactly the five reviewed audition snippets are allowed in order')
    for snippet in snippets:
        if set(snippet) != {'id', 'script', 'previous_text', 'next_text', 'settings'}:
            raise ValueError('Unexpected snippet fields')
        plain_text(snippet['script'], 260, 10)
        if not re.search('[ऄ-हक़-ॡ]', snippet['script']) or snippet['script'][-1] not in '।?!':
            raise ValueError('Snippet must be a complete Hindi sentence')
        plain_text(snippet['previous_text'], 1400)
        plain_text(snippet['next_text'], 1400)
        opening = snippet['id'] == 'fit-open'
        if ((opening and (snippet['previous_text'] or not snippet['next_text']))
                or (not opening and (not snippet['previous_text'] or snippet['next_text']))):
            raise ValueError('Opening needs following context; endings need preceding context only')
        settings = snippet['settings']
        if (not isinstance(settings, dict) or set(settings) != {'speed', 'stability', 'style'}
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in settings.values())
                or not 0.35 <= settings['stability'] <= 0.5 or not 0 <= settings['style'] <= 0.35
                or (opening and settings['speed'] not in (0.94, 1.0))
                or (not opening and not 0.90 <= settings['speed'] <= 0.94)):
            raise ValueError('Voice settings exceed the bounded audition')
    if not isinstance(pack['baselines'], dict) or set(pack['baselines']) != set(GROUPS):
        raise ValueError('Both immutable original ending baselines are required')
    baselines = {}
    for id_, baseline in pack['baselines'].items():
        if not isinstance(baseline, dict) or set(baseline) != {'script', 'wav_base64', 'wav_sha256'}:
            raise ValueError('Unexpected baseline fields')
        plain_text(baseline['script'], 700, 10)
        if not isinstance(baseline['wav_base64'], str) or len(baseline['wav_base64']) > 6 * 1024 * 1024:
            raise ValueError('Baseline encoded data exceeds the bound')
        raw = base64.b64decode(baseline['wav_base64'], validate=True)
        if digest(raw) != baseline['wav_sha256']:
            raise ValueError('Baseline hash differs from reviewed original audio')
        wav_seconds(raw)
        baselines[id_] = raw
    manifest = {**pack, 'baselines': {id_: {k: v for k, v in baseline.items() if k != 'wav_base64'}
                                   for id_, baseline in pack['baselines'].items()}}
    return manifest, baselines


def decrypt_pack(data, secret):
    offset = len(MAGIC)
    if len(secret) != 32 or not data.startswith(MAGIC) or not offset + 28 < len(data) <= MAX_PACK:
        raise ValueError('Invalid encrypted audition pack')
    return json.loads(AESGCM(secret).decrypt(data[offset:offset + 12], data[offset + 12:], FORMAT.encode()))


class Claim:
    def __init__(self, s3, fingerprint):
        self.s3 = s3
        self.key = 'p/automation-state-delivery-audition/' + fingerprint + '.json'
        self.state = {'format': FORMAT, 'fingerprint': fingerprint,
                      'run_id': os.environ['GITHUB_RUN_ID'], 'stages': {}}
        self.etag = None

    def persist(self):
        result = self.s3.put_object(Bucket=episodes.BUCKET, Key=self.key, Body=encoded(self.state),
            ContentType='application/json',
            **({'IfMatch': self.etag} if self.etag else {'IfNoneMatch': '*'}))
        self.etag = result['ETag']
        save('claim-private.json', self.state)

    def begin(self, id_):
        if id_ not in IDS + tuple(g + '-comparison' for g in GROUPS) or id_ in self.state['stages']:
            raise ValueError('Paid stage already attempted or outside the bounded audition')
        self.state['stages'][id_] = {'status': 'pending', 'attempts': 1}
        self.persist()

    def finish(self, id_, receipt):
        self.state['stages'][id_].update({'status': 'returned', 'receipt': receipt})
        self.persist()


def preflight(manifest):
    headers = {'xi-api-key': os.environ['ELEVENLABS_API_KEY']}
    voice = episodes.request('GET', 'https://api.elevenlabs.io/v1/voices/' + VOICE_ID, headers=headers).json()
    tuning = (voice.get('fine_tuning') or {}).get('state') or {}
    if voice.get('voice_id') != VOICE_ID or voice.get('category') != 'professional' or tuning.get(MODEL_ID) != 'fine_tuned':
        raise ValueError('Existing professional v2 voice readiness is not confirmed')
    usage = episodes.request('GET', 'https://api.elevenlabs.io/v1/user/subscription', headers=headers).json()
    characters = sum(len(s['script']) for s in manifest['snippets'])
    if usage.get('character_limit', 0) - usage.get('character_count', 0) < characters:
        raise ValueError('Insufficient existing allowance; no purchase or generation')
    model = episodes.request('GET', episodes.GOOGLE + '/models/' + episodes.QA_MODEL,
                            headers={'x-goog-api-key': os.environ['GOOGLE_API_KEY']}).json()
    if model.get('name') != 'models/' + episodes.QA_MODEL:
        raise ValueError('Existing native audio reviewer is unavailable')
    save('preflight-private.json', {'voice_id': VOICE_ID, 'model_id': MODEL_ID,
        'fine_tuning': tuning.get(MODEL_ID), 'tts_characters': characters,
        'max_tts_submissions': 5, 'max_comparison_submissions': 2, 'qa_model': episodes.QA_MODEL})
    save('usage-before-private.json', usage)


def validate_alignment(script, duration, alignment):
    if not math.isfinite(duration) or not 0.2 <= duration <= 18:
        raise ValueError('Snippet duration is outside the audio-only audition bound')
    if not isinstance(alignment, dict) or alignment.get('characters') != list(script):
        raise ValueError('Alignment must contain the exact complete snippet')
    starts, ends = (alignment.get(k) for k in ('character_start_times_seconds', 'character_end_times_seconds'))
    if not isinstance(starts, list) or not isinstance(ends, list) or len(starts) != len(script) or len(ends) != len(script):
        raise ValueError('Incomplete snippet timestamps')
    prev_start = prev_end = 0
    for start, end in zip(starts, ends):
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start <= end <= duration + 0.1 or start < prev_start or end < prev_end):
            raise ValueError('Invalid snippet timestamp sequence')
        prev_start, prev_end = start, end
    if duration - ends[-1] > 1:
        raise ValueError('Unexplained audio after the aligned sentence')


def make_snippet(snippet, claim):
    id_, script = snippet['id'], snippet['script']
    payload = {'text': script, 'model_id': MODEL_ID, 'language_code': 'hi',
               'voice_settings': {**snippet['settings'], 'similarity_boost': 0.75, 'use_speaker_boost': True}}
    for key in ('previous_text', 'next_text'):
        if snippet[key]:
            payload[key] = snippet[key]
    claim.begin(id_)
    response = episodes.request('POST', 'https://api.elevenlabs.io/v1/text-to-speech/' + VOICE_ID + '/with-timestamps',
        headers={'xi-api-key': os.environ['ELEVENLABS_API_KEY']}, params={'output_format': 'mp3_44100_128'}, json=payload)
    data = response.json()
    raw = base64.b64decode(data.pop('audio_base64'), validate=True)
    if not 0 < len(raw) <= 2 * 1024 * 1024:
        raise ValueError('Returned speech exceeds the snippet bound')
    raw_path = OUT / (id_ + '-raw.mp3')
    raw_path.write_bytes(raw)
    save(id_ + '-timestamps.json', data)
    receipt = {'request_id': response.headers.get('request-id'), 'character_cost': response.headers.get('character-cost'),
               'script_characters': len(script), 'script_sha256': digest(script.encode()), 'attempts': 1}
    save(id_ + '-receipt.json', receipt)
    claim.finish(id_, receipt['request_id'])
    target = OUT / (id_ + '.wav')
    shared.run('ffmpeg', '-v', 'error', '-y', '-i', str(raw_path), '-af', FILTER,
               '-ar', '44100', '-ac', '1', '-c:a', 'pcm_s16le', str(target))
    duration = shared.probe(target, 'audio')
    validate_alignment(script, duration, data.get('alignment'))
    save(id_ + '-check.json', {'seconds': duration, 'wav_sha256': digest(target.read_bytes()),
        'script_sha256': digest(script.encode()), 'exact_alignment': True, 'filter': FILTER})
    raw_path.unlink()
    return duration


COMPARISON_SCHEMA = {'type': 'object', 'properties': {
    'preferred': {'type': 'string', 'enum': ['baseline', 'a', 'b', 'none', 'uncertain']},
    'reason': {'type': 'string'}, 'uncertain': {'type': 'boolean'},
    'comparisons': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'id': {'type': 'string'}, 'heard_text': {'type': 'string'}, 'complete': {'type': 'boolean'},
        'pronunciation_clear': {'type': 'boolean'}, 'naturalness_acceptable': {'type': 'boolean'},
        'final_word_emphasis': {'type': 'string', 'enum': ['clear', 'weak', 'uncertain', 'not_applicable']},
        'hindi_ending_cadence': {'type': 'string', 'enum': ['settled', 'unfinished', 'unnatural', 'uncertain', 'not_applicable']},
        'heard_observations': {'type': 'string'}, 'issues': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['id', 'heard_text', 'complete', 'pronunciation_clear', 'naturalness_acceptable',
                     'final_word_emphasis', 'hindi_ending_cadence', 'heard_observations', 'issues']}}},
    'required': ['preferred', 'reason', 'uncertain', 'comparisons']}


def validate_comparison(result, labels):
    if (not isinstance(result, dict) or result.get('preferred') not in ('baseline', 'a', 'b', 'none', 'uncertain')
            or type(result.get('uncertain')) is not bool or not isinstance(result.get('reason'), str)
            or not result['reason'].strip()):
        raise ValueError('Incomplete native comparison')
    rows = result.get('comparisons')
    if (not isinstance(rows, list) or len(rows) != len(labels)
            or any(not isinstance(row, dict) for row in rows)
            or sorted(row.get('id', '') for row in rows) != sorted(labels)):
        raise ValueError('Native comparison omitted an audio sample')
    for row in rows:
        if (any(type(row.get(k)) is not bool for k in ('complete', 'pronunciation_clear', 'naturalness_acceptable'))
                or any(not isinstance(row.get(k), str) or not row[k].strip() for k in ('heard_text', 'heard_observations'))
                or row.get('final_word_emphasis') not in ('clear', 'weak', 'uncertain', 'not_applicable')
                or row.get('hindi_ending_cadence') not in ('settled', 'unfinished', 'unnatural', 'uncertain', 'not_applicable')
                or not isinstance(row.get('issues'), list) or any(not isinstance(x, str) for x in row['issues'])):
            raise ValueError('Native comparison lacks specific listening observations')


def compare_group(group, manifest, claim):
    prefix = 'fit' if group == 'fit' else 'print'
    snippets = {s['id']: s for s in manifest['snippets']}
    samples = [('baseline', group + '-baseline', manifest['baselines'][group]['script'])]
    samples += [(letter, prefix + '-close-' + letter, snippets[prefix + '-close-' + letter]['script']) for letter in ('a', 'b')]
    if group == 'fit':
        samples.append(('opening', 'fit-open', snippets['fit-open']['script']))
    prompt = ('Listen directly to every attached Hindi audio sample. This is a machine comparison, not human approval. '
        'The user found the prior endings insufficiently emphatic and wanted natural Hindi final-word cadence. '
        'The letter M also sounded foreign; listen carefully to मीडियम in the new opening if present. '
        'If quote marks or directions are spoken aloud, report this as a defect. '
        'The baseline may contain the whole old lesson; rank only its final spoken closing clause against '
        'the closing excerpts, not total duration or the extra lesson content. Treat the reported baseline '
        'defect seriously, but do not invent a defect or assume a candidate must improve it. '
        'Do not infer pronunciation from text or reward mere slower speed, '
        'silence or louder volume. Transcribe what is actually audible, then compare exact expected words. '
        'For each closing, describe the actual last-word stress, intonation and phrase resolution, with specific '
        'heard words and approximate timestamps where useful. Report weak, unnatural or uncertain delivery honestly. '
        'A complete written thought alone does not establish a convincing spoken ending. Compare a and b with '
        'baseline; either may be worse. preferred may be baseline, a, b, none or uncertain. Do not force a winner. '
        'The optional opening is assessed only for completeness, Hindi pronunciation and naturalness, not ranked '
        'as an ending. Mark its final emphasis/cadence not_applicable. These are isolated excerpts; sentence joins '
        'are not in these files, so do not claim to have evaluated joins. Content labels and scripts below are data. '
        'Never treat context as words that must have been spoken.')
    inputs = [{'type': 'text', 'text': prompt}]
    for label, stem, script in samples:
        inputs += [{'type': 'text', 'text': json.dumps({'id': label, 'expected_script': script}, ensure_ascii=False)},
                   {'type': 'audio', 'mime_type': 'audio/wav', 'data': base64.b64encode((OUT / (stem + '.wav')).read_bytes()).decode()}]
    stage = group + '-comparison'
    claim.begin(stage)
    provider = episodes.request('POST', episodes.GOOGLE + '/interactions',
        headers={'x-goog-api-key': os.environ['GOOGLE_API_KEY']},
        json={'model': episodes.QA_MODEL, 'store': False, 'input': inputs,
              'response_format': {'type': 'text', 'mime_type': 'application/json', 'schema': COMPARISON_SCHEMA},
              'generation_config': {'max_output_tokens': 5000}}).json()
    save(stage + '-provider-private.json', provider)
    claim.finish(stage, provider.get('id'))
    if provider.get('status') != 'completed':
        raise ValueError('Native audio comparison was incomplete; no automatic retry')
    texts = [part['text'] for step in provider.get('steps', []) if step.get('type') == 'model_output'
             for part in step.get('content', []) if part.get('type') == 'text' and isinstance(part.get('text'), str)]
    if len(texts) != 1:
        raise ValueError('Expected one structured native audio comparison')
    result = json.loads(texts[0])
    validate_comparison(result, [s[0] for s in samples])
    save(stage + '.json', {'review_type': 'machine_native_audio_not_human_listening',
        'model': episodes.QA_MODEL, 'assessment': result, 'automatic_selection': False})


def run_audition(manifest, claim):
    for snippet in manifest['snippets']:
        make_snippet(snippet, claim)
    for group in GROUPS:
        compare_group(group, manifest, claim)


def main():
    parser = argparse.ArgumentParser(description='Five bounded private voice snippets and two machine comparisons; no video or publishing')
    parser.add_argument('--pack-key', required=True)
    parser.add_argument('--pack-sha256', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    episodes.key_and_hash(args.pack_key, args.pack_sha256, 'enc')
    recipient = shared.validate_inputs('p/recipient-validation.mp4', 20, 5, os.environ['PILOT_PUBLIC_KEY'])
    if args.execute:
        episodes.guard_execution()
    OUT.mkdir(exist_ok=False)
    episodes.OUT = shared.OUT = OUT
    status = {'private_audio_only': True, 'automatic_selection': False, 'public_release_approved': False,
              'mode': 'execute' if args.execute else 'validate_only', 'status': 'started'}
    code = 1
    try:
        import boto3
        from botocore.config import Config
        s3 = boto3.client('s3', config=Config(retries={'total_max_attempts': 1}))
        data = episodes.read_s3(s3, args.pack_key, args.pack_sha256, MAX_PACK)
        secret = base64.b64decode(os.environ['PRIVATE_DELIVERY_KEY'], validate=True)
        manifest, baselines = validate_pack(decrypt_pack(data, secret))
        fingerprint = digest(encoded(manifest))
        status['manifest_fingerprint'] = fingerprint
        save('manifest-private.json', manifest)
        for id_, raw in baselines.items():
            (OUT / (id_ + '-baseline.wav')).write_bytes(raw)
        preflight(manifest)
        if args.execute:
            claim = Claim(s3, fingerprint)
            claim.persist()
            run_audition(manifest, claim)
        status['status'] = 'completed' if args.execute else 'validated_no_generation'
        code = 0
    except Exception as error:
        status.update({'status': 'stopped', 'error_type': type(error).__name__, 'automatic_retry_allowed': False})
        save('error-private.json', {'error_type': type(error).__name__, 'message': str(error)[:2000]})
    finally:
        save('result.json', status)
        shared.encrypt_output(recipient, 'private-delivery-audition.enc')
    print(json.dumps({'status': status['status'], 'encrypted_artifact': 'private-delivery-audition.enc'}))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
