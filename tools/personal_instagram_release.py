#!/usr/bin/env python3
"""Release one exact approved personal Reel only after its full-story dependencies pass."""
import argparse
import base64
import hashlib
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import requests

from tools.curated_feed import ACCOUNT_ID, ACCOUNT_USERNAME, FeedError as Error, StateStore, digest, parse_time, queue_lock, record_post, utc_now, wait_finished
from tools.reviewed_ai_reels import Instagram as BaseInstagram, validate_assets, read_object, require_account

MAIN_CHANNEL = 'UCdgOMA7WO48MYimj6q6mvNQ'
PREFIX = 'p/automation-state-personal-instagram/'
SHA = re.compile(r'[0-9a-f]{64}\Z')
FIELDS = {'id', 'status', 'account_id', 'publish_at', 'release_by', 'caption', 'video_url', 'video_sha256', 'cover_url', 'cover_sha256', 'user_selection', 'dependency'}
STATE_FIELDS = {'format', 'mode', 'job_id', 'job_sha256', 'account_id', 'video_sha256', 'phase', 'pending', 'parent_id', 'media_id', 'publish_attempt_at', 'published_at', 'guard_sha256'}


def https(value):
    if not isinstance(value, str): raise Error('HTTPS URL required.')
    u = urlsplit(value)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.fragment:
        raise Error('Public HTTPS URL required.')
    return u


def validate_job(job):
    if not isinstance(job, dict) or set(job) != FIELDS or job['status'] != 'reviewed':
        raise Error('Only an exact reviewed personal-video job is accepted.')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', job['id']) or job['account_id'] != ACCOUNT_ID:
        raise Error('Wrong job or Instagram account.')
    due, expiry = parse_time(job['publish_at']), parse_time(job['release_by'])
    if not 0 < (expiry - due).total_seconds() <= 10800:
        raise Error('Release window must end within three hours; missed jobs stay held.')
    if not isinstance(job['caption'], str) or not 1 <= len(job['caption']) <= 2200:
        raise Error('Caption must contain 1–2200 characters.')
    for kind in ('video', 'cover'):
        https(job[kind + '_url'])
        if not isinstance(job[kind + '_sha256'], str) or not SHA.fullmatch(job[kind + '_sha256']):
            raise Error('Exact approved video and cover SHA256 required.')
    selection = job['user_selection']
    if not isinstance(selection, dict) or set(selection) != {'id', 'version', 'sha256', 'cover_sha256', 'approved_at', 'public_release_approved', 'real_recorded_speech'}:
        raise Error('Exact user-approved real-recording receipt required.')
    if selection['public_release_approved'] is not True or selection['real_recorded_speech'] is not True or selection['sha256'] != job['video_sha256'] or selection['cover_sha256'] != job['cover_sha256']:
        raise Error('The approved media/cover or real-speech provenance changed.')
    if not selection['id'] or not selection['version'] or parse_time(selection['approved_at']) >= due:
        raise Error('Approval must identify a version and precede release.')
    dep = job['dependency']
    if not isinstance(dep, dict) or set(dep) != {'youtube_id', 'youtube_channel_id', 'youtube_public_after', 'profile_url', 'primary_store_url', 'landing_sha256', 'native_profile_evidence'}:
        raise Error('Full YouTube and native profile-link dependencies are required.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', dep['youtube_id']) or dep['youtube_channel_id'] != MAIN_CHANNEL or parse_time(dep['youtube_public_after']) >= due:
        raise Error('Wrong full-video identity or release ordering.')
    link = https(dep['profile_url'])
    if link.hostname != 'www.bulkplaintshirt.com' or link.path != '/p/my-story.html' or link.query:
        raise Error('Unexpected first-party personal-story link.')
    if dep['primary_store_url'] != 'http://sale91.com' or not SHA.fullmatch(dep['landing_sha256']):
        raise Error('Preserve the verified primary store URL and reviewed landing page.')
    evidence = dep['native_profile_evidence']
    expected = {'account_id', 'username', 'profile_url', 'primary_store_url', 'secondary_link_verified', 'verified_at', 'screenshot_sha256'}
    if not isinstance(evidence, dict) or set(evidence) != expected or evidence['secondary_link_verified'] is not True:
        raise Error('Actual native secondary-link verification is required.')
    if any(evidence[k] != v for k, v in {'account_id': ACCOUNT_ID, 'username': ACCOUNT_USERNAME, 'profile_url': dep['profile_url'], 'primary_store_url': dep['primary_store_url']}.items()):
        raise Error('Native profile proof belongs to a different link/account.')
    if not SHA.fullmatch(evidence['screenshot_sha256']) or parse_time(evidence['verified_at']) > utc_now():
        raise Error('Invalid native profile evidence timestamp/hash.')
    return job


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'a' and values.get('href') and 'hidden' not in values:
            self.links.append(values)


def public_json(url, params=None):
    try:
        response = requests.get(url, params=params, timeout=(15, 30), allow_redirects=False)
        data = response.json()
        if response.status_code != 200 or not isinstance(data, dict): raise ValueError()
        return data
    except Exception:
        raise Error('Public-video dependency lookup failed; no release.') from None


