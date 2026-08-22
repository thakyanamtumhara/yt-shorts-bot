#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Burn captions into reel v2.

The v1 reel had NO captions at all — only the 0.5s cover carried text. On a muted
Instagram feed that gives a scroller nothing to read, which is half of why Ketu
said "they will not know what I'm talking about".

Timings are anchored to the 20ms RMS speech runs of the finished cut, not to
word-level DTW (which drifted up to 9s on this footage) and not to block ASR
(which silently drops content). Text is cleaned of stumbles — he says
"और 20 साल की एज से... करीब... 21-22 साल की" and the caption just reads
"21-22 साल की एज से".

Highlight colour is BLUE #7FD4FF, never red/green — Ketu is red-green colour blind,
and the yellow t-shirt would swallow a yellow highlight anyway.

This ffmpeg has no drawtext/subtitles filter, so: one PNG per caption state,
hardlinked into a 30fps sequence, single overlay pass.
"""
import os
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont, features

assert features.check("raqm"), "raqm required for Devanagari shaping"

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "cut2.mp4")
OUT = os.path.join(HERE, "reel2_cap.mp4")
FONT = "/Users/ankit/Projects/yt-shorts-bot/assets/fonts/Baloo2.ttf"

W, H, FPS = 1080, 1920, 30
BASE = (255, 255, 255, 255)
HI = (127, 212, 255, 255)      # #7FD4FF
SC = (0, 0, 0, 255)
FONTSIZE, STROKE = 74, 6
MAX_W = W - 150
CY = 1570                       # sits over his chest, clear of the mouth

# {} marks the highlight. Times are in output seconds.
CAPS = [
    (0.15,  1.60, "आपने कुछ सोचा होगा"),
    (1.60,  3.02, "ये कर देते हैं, देखते हैं क्या होता है"),
    (3.02,  4.15, "लेकिन {आप कर नहीं पा रहे हो}"),
    (4.60,  6.06, "छुट्टी मिलेगी तब करेंगे"),
    (6.06,  7.48, "या समय मिलेगा तब करेंगे"),
    (7.48,  9.40, "{कर डालो} — रुकना नहीं है"),
    (9.95, 10.82, "{ज़्यादा से ज़्यादा क्या होगा?}"),
    (10.82, 12.30, "एक रोटी कम खाओगे दिन में"),
    (13.25, 14.10, "यह होगा ना भाई"),
    (15.25, 16.50, "खा लेना, कम खा लेना"),
    (17.35, 18.20, "{एक रोटी कम}"),
    (18.20, 19.10, "{क्या फ़र्क पड़ता है}"),
    (19.55, 20.90, "कुछ भी करना है…"),
    (20.90, 22.40, "मेरी एज इस समय {30} है"),
    (22.90, 27.40, "{21-22 साल} की एज से"),
    (27.40, 30.20, "मैं इस चीज़ में घुसा हूं — प्लेन टी-शर्ट"),
    (31.25, 33.60, "{नौ साल} से कर रहा हूं"),
    (34.05, 36.95, "तो भैया कर जाओ, जो कुछ करना है"),
    (36.95, 38.28, "जो कुछ भी करना है, {कर जाओ}"),
    (38.28, 41.60, "यही समय है, {यही जवानी है}"),
    (41.60, 44.80, "एक समय आएगा — ना तो एनर्जी इतनी होगी"),
    (44.80, 46.35, "आपके शरीर में"),
    (46.35, 50.05, "दिमाग में {conflict} चलते रहेंगे"),
    (50.05, 52.55, "शरीर में {ऊर्जा बचेगी नहीं}"),
    (52.55, 54.55, "आप परेशान होते रहोगे"),
    (54.55, 55.65, "मेरे हिसाब से"),
    (55.65, 57.05, "जो करना है वो {कर डालो}"),
    (57.05, 59.05, "विचार मत करो"),
    (59.05, 60.25, "{सोचो मत}"),
    (60.25, 62.95, "चीज़ें कभी {परफेक्ट नहीं होंगी}"),
    (62.95, 67.25, "परफेक्ट नहीं होंगी यार, नहीं होंगी"),
    (67.25, 70.75, "आप {success} को किस मायने में देखते हो?"),
    (70.75, 74.25, "बहुत अलग-अलग मायने होंगे"),
    (74.25, 76.45, "लेकिन फाइनली यही होता है"),
    (76.45, 78.95, "कि आप कितने {आनंद} में हो"),
    (78.95, 80.05, "कितने मज़े में हो"),
    (80.05, 82.20, "तो भैया {मज़े में रहो}"),
    (82.20, 83.88, "{बड़ी दूर तक जाओगे}"),
]


def kfont(size):
    f = ImageFont.truetype(FONT, size, layout_engine=ImageFont.Layout.RAQM)
    try:
        f.set_variation_by_axes([700])
    except Exception:
        pass
    return f


def tokens(s):
    """-> [(text, is_highlight)] preserving spaces between words."""
    out, cur, hi = [], "", False
    for ch in s:
        if ch == "{":
            if cur:
                out.append((cur, hi))
            cur, hi = "", True
        elif ch == "}":
            if cur:
                out.append((cur, hi))
            cur, hi = "", False
        else:
            cur += ch
    if cur:
        out.append((cur, hi))
    # split into words but keep highlight flag
    words = []
    for txt, h in out:
        for i, wd in enumerate(txt.split(" ")):
            if wd:
                words.append((wd, h))
    return words


def wrap(words, font, draw):
    """Greedy 2-line wrap on measured widths."""
    gap = draw.textlength(" ", font=font)
    lines, cur, curw = [], [], 0.0
    for wd, h in words:
        wlen = draw.textlength(wd, font=font)
        add = wlen if not cur else wlen + gap
        if cur and curw + add > MAX_W:
            lines.append(cur)
            cur, curw = [(wd, h)], wlen
        else:
            cur.append((wd, h))
            curw += add
    if cur:
        lines.append(cur)
    return lines


def render(text, path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    words = tokens(text)
    size = FONTSIZE
    while True:
        font = kfont(size)
        lines = wrap(words, font, d)
        if len(lines) <= 2 or size <= 34:
            break
        size -= 3
    font = kfont(size)
    gap = d.textlength(" ", font=font)
    lh = int(size * 1.42)
    total_h = lh * len(lines)
    y = CY - total_h // 2
    for ln in lines:
        widths = [d.textlength(wd, font=font) for wd, _ in ln]
        tw = sum(widths) + gap * (len(ln) - 1)
        x = (W - tw) / 2
        for (wd, h), wdt in zip(ln, widths):
            d.text((x, y), wd, font=font, fill=(HI if h else BASE),
                   stroke_width=STROKE, stroke_fill=SC)
            x += wdt + gap
        y += lh
    img.save(path)


def main():
    st = os.path.join(HERE, "cstates")
    sq = os.path.join(HERE, "cseq")
    for p in (st, sq):
        shutil.rmtree(p, ignore_errors=True)
        os.makedirs(p)

    blank = os.path.join(st, "blank.png")
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)
    paths = []
    for i, (a, b, t) in enumerate(CAPS):
        p = os.path.join(st, "c%03d.png" % i)
        render(t, p)
        paths.append(p)
    print("rendered %d caption states" % len(paths))

    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                "format=duration", "-of", "csv=p=0", SRC],
                               capture_output=True, text=True).stdout)
    n = int(dur * FPS)
    for f in range(n):
        t = f / FPS
        use = blank
        for i, (a, b, _) in enumerate(CAPS):
            if a <= t < b:
                use = paths[i]
                break
        os.link(use, os.path.join(sq, "%05d.png" % f))
    print("%d frames linked (%.2fs)" % (n, dur))

    subprocess.run(["ffmpeg", "-v", "error", "-stats", "-y",
                    "-i", SRC, "-framerate", str(FPS), "-i", os.path.join(sq, "%05d.png"),
                    "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto,format=yuv420p[v]",
                    "-map", "[v]", "-map", "0:a",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-profile:v", "high", "-level", "4.0",
                    "-c:a", "copy", "-movflags", "+faststart", OUT], check=True)
    shutil.rmtree(sq, ignore_errors=True)
    print("wrote", os.path.basename(OUT))


if __name__ == "__main__":
    main()
