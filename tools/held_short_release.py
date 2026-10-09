#!/usr/bin/env python3
"""Release an owner-approved HELD Short exactly as approved.

Owner 9-Oct-2026 13:39 IST: "All three videos look good to me" - the three DTF Shorts made from his own footage of
7-Oct-2026 (announcements dtf_*_real_2026_10_07), held by hold_only runs of daily_short.yml and shown on one approval
page. A hold_only run stops after it puts its review copy at <bucket>/p/review/<run_id>/ (video.mp4, cover.png,
review.json). This tool publishes THAT file, byte for byte (its sha256 must equal the approved one in
held_shorts/jobs.json), with the daily run's own functions (daily_short.py), so the title, description, tags,
synthetic-media label, cover, buyer-question comment, playlist, Facebook AI label and Telegram caption are the same
as a normal daily Short.

  --mode check             fetch and verify every job's held files, print the plan and captions; posts nothing.
  --mode youtube --job ID  bot channel: private + publishAt (the job's youtube_at) after a 2 h clash check on this
                           channel (owner rule 6-Oct-2026), then the daily run's comment, cover and playlist steps.
  --mode verify --job ID   read the booked video's status again (privacy, publishAt, AI label) and store it.
  --mode meta_due          Facebook Reel (is_ai_generated) + Telegram channel post of every job whose meta_at has
                           passed, within META_WINDOW. Instagram goes through reviewed_ai_reels.py (exact-selection
                           rail with its own state), not through this tool.

State per job in S3 (p/automation-state-held-shorts/<id>.json, conditional writes). A platform that has an id, a
pending attempt or a failure is never posted again by this tool: a failure is reported for a human to reconcile.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

JOBS = ROOT / 'held_shorts' / 'jobs.json'
BUCKET = 'bulkplaintshirt.com'
BASE_URL = 'https://www.bulkplaintshirt.com'
STATE_PREFIX = 'p/automation-state-held-shorts/'
IST = timezone(timedelta(hours=5, minutes=30))
GAP = timedelta(hours=2)
YOUTUBE_LEAD = timedelta(minutes=30)
# A Facebook / Telegram post more than this late (GitHub cron outage) waits for a human instead of landing at night.
META_WINDOW = timedelta(hours=4)
META_PLATFORMS = ('facebook', 'telegram')
SHA = re.compile(r'[0-9a-f]{64}')
JOB_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')


class ReleaseError(RuntimeError):
    pass


def parse_at(value):
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        raise ReleaseError(f'bad time: {value!r}') from None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ReleaseError(f'time needs an offset: {value!r}')
    return stamp


def load_jobs(path=JOBS):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ReleaseError(f'jobs file unreadable ({type(error).__name__})') from None
    approval = data.get('approval') if isinstance(data, dict) else None
    if not isinstance(approval, dict) or not isinstance(approval.get('by'), str) or not approval.get('words'):
        raise ReleaseError('jobs file has no owner approval')
    approved_at = parse_at(approval.get('at'))
    jobs = data.get('jobs')
    if not isinstance(jobs, list) or not jobs:
        raise ReleaseError('no jobs')
    seen = set()
    for job in jobs:
        if not isinstance(job, dict) or not JOB_ID.fullmatch(str(job.get('id', ''))) or job['id'] in seen:
            raise ReleaseError('bad or duplicate job id')
        seen.add(job['id'])
        if not str(job.get('run_id', '')).isdigit():
            raise ReleaseError(f"{job['id']}: run_id must be the numeric workflow run")
        for key in ('video_sha256', 'cover_sha256'):
            if not SHA.fullmatch(str(job.get(key, ''))):
                raise ReleaseError(f"{job['id']}: {key} must be a full sha256")
        if not isinstance(job.get('announcement'), str) or not job['announcement']:
            raise ReleaseError(f"{job['id']}: announcement id missing")
        for key in ('youtube_at', 'meta_at'):
            if parse_at(job.get(key)) <= approved_at:
                raise ReleaseError(f"{job['id']}: {key} must be after the owner's approval")
        for key in ('youtube_title', 'instagram_title'):
            if key in job and (not isinstance(job[key], str) or not 1 <= len(job[key]) <= 100):
                raise ReleaseError(f"{job['id']}: {key} must be 1-100 characters")
    return jobs


def sha256_of(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def fetch_held(job, folder, get=None):
    """The held review copy of the job's run, checked against the approved hashes."""
    if get is None:
        import requests
        get = requests.get
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name in ('video.mp4', 'cover.png', 'review.json'):
        url = f"{BASE_URL}/p/review/{job['run_id']}/{name}"
        response = get(url, timeout=(15, 180))
        if response.status_code != 200:
            raise ReleaseError(f"{job['id']}: {name} answered HTTP {response.status_code}")
        paths[name] = folder / name
        paths[name].write_bytes(response.content)
    if sha256_of(paths['video.mp4']) != job['video_sha256']:
        raise ReleaseError(f"{job['id']}: the held video is not the approved file")
    if sha256_of(paths['cover.png']) != job['cover_sha256']:
        raise ReleaseError(f"{job['id']}: the held cover is not the approved one")
    try:
        review = json.loads(paths['review.json'].read_text(encoding='utf-8'))
    except ValueError:
        raise ReleaseError(f"{job['id']}: review.json unreadable") from None
    if review.get('video_sha256') != job['video_sha256'] or str(review.get('run_id')) != str(job['run_id']):
        raise ReleaseError(f"{job['id']}: review.json belongs to another file")
    titles = review.get('titles') or {}
    if not titles.get('youtube') or not titles.get('instagram') or not review.get('youtube_description'):
        raise ReleaseError(f"{job['id']}: review.json lacks titles or description")
    return paths['video.mp4'], paths['cover.png'], review


