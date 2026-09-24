#!/usr/bin/env python3
"""Read-only output receipts; never generate content or publish media."""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import audit_short_output as audit

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parents[1]
TEST_INPUTS = ('test_mode', 'new_test_mode', 'single_veo_test', 'test_thumbnail')


def timestamp(value):
    try:
        value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return value.astimezone(IST) if value.tzinfo else None
    except ValueError:
        return None


def result(state, note, **evidence):
    return {'state': state, 'note': note, **evidence}


def guarded(call):
    try:
        return call()
    except Exception as error:
        return result('unknown', 'Verification unavailable', error_type=type(error).__name__)


def latest_today(runs, now):
    eligible = []
    for run in runs:
        created = timestamp(run.get('created_at'))
        inputs = run.get('inputs') or {}
        test = any(str(inputs.get(key, '')).lower() == 'true' for key in TEST_INPUTS)
        if (created and created.date() == now.date() and run.get('head_branch') == 'main'
                and run.get('event') in ('schedule', 'workflow_dispatch') and not test):
            eligible.append(run)
    return max(eligible, key=lambda run: (run['created_at'], run['id']), default=None)


def workflow_state(workflow, now, deadline, sunday=False, inspect_failed=False):
    if sunday and now.weekday() == 6:
        return result('not_due', 'Sunday: no daily Short scheduled'), None
    listing = audit.github_json(f'/actions/workflows/{workflow}/runs?branch=main&per_page=50')
    run = latest_today(listing.get('workflow_runs', []), now)
    if run is None:
        return result('not_due' if now.time() < deadline else 'issue',
                      'Not due yet' if now.time() < deadline else 'No production run today'), None
    evidence = {'run_id': run['id']}
    if run.get('status') != 'completed':
        return result('pending', 'Today\'s run is still in progress', **evidence), None
    if run.get('conclusion') != 'success':
        return result('issue', 'Today\'s latest run did not succeed',
                      conclusion=run.get('conclusion'), **evidence), run if inspect_failed else None
    return result('ok', 'Today\'s run succeeded', **evidence), run


def youtube_check(manifest, now):
    video_id = manifest['source_posts']['bot_youtube']
    read = audit.youtube_readback(video_id, (manifest.get('titles') or {}).get('youtube', ''))
    disclosure = audit.youtube_disclosure_verification(manifest, read)
    saved = (manifest.get('run_flags') or {}).get('youtube_status_evidence') or {}
    expected = (saved.get('request') or {}).get('status') or {}
    scheduled = timestamp(expected.get('publishAt'))
    if (not disclosure.get('verified') and read.get('privacyStatus') == 'public'
            and not read.get('publishAt') and expected.get('privacyStatus') == 'private'
            and scheduled and scheduled <= now):
        ack = saved.get('acknowledgment') or {}
        historical = audit.status_verification(video_id, expected, saved.get('status_readback'), ack)
        actual = read.get('mutable_status') or {}
        stable = {key: value for key, value in expected.items()
                  if key not in ('privacyStatus', 'publishAt', 'containsSyntheticMedia')}
        if (saved.get('verified') is True and saved.get('video_id') == video_id
                and (saved.get('request') or {}).get('id') == video_id
                and expected.get('containsSyntheticMedia') is True
                and ack.get('id') == video_id and (ack.get('status') or {}).get('containsSyntheticMedia') is True
                and historical.get('verified') is True and actual.get('containsSyntheticMedia', True) is True
                and all(type(actual.get(key)) is type(value) and actual[key] == value for key, value in stable.items())):
            disclosure = {'verified': True, 'state': 'bound_native_ack_after_expected_publication'}
    future = timestamp(read.get('publishAt'))
    publication = read.get('privacyStatus') == 'public' or (
        read.get('privacyStatus') == 'private' and future is not None and future > now)
    passed = (publication and read.get('uploadStatus') == 'processed'
              and read.get('title_matches_manifest') is True and disclosure.get('verified') is True)
    return result('ok' if passed else 'issue', 'Processed; public or scheduled ahead' if passed else
                  'Publication, processing or native AI acknowledgment not verified', video_id=video_id,
                  privacy=read.get('privacyStatus'), publish_at=read.get('publishAt'),
                  upload_status=read.get('uploadStatus'), disclosure=disclosure)


