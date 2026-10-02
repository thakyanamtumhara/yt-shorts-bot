"""Daily Short script checks for Ketu's cloned voice (Eleven v4 at its natural pace).

Measured 2-Oct-2026 on eight recent daily Short scripts: eleven_v4 speaks the normalized
script at 0.33-0.39 s per spoken word (mean 0.36), 27-44% slower than eleven_v3, so those
114-147-word scripts ran 43-51 s. The five 8-second clips cover about 38 s of voice; longer
audio repeats clips. Six-sentence cuts of the same scripts (91-99 spoken words) ran 30-37 s.
The writer overshoots a plain word range by 10-20% (three drafts asked for 75-90 words came
back at 97-108), so the prompt also caps each sentence, and a script outside LIMITS is sent
back with its sentence lengths before any review, voice or video money is spent. v4 reads
[bracketed] text as delivery directions and has no SSML, so the spoken text must be plain words.
"""

import re

from tools.tts_alignment import UNIT

TARGET_WORDS = (75, 85)
SENTENCE_WORDS = 14
LIMITS = (60, 100)
MARKUP = re.compile(r"<[^>]*>|\[[^\]]*\]")
SENTENCE = re.compile(r"(?<=[.!?])\s+")
SHAPE = (f"6-7 sentences and {TARGET_WORDS[0]}-{TARGET_WORDS[1]} words (hook up to 10 words, every other "
         f"sentence up to {SENTENCE_WORDS} words, final line 3-6 words)")


def spoken_words(spoken_text):
    return len(UNIT.findall(spoken_text or ""))


def sentence_lengths(script_voice):
    return [len(part.split()) for part in SENTENCE.split((script_voice or "").strip()) if part.strip()]


def length_feedback(spoken_text, script_voice="", limits=LIMITS):
    count = spoken_words(spoken_text)
    low, high = limits
    if count > high:
        lengths = ", ".join(str(n) for n in sentence_lengths(script_voice or spoken_text))
        return (f"Script is too long for the voice: {count} spoken words, limit {high} (numbers and rupee "
                f"amounts count as the words said aloud). Sentence lengths now: {lengths} words. Write {SHAPE} "
                f"so the Short stays about 30-35 seconds; cut repetition and extra examples, keep the lesson.")
    if count < low:
        return (f"Script is too short: {count} spoken words. Write {SHAPE} so the Short runs about 30-35 "
                f"seconds and still teaches the complete lesson.")
    return None


def voice_text_feedback(script_voice, spoken_text):
    if MARKUP.search(script_voice or "") or MARKUP.search(spoken_text or ""):
        return ("Remove bracketed directions and <markup> from script_voice; the voice reads plain "
                "spoken words only.")
    return length_feedback(spoken_text, script_voice)
