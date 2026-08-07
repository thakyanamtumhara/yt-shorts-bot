#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""260 GSM launch cover — house renderer (Baloo2 via RAQM, whole-string draws;
never char-by-char, that breaks Devanagari shaping). Text band centred at 62%
because this is a selfie framing — his face fills the upper half, unlike the
godam-wide Factory Sach frames."""
from PIL import Image, ImageDraw, ImageFont, features
import os

assert features.check("raqm"), "raqm required"

W, H = 1080, 1920
FONT = "/Users/ankit/Projects/yt-shorts-bot/assets/fonts/Baloo2.ttf"
YELLOW = (255, 214, 10); WHITE = (255, 255, 255)
BAND_C = 0.70          # selfie framing: face fills 20-70%, so text sits at chest


def _f(size):
    f = ImageFont.truetype(FONT, size, layout_engine=ImageFont.Layout.RAQM)
    try: f.set_variation_by_axes([800])
    except Exception: pass
    return f


def seg_width(d, s, size):
    return int(d.textlength(s, font=_f(size)))


def draw_mixed(d, x, y, s, size, fill, ow):
    f = _f(size)
    d.text((x + ow + 5, y + ow + 7), s, font=f, fill=(0, 0, 0))
    for dx in range(-ow, ow + 1):
        for dy in range(-ow, ow + 1):
            if dx * dx + dy * dy <= ow * ow:
                d.text((x + dx, y + dy), s, font=f, fill=(0, 0, 0))
    d.text((x, y), s, font=f, fill=fill)
    return int(d.textlength(s, font=f))


def fit(d, full, target_w, start, minsz=44):
    sz = start
    while sz > minsz and seg_width(d, full, sz) > target_w:
        sz -= 4
    return sz


def darken(img, band=(0.58, 0.84), scrim=95, top=140, bottom=190):
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0)); dd = ImageDraw.Draw(ov)
    b0, b1 = int(H * band[0]), int(H * band[1])
    for y in range(H):
        if y < H * 0.30:
            a = int(top * (1 - y / (H * 0.30)))
        elif y > H * 0.72:
            a = int(bottom * ((y - H * 0.72) / (H * 0.28)))
        else:
            a = 0
        if b0 <= y <= b1:
            edge = min(y - b0, b1 - y) / max(1, (b1 - b0) * 0.22)
            a = max(a, int(scrim * min(1.0, edge)))
        dd.line([(0, y), (W, y)], fill=(0, 0, 0, max(0, min(255, a))))
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")


def render(bg, rows, out):
    img = Image.open(bg).convert("RGB").resize((W, H))
    img = darken(img)
    d = ImageDraw.Draw(img)
    safe_w = int(W * 0.92)
    sized = []
    for segs, start in rows:
        full = "".join(s for s, _ in segs)
        sz = fit(d, full, safe_w, start)
        sized.append((segs, sz, seg_width(d, full, sz), int(sz * 1.02)))
    gap = 26
    total = sum(h for *_, h in sized) + gap * (len(sized) - 1)
    y = int(H * BAND_C - total / 2)
    for segs, sz, w, h in sized:
        cx = (W - w) // 2
        ow = max(6, int(sz * 0.075))
        for s, col in segs:
            cx += draw_mixed(d, cx, y, s, sz, col, ow)
        y += h + gap
    img.save(out, "PNG")
    print("saved", out)


if __name__ == "__main__":
    D = os.path.dirname(os.path.abspath(__file__))
    bg = os.path.join(D, "cand", "f2.0.jpg")
    render(bg, [
        ([("नया ", WHITE), ("260 GSM", YELLOW)], 195),
        ([("HEAVY T-SHIRT LAUNCH", WHITE)], 135),
    ], os.path.join(D, "coverA.png"))
    render(bg, [
        ([("240 के बाद —", WHITE)], 140),
        ([("अब ", WHITE), ("260 GSM", YELLOW)], 205),
    ], os.path.join(D, "coverB.png"))