def topic_for(job, review, bank=None):
    """The run's own topic object (same announcement brief), refused if the bank changed it since the run."""
    from tools.daily_topic_selection import announcement_topic, load_bank
    topic = announcement_topic(bank if bank is not None else load_bank(), job['announcement'])
    if str(topic) != review.get('topic'):
        raise ReleaseError(f"{job['id']}: the announcement topic changed since the held run")
    return topic


def meta_caption(ds, topic, job, review):
    """The daily run's Facebook / Telegram caption (daily_short.py step 10d2)."""
    title = job.get('instagram_title') or review['titles']['instagram']
    first = review['youtube_description'].split('\n')[0]
    launch = ds.campaign_line(topic, 'ig_line')
    return f"{title}\n\n{first}\n\n" + (launch or '📦 Order: Sale91.com')


class S3State:
    """One job's release state; every write is conditional on the version just read."""

    def __init__(self, client, job_id, bucket=BUCKET):
        self.client, self.bucket, self.key, self.etag = client, bucket, STATE_PREFIX + job_id + '.json', None

    def read(self):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self.key)
        except Exception as error:
            code = str(((getattr(error, 'response', None) or {}).get('Error') or {}).get('Code', ''))
            if code in ('NoSuchKey', '404', 'NotFound'):
                self.etag = None
                return {}
            raise ReleaseError(f'release state unreadable ({type(error).__name__}); nothing is posted') from None
        body = response['Body'].read(65537)
        if len(body) > 65536 or not response.get('ETag'):
            raise ReleaseError('release state invalid; nothing is posted')
        state = json.loads(body)
        if not isinstance(state, dict):
            raise ReleaseError('release state invalid; nothing is posted')
        self.etag = response['ETag']
        return state

    def save(self, state):
        condition = {'IfMatch': self.etag} if self.etag else {'IfNoneMatch': '*'}
        try:
            response = self.client.put_object(
                Bucket=self.bucket, Key=self.key, Body=json.dumps(state, sort_keys=True).encode('utf-8'),
                ContentType='application/json', CacheControl='no-store', **condition)
        except Exception as error:
            raise ReleaseError(f'release state write failed ({type(error).__name__}); stopping before any post') from None
        if not response.get('ETag'):
            raise ReleaseError('release state write unconfirmed; stopping before any post')
        self.etag = response['ETag']


def now_iso(now):
    return now.astimezone(timezone.utc).isoformat(timespec='seconds')


