#!/usr/bin/env python3
"""Voice QA loop with full script generation.

Like qa_loop.py but for each input topic, FIRST calls Claude to generate
a full ~45-55 second Hinglish script (matching production style), THEN
runs the normal preprocess + ElevenLabs + Whisper diff.

Outputs the same qa_results.json format plus the generated_scripts.json
so you can see what Claude produced.
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


SCRIPT_GEN_PROMPT = """You are a real Indian textile manufacturer (Sale91.com, B2B plain t-shirts).
Write a 45-55 second YouTube Short voiceover script in HINGLISH (mixed Hindi-English,
written in Latin script — NOT Devanagari) on this topic:

TOPIC: {topic}

STYLE — match this EXACTLY:
- 8-12 sentences, conversational, like talking to a friend
- Start with a STORY hook ("Ek customer aaya tha, bola..." or "Maine ek baar..." or "Pehle main bhi...")
- Use real numbers, ₹ amounts, GSM values, percentages
- Mix English business nouns (customer, order, GSM, DTF, MOQ, fabric, quality, batch) with Hindi verbs
  and connectors (samjho, bola, kiya, hua, hai, toh, lekin, par, varna, accha)
- End with a casual conclusion ("simple hai, bas...", "yehi galti mat karna", "toh wahi hota hai")

