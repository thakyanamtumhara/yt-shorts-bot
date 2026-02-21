# CLAUDE.md

## Project Overview

Automated YouTube Shorts generator and uploader for Sale91.com (BulkPlainTshirt.com), a B2B plain t-shirt manufacturer. The bot runs a full pipeline: topic selection → script writing → voice generation → video creation → YouTube upload, with optional Instagram Reels cross-posting.

## Key Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the full generation pipeline
python daily_short.py

# Generate YouTube OAuth token (run locally, not in CI)
python generate_token.py

# Retry a failed upload from saved artifacts
python retry_upload.py
```

### Test/Debug Modes

```bash
TEST_MODE=1 python daily_short.py    # Skip Veo API calls + YouTube upload (free)
SKIP_CLIPS=1 python daily_short.py   # Skip Veo clips but still upload
```

## System Dependencies

These must be installed on the system (handled by GitHub Actions in CI):
- `ffmpeg` — video encoding
- `imagemagick` — image processing
- `python3.12`
- `fonts-noto-core` — font support for subtitles

## Required Environment Variables

```
ANTHROPIC_API_KEY            # Claude API (script writing + quality gates)
OPENAI_API_KEY               # OpenAI (TTS fallback + Whisper transcription)
GOOGLE_API_KEY               # Google Veo 3.1 (video clip generation)
OAUTHLIB_INSECURE_TRANSPORT=1
```

### Optional Environment Variables

```
ELEVENLABS_API_KEY           # ElevenLabs TTS (primary voice; falls back to OpenAI)
REPLICATE_API_TOKEN          # AI background music generation
INSTAGRAM_ACCESS_TOKEN       # Instagram Reels cross-posting
INSTAGRAM_BUSINESS_ID        # Instagram Business Account ID
WORK_DIR                     # Working directory (default: /tmp/yt_shorts)
```

## Architecture

The project is a single-script pipeline (`daily_short.py`, ~2800 lines) with supporting utilities.

### Pipeline Flow

1. **Topic Selection** — Smart pick from 110-topic bank + Claude-generated trending topics, with quality gate
2. **Script Generation** — Claude Sonnet writes Hinglish voiceover script + metadata, reviewed via quality gate (score ≥25/50)
3. **Voice Synthesis** — ElevenLabs TTS (primary) → OpenAI fallback
4. **Video Clips** — Google Veo 3.1 generates 5 × 8s clips per video
5. **Subtitles** — Whisper transcription with word-level timing + keyword highlighting
6. **Audio Mixing** — Voice + hook SFX + background music with dynamic volume curve
7. **Video Assembly** — Clips + subtitles + watermark + CTA overlay + Ken Burns effect
8. **Thumbnail** — Frame extraction + AI text overlay
9. **YouTube Upload** — Scheduled publish with auto-pinned comment + playlist organization
10. **Instagram Cross-Post** — Optional Reels upload via Graph API
11. **Analytics** — Cost tracking, engagement feedback loop (48h check)

### Key Patterns

- **Multi-provider fallbacks**: ElevenLabs → OpenAI for TTS; Replicate → local files for music
- **Quality gates**: Claude reviews its own generated scripts and topics before proceeding
- **Configuration-driven**: All tuning constants at top of `daily_short.py` (lines 47–449)

## CI/CD

Two GitHub Actions workflows in `.github/workflows/`:

- **`daily_short.yml`** — Runs daily at 12:00 UTC via cron. Supports `test_mode` and `skip_clips` workflow dispatch inputs. Saves artifacts on failure for retry.
- **`retry_upload.yml`** — Manual trigger to retry failed uploads from saved artifacts.

## File Structure

```
daily_short.py           # Main pipeline script (all core logic)
generate_token.py        # YouTube OAuth credential generator
retry_upload.py          # Upload retry utility
requirements.txt         # Python dependencies
topic_history.json       # Tracks previously used topics (committed)
.github/workflows/       # CI/CD workflows
bg_music/                # Background music directory
```

## Testing

No automated test framework. Quality is enforced via:
- Claude-based script review (5-dimension scoring with retry)
- Claude-based topic quality validation
- `TEST_MODE=1` for free dry runs without API costs

## Linting/Formatting

No linter or formatter is configured.