def release_youtube(job, ds, store, folder, now=None, get=None):
    """Book the held file on the bot channel at youtube_at. Returns the video id."""
    now = now or datetime.now(timezone.utc)
    state = store.read()
    if 'youtube' in state:
        booked = state['youtube']
        if isinstance(booked, dict) and booked.get('video_id'):
            print(f"   ✅ {job['id']}: already booked on YouTube: {booked['video_id']}")
            return booked['video_id']
        raise ReleaseError(f"{job['id']}: an earlier YouTube attempt left {booked!r}; reconcile on the channel first")
    at = parse_at(job['youtube_at'])
    if at < now + YOUTUBE_LEAD:
        raise ReleaseError(f"{job['id']}: youtube_at must be at least 30 min ahead")
    video, cover, review = fetch_held(job, folder, get=get)
    topic = topic_for(job, review)
    youtube = ds.get_youtube_service()
    if youtube is None:
        raise ReleaseError('no YouTube client; nothing uploaded')
    from tools.publish_slots import channel_taken, when
    clash = [t for t in channel_taken(youtube, now) if abs(t.at - at) < GAP]
    if clash:
        raise ReleaseError(f"{job['id']}: another video within 2 h on this channel: "
                           + ', '.join(f'{when(t.at)} ({t.source})' for t in clash))
    state['youtube'] = {'pending': True, 'attempt_at': now_iso(now)}
    store.save(state)
    at_ist, at_utc = at.astimezone(IST), at.astimezone(timezone.utc)
    ds.get_publish_time = lambda youtube=None: (at_ist, at_utc)
    title = job.get('youtube_title') or review['titles']['youtube']
    try:
        video_id, url = ds.upload_to_youtube(youtube, str(video), title, review['youtube_description'],
                                             review.get('youtube_tags') or [], topic=topic)
    except Exception as error:
        state['youtube'] = {'failed': f'upload error {type(error).__name__}', 'attempt_at': now_iso(now)}
        store.save(state)
        raise ReleaseError(f"{job['id']}: YouTube upload failed ({type(error).__name__})") from None
    if not VIDEO_ID.fullmatch(str(video_id or '')):
        state['youtube'] = {'failed': 'no video id', 'attempt_at': now_iso(now)}
        store.save(state)
        raise ReleaseError(f"{job['id']}: YouTube returned no video id")
    state['youtube'] = {'video_id': video_id, 'url': url, 'publish_at': at.isoformat(), 'uploaded_at': now_iso(now)}
    store.save(state)
    print(f"   ✅ {job['id']}: uploaded {url}, public {at_ist.strftime('%a %d-%b %H:%M')} IST")

    custom_pin = ds.get_pin_tail(topic)
    if custom_pin and ds.AUTO_PIN_COMMENT:
        from tools.youtube_status import StatusRestorationError, post_daily_ai_comment
        try:
            post_daily_ai_comment(youtube, video_id, custom_pin,
                                  lambda: ds.pin_comment(youtube, video_id, comment_text=custom_pin), scheduled=True)
        except StatusRestorationError as error:
            raise ReleaseError(f"{job['id']}: the comment step could not restore the schedule ({error}); "
                               f"check {video_id} on the channel NOW") from None
        except Exception as error:
            print(f"   ⚠️ Buyer question comment skipped ({type(error).__name__}); upload kept")
    try:
        ds.upload_thumbnail(youtube, video_id, str(cover))
    except Exception as error:
        print(f"   ⚠️ Cover step errored ({type(error).__name__}); upload kept")
    try:
        ds.add_to_playlist(youtube, video_id, topic)
    except Exception as error:
        print(f"   ⚠️ Playlist step errored ({type(error).__name__}); upload kept")

    checks = readback(youtube, video_id, at)
    state['youtube']['readback'] = checks
    store.save(state)
    if not all(checks.values()):
        raise ReleaseError(f"{job['id']}: YouTube readback failed {checks}; fix {video_id} on the channel")
    print(f"   🔎 {job['id']}: readback private + publishAt OK, synthetic media label not refused")
    return video_id


def readback(youtube, video_id, at):
    """YouTube often OMITS containsSyntheticMedia from a status readback (tools/youtube_status.py treats an omitted
    field after an accepted True as set); only an explicit non-True value fails."""
    items = youtube.videos().list(part='status', id=video_id).execute().get('items') or []
    status = (items[0] if items else {}).get('status') or {}
    print(f"   status readback: { {k: status.get(k) for k in ('privacyStatus', 'publishAt', 'containsSyntheticMedia')} }")
    return {
        'private': status.get('privacyStatus') == 'private',
        'publish_at': bool(status.get('publishAt')) and parse_at(status['publishAt'].replace('Z', '+00:00')) == at,
        'synthetic_media': status.get('containsSyntheticMedia', True) is True,
    }


