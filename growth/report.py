#!/usr/bin/env python3
"""Telegram Ketu the weekly Google numbers (growth/weekly.json, written by pull_gsc.py) against the 17-Oct-2026 start
and the week before. Every automatic job tells him when it breaks (his rule, 18-Oct-2026): a row with no numbers is
reported as blind, and --check (Tuesday) tells him when Monday's row never came.
Usage: python3 growth/report.py [--check | --print]"""
import datetime as dt
import json
import os
import pathlib
import sys
import urllib.request

ROWS = json.loads((pathlib.Path(__file__).resolve().parent / "weekly.json").read_text())


def telegram(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_ALERT_CHAT_ID")
    if not tok or not chat:
        sys.exit("Telegram secrets missing")
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage",
                                 data=json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        if not json.load(r).get("ok"):
            sys.exit("Telegram refused the message")
    pathlib.Path("/tmp/growth_told").touch()


def get(row, *path):
    for p in path:
        row = (row or {}).get(p)
    return row


def count(label, path, now, start, last):
    n = get(now, *path)
    if n is None:
        return f"• {label}: not in this week's data"
    parts = []
    for name, row in (("since the 17-Oct start", start), ("vs last week", None if last is start else last)):
        b = get(row, *path) if row is not None else None
        if b is not None:
            parts.append(f"{'+' if n >= b else ''}{n - b:,} {name}")
    return f"• {label}: {n:,}" + (f" ({', '.join(parts)})" if parts else "")


def message(rows):
    now, start = rows[-1], rows[0]
    last = rows[-2] if len(rows) > 1 else start
    lines = [f"Weekly Google numbers, India, 28 days ({now.get('window_28d', '?').replace('..', ' to ')}):",
             count("Clicks from searches without our name (the SEO number)", ("india_non_brand_28d", "clicks"), now, start, last),
             count("All clicks", ("india_28d", "clicks"), now, start, last),
             count("Clicks from searches with our name", ("india_brand_28d", "clicks"), now, start, last),
             count("Times shown on Google", ("india_28d", "impressions"), now, start, last),
             count("DTF site clicks", ("dtf_site_28d", "clicks"), now, start, last)]
    watched = now.get("watched_queries") or {}
    if watched:
        was = start.get("watched_queries") or {}
        lines.append("• Place on Google (1 = top), now / at the start: " + ", ".join(
            f"{q} {v['position']}" + (f" / {was[q]['position']}" if q in was else "") for q, v in
            sorted(watched.items(), key=lambda kv: kv[1]["position"])))
    lines.append("Google's numbers come 3 days late. First real read 27-Oct, verdict 16-Nov.")
    return "\n".join(lines)


def main():
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date()
    newest = ROWS[-1] if ROWS else {}
    if "--check" in sys.argv:
        if newest.get("pulled", "") >= (today - dt.timedelta(days=1)).isoformat():
            print("Monday's row is in")
            return
        telegram(f"The weekly Google numbers did not come this Monday. The last row is from {newest.get('pulled', 'never')}.")
        sys.exit("Monday's row is missing")
    if not get(newest, "india_28d", "impressions"):
        telegram("The weekly Google numbers are blind: Search Console sent nothing this week. Check its access.")
        sys.exit("blind: no Search Console numbers")
    text = message(ROWS)
    print(text)
    if "--print" not in sys.argv:
        telegram(text)


if __name__ == "__main__":
    main()