def instagram_check(manifest, feed=False):
    read = audit.instagram_readback(manifest)
    readable = read.get('published_media_readable') is True
    if feed:
        readable = bool(read.get('owner_verified') and read.get('permalink')
                        and read.get('media_type') in ('IMAGE', 'CAROUSEL_ALBUM', 'VIDEO')
                        and read.get('media_product_type') in ('FEED', 'REELS'))
    passed = readable and (feed or read.get('native_ai_disclosure') == 'api_true')
    return result('ok' if passed else 'issue', 'Owner and published media verified' if passed else
                  'Instagram publication or required native AI flag not verified',
                  media_id=read.get('media_id'), owner_verified=read.get('owner_verified'),
                  native_ai_disclosure=read.get('native_ai_disclosure'), permalink=read.get('permalink'))


def daily_check(now, observed_now=None):
    state, run = workflow_state('daily_short.yml', now, datetime.min.replace(hour=16, minute=37).time(),
                                True, inspect_failed=True)
    if run is None:
        return state
    run = audit.github_json(f'/actions/runs/{run["id"]}')
    audit.validate_run(run, str(run['id']))
    try:
        data, artifact_id = audit.download_artifact(str(run['id']), run)
    except Exception as error:
        return result('issue', 'Run has no verifiable finished output archive',
                      run_id=run['id'], conclusion=run.get('conclusion'), error_type=type(error).__name__)
    with tempfile.TemporaryDirectory() as folder:
        manifest, _ = audit.validate_archive(data, run, Path(folder))
    flags = manifest.get('run_flags') or {}
    checks = {'audio': result('ok' if (flags.get('native_audio_review') or {}).get('passed') is True else 'issue',
                              'Saved native audio review; no new model assessment'),
              'visual': result('ok' if (flags.get('native_visual_review') or {}).get('passed') is True else 'issue',
                               'Saved final visual approval' if (flags.get('native_visual_review') or {}).get('passed') is True else
                               'No passed final visual review saved'),
              'youtube': guarded(lambda: youtube_check(manifest, observed_now or now)),
              'instagram': guarded(lambda: instagram_check(manifest))}
    def facebook():
        read = audit.facebook_readback(manifest)
        passed = read.get('publication_confirmed_by_api') is True and read.get('public_by_api') is True
        return result('ok' if passed and read.get('native_ai_disclosure') != 'api_false' else 'issue',
                      read.get('publication_state', read.get('state', 'Publication unknown')), video_id=read.get('video_id'),
                      native_ai_disclosure=read.get('native_ai_disclosure'),
                      disclosure_requested=read.get('manifest_disclosure_requested'),
                      visible_label='Not independently checked')
    checks['facebook'] = guarded(facebook)
    passed = all(item['state'] == 'ok' for item in checks.values()) and state['state'] == 'ok'
    return result('ok' if passed else 'issue', 'Verified output receipts' if passed else
                  'Run failed; individual publication receipts checked' if state['state'] != 'ok' else
                  'Some output checks need attention', run_id=run['id'], artifact_id=artifact_id,
                  conclusion=run.get('conclusion'), checks=checks)


def feed_receipts(root, now):
    posted, eligible = [], 0
    for file in sorted((root / 'ig_drafts').glob('*.json'))[-120:]:
        draft = json.loads(file.read_text())
        stamp = timestamp(draft.get('posted_at'))
        if draft.get('posted') is True and stamp and stamp.date() == now.date() and draft.get('media_id'):
            posted.append(str(draft['media_id']))
        try:
            age = (now.date() - datetime.fromisoformat(file.stem).date()).days
        except ValueError:
            continue
        if (0 <= age <= 4 and not draft.get('posted') and not draft.get('held')
                and not draft.get('skipped') and draft.get('image_urls')):
            eligible += 1
    for file in sorted((root / 'feed_queue' / 'state').glob('*.json'))[-120:]:
        data = json.loads(file.read_text())
        stamp = timestamp(data.get('published_at'))
        if (data.get('format') == 'curated-feed-v1' and data.get('mode') == 'publish'
                and data.get('phase') == 'published' and data.get('account_id') == audit.IG_ACCOUNT
                and stamp and stamp.date() == now.date() and data.get('media_id')):
            posted.append(str(data['media_id']))
    return list(dict.fromkeys(posted)), eligible


def feed_check(now, root=ROOT):
    state, run = workflow_state('ig_carousel.yml', now, datetime.min.replace(hour=13, minute=30).time())
    if run is None:
        return state
    posted, eligible = feed_receipts(root, now)
    if not posted:
        return result('issue' if eligible else 'unknown', 'No dated publication receipt today',
                      run_id=run['id'], eligible_unposted_drafts=eligible)
    if len(posted) > 5:
        return result('unknown', 'Too many daily receipts for bounded verification')
    checks = [guarded(lambda media_id=media_id: instagram_check(
        {'source_posts': {'instagram': media_id}}, feed=True)) for media_id in posted]
    passed = all(check['state'] == 'ok' for check in checks)
    return result('ok' if passed else 'issue', 'Dated feed receipts checked', run_id=run['id'],
                  eligible_unposted_drafts=eligible, checks=checks)


