"""Is the daily auto Short still going out? Says so on the WWbun top bar when it is not.

The daily run already sends one Telegram per failed day. This watch answers the other question:
how long has nothing gone out, and why. It reads only files this repo keeps:

  lesson_history.json          one entry per published daily Short (published_on)
  ig_engagement_history.json   one entry per Instagram Reel of a daily run (workflow_run_id)
  ig_drafts/*.json             the automatic Instagram photo posts (posted_at)
  daily_short_status.json      how the last runs ended (written by record_run below)

Two missed working days (Mon-Sat) put an item on urgent_actions.json; the first published Short
takes it off. Nothing here stops, starts or changes a run.
"""

from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = ROOT / 'daily_short_status.json'
IST = timezone(timedelta(hours=5, minutes=30))
OWNER = 'content_watch'
MISSED_DAYS = 2
RUN_DONE_BY = time(17, 0)
REPO_URL = 'https://github.com/thakyanamtumhara/yt-shorts-bot'
TEST_FLAGS = ('TEST_MODE', 'NEW_TEST_MODE', 'SINGLE_VEO_TEST', 'HOLD_ONLY', 'ANSWER_DRY')
REASONS = (
    (r'TopicHold|No distinct, evidence-supported topic', 'no new topic passed the checks'),
    (r'No approved script|No plan passed the fact check', 'no script passed the fact check'),
    (r'WriteHold|searched questions', 'the question list could not be used'),
    (r'VoiceHold|ElevenLabs', 'the voice could not be made'),
    (r'RenderHold', 'the video could not be built from the real clips'),
    (r'ClipRepairError|clip check|VisualReviewError|visual review', 'the pictures did not match the lesson'),
    (r'OwnerReviewStop|ReviewHold|rejected by|review: rejected', 'the final AI review refused the video'),
    (r'RateError|rate list|Catalogue', 'the website rate list could not be read'),
    (r'402|PaymentRequired|prepayment|Insufficient credit|billing', 'a paid AI service refused (billing)'),
    (r'preflight|dependency gate', 'a needed service was down before the run'),
)


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def plain_reason(text):
    text = str(text or '')
    for pattern, words in REASONS:
        if re.search(pattern, text, re.I):
            return words
    return 'a technical error' if text.strip() else ''


def last_short(root=ROOT):
    """Day of the newest published daily Short, from the two files each upload writes."""
    days = [_day(item.get('published_on')) for item in _read(Path(root) / 'lesson_history.json', [])
            if isinstance(item, dict)]
    days += [_day(item.get('published_at')) for item in _read(Path(root) / 'ig_engagement_history.json', [])
             if isinstance(item, dict) and item.get('workflow_run_id')]
    answers = _read(Path(root) / 'answer_history.json', {})
    days += [_day(item.get('date')) for item in (answers.get('published') or [] if isinstance(answers, dict) else [])
             if isinstance(item, dict)]
    days = [day for day in days if day]
    return max(days) if days else None


def last_photo_post(root=ROOT):
    days = []
    for path in (Path(root) / 'ig_drafts').glob('*.json'):
        draft = _read(path, {})
        if isinstance(draft, dict) and draft.get('posted') is True:
            days.append(_day(draft.get('posted_at')))
    days = [day for day in days if day]
    return max(days) if days else None


def missed_working_days(last, now):
    """Mon-Sat days after `last` with no Short; today counts only once its run should be over."""
    if last is None:
        return 0
    end = now.date() if now.time() >= RUN_DONE_BY else now.date() - timedelta(days=1)
    count, day = 0, last + timedelta(days=1)
    while day <= end:
        if day.weekday() != 6:
            count += 1
        day += timedelta(days=1)
    return count


