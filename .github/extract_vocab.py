#!/usr/bin/env python3
"""Extract Hinglish vocabulary from ALL of the user's source YouTube videos.

Uses YouTube Data API to enumerate every video on the user's main channel
(CHANNEL_ID_2 = @bulkplaintshirt_com), then downloads and transcribes each
via Whisper-Hindi to extract their actual speaking vocabulary.

Workflow:
  1. List all videos from source channel via YouTube Data API uploads playlist
  2. Cap at MAX_VIDEOS most recent (default 50) to keep cost+time bounded
  3. yt-dlp each → mp3 audio (skip if already downloaded)
  4. ffmpeg split into <25MB chunks (Whisper API limit)
  5. Whisper-Hindi transcribe each chunk → Devanagari output
  6. Aggregate transcripts, extract Devanagari + Latin words
  7. Compare against current _TTS_HINGLISH_DEVANAGARI map values + EXPECTED_ENGLISH
  8. Output gaps as JSON for human review/import

Env vars expected:
  OPENAI_API_KEY     for Whisper API
  YOUTUBE_API_KEY_1  for YouTube Data API (read)
  CHANNEL_ID_2       source channel ID

Outputs:
  - transcripts/transcript_<videoid>.txt    full per-video Devanagari text
  - vocab_analysis.json                     gap analysis with frequencies
  - videos.json                             list of videos processed
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path

import requests

OPENAI_KEY = os.environ["OPENAI_API_KEY"]
YT_API_KEY = os.environ["YOUTUBE_API_KEY_1"]
CHANNEL_ID = os.environ["CHANNEL_ID_2"]
MAX_VIDEOS = int(os.environ.get("MAX_VIDEOS", "50"))

WORK = Path("/tmp/vocab")
WORK.mkdir(exist_ok=True)
ARTIFACT_DIR = Path("transcripts")
ARTIFACT_DIR.mkdir(exist_ok=True)


def load_current_map() -> dict:
    src = open("daily_short.py").read()
    s = src.index(
        "# ╔══════════════════════════════════════════════════════════════════════╗\n"
        "# ║                   TTS PRE-PROCESSING"
    )
    e = src.index("def sarvam_tts_to_mp3")
    ns: dict = {}
    exec(src[s:e], ns)
    return ns["_TTS_HINGLISH_DEVANAGARI"]


def load_expected_english() -> set:
    src = open(".github/qa_loop.py").read()
    s = src.index("EXPECTED_ENGLISH = {")
    e = src.index("}", s) + 1
    ns: dict = {}
    exec(src[s:e], ns)
    return ns["EXPECTED_ENGLISH"]


def get_uploads_playlist_id(channel_id: str) -> str:
    """A channel's 'uploads' playlist contains every video, sorted newest-first."""
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "contentDetails", "id": channel_id, "key": YT_API_KEY},
        timeout=30,
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        raise RuntimeError(f"No channel found for id={channel_id}")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def list_videos(playlist_id: str, max_videos: int) -> list:
    """Fetch up to max_videos most recent uploads. Returns list of dicts with
    video_id, title, duration_seconds (approx via contentDetails)."""
    videos = []
    page_token = None
    while len(videos) < max_videos:
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/playlistItems",
            params={
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": min(50, max_videos - len(videos)),
                "pageToken": page_token,
                "key": YT_API_KEY,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        for item in data.get("items", []):
            videos.append({
                "video_id": item["contentDetails"]["videoId"],
                "title": item["snippet"]["title"],
                "published_at": item["snippet"]["publishedAt"],
            })
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return videos[:max_videos]


def whisper_chunk(mp3_path: Path) -> str:
    with open(mp3_path, "rb") as f:
        r = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_KEY}"},
            files={"file": (mp3_path.name, f, "audio/mpeg")},
            data={"model": "whisper-1", "language": "hi"},
            timeout=300,
        )
    r.raise_for_status()
    return r.json().get("text", "")


