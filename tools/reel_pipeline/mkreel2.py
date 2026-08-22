#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reel v2 — re-cut for a hook that actually lands.

Why v1 failed (Ketu, 21-Aug): it opened at source 899.98, which is 0.7s INTO the
phrase "और आदमी फ्रस्ट्रेट होता रहता है" — so the first word a viewer hears is
"और". Then 1.5s of dead air at 0:07, and the thesis did not arrive until 0:29.
His words: "it's not catching... they will not know what I'm talking about."

New opening is the procrastination -> "कर डालो" -> roti run (1028.4-1048.0). It is
ONE continuous take that sets up its own punchline, so the hook needs no invented
on-screen framing and has no seam. A judge panel picked the roti line 3-1 as the
strongest opener but every judge flagged the same risk — "worst case if I do WHAT?"
Using the run that already contains the setup answers that for free.

Note: block-level ASR shows the roti line twice at 1037.5 and 1038.8. The 20ms RMS
envelope says 1037.46-1039.08 is BELOW -42dB, i.e. silence. The repeat is a Whisper
repetition artifact, not a stumble in the audio. Do not "fix" it.

Every boundary below sits inside a measured silence >= 0.25s, so no word is clipped
and a mid-sentence cut is structurally impossible.
"""
import array
import json
import math
import os
import subprocess
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "/Users/ankit/Desktop/DJI_20260819094237_0018_D.MP4"
WAV = os.path.join(HERE, "audio.wav")
OUT = os.path.join(HERE, "cut2.mp4")

# Squeeze only clearly-dead pauses. Gentler than the long video's 1.0/0.55 because
# the beat before "ज़्यादा से ज़्यादा क्या होगा?" is comic timing, not dead air.
KEEP_UNDER, TARGET, GUARD = 1.10, 0.65, 0.12
THRESH_DB, WIN = -42.0, 0.02

GRADE = ("eq=brightness=-0.045:contrast=1.06:saturation=0.94,"
         "colorbalance=rs=-0.06:bs=0.06:rm=-0.04:bm=0.05")

BLOCKS = [
    (1028.40, 1048.00, "HOOK  postpone -> कर डालो -> एक रोटी कम"),
    (909.78,  926.48,  "WHO   30, 21-22 से, प्लेन टी-शर्ट, 9 साल"),
    (964.64,  971.92,  "URGE  कर जाओ / यही समय है यही जवानी है"),
    (973.26,  976.82,  "STAKE एक समय आएगा, एनर्जी इतनी नहीं होगी"),
    (989.54,  997.48,  "STAKE दिमाग में conflict / ऊर्जा बचेगी नहीं"),
    (927.88,  939.94,  "THESIS जो करना है कर डालो / परफेक्ट नहीं होंगी"),
    # Was 1122.26-1135.04. Two faults: it opened on "लेकिन" (mid-thought, the exact
    # sin that made v1 open on "और"), and 1135.04 landed inside "...और बड़ा ही—",
    # cutting the last sentence in half. 1115.78 starts on "ठीक है, आप success को
    # किस मायने में देखते हो" and 1133.70 ends on "बड़ी दूर तक जाओगे".
    (1115.78, 1133.70, "CLOSE success का मतलब / मज़े में रहो, बड़ी दूर तक जाओगे"),
]

# Blocks butt straight up against each other in the concat, so a block that starts
# exactly on its first syllable and ends exactly on its last produces an audible
# hard splice. Breathe each edge out into the silence that is already there — this
# is real room tone from the take, never inserted digital silence.
LEAD, TAIL = 0.25, 0.35


def envelope():
    w = wave.open(WAV)
    sr = w.getframerate()
    a = array.array("h", w.readframes(w.getnframes()))
    n = int(sr * WIN)
    return [(i * WIN, 20 * math.log10(max(math.sqrt(
        sum(x * x for x in a[i * n:(i + 1) * n]) / n), 1) / 32768))
        for i in range(len(a) // n)], w.getnframes() / sr


def silences(env):
    out, st = [], None
    for t, v in env:
        if v < THRESH_DB:
            if st is None:
                st = t
        else:
            if st is not None and t - st >= 0.25:
                out.append((st, t))
            st = None
    return out


def main():
    env, _ = envelope()
    sil = silences(env)

    # every block edge must land inside a measured silence
    for a, b, tag in BLOCKS:
        for edge in (a, b):
            assert any(s - 0.02 <= edge <= e + 0.02 for s, e in sil), \
                "edge %.2f (%s) is not in a silence" % (edge, tag)

    def pad(a, b):
        for s, e in sil:
            if s - 0.02 <= a <= e + 0.02:
                a = max(s, a - LEAD); break
        for s, e in sil:
            if s - 0.02 <= b <= e + 0.02:
                b = min(e, b + TAIL); break
        return a, b

    BLOCKS[:] = [(pad(a, b)[0], pad(a, b)[1], t) for a, b, t in BLOCKS]

    keeps, tmap, outt = [], [], 0.0
    for a, b, tag in BLOCKS:
        cuts = []
        for s, e in sil:
            if s >= a and e <= b and (e - s) > KEEP_UNDER + 2 * GUARD:
                mid, ex = (s + e) / 2.0, (e - s) - TARGET
                cuts.append((max(mid - ex / 2, s + GUARD), min(mid + ex / 2, e - GUARD)))
        cur = a
        for c0, c1 in cuts:
            if c0 - cur > 0.05:
                keeps.append((cur, c0)); tmap.append((cur, c0, outt)); outt += c0 - cur
            cur = c1
        if b - cur > 0.05:
            keeps.append((cur, b)); tmap.append((cur, b, outt)); outt += b - cur

    print("blocks:")
    for a, b, tag in BLOCKS:
        print("   %7.2f-%7.2f  %5.1fs  %s" % (a, b, b - a, tag))
    print("\n%d source pieces -> %.2fs (%d:%04.1f)" % (len(keeps), outt, outt // 60, outt % 60))
    json.dump({"blocks": BLOCKS, "keeps": keeps, "tmap": tmap, "dur": outt},
              open(os.path.join(HERE, "cut2_map.json"), "w"), indent=1)

    parts, streams = [], ""
    for i, (a, b) in enumerate(keeps):
        parts.append("[0:v]trim=start=%.3f:end=%.3f,setpts=PTS-STARTPTS[v%d];" % (a, b, i))
        parts.append("[0:a]atrim=start=%.3f:end=%.3f,asetpts=PTS-STARTPTS[a%d];" % (a, b, i))
        streams += "[v%d][a%d]" % (i, i)
    graph = ("".join(parts) + streams + "concat=n=%d:v=1:a=1[vc][ac];" % len(keeps) +
             "[vc]" + GRADE + ",scale=1080:1920:flags=lanczos,fps=30,format=yuv420p,setsar=1[v];"
             "[ac]highpass=f=60[a]")
    gf = os.path.join(HERE, "_g3.txt")
    open(gf, "w").write(graph)
    raw = os.path.join(HERE, "_cut2raw.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-stats", "-y", "-i", SRC,
                    "-filter_complex_script", gf, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-profile:v", "high", "-level", "4.0",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    "-movflags", "+faststart", raw], check=True)
    os.remove(gf)

    def measure(p):
        o = subprocess.run(["ffmpeg", "-hide_banner", "-i", p, "-vn", "-af",
                            "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
                            "-f", "null", "-"], capture_output=True, text=True).stderr
        return {k: float([l for l in o.splitlines() if '"%s"' % k in l][0].split('"')[3])
                for k in ("input_i", "input_tp", "input_lra", "input_thresh")}

    m = measure(raw)
    af = ("loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=%(input_i).2f:measured_TP=%(input_tp).2f:"
          "measured_LRA=%(input_lra).2f:measured_thresh=%(input_thresh).2f" % m
          + ",alimiter=limit=0.79:level=disabled")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-af", af, "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    "-movflags", "+faststart", OUT], check=True)
    os.remove(raw)
    f = measure(OUT)
    d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", OUT], capture_output=True, text=True).stdout.strip()
    print("cut2.mp4  %ss  %.2f LUFS  TP %+.2f" % (d, f["input_i"], f["input_tp"]))


if __name__ == "__main__":
    main()