def record_run(error=None, root=ROOT, now=None, env=None):
    """Called once when the daily run ends. Never raises: a status note must not fail a run."""
    try:
        env = os.environ if env is None else env
        if any(str(env.get(flag, '')).strip().lower() in ('1', 'true', 'yes') for flag in TEST_FLAGS):
            return None
        now = now or datetime.now(IST)
        today = now.date()
        published = last_short(root) == today
        if error is not None and not (isinstance(error, SystemExit) and error.code in (0, None)):
            text = f'{type(error).__name__}: {error}'
        else:
            text = ''
        decision = _read(Path(root) / 'review_decisions' / f"{env.get('GITHUB_RUN_ID', '')}.json", {})
        if not published and not text and isinstance(decision, dict) and decision.get('decision') == 'reject':
            text = 'review: rejected'
        entry = {'date': today.isoformat(), 'published': published,
                 'reason': '' if published else (plain_reason(text) or 'the run ended without a video'),
                 'detail': '' if published else text[:300],
                 'run': str(env.get('GITHUB_RUN_ID', ''))[:20]}
        path = Path(root) / STATUS_PATH.name
        status = _read(path, {})
        runs = [item for item in status.get('runs', []) if isinstance(item, dict) and item.get('date') != entry['date']
                or item.get('published')] if isinstance(status, dict) else []
        runs = [item for item in runs if not (item.get('date') == entry['date'] and entry['published'])]
        runs.append(entry)
        path.write_text(json.dumps({'format': 'daily-short-status-v1', 'runs': runs[-30:]},
                                   ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        return entry
    except Exception as failure:
        print(f'   ⚠️ Run status not saved ({type(failure).__name__})')
        return None


def _short_date(day):
    return f'{day.day} {day:%b}'


def verdict(root=ROOT, now=None):
    now = now or datetime.now(IST)
    last = last_short(root)
    missed = missed_working_days(last, now)
    status = _read(Path(root) / STATUS_PATH.name, {})
    runs = [item for item in status.get('runs', []) if isinstance(item, dict)] if isinstance(status, dict) else []
    failed = [item for item in runs if not item.get('published') and (last is None or (_day(item.get('date')) or last) > last)]
    newest = failed[-1] if failed else None
    reasons = []
    for item in reversed(failed):
        if item.get('reason') and item['reason'] not in reasons:
            reasons.append(item['reason'])
    photo = last_photo_post(root)
    return {'last': last, 'missed': missed, 'stopped': missed >= MISSED_DAYS, 'reasons': reasons[:2],
            'run': (newest or {}).get('run') or '', 'ran_days': len({item.get('date') for item in failed}),
            'photo': photo}


def urgent_item(result):
    if not result['stopped']:
        return {}
    last = result['last']
    why = ' and '.join(result['reasons']) if result['reasons'] else 'not known yet'
    if result['ran_days'] == 0:
        why = 'the daily run did not start'
    detail = (f"No automatic Short for {result['missed']} working days. Why: {why}. "
              "Nothing to pay and no login. Tell any AI session: fix the daily Short.")
    url = f"{REPO_URL}/actions/runs/{result['run']}" if result['run'] else f'{REPO_URL}/actions/workflows/daily_short.yml'
    return {'auto_short_stopped': {'title': f'Auto Short stopped: last one {_short_date(last)}',
                                   'detail': detail[:280], 'url': url,
                                   'evidence': f"last {last.isoformat()}; missed {result['missed']}; {why}"}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    result = verdict()
    items = urgent_item(result)
    last = result['last'].isoformat() if result['last'] else 'never'
    print(f"Daily Short: last published {last}; missed working days {result['missed']}; "
          f"{'STOPPED' if result['stopped'] else 'ok'}"
          + (f"; why: {', '.join(result['reasons'])}" if result['reasons'] else ''))
    if '--dry-run' in argv:
        print(json.dumps(items, ensure_ascii=False, indent=1))
        return 0
    from tools.urgent_actions import update
    update(OWNER, items)
    return 0


if __name__ == '__main__':
    sys.exit(main())
