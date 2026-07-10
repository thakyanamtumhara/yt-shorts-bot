#!/usr/bin/env python3
"""Backfill voice_corpus/ with REAL speech from Ketu's main channel back-catalog
so the self-learning voice models (build_voice_models) have rich data now
instead of accumulating one video per Sunday.

Per video, cheapest source first (see get_video_speech_text):
  1. YouTube auto-captions (youtube-transcript-api, no auth, Devanagari)
  2. OAuth captions API
  3. yt-dlp + local Whisper (slow — skip with SKIP_WHISPER=1)

Run (Mac or manual workflow_dispatch — NEVER the daily cron):
  MAX_VIDEOS=40 python3 backfill_voice_corpus.py
  SKIP_WHISPER=1 MAX_VIDEOS=40 python3 backfill_voice_corpus.py   # captions only, fast

Video listing needs no secrets: uses the YouTube Data API when
YOUTUBE_API_KEY_1 + CHANNEL_ID_2 are set (CI), else yt-dlp flat-playlist on
the public channel page (local). Idempotent — already-fetched videos skip.
"""
import os
import subprocess
import sys
import time

import requests

import daily_short
from daily_short import (
    MAIN_CHANNEL_VIDEOS_URL,
    SOURCE_CHANNEL_API_KEY,
    SOURCE_CHANNEL_ID,
    VOICE_CORPUS_DIR,
    build_voice_models,
    get_video_speech_text,
)

# `or` fallbacks: workflow_dispatch inputs arrive as empty-but-set env vars
# when the operator clears the pre-filled box — int("") would crash.
MAX_VIDEOS = int(os.environ.get("MAX_VIDEOS") or "40")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL") or "small"
SKIP_WHISPER = (os.environ.get("SKIP_WHISPER") or "").strip() in ("1", "true", "yes")


def list_videos_api(n):
    """Uploads playlist via YouTube Data API (1 quota unit per 50 videos —
    search.list would cost 100 per call and can miss videos)."""
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "contentDetails", "id": SOURCE_CHANNEL_ID,
                "key": SOURCE_CHANNEL_API_KEY},
        timeout=30,
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        raise RuntimeError(f"No channel found for id={SOURCE_CHANNEL_ID}")
    uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    videos, page_token = [], None
    while len(videos) < n:
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/playlistItems",
            params={"part": "snippet,contentDetails", "playlistId": uploads_id,
                    "maxResults": min(50, n - len(videos)),
                    "pageToken": page_token, "key": SOURCE_CHANNEL_API_KEY},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        for item in data.get("items", []):
            videos.append({"video_id": item["contentDetails"]["videoId"],
                           "title": item["snippet"]["title"]})
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return videos[:n]


def list_videos_yt_dlp(n):
    """Keyless fallback: flat-playlist over the public /videos tab (long-form
    only — Shorts live on a different tab, which is what we want)."""
    r = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--print", "%(id)s\t%(title)s",
         "--playlist-end", str(n), MAIN_CHANNEL_VIDEOS_URL],
        capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        raise RuntimeError(f"yt-dlp listing failed: {r.stderr[:300]}")
    videos = []
    for line in r.stdout.splitlines():
        if "\t" in line:
            vid, title = line.split("\t", 1)
            videos.append({"video_id": vid.strip(), "title": title.strip()})
    return videos[:n]


def existing_corpus_video_ids():
    """Video ids already captured in ANY corpus file (weekly Sunday files
    embed the watch URL in their '#' header) — prevents the same video being
    counted twice by backfill after a Sunday recap already saved it."""
    import re
    ids = set()
    for fn in os.listdir(VOICE_CORPUS_DIR):
        if not fn.endswith(".txt"):
            continue
        try:
            with open(os.path.join(VOICE_CORPUS_DIR, fn)) as f:
                head = f.read(400)
        except Exception:
            continue
        m = re.search(r"watch\?v=([A-Za-z0-9_-]{6,})", head)
        if m:
            ids.add(m.group(1))
    return ids


def main():
    os.makedirs(VOICE_CORPUS_DIR, exist_ok=True)
    if SOURCE_CHANNEL_API_KEY and SOURCE_CHANNEL_ID:
        videos = list_videos_api(MAX_VIDEOS)
        print(f"Listed {len(videos)} videos via YouTube Data API")
    else:
        videos = list_videos_yt_dlp(MAX_VIDEOS)
        print(f"Listed {len(videos)} videos via yt-dlp (no API key in env)")

    already = existing_corpus_video_ids()
    done = skipped = failed = 0
    for i, v in enumerate(videos, 1):
        vid = v["video_id"]
        out = os.path.join(VOICE_CORPUS_DIR, f"backfill-{vid}.txt")
        if os.path.exists(out) or vid in already:
            skipped += 1
            continue
        if daily_short._CAPTIONS_IP_BLOCKED and SKIP_WHISPER:
            print(f"🛑 YouTube rate-limited caption fetches from this IP — "
                  f"stopping at [{i}/{len(videos)}]. Re-run later; already-"
                  f"fetched videos are skipped automatically.")
            break
        time.sleep(2)  # pace requests — rapid bursts trip YouTube's IP block
        text, source = get_video_speech_text(
            vid, allow_whisper=not SKIP_WHISPER,
            whisper_model=WHISPER_MODEL, max_seconds=3600,
        )
        if not text:
            failed += 1
            print(f"[{i}/{len(videos)}] {vid}  ❌ no speech source — {v['title'][:60]}")
            continue
        with open(out, "w") as f:
            f.write(f"# {v['title']}\n# https://www.youtube.com/watch?v={vid}\n")
            f.write(f"# source: {source}\n\n{text}")
        done += 1
        print(f"[{i}/{len(videos)}] {vid}  ✅ {len(text):>6} chars via {source} — {v['title'][:60]}")

    print(f"\nBackfill: {done} new, {skipped} already present, {failed} failed.")
    print("Building voice models…")
    build_voice_models()
    return 0


if __name__ == "__main__":
    sys.exit(main())
