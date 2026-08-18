#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prepend the cover as a FULL 1.0 second still.

Usage: python3 prepend_cover.py IN.mp4 COVER.png OUT.mp4

His rule (2026-08-01): on YouTube/FB/LinkedIn/X the platform picks the thumbnail
from a video frame, so the cover has to be IN the file. A full second, not 0.2s —
a fifth of a second is too small a target to hit in the frame picker.

Encoded to match the main file's parameters exactly (H.264 High@4.0, yuv420p,
1080x1920, 30fps, AAC 48k stereo) so the two can be concatenated with STREAM COPY.
That avoids re-encoding 10 minutes of video, which would take ~15 min and cost a
generation of quality. Falls back to a full re-encode only if the copy path fails
verification.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, sys.argv[1] if len(sys.argv) > 1 else "input.mp4")
COVER = os.path.join(HERE, sys.argv[2] if len(sys.argv) > 2 else "cover.png")
STILL = os.path.join(HERE, "_cover1s.mp4")
OUT = os.path.join(HERE, sys.argv[3] if len(sys.argv) > 3 else "output.mp4")
HOLD = 1.0   # FULL second. Never 0.2/0.3/0.5 — Ketu has called that out twice:
             # he drags in the YouTube frame picker and a third of a second is
             # too small a target, so he lands on a random mid-word frame.


def probe(path):
    d = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_name,width,height,pix_fmt,profile,level",
         "-of", "json", path], capture_output=True, text=True).stdout)
    return d


def main():
    before = float(probe(MAIN)["format"]["duration"])

    # 1s still, silent stereo audio, parameters matched to the main file
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-loop", "1", "-t", "%.3f" % HOLD, "-i", COVER,
        "-f", "lavfi", "-t", "%.3f" % HOLD, "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-vf", "scale=1080:1920,fps=30,format=yuv420p,setsar=1",
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.0",
        "-preset", "medium", "-crf", "18", "-g", "30", "-keyint_min", "30",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-video_track_timescale", "15360",
        "-movflags", "+faststart", STILL], check=True)

    lst = os.path.join(HERE, "_concat.txt")
    with open(lst, "w") as f:
        f.write("file '%s'\nfile '%s'\n" % (STILL, MAIN))

    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", lst, "-c", "copy", "-movflags", "+faststart", OUT],
                       capture_output=True, text=True)

    ok = r.returncode == 0 and os.path.exists(OUT)
    if ok:
        try:
            after = float(probe(OUT)["format"]["duration"])
        except Exception:
            ok = False
        else:
            # concat must have ADDED the hold, not silently dropped a stream
            ok = abs(after - (before + HOLD)) < 0.45

    if not ok:
        print("stream-copy concat rejected -> falling back to re-encode")
        graph = ("[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]")
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-i", STILL, "-i", MAIN,
            "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", OUT], check=True)
        after = float(probe(OUT)["format"]["duration"])

    os.remove(lst)
    os.remove(STILL)

    v = [s for s in probe(OUT)["streams"] if s.get("width")][0]
    print("in  %.2fs -> out %.2fs  (+%.2fs)" % (before, after, after - before))
    print("out %sx%s %s %s  %.0f MB"
          % (v["width"], v["height"], v["codec_name"], v["pix_fmt"],
             os.path.getsize(OUT) / 1e6))
    assert after > before, "cover was not added"


if __name__ == "__main__":
    sys.exit(main())