Write ONLY the spoken script (no labels, no markdown). Don't include "[Script]" or "Script:".
Just the raw words to be spoken.
"""


def load_normalize_for_tts():
    src = Path("daily_short.py").read_text()
    start = src.index(
        "# ╔══════════════════════════════════════════════════════════════════════╗\n"
        "# ║                   TTS PRE-PROCESSING"
    )
    end = src.index("def sarvam_tts_to_mp3")
    ns: dict = {}
    exec(src[start:end], ns)
    return ns["normalize_for_tts"]


def claude_generate_script(topic: str, api_key: str) -> str:
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-5",
            "max_tokens": 600,
            "messages": [
                {"role": "user", "content": SCRIPT_GEN_PROMPT.format(topic=topic)},
            ],
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()


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


# Re-use the SAME EXPECTED_ENGLISH set as qa_loop.py — read it dynamically.
_qa_loop_src = (Path(__file__).parent / "qa_loop.py").read_text()
_e_start = _qa_loop_src.index("EXPECTED_ENGLISH = {")
_e_end = _qa_loop_src.index("}", _e_start) + 1
_ns: dict = {}
exec(_qa_loop_src[_e_start:_e_end], _ns)
EXPECTED_ENGLISH = _ns["EXPECTED_ENGLISH"]


def latin_words(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z][a-zA-Z\-']*", text)


def main() -> int:
    eleven_key = os.environ["ELEVENLABS_API_KEY"]
    openai_key = os.environ["OPENAI_API_KEY"]
    anthropic_key = os.environ["ANTHROPIC_API_KEY"]
    topics_json = os.environ["TOPICS_JSON"]
    topics: list[str] = json.loads(topics_json)

    normalize = load_normalize_for_tts()

    results = []
    transcripts_log = []
    generated_scripts: list[dict] = []
    candidates: dict[str, int] = {}
    preprocessed_candidates: dict[str, int] = {}

    for i, topic in enumerate(topics):
        idx = i + 1
        print(f"\n=== Sample {idx}/{len(topics)} ===")
        print(f"Topic      : {topic}")
        try:
            script = claude_generate_script(topic, anthropic_key)
        except Exception as e:
            print(f"!! Claude failed: {e}")
            results.append({"idx": idx, "topic": topic, "error": f"claude: {e}"})
            continue
        print(f"Script     : {script[:200]}{'...' if len(script) > 200 else ''}")
        generated_scripts.append({"idx": idx, "topic": topic, "script": script})

        preprocessed = normalize(script)
        print(f"Processed  : {preprocessed[:200]}{'...' if len(preprocessed) > 200 else ''}")

        audio_path = OUT_DIR / f"qa_audio_{idx:02d}.mp3"
        try:
            elevenlabs_tts(preprocessed, audio_path, eleven_key)
        except Exception as e:
            print(f"!! ElevenLabs failed: {e}")
            results.append({"idx": idx, "topic": topic, "script": script, "error": f"elevenlabs: {e}"})
            continue

        try:
            transcript = whisper_transcribe(audio_path, openai_key)
        except Exception as e:
            print(f"!! Whisper failed: {e}")
            results.append({"idx": idx, "topic": topic, "script": script, "audio": str(audio_path), "error": f"whisper: {e}"})
            continue
        print(f"Transcript : {transcript[:200]}{'...' if len(transcript) > 200 else ''}")

        # Latin words in transcript (Whisper-Hindi rendering English)
        sample_trans_cands = []
        for w in latin_words(transcript):
            wl = w.lower().strip("'-")
            if not wl or wl in EXPECTED_ENGLISH or wl.isdigit() or len(wl) <= 1:
                continue
            sample_trans_cands.append(wl)
            candidates[wl] = candidates.get(wl, 0) + 1

        # Latin words in PREPROCESSED text (real bug candidates — Hindi words still in Latin)
        sample_pre_cands = []
        seen: set[str] = set()
        for w in latin_words(preprocessed):
            wl = w.lower().strip("'-")
            if not wl or wl in EXPECTED_ENGLISH or wl.isdigit() or len(wl) <= 1:
                continue
            if wl in seen: continue
            seen.add(wl)
            sample_pre_cands.append(wl)
            preprocessed_candidates[wl] = preprocessed_candidates.get(wl, 0) + 1

        results.append({
            "idx": idx,
            "topic": topic,
            "script": script,
            "preprocessed": preprocessed,
            "audio": str(audio_path),
            "transcript": transcript,
            "transcript_candidates": sample_trans_cands,
            "preprocessed_candidates": sample_pre_cands,
        })
        transcripts_log.append(
            f"--- Sample {idx} ---\n"
            f"TOPIC : {topic}\n"
            f"SCRIPT: {script}\n\n"
            f"PRE   : {preprocessed}\n\n"
            f"TRX   : {transcript}\n\n"
            f"PRE-CAND: {', '.join(sample_pre_cands)}\n"
            f"TRX-CAND: {', '.join(sample_trans_cands)}\n"
        )

    pre_high = sorted([(w, c) for w, c in preprocessed_candidates.items() if c >= 2], key=lambda kv: -kv[1])
    pre_low = sorted([(w, c) for w, c in preprocessed_candidates.items() if c == 1], key=lambda kv: kv[0])
    trx_high = sorted([(w, c) for w, c in candidates.items() if c >= 2], key=lambda kv: -kv[1])

    summary = {
        "n_topics": len(topics),
        "n_succeeded": sum(1 for r in results if "transcript" in r),
        "preprocessed_high_confidence": [{"word": w, "count": c} for w, c in pre_high],
        "preprocessed_low_confidence": [{"word": w, "count": c} for w, c in pre_low],
        "transcript_high_confidence": [{"word": w, "count": c} for w, c in trx_high],
        "results": results,
    }
    Path("/tmp/qa_results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    Path("/tmp/qa_transcripts.txt").write_text("\n".join(transcripts_log))
    Path("/tmp/generated_scripts.json").write_text(json.dumps(generated_scripts, ensure_ascii=False, indent=2))

    print("\n" + "=" * 70)
    print(f"SUMMARY  ::  {summary['n_succeeded']}/{summary['n_topics']} succeeded")
    print(f"PREPROCESSED Latin (high-conf, >=2): {len(pre_high)}")
    for w, c in pre_high:
        print(f"  [{c}]  {w}")
    print(f"PREPROCESSED Latin (low-conf, 1 sample): {len(pre_low)}")
    print(f"TRANSCRIPT Latin (high-conf): {len(trx_high)}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
