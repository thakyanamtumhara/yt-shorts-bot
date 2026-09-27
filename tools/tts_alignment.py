"""Caption timing from the voice model's own character timestamps.

ElevenLabs /with-timestamps returns start/end seconds for every character of
the exact text it spoke. Sentence and word timings are read from those
measurements, so captions follow the real audio instead of Whisper's guessed
segment breaks (25-Sep: one caption froze for 28s; 26-Sep: 8 sentences vs 12
Whisper segments left only estimated timing, so the final gate blocked it).
"""

import base64
import hashlib
import json
import math
import re
import time

UNIT = re.compile(r"[ऀ-ॿ]+|[A-Za-z]+|\d+")
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
MAX_GROUP = 3
MAX_LEAD_SECONDS = 1.0
MAX_TAIL_SECONDS = 1.5
OVERRUN_SECONDS = 0.15
LAST_CAPTION_HOLD = 0.5
RETRY_STATUSES = (429, 500, 502, 503, 504)


def _error_code(response):
    try:
        detail = response.json().get("detail")
    except Exception:
        return None
    status = detail.get("status") if isinstance(detail, dict) else None
    return status if isinstance(status, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", status) else None


def speech_with_timestamps(key, voice_id, model_id, text, voice_settings, post=None, attempts=2, sleep=time.sleep):
    """Same voice, model and settings as the plain request, plus per-character timing."""
    if post is None:
        import requests
        post = requests.post
    for attempt in range(1, attempts + 1):
        try:
            response = post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
                            params={"output_format": "mp3_44100_128"}, headers={"xi-api-key": key},
                            json={"text": text, "model_id": model_id, "voice_settings": voice_settings},
                            timeout=240)
        except (ConnectionError, TimeoutError, OSError):
            if attempt == attempts:
                raise
            sleep(5 * attempt)
            continue
        if response.status_code in RETRY_STATUSES and attempt < attempts:
            sleep(5 * attempt)
            continue
        if response.status_code != 200:
            raise RuntimeError(f"timestamped voice HTTP {response.status_code} ({_error_code(response)})")
        data = response.json()
        audio = base64.b64decode(data.get("audio_base64") or "", validate=True)
        if len(audio) < 1000 or not (audio.startswith(b"ID3") or (audio[0] == 255 and audio[1] & 224 == 224)):
            raise RuntimeError("timestamped voice response has no MP3 audio")
        return audio, data.get("alignment"), response.headers.get("request-id")
    raise RuntimeError("timestamped voice request was not attempted")


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def alignment_digest(alignment):
    return hashlib.sha256(json.dumps(alignment, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def alignment_evidence(text, alignment, audio_seconds, scale=1.0):
    """Exact only when every character of the spoken text has ordered times that fit the final audio."""
    result = {"exact": False, "reason": "missing_alignment", "characters": len(text or ""), "scale": scale}
    if not isinstance(alignment, dict) or not text:
        return result
    starts = alignment.get("character_start_times_seconds")
    ends = alignment.get("character_end_times_seconds")
    if alignment.get("characters") != list(text):
        return {**result, "reason": "characters_differ"}
    if not (isinstance(starts, list) and isinstance(ends, list) and len(starts) == len(ends) == len(text)):
        return {**result, "reason": "incomplete_timestamps"}
    if not _number(audio_seconds) or audio_seconds <= 0 or not _number(scale) or scale <= 0:
        return {**result, "reason": "invalid_audio_duration"}
    previous = 0.0
    for start, end in zip(starts, ends):
        if not _number(start) or not _number(end) or not 0 <= start <= end or start < previous:
            return {**result, "reason": "unordered_timestamps"}
        previous = start
    first, last = starts[0] * scale, max(ends) * scale
    result.update(first_start=round(first, 3), last_end=round(last, 3), audio_seconds=round(audio_seconds, 3),
                  alignment_sha256=alignment_digest(alignment))
    if first > MAX_LEAD_SECONDS:
        return {**result, "reason": "late_first_character"}
    if last > audio_seconds + OVERRUN_SECONDS:
        return {**result, "reason": "timestamps_exceed_audio"}
    if audio_seconds - last > MAX_TAIL_SECONDS:
        return {**result, "reason": "unexplained_audio_after_text"}
    return {**result, "exact": True, "reason": "exact_character_timestamps"}


def _span_seconds(alignment, first_char, last_char, scale):
    starts = alignment["character_start_times_seconds"]
    ends = alignment["character_end_times_seconds"]
    return starts[first_char] * scale, max(ends[first_char:last_char + 1]) * scale


def sentence_spans(text, alignment, scale=1.0):
    """Measured start/end of each sentence, split exactly like the English captions."""
    bounds, position = [], 0
    for match in SENTENCE_BREAK.finditer(text):
        bounds.append((position, match.start()))
        position = match.end()
    bounds.append((position, len(text)))
    spans = []
    for low, high in bounds:
        units = list(UNIT.finditer(text, low, high))
        if not text[low:high].strip():
            continue
        if not units:
            return None
        start, end = _span_seconds(alignment, units[0].start(), units[-1].end() - 1, scale)
        spans.append({"start": start, "end": end})
    return spans


def timed_sentences(sentences, spans, duration):
    """Each caption stays up from its measured start until the next sentence starts."""
    if not spans or len(sentences) != len(spans):
        return None
    captions = []
    for index, (line, span) in enumerate(zip(sentences, spans)):
        if index + 1 < len(spans):
            end = spans[index + 1]["start"]
        else:
            end = min(duration, span["end"] + LAST_CAPTION_HOLD)
        if not span["start"] < end:
            return None
        captions.append({"text": line, "start": span["start"], "end": end})
    return captions


def aligned_words(raw_tokens, display_tokens, text, alignment, normalize, scale=1.0):
    """Give every displayed script word the measured time of the speech it became.

    The audio is TTS of normalize(script). Each script token (or a run of up to
    MAX_GROUP tokens, for phrases like "Rs 99" that normalize together) must
    reproduce the exact next spoken units, so a word is only highlighted while
    its own sound plays. Any unmatched word turns the whole highlight off.
    """
    if len(raw_tokens) != len(display_tokens):
        raise ValueError("Caption token arrays differ")

    def spoken_units(value):
        try:
            return UNIT.findall(normalize(value))
        except Exception:
            return []

    tokens = [(raw, shown) for raw, shown in zip(raw_tokens, display_tokens)
              if shown.strip() and spoken_units(raw)]
    spoken = list(UNIT.finditer(text))
    if not tokens or not spoken:
        return [], {"mode": "plain", "reason": "empty_script", "highlight_verified": False}
    heard = [match.group(0) for match in spoken]
    cache = {}

    def units(index, size):
        if (index, size) not in cache:
            cache[index, size] = spoken_units(" ".join(raw for raw, _ in tokens[index:index + size]))
        return cache[index, size]

    count, total = len(tokens), len(heard)
    cost = {(0, 0): 0}
    back = {}
    for index in range(count):
        for position in sorted(p for (i, p) in cost if i == index):
            here = cost[index, position]
            for size in range(1, min(MAX_GROUP, count - index) + 1):
                expected = units(index, size)
                finish = position + len(expected)
                if not expected or finish > total or heard[position:finish] != expected:
                    continue
                state = (index + size, finish)
                if here + size - 1 < cost.get(state, math.inf):
                    cost[state] = here + size - 1
                    back[state] = (index, position, size)
    if (count, total) not in cost:
        return [], {"mode": "plain", "reason": "script_words_not_matched", "highlight_verified": False}
    groups, state = [], (count, total)
    while state != (0, 0):
        index, position, size = back[state]
        groups.append((index, position, size, state[1]))
        state = (index, position)
    output, previous, grouped = [], 0.0, 0
    for index, position, size, finish in reversed(groups):
        start, end = _span_seconds(alignment, spoken[position].start(), spoken[finish - 1].end() - 1, scale)
        if start < previous - 1e-6 or not end >= start:
            return [], {"mode": "plain", "reason": "unordered_word_timestamps", "highlight_verified": False}
        grouped += size if size > 1 else 0
        output.append({"text": " ".join(shown for _, shown in tokens[index:index + size]),
                       "start": start, "end": max(end, start + 0.04)})
        previous = start
    return output, {"mode": "verified_word_timing", "reason": "all_words_have_character_timestamps",
                    "highlight_verified": True, "word_count": len(output), "grouped_tokens": grouped}
