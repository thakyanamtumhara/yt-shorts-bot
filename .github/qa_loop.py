#!/usr/bin/env python3
"""Voice QA loop runner.

Reads SCRIPTS_JSON env var (a JSON array of test strings).
For each script:
  1. Loads normalize_for_tts() from daily_short.py (without importing the whole module)
  2. Generates audio via ElevenLabs (Ketu Original PVC voice)
  3. Transcribes via OpenAI Whisper with language="hi"
  4. Records: original, preprocessed, transcript, audio path

Writes consolidated qa_results.json + per-sample mp3s + transcripts.txt.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import requests

ELEVENLABS_VOICE_ID = "cejtKjfE9sHUZ1FnUYEV"
ELEVENLABS_MODEL = "eleven_multilingual_v2"

OUT_DIR = Path("/tmp/qa_out")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_normalize_for_tts():
    """Extract normalize_for_tts from daily_short.py without importing heavy deps."""
    src = Path("daily_short.py").read_text()
    start = src.index(
        "# ╔══════════════════════════════════════════════════════════════════════╗\n"
        "# ║                   TTS PRE-PROCESSING"
    )
    end = src.index("def sarvam_tts_to_mp3")
    ns: dict = {}
    exec(src[start:end], ns)
    return ns["normalize_for_tts"]


def elevenlabs_tts(text: str, out_path: Path, api_key: str) -> None:
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": text,
            "model_id": ELEVENLABS_MODEL,
            "voice_settings": {
                "stability": 0.50,
                "similarity_boost": 0.75,
                "style": 0.00,
                "use_speaker_boost": True,
            },
        },
        timeout=120,
    )
    r.raise_for_status()
    out_path.write_bytes(r.content)


def whisper_transcribe(audio_path: Path, api_key: str, language: str = "hi") -> str:
    with audio_path.open("rb") as f:
        r = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (audio_path.name, f, "audio/mpeg")},
            data={"model": "whisper-1", "language": language},
            timeout=120,
        )
    r.raise_for_status()
    return r.json().get("text", "").strip()


# Latin words considered "expected English" in our domain — anything else
# in Latin script in the Whisper-Hindi transcript = mispronunciation candidate.
EXPECTED_ENGLISH = {
    # Core nouns / brands
    "customer", "customers", "product", "products", "business", "marketing",
    "brand", "brands", "factory", "company", "companies", "supplier", "suppliers",
    # Manufacturing / technical
    "dtg", "dtf", "gsm", "moq", "b2b", "ecommerce", "amazon", "flipkart",
    "cotton", "polyester", "viscose", "lycra", "spandex", "fabric", "textile",
    "polo", "tshirt", "t-shirt", "tee", "shirt", "hoodie", "joggers", "shorts",
    "jeans", "bio-wash", "biowash", "french", "terry", "fleece", "rib",
    "yarn", "loom", "knit", "knitted", "stitching", "stitched",
    "size", "sizes", "color", "colors", "colour", "colours", "design", "designs",
    "print", "printing", "printer", "printers", "machine", "machines",
    "screen", "embroidery", "logo", "label",
    # Commerce
    "order", "orders", "piece", "pieces", "box", "carton",
    "rate", "rates", "price", "prices", "discount", "discounts",
    "wholesale", "retail", "premium", "plain", "stock", "ready",
    "dispatch", "delivery", "shipping", "courier", "transport",
    "warehouse", "inventory", "supply", "demand",
    "quality", "check", "test", "certified", "iso",
    "credit", "debit", "cash", "online", "offline", "upi", "neft", "rtgs",
    "tax", "gst", "invoice", "bill", "receipt", "challan",
    "margin", "profit", "loss", "cost", "revenue", "investment", "roi",
    "patience", "patient", "passion", "dedication",
    "result", "results", "growth", "scale", "scaling",
    "feedback", "review", "reviews", "rating", "ratings",
    "video", "videos", "channel", "subscribe", "like", "share", "comment",
    "youtube", "instagram", "facebook", "whatsapp", "linkedin",
    "phone", "mobile", "number", "call", "message", "email",
    "website", "site", "link", "click", "visit",
    "team", "staff", "employee", "manager", "owner",
    "follow", "following", "follower", "followers",
    "start", "started", "starting", "starts", "stop",
    "return", "returns", "exchange", "refund",
    "reputation", "trust", "trusted",
    # Common loanwords used in Hinglish
    "okay", "ok", "fine", "good", "bad", "best", "better",
    "yes", "no", "well", "sure",
    "minute", "second", "hour", "minutes", "seconds", "hours",
    # Cycle 1 additions: domain-specific English that's not Hindi
    "ink", "directly", "natural", "feel", "transfer", "paper", "color",
    "colors", "vibrant", "durability", "powder", "film", "meter", "cloth",
    "roll", "resolution", "extra", "average", "amount", "total", "summary",
    "round", "neck", "sleeve", "v-neck",
    "touch", "wash", "shrink", "stitching", "sample", "confirm",
    "fix", "fixed", "white", "black", "blurry",
    "advance", "payment", "competition", "turnover",
    "rule", "off", "bulk", "difference", "accept",
    "round", "half", "dozen", "lot", "mix", "fashion",
    "delivery", "team", "serious", "full", "delivery",
    "service", "support", "demo", "trial", "free", "paid",
    "bag", "tag", "label", "barcode", "qr",
    # Cycle 2 additions
    "packaging", "around", "batch", "booking", "breakdown", "case", "chain",
    "charge", "compare", "cold", "cutting", "defect", "diligence", "double",
    "due", "easy", "expenses", "fast", "figure", "goods", "google", "hybrid",
    "immediately", "important", "industry", "initially", "ironing",
    "kg", "kyc", "knitting", "last", "layers", "line", "local", "lock",
    "minus", "model", "net", "normal", "options", "parameters", "photos",
    "plus", "police", "randomly", "recovery", "refrigerated", "reject",
    "repeat", "road", "room", "sales", "samples", "search", "selling",
    "shifts", "shortlist", "situation", "sometimes", "sourcing", "south",
    "north", "east", "west", "central", "standard", "table", "top",
    "variation", "wait", "year", "month", "week", "day", "today", "tomorrow",
    "yesterday", "trust", "name", "value", "visit", "p", "l",
    # Place names
    "india", "china", "pakistan", "bangladesh", "vietnam",
    "mumbai", "delhi", "bangalore", "kolkata", "chennai", "hyderabad",
    "ahmedabad", "pune", "jaipur", "lucknow", "kanpur", "nagpur",
    "indore", "bhopal", "patna", "surat", "agra", "varanasi",
    "ludhiana", "tirupur", "tirpur", "kochi", "noida", "gurgaon", "gurugram",
    "october", "november", "december", "january", "february", "march",
    "april", "may", "june", "july", "august", "september",
    "diwali", "holi", "eid", "christmas", "navratri", "rakhi",
    "client", "shrinkage",
    # Hinglish loanwords commonly kept English-spoken
    "per", "negotiation", "percent", "production", "pan", "production",
    "hub", "spoke", "stock", "ready", "lead", "time", "point",
    # Single letters / particles Whisper may emit
    "a", "an", "the", "i", "is", "it", "of", "to", "in", "on", "at", "by", "for",
    "and", "or", "but", "if", "so", "as", "be", "do", "go",
    "you", "we", "he", "she", "they", "me", "us",
    "this", "that", "these", "those",
}


def latin_words(text: str) -> list[str]:
    """Extract Latin-script words (letters only) from a string, lowercased."""
    return re.findall(r"[a-zA-Z][a-zA-Z\-']*", text)


def main() -> int:
    eleven_key = os.environ["ELEVENLABS_API_KEY"]
    openai_key = os.environ["OPENAI_API_KEY"]
    scripts_json = os.environ["SCRIPTS_JSON"]
    scripts: list[str] = json.loads(scripts_json)

    normalize = load_normalize_for_tts()

    results = []
    transcripts_log = []
    candidates: dict[str, int] = {}  # latin_word → count of samples it appeared in

    for i, script in enumerate(scripts):
        idx = i + 1
        print(f"\n=== Sample {idx}/{len(scripts)} ===")
        print(f"Original   : {script}")
        preprocessed = normalize(script)
        print(f"Processed  : {preprocessed}")

        audio_path = OUT_DIR / f"qa_audio_{idx:02d}.mp3"
        try:
            elevenlabs_tts(preprocessed, audio_path, eleven_key)
            print(f"Audio      : {audio_path} ({audio_path.stat().st_size} bytes)")
        except Exception as e:
            print(f"!! ElevenLabs failed: {e}")
            results.append({
                "idx": idx, "original": script, "preprocessed": preprocessed,
                "error": f"elevenlabs: {e}",
            })
            continue

        try:
            transcript = whisper_transcribe(audio_path, openai_key)
            print(f"Transcript : {transcript}")
        except Exception as e:
            print(f"!! Whisper failed: {e}")
            results.append({
                "idx": idx, "original": script, "preprocessed": preprocessed,
                "audio": str(audio_path), "error": f"whisper: {e}",
            })
            continue

        # Find Latin words in transcript that are NOT expected English
        sample_candidates = []
        seen_in_sample: set[str] = set()
        for w in latin_words(transcript):
            wl = w.lower().strip("'-")
            if not wl or wl in EXPECTED_ENGLISH:
                continue
            if wl.isdigit():
                continue
            if wl in seen_in_sample:
                continue
            seen_in_sample.add(wl)
            sample_candidates.append(wl)
            candidates[wl] = candidates.get(wl, 0) + 1

        results.append({
            "idx": idx,
            "original": script,
            "preprocessed": preprocessed,
            "audio": str(audio_path),
            "transcript": transcript,
            "candidates": sample_candidates,
        })
        transcripts_log.append(f"--- Sample {idx} ---\nORIG: {script}\nPRE : {preprocessed}\nTRX : {transcript}\nCAND: {', '.join(sample_candidates)}\n")

    # High-confidence candidates: appeared in ≥2 samples
    high_conf = sorted(
        [(w, c) for w, c in candidates.items() if c >= 2],
        key=lambda kv: -kv[1],
    )
    low_conf = sorted(
        [(w, c) for w, c in candidates.items() if c == 1],
        key=lambda kv: kv[0],
    )

    summary = {
        "n_samples": len(scripts),
        "n_succeeded": sum(1 for r in results if "transcript" in r),
        "high_confidence_candidates": [{"word": w, "count": c} for w, c in high_conf],
        "low_confidence_candidates": [{"word": w, "count": c} for w, c in low_conf],
        "results": results,
    }
    (Path("/tmp/qa_results.json")).write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (Path("/tmp/qa_transcripts.txt")).write_text("\n".join(transcripts_log))

    print("\n" + "=" * 70)
    print(f"SUMMARY  ::  {summary['n_succeeded']}/{summary['n_samples']} succeeded")
    print(f"High-confidence (>=2 samples): {len(high_conf)} words")
    for w, c in high_conf:
        print(f"  [{c}] {w}")
    print(f"Low-confidence (1 sample only): {len(low_conf)} words")
    print("=" * 70)
    # Copy artifact-friendly files to /tmp root for upload-artifact step
    return 0


if __name__ == "__main__":
    sys.exit(main())