def main() -> int:
    current_map = load_current_map()
    expected_english = load_expected_english()
    mapped_devanagari = set(current_map.values())
    mapped_latin_keys = set(current_map.keys())

    print(f"Current map: {len(current_map)} entries", flush=True)
    print(f"EXPECTED_ENGLISH: {len(expected_english)} entries", flush=True)

    print(f"\nFetching uploads playlist for channel {CHANNEL_ID}...", flush=True)
    uploads_id = get_uploads_playlist_id(CHANNEL_ID)
    videos = list_videos(uploads_id, MAX_VIDEOS)
    print(f"Found {len(videos)} videos to process (cap={MAX_VIDEOS})", flush=True)

    Path("videos.json").write_text(json.dumps(videos, ensure_ascii=False, indent=2))

    all_transcripts = []

    for i, v in enumerate(videos, 1):
        vid = v["video_id"]
        print(f"\n=== [{i}/{len(videos)}] {vid} — {v['title'][:60]} ===", flush=True)
        audio_path = WORK / f"{vid}.mp3"

        # Download audio
        if not audio_path.exists():
            try:
                subprocess.run(
                    ["yt-dlp", "-x", "--audio-format", "mp3",
                     "-o", str(audio_path),
                     f"https://youtube.com/watch?v={vid}"],
                    check=True, capture_output=True, timeout=600,
                )
                size_mb = audio_path.stat().st_size / 1024 / 1024
                print(f"  ✅ downloaded {size_mb:.1f} MB", flush=True)
            except Exception as e:
                print(f"  ❌ download failed: {str(e)[:200]}", flush=True)
                continue

        # Split into 9-min chunks (well under 25MB)
        chunks_dir = WORK / f"{vid}_chunks"
        chunks_dir.mkdir(exist_ok=True)
        if not list(chunks_dir.glob("chunk_*.mp3")):
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(audio_path),
                     "-f", "segment", "-segment_time", "540",
                     "-c", "copy", str(chunks_dir / "chunk_%03d.mp3")],
                    check=True, capture_output=True, timeout=120,
                )
            except Exception as e:
                print(f"  ❌ chunk failed: {e}", flush=True)
                continue
        chunk_files = sorted(chunks_dir.glob("chunk_*.mp3"))
        print(f"  split into {len(chunk_files)} chunks", flush=True)

        # Transcribe each chunk
        transcript_parts = []
        for chunk_file in chunk_files:
            try:
                text = whisper_chunk(chunk_file)
                transcript_parts.append(text)
                print(f"    {chunk_file.name}: {len(text)} chars", flush=True)
            except Exception as e:
                print(f"    ❌ {chunk_file.name}: {str(e)[:120]}", flush=True)

        full_transcript = " ".join(transcript_parts)
        if full_transcript.strip():
            all_transcripts.append({
                "video_id": vid,
                "title": v["title"],
                "transcript": full_transcript,
            })
            (ARTIFACT_DIR / f"transcript_{vid}.txt").write_text(
                full_transcript, encoding="utf-8"
            )

        # Cleanup audio to save runner disk
        try:
            audio_path.unlink(missing_ok=True)
            for cf in chunk_files:
                cf.unlink(missing_ok=True)
        except Exception:
            pass

    # ── Aggregate ──
    all_text = " ".join(t["transcript"] for t in all_transcripts)
    devanagari_words = re.findall(r"[\u0900-\u097F][\u0900-\u097F\u200d]*", all_text)
    latin_words = [w.lower() for w in re.findall(r"[a-zA-Z][a-zA-Z'-]*", all_text)]

    deva_freq = Counter(devanagari_words)
    latin_freq = Counter(latin_words)

    deva_gap = {w: c for w, c in deva_freq.items() if w not in mapped_devanagari}
    latin_gap = {
        w: c for w, c in latin_freq.items()
        if w not in expected_english
        and w not in mapped_latin_keys
        and not w.isdigit()
        and len(w) > 1
    }

    analysis = {
        "channel_id": CHANNEL_ID,
        "videos_attempted": len(videos),
        "videos_succeeded": len(all_transcripts),
        "total_chars_transcribed": sum(len(t["transcript"]) for t in all_transcripts),
        "devanagari_words_total": len(devanagari_words),
        "devanagari_unique": len(deva_freq),
        "devanagari_already_mapped_count": sum(1 for w in deva_freq if w in mapped_devanagari),
        "devanagari_gaps_count": len(deva_gap),
        "devanagari_TOP_GAPS": [
            {"word": w, "count": c}
            for w, c in sorted(deva_gap.items(), key=lambda x: -x[1])[:500]
        ],
        "latin_words_total": len(latin_words),
        "latin_unique": len(latin_freq),
        "latin_gaps_count": len(latin_gap),
        "latin_TOP_GAPS": [
            {"word": w, "count": c}
            for w, c in sorted(latin_gap.items(), key=lambda x: -x[1])[:500]
        ],
    }

    Path("vocab_analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2)
    )

    print("\n" + "=" * 70)
    print("SUMMARY")
    print(f"  Videos: {analysis['videos_succeeded']}/{analysis['videos_attempted']}")
    print(f"  Total chars: {analysis['total_chars_transcribed']:,}")
    print(f"  Devanagari unique: {analysis['devanagari_unique']:,}")
    print(f"    Already mapped : {analysis['devanagari_already_mapped_count']:,}")
    print(f"    GAPS           : {analysis['devanagari_gaps_count']:,}")
    print(f"  Latin unique: {analysis['latin_unique']:,}")
    print(f"    GAPS           : {analysis['latin_gaps_count']:,}")
    print(f"\nTop 50 Devanagari gaps:")
    for g in analysis["devanagari_TOP_GAPS"][:50]:
        print(f"  [{g['count']}]  {g['word']}")
    print(f"\nTop 50 Latin gaps:")
    for g in analysis["latin_TOP_GAPS"][:50]:
        print(f"  [{g['count']}]  {g['word']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
