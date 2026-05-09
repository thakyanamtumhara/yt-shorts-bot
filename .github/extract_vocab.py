#!/usr/bin/env python3
"""Extract Hinglish vocabulary from the user's 11 source YouTube videos.

Workflow:
  1. yt-dlp each video → mp3 audio
  2. ffmpeg split into <25MB chunks (Whisper API limit)
  3. Whisper-Hindi transcribe each chunk → Devanagari output
  4. Aggregate transcripts, extract Devanagari + Latin words
  5. Compare against current _TTS_HINGLISH_DEVANAGARI map values + EXPECTED_ENGLISH
  6. Output gaps as JSON for human review/import

Outputs:
  - transcripts/transcript_<videoid>.txt    full per-video Devanagari text
  - vocab_analysis.json                     gap analysis with frequencies
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import requests

# 11 source videos used to train the user's PVC voice
VIDEO_IDS = [
    "dm-3wqKPkic", "j71qiNc-qio", "eDMWP2WgE0U", "E1B-HKoek5Y",
    "MKgpoyx3SyU", "d84ZSj-L8IQ", "8NgnQN3oJQE", "J5g_DfyxxW8",
    "YPCFTtxd_MY", "Ic16Ms2vqaY", "UuITbFomJBI",
]

OPENAI_KEY = os.environ["OPENAI_API_KEY"]
WORK = Path("/tmp/vocab")
WORK.mkdir(exist_ok=True)
ARTIFACT_DIR = Path("transcripts")
ARTIFACT_DIR.mkdir(exist_ok=True)


def load_current_map():
    """Load _TTS_HINGLISH_DEVANAGARI from daily_short.py without importing it."""
    src = open("daily_short.py").read()
    start = src.index(
        "# ╔══════════════════════════════════════════════════════════════════════╗\n"
        "# ║                   TTS PRE-PROCESSING"
    )
    end = src.index("def sarvam_tts_to_mp3")
    ns: dict = {}
    exec(src[start:end], ns)
    return ns["_TTS_HINGLISH_DEVANAGARI"]


def load_expected_english():
    src = open(".github/qa_loop.py").read()
    s = src.index("EXPECTED_ENGLISH = {")
    e = src.index("}", s) + 1
    ns: dict = {}
    exec(src[s:e], ns)
    return ns["EXPECTED_ENGLISH"]


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

    all_transcripts = []

    for vid in VIDEO_IDS:
        print(f"\n=== {vid} ===", flush=True)
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
                print(f"  ✅ downloaded {audio_path.stat().st_size // 1024} KB", flush=True)
            except Exception as e:
                print(f"  ❌ download failed: {e}", flush=True)
                continue

        # Split into 9-min chunks (well under 25MB limit at 96kbps mp3)
        chunks_dir = WORK / f"{vid}_chunks"
        chunks_dir.mkdir(exist_ok=True)
        # If already split, skip
        if not list(chunks_dir.glob("chunk_*.mp3")):
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(audio_path),
                 "-f", "segment", "-segment_time", "540",
                 "-c", "copy", str(chunks_dir / "chunk_%03d.mp3")],
                check=True, capture_output=True, timeout=120,
            )
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
                print(f"    ❌ {chunk_file.name} failed: {e}", flush=True)

        full_transcript = " ".join(transcript_parts)
        all_transcripts.append({"video_id": vid, "transcript": full_transcript})

        # Save per-video transcript
        out = ARTIFACT_DIR / f"transcript_{vid}.txt"
        out.write_text(full_transcript, encoding="utf-8")

    # Aggregate
    all_text = " ".join(t["transcript"] for t in all_transcripts)

    devanagari_words = re.findall(r"[\u0900-\u097F][\u0900-\u097F\u200d]*", all_text)
    latin_words = [w.lower() for w in re.findall(r"[a-zA-Z][a-zA-Z'-]*", all_text)]

    deva_freq = Counter(devanagari_words)
    latin_freq = Counter(latin_words)

    # Devanagari gap = word in user's speech NOT a value in my current map
    deva_gap = {w: c for w, c in deva_freq.items() if w not in mapped_devanagari}
    # Latin gap = word in user's speech NOT in EXPECTED_ENGLISH and NOT a key in my map
    latin_gap = {
        w: c for w, c in latin_freq.items()
        if w not in expected_english
        and w not in mapped_latin_keys
        and not w.isdigit()
        and len(w) > 1
    }

    analysis = {
        "total_videos": len(VIDEO_IDS),
        "videos_succeeded": len(all_transcripts),
        "total_chars_transcribed": sum(len(t["transcript"]) for t in all_transcripts),
        "devanagari_words_total": len(devanagari_words),
        "devanagari_unique": len(deva_freq),
        "devanagari_already_mapped_count": sum(1 for w in deva_freq if w in mapped_devanagari),
        "devanagari_gaps_count": len(deva_gap),
        "devanagari_TOP_GAPS": [
            {"word": w, "count": c}
            for w, c in sorted(deva_gap.items(), key=lambda x: -x[1])[:300]
        ],
        "latin_words_total": len(latin_words),
        "latin_unique": len(latin_freq),
        "latin_gaps_count": len(latin_gap),
        "latin_TOP_GAPS": [
            {"word": w, "count": c}
            for w, c in sorted(latin_gap.items(), key=lambda x: -x[1])[:300]
        ],
    }

    Path("vocab_analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2))

    print("\n" + "=" * 70)
    print(f"SUMMARY")
    print(f"  Videos processed: {analysis['videos_succeeded']}/{analysis['total_videos']}")
    print(f"  Total chars: {analysis['total_chars_transcribed']:,}")
    print(f"  Devanagari unique: {analysis['devanagari_unique']:,}")
    print(f"    Already mapped : {analysis['devanagari_already_mapped_count']:,}")
    print(f"    GAPS           : {analysis['devanagari_gaps_count']:,}")
    print(f"  Latin unique: {analysis['latin_unique']:,}")
    print(f"    GAPS           : {analysis['latin_gaps_count']:,}")
    print(f"\nTop 30 Devanagari gaps:")
    for g in analysis["devanagari_TOP_GAPS"][:30]:
        print(f"  [{g['count']}]  {g['word']}")
    print(f"\nTop 30 Latin gaps:")
    for g in analysis["latin_TOP_GAPS"][:30]:
        print(f"  [{g['count']}]  {g['word']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
