#!/usr/bin/env python3
"""Telegram Ketu when a reviewed Instagram Reel run failed or a Reel went out late.

Reads the JSON that tools/reviewed_ai_reels.py --execute printed. Silent when every
due Reel went out within LATE_AFTER of its time and the run succeeded.

Usage: python3 tools/reviewed_reels_alert.py <release.json>
Env: JOB_STATUS, RUN_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_ALERT_CHAT_ID
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

LATE_AFTER = 30 * 60
IST = timezone(timedelta(hours=5, minutes=30))


def problems(data, status):
    lines = []
    for result in (data or {}).get('results', []):
        if result.get('already_verified'):
            continue
        late = result.get('due_lateness_seconds') or 0
        if late > LATE_AFTER:
            due = ''
            try:
                due = ' (due %s IST)' % (datetime.fromisoformat(result['published_at']) - timedelta(seconds=late - 30)).astimezone(IST).strftime('%d %b %H:%M')
            except (KeyError, TypeError, ValueError):
                pass
            lines.append('- %s went out %d min late%s' % (result.get('job_id'), late // 60, due))
    for error in (data or {}).get('errors', []):
        lines.append('- %s: %s' % (error.get('job_id'), error.get('error')))
    if status and status != 'success' and not lines:
        lines.append('- the publish run stopped before it could report')
    return lines


def main(path):
    data = None
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        pass
    lines = problems(data, os.environ.get('JOB_STATUS', ''))
    if not lines:
        print('Reviewed Reels: on time, nothing to report.')
        return 0
    text = 'Instagram Reel release needs a look\n' + '\n'.join(lines)
    if os.environ.get('RUN_URL'):
        text += '\n' + os.environ['RUN_URL']
    print(text)
    token = (os.environ.get('TELEGRAM_BOT_TOKEN') or '').strip()
    chat = (os.environ.get('TELEGRAM_ALERT_CHAT_ID') or '').strip()
    if not token or not chat:
        print('Telegram secrets missing; alert not sent.')
        return 1
    body = urllib.parse.urlencode({'chat_id': chat, 'text': text, 'disable_web_page_preview': 'true'}).encode()
    try:
        reply = urllib.request.urlopen(urllib.request.Request('https://api.telegram.org/bot%s/sendMessage' % token, data=body), timeout=20)
        sent = json.loads(reply.read().decode()).get('ok', False)
    except Exception as exc:
        print('Telegram send failed: ' + type(exc).__name__)
        return 1
    print('Telegram sent.' if sent else 'Telegram refused the alert.')
    return 0 if sent else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ''))
