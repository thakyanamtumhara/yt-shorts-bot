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
5. Prepend cover for 0.3s (concat pattern in git log / memory) — thumbnails on
   YT/FB/LI/X are picked from the first frames by Ketu in the wizard.
6. X limit: 140s. Longer episodes need a separate sub-140s cut (sort CUTS by
   time; keeps() asserts non-overlap after the scrambled-render bug).
