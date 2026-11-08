#!/usr/bin/env python3
"""Telegram Ketu when a reviewed Instagram photo (feed_queue/) went out late.

Reads the JSON that tools/curated_feed.py --execute printed. Silent when this run published no photo, or published it
within LATE_AFTER of its publish_at. A failed run is already reported by the workflow's own failure step.

Usage: python3 tools/curated_feed_alert.py <result.json> [queue_dir]
Env: RUN_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_ALERT_CHAT_ID
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


def problems(result, queue):
    if not isinstance(result, dict) or result.get("reason") != "curated_published" or not result.get("job_id"):
        return []
    job_id = result["job_id"]
    try:
        job = next(data for data in (json.loads(path.read_text(encoding="utf-8")) for path in sorted(Path(queue).glob("*.json")))
                   if isinstance(data, dict) and data.get("id") == job_id)
        state = json.loads((Path(queue) / "state" / (job_id + ".json")).read_text(encoding="utf-8"))
        due = datetime.fromisoformat(job["publish_at"])
        posted = datetime.fromisoformat(state["published_at"])
    except (StopIteration, OSError, ValueError, KeyError, TypeError):
        return ["- %s was published, but its time could not be checked" % job_id]
    late = int((posted - due).total_seconds())
    if late <= LATE_AFTER:
        return []
    return ["- %s went out %d min late (due %s IST)" % (job_id, late // 60, due.astimezone(IST).strftime("%d %b %H:%M"))]


def main(path, queue="feed_queue"):
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = None
    lines = problems(result, queue)
    if not lines:
        print("Reviewed photo: on time or none published, nothing to report.")
        return 0
    text = "Instagram photo post needs a look\n" + "\n".join(lines)
    if os.environ.get("RUN_URL"):
        text += "\n" + os.environ["RUN_URL"]
    print(text)
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_ALERT_CHAT_ID") or "").strip()
    if not token or not chat:
        print("Telegram secrets missing; alert not sent.")
        return 1
    body = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
    try:
        reply = urllib.request.urlopen(urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token, data=body), timeout=20)
        sent = json.loads(reply.read().decode()).get("ok", False)
    except Exception as exc:
        print("Telegram send failed: " + type(exc).__name__)
        return 1
    print("Telegram sent." if sent else "Telegram refused the alert.")
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "", *sys.argv[2:3]))
