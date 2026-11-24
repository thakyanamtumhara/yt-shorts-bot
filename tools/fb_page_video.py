"""Schedule ONE long video on the Facebook Page with Facebook's own scheduler.

usage: python3 tools/fb_page_video.py fb_page_videos/<job>.json [--execute]

For videos too long for a Reel or for Vizard (a whole video there is at most 3 minutes): POST /{page}/videos with
file_url, published=false and scheduled_publish_time, then Facebook itself publishes it at that time.
Job file keys: id, video_url, video_sha256, title, description, publish_at_ist ("YYYY-MM-DD HH:MM"), approval.
The hosted file must hash to video_sha256 before anything is sent. The answer is written to <job>.result.json,
and a job that already has a result is only read back, never posted twice.
"""
import hashlib, json, os, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

V = 'v24.0'
IST = timezone(timedelta(hours=5, minutes=30))
KEYS = {'id', 'video_url', 'video_sha256', 'title', 'description', 'publish_at_ist', 'approval'}


def call(method, url, data=None):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f'{method} {url.split("?")[0]} -> HTTP {e.code}: {e.read().decode()[:400]}')


def read_back(video_id, token):
    fields = 'id,title,published,scheduled_publish_time,status,permalink_url,length'
    return call('GET', f'https://graph.facebook.com/{V}/{video_id}?' + urllib.parse.urlencode({'fields': fields, 'access_token': token}))


def main():
    job_path = Path(sys.argv[1])
    execute = '--execute' in sys.argv
    job = json.loads(job_path.read_text())
    if set(job) != KEYS:
        raise SystemExit(f'job keys must be exactly {sorted(KEYS)}')
    result_path = job_path.with_suffix('.result.json')
    token = os.environ['FB_PAGE_ACCESS_TOKEN'].strip()
    page = os.environ['FB_PAGE_ID'].strip()
    if result_path.exists():
        done = json.loads(result_path.read_text())
        print(json.dumps({'already_scheduled': done.get('video_id'), 'now': read_back(done['video_id'], token)}, ensure_ascii=False, indent=1))
        return
    when = datetime.strptime(job['publish_at_ist'], '%Y-%m-%d %H:%M').replace(tzinfo=IST)
    ahead = when - datetime.now(timezone.utc)
    if not timedelta(minutes=20) <= ahead <= timedelta(days=150):
        raise SystemExit(f'publish time must be 20 min to 150 days ahead (Facebook allows 10 min to 6 months); it is {ahead}')
    sha, size = hashlib.sha256(), 0
    with urllib.request.urlopen(job['video_url'], timeout=300) as r:
        while chunk := r.read(1 << 20):
            sha.update(chunk); size += len(chunk)
    if sha.hexdigest() != job['video_sha256']:
        raise SystemExit('hosted video differs from the approved file')
    print(f'hosted video verified: {size} bytes, sha256 {job["video_sha256"][:16]}')
    if not execute:
        print('[dry] would schedule', job['id'], 'at', job['publish_at_ist'], 'IST'); return
    out = call('POST', f'https://graph-video.facebook.com/{V}/{page}/videos', {
        'file_url': job['video_url'], 'title': job['title'], 'description': job['description'],
        'published': 'false', 'scheduled_publish_time': str(int(when.timestamp())), 'access_token': token})
    video_id = out['id']
    result = {'job': job['id'], 'video_id': video_id, 'publish_at_ist': job['publish_at_ist'],
              'scheduled_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    result_path.write_text(json.dumps(result, indent=1) + '\n')
    for _ in range(20):
        now = read_back(video_id, token)
        if (now.get('status') or {}).get('video_status') not in ('processing', 'upload_complete', None):
            break
        time.sleep(30)
    result['read_back'] = now
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=1))
    spt = now.get('scheduled_publish_time')
    if isinstance(spt, str) and 'T' in spt:
        spt = datetime.strptime(spt, '%Y-%m-%dT%H:%M:%S%z').timestamp()
    if now.get('published') is not False or int(float(spt or 0)) != int(when.timestamp()):
        raise SystemExit('Facebook did not keep the video unpublished with the asked publish time')


if __name__ == '__main__':
    main()