def blog_check(now, root=ROOT):
    weekdays = {int(day) for day in os.environ.get('BLOG_WEEKDAYS', '0,2,4').split(',')
                if day.strip().isdigit()}
    if now.weekday() not in weekdays:
        return result('not_due', 'No generated blog scheduled on this weekday')
    if now.hour * 60 + now.minute < 16 * 60 + 37:
        return result('not_due', 'Today\'s generated blog is not due yet')
    posts = json.loads((root / 'blog_history.json').read_text())
    today = [post for post in posts if timestamp(post.get('date'))
             and timestamp(post['date']).date() == now.date()]
    if not today:
        return result('issue', 'No new blog article dated today')
    url = today[-1].get('url', '')
    if not re.fullmatch(r'https://www\.bulkplaintshirt\.com/p/[a-z0-9-]+\.html', url):
        return result('unknown', 'Article URL cannot be safely verified')
    with audit.requests.get(url, timeout=20, stream=True, allow_redirects=False) as response:
        status = response.status_code
    return result('ok' if status == 200 else 'issue', 'HTTP availability only; not a content quality approval',
                  url=url, http_status=status)


def summary(report):
    lines = ['Auto-content recheck — ' + report['checked_at_ist'] + ' (IST)',
             'Checking outputs for ' + report['checked_date']]
    for name, check in report['checks'].items():
        lines.append(f"{name.title()}: {check['state'].replace('_', ' ')} — {check['note']}.")
        for platform, detail in (check.get('checks') or {}).items() if isinstance(check.get('checks'), dict) else []:
            if detail['state'] != 'ok':
                lines.append(f"  {platform.title()}: {detail['state']} — {detail['note']}.")
    if report.get('monitor_run_url'):
        lines.append('Check details: ' + report['monitor_run_url'])
    return '\n'.join(lines)


def check_clock(now):
    trigger = timestamp(os.environ.get('RECHECK_TRIGGER_CREATED_AT'))
    target = trigger.date() if trigger else now.date()
    if not trigger and os.environ.get('GITHUB_EVENT_NAME') == 'schedule' and (now.hour, now.minute) < (16, 37):
        target -= timedelta(days=1)
    return now if target == now.date() else datetime.combine(target, datetime.max.time(), IST)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    actual_now = datetime.now(IST)
    now = check_clock(actual_now)
    report = {'checked_at_ist': actual_now.isoformat(timespec='seconds'), 'checked_date': now.date().isoformat(),
              'slot': os.environ.get('GITHUB_EVENT_NAME', 'manual'), 'notification_delivered': False,
              'dry_run': args.dry_run, 'checks': {}}
    folder = Path('auto-content-recheck')
    folder.mkdir(exist_ok=True)
    for name, call in [('short', lambda: daily_check(now, actual_now)), ('feed', lambda: feed_check(now)),
                       ('blog', lambda: blog_check(now))]:
        report['checks'][name] = guarded(call) if now.date().isoformat() >= '2026-09-24' else result('not_due', 'Before monitoring began on 24 September')
    monitor_id = os.environ.get('GITHUB_RUN_ID', '')
    if re.fullmatch(r'[1-9][0-9]{0,19}', monitor_id):
        report['monitor_run_url'] = f'https://github.com/{audit.REPOSITORY}/actions/runs/{monitor_id}'
    report['summary'] = summary(report)
    path = folder / 'report.json'
    path.write_text(json.dumps(report, indent=2) + '\n')
    if not args.dry_run and all(os.environ.get(key, '').strip() for key in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_ALERT_CHAT_ID')):
        from social_watch import _tg_direct
        try:
            with redirect_stdout(StringIO()) as output:
                delivered, _ = _tg_direct('Daily content verification', report['summary'])
            report['notification_delivered'] = delivered is True
            match = re.search(r'Private Telegram receipt: ([0-9]+)', output.getvalue())
            if delivered and match:
                report['notification_receipt'] = int(match.group(1))
        except Exception as error:
            report['notification_error_type'] = type(error).__name__
    path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, separators=(',', ':')))
    return int(any(check['state'] not in ('ok', 'not_due') for check in report['checks'].values())
               or (not args.dry_run and not report['notification_delivered']))


if __name__ == '__main__':
    raise SystemExit(main())
