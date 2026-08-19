# Reel pipeline (Ketu's face videos: Factory Sach + launches)

Local-Mac toolchain for turning a raw shot into the posted reel. Lives in the
repo because /private/tmp scratchpads get purged after ~3 days (learned the
hard way, twice).

Flow per episode:
1. `whisper-cli -m ~/.whisper-models/ggml-large-v3-turbo.bin -f audio16k.wav -l hi -mc 0`
   (+ `-ml 1 -sow -dtw large.v3.turbo` for word timing; re-run shifted regions
   for the 30s-window tail gaps; read output with errors='replace')
2. Hand-correct caption lines into `lines.py` (≤5 tokens, see lines_example.py)
3. `build_reel.py` — edit SRC/OUT/DUR at top. PIL word-pop states → hardlinked
   30fps PNG seq → ONE ffmpeg overlay pass (this ffmpeg has no drawtext).
   AUDIO CHAIN — **`highpass=f=60` + two-pass `loudnorm`. NOTHING ELSE.**
   **Never `afftdn` on Ketu's voice.** He heard it and called it out (19-Aug-2026:
   "it's looking filtered, it's looking processed"). Measured on his own DJI lav
   recording, the old highpass=80+afftdn=nf=-28 chain cost -0.8dB at 2.5k, -1.5 at
   5k, -1.7 at 8k, -2.3 at 12k, and **-10.5dB of room tone in the gaps between
   words** — that dead-silence-between-words is what reads as "processed". His mic
   runs ~21dB SNR with no mains hum; there is nothing to denoise. Verify any chain
   change with `audio_ab.py A.wav B.wav` (per-band RMS + room tone) BEFORE shipping.
   **Rebuild video and audio in ONE filtergraph** — building audio separately and
   re-muxing lets per-segment frame rounding drift them apart.
4. `cover.py` — house cover (Baloo2+RAQM, whole-string draws). BAND_C 0.50 for
   godam-wide framing, ~0.70 for selfie framing (face fills the frame).
5. **Prepend cover for 0.5s** — `prepend_cover.py IN.mp4 COVER.png OUT.mp4`.
   0.5s is Ketu's settled number (19-Aug-2026): 1.0s "looks a little frozen",
   0.2-0.3s is too small a target when he drags the YouTube frame picker.
   Do not re-derive it in either direction.
   Which output needs it:
     * YouTube / any file HE uploads by hand -> 0.5s, mandatory. This is the
       one that actually matters and it is the one that got missed once.
     * Vizard auto-posts (FB / X / LinkedIn) -> the platform takes frame 0, so
       frame 0 must be the cover; 1.0s satisfies this too.
     * Instagram (our Meta queue) -> sets `cover_url` via API, so the baked
       cover is redundant there and only eats into the hook.
   Cheap way to do it on a long file: encode the still matched to the main
   file's codec/profile/level/pix_fmt/timescale + silent stereo AAC, then
   `ffmpeg -f concat -c copy`. No re-encode of the body, no quality loss —
   a 10-minute 600 MB file takes seconds instead of ~15 minutes.
   See scratchpad pattern `prepend_cover.py` (founder story, 18-Aug-2026).
6. X limit: 140s. Longer episodes need a separate sub-140s cut (sort CUTS by
   time; keeps() asserts non-overlap after the scrambled-render bug).
