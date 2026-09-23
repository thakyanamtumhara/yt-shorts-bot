"""Narrow publication holds for observed unsupported textile explanations."""

import html
import re


def unsupported_derived_claim(content):
    text = re.sub(r'(?is)<(script|style)\b.*?</\1>', ' ', content or '')
    text = re.sub(r'(?i)</(?:p|div|li|tr|h[1-6])\s*>', '\n', text)
    text = html.unescape(re.sub(r'<[^>]+>', ' ', text))
    text = re.sub(r'[ \t]+', ' ', text).strip()

    if re.search(r'\bone reference,? then move on\b', text, re.I):
        return 'Editorial instructions leaked into the public article.'

    if re.search(r'\btuck(?:ed|ing|s|[- ]stitch)?\b|\bpiqu[eé]\b', text, re.I):
        for sentence in re.split(r'(?<=[.!?])\s+|\n+', text):
            if re.search(r'\b(?:do not|don\x27t|never) (?:describe|call|label)|\bdoes not mean\b', sentence, re.I):
                continue
            if re.search(r'\b(?:incomplete|partially[- ]formed|not fully formed)\b.{0,45}\bloops?\b|'
                         r'\bloops?\b.{0,65}\b(?:(?:not|aren\x27t|don\x27t get) fully (?:formed|pulled through)|incomplete|partially[- ]formed)\b', sentence, re.I):
                return 'Tuck formation must describe held and tuck loops being knitted through later, not incomplete loops.'

    for paragraph in re.split(r'\n+', text):
        if not re.search(r'\bprint(?:ing|ed|s)?\b', paragraph, re.I):
            continue
        if re.search(r'\b(?:not|never) (?:only|solely|entirely) (?:a )?fib(?:re|er)\b', paragraph, re.I):
            continue
        if re.search(r'\bfib(?:re|er)[- ]level\b.{0,65}\bnot (?:a )?construction(?:[- ]level)?\b|'
                     r'\b(?:only|solely|entirely) (?:on )?(?:the )?fib(?:re|er)(?: composition)?\b', paragraph, re.I):
            return 'A fibre label alone cannot establish print compatibility; evaluate the actual blank and printing process.'
    return None