def check_dependencies(api, job, clock=utc_now):
    dep = job['dependency']
    if clock() < parse_time(dep['youtube_public_after']):
        raise Error('Full-story release time has not arrived.')
    profile = api.profile()
    if profile.get('id') != ACCOUNT_ID or profile.get('username') != ACCOUNT_USERNAME or profile.get('website') != dep['primary_store_url']:
        raise Error('Instagram account or preserved primary store link changed.')
    key = os.environ.get('YOUTUBE_API_KEY_1', '').strip()
    if not key: raise Error('Public YouTube readback key unavailable.')
    data = public_json('https://www.googleapis.com/youtube/v3/videos', {'id': dep['youtube_id'], 'part': 'snippet,status,contentDetails', 'key': key})
    rows = data.get('items') or []
    if len(rows) != 1: raise Error('Exact full YouTube video is not publicly available.')
    v = rows[0]; status = v.get('status') or {}; details = v.get('contentDetails') or {}
    region = details.get('regionRestriction') or {}
    if v.get('id') != dep['youtube_id'] or (v.get('snippet') or {}).get('channelId') != MAIN_CHANNEL or status.get('privacyStatus') != 'public' or status.get('uploadStatus') != 'processed' or status.get('embeddable') is not True:
        raise Error('Full YouTube video is not public, processed and embeddable on the approved channel.')
    if 'IN' in region.get('blocked', []) or ('allowed' in region and 'IN' not in region['allowed']) or (details.get('contentRating') or {}).get('ytRating') == 'ytAgeRestricted':
        raise Error('Full video has a region/age viewing restriction; review before releasing the preview.')
    watch = 'https://www.youtube.com/watch?v=' + dep['youtube_id']
    embed = public_json('https://www.youtube.com/oembed', {'url': watch, 'format': 'json'})
    if embed.get('provider_name') != 'YouTube' or not re.search(r'/embed/' + re.escape(dep['youtube_id']) + r'(?:[?"\s/])', embed.get('html', '')):
        raise Error('Anonymous exact-video oEmbed is unavailable.')
    try:
        response = requests.get(dep['profile_url'], timeout=(15, 30), allow_redirects=False)
        raw = response.content
        if response.status_code != 200 or len(raw) > 2_000_000 or hashlib.sha256(raw).hexdigest() != dep['landing_sha256']:
            raise ValueError()
        links = Links(); links.feed(raw.decode('utf-8'))
        if not any(a.get('id') == 'full-story-video' and a['href'] == watch for a in links.links): raise ValueError()
        if not any(urlsplit(a['href']).hostname == 'sale91.com' for a in links.links): raise ValueError()
    except Exception:
        raise Error('Reviewed first-party page or exact full-video/store links failed verification.') from None
    return {'youtube_id': dep['youtube_id'], 'checked_at': clock().isoformat(), 'primary_store_verified': True, 'landing_sha256': dep['landing_sha256'], 'native_secondary_evidence_sha256': digest(dep['native_profile_evidence']), 'secondary_verification_limit': 'Native UI was verified at setup; Graph exposes primary website only.'}


class Instagram(BaseInstagram):
    def profile(self):
        return self.request('GET', ACCOUNT_ID, params={'fields': 'id,username,website'})
    def create(self, data):
        if 'is_ai_generated' in data: raise Error('This approved file contains real recorded speech; no synthetic flag accepted.')
        return self.request('POST', ACCOUNT_ID + '/media', data=data).get('id')
    def media(self, ident):
        return self.request('GET', ident, params={'fields': 'id,username,owner,caption,permalink'})


class S3Backend:
    def __init__(self, client): self.client = client
    def read(self, filename):
        key = PREFIX + filename
        try:
            found = self.client.list_objects_v2(Bucket='bulkplaintshirt.com', Prefix=key, MaxKeys=1)
            if not any(row.get('Key') == key for row in found.get('Contents', [])): return None, None
            response = self.client.get_object(Bucket='bulkplaintshirt.com', Key=key)
            with response['Body'] as body: raw = body.read(65537)
            state = json.loads(raw)
            if len(raw) > 65536 or not isinstance(state, dict) or set(state) - STATE_FIELDS or not response.get('ETag'): raise ValueError()
            return state, response['ETag']
        except Exception: raise Error('Durable state unavailable; no local fallback or release.') from None
    def save(self, filename, state, etag):
        if set(state) - STATE_FIELDS or state.get('format') != 'personal-reel-v1': raise Error('Only identifier/hash release state is allowed remotely.')
        condition = {'IfMatch': etag} if etag else {'IfNoneMatch': '*'}
        try:
            response = self.client.put_object(Bucket='bulkplaintshirt.com', Key=PREFIX + filename, Body=json.dumps(state, sort_keys=True).encode(), ContentType='application/json', CacheControl='no-store', **condition)
            if not response.get('ETag'): raise ValueError()
            return response['ETag']
        except Exception: raise Error('Conditional state write failed/ambiguous; no next Graph POST.') from None


