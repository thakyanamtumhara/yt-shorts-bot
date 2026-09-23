"""Use evidenced speech timestamps; never stretch guessed karaoke across audio."""

from difflib import SequenceMatcher
import math
import unicodedata


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def timing_segments(result):
    segments = []
    for item in result.get("segments", []):
        segments.append({"start": item.get("start"), "end": item.get("end"), "words": [
            {"text": (word.get("word") or word.get("text") or "").strip(),
             "start": word.get("start"), "end": word.get("end")}
            for word in item.get("words", [])]})
    return segments


def timing_evidence(segments, duration):
    result = {"segments_reliable": False, "words_reliable": False, "word_count": 0,
              "reason": "missing_or_invalid_segments"}
    if not _number(duration) or duration <= 0 or not segments:
        return result
    previous_end = 0
    for segment in segments:
        start, end = segment.get("start"), segment.get("end")
        if (not _number(start) or not _number(end) or not 0 <= start < end <= duration + 0.15
                or start < previous_end - 0.1):
            return result
        previous_end = end
    start, end = segments[0]["start"], segments[-1]["end"]
    result.update(segment_start=round(start, 3), segment_end=round(end, 3))
    if start > 2 or end < duration - max(1.5, duration * 0.12):
        return {**result, "reason": "incomplete_segment_coverage"}
    result["segments_reliable"] = True
    words = []
    for segment in segments:
        for word in segment.get("words", []):
            start, end = word.get("start"), word.get("end")
            if (not word.get("text") or not _number(start) or not _number(end)
                    or not segment["start"] - 0.1 <= start < end <= segment["end"] + 0.1
                    or (words and start < words[-1]["end"] - 0.1)):
                return {**result, "reason": "invalid_word_timestamps"}
            words.append(word)
    result["word_count"] = len(words)
    if not words:
        return {**result, "reason": "word_timestamps_missing"}
    result.update(word_start=round(words[0]["start"], 3), word_end=round(words[-1]["end"], 3))
    if words[0]["start"] > segments[0]["start"] + 0.75 or words[-1]["end"] < segments[-1]["end"] - 0.75:
        return {**result, "reason": "incomplete_word_coverage"}
    return {**result, "words_reliable": True, "reason": "source_timestamps_complete"}


def choose_timing_source(result, duration, retry=None):
    """At most one same-model retry when original timestamps lack full coverage."""
    original = timing_evidence(timing_segments(result), duration)
    evidence = {**original, "retry_attempted": False, "source": "initial_whisper"}
    if original["words_reliable"] or retry is None:
        return result, evidence
    evidence["retry_attempted"] = True
    try:
        alternative = retry()
        checked = timing_evidence(timing_segments(alternative), duration)
    except Exception as error:
        return result, {**evidence, "retry_error": type(error).__name__}
    evidence["retry_reason"] = checked["reason"]
    rank = lambda item: (item["words_reliable"], item["segments_reliable"])
    if rank(checked) > rank(original):
        return alternative, {**checked, "retry_attempted": True, "source": "retry_whisper",
                             "initial_reason": original["reason"]}
    return result, evidence


def plain_caption_segments(sentences, segments, duration):
    evidence = timing_evidence(segments, duration)
    if evidence["segments_reliable"] and len(sentences) == len(segments):
        return [{"text": text, "start": segment["start"], "end": segment["end"]}
                for text, segment in zip(sentences, segments)], "segment_timed_plain"
    total = sum(max(1, len(text.split())) for text in sentences)
    captions, elapsed = [], 0
    for text in sentences:
        end = elapsed + duration * max(1, len(text.split())) / max(total, 1)
        captions.append({"text": text, "start": elapsed, "end": min(end, duration)})
        elapsed = end
    return captions, "estimated_plain"


def _letters(text):
    return "".join(character for character in unicodedata.normalize("NFKC", text).casefold()
                   if character.isalnum() or unicodedata.category(character).startswith("M"))


def verified_karaoke(raw_tokens, display_tokens, segments, duration, normalize, align):
    evidence = timing_evidence(segments, duration)
    if not evidence["words_reliable"]:
        return [], {"mode": "plain", "reason": evidence["reason"], "highlight_verified": False}
    if len(raw_tokens) != len(display_tokens):
        raise ValueError("Caption token arrays differ")
    pairs = [(raw, shown) for raw, shown in zip(raw_tokens, display_tokens) if _letters(raw) and shown.strip()]
    if not pairs:
        return [], {"mode": "plain", "reason": "empty_script", "highlight_verified": False}
    words = [word for segment in segments for word in segment.get("words", [])]
    try:
        aligned = align([raw for raw, _ in pairs], words, normalize)
    except Exception as error:
        return [], {"mode": "plain", "reason": "alignment_error", "error": type(error).__name__,
                    "highlight_verified": False}
    if not aligned or len(aligned) != len(pairs):
        return [], {"mode": "plain", "reason": "incomplete_alignment", "highlight_verified": False}
    output, last_end = [], 0
    for (raw, display), stamp in zip(pairs, aligned):
        if (not isinstance(stamp, (list, tuple)) or len(stamp) != 2
                or not all(_number(value) for value in stamp)):
            return [], {"mode": "plain", "reason": "missing_word_anchor", "highlight_verified": False}
        start, end = stamp
        if not 0 <= start < end <= duration + 0.1 or start < last_end - 0.1:
            return [], {"mode": "plain", "reason": "collapsed_or_overlapping_alignment", "highlight_verified": False}
        heard = [word for word in words if word["start"] >= start - 0.06 and word["end"] <= end + 0.06]
        expected = _letters(normalize(raw))
        actual = _letters("".join(word["text"] for word in heard))
        if not actual or (expected != actual and (min(len(expected), len(actual)) < 4
                          or SequenceMatcher(None, expected, actual).ratio() < 0.85)):
            return [], {"mode": "plain", "reason": "unverified_word_anchor", "highlight_verified": False}
        output.append({"text": display, "start": start, "end": end})
        last_end = end
    if (output[0]["start"] > words[0]["start"] + 0.3
            or output[-1]["end"] < words[-1]["end"] - 0.3):
        return [], {"mode": "plain", "reason": "alignment_missing_speech_edges", "highlight_verified": False}
    return output, {"mode": "verified_word_timing", "reason": "all_words_have_audio_anchors",
                    "highlight_verified": True, "word_count": len(output)}