def release_meta_due(jobs, ds, store_for, folder, now=None, get=None):
    """Facebook + Telegram for every job whose meta_at has passed (inside META_WINDOW). Returns a report list."""
    now = now or datetime.now(timezone.utc)
    report = []
    for job in sorted(jobs, key=lambda j: parse_at(j['meta_at'])):
        due = parse_at(job['meta_at'])
        if due > now:
            continue
        store = store_for(job['id'])
        state = store.read()
        todo = [p for p in META_PLATFORMS if p not in state]
        if not todo:
            continue
        if now - due > META_WINDOW:
            report.append(f"{job['id']}: {', '.join(todo)} missed its time ({due.astimezone(IST):%d-%b %H:%M} IST); "
                          "not posted late - decide by hand")
            continue
        video, cover, review = fetch_held(job, Path(folder) / job['id'], get=get)
        topic = topic_for(job, review)
        caption = meta_caption(ds, topic, job, review)
        for platform in todo:
            state[platform] = {'pending': True, 'attempt_at': now_iso(now)}
            store.save(state)
            try:
                if platform == 'facebook':
                    posted = ds.publish_fb_reel(str(video), caption, cover_path=str(cover))
                else:
                    posted = ds.post_telegram_channel(str(video), caption)
            except Exception as error:
                posted, why = None, type(error).__name__
            else:
                why = 'no id returned'
            if posted:
                state[platform] = {'id': str(posted), 'posted_at': now_iso(now)}
                report.append(f"{job['id']}: {platform} posted ({posted})")
            else:
                state[platform] = {'failed': why, 'attempt_at': now_iso(now)}
                report.append(f"{job['id']}: {platform} FAILED ({why}) - check the page before any retry")
            store.save(state)
    return report


def alert(text):
    token = (os.environ.get('ALERT_BOT_TOKEN') or '').strip()
    chat = (os.environ.get('ALERT_CHAT_ID') or '').strip()
    if not token or not chat:
        return
    try:
        import requests
        requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                      data={'chat_id': chat, 'text': text[:3500]}, timeout=20)
    except Exception:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('check', 'youtube', 'verify', 'meta_due'), required=True)
    parser.add_argument('--job')
    parser.add_argument('--jobs', type=Path, default=JOBS)
    args = parser.parse_args(argv)
    jobs = load_jobs(args.jobs)
    folder = Path(tempfile.mkdtemp(prefix='held-short-'))
    if args.mode == 'check':
        for job in jobs:
            video, cover, review = fetch_held(job, folder / job['id'])
            topic = topic_for(job, review)
            print(f"✅ {job['id']}: run {job['run_id']} video {job['video_sha256'][:16]} = approved; topic matches")
            print(f"   YouTube {parse_at(job['youtube_at']).astimezone(IST):%a %d-%b %H:%M} IST: "
                  f"{job.get('youtube_title') or review['titles']['youtube']}")
            print(f"   Facebook + Telegram {parse_at(job['meta_at']).astimezone(IST):%a %d-%b %H:%M} IST")
            try:
                import daily_short as ds
                print('   caption: ' + meta_caption(ds, topic, job, review).replace('\n', ' | '))
            except ImportError:
                print('   caption: (daily_short.py not importable here)')
        return 0
    import boto3
    import daily_short as ds
    client = boto3.client('s3')
    if args.mode in ('youtube', 'verify'):
        job = next((j for j in jobs if j['id'] == args.job), None)
        if not job:
            raise ReleaseError('unknown job id')
    if args.mode == 'verify':
        store = S3State(client, job['id'])
        state = store.read()
        video_id = (state.get('youtube') or {}).get('video_id')
        if not video_id:
            raise ReleaseError(f"{job['id']}: not booked")
        checks = readback(ds.get_youtube_service(), video_id, parse_at(job['youtube_at']))
        state['youtube']['readback'] = checks
        store.save(state)
        print(f"   {job['id']} {video_id}: {checks}")
        return 0 if all(checks.values()) else 1
    if args.mode == 'youtube':
        try:
            video_id = release_youtube(job, ds, S3State(client, job['id']), folder / job['id'])
        except ReleaseError as error:
            alert(f'Held Short {job["id"]}: YouTube booking stopped - {error}')
            raise
        alert(f"Held Short {job['id']}: booked on YouTube https://youtube.com/shorts/{video_id} for "
              f"{parse_at(job['youtube_at']).astimezone(IST):%a %d-%b %H:%M} IST")
        return 0
    report = release_meta_due(jobs, ds, lambda job_id: S3State(client, job_id), folder)
    for line in report:
        print('   ' + line)
    if report:
        alert('Held Shorts (Facebook + Telegram):\n' + '\n'.join(report))
    return 1 if any('FAILED' in line or 'missed' in line for line in report) else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ReleaseError as error:
        print(f'❌ {error}')
        sys.exit(1)