def bind(state, job):
    fields = {'format': 'personal-reel-v1', 'mode': 'publish', 'job_id': job['id'], 'job_sha256': digest(job), 'account_id': ACCOUNT_ID, 'video_sha256': job['video_sha256']}
    if state is None: return dict(fields, phase='validated', pending=None)
    if set(state) - STATE_FIELDS or any(state.get(k) != v for k, v in fields.items()): raise Error('Saved source, copy, dependency or schedule changed.')
    if state.get('pending'): raise Error('A previous Graph POST is ambiguous; no automatic retry.')
    return state


def verify_published(api, job, store, state):
    media = api.media(state['media_id'])
    if media.get('id') != state['media_id'] or media.get('username') != ACCOUNT_USERNAME or (media.get('owner') or {}).get('id') != ACCOUNT_ID or media.get('caption') != job['caption']:
        raise Error('Published identity/caption differs; saved ID retained, do not republish.')
    state['phase'] = 'published_verified'; store.save(state)
    return state


def run_job(api, job, store, execute=False, clock=utc_now, assets_check=validate_assets, dependencies=check_dependencies):
    validate_job(job)
    state = bind(store.read(), job)
    require_account(api)
    if state.get('media_id'):
        return verify_published(api, job, store, state) if execute else {'already_published': True, 'media_id': state['media_id']}
    if execute:
        if store.backend is None: raise Error('Public execution requires durable conditional S3 state.')
        if not parse_time(job['publish_at']) <= clock() <= parse_time(job['release_by']): raise Error('Outside the approved release window; no mutation.')
    assets_check(job)
    try:
        guard = dependencies(api, job, clock)
    except Error as exc:
        if execute: raise
        return {'dry_run': True, 'ready_for_release': False, 'dependency_hold': str(exc), 'job_id': job['id']}
    if not execute: return {'dry_run': True, 'ready_for_release': True, 'dependencies': guard, 'job_id': job['id']}
    state['guard_sha256'] = digest(guard); store.save(state)
    body = {'media_type': 'REELS', 'video_url': job['video_url'], 'cover_url': job['cover_url'], 'caption': job['caption'], 'share_to_feed': 'true'}
    parent = state.get('parent_id') or record_post(api.create, store, state, 'parent', body)
    wait_finished(api, parent)
    if not parse_time(job['publish_at']) <= clock() <= parse_time(job['release_by']): raise Error('Release window passed during container processing; no publication.')
    require_account(api)
    state['guard_sha256'] = digest(dependencies(api, job, clock))
    state['publish_attempt_at'] = clock().isoformat(); store.save(state)
    record_post(lambda: api.publish(parent), store, state, 'media')
    state['published_at'] = state['publish_attempt_at']; store.save(state)
    return verify_published(api, job, store, state)


def read_encrypted(path):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    envelope = read_object(path)
    if set(envelope) != {'id', 'status', 'publish_at', 'job_sha256', 'nonce', 'ciphertext'} or envelope['status'] != 'active': raise Error('No active encrypted personal release.')
    try:
        key = bytes.fromhex(os.environ['PERSONAL_INSTAGRAM_RELEASE_KEY'])
        raw = AESGCM(key).decrypt(base64.b64decode(envelope['nonce'], validate=True), base64.b64decode(envelope['ciphertext'], validate=True), envelope['id'].encode())
        job = validate_job(json.loads(raw))
        if job['id'] != envelope['id'] or job['publish_at'] != envelope['publish_at'] or digest(job) != envelope['job_sha256']: raise ValueError()
        return job
    except Exception: raise Error('Encrypted approved job failed authentication or exact binding.') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue-dir', type=Path, default=Path('personal_instagram_releases'))
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    try:
        import boto3
        from botocore.config import Config
        client = boto3.client('s3', config=Config(retries={'total_max_attempts': 1}))
        if not {'IfMatch', 'IfNoneMatch'} <= set(client.meta.service_model.operation_model('PutObject').input_shape.members): raise Error('Conditional S3 writes are required.')
        results = []
        with queue_lock(args.queue_dir):
            envelopes = [read_object(p) for p in sorted(args.queue_dir.glob('*.json'))]
            if len({e.get('id') for e in envelopes}) != len(envelopes): raise Error('Duplicate personal release IDs.')
            hashes = set()
            for path in sorted(args.queue_dir.glob('*.json')):
                envelope = read_object(path)
                if envelope.get('status') == 'held':
                    results.append({'id': envelope.get('id'), 'status': 'held'}); continue
                job = read_encrypted(path)
                if job['video_sha256'] in hashes: raise Error('Duplicate approved video bytes.')
                hashes.add(job['video_sha256'])
                if args.execute and parse_time(job['publish_at']) > utc_now():
                    results.append({'id': job['id'], 'status': 'not_due'}); continue
                store = StateStore(args.queue_dir / 'state' / (job['video_sha256'] + '.json'), S3Backend(client))
                results.append(run_job(Instagram(), job, store, execute=args.execute))
        print(json.dumps({'results': results}, indent=2)); return 0
    except Exception as exc:
        print('Personal Instagram release held: ' + (str(exc) if isinstance(exc, Error) else type(exc).__name__ + '; private details omitted'), file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
