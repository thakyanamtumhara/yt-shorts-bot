#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure what a filter chain actually does to his voice.

Two things matter and they are different:
  1. TONE  — per-band RMS on the SAME speech. Denoisers eat presence/air, which
             is exactly what makes a voice sound "filtered".
  2. ROOM  — the level in the gaps BETWEEN words. A denoiser drives room tone to
             digital silence; the ear reads that as processed/detached even when
             the speech itself is untouched.
"""
import re
import subprocess
import sys

BANDS = [(150, 100), (400, 300), (1000, 600), (2500, 1500),
         (5000, 2000), (8000, 3000), (12000, 4000)]


def rms(path, f=None, w=None):
    af = "astats"
    if f:
        af = "bandpass=f=%d:width_type=h:w=%d,astats" % (f, w)
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af", af, "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = [x for x in out.splitlines() if "RMS level dB" in x and "Parsed_astats" in x]
    if not m:
        return None
    v = m[0].split()[-1]
    try:
        return float(v)
    except ValueError:
        return None


def gaps(path, thresh=-45.0, win=0.02):
    """Level inside the quiet stretches — i.e. the room tone."""
    import array
    import math
    import wave
    w = wave.open(path)
    sr = w.getframerate()
    a = array.array("h", w.readframes(w.getnframes()))
    n = int(sr * win)
    quiet = []
    for i in range(0, len(a) - n, n):
        s = a[i:i + n]
        r = math.sqrt(sum(x * x for x in s) / len(s))
        db = 20 * math.log10(max(r, 1) / 32768)
        if db < thresh:
            quiet.append(db)
    if not quiet:
        return None, 0
    return sum(quiet) / len(quiet), len(quiet) * win


def compare(label_a, a, label_b, b):
    print("\n%-14s %10s %10s %9s" % ("band", label_a, label_b, "change"))
    print("-" * 46)
    for f, w in BANDS:
        ra, rb = rms(a, f, w), rms(b, f, w)
        if ra is None or rb is None:
            continue
        print("%-14s %10.1f %10.1f %+9.1f" % ("%d Hz" % f, ra, rb, rb - ra))
    ga, da = gaps(a)
    gb, db = gaps(b)
    print("\nroom tone in the gaps:")
    print("  %-12s %6.1f dB  over %.1fs of silence" % (label_a, ga, da))
    print("  %-12s %6.1f dB  over %.1fs of silence" % (label_b, gb, db))
    print("  -> denoiser removed %.1f dB of room tone" % (ga - gb))


if __name__ == "__main__":
    compare(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
