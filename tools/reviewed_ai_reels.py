#!/usr/bin/env python3
"""Release exact user-selected AI Reels; default to read-only validation."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image

try:
    from .curated_feed import ACCOUNT_ID, ACCOUNT_USERNAME, FeedError, StateStore, digest, parse_time, queue_lock, record_post, utc_now, wait_finished
except ImportError:
    from curated_feed import ACCOUNT_ID, ACCOUNT_USERNAME, FeedError, StateStore, digest, parse_time, queue_lock, record_post, utc_now, wait_finished

Error = FeedError
JOB_FIELDS = {'id', 'status', 'account_id', 'publish_at', 'caption', 'video_url', 'video_sha256', 'cover_url', 'cover_sha256', 'is_ai_generated', 'user_selection'}
SELECTION_FIELDS = {'batch_id', 'id', 'version', 'sha256', 'approved'}
STATE_FIELDS = {'format', 'mode', 'job_id', 'job_sha256', 'account_id', 'video_sha256', 'selection_sha256', 'phase', 'pending', 'parent_id', 'media_id', 'publish_attempt_at', 'published_at', 'native_ai_requested', 'native_ai_verified', 'prepared_at'}
DISCLOSURE = 'AI-assisted dialogue using my own footage and voice.'
DISCLOSURE_PROBE_ID = '18091355006159379'
PREFIX = 'p/automation-state-reviewed-ai-reels'
BUCKET = 'bulkplaintshirt.com'
SHA = re.compile(r'[0-9a-f]{64}')


def read_object(path):
    try:
        result = json.loads(Path(path).read_text())
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (OSError, ValueError, UnicodeError):
        raise Error('Invalid JSON object: ' + Path(path).name) from None


def load_approvals(path):
    receipt = read_object(path)
    if set(receipt) != {'batch_id', 'approval_method', 'approved_at', 'videos'} or receipt['approval_method'] != 'user_exact_selection':
        raise Error('An exact user-selection receipt is required.')
    try:
        approved_at = datetime.fromisoformat(receipt['approved_at'])
        if approved_at.tzinfo is None or approved_at > utc_now():
            raise ValueError()
    except (ValueError, TypeError):
        raise Error('Invalid approval time.') from None
    rows = receipt['videos']
    if not isinstance(rows, list) or not rows:
        raise Error('The approval receipt has no selections.')
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'id', 'version', 'sha256', 'public_release_approved'} or row['public_release_approved'] is not True:
            raise Error('Invalid approved version receipt.')
        if not isinstance(row['id'], str) or not isinstance(row['version'], str) or not isinstance(row['sha256'], str) or not SHA.fullmatch(row['sha256']):
            raise Error('Invalid approved identifier, version or hash.')
    if len({r['id'] for r in rows}) != len(rows) or len({r['sha256'] for r in rows}) != len(rows):
        raise Error('Duplicate approved IDs or assets.')
    return receipt


def load_job(path, approvals):
    job = read_object(path)
    if set(job) != JOB_FIELDS or job.get('status') != 'reviewed' or job.get('is_ai_generated') is not True:
        raise Error('Use exact reviewed-AI job fields with mandatory boolean is_ai_generated=true.')
    if not isinstance(job['id'], str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', job['id']) or job['account_id'] != ACCOUNT_ID:
        raise Error('Invalid job ID or Instagram account.')
    due = parse_time(job['publish_at'])
    if due <= datetime.fromisoformat(approvals['approved_at']):
        raise Error('Release time must be later than explicit approval.')
    caption = job['caption']
    if not isinstance(caption, str) or not 1 <= len(caption) <= 2200 or DISCLOSURE not in caption:
        raise Error('Caption must retain the explicit own-footage AI disclosure and fit Instagram limits.')
    for kind in ('video', 'cover'):
        url, sha = job[kind + '_url'], job[kind + '_sha256']
        if not isinstance(url, str) or not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise Error('Every asset requires an HTTPS URL and exact SHA256.')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise Error('Asset URLs must be public HTTPS without embedded credentials or fragments.')
    selection = job['user_selection']
    if not isinstance(selection, dict) or set(selection) != SELECTION_FIELDS or selection['approved'] is not True:
        raise Error('Job has no exact user selection.')
    if selection['batch_id'] != approvals['batch_id'] or selection['sha256'] != job['video_sha256']:
        raise Error('Selected batch or video hash changed.')
    if not any(r['id'] == selection['id'] and r['version'] == selection['version'] and r['sha256'] == selection['sha256'] for r in approvals['videos']):
        raise Error('Video ID/version/hash is not in the explicit user-selected receipt.')
    return job


def download_checked(url, path, expected, limit):
    sha, total = hashlib.sha256(), 0
    with requests.get(url, stream=True, timeout=(15, 60)) as response:
        if response.status_code != 200 or urlsplit(response.url).scheme != 'https':
            raise Error('Reviewed asset did not return HTTPS 200.')
        with path.open('wb') as target:
            for block in response.iter_content(1024 * 1024):
                total += len(block)
                if total > limit:
                    raise Error('Reviewed asset exceeds its size limit.')
                target.write(block)
                sha.update(block)
    if not total or sha.hexdigest() != expected:
        raise Error('Downloaded asset SHA256 differs from the reviewed asset.')


def validate_assets(job):
    with tempfile.TemporaryDirectory(prefix='reviewed-ai-assets-') as folder:
        video, cover = Path(folder) / 'video.mp4', Path(folder) / 'cover.jpg'
        download_checked(job['video_url'], video, job['video_sha256'], 512_000_000)
        download_checked(job['cover_url'], cover, job['cover_sha256'], 8_000_000)
        with Image.open(cover) as picture:
            if picture.format not in ('JPEG', 'PNG') or picture.mode not in ('RGB', 'RGBA'):
                raise Error('Reviewed Reel cover must be a decoded RGB/RGBA JPEG or PNG.')
            picture.verify()
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video)], capture_output=True, timeout=45, check=True)
        data = json.loads(probe.stdout)
        videos = [s for s in data['streams'] if s.get('codec_type') == 'video']
        audios = [s for s in data['streams'] if s.get('codec_type') == 'audio']
        if len(videos) != 1 or not audios or not 1 <= float(data['format']['duration']) <= 180:
            raise Error('Reviewed Reel must have one video stream and audio, lasting 1–180 seconds.')
        v = videos[0]
        if v.get('codec_name') != 'h264' or v.get('width', 0) * 16 != v.get('height', 0) * 9:
            raise Error('Reviewed Reel must be portrait 9:16 H.264.')


class S3Backend:
    def __init__(self, client, bucket=BUCKET, prefix=PREFIX, mode='publish'):
        if bucket != BUCKET or prefix != PREFIX or mode not in ('publish', 'prepare_only'):
            raise Error('Unexpected reviewed-AI state destination.')
        self.mode = mode
        self.client, self.bucket, self.prefix = client, bucket, prefix + ('/prepare/' if mode == 'prepare_only' else '/')

    def read(self, filename):
        key = self.prefix + filename
        try:
            result = self.client.list_objects_v2(Bucket=self.bucket, Prefix=key, MaxKeys=1)
            if not any(x.get('Key') == key for x in result.get('Contents', [])):
                return None, None
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            with response['Body'] as body:
                raw = body.read(65537)
            state = json.loads(raw)
            if len(raw) > 65536 or not isinstance(state, dict) or set(state) - STATE_FIELDS or not response.get('ETag'):
                raise ValueError()
            return state, response['ETag']
        except Exception:
            raise Error('Remote reviewed-AI state unavailable or invalid; no local fallback or mutation.') from None

    def save(self, filename, state, etag):
        if state.get('mode') != self.mode or set(state) - STATE_FIELDS:
            raise Error('Remote state accepts only release identifiers, hashes and status evidence.')
        condition = {'IfMatch': etag} if etag is not None else {'IfNoneMatch': '*'}
        try:
            response = self.client.put_object(Bucket=self.bucket, Key=self.prefix + filename, Body=json.dumps(state, sort_keys=True).encode(), ContentType='application/json', CacheControl='no-store', **condition)
            if not response.get('ETag'):
                raise ValueError()
            return response['ETag']
        except Exception:
            raise Error('Conditional release-state write failed or is ambiguous; no next Graph POST.') from None


def backend_from_environment(mode='publish'):
    if os.environ.get('REVIEWED_AI_STATE_BUCKET') != BUCKET:
        raise Error('Durable reviewed-AI S3 state is required for queued publication.')
    import boto3
    from botocore.config import Config
    client = boto3.client('s3', config=Config(retries={'total_max_attempts': 1}))
    members = client.meta.service_model.operation_model('PutObject').input_shape.members
    if not {'IfMatch', 'IfNoneMatch'}.issubset(members):
        raise Error('boto3 must support conditional S3 writes.')
    return S3Backend(client, mode=mode)


class Instagram:
    def __init__(self):
        token = os.environ.get('INSTAGRAM_ACCESS_TOKEN', '').strip()
        if not token or os.environ.get('INSTAGRAM_BUSINESS_ID') != ACCOUNT_ID:
            raise Error('Missing token or wrong Instagram account.')
        version = os.environ.get('REVIEWED_AI_GRAPH_VERSION', 'v26.0')
        if not re.fullmatch(r'v(?:2[4-9]|[3-9][0-9])\.0', version):
            raise Error('Use an explicitly supported Graph version v24.0 or newer.')
        self.base = 'https://graph.facebook.com/' + version + '/'
        self.session = requests.Session()
        self.session.headers['Authorization'] = 'Bearer ' + token

    def request(self, method, path, **kwargs):
        try:
            response = self.session.request(method, self.base + path, timeout=(15, 60), allow_redirects=False, **kwargs)
            data = response.json()
        except Exception:
            raise Error('Graph request outcome unavailable; response details omitted. Do not retry a saved POST.') from None
        if not isinstance(data, dict) or not 200 <= response.status_code < 300 or 'error' in data:
            code = data.get('error', {}).get('code') if isinstance(data, dict) else None
            raise Error('Graph rejected request: HTTP %s, code %s; mandatory disclosure will not be removed.' % (response.status_code, code))
        return data

    def identity(self):
        return self.request('GET', ACCOUNT_ID, params={'fields': 'id,username'})

    def create(self, data):
        if data.get('is_ai_generated') != 'true':
            raise Error('Native AI disclosure is mandatory.')
        return self.request('POST', ACCOUNT_ID + '/media', data=data).get('id')

    def status(self, container):
        return self.request('GET', container, params={'fields': 'status_code'}).get('status_code')

    def publish(self, container):
        return self.request('POST', ACCOUNT_ID + '/media_publish', data={'creation_id': container}).get('id')

    def media(self, media):
        return self.request('GET', media, params={'fields': 'id,username,owner,caption,permalink,is_ai_generated'})


def require_account(api):
    who = api.identity()
    if who.get('id') != ACCOUNT_ID or who.get('username') != ACCOUNT_USERNAME:
        raise Error('Live Instagram identity differs from the approved account.')


def require_disclosure_route(api):
    proof = api.media(DISCLOSURE_PROBE_ID)
    if proof.get('id') != DISCLOSURE_PROBE_ID or proof.get('username') != ACCOUNT_USERNAME or (proof.get('owner') or {}).get('id') != ACCOUNT_ID or proof.get('is_ai_generated') is not True:
        raise Error('Known owned native AI-label readback is unavailable; no public release is allowed.')


def bind_state(state, job, mode):
    binding = {'format': 'reviewed-ai-reel-v1', 'mode': mode, 'job_id': job['id'], 'job_sha256': digest(job), 'account_id': ACCOUNT_ID, 'video_sha256': job['video_sha256'], 'selection_sha256': digest(job['user_selection'])}
    if state is None:
        return dict(binding, phase='validated', pending=None, native_ai_requested=True, native_ai_verified=False)
    if any(state.get(k) != v for k, v in binding.items()) or set(state) - STATE_FIELDS:
        raise Error('Saved release belongs to changed copy, schedule, selection, version or account.')
    if state.get('pending'):
        raise Error('Previous %s POST is ambiguous; automatic retry is blocked.' % state['pending'])
    return state


def verify_published(api, job, store, state):
    media = api.media(state['media_id'])
    if media.get('id') != state['media_id'] or media.get('username') != ACCOUNT_USERNAME or media.get('caption') != job['caption'] or (media.get('owner') or {}).get('id') != ACCOUNT_ID or media.get('is_ai_generated') is not True:
        raise Error('Published media disclosure/identity/caption verification failed. ID is retained; do not republish.')
    state['native_ai_verified'] = True
    state['phase'] = 'published_verified'
    store.save(state)
    return state


def run_job(api, job, store, execute=False, prepare_only=False, clock=utc_now, assets_check=validate_assets):
    if prepare_only and store.backend is not None and getattr(store.backend, 'mode', None) != 'prepare_only':
        raise Error('Unpublished prepare-only state must use a separate local or durable prepare namespace.')
    if not prepare_only and store.backend is not None and getattr(store.backend, 'mode', 'publish') != 'publish':
        raise Error('Prepare-only state cannot be used for publication.')
    if execute and not prepare_only and store.backend is None:
        raise Error('Public execution requires conditional durable S3 state.')
    state = bind_state(store.read(), job, 'prepare_only' if prepare_only else 'publish')
    due = parse_time(job['publish_at'])
    if execute and not prepare_only and due > clock():
        raise Error('Release is not due; no container or publication before its time.')
    require_account(api)
    assets_check(job)
    if not prepare_only:
        require_disclosure_route(api)
    if not execute:
        return {'dry_run': True, 'job_id': job['id'], 'native_ai_required': True, 'native_ai_route_verified': not prepare_only, 'publish_at': job['publish_at'], 'video_sha256': job['video_sha256']}
    if state.get('media_id'):
        return verify_published(api, job, store, state)
    store.save(state)
    body = {'media_type': 'REELS', 'video_url': job['video_url'], 'cover_url': job['cover_url'], 'caption': job['caption'], 'is_ai_generated': 'true', 'share_to_feed': 'true'}
    parent = state.get('parent_id') or record_post(api.create, store, state, 'parent', body)
    wait_finished(api, parent)
    state['phase'] = 'prepared_native_requested' if prepare_only else 'ready'
    state['prepared_at'] = clock().isoformat()
    store.save(state)
    if prepare_only:
        return state
    if due > clock():
        raise Error('Clock is before the release time; publication blocked.')
    require_account(api)
    state['publish_attempt_at'] = clock().isoformat()
    record_post(lambda: api.publish(parent), store, state, 'media')
    state['published_at'] = state['publish_attempt_at']
    store.save(state)
    return verify_published(api, job, store, state)


def run_queue(queue, approvals, backend, api_factory=Instagram, execute=False, clock=utc_now, assets_check=validate_assets):
    jobs = [load_job(p, approvals) for p in sorted(Path(queue).glob('*.json'))]
    if len({j['id'] for j in jobs}) != len(jobs) or len({j['video_sha256'] for j in jobs}) != len(jobs):
        raise Error('Duplicate job ID or approved video content in queue.')
    results = []
    for job in sorted(jobs, key=lambda j: parse_time(j['publish_at'])):
        store = StateStore(Path(queue) / 'state' / (job['video_sha256'] + '.json'), backend)
        state = bind_state(store.read(), job, 'publish')
        if not execute or parse_time(job['publish_at']) <= clock():
            if execute and state.get('native_ai_verified') and state.get('media_id'):
                results.append({'job_id': job['id'], 'already_verified': True, 'media_id': state['media_id']})
                continue
            results.append(run_job(api_factory(), job, store, execute=execute, clock=clock, assets_check=assets_check))
    return {'results': results, 'queue_count': len(jobs)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue-dir', type=Path, default=Path('reviewed_ai_reels'))
    parser.add_argument('--approvals', type=Path, default=Path('reviewed_ai_releases/approvals.json'))
    parser.add_argument('--job', type=Path)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--durable-prepare', action='store_true')
    args = parser.parse_args(argv)
    if args.durable_prepare and not args.prepare_only:
        parser.error('--durable-prepare requires --prepare-only.')
    if args.prepare_only and (not args.job or not args.state):
        parser.error('Prepare-only requires --job and a separate local --state.')
    if args.state and not args.prepare_only:
        parser.error('--state is only supported for unpublished prepare-only mode.')
    if args.job and args.execute and not args.prepare_only:
        parser.error('Publishing must process the whole queue.')
    if args.prepare_only and (args.state.resolve().is_relative_to(args.queue_dir.resolve()) or args.state.resolve().is_relative_to(args.job.parent.resolve())):
        parser.error('Prepare state must be outside queue and job directories.')
    try:
        approvals = load_approvals(args.approvals)
        with queue_lock(args.queue_dir):
            if args.job:
                job = load_job(args.job, approvals)
                backend = backend_from_environment(mode='prepare_only') if args.durable_prepare else None
                state_path = args.state or args.queue_dir / 'state' / (job['video_sha256'] + '.json')
                if args.durable_prepare:
                    state_path = state_path.parent / (job['video_sha256'] + '.json')
                store = StateStore(state_path, backend)
                result = run_job(Instagram(), job, store, execute=args.execute, prepare_only=args.prepare_only)
            else:
                result = run_queue(args.queue_dir, approvals, backend_from_environment(), execute=args.execute)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        message = str(exc) if isinstance(exc, Error) else type(exc).__name__ + '; response details omitted'
        print('Reviewed AI Reel stopped: ' + message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
