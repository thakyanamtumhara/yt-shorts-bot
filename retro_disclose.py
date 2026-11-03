#!/usr/bin/env python3
"""Backfill status.containsSyntheticMedia=true on already-published channel videos.

Why: every bot short is realistic Veo footage + cloned voice — YouTube's A/S
disclosure policy requires the flag, and daily_short.py only sets it on NEW
uploads (since 2026-07-06). This flags the back-catalog, and any later upload
the normal path missed.

Safe-update pattern (verified against API docs 2026-07-06):
- videos.update REPLACES the status part — any mutable field omitted is CLEARED
  (a bare update would un-schedule scheduled videos and wipe privacy). So we
  read the full status via videos.list, write it back with only
  containsSyntheticMedia added, then read it back and halt if anything else
  moved (tools/youtube_status.status_verification).
- Skips "Warehouse Live" archives and anything with liveStreamingDetails (real
  footage, not synthetic).
- videos.list never returns containsSyntheticMedia (every run Jul-Nov 2026 said
  "already flagged: 0"), so a run cannot see what is done: the old job
  re-flagged the newest 120 videos daily and never reached the oldest. The
  record is retro_disclose_state.json: "flagged" ids are done for good, "skip"
  ids were checked by a person and need no label. Oldest first; a day with
  nothing left only lists (~15 units) and writes nothing.
- Refuses a token for any channel but the BOT channel (@sale91_com).
- Quota: videos.update = 50 units from the shared 10,000/day bucket, so
  MAX_UPDATES caps a run. Halts cleanly on quotaExceeded (resets midnight PT).
- VIDEO_IDS="id1,id2" flags exactly those BOT videos, even ones already listed
  as flagged (a one-off repair); the skip list still wins.
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from tools.youtube_status import status_verification

BOT_CHANNEL = "UCHZbA84OiM9COlTQ4JcVgeQ"
STATE_FILE = Path(__file__).resolve().parent / "retro_disclose_state.json"
TOKEN_FILE = os.environ.get("TOKEN_FILE", "/tmp/yt_shorts/youtube_token.json")

# status fields that are mutable via videos.update — all must be re-sent or
# the API clears them
MUTABLE_STATUS_FIELDS = (
    "privacyStatus", "publishAt", "license", "embeddable",
    "publicStatsViewable", "selfDeclaredMadeForKids",
)


class Halt(RuntimeError):
    pass


def parse_ids(text):
    ids = [part for part in re.split(r"[\s,]+", text or "") if part]
    bad = [part for part in ids if not re.fullmatch(r"[A-Za-z0-9_-]{11}", part)]
    if bad:
        raise ValueError(f"not YouTube video ids: {bad}")
    return list(dict.fromkeys(ids))


def load_state(path=STATE_FILE):
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {}
    state["flagged"] = dict(state.get("flagged") or {})
    state["skip"] = dict(state.get("skip") or {})
    return state


def save_state(state, path=STATE_FILE):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def get_service():
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def uploads_playlist(yt):
    items = yt.channels().list(part="id,contentDetails", mine=True).execute().get("items", [])
    if len(items) != 1 or items[0].get("id") != BOT_CHANNEL:
        raise Halt("the token is not the BOT channel @sale91_com — nothing written")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def upload_ids(yt, playlist):
    ids, page_token = [], None
    while True:
        resp = yt.playlistItems().list(
            part="contentDetails", playlistId=playlist,
            maxResults=50, pageToken=page_token,
        ).execute()
        ids += [i["contentDetails"]["videoId"] for i in resp.get("items", [])]
        page_token = resp.get("nextPageToken")
        if not page_token:
            return ids


def read_videos(yt, ids):
    videos = []
    for start in range(0, len(ids), 50):
        videos += yt.videos().list(
            part="status,snippet,liveStreamingDetails", id=",".join(ids[start:start + 50])
        ).execute().get("items", [])
    return videos


def flag(yt, video, sleep=time.sleep):
    vid, status = video["id"], video.get("status", {})
    new_status = {k: status[k] for k in MUTABLE_STATUS_FIELDS if k in status}
    new_status["containsSyntheticMedia"] = True
    ack = yt.videos().update(part="status", body={"id": vid, "status": new_status}).execute()
    for attempt in range(3):
        if attempt:
            sleep(2)
        items = yt.videos().list(part="status", id=vid).execute().get("items", [])
        actual = items[0].get("status") if len(items) == 1 and items[0].get("id") == vid else None
        evidence = status_verification(vid, new_status, actual, ack)
        if evidence["verified"] or evidence["state"] != "status_mismatch":
            break
    if not evidence["verified"]:
        raise Halt(f"{vid}: {evidence['state']} after the update — HALTING, check this video in Studio")


def run(yt, state, *, max_updates=120, dry_run=False, only_ids=(), today=None, sleep=time.sleep):
    playlist = uploads_playlist(yt)
    if only_ids:
        videos = read_videos(yt, list(only_ids))
        found = {v.get("id"): v for v in videos}
        foreign = [i for i in only_ids
                   if (found.get(i) or {}).get("snippet", {}).get("channelId") != BOT_CHANNEL]
        if foreign:
            raise Halt(f"not found on the BOT channel: {foreign} — nothing written")
    else:
        ids = upload_ids(yt, playlist)
        print(f"📼 Channel uploads found: {len(ids)}")
        videos = read_videos(yt, ids)
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    c = {"flagged": 0, "done_before": 0, "api_true": 0, "skipped_real": 0,
         "skip_list": 0, "pending": 0, "errors": 0, "halted": False, "halt": None}
    todo = []
    for v in videos:
        vid, status = v["id"], v.get("status", {})
        title = v.get("snippet", {}).get("title", "?")
        if v.get("liveStreamingDetails") or "Warehouse Live" in title:
            c["skipped_real"] += 1
            print(f"  ⏭️ real footage (not flagged): {title[:60]}")
        elif vid in state["skip"]:
            c["skip_list"] += 1
            if only_ids:
                print(f"  ⏭️ {vid} is on the skip list ({state['skip'][vid]}) — not flagged")
        elif vid in state["flagged"] and not only_ids:
            c["done_before"] += 1
        elif status.get("containsSyntheticMedia") is True and not only_ids:
            c["api_true"] += 1
            if not dry_run:
                state["flagged"][vid] = f"api {today}"
        else:
            todo.append(v)
    todo.sort(key=lambda v: v.get("snippet", {}).get("publishedAt") or "")

    for n, v in enumerate(todo):
        if c["flagged"] >= max_updates:
            c["pending"] = len(todo) - n
            break
        vid, status = v["id"], v.get("status", {})
        snippet = v.get("snippet", {})
        # a scheduled video must keep privacyStatus=private together with
        # publishAt — anything inconsistent is left alone rather than risked
        if "publishAt" in status and status.get("privacyStatus") != "private":
            print(f"  ⚠️ {vid}: publishAt present but privacy={status.get('privacyStatus')} — skipped")
            c["errors"] += 1
            continue
        print(f"  {'[DRY] ' if dry_run else ''}🏷️ flagging {vid} {(snippet.get('publishedAt') or '?')[:10]} "
              f"{status.get('privacyStatus', '?')}: {snippet.get('title', '?')[:60]}")
        if not dry_run:
            try:
                flag(yt, v, sleep=sleep)
            except Halt as e:
                c["halt"] = str(e)
                break
            except Exception as e:
                if "quotaExceeded" in str(e):
                    print("  🛑 quotaExceeded — stopping; the next run carries on (state saved)")
                    c["pending"], c["halted"] = len(todo) - n, True
                    break
                print(f"  ⚠️ {vid}: {e} — continuing")
                c["errors"] += 1
                continue
            state["flagged"][vid] = f"flagged {today}"
            sleep(1)
        c["flagged"] += 1
    return c


def report(c, dry_run=False):
    print("\n──── retro-disclose summary ────")
    print(f"  flagged this run : {c['flagged']}{' (dry run — nothing written)' if dry_run else ''}")
    print(f"  flagged before   : {c['done_before']} (retro_disclose_state.json)")
    print(f"  API said flagged : {c['api_true']}")
    print(f"  real footage skip: {c['skipped_real']}")
    print(f"  skip list        : {c['skip_list']}")
    print(f"  still pending    : {c['pending']}{' (halted early)' if c['halted'] else ''}")
    print(f"  errors/skipped   : {c['errors']}")
    if c["halt"]:
        print(f"  ❌ {c['halt']}")
    elif c["pending"]:
        print("  ➡️ the next run carries on, oldest first")
    else:
        print("  ✅ back-catalog fully disclosed — later runs only list, until a new upload appears")


def main():
    dry_run = os.environ.get("DRY_RUN", "0") == "1"
    only_ids = parse_ids(os.environ.get("VIDEO_IDS", ""))
    state = load_state()
    before = json.dumps(state, sort_keys=True)
    try:
        c = run(get_service(), state, max_updates=int(os.environ.get("MAX_UPDATES") or "120"),
                dry_run=dry_run, only_ids=only_ids)
    except Halt as e:
        print(f"  ❌ {e}")
        return 1
    if not dry_run and json.dumps(state, sort_keys=True) != before:
        save_state(state)
    report(c, dry_run)
    return 1 if c["halt"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
