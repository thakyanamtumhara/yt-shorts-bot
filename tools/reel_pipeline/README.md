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
   Phone-mic audio chain: highpass+afftdn+loudnorm. Recorder audio: loudnorm only.
4. `cover.py` — house cover (Baloo2+RAQM, whole-string draws). BAND_C 0.50 for
   godam-wide framing, ~0.70 for selfie framing (face fills the frame).
5. **Prepend cover for a FULL 1.0 SECOND — never 0.2/0.3/0.5s.** Ketu has called
   this out twice. He sets the YouTube thumbnail by dragging in the frame picker;
   a third of a second is too small a target to land on, so he ends up with a
   random frame of himself mid-word. One second means any scrub inside it hits
   the cover.
   Which output needs it:
     * YouTube / any file HE uploads by hand -> 1.0s, mandatory. This is the
       one that actually matters and it is the one that got missed.
     * Vizard auto-posts (FB / X / LinkedIn) -> the platform takes frame 0, so
       frame 0 must be the cover; 1.0s satisfies this too.
     * Instagram (our Meta queue) -> sets `cover_url` via API, so the baked
       cover is redundant there and only eats into the hook.
   Cheap way to do it on a long file: encode a 1.0s still matched to the main
   file's codec/profile/level/pix_fmt/timescale + silent stereo AAC, then
   `ffmpeg -f concat -c copy`. No re-encode of the body, no quality loss —
   a 10-minute 600 MB file takes seconds instead of ~15 minutes.
   See scratchpad pattern `prepend_cover.py` (founder story, 18-Aug-2026).
6. X limit: 140s. Longer episodes need a separate sub-140s cut (sort CUTS by
   time; keeps() asserts non-overlap after the scrambled-render bug).
