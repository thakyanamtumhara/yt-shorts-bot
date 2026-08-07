#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""260 GSM launch reel — production build.

IMG_6180.MOV (4K vertical via rotation tag, 98.14s, phone audio)
-> 1080x1920 with house word-pop captions + phone-audio cleanup.

Same pipeline as Factory Sach #3: PIL word-state strips (RAQM shaping),
hardlinked 30fps PNG sequence, ONE ffmpeg overlay pass (this ffmpeg has no
drawtext/subtitles filter). Phone mic (no recorder file this time) so the
audio chain adds highpass + light denoise before loudnorm.
"""
import os, sys, shutil, subprocess
from PIL import Image, ImageDraw, ImageFont, features

assert features.check("raqm"), "raqm required for Devanagari shaping"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lines import LINES

SRC = "/Users/ankit/Downloads/IMG_6180.MOV"
FONT = "/Users/ankit/Projects/yt-shorts-bot/assets/fonts/Baloo2.ttf"
OUT = os.path.join(HERE, "Launch_260GSM_final.mp4")

W, H = 1080, 1920
FPS = 30
DUR = 98.1383
FONTSIZE, STROKE = 64, 4
BASE = (255, 255, 255, 255)
HI = (255, 215, 0, 255)
SC = (0, 0, 0, 255)
Y_PCT = 0.55
MAX_W = W - 160
STRIP_H = 300

STATES = os.path.join(HERE, "states")
SEQ = os.path.join(HERE, "seq")


def kfont(size):
    f = ImageFont.truetype(FONT, size, layout_engine=ImageFont.Layout.RAQM)
    try:
        f.set_variation_by_axes([700])
    except Exception:
        pass
    return f


def strip_img(word_texts, hi_idx):
    fsize = FONTSIZE
    while True:
        font = kfont(fsize)
        gap = int(fsize * 0.35)
        dd = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        boxes = [dd.textbbox((0, 0), wt, font=font, stroke_width=STROKE) for wt in word_texts]
        widths = [b[2] - b[0] for b in boxes]
        total_w = sum(widths) + gap * (len(word_texts) - 1)
        if total_w <= MAX_W or fsize <= 38:
            break
        fsize -= 6
    top = min(b[1] for b in boxes)
    bot = max(b[3] for b in boxes)
    line_h = bot - top
    img = Image.new("RGBA", (W, STRIP_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = (W - total_w) // 2
    y = (STRIP_H - line_h) // 2 - top
    for i, (wt, b, wpx) in enumerate(zip(word_texts, boxes, widths)):
        d.text((x - b[0], y), wt, font=font,
               fill=HI if i == hi_idx else BASE, stroke_width=STROKE, stroke_fill=SC)
        x += wpx + gap
    return img


def main():
    for p in (STATES, SEQ):
        shutil.rmtree(p, ignore_errors=True)
        os.makedirs(p)

    print("rendering caption states…")
    state_path, n_states = {}, 0
    for li, (ls, le, text) in enumerate(LINES):
        toks = text.split()
        assert len(toks) <= 5, f"line {li}: {text}"
        for wi in range(len(toks)):
            p = os.path.join(STATES, f"s_{li:03d}_{wi}.png")
            strip_img(toks, wi).save(p)
            state_path[(li, wi)] = p
            n_states += 1
    blank = os.path.join(STATES, "blank.png")
    Image.new("RGBA", (W, STRIP_H), (0, 0, 0, 0)).save(blank)
    print(f"{len(LINES)} lines / {n_states} word-states")

    nframes = int(round(DUR * FPS))
    cuts = []
    for li, (ls, le, text) in enumerate(LINES):
        toks = text.split()
        per = (le - ls) / len(toks)
        for wi in range(len(toks)):
            cuts.append((ls + wi * per,
                         le if wi == len(toks) - 1 else ls + (wi + 1) * per,
                         state_path[(li, wi)]))
    cuts.sort()
    print(f"building {nframes} frame links…")
    ci = 0
    for f in range(nframes):
        t = f / FPS
        while ci < len(cuts) and cuts[ci][1] <= t:
            ci += 1
        src = blank
        if ci < len(cuts) and cuts[ci][0] <= t < cuts[ci][1]:
            src = cuts[ci][2]
        os.link(src, os.path.join(SEQ, f"{f:05d}.png"))

    y = int(H * Y_PCT) - STRIP_H // 2
    cmd = [
        "ffmpeg", "-v", "error", "-stats", "-y",
        "-i", SRC,
        "-framerate", str(FPS), "-start_number", "0", "-i", os.path.join(SEQ, "%05d.png"),
        "-filter_complex",
        f"[0:v]scale={W}:{H},setsar=1,fps={FPS}[bg];[bg][1:v]overlay=0:{y}:shortest=1,format=yuv420p[v]",
        "-map", "[v]", "-map", "0:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-profile:v", "high", "-level", "4.1", "-r", str(FPS),
        "-af", "highpass=f=80,afftdn=nf=-28,loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        OUT,
    ]
    print("encoding…")
    subprocess.run(cmd, check=True)
    print("FINAL:", OUT, os.path.getsize(OUT) // (1024 * 1024), "MB")


if __name__ == "__main__":
    main()
