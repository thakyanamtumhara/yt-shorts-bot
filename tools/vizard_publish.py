#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Publish/schedule a FINISHED reel to Ketu's socials through the Vizard API.

Replaces the manual wizard legs (Facebook Page, LinkedIn personal, X). The
video must already be fully edited (captions + cover baked in) — Vizard is
used purely as the publishing rail:

    project/create (getClips=0, all enhancement switches OFF)
        -> poll project/query until code 2000
        -> publish-video once per platform with publishTime (EPOCH MILLIS —
           the docs' own example is 13 digits; seconds would schedule 1970)

Facts that cost time to learn (do not rediscover):
  * getClips=0 requires duration < 3 min, else error 4005 (fails closed).
  * subtitleSwitch and headlineSwitch DEFAULT ON — leave them on and Vizard
    re-captions our already-captioned video. Everything is forced off here.
  * project/create consumes upload minutes equal to video length; the other
    endpoints have no documented cost.
  * Rate limit on create: 3/min, 20/hour.
  * socialAccountIds come from GET /project/social-accounts. LinkedIn: only
    his PERSONAL profile ("Ketu R") is connected; the company page shows
    "not connected" — same as his manual posting. YouTube MAIN channel IS
    connected in Vizard but is deliberately not in DEFAULT_ACCOUNTS (his
    call, 2026-08-07: YouTube stays manual).

Key: env VIZARD_API_KEY, else ~/.vizard_api_key. Never hardcode — repo is public.

Usage:
  python3 tools/vizard_publish.py --video-url URL --name "260 GSM launch" \
      --leg x:COPY.json:x_post:"2026-08-07 19:30" \
      --leg fb:COPY.json:fb_caption:"2026-08-07 20:00" \
      --leg li:COPY.json:li_post:"2026-08-08 09:30"
  (times are IST; leg = account:copyfile:field:when)
"""
import argparse, json, os, sys, time, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"
IST = ZoneInfo("Asia/Kolkata")

ACCOUNTS = {
    "fb": ("dml6YXJkLTMtMTY5NjIzLTE3NzMx", "Facebook Page: Own Knitted Blank Wears"),
    "x": ("dml6YXJkLTUtMTY5NzY5", "X: Ketu R"),
    "li": ("dml6YXJkLTYtMTY5NjIyLTE3NzMw", "LinkedIn personal: Ketu R"),
    # YouTube main is connected in Vizard; add only if Ketu says so:
    # "yt-main": ("dml6YXJkLTEtMTcwMDY3", "YouTube: Own Knitted Blank Wears"),
}
CHAR_LIMITS = {"fb": 2200, "x": 280, "li": 3000}


def key():
    k = os.environ.get("VIZARD_API_KEY", "").strip()
    if not k:
        p = os.path.expanduser("~/.vizard_api_key")
        if os.path.exists(p):
            k = open(p).read().strip()
    if not k:
        sys.exit("no VIZARD_API_KEY (env or ~/.vizard_api_key)")
    return k


def call(path, body=None, timeout=60):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"VIZARDAI_API_KEY": key(), "Content-Type": "application/json"},
        method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def submit(video_url, name, lang="hi"):
    d = call("/project/create", {
        "lang": lang, "preferLength": [0], "videoUrl": video_url, "videoType": 1,
        "ext": video_url.rsplit(".", 1)[-1].split("?")[0] or "mp4",
        "ratioOfClip": 1, "getClips": 0, "projectName": name,
        "subtitleSwitch": 0, "headlineSwitch": 0, "emojiSwitch": 0,
        "highlightSwitch": 0, "autoBrollSwitch": 0, "removeSilenceSwitch": 0,
    })
    if d.get("code") != 2000:
        sys.exit(f"create failed: {d}")
    pid = d["projectId"]
    print(f"project {pid} submitted; polling…")
    for i in range(40):
        time.sleep(30)
        q = call(f"/project/query/{pid}")
        c = q.get("code")
        if c == 2000:
            vids = q.get("videos") or []
            if not vids:
                sys.exit(f"processed but no videos: {q}")
            v = vids[0]
            print(f"ready: finalVideoId {v['videoId']}")
            return v["videoId"]
        if c != 1000:
            sys.exit(f"processing failed: {q}")
        print(f"  …still processing ({(i+1)*30}s)")
    sys.exit("timed out after 20 min")


def ist_millis(s):
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    ms = int(dt.timestamp() * 1000)
    assert len(str(ms)) == 13
    return ms, dt


def publish(final_video_id, acct_key, post_text, when_ist):
    acct_id, label = ACCOUNTS[acct_key]
    lim = CHAR_LIMITS.get(acct_key)
    if lim and len(post_text) > lim:
        sys.exit(f"{label}: caption {len(post_text)} chars > {lim} limit — refusing")
    body = {"finalVideoId": final_video_id, "socialAccountId": acct_id, "post": post_text}
    ms, dt = (None, None)
    if when_ist:
        ms, dt = ist_millis(when_ist)
        body["publishTime"] = ms
    d = call("/project/publish-video", body)
    ok = d.get("code") == 2000
    when = dt.strftime("%a %d-%b %H:%M IST") if dt else "NOW"
    print(f"{'✅' if ok else '❌'} {label} @ {when} -> {d}")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-url", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--lang", default="hi")
    ap.add_argument("--final-video-id", type=int, help="skip create/poll, reuse an id")
    ap.add_argument("--leg", action="append", required=True,
                    help="acct:copy.json:field:YYYY-MM-DD HH:MM (IST) — time empty = now")
    a = ap.parse_args()

    legs = []
    for leg in a.leg:
        acct, path, field, when = leg.split(":", 3)
        assert acct in ACCOUNTS, acct
        text = json.load(open(path, encoding="utf-8"))[field]
        legs.append((acct, text, when.strip() or None))

    fid = a.final_video_id or submit(a.video_url, a.name, a.lang)
    results = [publish(fid, acct, text, when) for acct, text, when in legs]
    print(f"\nfinalVideoId {fid} — {sum(results)}/{len(results)} legs scheduled")
    sys.exit(0 if all(results) else 1)
