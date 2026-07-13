#!/usr/bin/env python3
"""
Sale91.com — Daily YouTube Shorts Generator + Uploader
Runs standalone (no Colab needed). Use with GitHub Actions for full automation.

Usage:
  python daily_short.py

Required environment variables:
  ANTHROPIC_API_KEY
  OPENAI_API_KEY
  GOOGLE_API_KEY
  OAUTHLIB_INSECURE_TRANSPORT=1

Optional environment variables:
  ELEVENLABS_API_KEY  — ElevenLabs TTS (primary voice; falls back to OpenAI if missing)
  REPLICATE_API_TOKEN — Replicate token for AI background music generation
  FAL_KEY             — fal.ai API key for Kling video fallback + FLUX image fallback
"""

import anthropic
import requests
import json
import random
import os
import sys
import glob
import math
import re
import time
import pytz
from datetime import datetime, timedelta

from openai import OpenAI
# Fix Pillow 10+ compatibility with MoviePy
import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS
from moviepy.editor import (
    VideoFileClip, AudioFileClip, TextClip, ImageClip,
    CompositeVideoClip, concatenate_videoclips, ColorClip,
    CompositeAudioClip, concatenate_audioclips
)
from moviepy.audio.fx.audio_loop import audio_loop
from moviepy.audio.fx.volumex import volumex

# Allow http localhost for OAuth
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# ═══════════════════════════════════════════════════════════════════════
# Working directory (GitHub Actions uses /tmp)
# ═══════════════════════════════════════════════════════════════════════
WORK_DIR = "/tmp/yt_shorts"
os.makedirs(WORK_DIR, exist_ok=True)
os.makedirs(f"{WORK_DIR}/bg_music", exist_ok=True)
os.makedirs(f"{WORK_DIR}/my_clips", exist_ok=True)

# ╔══════════════════════════════════════════════════════════════════════╗
# ║                   BUSINESS CONTEXT                                   ║
# ╚══════════════════════════════════════════════════════════════════════╝

BUSINESS_CONTEXT = """
BRAND: Sale91.com (also known as BulkPlainTshirt.com)
TAGLINE: "Own Knitted Blank Wears"
ORDER WEBSITE: Sale91.com (always refer to this for ordering)

WHAT WE DO:
- B2B plain/blank t-shirt MANUFACTURER & supplier
- We KNIT OUR OWN FABRIC in-house (not a trader/reseller)
- We sell to custom printing businesses (DTG, DTF, Screen print, Heat Transfer) PAN India
- We also EXPORT to other countries via courier or sea transport
- Manufacturing in Tiruppur (India's textile hub), Warehouse in Delhi (Khanpur, South Delhi)
- 1,25,232+ pieces sold in last 30 days

PRODUCTS WE MAKE:
- Plain Round Neck T-shirts (180, 200, 210, 220 GSM)
- Oversized T-shirts
- Plain Polo T-shirts
- Plain Hoodies & Sweatshirts (240, 320, 430 GSM)
- Acid Wash T-shirts (regular & oversized)
- All products: 100% Cotton, Bio-washed, Pre-shrunk, Combed/Ring-spun Cotton

PRICING & OFFERS:
- Rs 2/pc discount for 500+ quantity orders
- Rs 3/pc online purchase discount for any quantity
- 50% COD available on first order for new buyers (+3% COD charge)
- From second order: prepaid

KEY FACTS:
- 1 lakh+ t-shirts ready stock at any time
- GSM: 180 for everyday wear, 200 for premium, 220 for heavy premium
- All shirts are Bio-washed (enzyme-treated for smoothness) and Pre-shrunk
- Ring-spun Combed Cotton (premium yarn, softer feel)
- Available in 15+ colors
- MOQ: as low as 10 pieces for ready stock items
"""

# ╔══════════════════════════════════════════════════════════════════════╗
# ║                   VIDEO SETTINGS                                     ║
# ╚══════════════════════════════════════════════════════════════════════╝

# Test mode: skip expensive Veo clips, use placeholder video. Set TEST_MODE=1 to enable.
TEST_MODE = os.environ.get("TEST_MODE", "").strip() in ("1", "true", "yes")
# Skip clips mode: use placeholder clips but still run everything else (including YouTube upload).
SKIP_CLIPS = os.environ.get("SKIP_CLIPS", "").strip() in ("1", "true", "yes")
# Single Veo test: generate only 1 real Veo clip (clip #1), remaining 4 use blank placeholders.
# Full pipeline runs (upload, Instagram etc.) — saves ~80% Veo cost while testing end-to-end.
SINGLE_VEO_TEST = os.environ.get("SINGLE_VEO_TEST", "").strip() in ("1", "true", "yes")
# New Test Mode: skip Veo clips (placeholders) but run full pipeline (upload, Instagram, blog, etc.)
# Same as main production flow minus the expensive video generation.
NEW_TEST_MODE = os.environ.get("NEW_TEST_MODE", "").strip() in ("1", "true", "yes")

# Script quality gate: Claude reviews its own script before proceeding
SCRIPT_MAX_ATTEMPTS = 5

# ── Sarvam TTS (Primary — Indian-native Hinglish) ──
# Bulbul v3 — 30+ voices, native ₹/digit pronunciation, code-switches naturally.
# Compatible v3 voices: rahul, amit, vijay, advait, aditya, ashutosh, rohan, dev,
#   varun, kabir, shubh, anand, tarun, mohit, soham (males)
#   ritu, priya, neha, pooja, simran, kavya, ishita, shreya, tanya, suhani (females)
SARVAM_MODEL = "bulbul:v3"
SARVAM_SPEAKER = os.environ.get("SARVAM_VOICE", "amit").strip() or "amit"
SARVAM_PACE = float(os.environ.get("SARVAM_PACE", "1.0"))     # 0.5-2.0
SARVAM_TEMPERATURE = float(os.environ.get("SARVAM_TEMP", "0.7"))
SARVAM_TARGET_LANG = "hi-IN"
SARVAM_SAMPLE_RATE = 22050

# ── ElevenLabs TTS (Fallback 1) ──
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "cejtKjfE9sHUZ1FnUYEV")  # Ketu Original (PVC clone)
# Set ELEVENLABS_MODEL=eleven_v3 to use the newest (more natural) model.
# Defaults to multilingual_v2 (stable, broad availability). v3 access is account-tier dependent.
ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")
# ElevenLabs Voice Library DEFAULT settings — what their web previews use.
# We match these so bot output sounds identical to the preview the user heard.
ELEVENLABS_VOICE_SETTINGS = {
    "stability": 0.50,        # was 0.62 — preview uses 0.50
    "similarity_boost": 0.75,
    "style": 0.00,            # was 0.22 — preview uses 0.00 (less artificial emphasis)
    "use_speaker_boost": True,
}

# ── OpenAI TTS (Fallback) ──
TARGET_VOICE = "ash"  # OpenAI TTS voice (try: ash, ballad, coral, echo, sage, verse)
VOICE_SPEED = 1.0
VOICE_INSTRUCTIONS = """You are an Indian man from Delhi speaking casual Hinglish.

PRONUNCIATION RULES (CRITICAL):
- You are a NATIVE HINDI speaker. Hindi words MUST sound fully native Indian, not anglicized.
- "hai" = "hai" (short, flat) — NOT "high" or "hay"
- "toh" = soft "toh" — NOT "toe"
- "matlab" = "mut-lub" — NOT "mat-lab"
- "hota hai" = quick natural "hota-hai" — NOT two separate English words
- "karo/karlo" = soft rolled 'r' — NOT hard English 'r'
- "dekho" = "deh-kho" with soft 'deh' — NOT "deck-oh"
- All Hindi connectors (toh, ki, ka, ke, mein, se, pe) should flow naturally, unstressed
- English words like "quality", "print", "GSM", "fabric" keep their English pronunciation
- The RHYTHM should be Hindi — not English rhythm with Hindi words inserted

SPEAKING STYLE:
- Speak like you're explaining something to a fellow businessman over chai
- Confident, knowledgeable, casual — NOT formal, NOT scripted, NOT like a narrator
- Medium pace, relaxed delivery — do NOT elongate or stretch any syllables
- Do NOT add "umm", "hmm", "aaaa" or any stretched filler sounds
- Keep pauses SHORT and natural — just a brief comma pause, nothing long
- Trail off naturally at the end of sentences"""
VIDEO_WIDTH, VIDEO_HEIGHT = 1080, 1920
FPS = 30
VEO_CLIPS_PER_VIDEO = 5
# Default: Fast (cheap, ~$1/clip 1080p). Set VEO_FULL=1 for cinematic full-quality (~$3.20/clip).
# Best-bang-for-buck middle path: VEO_HERO_FULL=1 → first clip uses full quality (the hook),
# remaining clips use Fast. Adds ~$2 per video for the only clip that matters for swipe rate.
VEO_MODEL = (
    "veo-3.1-generate-preview"
    if os.environ.get("VEO_FULL", "").strip() in ("1", "true", "yes")
    else "veo-3.1-fast-generate-preview"
)
_VEO_HERO_DEFAULT = "1"  # Default ON: hero clip uses full-quality Veo (~$2 extra/video)
VEO_HERO_FULL = (
    os.environ.get("VEO_HERO_FULL", _VEO_HERO_DEFAULT).strip().lower()
    not in ("0", "false", "no", "off")
)
VEO_HERO_MODEL = "veo-3.1-generate-preview"
VEO_ASPECT_RATIO = "9:16"
VEO_DURATION = 8
VEO_MAX_RETRIES = 3
VEO_RETRY_WAIT = 60
VEO_POLL_TIMEOUT = 300  # 5 min max wait per clip generation

# ── Kling Fallback (via fal.ai) ──
# Auto-activates when Veo rate-limits (429/RESOURCE_EXHAUSTED).
# Uses Kling v2.6 Pro for cost-effective fallback ($0.07/s, no audio).
# Set FAL_KEY env var to enable. Without it, Kling fallback is skipped.
KLING_ENABLED = bool(os.environ.get("FAL_KEY", ""))  # Re-enabled: fal.ai account active again
KLING_MODEL = "fal-ai/kling-video/v2.6/pro/text-to-video"
KLING_DURATION = "5"  # "5" or "10" seconds (5s = $0.35/clip, 10s = $0.70/clip)
KLING_ASPECT_RATIO = "9:16"
KLING_MAX_RETRIES = 3
KLING_NEGATIVE_PROMPT = "blur, distort, low quality, text, watermark, face, human face"

# Subtitles
ADD_SUBTITLES = os.environ.get("ADD_SUBTITLES", "0").strip() in ("1", "true", "yes")
SUBTITLE_FONT = "Noto-Sans-Bold"
SUBTITLE_FONTSIZE = 62
SUBTITLE_COLOR = "white"
SUBTITLE_STROKE = "black"
SUBTITLE_STROKE_W = 2
SUBTITLE_BG_COLOR = (0, 0, 0)
SUBTITLE_BG_OPACITY = 0.7
SUBTITLE_BG_PADDING = 16
WORDS_PER_SUBTITLE = 3        # Punchy 3-word phrases (top-quartile Shorts pace)
MAX_SUBTITLE_DURATION = 1.8   # Force caption swap at least every 1.8s
# Karaoke captions — word-by-word highlight of the ACTUAL spoken Roman-Hinglish words.
# KARAOKE_CAPTIONS=0 is the kill switch (falls back to old segment-level English captions).
KARAOKE_CAPTIONS = os.environ.get("KARAOKE_CAPTIONS", "1").strip() in ("1", "true", "yes")
KARAOKE_FONTSIZE = 64
KARAOKE_BASE_COLOR = (255, 255, 255, 255)       # white
KARAOKE_HIGHLIGHT_COLOR = (255, 215, 0, 255)    # #FFD700 yellow — CVD-safe, no red/green
KARAOKE_STROKE_COLOR = (0, 0, 0, 255)
KARAOKE_STROKE_W = 4
KARAOKE_Y_PERCENT = 0.55      # line vertical center — inside IG Reels safe band (~25-70%)
KARAOKE_MAX_LINE_W = VIDEO_WIDTH - 160
KARAOKE_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",        # CI: fonts-noto-core
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",    # ubuntu preinstalled
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",     # CI: fonts-freefont-ttf
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",       # mac local runs
]
# Keywords that get highlighted in yellow for visual emphasis
SUBTITLE_HIGHLIGHT_COLOR = "yellow"
SUBTITLE_HIGHLIGHT_WORDS = {
    "gsm", "fabric", "cotton", "biowash", "preshrunk", "pre-shrunk", "shrinkage",
    "combed", "carded", "ring-spun", "ringspun", "printing", "print", "dtg", "dtf",
    "screen", "quality", "weight", "200", "180", "220", "160", "430", "240", "320",
    "210", "250", "280", "300",
    "pilling", "dyeing", "collar", "ribbing", "rib", "yarn", "knit", "interlock",
    "jersey", "fleece", "terry", "pique", "mercerized", "organic", "polyester",
    "sublimation", "vinyl", "embroidery", "discharge", "plastisol", "mesh",
    "sample", "moq", "bulk", "wholesale", "premium", "acid wash",
    # Manufacturing & process terms
    "stitching", "cutting", "knitting", "carding", "combing", "finishing",
    "tiruppur", "delhi", "manufacturer", "factory", "warehouse",
    # Business terms
    "order", "price", "profit", "margin", "cost", "business", "customer",
    # Product types
    "oversized", "polo", "hoodie", "sweatshirt", "roundneck", "vneck",
}

# Watermark Badge (small one-side tag, avoids YouTube Shorts UI)
ADD_WATERMARK = True
WATERMARK_TEXT = "Sale91.com"
WATERMARK_FONT_SIZE = 24
WATERMARK_OPACITY = 0.70
WATERMARK_PADDING_H = 12   # Horizontal padding inside badge
WATERMARK_PADDING_V = 6    # Vertical padding inside badge
WATERMARK_MARGIN_X = 30    # Distance from left edge
WATERMARK_Y_PERCENT = 0.17 # 17% from top — below YT channel name, above subtitles

# Background Music
ADD_BG_MUSIC = True
BG_MUSIC_FOLDER = f"{WORK_DIR}/bg_music"
BG_MUSIC_VOLUME = 0.08
# Dynamic music volume: louder at start/end for energy, quieter in middle for voice clarity
BG_MUSIC_VOLUME_START = 0.15   # First 2 seconds — energy at hook
BG_MUSIC_VOLUME_MID = 0.05    # Middle — voice dominant
BG_MUSIC_VOLUME_END = 0.12    # Last 3 seconds — emotional close

# Veo Ambient Audio (disabled — we generate video-only clips to save ~33% on Veo cost)
VEO_AMBIENT_VOLUME = 0

# Hook Sound Effect (low bass drop at video start to stop the scroll)
ADD_HOOK_SFX = True
HOOK_SFX_VOLUME = 0.25

# Transition Whoosh SFX (short whoosh at each clip boundary — adds cuts/min punch)
ADD_TRANSITION_SFX = True
TRANSITION_SFX_VOLUME = 0.18

# Hook Text (scroll-stopping overlay on first frame)
ADD_HOOK_TEXT = True
HOOK_DURATION = 2.0  # 2 seconds for better scroll-stop impact

# Transitions
CLIP_FADE_DURATION = 0.3

# CTA
ADD_CTA_OVERLAY = True
CTA_TEXT = "Sale91.com — MOQ sirf 10 pieces"

# YouTube
SCHEDULE_PUBLISH = True
TIMEZONE = "Asia/Kolkata"
UPLOAD_AS_SHORT = True

# Publish slots — labels kept stable; 21:30 retired from rotation but left in
# the list so historical analytics slot-mapping keeps bucketing old videos.
# Each slot: (hour, minute, label)
PUBLISH_SLOTS = [
    (21, 30, "9:30 PM"),   # RETIRED from rotation (median 31.5 views) — analytics bucket only
    (11,  0, "11:00 AM"),  # Small A/B arm — chai break / office downtime
    (19,  0, "7:00 PM"),   # WINNER — median 79 views (owner data, Jul 2026)
]
# Rotation: 19:00 IST dominant (owner data: 79 vs 31.5 median views);
# Wednesday keeps the 11:00 A/B arm for continued signal.
PUBLISH_SLOT_SCHEDULE = {
    0: 2,  # Monday    → 7:00 PM
    1: 2,  # Tuesday   → 7:00 PM
    2: 1,  # Wednesday → 11:00 AM (A/B arm)
    3: 2,  # Thursday  → 7:00 PM
    4: 2,  # Friday    → 7:00 PM
    5: 2,  # Saturday  → 7:00 PM
    6: 2,  # Sunday    → 7:00 PM (unused — Sunday recap workflow)
}

# Files
TOPIC_HISTORY_FILE = "topic_history.json"  # In repo root for git tracking
CLIP_HISTORY_FILE = "clip_history.json"  # In repo root for git tracking
CLIENT_SECRETS_FILE = f"{WORK_DIR}/client_secret.json"
TOKEN_FILE = f"{WORK_DIR}/youtube_token.json"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # For comments + playlists
]

# Thumbnail
GENERATE_THUMBNAIL = True
THUMBNAIL_WIDTH = 1080
THUMBNAIL_HEIGHT = 1920  # Vertical for Shorts

# AI Thumbnail (Claude text strategy + Gemini TEXT-FREE scene; text is
# composited by our own PIL renderer, never rendered by the image model —
# diffusion models garble letters, worst of all Devanagari). 2026-07-13.
AI_THUMBNAIL = True
AI_THUMBNAIL_GEMINI_MODEL = "gemini-3-pro-image-preview"
AI_THUMBNAIL_GEMINI_FALLBACK = "gemini-3.1-flash-image-preview"
THUMBNAIL_RESEARCH_FILE = "thumbnail_research.json"  # Weekly research cache
THUMBNAIL_RESEARCH_MAX_AGE_DAYS = 7

# ── Cover typography (his proven style: huge, one keyword yellow, heavy
# black outline + top/bottom darkening, all inside the 25-70% safe band) ──
_ASSETS_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")
# Baloo 2 (variable, wght→800) carries Latin + Devanagari + ₹ in ONE family.
COVER_FONT_BALOO = os.path.join(_ASSETS_FONT_DIR, "Baloo2.ttf")
COVER_FONT_DEVA = os.path.join(_ASSETS_FONT_DIR, "NotoSansDevanagari-Bold.ttf")
COVER_HL_YELLOW = (255, 212, 0)     # CVD-safe highlight (Ketu red-green CVD) + top-CTR
COVER_WHITE = (255, 255, 255)
COVER_BLACK = (0, 0, 0)
# Correct Devanagari shaping (ि-matra reorder, conjuncts) needs libraqm.
# Without it, Hindi garbles — so we fall back to Latin-only cover text.
try:
    from PIL import features as _pil_features
    COVER_RAQM = bool(_pil_features.check("raqm"))
except Exception:
    COVER_RAQM = False

# Cover-design metadata for the learning loop. Set by whichever cover path runs
# (generate_ai_thumbnail → "ai", generate_thumbnail → "pil"); read when saving
# the IG upload record so ig_engagement_history learns which covers work.
COVER_META = {"cover_text": None, "cover_color": None, "cover_path": None, "cover_face": None}

# Auto-Pin Comment — posts a CTA comment and pins it on every upload.
# Rotates daily between question-bait (comment-signal days) and Sale91-link nudges.
AUTO_PIN_COMMENT = True
PIN_TAIL_VARIANTS = [
    "🤔 Aapka next question kya hai? Comment mein puchho 👇",
    "💬 Aap kaunsa printing method use karte ho — DTF ya screen? Comment karo 👇",
    "📦 Bulk plain t-shirts chahiye? Sale91.com — MOQ sirf 10 pieces, Pan India",
    "🔥 Agla video kis topic pe banaye? Comment mein batao 👇",
    "🏭 Direct manufacturer se blanks lo → Sale91.com (khud ki knitting, biowashed)",
]

def get_pin_tail():
    """Day-rotated pinned-comment tail: 3 question-bait + 2 Sale91 nudges."""
    return PIN_TAIL_VARIANTS[datetime.now().timetuple().tm_yday % len(PIN_TAIL_VARIANTS)]

PIN_COMMENT_TEXT = """🤔 Aapka next question kya hai? Comment mein puchho 👇

📦 Plain t-shirt for printing? → Sale91.com (MOQ 10 pieces, Pan India)"""

# Auto-Playlist — organize videos into series playlists automatically
AUTO_PLAYLIST = True
PLAYLIST_CACHE_FILE = f"{WORK_DIR}/playlist_cache.json"  # Cache playlist IDs

# Instagram Reels Cross-Post (requires INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_BUSINESS_ID secrets)
CROSS_POST_INSTAGRAM = True
IG_API_VERSION = "v21.0"  # v22.0 causes "Carousel item cannot be published standalone" error

# Instagram Carousel — autonomous post of blog hero+img1+img2 to IG as a carousel.
# Fires at 4:30 UTC (10 AM IST) next morning, 16h after the daily Reel — spacing
# avoids IG flagging the account for two API posts in the same hour.
IG_CAROUSEL_DRAFTS_DIR = "ig_drafts"

# Cost Tracker — log per-video API costs
COST_TRACKER_FILE = "cost_tracker.json"  # In repo root for git tracking
DAILY_COST_LIMIT_USD = 10.0  # Circuit breaker: skip video if today's spend exceeds this

# Engagement Feedback Loop — check video performance after 48h
ENGAGEMENT_FILE = "engagement_history.json"  # In repo root for git tracking
ENGAGEMENT_CHECK_DELAY_HOURS = 48
NEW_CHANNEL_VIEWS_THRESHOLD = 100_000  # 1 lakh — use main channel data until new channel crosses this

# Instagram Engagement Feedback Loop — check Reel performance after 48h
IG_ENGAGEMENT_FILE = "ig_engagement_history.json"  # In repo root for git tracking
IG_ENGAGEMENT_CHECK_DELAY_HOURS = 48

# Source Channel — read engagement data from existing 50K channel to inform topic selection
SOURCE_CHANNEL_ID = os.environ.get("CHANNEL_ID_2", "")
SOURCE_CHANNEL_API_KEY = os.environ.get("YOUTUBE_API_KEY_1", "")
SOURCE_CHANNEL_CACHE_FILE = "source_channel_insights.json"  # In repo root for git tracking

# ╔══════════════════════════════════════════════════════════════════════╗
# ║                   TOPIC BANK                                         ║
# ╚══════════════════════════════════════════════════════════════════════╝

TOPIC_BANK = [
    # ── SERIES 1: Fabric & GSM Knowledge (20 topics) ──
    "GSM bas fabric ka weight hota hai — kaise check karein ghar pe",
    "180 GSM aur 220 GSM mein kya farq hai — printing ke baad dikta hai",
    "160 GSM tshirt pe print karega toh regret hoga — pata hai kyu?",
    "200 GSM sweet spot hai printing ke liye — na mota na patla",
    "GSM zyada matlab better quality? Galat hai — suno kyu",
    "Fabric weight ghar pe check karo — ek scissor aur weighing scale chahiye bas",
    "Single jersey aur interlock fabric — fark samjho warna galti hogi",
    "Fabric ki knitting tight hai ya loose — print quality isse decide hoti hai",
    "Pique fabric polo ke liye best hai — smooth fabric pe polo mat banao",
    "Fleece vs French terry hoodie blank — winter ke liye kaunsa better",
    "Rib fabric kahan use hota hai — collar cuff aur hem mein fark hai",
    "30s aur 40s yarn count ka matlab — patla aur mota fabric aise banta hai",
    "Slub cotton tshirt ka texture — ye defect nahi hai design hai",
    "Organic cotton aur regular cotton — printing business ke liye kya fark padta hai",
    "Bamboo cotton blend trending hai — par printing ke liye theek hai ya nahi",
    "Polyester blend tshirt pe DTG mat karo — ink absorb nahi hoti",
    "Tri-blend fabric kya hota hai — cotton polyester rayon ka mix",
    "Mercerized cotton ka lustre alag hota hai — premium feel instantly",
    "Supima cotton vs regular combed cotton — touch karke pata chal jayega",
    "Fabric ka shrinkage direction — length mein zyada hota hai width mein kam",

    # ── SERIES 2: Customer Stories & Incidents (20 topics) ──
    "Client ne return kiya? Pre-shrunk nahi tha shayad",
    "Ek customer aaya 500 piece cancel karwa diya — galti uski thi ya supplier ki?",
    "Customer bola print crack ho gaya — maine pucha kaunsa ink use kiya?",
    "Pehla order 50 piece tha ab monthly 5000 leta hai — kya kiya alag?",
    "Ek banda ne Rs 45 wali tshirt pe DTG kiya — result dekh ke ro diya",
    "Customer ne 5 supplier try kiye — phir humse kyu ruka? GSM consistency",
    "Wedding merch ka order aaya 200 piece — 3 din mein chahiye tha",
    "Customer bola collar yellow ho gaya — sweat stain tha ya fabric issue?",
    "Return hua kyunki color screen pe alag tha — ye common problem hai",
    "Ek customer ne white aur off-white confuse kar diya — 1000 piece ka order",
    "Client bola tshirt ka weight kam lag raha hai — GSM aur weight mein fark hai",
    "500 piece screen print order mein 20 piece waste hua — ye normal hai ya nahi",
    "Ek customer ne bina sample liye 2000 piece order kiya — kya hua phir?",
    "Client ne acid wash pe embroidery karwai — thread toota kyu? Texture issue",
    "Customer bola sizing galat hai — L size chhota lag raha hai — actual problem kya thi",
    "Ek startup ne 10 piece se shuru kiya — ab apna brand hai 50K monthly",
    "Festival season mein 2x order aata hai — stock kaise manage karein",
    "Customer ne black tshirt pe white DTF kiya — peeling kyu hui?",
    "Repeat customer ka order mix ho gaya — color code follow karna zaroori hai",
    "Ek banda export karta hai humse plain leke — shipping mein kya dikkat aati hai",

    # ── SERIES 3: Printing Methods Deep Dive (15 topics) ──
    "DTG DTF Screen — har method ke liye alag blank tshirt theek rehta hai",
    "DTG printing ke liye pre-treatment zaroori hai — bina kiye print dhul jayega",
    "DTF printing ka fayda — dark fabric pe bhi sharp colors aate hain",
    "Screen printing mein mesh count matter karta hai — detail ka game hai",
    "Heat transfer vinyl aur DTF mein fark — kaunsa business ke liye better",
    "White ink DTG pe costly hai — dark tshirt pe margin kam hoga",
    "Sublimation sirf polyester pe hota hai — cotton pe try mat karna",
    "Screen print mein spot colors aur CMYK — kab kaunsa use karo",
    "DTF film ka quality matter karta hai — saste film se print kharab hoga",
    "Discharge printing kya hai — bleach se design banta hai fabric pe",
    "Water based vs plastisol ink — feel aur durability mein fark hai",
    "All over print kaise hota hai — sublimation ya screen dono se ho sakta hai",
    "Embroidery blank ke liye 200+ GSM chahiye — patla fabric pe thread kheechta hai",
    "Puff print trending hai — 3D effect ke liye kaunsa fabric best hai",
    "Vinyl cut vs DTF — small orders ke liye kaunsa sasta padega",

    # ── SERIES 4: Business Tips & Mistakes (15 topics) ──
    "Pehla order dene se pehle 5 cheezein confirm kar lo supplier se",
    "10 piece se merch brand start ho sakta hai — high MOQ ki zaroorat nahi",
    "Naya printing business start karna hai? 3 galtiyan mat kariyega",
    "Pricing galat rakhi toh loss hoga — fabric plus print plus margin calculate karo",
    "Mockup accha dikhta hai par actual print alag — sample zaroori hai",
    "Bulk mein order karo toh rate kam milta hai — par pehle sample lo",
    "Printing business mein waste percentage rakho — 3 to 5% normal hai",
    "Instagram se customer aayega — par product quality se rukega",
    "COD doge toh returns badhenge — prepaid model better hai",
    "Apna brand banana hai? Pehle 100 piece becho bina brand ke",
    "Supplier change karne se pehle soch lo — consistency matter karti hai",
    "Tshirt business mein seasonal demand hoti hai — summer mein 3x sale hoti hai",
    "Freight cost calculate karo — door delivery mein margin khata hai",
    "Custom packaging se brand value badhti hai — par shuruat mein zaroor nahi",
    "B2B aur B2C pricing alag hoti hai — dono mat mix karo",

    # ── SERIES 5: Quality Checks & Testing (15 topics) ──
    "Biowash ka matlab acchi quality — roa nahi aata fabric mein",
    "Normal 2% shrinkage hota hai — ye common hai kuch nahi kar sakte",
    "Ring-spun aur open-end yarn — quality mein zameen aasmaan ka fark hai",
    "Biowash aur pre-shrunk mein fark hai — dono zaroori hain",
    "Collar 5 wash mein loose ho jaata hai? Collar ribbing ka scene samjho",
    "Cotton tshirt mein pilling kyu hoti hai — yarn quality se connection hai",
    "Tshirt ka color 2 wash mein fade ho gaya? Dyeing quality ka issue hai",
    "Combed aur carded cotton — touch karke fark samajh aa jayega",
    "Tshirt mein smell aa rahi hai? Dyeing ke baad washing properly nahi hui",
    "Fabric ka GSM check karne ka tarika — round cutter aur scale se",
    "Colorfastness test ghar pe karo — rubbing se pata chal jayega",
    "Seam strength kaise check karo — haath se kheench ke dekho",
    "Wash test karo print ke baad — 3 wash ke baad asli quality dikhti hai",
    "Fabric pe crease marks aa rahe hain — ye permanent hai ya jaayenge?",
    "Tshirt ka hand feel kaise judge karo — 3 cheezein check karo",

    # ── SERIES 6: Product & Style Knowledge (15 topics) ──
    "Rs 55 wali aur Rs 90 wali tshirt mein quality quality ka farq hota hai",
    "White tshirt pe dark print — fabric quality matter karti hai",
    "Ek tshirt ki actual cost kya hoti hai — fabric dyeing stitching biowash",
    "Acid wash oversized blank — printing business ke liye next trend hai",
    "Polo tshirt blanks — corporate orders ke liye best quality kaise pehchano",
    "430 GSM hoodie blank — winter mein demand sabse zyada isi ki hoti hai",
    "Side seam aur tubular tshirt — printing ke liye kaunsa better hai",
    "Oversized tshirt ka trend hai — GSM aur fit sahi choose kar lo",
    "Drop shoulder vs regular shoulder — fit mein fark dikhta hai",
    "Round neck vs V-neck — printing ke liye kaunsa better sell hota hai",
    "Crop top blanks ka demand badh raha hai — women's merch mein scope hai",
    "Raglan sleeve tshirt — sporty look ke liye trending hai",
    "Hoodie mein kangaroo pocket ya side pocket — style matter karta hai",
    "Sweatshirt vs hoodie blank — margin kismein zyada hai",
    "Henley collar tshirt — ye niche product hai par premium customer milta hai",

    # ── SERIES 7: Myth Busters (10 topics) ──
    "Myth: Imported tshirt better hoti hai — India ka Tiruppur world supply karta hai",
    "Myth: 100% cotton best hai — kuch cases mein blend better hota hai",
    "Myth: Zyada GSM matlab zyada quality — galat sochte ho tum",
    "Myth: Biowash sirf softness ke liye hai — nahi aur bhi fayda hai",
    "Myth: Black tshirt pe print nahi tikta — technique galat hai tumhari",
    "Myth: Cheap blank leke accha print kar do — final product bekar niklega",
    "Myth: Screen printing dead hai — volume orders mein sabse sasta aaj bhi",
    "Myth: Online supplier pe trust nahi kar sakte — sample mangao pehle",
    "Myth: Tshirt business easy hai — margins tight hain competition zyada hai",
    "Myth: Washing instructions koi nahi padhta — par return isi se hota hai",
]

# Base tags always included
BASE_TAGS = [
    "plain tshirt", "blank tshirt", "Sale91",
    "t-shirt manufacturer India", "wholesale tshirt",
]

# Series-specific tags — matched by keywords in the topic string
TOPIC_SERIES_TAGS = {
    "fabric_gsm": {
        "keywords": ["gsm", "fabric", "cotton", "yarn", "knit", "jersey", "fleece",
                      "interlock", "pique", "rib", "slub", "organic", "bamboo",
                      "polyester", "tri-blend", "mercerized", "supima", "shrinkage",
                      "weight"],
        "tags": ["fabric quality", "GSM explained", "cotton fabric", "textile knowledge",
                 "fabric weight", "tshirt fabric", "cotton tshirt"],
    },
    "customer_stories": {
        "keywords": ["customer", "client", "return", "cancel", "order", "complaint",
                      "repeat", "export", "startup"],
        "tags": ["customer story", "business lessons", "tshirt business India",
                 "printing business tips", "B2B tshirt"],
    },
    "printing_methods": {
        "keywords": ["dtg", "dtf", "screen print", "sublimation", "vinyl",
                      "embroidery", "discharge", "plastisol", "heat transfer",
                      "puff print", "ink", "mesh", "pre-treatment"],
        "tags": ["DTG printing", "DTF printing", "screen printing", "tshirt printing",
                 "printing methods", "custom printing", "print on demand"],
    },
    "business_tips": {
        "keywords": ["business", "pricing", "moq", "margin", "profit", "brand",
                      "supplier", "bulk", "freight", "packaging", "b2b", "b2c",
                      "seasonal", "instagram"],
        "tags": ["tshirt business", "printing business", "business tips India",
                 "small business", "merch business", "startup tips"],
    },
    "quality_checks": {
        "keywords": ["biowash", "pre-shrunk", "preshrunk", "pilling", "dyeing",
                      "colorfastness", "seam", "wash test", "crease", "hand feel",
                      "ring-spun", "combed", "carded", "quality"],
        "tags": ["quality check", "tshirt quality", "fabric testing",
                 "biowash tshirt", "cotton quality", "textile testing"],
    },
    "product_style": {
        "keywords": ["oversized", "polo", "hoodie", "sweatshirt", "acid wash",
                      "drop shoulder", "v-neck", "crop top", "raglan", "henley",
                      "round neck", "side seam"],
        "tags": ["oversized tshirt", "polo tshirt", "hoodie blank",
                 "tshirt styles", "blank apparel", "streetwear blanks"],
    },
    "myth_busters": {
        "keywords": ["myth", "galat", "sochte"],
        "tags": ["myth busted", "tshirt myths", "textile myths",
                 "printing myths", "fact check", "common mistakes"],
    },
}


def get_topic_tags(topic):
    """Return topic-specific tags by matching topic keywords against series."""
    topic_lower = topic.lower()
    matched_tags = list(BASE_TAGS)  # Always include base tags

    for series_name, series_data in TOPIC_SERIES_TAGS.items():
        for kw in series_data["keywords"]:
            if kw in topic_lower:
                for tag in series_data["tags"]:
                    if tag.lower() not in [t.lower() for t in matched_tags]:
                        matched_tags.append(tag)
                break  # One keyword match per series is enough

    return matched_tags


def sanitize_tags(tags):
    """Clean and validate tags for YouTube API (prevents invalidTags error).

    YouTube rules (enforced server-side; violations → 400 invalidTags):
    - Each tag must be a non-empty ASCII string ≤ 30 chars
    - 500-char total budget includes quote wrapping on multi-word tags
      (a tag with a space is counted as len + 2) and comma separators between tags
    - No angle brackets, quotes, or commas
    """
    import re as _re
    cleaned = []
    used_budget = 0
    seen = set()
    BUDGET = 450  # Safety margin under YouTube's 500-char hard limit
    for tag in tags:
        if not isinstance(tag, str):
            tag = str(tag)
        tag = tag.strip()
        tag = _re.sub(r'[^a-zA-Z0-9\s\-]', '', tag)
        tag = _re.sub(r'\s+', ' ', tag).strip()
        tag = tag[:30]                                # YouTube per-tag max
        if not tag or tag.lower() in seen:
            continue
        effective = len(tag) + (2 if ' ' in tag else 0)   # quote overhead
        if cleaned:
            effective += 1                                  # comma separator
        if used_budget + effective > BUDGET:
            continue
        seen.add(tag.lower())
        cleaned.append(tag)
        used_budget += effective
    return cleaned


def get_topic_hashtags(topic):
    """Generate topic-specific hashtags for YouTube description."""
    topic_lower = topic.lower()
    # Always present
    hashtags = ["#Sale91", "#PlainTshirt", "#TshirtManufacturer"]

    series_hashtags = {
        "fabric_gsm": ["#FabricQuality", "#GSM", "#CottonFabric", "#TextileKnowledge"],
        "customer_stories": ["#CustomerStory", "#BusinessLesson", "#TshirtBusiness"],
        "printing_methods": ["#DTGPrinting", "#DTFPrinting", "#ScreenPrinting", "#CustomPrinting"],
        "business_tips": ["#BusinessTips", "#PrintingBusiness", "#SmallBusiness", "#Startup"],
        "quality_checks": ["#QualityCheck", "#FabricTesting", "#Biowash", "#CottonQuality"],
        "product_style": ["#OversizedTshirt", "#PoloTshirt", "#HoodieBlank", "#Streetwear"],
        "myth_busters": ["#MythBusted", "#TshirtMyths", "#FactCheck", "#CommonMistakes"],
    }

    for series_name, series_data in TOPIC_SERIES_TAGS.items():
        for kw in series_data["keywords"]:
            if kw in topic_lower:
                hashtags.extend(series_hashtags.get(series_name, []))
                break

    # Deduplicate while keeping order
    seen = set()
    unique = []
    for h in hashtags:
        if h.lower() not in seen:
            seen.add(h.lower())
            unique.append(h)
    return unique


# Instagram-specific hashtag/SEO/CTA pools — IG rewards 3-5 targeted tags,
# a keyword-rich first caption line (IG search indexes captions), and an
# explicit send/save CTA. Kept separate from the YouTube pools above.
IG_NICHE_HASHTAGS = {
    "fabric_gsm": ["#fabricquality", "#cottontshirt", "#gsmfabric"],
    "customer_stories": ["#b2bindia", "#businesslessons", "#tshirtmanufacturer"],
    "printing_methods": ["#dtfprinting", "#screenprinting", "#customtshirts"],
    "business_tips": ["#printingbusiness", "#smallbusinessindia", "#tshirtbrand"],
    "quality_checks": ["#tshirtquality", "#biowash", "#cottonfabric"],
    "product_style": ["#oversizedtshirt", "#streetwearindia", "#blankapparel"],
    "myth_busters": ["#tshirtprinting", "#mythvsfact", "#printingtips"],
}
IG_DEFAULT_NICHE = ["#plaintshirt", "#tshirtmanufacturer", "#wholesaletshirt"]

IG_SEO_KEYWORDS = {
    "fabric_gsm": "cotton fabric GSM guide for t-shirt printing",
    "customer_stories": "B2B plain t-shirt wholesale India",
    "printing_methods": "DTF DTG screen printing on plain t-shirts",
    "business_tips": "t-shirt printing business tips India",
    "quality_checks": "biowash cotton t-shirt quality check",
    "product_style": "oversized polo hoodie blanks wholesale",
    "myth_busters": "t-shirt printing myths busted",
}
IG_DEFAULT_SEO = "plain t-shirt wholesale for printing business India"

IG_CTA_LINES = [
    "Us dost ko bhejo jo t-shirt business start kar raha hai 📩",
    "Save kar lo — printing business mein kaam aayega 📌",
    "Apne printing partner ko ye Reel share karo 🤝",
    "Comment mein batao — agla video kis topic pe banaye? 👇",
    "Us bande ko tag karo jo abhi bhi mehenge blanks kharid raha hai 😅",
]

def _match_topic_series(topic):
    """Return the first TOPIC_SERIES_TAGS series name matching the topic, or None."""
    topic_lower = topic.lower()
    for series_name, series_data in TOPIC_SERIES_TAGS.items():
        for kw in series_data["keywords"]:
            if kw in topic_lower:
                return series_name
    return None

def get_ig_hashtags(topic):
    """3-5 targeted IG hashtags: 1 broad + 3 niche (per topic) + brand."""
    series = _match_topic_series(topic)
    niche = IG_NICHE_HASHTAGS.get(series, IG_DEFAULT_NICHE)
    return ["#tshirtbusiness"] + niche[:3] + ["#sale91"]

def get_ig_seo_line(topic, title):
    """Keyword-rich first caption line — IG search indexes caption text."""
    series = _match_topic_series(topic)
    kw = IG_SEO_KEYWORDS.get(series, IG_DEFAULT_SEO)
    line = f"{title} | {kw}"
    return line[:150]

def get_ig_cta_line():
    """Rotate send/save CTA deterministically by day of year."""
    return IG_CTA_LINES[datetime.now().timetuple().tm_yday % len(IG_CTA_LINES)]


# ═══════════════════════════════════════════════════════════════════════
# THUMBNAIL GENERATION
# ═══════════════════════════════════════════════════════════════════════

def _has_deva(s):
    return any("ऀ" <= c <= "ॿ" for c in (s or ""))


def _cover_font(size):
    """Baloo 2 @ weight 800 with RAQM shaping when available (needed for
    correct Devanagari). Falls back to bundled Noto Devanagari, then to a
    system font. Latin/number covers never need RAQM."""
    from PIL import ImageFont
    layout = getattr(ImageFont, "Layout", None)
    engine = (layout.RAQM if (COVER_RAQM and layout) else
              (layout.BASIC if layout else None))
    for path in (COVER_FONT_BALOO, COVER_FONT_DEVA):
        if os.path.exists(path):
            try:
                f = (ImageFont.truetype(path, size, layout_engine=engine)
                     if engine is not None else ImageFont.truetype(path, size))
                try:
                    f.set_variation_by_axes([800])   # Baloo variable → ExtraBold
                except Exception:
                    pass
                return f
            except Exception:
                continue
    for sysf in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/System/Library/Fonts/Supplemental/Arial Black.ttf"):
        if os.path.exists(sysf):
            return ImageFont.truetype(sysf, size)
    return ImageFont.load_default()


def _cover_line_width(draw, s, size):
    f = _cover_font(size)
    return int(draw.textlength(s, font=f)), f


def _cover_fit_size(draw, s, target_w, start, minsz=54, step=6):
    size = start
    while size > minsz:
        w, _ = _cover_line_width(draw, s, size)
        if w <= target_w:
            break
        size -= step
    return size


def _cover_darken(img, top=210, bottom=215):
    """Darken the top ~44% and bottom ~40% so text reads over any scene while
    the vivid middle (the hero) stays bright."""
    from PIL import Image, ImageDraw
    W, H = img.size
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for y in range(H):
        if y < H * 0.44:
            a = int(top * (1 - y / (H * 0.44)))
        elif y > H * 0.60:
            a = int(bottom * ((y - H * 0.60) / (H * 0.40)))
        else:
            a = 0
        d.line([(0, y), (W, y)], fill=(0, 0, 0, max(0, min(255, a))))
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")


def _cover_draw_line(draw, x, y, s, size, fill, ow):
    """One line: hard drop-shadow + thick circular black outline + fill."""
    f = _cover_font(size)
    draw.text((x + ow + 5, y + ow + 7), s, font=f, fill=COVER_BLACK)  # shadow
    for dx in range(-ow, ow + 1):
        for dy in range(-ow, ow + 1):
            if dx * dx + dy * dy <= ow * ow:
                draw.text((x + dx, y + dy), s, font=f, fill=COVER_BLACK)
    draw.text((x, y), s, font=f, fill=fill)


def compose_cover_text(base_img, lines, highlight=None, output_path=None):
    """THE single cover-text renderer (used by AI + fallback paths). Draws
    1-2 huge lines centred in the 25-70% safe band, one token highlighted
    yellow, heavy black outline + shadow + top/bottom darkening. Text is
    razor-sharp PIL — it can never garble or crop (auto-fit guarantees fit).

    lines: list of 1-2 short strings (already split).
    highlight: exact substring to colour yellow (e.g. "₹60"); rest is white.
    Returns output_path or None.
    """
    from PIL import Image, ImageDraw
    try:
        W, H = THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT
        img = base_img.convert("RGB").resize((W, H), Image.LANCZOS)
        img = _cover_darken(img)
        draw = ImageDraw.Draw(img)

        lines = [l.strip() for l in lines if l and l.strip()][:2]
        if not lines:
            return None
        safe_w = int(W * 0.88)
        safe_top, safe_bot = int(H * 0.26), int(H * 0.70)

        sized = []
        for i, ln in enumerate(lines):
            is_hero = (highlight and highlight in ln) or (len(lines) == 1) or (i == len(lines) - 1)
            start = 300 if is_hero else 180
            size = _cover_fit_size(draw, ln, safe_w, start)
            w, f = _cover_line_width(draw, ln, size)
            asc, desc = f.getmetrics()
            sized.append({"text": ln, "size": size, "w": w, "h": asc + desc})

        gap = 24
        block_h = sum(s["h"] for s in sized) + gap * (len(sized) - 1)
        y = max(safe_top, (safe_top + safe_bot) // 2 - block_h // 2)

        for s in sized:
            ln, size, w = s["text"], s["size"], s["w"]
            x = (W - w) // 2
            ow = max(6, int(size * 0.075))
            hl = highlight if (highlight and highlight in ln) else None
            if hl:
                idx = ln.index(hl)
                parts = [(ln[:idx], COVER_WHITE), (hl, COVER_HL_YELLOW),
                         (ln[idx + len(hl):], COVER_WHITE)]
                cx = x
                for txt, col in parts:
                    if not txt:
                        continue
                    _cover_draw_line(draw, cx, y, txt, size, col, ow)
                    cx += int(draw.textlength(txt, font=_cover_font(size)))
            else:
                _cover_draw_line(draw, x, y, ln, size, COVER_WHITE, ow)
            y += s["h"] + gap

        if output_path is None:
            output_path = f"{WORK_DIR}/thumbnail_{random.randint(100,999)}.png"
        img.save(output_path, "PNG")
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
            return None
        return output_path
    except Exception as e:
        print(f"   ⚠️ compose_cover_text failed: {e}")
        return None


def _cover_lines_from_text(text):
    """Turn a brief's short thumbnail text into (lines, highlight).
    Accepts a pipe '|' as an explicit line break; else splits ~half by words.
    Highlight = the ₹-number/percent token if present, else None."""
    import re as _re
    text = (text or "").strip()
    if not text:
        return [], None
    if "|" in text:
        lines = [p.strip() for p in text.split("|") if p.strip()][:2]
    else:
        words = text.split()
        if len(words) <= 2:
            lines = [text]
        else:
            cut = len(words) // 2
            for i, w in enumerate(words):
                if "₹" in w or _re.search(r"\d", w):
                    if 0 < i < len(words):
                        cut = i
                        break
            lines = [" ".join(words[:cut]).strip(), " ".join(words[cut:]).strip()]
            lines = [l for l in lines if l][:2]
    m = _re.search(r"₹[\d,]+|\b\d[\d,]*%?\b", text)
    highlight = m.group(0) if (m and any(m.group(0) in l for l in lines)) else None
    return lines, highlight


def _thumbnail_background(veo_clip_path, enhance=True):
    """Return a 1080x1920 PIL background for the cover: the most visually
    striking frame from the first Veo clip, else a dark gradient. No text."""
    from PIL import Image, ImageDraw, ImageEnhance
    import numpy as np
    W, H = THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT
    if veo_clip_path and os.path.exists(veo_clip_path):
        try:
            clip = VideoFileClip(veo_clip_path)
            best_frame, best_score = None, -1
            for t_pct in (0.25, 0.40, 0.50, 0.60):
                t = min(t_pct * clip.duration, clip.duration - 0.1)
                frame = clip.get_frame(t)
                score = float(np.std(frame))
                if score > best_score:
                    best_score, best_frame = score, frame
            clip.close()
            if best_frame is not None:
                img = Image.fromarray(best_frame).resize((W, H), Image.LANCZOS)
                if enhance:
                    img = ImageEnhance.Contrast(ImageEnhance.Brightness(img).enhance(0.94)).enhance(1.2)
                print(f"   \U0001F5BC\uFE0F Thumbnail bg: Veo frame (contrast {best_score:.0f})")
                return img
        except Exception as e:
            print(f"   \u26A0\uFE0F Veo frame extraction failed: {e}, using gradient")
    img = Image.new("RGB", (W, H), (16, 16, 26))
    d = ImageDraw.Draw(img)
    for y in range(H):
        r = y / H
        d.line([(0, y), (W, y)], fill=(int(16 + 55 * r), int(16 + 8 * r), int(30 + 20 * (1 - r))))
    return img


def generate_thumbnail(hook_text, topic, output_path=None, veo_clip_path=None,
                       cover_text=None, highlight=None):
    """Fallback cover: striking Veo frame (or gradient) + our crisp PIL text
    layer (compose_cover_text). Big, one keyword yellow, heavy outline, always
    inside the safe band. Returns the PNG path or None."""
    if not GENERATE_THUMBNAIL:
        return None
    try:
        bg = _thumbnail_background(veo_clip_path)
        text = cover_text or hook_text or (topic.split("\u2014")[0] if topic else "")
        # Latin-safe guard: without RAQM, Devanagari garbles \u2192 drop Hindi
        # words entirely and tidy orphaned punctuation rather than ship broken
        # Hindi. (On CI, RAQM is present, so this rarely triggers.)
        if _has_deva(text) and not COVER_RAQM:
            import re as _re
            text = _re.sub(r"[\u0900-\u097F\u200C\u200D\u093C]+[\?\u0964!.]*", "", text)
            text = _re.sub(r"\s{2,}", " ", text).strip(" -\u2014|?.\u0964")
            if not text:
                text = _re.sub(r"[\u0900-\u097F]", "", (topic or "")).strip() or "SAME GSM"
        lines, hl = _cover_lines_from_text(text)
        if highlight and any(highlight in l for l in lines):
            hl = highlight
        path = compose_cover_text(bg, lines, hl, output_path)
        if path:
            COVER_META.update({
                "cover_text": " ".join(lines), "cover_color": "#FFD400 keyword + #FFFFFF",
                "cover_path": "pil", "cover_face": False,
            })
            print(f"   \U0001F5BC\uFE0F Thumbnail generated: {os.path.basename(path)} | \"{' / '.join(lines)}\"")
        return path
    except Exception as e:
        print(f"   \u26A0\uFE0F Thumbnail generation failed: {e}")
        return None



def upload_thumbnail(youtube, video_id, thumbnail_path):
    """Upload custom thumbnail to a YouTube video."""
    if not os.path.exists(thumbnail_path):
        print(f"   ❌ Thumbnail file not found: {thumbnail_path}")
        return False
    file_size = os.path.getsize(thumbnail_path)
    if file_size < 1000:
        print(f"   ❌ Thumbnail file too small ({file_size} bytes), skipping upload")
        return False
    print(f"   🖼️ Uploading thumbnail: {thumbnail_path} ({file_size:,} bytes) for video {video_id}")
    from googleapiclient.http import MediaFileUpload
    import time as _time
    # Retry up to 3 times with delay — YouTube may need time to process the video
    for attempt in range(1, 4):
        try:
            media = MediaFileUpload(thumbnail_path, mimetype="image/png", resumable=True)
            response = youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
            print(f"   🖼️ Custom thumbnail uploaded for {video_id} (attempt {attempt})")
            print(f"   📋 Thumbnail API response: {response}")
            return True
        except Exception as e:
            print(f"   ⚠️ Thumbnail upload attempt {attempt}/3 failed: {e}")
            if attempt < 3:
                wait = attempt * 10  # 10s, 20s
                print(f"   ⏳ Waiting {wait}s before retry (YouTube may need time to process video)...")
                _time.sleep(wait)
    print(f"   ❌ Thumbnail upload failed after 3 attempts")
    print(f"   ℹ️ Note: Thumbnail upload requires YouTube channel verification")
    return False


def refresh_thumbnail_research(claude_client):
    """Refresh weekly thumbnail research cache. Returns cached research patterns dict.
    Calls Claude Opus to analyze top-performing thumbnail patterns for Indian wholesale t-shirt niche.
    Caches results in THUMBNAIL_RESEARCH_FILE; refreshes only if older than THUMBNAIL_RESEARCH_MAX_AGE_DAYS."""
    import json as _json
    from datetime import datetime as _datetime

    default_patterns = {
        "updated": "",
        "power_words": ["Secret", "Free", "Shocking", "Reality", "Truth", "Mistake", "Hack", "Asli", "Sach"],
        "best_colors": {"text": ["#FFD700", "#FFFFFF", "#FF0000", "#FF6600"], "stroke": "#000000"},
        "text_rules": "Max 3-4 words, Hinglish, include price/number if relevant, curiosity/urgency",
        "layout": "Text in the 25-70% vertical band for 9:16, face on one side text on other, rule of thirds",
        "patterns": "Bold block fonts, high contrast, yellow/white text on dark, price reveals get clicks",
    }

    # Check if cache is fresh
    if os.path.exists(THUMBNAIL_RESEARCH_FILE):
        try:
            with open(THUMBNAIL_RESEARCH_FILE, "r") as f:
                cached = _json.load(f)
            updated = cached.get("updated", "")
            if updated:
                age = (_datetime.now() - _datetime.fromisoformat(updated)).days
                if age < THUMBNAIL_RESEARCH_MAX_AGE_DAYS:
                    print(f"   📋 Thumbnail research cache fresh ({age}d old, max {THUMBNAIL_RESEARCH_MAX_AGE_DAYS}d)")
                    return cached
                print(f"   🔄 Thumbnail research cache stale ({age}d old), refreshing...")
        except Exception as e:
            print(f"   ⚠️ Failed to read research cache: {e}")

    # Generate fresh research via Claude Opus (best judgment for niche-specific patterns)
    try:
        print("   🔍 Generating thumbnail research patterns via Claude Opus...")
        # Include Instagram performance data (primary channel)
        ig_research_context = ""
        _ig = get_ig_engagement_summary()
        if _ig.get("total_reels_analyzed", 0) > 0:
            ig_research_context = (
                "\n\nINSTAGRAM REELS PERFORMANCE DATA (PRIMARY CHANNEL — optimize for this):\n"
                f"Reels analyzed: {_ig['total_reels_analyzed']}\n"
                f"Top Reels by views: {_json.dumps(_ig.get('top_reels', [])[:5], ensure_ascii=False)}\n"
                f"Highest save-rate Reels (= high quality content viewers bookmark): {_json.dumps(_ig.get('top_by_saves', [])[:3], ensure_ascii=False)}\n"
                f"Highest share-rate Reels (= viral content viewers spread): {_json.dumps(_ig.get('top_by_shares', [])[:3], ensure_ascii=False)}\n"
                "CRITICAL: Instagram is our primary channel. Optimize thumbnail patterns for INSTAGRAM FIRST, YouTube second.\n"
                "High save-rate = thumbnails that promise lasting value. High share-rate = thumbnails that trigger 'my friend needs to see this'.\n"
            )
        # Real cover outcomes — top/bottom 5 covers by views+saves, WITH their cover text,
        # so the brief generator learns from actual results instead of generic best practice.
        cover_learning_context = ""
        try:
            if os.path.exists(IG_ENGAGEMENT_FILE):
                with open(IG_ENGAGEMENT_FILE, "r") as _cf:
                    _cov_all = _json.load(_cf)
                _covers = [r for r in _cov_all if r.get("checked") and not r.get("check_failed") and r.get("cover_text")]
                if len(_covers) >= 4:
                    _covers.sort(key=lambda r: r.get("views", 0) + 25 * r.get("saves", 0), reverse=True)
                    _top = _covers[:5]
                    _bottom = [r for r in _covers[-5:] if r not in _top]

                    def _cov_line(r):
                        return (f'- "{r["cover_text"]}" (path={r.get("cover_path", "?")}, color={r.get("cover_color", "?")}, '
                                f'face={"yes" if r.get("cover_face") else "no"}) → '
                                f'{r.get("views", 0)} views, {r.get("saves", 0)} saves, {r.get("shares", 0)} shares')

                    cover_learning_context = (
                        "\n\nREAL COVER OUTCOMES (our own Reel covers with actual results — HIGHEST-VALUE signal):\n"
                        f"BEST covers (by views + saves):\n" + "\n".join(_cov_line(r) for r in _top) + "\n"
                    )
                    if _bottom:
                        cover_learning_context += "WORST covers:\n" + "\n".join(_cov_line(r) for r in _bottom) + "\n"
                    cover_learning_context += (
                        "Compare winning vs losing cover texts: word count, Hindi/English mix, numbers/prices, "
                        "curiosity angle, face vs product-hero. Derive power_words, text_rules and example_texts "
                        "from what ACTUALLY worked above, not generic best practice.\n"
                    )
        except Exception as _cov_e:
            print(f"   ⚠️ Cover-outcome context skipped: {_cov_e}")
        resp = claude_client.messages.create(
            model="claude-opus-4-6",
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": (
                    "You are a content strategist for an Indian wholesale bulk plain t-shirt business (Sale91.com). "
                    "Your job is to research what's performing well on Instagram Reels AND YouTube Shorts and identify high-CTR thumbnail patterns.\n\n"
                    "BUSINESS CONTEXT:\n"
                    "- Business: Wholesale/bulk plain t-shirts, B2B sales in India\n"
                    "- Target audience: Small business owners, retailers, resellers, bulk buyers in India\n"
                    "- Content style: Informational, business opportunity, pricing reveals, factory/warehouse tours\n"
                    "- Platform: Instagram Reels (PRIMARY) + YouTube Shorts (9:16 vertical)\n\n"
                    f"{ig_research_context}{cover_learning_context}\n"
                    "RESEARCH TASK:\n"
                    "Analyze top-performing Reels and Shorts in the Indian business niche. "
                    "Focus on: t-shirt business, wholesale business, bulk selling, garment industry, small business ideas India, low investment business. "
                    "Also analyze trending Reels in the broader 'business/money' niche in India for thumbnail inspiration.\n"
                    "For each pattern you identify, consider: what text is on the thumbnail, what colors are used, what emotions/expressions appear, what layout works.\n\n"
                    "IMPORTANT CONTEXT:\n"
                    "- These are VERTICAL 9:16 Reel/Shorts thumbnails\n"
                    "- Viewers see these as tiny previews on mobile phones while scrolling\n"
                    "- On Instagram: thumbnail appears as Reel cover on profile grid and Explore page\n"
                    "- Instagram grid-crop + YouTube UI cover the edges — text must live in the 25%-70% vertical band\n"
                    "- No brand names, URLs, or watermarks — ONLY the hook text goes on the thumbnail\n"
                    "- Think like a viewer scrolling on their phone — what makes them STOP and click?\n\n"
                    "Return a JSON object (no markdown fencing) with these fields:\n"
                    "- power_words: array of 10-15 Hindi/Hinglish power words that get clicks on Reels/Shorts (e.g., Secret, सच, Mistake, Free, Shocking, Reality, Truth, Hack)\n"
                    "- best_colors: object with 'text' (array of 4-5 hex codes — best: Yellow #FFD700, White #FFFFFF, Red #FF0000, Orange #FF6600) and 'stroke' (hex code for outline, usually black)\n"
                    "- text_rules: string summarizing best practices for Reels thumbnail text — max 3-4 words, Hinglish performs best, include numbers/prices, create curiosity/urgency (max 2 sentences)\n"
                    "- layout: string summarizing layout rules for 9:16 Reels thumbnails — face on one side text on other, rule of thirds, all text in the 25%-70% vertical band (max 2 sentences)\n"
                    "- patterns: string summarizing top-performing thumbnail patterns in Indian business Instagram/YouTube — what makes viewers stop scrolling (max 3 sentences)\n"
                    "- example_texts: array of 10 example thumbnail texts (3-4 words max each) that would work for t-shirt/wholesale business topics. Each must be a different approach — don't give variations of the same idea.\n"
                    "- ig_patterns: string summarizing what thumbnail text/design patterns correlate with high saves and shares on Instagram specifically (max 3 sentences)\n"
                )
            }],
        )
        research_text = resp.content[0].text.strip()
        # Parse JSON from response
        if research_text.startswith("```"):
            research_text = research_text.split("```")[1]
            if research_text.startswith("json"):
                research_text = research_text[4:]
        research = _json.loads(research_text)
        research["updated"] = _datetime.now().isoformat()

        with open(THUMBNAIL_RESEARCH_FILE, "w") as f:
            _json.dump(research, f, indent=2, ensure_ascii=False)
        print(f"   ✅ Thumbnail research cache refreshed")
        return research

    except Exception as e:
        print(f"   ⚠️ Research generation failed: {e}, using defaults")
        default_patterns["updated"] = _datetime.now().isoformat()
        try:
            with open(THUMBNAIL_RESEARCH_FILE, "w") as f:
                _json.dump(default_patterns, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
        return default_patterns


def generate_thumbnail_brief(claude_client, script_text, hook_text, topic, research_patterns,
                             source_insights=None, audience_qs=None, cost_tracker=None,
                             frame_image=None, ig_summary=None):
    """Generate a detailed thumbnail brief using Claude Opus (with vision if frame provided).
    Claude sees the reference image and generates a full descriptive brief that Gemini can execute.
    Returns a dict with 'brief_text' (full brief for Gemini) and structured fields for logging."""
    import json as _json
    import base64
    import io

    research_context = _json.dumps(research_patterns, indent=2, ensure_ascii=False)

    # Build YouTube insights context if available
    yt_context = ""
    if source_insights:
        yt_context += f"\nTOP PERFORMING VIDEOS IN THIS NICHE (use these to inform thumbnail text strategy):\n{_json.dumps(source_insights, ensure_ascii=False)}\n"
    if audience_qs:
        yt_context += f"\nREAL AUDIENCE QUESTIONS (what viewers actually care about — use to make thumbnail text resonate):\n{audience_qs}\n"

    # Build Instagram context (PRIMARY channel)
    ig_context = ""
    if ig_summary and ig_summary.get("total_reels_analyzed", 0) > 0:
        ig_context = (
            "\nINSTAGRAM PERFORMANCE DATA (PRIMARY CHANNEL — optimize thumbnail for this):\n"
            f"Top Reels by views: {_json.dumps(ig_summary.get('top_reels', [])[:5], ensure_ascii=False)}\n"
            f"Highest save-rate Reels (viewers SAVED these = high quality): {_json.dumps(ig_summary.get('top_by_saves', [])[:3], ensure_ascii=False)}\n"
            f"Highest share-rate Reels (viewers SHARED these = viral): {_json.dumps(ig_summary.get('top_by_shares', [])[:3], ensure_ascii=False)}\n"
            f"Avg save rate: {ig_summary.get('avg_metrics', {}).get('avg_save_rate', 'N/A')} | Avg share rate: {ig_summary.get('avg_metrics', {}).get('avg_share_rate', 'N/A')}\n"
            "\nINSTAGRAM-FIRST THUMBNAIL STRATEGY:\n"
            "- Thumbnails appear as Reel covers on Instagram profile grid and Explore page\n"
            "- High save-rate content = thumbnails promising LASTING VALUE (tips, secrets, methods)\n"
            "- High share-rate content = thumbnails triggering SOCIAL SHARING ('my friend needs this')\n"
            "- Design the thumbnail text to maximize saves + shares on Instagram\n"
        )

    prompt_text = (
        "You are a content strategist for an Indian wholesale bulk plain t-shirt business (Sale91.com). "
        "Your job is to generate high-CTR thumbnail text and design briefs optimized for Instagram Reels (primary) and YouTube Shorts.\n\n"
        "BUSINESS CONTEXT:\n"
        "- Business: Wholesale/bulk plain t-shirts, B2B sales in India\n"
        "- Target audience: Small business owners, retailers, resellers, bulk buyers in India\n"
        "- Content style: Informational, business opportunity, pricing reveals, factory/warehouse tours\n"
        "- Format: Always Reel 9:16 (YouTube Shorts / Instagram Reels)\n\n"
        f"RESEARCH PATTERNS (what works in this niche):\n{research_context}\n\n"
        f"{yt_context}\n"
        f"{ig_context}\n"
        "TASK:\n"
        "You are given a reference frame from the video. Choose the COVER TEXT only. "
        "Our own renderer draws it huge and razor-sharp — you do NOT design or place it, "
        "just pick the words. Winning covers on THIS channel are 2 short lines, one number "
        "popped, e.g. 'PROFIT vs TURNOVER?', 'MY BUSINESS / STORY', '10rs Price Drop'.\n\n"
        "COVER TEXT RULES:\n"
        "- TWO lines, separated by a single '|'. TOTAL 2-4 words across both lines. Shorter = higher CTR.\n"
        "- Line 1 = short supporting phrase (e.g. 'SAME GSM'). Line 2 = the HERO line and MUST contain a\n"
        "  number or ₹-price (e.g. '₹60 फर्क?'). The number is what stops the scroll — always include one.\n"
        "- Keep the CONTRADICTION/curiosity that makes them click (e.g. 'same' + the '₹60' gap), not a flat label.\n"
        "- Hinglish is great, but at most ONE short Hindi power-word (सच / गलती / फर्क / क्यों / राज़). "
        "Everything else Latin/number. Never a full Hindi sentence.\n"
        "- Think: scrolling on a 4-inch phone — what makes them STOP?\n\n"
        "ALSO give a LATIN-SAFE version: the SAME hook with the Hindi word swapped for an English/number "
        "equivalent (used when Devanagari can't be shaped). e.g. 'SAME GSM | ₹60 फर्क?' → 'SAME GSM | ₹60 FARAK?'.\n\n"
        "OUTPUT FORMAT — return EXACTLY these lines:\n\n"
        "=== THUMBNAIL BRIEF ===\n"
        "Thumbnail Text: [line1 | line2 — 2-4 words total, line2 has a number]\n"
        "Thumbnail Text (Latin-safe): [same hook, no Devanagari]\n"
        "Text Color: [hex] ([name])\n"
        "Face In Design: [Yes / No — Yes only for a customer-story/reaction video]\n"
        "=== END BRIEF ===\n\n"
        "IMPORTANT:\n"
        "- Look at the reference image carefully and describe it accurately\n"
        "- Give SPECIFIC placement instructions based on what you see (not generic)\n"
        "- Best performing text colors for Indian YouTube: Yellow (#FFD700), White (#FFFFFF), Red (#FF0000), Orange (#FF6600)\n"
        "- The designer will keep the base image EXACTLY as-is and only add text on top\n"
        "- Text + any face must live in the 25%-70% vertical band — that band survives BOTH YT Shorts UI AND\n"
        "  the Instagram profile-grid 4:5 crop. Nothing critical above 25% or below 70%.\n"
        "- FACE IS A JUDGMENT CALL: customer-story/reaction content benefits from a clear face making eye\n"
        "  contact; technical/specs/how-to content performs better with a bold product close-up as the hero.\n"
        "  State your decision in the 'Face In Design' field — the designer has detailed conditional face\n"
        "  rules and will execute your call.\n\n"
        f"TOPIC: {topic}\n"
        f"HOOK: {hook_text}\n"
        f"SCRIPT:\n{script_text[:1500]}\n"
    )

    # Build message content — with or without frame image
    message_content = []
    if frame_image is not None:
        try:
            from PIL import Image
            # Convert PIL image to base64 for Claude vision
            buf = io.BytesIO()
            frame_image.save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            message_content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": img_b64}
            })
            print("   👁️ Sending frame image to Claude for visual analysis...")
        except Exception as e:
            print(f"   ⚠️ Could not encode frame for Claude vision: {e}")
    message_content.append({"type": "text", "text": prompt_text})

    try:
        print("   🎨 Generating thumbnail brief via Claude Opus...")
        resp = claude_client.messages.create(
            model="claude-opus-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": message_content}],
        )

        if cost_tracker:
            cost_tracker.track_claude_call("opus", resp.usage.input_tokens, resp.usage.output_tokens)

        brief_text = resp.content[0].text.strip()

        thumb_text = ""
        thumb_latin = ""
        thumb_color = "#FFD400 (Gold Yellow)"
        thumb_face = False
        for line in brief_text.split("\n"):
            ls = line.strip()
            if ls.startswith("Thumbnail Text (Latin-safe):"):
                thumb_latin = ls.split(":", 1)[1].strip().strip('"').strip("[]")
            elif ls.startswith("Thumbnail Text:"):
                thumb_text = ls.split(":", 1)[1].strip().strip('"').strip("[]")
            elif ls.startswith("Text Color:"):
                thumb_color = ls.split(":", 1)[1].strip()
            elif ls.startswith("Face In Design:"):
                thumb_face = ls.split(":", 1)[1].strip().lower().startswith("y")

        if not thumb_text:
            # last-ditch: use the hook/topic; renderer + auto-fit handle length
            thumb_text = (hook_text or topic or "").strip()
        # No word-count truncation — the renderer auto-fits, so long text just
        # shrinks instead of getting butchered (old bug: dropped words + dangling
        # '—'). We only cap absurd length to keep it punchy.
        if len(thumb_text.split()) > 6:
            thumb_text = " ".join(thumb_text.split()[:6])
        if not thumb_latin:
            import re as _re
            thumb_latin = _re.sub(r"[ऀ-ॿ‌‍़]+[\?।!.]*", "", thumb_text)
            thumb_latin = _re.sub(r"\s{2,}", " ", thumb_latin).strip(" -—|?.।")

        print(f"   ✅ Cover text: \"{thumb_text}\"  (latin-safe: \"{thumb_latin}\") | {thumb_color}")
        return {
            "brief_text": brief_text,
            "text": thumb_text,
            "text_latin": thumb_latin,
            "color": thumb_color,
            "face": thumb_face,
        }

    except Exception as e:
        print(f"   ⚠️ Thumbnail brief generation failed: {e}")
        return None


def generate_ai_thumbnail(hook_text, topic, script_text, veo_clip_path=None,
                          claude_client=None, genai_client=None, cost_tracker=None):
    """High-CTR cover: Claude writes SHORT punchy text (with vision on the Veo
    frame) -> Gemini paints a TEXT-FREE hero scene -> our PIL renderer
    composites big, crisp, keyword-highlighted text. The image model never
    renders letters (it garbles them, worst of all Devanagari), so clarity is
    guaranteed. Returns the cover PNG path or None.
    """
    if not AI_THUMBNAIL or not claude_client:
        return None
    try:
        from PIL import Image
        from google.genai import types

        print("   \U0001F916 Cover pipeline: Claude text -> Gemini TEXT-FREE scene -> PIL crisp text...")
        research = refresh_thumbnail_research(claude_client)

        # 1. Best Veo frame = the base scene (and Claude's vision reference)
        frame_image = _thumbnail_background(veo_clip_path, enhance=False)

        # 2. Claude writes the cover text (2 lines, one number highlighted)
        source_insights = get_source_channel_top_topics(5)
        audience_qs = get_audience_questions(5)
        _ig_brief = get_ig_engagement_summary()
        brief = generate_thumbnail_brief(
            claude_client, script_text, hook_text, topic, research,
            source_insights=source_insights, audience_qs=audience_qs,
            cost_tracker=cost_tracker, frame_image=frame_image, ig_summary=_ig_brief,
        )
        if not brief:
            print("   ⚠️ Cover: brief failed → basic fallback")
            return generate_thumbnail(hook_text, topic, veo_clip_path=veo_clip_path)

        # Pick the cover text: Devanagari version only if we can shape it (RAQM),
        # else the Latin-safe version the brief also returned.
        cover_text = brief.get("text") or ""
        if _has_deva(cover_text) and not COVER_RAQM:
            cover_text = brief.get("text_latin") or cover_text
        lines, highlight = _cover_lines_from_text(cover_text)
        if not lines:
            return generate_thumbnail(hook_text, topic, veo_clip_path=veo_clip_path)

        # 3. Gemini paints a TEXT-FREE hero scene (background only). If it fails
        #    or is unavailable, the Veo frame is already a fine background.
        scene = frame_image
        if genai_client and frame_image is not None:
            scene_prompt = (
                "You are a product photographer creating a background plate for an Indian "
                "wholesale plain t-shirt brand's Reel cover (1080x1920, 9:16).\n"
                "Recreate/enhance the reference image into a striking, high-contrast HERO SCENE: "
                "keep the product/fabric the clear subject, rich warehouse bokeh, cinematic lighting.\n"
                "Leave the UPPER-CENTER (25%-55% height) visually CALM and slightly darker so text can "
                "sit there later. Put the main subject in the lower-center.\n\n"
                "ABSOLUTELY NO TEXT: no letters, no words, no numbers, no captions, no watermark, "
                "no logo, no price tag with digits, no signage. A person is optional; if present, "
                "an Indian factory-owner look, no text on clothing. Output exactly 1080x1920."
            )
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
            for model_name in (AI_THUMBNAIL_GEMINI_MODEL, AI_THUMBNAIL_GEMINI_FALLBACK):
                try:
                    print(f"   \U0001F3A8 Gemini text-free scene ({model_name}) [timeout=120s]...")
                    def _call(m=model_name):
                        return genai_client.models.generate_content(
                            model=m, contents=[scene_prompt, frame_image],
                            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
                        )
                    with ThreadPoolExecutor(max_workers=1) as ex:
                        response = ex.submit(_call).result(timeout=120)
                    got = None
                    for part in response.parts:
                        if part.inline_data is not None:
                            got = part.as_image()
                            if not isinstance(got, Image.Image):
                                import io as _io
                                got = Image.open(_io.BytesIO(part.inline_data.data))
                            break
                    if got is not None:
                        scene = got.resize((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.LANCZOS)
                        if cost_tracker:
                            cost_tracker.track_gemini_image()
                        print("   ✅ Gemini scene ready (text-free)")
                        break
                    print(f"   ⚠️ Gemini ({model_name}) returned no image")
                except FuturesTimeout:
                    print(f"   ⚠️ Gemini ({model_name}) timed out → next")
                except Exception as _ge:
                    print(f"   ⚠️ Gemini ({model_name}) failed: {str(_ge)[:120]} → next")

        # 4. Composite the crisp text (this is what guarantees clarity)
        out = compose_cover_text(scene, lines, highlight)
        if not out:
            return generate_thumbnail(hook_text, topic, veo_clip_path=veo_clip_path,
                                      cover_text=cover_text, highlight=highlight)
        COVER_META.update({
            "cover_text": " ".join(lines),
            "cover_color": "#FFD400 keyword + #FFFFFF",
            "cover_path": "ai+pil" if scene is not frame_image else "veo+pil",
            "cover_face": brief.get("face", False),
        })
        print(f"   ✅ Cover ready: \"{' / '.join(lines)}\"  ({os.path.basename(out)})")
        return out

    except Exception as e:
        print(f"   ⚠️ AI thumbnail pipeline failed: {e}")
        try:
            return generate_thumbnail(hook_text, topic, veo_clip_path=veo_clip_path)
        except Exception:
            return None



def pin_comment(youtube, video_id, comment_text=None):
    """Post a CTA comment on the video and pin it to the top."""
    if not AUTO_PIN_COMMENT:
        return
    try:
        text = comment_text or PIN_COMMENT_TEXT

        # Get our channel ID (required by commentThreads.insert)
        channel_id = None
        try:
            ch_resp = youtube.channels().list(part="id", mine=True).execute()
            if ch_resp.get("items"):
                channel_id = ch_resp["items"][0]["id"]
        except Exception:
            pass

        # Insert the comment (retry with delay — freshly uploaded videos need processing time)
        comment_response = None
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                comment_body = {
                    "snippet": {
                        "videoId": video_id,
                        "topLevelComment": {
                            "snippet": {
                                "textOriginal": text,
                            }
                        }
                    }
                }
                if channel_id:
                    comment_body["snippet"]["channelId"] = channel_id

                comment_response = youtube.commentThreads().insert(
                    part="snippet", body=comment_body
                ).execute()
                break  # Success
            except Exception as e:
                error_msg = str(e)
                if attempt < max_retries and ("403" in error_msg or "processing" in error_msg.lower()):
                    wait = 30 * attempt
                    print(f"   ⏳ Comment attempt {attempt} failed, retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise

        if not comment_response:
            print(f"   ⚠️ Pin comment failed after {max_retries} attempts")
            return None

        comment_id = comment_response["snippet"]["topLevelComment"]["id"]
        print(f"   💬 CTA comment posted: {comment_id}")

        # Channel owner's first comment is automatically prominent.
        # setModerationStatus as "published" ensures it's visible immediately.
        try:
            youtube.comments().setModerationStatus(
                id=comment_id, moderationStatus="published"
            ).execute()
        except Exception:
            pass  # Comment is already published, this is fine

        print(f"   📌 Comment pinned (channel owner comment = top position)")
        return comment_id

    except Exception as e:
        print(f"   ⚠️ Pin comment failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════
# AUTO-PLAYLIST ORGANIZATION
# ═══════════════════════════════════════════════════════════════════════

# Map topic series → playlist title + description
SERIES_PLAYLISTS = {
    "fabric_gsm": {
        "title": "Fabric & GSM Knowledge | Sale91.com",
        "description": "Plain t-shirt ka fabric samjho — GSM, cotton types, yarn count, shrinkage sab kuch. B2B textile knowledge for printing businesses.",
    },
    "customer_stories": {
        "title": "Customer Stories & Business Lessons | Sale91.com",
        "description": "Real customer incidents, order mistakes, and business lessons from the t-shirt manufacturing industry.",
    },
    "printing_methods": {
        "title": "Printing Methods Deep Dive | Sale91.com",
        "description": "DTG, DTF, Screen Print, Sublimation, Heat Transfer — har printing method ke liye kaunsa blank best hai.",
    },
    "business_tips": {
        "title": "T-shirt Business Tips | Sale91.com",
        "description": "Pricing, MOQ, margins, supplier selection — printing aur merch business ke liye practical tips.",
    },
    "quality_checks": {
        "title": "Quality Check & Testing | Sale91.com",
        "description": "Biowash, pre-shrunk, pilling, colorfastness — t-shirt quality kaise check karein. Practical testing tips.",
    },
    "product_style": {
        "title": "Product & Style Guide | Sale91.com",
        "description": "Oversized, polo, hoodie, acid wash — kaunsa product kab use karein. Style + business knowledge.",
    },
    "myth_busters": {
        "title": "T-shirt Myths Busted | Sale91.com",
        "description": "Common myths about t-shirts, printing, and fabric — fact-checked by a manufacturer.",
    },
}


def _detect_series(topic):
    """Detect which series a topic belongs to based on keywords."""
    topic_lower = topic.lower()
    for series_name, series_data in TOPIC_SERIES_TAGS.items():
        for kw in series_data["keywords"]:
            if kw in topic_lower:
                return series_name
    return None


def _load_playlist_cache():
    """Load cached playlist IDs (series_name → playlist_id)."""
    if os.path.exists(PLAYLIST_CACHE_FILE):
        try:
            with open(PLAYLIST_CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_playlist_cache(cache):
    """Save playlist ID cache."""
    try:
        with open(PLAYLIST_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass


def add_to_playlist(youtube, video_id, topic):
    """Auto-add video to the correct series playlist. Creates playlist if needed."""
    if not AUTO_PLAYLIST:
        return

    series = _detect_series(topic)
    if not series or series not in SERIES_PLAYLISTS:
        print(f"   ℹ️ No playlist match for topic")
        return

    playlist_info = SERIES_PLAYLISTS[series]
    cache = _load_playlist_cache()

    try:
        # Check if playlist already exists in cache
        playlist_id = cache.get(series)

        if not playlist_id:
            # Search for existing playlist by title
            playlists = youtube.playlists().list(
                part="snippet", mine=True, maxResults=50
            ).execute()

            for pl in playlists.get("items", []):
                if pl["snippet"]["title"] == playlist_info["title"]:
                    playlist_id = pl["id"]
                    cache[series] = playlist_id
                    _save_playlist_cache(cache)
                    break

        if not playlist_id:
            # Create new playlist
            pl_body = {
                "snippet": {
                    "title": playlist_info["title"],
                    "description": playlist_info["description"],
                },
                "status": {"privacyStatus": "public"},
            }
            new_pl = youtube.playlists().insert(part="snippet,status", body=pl_body).execute()
            playlist_id = new_pl["id"]
            cache[series] = playlist_id
            _save_playlist_cache(cache)
            print(f"   📁 Created playlist: {playlist_info['title']}")

        # Add video to playlist
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()
        print(f"   📁 Added to playlist: {playlist_info['title']}")

    except Exception as e:
        print(f"   ⚠️ Playlist add failed: {e}")


# ═══════════════════════════════════════════════════════════════════════
# INSTAGRAM REELS CROSS-POST
# ═══════════════════════════════════════════════════════════════════════

def get_instagram_best_time(ig_token, ig_business_id):
    """Find the best IG posting slot from online_followers insights.

    online_followers (period=lifetime — the only supported period) returns one
    entry per day for the last ~30 days; each entry's value is {hour: count}
    with hours in UTC (NOT day-name keyed — the old parser assumed
    {day_name: {hour: count}} and always failed). We aggregate across days,
    convert UTC→IST, and pick the peak inside the 17:00-21:00 IST evening
    window. Never returns None: falls back to 18:30 IST fixed (same evening
    if possible, else tomorrow).

    DISABLED 2026-07-07: returning a time makes cross_post_to_instagram use
    scheduled_publish_time, which Meta rejects for this account with
    "(#3) User must be on whitelist" (scheduled publishing is a restricted
    feature we're not whitelisted for) — so the Reel never posted. Returning
    None forces the immediate-publish path (reliable for months). Re-enable
    only if the account is granted scheduled-publishing access.
    """
    return None
    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    min_lead = now_ist + timedelta(minutes=20)

    def _fallback():
        candidate = now_ist.replace(hour=18, minute=30, second=0, microsecond=0)
        if candidate <= min_lead:
            # 18:30 already passed — post later this evening rather than skipping a day
            candidate = (now_ist + timedelta(minutes=30)).replace(second=0, microsecond=0)
            if candidate.hour >= 22 or candidate.hour < 8:
                candidate = (now_ist + timedelta(days=1)).replace(hour=18, minute=30, second=0, microsecond=0)
        print(f"   🕐 IG fallback slot: {candidate.strftime('%d %b %I:%M %p IST')}")
        return candidate

    try:
        resp = requests.get(
            f"https://graph.facebook.com/{IG_API_VERSION}/{ig_business_id}/insights",
            params={
                "metric": "online_followers",
                "period": "lifetime",
                "access_token": ig_token,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"   ⚠️ Instagram Insights API failed ({resp.status_code}): {resp.text[:120]}")
            return _fallback()

        data = resp.json().get("data", [])
        values = data[0].get("values", []) if data else []

        # Aggregate follower counts per UTC hour across all returned days
        hour_totals = {}
        for v in values:
            day_value = v.get("value") or {}
            if not isinstance(day_value, dict):
                continue
            for hour_str, count in day_value.items():
                try:
                    h = int(hour_str)
                    hour_totals[h] = hour_totals.get(h, 0) + int(count or 0)
                except (ValueError, TypeError):
                    continue

        if not hour_totals:
            print("   ⚠️ No usable online_followers data")
            return _fallback()

        # UTC → IST (+5:30): utc_hour h maps to IST h+5 at :30 past
        ist_totals = {}
        for utc_hour, count in hour_totals.items():
            ist_hour = (utc_hour + 5) % 24
            ist_totals[ist_hour] = ist_totals.get(ist_hour, 0) + count

        # Restrict to the 17:00-21:00 IST evening window
        window = {h: c for h, c in ist_totals.items() if 17 <= h <= 20}
        if not window:
            print("   ⚠️ No audience peak inside 17:00-21:00 IST window")
            return _fallback()

        ranked = sorted(window.items(), key=lambda x: x[1], reverse=True)
        print("   📊 IG audience peaks (17:00-21:00 IST window, 30-day sum):")
        for h, c in ranked[:3]:
            print(f"      {h:02d}:30 IST — {c:,} follower-hours")

        for h, _ in ranked:
            candidate = now_ist.replace(hour=h, minute=30, second=0, microsecond=0)
            if candidate > min_lead:
                print(f"   🕐 IG best slot: {candidate.strftime('%d %b %I:%M %p IST')}")
                return candidate
        # Whole window already passed today
        return _fallback()

    except Exception as e:
        print(f"   ⚠️ Instagram best-time detection failed: {e}")
        return _fallback()


def refresh_instagram_token_if_needed():
    """Check Instagram token expiry and auto-refresh if expiring within 7 days.
    Updates GitHub Secrets in yt-shorts-bot and yt-reply-tool repos."""
    ig_token = (os.environ.get("INSTAGRAM_ACCESS_TOKEN") or "").strip()
    fb_app_id = (os.environ.get("FB_APP_ID") or "").strip()
    fb_app_secret = (os.environ.get("FB_APP_SECRET") or "").strip()
    gh_pat = (os.environ.get("GH_PAT") or "").strip()

    if not ig_token or not fb_app_id or not fb_app_secret:
        return ig_token  # Can't refresh without credentials

    # Step 1: Check token expiry via debug_token API
    try:
        debug_resp = requests.get(
            f"https://graph.facebook.com/{IG_API_VERSION}/debug_token",
            params={
                "input_token": ig_token,
                "access_token": f"{fb_app_id}|{fb_app_secret}",
            },
            timeout=10,
        )
        if debug_resp.status_code != 200:
            print(f"   ⚠️ Token debug check failed ({debug_resp.status_code}): {debug_resp.text[:150]}")
            return ig_token

        token_data = debug_resp.json().get("data", {})
        expires_at = token_data.get("expires_at", 0)
        is_valid = token_data.get("is_valid", False)

        if not is_valid:
            print("   ❌ Instagram token is INVALID — manual refresh required")
            print("      Go to: https://developers.facebook.com/tools/debug/accesstoken/")
            return ig_token

        if expires_at == 0:
            print("   ✅ Instagram token never expires (page token)")
            return ig_token

        now_ts = int(time.time())
        days_left = (expires_at - now_ts) / 86400
        expires_date = datetime.fromtimestamp(expires_at, pytz.timezone("Asia/Kolkata")).strftime("%d %b %Y")
        print(f"   🔑 Instagram token expires: {expires_date} ({days_left:.0f} days left)")

        if days_left > 3:
            print(f"   ✅ Token OK — no refresh needed (>{3} days remaining)")
            return ig_token

        print(f"   ⚠️ Token expiring soon ({days_left:.0f} days) — refreshing...")

    except Exception as e:
        print(f"   ⚠️ Token expiry check failed: {e}")
        return ig_token

    # Step 2: Exchange for new long-lived token
    try:
        refresh_resp = requests.get(
            f"https://graph.facebook.com/{IG_API_VERSION}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": fb_app_id,
                "client_secret": fb_app_secret,
                "fb_exchange_token": ig_token,
            },
            timeout=15,
        )
        if refresh_resp.status_code != 200:
            print(f"   ❌ Token refresh failed ({refresh_resp.status_code}): {refresh_resp.text[:200]}")
            print("      Manual refresh: https://developers.facebook.com/tools/debug/accesstoken/")
            return ig_token

        new_token = refresh_resp.json().get("access_token", "")
        if not new_token:
            print(f"   ❌ Token refresh returned empty token: {refresh_resp.text[:200]}")
            return ig_token

        print(f"   ✅ New long-lived token obtained (len={len(new_token)}, prefix={new_token[:6]}...)")

        # Update current process env
        os.environ["INSTAGRAM_ACCESS_TOKEN"] = new_token

    except Exception as e:
        print(f"   ❌ Token refresh request failed: {e}")
        return ig_token

    # Step 3: Update GitHub Secrets in both repos
    if not gh_pat:
        print("   ⚠️ GH_PAT not set — cannot auto-update GitHub Secrets")
        print(f"      Manually update INSTAGRAM_ACCESS_TOKEN in GitHub Secrets")
        return new_token

    import subprocess

    repos = ["thakyanamtumhara/yt-shorts-bot", "thakyanamtumhara/yt-reply-tool"]
    for repo in repos:
        try:
            result = subprocess.run(
                ["gh", "secret", "set", "INSTAGRAM_ACCESS_TOKEN", "--repo", repo],
                input=new_token,
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "GH_TOKEN": gh_pat},
            )
            if result.returncode == 0:
                print(f"   ✅ Secret updated in {repo}")
            else:
                print(f"   ⚠️ Failed to update secret in {repo}: {result.stderr[:150]}")
        except Exception as e:
            print(f"   ⚠️ Failed to update secret in {repo}: {e}")

    return new_token


def publish_fb_reel(video_path, description):
    """Cross-post the video as a Facebook Reel on the Sale91 FB Page.

    Official 3-step flow (developers.facebook.com/docs/video-api/guides/reels-publishing):
    1. POST /{page_id}/video_reels upload_phase=start  → video_id + upload_url
    2. POST binary to rupload.facebook.com with offset/file_size headers
    3. POST /{page_id}/video_reels upload_phase=finish video_state=PUBLISHED

    Dormant until FB_PAGE_ID + FB_PAGE_ACCESS_TOKEN secrets exist (page token needs
    pages_manage_posts + pages_read_engagement + pages_show_list).
    Page rate limit: 30 API-published reels per rolling 24h — we post 1/day.
    Returns fb video_id on success, None on failure. Non-fatal."""
    # The main IG token is a never-expiring PAGE token for this Page and (since
    # 2026-07-06) carries pages_manage_posts — use it when no dedicated token set.
    page_id = (os.environ.get("FB_PAGE_ID") or "1999594936994410").strip()
    page_token = (os.environ.get("FB_PAGE_ACCESS_TOKEN") or os.environ.get("INSTAGRAM_ACCESS_TOKEN") or "").strip()
    if not page_id or not page_token:
        print("   ℹ️ FB Reel: skipped (no page token available)")
        return None
    if not video_path or not os.path.exists(video_path):
        print("   ⚠️ FB Reel: video file missing")
        return None

    try:
        # Step 1: initialize upload session
        start_resp = requests.post(
            f"https://graph.facebook.com/{IG_API_VERSION}/{page_id}/video_reels",
            data={"upload_phase": "start", "access_token": page_token},
            timeout=30,
        )
        if start_resp.status_code != 200:
            print(f"   ❌ FB Reel: start failed: {start_resp.text[:200]}")
            return None
        start_data = start_resp.json()
        fb_video_id = start_data.get("video_id")
        upload_url = start_data.get("upload_url")
        if not fb_video_id or not upload_url:
            print(f"   ❌ FB Reel: start returned no video_id/upload_url: {start_resp.text[:200]}")
            return None
        print(f"   📦 FB Reel: upload session started → {fb_video_id}")

        # Step 2: upload the binary
        file_size = os.path.getsize(video_path)
        with open(video_path, "rb") as f:
            up_resp = requests.post(
                upload_url,
                headers={
                    "Authorization": f"OAuth {page_token}",
                    "offset": "0",
                    "file_size": str(file_size),
                },
                data=f,
                timeout=300,
            )
        if up_resp.status_code != 200 or not up_resp.json().get("success"):
            print(f"   ❌ FB Reel: binary upload failed: {up_resp.text[:200]}")
            return None
        print(f"   ⬆️ FB Reel: uploaded {file_size // 1024}KB")

        # Step 3: publish
        fin_resp = requests.post(
            f"https://graph.facebook.com/{IG_API_VERSION}/{page_id}/video_reels",
            data={
                "upload_phase": "finish",
                "video_state": "PUBLISHED",
                "video_id": fb_video_id,
                "description": description[:2000],
                "access_token": page_token,
            },
            timeout=60,
        )
        if fin_resp.status_code != 200:
            print(f"   ❌ FB Reel: finish failed: {fin_resp.text[:200]}")
            return None
        print(f"   ✅ FB Reel: PUBLISHED → video_id {fb_video_id}")
        return fb_video_id
    except Exception as e:
        print(f"   ⚠️ FB Reel: unexpected error: {e}")
        return None


def post_telegram_channel(video_path, caption):
    """Post the video to the Sale91 Telegram channel via Bot API sendVideo.

    Dormant until TELEGRAM_BOT_TOKEN + TELEGRAM_CHANNEL_ID secrets exist
    (channel id = @handle or -100xxxxxxxxxx; bot must be channel admin).
    Bot API upload limit 50MB — our ~40s 720x1280 output fits comfortably.
    Returns telegram message_id on success, None on failure. Non-fatal."""
    bot_token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    channel_id = (os.environ.get("TELEGRAM_CHANNEL_ID") or "").strip()
    if not bot_token or not channel_id:
        print("   ℹ️ Telegram: skipped (no TELEGRAM_BOT_TOKEN/TELEGRAM_CHANNEL_ID)")
        return None
    if not video_path or not os.path.exists(video_path):
        print("   ⚠️ Telegram: video file missing")
        return None
    if os.path.getsize(video_path) > 49 * 1024 * 1024:
        print("   ⚠️ Telegram: video exceeds 50MB bot upload limit — skipped")
        return None

    try:
        with open(video_path, "rb") as f:
            resp = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendVideo",
                data={
                    "chat_id": channel_id,
                    "caption": caption[:1024],
                    "supports_streaming": "true",
                },
                files={"video": ("short.mp4", f, "video/mp4")},
                timeout=300,
            )
        data = resp.json()
        if not data.get("ok"):
            print(f"   ❌ Telegram: sendVideo failed: {str(data)[:200]}")
            return None
        message_id = data.get("result", {}).get("message_id")
        print(f"   ✅ Telegram: posted → message_id {message_id}")
        return message_id
    except Exception as e:
        print(f"   ⚠️ Telegram: unexpected error: {e}")
        return None


def cross_post_to_instagram(video_path, title, description, topic, thumbnail_path=None):
    """Cross-post the video to Instagram Reels via the Instagram Graph API.
    Requires INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_BUSINESS_ID env vars.
    Uses a 2-step flow: create media container → publish."""
    if not CROSS_POST_INSTAGRAM:
        return None

    ig_token = (os.environ.get("INSTAGRAM_ACCESS_TOKEN") or "").strip()
    ig_business_id = (os.environ.get("INSTAGRAM_BUSINESS_ID") or "").strip()

    if not ig_token or not ig_business_id:
        print("   ℹ️ Instagram cross-post skipped (no INSTAGRAM_ACCESS_TOKEN/INSTAGRAM_BUSINESS_ID)")
        return None

    # Quick token sanity check — catch obviously broken tokens early
    if len(ig_token) < 20 or " " in ig_token:
        print(f"   ⚠️ Instagram token looks malformed (len={len(ig_token)}). Skipping cross-post.")
        print(f"      Hint: refresh your long-lived token via Facebook Graph API Explorer")
        return None

    try:
        # Pre-flight: validate token with a lightweight /me call
        print(f"   🔑 Verifying Instagram token (len={len(ig_token)}, prefix={ig_token[:6]}...{ig_token[-4:]})...")
        me_resp = requests.get(
            f"https://graph.facebook.com/{IG_API_VERSION}/{ig_business_id}",
            params={"fields": "id,name,username", "access_token": ig_token},
            timeout=10,
        )
        if me_resp.status_code != 200:
            error_data = me_resp.json().get("error", {})
            err_code = error_data.get("code", "")
            err_msg = error_data.get("message", me_resp.text[:150])
            print(f"   ❌ Instagram token invalid (code {err_code}): {err_msg}")
            if err_code == 190:
                sub_code = error_data.get("error_subcode", "")
                if sub_code == 463:
                    print(f"      🔑 Token EXPIRED — generate new long-lived token and update INSTAGRAM_ACCESS_TOKEN secret")
                else:
                    print(f"      🔑 Token cannot be parsed — check for extra quotes/newlines/spaces in the GitHub secret")
                    print(f"         Token should start with 'EAA' or 'IGA' (yours starts with '{ig_token[:3]}')")
            return None
        else:
            ig_info = me_resp.json()
            print(f"   ✅ Token valid — account: {ig_info.get('name', ig_info.get('username', ig_info.get('id')))}")

        # Instagram Reels need the video hosted at a public URL.
        # Upload to a temporary public host (with fallback chain).
        print("   📸 Cross-posting to Instagram Reels...")

        # Step 0: Upload video to a public URL that Instagram can fetch.
        # S3 + CloudFront is the most reliable — Instagram always reaches it.
        # Free hosts (0x0.st, litterbox, file.io) are unreliable:
        #   - 0x0.st times out on large files (>30MB)
        #   - litterbox returns URLs that Instagram can't download (error 2207077)
        #   - file.io is single-download (Instagram retry = 404)
        public_url = None
        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        ig_s3_key = f"p/ig-reel-{int(time.time())}.mp4"

        # Host 1 (PRIMARY): S3 + CloudFront — Instagram always reaches this
        try:
            import boto3
            print(f"   📤 Uploading to S3/CloudFront ({file_size_mb:.0f}MB)...")
            s3 = boto3.client("s3")
            s3.upload_file(
                video_path,
                BLOG_S3_BUCKET,
                ig_s3_key,
                ExtraArgs={"ContentType": "video/mp4", "CacheControl": "max-age=3600"},
            )
            public_url = f"{BLOG_BASE_URL}/{ig_s3_key}"
            print(f"   ✅ Video hosted via CloudFront: {public_url}")
        except Exception as e:
            print(f"   ⚠️ S3 upload failed: {e}")

        # Host 2 (fallback): 0x0.st (up to 512MB, direct URL, no account needed)
        if not public_url:
            try:
                print(f"   📤 Uploading to 0x0.st ({file_size_mb:.0f}MB)...")
                with open(video_path, "rb") as vf:
                    resp = requests.post(
                        "https://0x0.st",
                        files={"file": (os.path.basename(video_path), vf, "video/mp4")},
                        timeout=300,
                    )
                if resp.status_code == 200 and resp.text.strip().startswith("http"):
                    public_url = resp.text.strip()
                    print(f"   📤 Video hosted via 0x0.st")
                else:
                    print(f"   ⚠️ 0x0.st failed ({resp.status_code}): {resp.text[:100]}")
            except Exception as e:
                print(f"   ⚠️ 0x0.st error: {e}")

        # Host 3 (fallback): litterbox.catbox.moe (up to 1GB, 72h expiry)
        if not public_url:
            try:
                print(f"   📤 Trying litterbox.catbox.moe...")
                with open(video_path, "rb") as vf:
                    resp = requests.post(
                        "https://litterbox.catbox.moe/resources/internals/api.php",
                        data={"reqtype": "fileupload", "time": "72h"},
                        files={"fileToUpload": (os.path.basename(video_path), vf, "video/mp4")},
                        timeout=300,
                    )
                if resp.status_code == 200 and resp.text.strip().startswith("http"):
                    public_url = resp.text.strip()
                    print(f"   ⚠️ Video hosted via litterbox (Instagram may not accept this)")
                else:
                    print(f"   ⚠️ litterbox failed ({resp.status_code}): {resp.text[:100]}")
            except Exception as e:
                print(f"   ⚠️ litterbox error: {e}")

        if not public_url:
            print(f"   ❌ Instagram: all hosts failed. Skipping cross-post.")
            return None

        # Step 1: Detect best posting time from audience insights
        best_time = get_instagram_best_time(ig_token, ig_business_id)
        schedule_for_later = False
        schedule_timestamp = None

        if best_time:
            ist = pytz.timezone("Asia/Kolkata")
            now_ist = datetime.now(ist)
            # Only schedule if best time is >15 min away (IG minimum)
            diff_minutes = (best_time - now_ist).total_seconds() / 60
            if diff_minutes > 15:
                schedule_for_later = True
                schedule_timestamp = int(best_time.timestamp())
                print(f"   📅 Will schedule Reel for {best_time.strftime('%d %b %I:%M %p IST')} (in {int(diff_minutes)} min)")
            else:
                print(f"   ⚡ Peak time is now — publishing immediately")
        else:
            print(f"   ℹ️ Insights unavailable — publishing immediately")

        # Step 2: Create media container (Reels)
        ig_hashtags = get_ig_hashtags(topic)
        ig_caption = (
            f"{get_ig_seo_line(topic, title)}\n\n"
            f"{description.split(chr(10))[0]}\n\n"
            f"{get_ig_cta_line()}\n\n"
            f"📦 Order: Sale91.com (MOQ 10 pcs, Pan India)\n\n"
            f"{' '.join(ig_hashtags)}"
        )

        container_data = {
            "media_type": "REELS",
            "video_url": public_url,
            "caption": ig_caption[:2200],  # IG caption limit
            "access_token": ig_token,
        }
        # NOTE: share_to_feed removed — deprecated for Reels in v18+,
        # causes "Carousel item cannot be published standalone" error.
        # Reels are auto-shared to feed by default.

        # Upload custom cover image (thumbnail) to S3 and set as Instagram Reel cover
        if thumbnail_path and os.path.exists(thumbnail_path):
            try:
                import boto3
                ig_cover_key = f"p/ig-cover-{int(time.time())}.png"
                s3_cover = boto3.client("s3")
                s3_cover.upload_file(
                    thumbnail_path, BLOG_S3_BUCKET, ig_cover_key,
                    ExtraArgs={"ContentType": "image/png", "CacheControl": "max-age=3600"}
                )
                cover_url = f"{BLOG_BASE_URL}/{ig_cover_key}"
                container_data["cover_url"] = cover_url
                print(f"   🖼️ Instagram cover image set: {cover_url}")
            except Exception as e:
                print(f"   ⚠️ Cover image upload failed, Instagram will auto-select: {e}")
                COVER_META.update({"cover_text": None, "cover_color": None, "cover_path": None, "cover_face": None})

        if schedule_for_later and schedule_timestamp:
            # Schedule instead of instant publish — IG handles it
            container_data["published"] = "false"
            container_data["scheduled_publish_time"] = str(schedule_timestamp)

        # Meta AI-disclosure param (added to Graph API Jun 2026) — required for
        # photorealistic AI video + synthetic audio. Applies the "AI info" label.
        container_data["is_ai_generated"] = "true"

        container_resp = requests.post(
            f"https://graph.facebook.com/{IG_API_VERSION}/{ig_business_id}/media",
            data=container_data,
            timeout=30,
        )

        if container_resp.status_code != 200 and "is_ai_generated" in container_data:
            # Param is new — if this API version rejects it, retry without rather than
            # losing the post (Meta can still auto-label via C2PA metadata).
            print(f"   ⚠️ IG container failed with is_ai_generated param ({container_resp.text[:120]}) — retrying without")
            container_data.pop("is_ai_generated", None)
            container_resp = requests.post(
                f"https://graph.facebook.com/{IG_API_VERSION}/{ig_business_id}/media",
                data=container_data,
                timeout=30,
            )

        if container_resp.status_code != 200:
            error_text = container_resp.text[:200]
            print(f"   ⚠️ Instagram container creation failed: {error_text}")
            if "OAuthException" in error_text or "access token" in error_text.lower():
                print(f"      🔑 Token expired/invalid — refresh at: https://developers.facebook.com/tools/explorer/")
                print(f"         1. Generate new long-lived token  2. Update INSTAGRAM_ACCESS_TOKEN secret")
            return None

        container_id = container_resp.json().get("id")
        print(f"   📦 Instagram container created: {container_id}")

        # Step 3: Wait for processing (Instagram processes video async)
        processing_finished = False
        for check in range(20):  # Max 10 minutes
            time.sleep(30)
            status_resp = requests.get(
                f"https://graph.facebook.com/{IG_API_VERSION}/{container_id}",
                params={"fields": "status_code,status", "access_token": ig_token},
                timeout=15,
            )
            status_data = status_resp.json()
            status_code = status_data.get("status_code", "")
            if status_code == "FINISHED":
                processing_finished = True
                break
            elif status_code == "ERROR":
                err_detail = status_data.get("status", {})
                print(f"   ❌ Instagram processing failed: {err_detail}")
                return None
            print(f"   ⏳ Instagram processing... ({check + 1}/20, status={status_code})")

        if not processing_finished:
            print(f"   ❌ Instagram processing timed out after 10 minutes")
            return None

        # Step 3b: Verify container is REELS (catch carousel misclassification)
        try:
            verify_resp = requests.get(
                f"https://graph.facebook.com/{IG_API_VERSION}/{container_id}",
                params={"fields": "status_code,media_type,media_product_type", "access_token": ig_token},
                timeout=15,
            )
            verify_data = verify_resp.json()
            c_type = verify_data.get("media_type", "?")
            c_product = verify_data.get("media_product_type", "?")
            print(f"   \U0001f50d Container type: media_type={c_type}, product={c_product}")
        except Exception:
            pass

        # Step 4: Publish (or confirm schedule)
        if NEW_TEST_MODE or SINGLE_VEO_TEST:
            # Test modes: do NOT publish to IG (would give wrong impression to public followers)
            mode_label = "NEW TEST MODE" if NEW_TEST_MODE else "SINGLE VEO TEST"
            print(f"   🧪 {mode_label} — Instagram container created & processed but NOT published")
            print(f"      Container ID: {container_id}")
            print(f"      ℹ️ Instagram API does not support private/draft Reels — skipping publish for test")
            # Cleanup: delete temp video from S3
            if ig_s3_key:
                try:
                    boto3.client("s3").delete_object(Bucket=BLOG_S3_BUCKET, Key=ig_s3_key)
                except Exception:
                    pass
            return f"test:{container_id}"
        elif schedule_for_later:
            # Scheduled posts are auto-published by Instagram at the set time
            # Return prefixed ID so engagement tracker knows this is a scheduled post
            print(f"   \u2705 Instagram Reel SCHEDULED for {best_time.strftime('%d %b %I:%M %p IST')}!")
            print(f"      Container ID: {container_id} \u2014 Instagram will auto-publish")
            return f"scheduled:{container_id}"
        else:
            # Immediate publish — try current version, then fallback
            for api_ver in [IG_API_VERSION, "v20.0"]:
                publish_resp = requests.post(
                    f"https://graph.facebook.com/{api_ver}/{ig_business_id}/media_publish",
                    data={"creation_id": container_id, "access_token": ig_token},
                    timeout=30,
                )

                if publish_resp.status_code == 200:
                    ig_media_id = publish_resp.json().get("id")
                    print(f"   \u2705 Instagram Reel published! ID: {ig_media_id} (api={api_ver})")
                    # Cleanup: delete temp video from S3 (non-blocking)
                    if ig_s3_key:
                        try:
                            boto3.client("s3").delete_object(Bucket=BLOG_S3_BUCKET, Key=ig_s3_key)
                        except Exception:
                            pass
                    return ig_media_id

                error_text = publish_resp.text[:300]
                print(f"   \u26a0\ufe0f Instagram publish failed (api={api_ver}): {error_text}")

                # If carousel error, retry with next version
                if "2207089" in error_text or "carousel" in error_text.lower():
                    print(f"   \U0001f504 Carousel error detected — retrying with fallback API version...")
                    time.sleep(5)
                    continue
                else:
                    break  # Different error — don't retry

            return None

    except Exception as e:
        print(f"   ⚠️ Instagram cross-post failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════
# COST TRACKER
# ═══════════════════════════════════════════════════════════════════════

# Approximate per-unit costs (USD)
COST_RATES = {
    "claude_sonnet_input_1k": 0.003,    # $3/M input tokens
    "claude_sonnet_output_1k": 0.015,   # $15/M output tokens
    "claude_opus_input_1k": 0.015,      # $15/M input tokens
    "claude_opus_output_1k": 0.075,     # $75/M output tokens
    "openai_tts_per_char": 0.000015,    # $15/1M chars
    "elevenlabs_per_char": 0.00003,     # ~$0.30/1K chars (pro plan)
    "sarvam_per_char": 0.000010,        # ~$0.01/1K chars (Bulbul v3)
    "veo_per_clip": 0.96,               # Veo 3.1 Fast: ~$0.12/sec × 8s @ 1080p
    "veo_full_per_clip": 3.20,          # Veo 3.1 Full: $0.40/sec × 8s
    "kling_per_clip": 0.35,            # ~$0.35 per 5s clip ($0.07/s via fal.ai)
    "replicate_ace_step": 0.05,         # ~$0.05 per music generation
    "replicate_flux_image": 0.025,      # ~$0.025 per FLUX Dev image
    "whisper_per_minute": 0.006,        # $0.006/min
    "gemini_image": 0.045,             # ~$0.045 per Gemini image generation
}


class CostTracker:
    """Track API costs per video generation run."""

    def __init__(self):
        self.costs = {}
        self.start_time = time.time()

    def add(self, service, amount, detail=""):
        """Add a cost entry. amount is in USD."""
        if service not in self.costs:
            self.costs[service] = {"total": 0, "details": []}
        self.costs[service]["total"] += amount
        if detail:
            self.costs[service]["details"].append(detail)

    def track_claude_call(self, model, input_tokens, output_tokens):
        """Track a Claude API call cost."""
        if "opus" in model.lower():
            cost = (input_tokens / 1000 * COST_RATES["claude_opus_input_1k"] +
                    output_tokens / 1000 * COST_RATES["claude_opus_output_1k"])
            self.add("claude_opus", cost, f"{input_tokens}in/{output_tokens}out")
        else:
            cost = (input_tokens / 1000 * COST_RATES["claude_sonnet_input_1k"] +
                    output_tokens / 1000 * COST_RATES["claude_sonnet_output_1k"])
            self.add("claude_sonnet", cost, f"{input_tokens}in/{output_tokens}out")

    def track_tts(self, provider, char_count):
        """Track TTS cost."""
        rate_key = {
            "elevenlabs": "elevenlabs_per_char",
            "sarvam": "sarvam_per_char",
            "openai": "openai_tts_per_char",
        }.get(provider, "openai_tts_per_char")
        cost = char_count * COST_RATES[rate_key]
        self.add(f"tts_{provider}", cost, f"{char_count} chars")

    def track_veo(self, num_clips, hero_full=False):
        """Track Veo video generation cost.

        With VEO_HERO_FULL=1: clip #1 uses full quality (~$3.20), rest are Fast (~$0.96).
        Without: all clips Fast (~$0.96 each at 1080p).
        """
        per_fast = COST_RATES.get("veo_per_clip", 0.96)
        per_full = COST_RATES.get("veo_full_per_clip", 3.20)
        if hero_full and num_clips > 0:
            cost = per_full + (num_clips - 1) * per_fast
            self.add("veo_clips", cost, f"1 full + {num_clips - 1} fast")
        else:
            cost = num_clips * per_fast
            self.add("veo_clips", cost, f"{num_clips} clips")

    def track_kling(self, num_clips):
        """Track Kling (fal.ai) fallback video generation cost."""
        cost = num_clips * COST_RATES["kling_per_clip"]
        self.add("kling_clips", cost, f"{num_clips} clips (fallback)")

    def track_replicate(self):
        """Track Replicate ACE-Step music generation cost."""
        self.add("replicate_music", COST_RATES["replicate_ace_step"], "1 generation")

    def track_blog_images(self, num_images):
        """Track Replicate FLUX image generation cost for blog."""
        cost = num_images * COST_RATES["replicate_flux_image"]
        self.add("blog_images", cost, f"{num_images} images")

    def track_whisper(self, duration_sec):
        """Track Whisper transcription cost."""
        cost = (duration_sec / 60) * COST_RATES["whisper_per_minute"]
        self.add("whisper", cost, f"{duration_sec:.0f}s")

    def track_gemini_image(self):
        """Track Gemini image generation cost (AI thumbnail)."""
        self.add("gemini_thumbnail", COST_RATES["gemini_image"], "1 thumbnail")

    def total(self):
        """Total cost in USD."""
        return sum(v["total"] for v in self.costs.values())

    def summary(self):
        """Print cost summary."""
        lines = ["   💰 Cost Breakdown:"]
        for service, data in sorted(self.costs.items()):
            lines.append(f"      {service}: ${data['total']:.4f}")
        lines.append(f"      ─────────────────")
        lines.append(f"      TOTAL: ${self.total():.4f}")
        lines.append(f"      Duration: {(time.time() - self.start_time) / 60:.1f} min")
        return "\n".join(lines)

    def save(self, topic, title):
        """Append cost data to tracker file."""
        entry = {
            "date": datetime.now().isoformat(),
            "topic": topic,
            "title": title,
            "total_usd": round(self.total(), 4),
            "duration_min": round((time.time() - self.start_time) / 60, 1),
            "breakdown": {k: round(v["total"], 4) for k, v in self.costs.items()},
        }

        history = []
        if os.path.exists(COST_TRACKER_FILE):
            try:
                with open(COST_TRACKER_FILE, "r") as f:
                    history = json.load(f)
            except Exception:
                pass

        history.append(entry)
        with open(COST_TRACKER_FILE, "w") as f:
            json.dump(history, f, indent=2)

        print(f"   💾 Cost logged: ${self.total():.4f} → {COST_TRACKER_FILE}")

    @staticmethod
    def check_daily_limit():
        """Check if today's total spend exceeds DAILY_COST_LIMIT_USD.
        Returns (within_limit: bool, today_spend: float)."""
        if not os.path.exists(COST_TRACKER_FILE):
            return True, 0.0
        try:
            with open(COST_TRACKER_FILE, "r") as f:
                history = json.load(f)
            today_str = datetime.now().strftime("%Y-%m-%d")
            today_spend = sum(
                e.get("total_usd", 0) for e in history
                if e.get("date", "").startswith(today_str)
            )
            return today_spend < DAILY_COST_LIMIT_USD, today_spend
        except Exception:
            return True, 0.0


# ═══════════════════════════════════════════════════════════════════════
# ENGAGEMENT FEEDBACK LOOP
# ═══════════════════════════════════════════════════════════════════════

def check_past_engagement(youtube):
    """Check engagement for videos uploaded ~48h ago.
    Saves performance data to engagement_history.json for future topic optimization."""
    if not youtube:
        return

    try:
        engagement = []
        if os.path.exists(ENGAGEMENT_FILE):
            with open(ENGAGEMENT_FILE, "r") as f:
                engagement = json.load(f)

        # Get recent uploads
        channels = youtube.channels().list(part="contentDetails", mine=True).execute()
        if not channels.get("items"):
            return
        uploads_playlist = channels["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

        playlist_items = youtube.playlistItems().list(
            part="snippet", playlistId=uploads_playlist, maxResults=10
        ).execute()

        already_tracked = {e["video_id"] for e in engagement}
        ist = pytz.timezone(TIMEZONE)
        now = datetime.now(ist)

        for item in playlist_items.get("items", []):
            vid_id = item["snippet"]["resourceId"]["videoId"]
            if vid_id in already_tracked:
                continue

            # Check if video was published ~48h ago
            pub_str = item["snippet"].get("publishedAt", "")
            if not pub_str:
                continue
            pub_utc = datetime.strptime(pub_str.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
            pub_utc = pytz.utc.localize(pub_utc)
            pub_ist = pub_utc.astimezone(ist)
            hours_since = (now - pub_ist).total_seconds() / 3600

            if hours_since < ENGAGEMENT_CHECK_DELAY_HOURS:
                continue  # Too early to check

            # Fetch stats
            video_resp = youtube.videos().list(
                part="statistics,snippet", id=vid_id
            ).execute()

            if not video_resp.get("items"):
                continue

            stats = video_resp["items"][0]["statistics"]
            snippet = video_resp["items"][0]["snippet"]

            entry = {
                "video_id": vid_id,
                "title": snippet.get("title", ""),
                "published_at": pub_str,
                "checked_at": now.isoformat(),
                "hours_since_publish": round(hours_since, 1),
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "publish_hour_ist": pub_ist.hour,
            }

            engagement.append(entry)
            print(f"   📊 Engagement tracked: {entry['title'][:40]}... → {entry['views']} views, {entry['likes']} likes ({hours_since:.0f}h)")

        # Save updated engagement data
        with open(ENGAGEMENT_FILE, "w") as f:
            json.dump(engagement, f, indent=2, ensure_ascii=False)

    except Exception as e:
        print(f"   ⚠️ Engagement check failed: {e}")


def get_top_performing_topics(n=5):
    """Return the top N performing topics based on engagement data.
    Used by the smart topic system to bias towards similar content."""
    if not os.path.exists(ENGAGEMENT_FILE):
        return []

    try:
        with open(ENGAGEMENT_FILE, "r") as f:
            engagement = json.load(f)

        if not engagement:
            return []

        # Sort by views (primary) and likes (secondary)
        sorted_entries = sorted(
            engagement,
            key=lambda e: (e.get("views", 0), e.get("likes", 0)),
            reverse=True,
        )

        return [e["title"] for e in sorted_entries[:n]]

    except Exception:
        return []


def get_top_performing_categories():
    """Analyze engagement data and return category names ranked by avg views.
    Maps video titles back to TOPIC_SERIES_TAGS categories via keyword matching."""
    if not os.path.exists(ENGAGEMENT_FILE):
        return []

    try:
        with open(ENGAGEMENT_FILE, "r") as f:
            engagement = json.load(f)

        if not engagement:
            return []

        # Aggregate views per category
        cat_stats = {}  # {category: [views, views, ...]}
        for entry in engagement:
            title_lower = entry.get("title", "").lower()
            views = entry.get("views", 0)
            for cat_name, cat_data in TOPIC_SERIES_TAGS.items():
                if any(kw in title_lower for kw in cat_data["keywords"]):
                    cat_stats.setdefault(cat_name, []).append(views)
                    break  # One category per video

        if not cat_stats:
            return []

        # Rank by average views
        ranked = sorted(
            cat_stats.items(),
            key=lambda x: sum(x[1]) / len(x[1]),
            reverse=True,
        )
        return [cat for cat, _ in ranked]

    except Exception:
        return []


def get_new_channel_total_views():
    """Return total views across all tracked videos on the new channel.
    Used to decide whether new channel has enough data (1L threshold)
    to override main channel signals for publish time and engagement."""
    if not os.path.exists(ENGAGEMENT_FILE):
        return 0
    try:
        with open(ENGAGEMENT_FILE, "r") as f:
            engagement = json.load(f)
        return sum(e.get("views", 0) for e in engagement)
    except Exception:
        return 0


# ═══════════════════════════════════════════════════════════════════════
# INSTAGRAM ENGAGEMENT FEEDBACK LOOP
# ═══════════════════════════════════════════════════════════════════════

def save_ig_upload_record(ig_media_id, title, topic, cover_meta=None):
    """Save Instagram media ID after upload for later engagement checking."""
    try:
        records = []
        if os.path.exists(IG_ENGAGEMENT_FILE):
            with open(IG_ENGAGEMENT_FILE, "r") as f:
                records = json.load(f)

        ist = pytz.timezone(TIMEZONE)
        now = datetime.now(ist)

        rec = {
            "media_id": str(ig_media_id),
            "title": title,
            "topic": topic,
            "published_at": now.isoformat(),
            "checked": False,
        }
        # Cover-design metadata — lets the weekly thumbnail research learn which covers work
        if cover_meta and cover_meta.get("cover_text"):
            rec["cover_text"] = cover_meta.get("cover_text")
            rec["cover_color"] = cover_meta.get("cover_color")
            rec["cover_path"] = cover_meta.get("cover_path")
            rec["cover_face"] = cover_meta.get("cover_face")
        records.append(rec)

        with open(IG_ENGAGEMENT_FILE, "w") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        print(f"   📝 Instagram media ID saved for engagement tracking")
    except Exception as e:
        print(f"   ⚠️ Failed to save IG upload record: {e}")


def generate_ig_carousel_draft(claude_client, cost_tracker, blog_title, blog_url, blog_slug,
                                topic, script_english, tags, uploaded_filenames=None):
    """Generate an IG carousel post draft for tomorrow morning's auto-publish.

    Daily-bot already cross-posts the Veo Short as a Reel. This adds a SECOND
    post format — a static carousel of the 3 AI blog images with a B2B-tuned
    caption — published 16h later (next morning 10 AM IST) so the same
    account doesn't fire two API posts in the same hour.

    Returns the saved draft path, or None on failure. Failure is non-fatal.
    """
    print("   📷 IG carousel: Drafting next-morning post...")
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")
    os.makedirs(IG_CAROUSEL_DRAFTS_DIR, exist_ok=True)
    out_path = os.path.join(IG_CAROUSEL_DRAFTS_DIR, f"{today}.json")

    # Build image URLs only for images actually uploaded to S3.
    # If a provider (Replicate/fal.ai) fails, those files are never uploaded and
    # the IG API returns "Only photo or video can be accepted as media type" on the
    # 403 S3 URL. IG carousel requires ≥2 images; skip the draft if we can't meet that.
    # IG Graph API rejects .webp — use the .jpg copies uploaded alongside each .webp.
    if uploaded_filenames and len(uploaded_filenames) >= 2:
        ig_filenames = [fn.replace(".webp", ".jpg") for fn in uploaded_filenames]
        image_urls = [f"{BLOG_BASE_URL}/p/{blog_slug}-{fn}" for fn in ig_filenames]
    elif uploaded_filenames and len(uploaded_filenames) == 1:
        # Only hero was generated (provider partial failure). Post a SINGLE image
        # instead of a degenerate carousel with the same photo twice (user saw
        # duplicate slides on 2026-07-06 — never again).
        hero_jpg = f"{BLOG_BASE_URL}/p/{blog_slug}-{uploaded_filenames[0].replace('.webp', '.jpg')}"
        image_urls = [hero_jpg]
        print("   ⚠️ IG carousel: Only 1 image uploaded — draft will post as a single image")
    else:
        # Fallback when no image info is passed (pre-fix callers / all 3 generated)
        image_urls = [
            f"{BLOG_BASE_URL}/p/{blog_slug}-hero.jpg",
            f"{BLOG_BASE_URL}/p/{blog_slug}-img1.jpg",
            f"{BLOG_BASE_URL}/p/{blog_slug}-img2.jpg",
        ]

    tags_str = ", ".join(tags) if tags else "none"
    prompt = f"""You are writing an Instagram CAROUSEL caption for a B2B Indian textile manufacturer (Sale91.com / BulkPlainTshirt.com — plain t-shirts, hoodies, blanks for printing businesses).

CONTEXT:
- Today's blog: "{blog_title}"
- Blog URL: {blog_url}
- Topic: {topic}
- Source script (English): {script_english}
- Tags: {tags_str}

GOAL: Write a carousel post caption (NOT a Reel caption — different format) that gets B2B printers + streetwear brand owners to STOP scrolling and read. The carousel has 3 images already (hero, secondary, takeaway). The caption is what makes them save the post.

OUTPUT FORMAT: Return ONLY a valid JSON object (no preamble, no markdown fences):

{{
  "caption": "<60-80 word caption in this exact structure:\\n\\nLine 1: ONE-LINE HOOK with 1-2 emojis — pain point or surprising number ('Lost ₹40k on a 500-piece order. Here's why.' or 'This GSM mistake destroyed 1000 tees 😬')\\n\\nLines 2-4: 3-4 lines telling the story or lesson. Use specific numbers (₹ amounts, GSM grades, piece counts). Conversational tone, first-person OK ('We learned the hard way...').\\n\\nLine 5: ONE-LINE CTA — 'Save this post if you're sourcing for your next bulk order.' or 'Tag a printer who needs to see this 👇'\\n\\nUse \\\\n for line breaks. 1-2 emojis MAX. NO marketing-pitch words (premium, journey, transform, etc.).>",
  "hashtags": [
    "<list of 12-15 hashtags total>",
    "<5 niche-specific to the topic — e.g. #240gsm #dtgprinting #screenprinting>",
    "<5 mid-tier general B2B textile — e.g. #wholesaletshirts #plaintshirt #bulktshirt>",
    "<5 broad Indian B2B — e.g. #indianmanufacturer #b2bindia #sale91 #bulkplaintshirt>"
  ]
}}

CAPTION RULES:
- 60-80 words total (Instagram carousels read better short than long).
- First-person voice OK ('We made this mistake...').
- Specific numbers (₹40k, 240 GSM, 500 pieces) — never vague claims.
- 1-2 emojis MAX, placed naturally (😬 after a bad number, 👇 before a CTA).
- End with ONE CTA: 'Save this post' / 'Tag a printer' / 'Share with your team'.
- DO NOT include the blog URL in the caption — IG caption links aren't clickable; use the bio.
- DO NOT mention "Sale91" or "BulkPlainTshirt" in the caption — let the profile + images speak.

HASHTAG RULES:
- Mix specific + general (Instagram rewards this distribution).
- 12-15 total (sweet spot for IG reach without looking spammy).
- Lowercase, no spaces.
- Always include #bulkplaintshirt + #sale91 once (low-volume branded tags help with discovery).

Return ONLY the JSON object."""

    try:
        resp = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        if raw.lower().startswith("json"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[4:]
        parsed = json.loads(raw)

        if cost_tracker and hasattr(cost_tracker, 'track_claude_call'):
            cost_tracker.track_claude_call("sonnet", resp.usage.input_tokens, resp.usage.output_tokens)

        draft = {
            "date": today,
            "blog_title": blog_title,
            "blog_url": blog_url,
            "blog_slug": blog_slug,
            "image_urls": image_urls,
            "caption": parsed.get("caption", ""),
            "hashtags": parsed.get("hashtags", []),
            "posted": False,
            "media_id": None,
            "error": None,
        }
        with open(out_path, "w") as f:
            json.dump(draft, f, ensure_ascii=False, indent=2)
        print(f"   ✅ IG carousel: Draft saved → {out_path}")
        return out_path
    except Exception as e:
        print(f"   ⚠️ IG carousel: Draft generation failed (non-fatal): {e}")
        return None


def publish_ig_carousel(image_urls, caption, hashtags=None):
    """Publish an Instagram carousel via the IG Graph API.

    3-step flow per Meta docs:
    1. Create one child container per image (image_url + is_carousel_item=true)
    2. Create one parent container (media_type=CAROUSEL, children=[...], caption=...)
    3. POST /{ig-user-id}/media_publish with creation_id of the parent

    Returns IG media_id on success, None on failure. Non-fatal — caller logs.
    """
    ig_token = (os.environ.get("INSTAGRAM_ACCESS_TOKEN") or "").strip()
    ig_business_id = (os.environ.get("INSTAGRAM_BUSINESS_ID") or "").strip()

    if not ig_token or not ig_business_id:
        print("   ℹ️ IG carousel: skipped (no INSTAGRAM_ACCESS_TOKEN/INSTAGRAM_BUSINESS_ID)")
        return None

    # Dedupe (order-preserving) — old drafts may contain the same hero twice;
    # a carousel of identical slides must never reach the account again.
    image_urls = list(dict.fromkeys(image_urls or []))
    if not image_urls:
        print("   ⚠️ IG carousel: no images to post")
        return None

    if len(image_urls) == 1:
        # Single-image fallback: plain photo post instead of a fake carousel
        print("   📷 IG carousel: 1 unique image — publishing as a single photo post")
        full_caption = caption.strip()
        if hashtags:
            hashtag_line = " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags)
            full_caption = f"{full_caption}\n\n{hashtag_line}"
        base = f"https://graph.facebook.com/{IG_API_VERSION}/{ig_business_id}"
        try:
            c_data = {"image_url": image_urls[0], "caption": full_caption,
                      "is_ai_generated": "true", "access_token": ig_token}
            resp = requests.post(f"{base}/media", data=c_data, timeout=30)
            if resp.status_code != 200 and "is_ai_generated" in c_data:
                c_data.pop("is_ai_generated")
                resp = requests.post(f"{base}/media", data=c_data, timeout=30)
            if resp.status_code != 200:
                print(f"   ❌ IG single photo: container failed: {resp.text[:200]}")
                return None
            cid = resp.json().get("id")
            pub = requests.post(f"{base}/media_publish",
                                data={"creation_id": cid, "access_token": ig_token}, timeout=30)
            if pub.status_code != 200:
                print(f"   ❌ IG single photo: publish failed: {pub.text[:200]}")
                return None
            media_id = pub.json().get("id")
            print(f"   ✅ IG single photo: PUBLISHED → media_id {media_id}")
            return media_id
        except Exception as e:
            print(f"   ⚠️ IG single photo: unexpected error: {e}")
            return None

    # Assemble the full caption (caption text + blank line + hashtags)
    full_caption = caption.strip()
    if hashtags:
        hashtag_line = " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags)
        full_caption = f"{full_caption}\n\n{hashtag_line}"

    base = f"https://graph.facebook.com/{IG_API_VERSION}/{ig_business_id}"
    try:
        # Step 1: Create child containers for each image
        child_ids = []
        for i, img_url in enumerate(image_urls[:10]):  # IG limit = 10 carousel items
            resp = requests.post(
                f"{base}/media",
                data={
                    "image_url": img_url,
                    "is_carousel_item": "true",
                    "access_token": ig_token,
                },
                timeout=30,
            )
            if resp.status_code != 200:
                err = resp.json().get("error", {})
                print(f"   ❌ IG carousel: child {i+1} container failed: {err.get('message', resp.text[:200])}")
                return None
            cid = resp.json().get("id")
            if not cid:
                print(f"   ❌ IG carousel: child {i+1} returned no id: {resp.text[:200]}")
                return None
            child_ids.append(cid)
            print(f"   📦 IG carousel: child container {i+1}/{len(image_urls)} → {cid}")

        # Step 2: Create parent CAROUSEL container
        parent_resp = requests.post(
            f"{base}/media",
            data={
                "media_type": "CAROUSEL",
                "children": ",".join(child_ids),
                "caption": full_caption,
                "access_token": ig_token,
            },
            timeout=30,
        )
        if parent_resp.status_code != 200:
            err = parent_resp.json().get("error", {})
            print(f"   ❌ IG carousel: parent container failed: {err.get('message', parent_resp.text[:200])}")
            return None
        parent_id = parent_resp.json().get("id")
        if not parent_id:
            print(f"   ❌ IG carousel: parent returned no id: {parent_resp.text[:200]}")
            return None
        print(f"   📦 IG carousel: parent CAROUSEL container → {parent_id}")

        # Step 2.5: Wait for parent container to reach FINISHED before publishing.
        # Publishing too early returns "Media ID is not available" — this missing
        # poll caused 5 of the last 14 carousel runs to fail.
        for check in range(10):
            status_resp = requests.get(
                f"{base.rsplit('/', 1)[0]}/{parent_id}",
                params={"fields": "status_code", "access_token": ig_token},
                timeout=30,
            )
            status_code = status_resp.json().get("status_code") if status_resp.status_code == 200 else None
            if status_code == "FINISHED":
                print(f"   ✅ IG carousel: parent container FINISHED")
                break
            if status_code == "ERROR":
                print(f"   ❌ IG carousel: parent container ERROR: {status_resp.text[:200]}")
                return None
            print(f"   ⏳ IG carousel: parent {status_code or 'PENDING'} — waiting 15s ({check+1}/10)")
            time.sleep(15)

        # Step 3: Publish (retry — container can need a little extra time)
        media_id = None
        for attempt in range(3):
            pub_resp = requests.post(
                f"{base}/media_publish",
                data={"creation_id": parent_id, "access_token": ig_token},
                timeout=30,
            )
            if pub_resp.status_code == 200:
                media_id = pub_resp.json().get("id")
                if media_id:
                    break
            err = pub_resp.json().get("error", {})
            print(f"   ⚠️ IG carousel: publish attempt {attempt+1}/3 failed: {err.get('message', pub_resp.text[:200])}")
            if attempt < 2:
                time.sleep(20)
        if not media_id:
            print(f"   ❌ IG carousel: publish failed after 3 attempts")
            return None
        print(f"   ✅ IG carousel: PUBLISHED → media_id {media_id}")
        return media_id
    except Exception as e:
        print(f"   ⚠️ IG carousel: unexpected error: {e}")
        return None


def post_latest_ig_carousel():
    """Read the latest ig_drafts/*.json and publish it to IG.

    Called by the ig_carousel.yml workflow each morning at 4:30 UTC (10 AM IST).
    Marks the draft as posted=true and stores the media_id, then commits the
    updated JSON so the auto-verifier can detect successful publish.
    """
    print("\n" + "=" * 60)
    print("  📷 IG CAROUSEL POST — morning auto-publish")
    print("=" * 60)

    if not os.path.isdir(IG_CAROUSEL_DRAFTS_DIR):
        print(f"   ℹ️ No {IG_CAROUSEL_DRAFTS_DIR}/ folder yet — nothing to post")
        return False

    # Find the most recent UNPOSTED draft (any date)
    candidates = sorted(
        [f for f in os.listdir(IG_CAROUSEL_DRAFTS_DIR) if f.endswith(".json")],
        reverse=True,
    )
    target = None
    target_data = None
    for fname in candidates:
        path = os.path.join(IG_CAROUSEL_DRAFTS_DIR, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            if not data.get("posted"):
                target = path
                target_data = data
                break
        except Exception:
            continue

    if not target_data:
        print("   ℹ️ No unposted IG carousel draft found — nothing to post today")
        return True  # no-op is not an error; exit 0 so workflow stays green

    print(f"   📰 Posting draft: {target}")
    print(f"   📰 Blog: {target_data.get('blog_title', '')[:80]}")
    print(f"   🖼️  Images: {len(target_data.get('image_urls', []))}")

    # Auto-refresh IG token if it's expiring soon (existing helper)
    if 'refresh_instagram_token_if_needed' in globals():
        refresh_instagram_token_if_needed()

    media_id = publish_ig_carousel(
        image_urls=target_data["image_urls"],
        caption=target_data.get("caption", ""),
        hashtags=target_data.get("hashtags", []),
    )

    # Update the draft with the result
    target_data["posted"] = bool(media_id)
    target_data["media_id"] = media_id
    target_data["posted_at"] = datetime.now(pytz.timezone(TIMEZONE)).isoformat() if media_id else None
    target_data["error"] = None if media_id else "publish_ig_carousel returned None — check logs"
    with open(target, "w") as f:
        json.dump(target_data, f, ensure_ascii=False, indent=2)
    print(f"   💾 Draft updated: posted={target_data['posted']}, media_id={media_id}")
    return bool(media_id)


def check_instagram_engagement():
    """Check engagement for Instagram Reels uploaded ~48h ago.
    Uses Instagram Graph API to fetch insights (views, likes, comments, shares, reach).
    Saves performance data to ig_engagement_history.json for future topic optimization."""
    ig_token = (os.environ.get("INSTAGRAM_ACCESS_TOKEN") or "").strip()
    if not ig_token:
        return

    if not os.path.exists(IG_ENGAGEMENT_FILE):
        return

    try:
        with open(IG_ENGAGEMENT_FILE, "r") as f:
            records = json.load(f)

        if not records:
            return

        ist = pytz.timezone(TIMEZONE)
        now = datetime.now(ist)
        updated = False

        for record in records:
            if record.get("checked"):
                continue

            # Check if enough time has passed since upload
            pub_str = record.get("published_at", "")
            if not pub_str:
                continue
            try:
                pub_time = datetime.fromisoformat(pub_str)
                if pub_time.tzinfo is None:
                    pub_time = ist.localize(pub_time)
            except Exception:
                continue

            hours_since = (now - pub_time).total_seconds() / 3600
            if hours_since < IG_ENGAGEMENT_CHECK_DELAY_HOURS:
                continue  # Too early

            media_id = record.get("media_id", "")
            if not media_id:
                continue
            # Test containers (test:xxx) are not published — skip engagement check
            if media_id.startswith("test:"):
                record["checked"] = True
                continue
            # Scheduled posts store the CONTAINER id ("scheduled:<id>").
            # After IG auto-publishes, the container is NOT insight-queryable —
            # resolve to the real media id by matching a REELS media published
            # within 12h after container creation.
            if media_id.startswith("scheduled:"):
                container_id = media_id.replace("scheduled:", "", 1)
                resolved = None
                ig_biz = (os.environ.get("INSTAGRAM_BUSINESS_ID") or "").strip()
                if ig_biz:
                    try:
                        m_resp = requests.get(
                            f"https://graph.facebook.com/{IG_API_VERSION}/{ig_biz}/media",
                            params={
                                "fields": "id,timestamp,media_product_type",
                                "limit": 15,
                                "access_token": ig_token,
                            },
                            timeout=15,
                        )
                        candidates = []
                        for m in m_resp.json().get("data", []):
                            if m.get("media_product_type") != "REELS":
                                continue
                            try:
                                ts = datetime.fromisoformat(
                                    m.get("timestamp", "").replace("+0000", "+00:00")
                                ).astimezone(ist)
                            except Exception:
                                continue
                            delta_h = (ts - pub_time).total_seconds() / 3600
                            if 0 <= delta_h <= 12:
                                candidates.append((delta_h, m["id"]))
                        if candidates:
                            resolved = min(candidates)[1]
                    except Exception:
                        pass
                if resolved:
                    media_id = resolved
                    record["media_id"] = resolved
                    updated = True
                else:
                    media_id = container_id  # 3-strikes below retires it if truly gone

            # Fetch media insights from Instagram Graph API
            try:
                # Get basic media metrics
                media_resp = requests.get(
                    f"https://graph.facebook.com/{IG_API_VERSION}/{media_id}",
                    params={
                        "fields": "like_count,comments_count,timestamp,media_type,media_product_type",
                        "access_token": ig_token,
                    },
                    timeout=15,
                )
                media_data = media_resp.json()

                if "error" in media_data:
                    err_msg = media_data["error"].get("message", "")
                    print(f"   ⚠️ IG insights failed for {media_id}: {err_msg}")
                    fails = record.get("insight_fail_count", 0) + 1
                    record["insight_fail_count"] = fails
                    updated = True
                    if fails >= 3:
                        record["checked"] = True
                        record["check_failed"] = True
                        print(f"   🪦 {media_id}: {fails} strikes — marking checked (media likely deleted)")
                    continue

                # Fetch Reels-specific insights.
                # NOTE: Meta deprecated `plays` for Reels in API v18+ (mid-2023).
                # Reels published after that date return 0 for `plays`; must use `views`.
                # We try `views` first (current); fall back to `plays` for older Reels.
                ig_views = 0
                ig_reach = 0
                ig_saves = 0
                ig_shares = 0

                def _fetch_metrics(metric_csv):
                    return requests.get(
                        f"https://graph.facebook.com/{IG_API_VERSION}/{media_id}/insights",
                        params={"metric": metric_csv, "access_token": ig_token},
                        timeout=15,
                    )

                # Primary: views (Reels-current metric)
                primary = _fetch_metrics("views,reach,saved,shares")
                primary_data = primary.json()
                if "error" in primary_data:
                    err = primary_data["error"].get("message", "")
                    # If `views` not supported on this media (old account/Reel), retry with plays
                    if "metric" in err.lower() or "views" in err.lower():
                        fallback = _fetch_metrics("plays,reach,saved,shares")
                        primary_data = fallback.json()
                    else:
                        print(f"   ⚠️ IG insights API error for {media_id}: {err[:140]}")

                for metric in primary_data.get("data", []):
                    name = metric.get("name", "")
                    values = metric.get("values", [{}])
                    val = values[0].get("value", 0) if values else 0
                    # Both names map to ig_views (whichever the API returned)
                    if name in ("views", "plays"):
                        ig_views = val
                    elif name == "reach":
                        ig_reach = val
                    elif name == "saved":
                        ig_saves = val
                    elif name == "shares":
                        ig_shares = val

                # If still zero views after both attempts AND it's been >24h since publish,
                # log a debug dump so future runs can see what Meta is returning.
                if ig_views == 0 and hours_since > 24:
                    print(f"   🔎 IG insights debug for {media_id}: {json.dumps(primary_data)[:300]}")

                ig_likes = media_data.get("like_count", 0)
                ig_comments = media_data.get("comments_count", 0)

                # Update record with engagement data
                record["checked"] = True
                record["checked_at"] = now.isoformat()
                record["hours_since_publish"] = round(hours_since, 1)
                record["views"] = ig_views
                record["reach"] = ig_reach
                record["likes"] = ig_likes
                record["comments"] = ig_comments
                record["shares"] = ig_shares
                record["saves"] = ig_saves
                updated = True

                title_short = record.get("title", "")[:40]
                print(f"   📸 IG engagement: {title_short}... → {ig_views} views, {ig_likes} likes, {ig_shares} shares, {ig_reach} reach ({hours_since:.0f}h)")

            except Exception as e:
                print(f"   ⚠️ IG insights fetch failed for {media_id}: {e}")

        if updated:
            with open(IG_ENGAGEMENT_FILE, "w") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)

    except Exception as e:
        print(f"   ⚠️ Instagram engagement check failed: {e}")


def get_top_performing_ig_topics(n=5):
    """Return top N performing topics on Instagram based on engagement data.
    Used to understand what content resonates differently on Instagram vs YouTube."""
    if not os.path.exists(IG_ENGAGEMENT_FILE):
        return []

    try:
        with open(IG_ENGAGEMENT_FILE, "r") as f:
            records = json.load(f)

        checked = [r for r in records if r.get("checked") and not r.get("check_failed")]
        if not checked:
            return []

        # Sort by views (primary) and likes (secondary)
        sorted_entries = sorted(
            checked,
            key=lambda e: (e.get("views", 0), e.get("likes", 0)),
            reverse=True,
        )

        return [e["title"] for e in sorted_entries[:n]]

    except Exception:
        return []


def get_top_performing_ig_categories():
    """Analyze Instagram engagement data and return category names ranked by avg views.
    Instagram audience may prefer different categories than YouTube."""
    if not os.path.exists(IG_ENGAGEMENT_FILE):
        return []

    try:
        with open(IG_ENGAGEMENT_FILE, "r") as f:
            records = json.load(f)

        checked = [r for r in records if r.get("checked") and not r.get("check_failed")]
        if not checked:
            return []

        # Aggregate views per category
        cat_stats = {}
        for entry in checked:
            title_lower = entry.get("title", "").lower()
            views = entry.get("views", 0)
            for cat_name, cat_data in TOPIC_SERIES_TAGS.items():
                if any(kw in title_lower for kw in cat_data["keywords"]):
                    cat_stats.setdefault(cat_name, []).append(views)
                    break

        if not cat_stats:
            return []

        ranked = sorted(
            cat_stats.items(),
            key=lambda x: sum(x[1]) / len(x[1]),
            reverse=True,
        )
        return [cat for cat, _ in ranked]

    except Exception:
        return []


def get_ig_engagement_summary():
    """Rich Instagram engagement analytics — the PRIMARY feedback signal for content decisions.
    Returns a dict with top Reels, save/share rates, averages, and quality/viral title lists.
    All downstream functions (thumbnail, title, script, topic) consume this single summary."""
    empty = {"total_reels_analyzed": 0, "top_reels": [], "top_by_saves": [],
             "top_by_shares": [], "avg_metrics": {}, "high_quality_titles": [], "viral_titles": []}
    if not os.path.exists(IG_ENGAGEMENT_FILE):
        return empty
    try:
        with open(IG_ENGAGEMENT_FILE, "r") as f:
            records = json.load(f)
        checked = [r for r in records if r.get("checked") and not r.get("check_failed")]
        if not checked:
            return empty

        # Compute derived rates for each record
        for r in checked:
            views = max(r.get("views", 0), 1)  # avoid div by zero
            r["save_rate"] = round(r.get("saves", 0) / views, 4)
            r["share_rate"] = round(r.get("shares", 0) / views, 4)
            r["engagement_rate"] = round(
                (r.get("likes", 0) + r.get("comments", 0) + r.get("shares", 0) + r.get("saves", 0)) / views, 4
            )

        # Top 5 by views
        by_views = sorted(checked, key=lambda r: r.get("views", 0), reverse=True)
        top_reels = [{
            "title": r.get("title", ""), "views": r.get("views", 0),
            "saves": r.get("saves", 0), "shares": r.get("shares", 0),
            "save_rate": r.get("save_rate", 0), "share_rate": r.get("share_rate", 0),
            "cover_text": r.get("cover_text", ""),
        } for r in by_views[:5]]

        # Top by save rate (min 100 views to avoid noise)
        qualified = [r for r in checked if r.get("views", 0) >= 100]
        top_by_saves = sorted(qualified, key=lambda r: r.get("save_rate", 0), reverse=True)[:5]
        top_by_saves = [{"title": r.get("title", ""), "save_rate": r["save_rate"],
                         "views": r.get("views", 0), "saves": r.get("saves", 0)} for r in top_by_saves]

        # Top by share rate (min 100 views)
        top_by_shares = sorted(qualified, key=lambda r: r.get("share_rate", 0), reverse=True)[:5]
        top_by_shares = [{"title": r.get("title", ""), "share_rate": r["share_rate"],
                          "views": r.get("views", 0), "shares": r.get("shares", 0)} for r in top_by_shares]

        # Averages
        n = len(checked)
        avg_metrics = {
            "avg_views": round(sum(r.get("views", 0) for r in checked) / n),
            "avg_likes": round(sum(r.get("likes", 0) for r in checked) / n, 1),
            "avg_saves": round(sum(r.get("saves", 0) for r in checked) / n, 1),
            "avg_shares": round(sum(r.get("shares", 0) for r in checked) / n, 1),
            "avg_save_rate": round(sum(r.get("save_rate", 0) for r in checked) / n, 4),
            "avg_share_rate": round(sum(r.get("share_rate", 0) for r in checked) / n, 4),
        }

        # High quality titles (save_rate above average)
        avg_sr = avg_metrics["avg_save_rate"]
        high_quality_titles = [r.get("title", "") for r in checked if r.get("save_rate", 0) > avg_sr]

        # Viral titles (share_rate above average)
        avg_shr = avg_metrics["avg_share_rate"]
        viral_titles = [r.get("title", "") for r in checked if r.get("share_rate", 0) > avg_shr]

        return {
            "total_reels_analyzed": n,
            "top_reels": top_reels,
            "top_by_saves": top_by_saves,
            "top_by_shares": top_by_shares,
            "avg_metrics": avg_metrics,
            "high_quality_titles": high_quality_titles,
            "viral_titles": viral_titles,
        }
    except Exception:
        return empty


# ═══════════════════════════════════════════════════════════════════════
# SOURCE CHANNEL INSIGHTS (read-only from existing 50K channel)
# ═══════════════════════════════════════════════════════════════════════

def fetch_source_channel_insights():
    """Fetch top videos from the source YouTube channel (read-only).
    Uses YouTube Data API v3: search.list + videos.list.
    Caches results for 24h to avoid unnecessary API calls.
    Returns list of dicts: [{title, views, likes, comments, published}]"""

    if not SOURCE_CHANNEL_ID or not SOURCE_CHANNEL_API_KEY:
        print("   📊 Source channel: skipped (CHANNEL_ID_2 or YOUTUBE_API_KEY_1 not set)")
        return []

    # Check cache — refresh only once per 24h
    if os.path.exists(SOURCE_CHANNEL_CACHE_FILE):
        try:
            with open(SOURCE_CHANNEL_CACHE_FILE, "r") as f:
                cache = json.load(f)
            cached_at = cache.get("fetched_at", "")
            if cached_at:
                age_hours = (datetime.now() - datetime.fromisoformat(cached_at)).total_seconds() / 3600
                if age_hours < 24:
                    print(f"   📊 Source channel insights from cache ({len(cache.get('videos', []))} videos, {age_hours:.0f}h old)")
                    return cache.get("videos", [])
        except Exception as e:
            print(f"   📊 Source channel cache read failed: {e}")

    print("   📊 Fetching source channel data (YouTube Data API — read only)...")
    base_url = "https://www.googleapis.com/youtube/v3"

    try:
        # Step 1: Get video IDs from channel (search.list — 100 quota units)
        search_resp = requests.get(f"{base_url}/search", params={
            "key": SOURCE_CHANNEL_API_KEY,
            "channelId": SOURCE_CHANNEL_ID,
            "part": "id",
            "order": "date",
            "maxResults": 50,
            "type": "video",
        }, timeout=15)
        search_resp.raise_for_status()
        search_data = search_resp.json()

        video_ids = [item["id"]["videoId"] for item in search_data.get("items", []) if item["id"].get("videoId")]
        if not video_ids:
            print("   ⚠️ No videos found on source channel")
            return []

        # Step 2: Get video details with duration to filter Shorts (videos.list)
        videos_resp = requests.get(f"{base_url}/videos", params={
            "key": SOURCE_CHANNEL_API_KEY,
            "id": ",".join(video_ids),
            "part": "snippet,statistics,contentDetails",
        }, timeout=15)
        videos_resp.raise_for_status()
        videos_data = videos_resp.json()

        import re as _re
        videos = []
        for item in videos_data.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            # Filter for Shorts only: duration <= 60s and vertical aspect
            duration_str = item.get("contentDetails", {}).get("duration", "")
            # Parse ISO 8601 duration (e.g., PT45S, PT1M, PT1M30S)
            duration_match = _re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str)
            if duration_match:
                hours = int(duration_match.group(1) or 0)
                minutes = int(duration_match.group(2) or 0)
                seconds = int(duration_match.group(3) or 0)
                total_seconds = hours * 3600 + minutes * 60 + seconds
            else:
                total_seconds = 999  # Unknown duration — skip
            # YouTube Shorts are <= 60 seconds
            if total_seconds > 60:
                continue
            videos.append({
                "video_id": item.get("id", ""),
                "title": snippet.get("title", ""),
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "published": snippet.get("publishedAt", ""),
                "duration_seconds": total_seconds,
            })

        # Sort by views descending
        videos.sort(key=lambda v: v["views"], reverse=True)

        # Cache results
        cache_data = {
            "fetched_at": datetime.now().isoformat(),
            "channel_id": SOURCE_CHANNEL_ID,
            "videos": videos,
        }
        with open(SOURCE_CHANNEL_CACHE_FILE, "w") as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)

        if not videos:
            print("   ⚠️ No Shorts found on source channel (all videos were >60s)")
            return []
        print(f"   ✅ Fetched {len(videos)} Shorts from source channel (top: {videos[0]['views']:,} views)")
        return videos

    except Exception as e:
        print(f"   ⚠️ Source channel fetch failed: {e}")
        return []


def fetch_latest_main_channel_long_form(within_hours=48):
    """Fetch the most recent LONG-FORM (>60s) video from SOURCE_CHANNEL_ID.

    Used by the Sunday recap flow to pick up the user's Saturday upload on
    their main 40K-subs channel. Returns dict with:
      video_id, title, description, tags, published_at, vid_url, hours_old
    or None if no qualifying video found in `within_hours` window.

    The 48h default catches anything posted from Friday onwards in IST when
    the Sunday workflow fires at 19:00 IST Sunday.
    """
    if not SOURCE_CHANNEL_ID or not SOURCE_CHANNEL_API_KEY:
        print("   📺 Main channel: CHANNEL_ID_2 or YOUTUBE_API_KEY_1 missing — cannot fetch")
        return None

    base_url = "https://www.googleapis.com/youtube/v3"
    try:
        # Step 1: Search latest videos by date (cheaper than fetching all)
        search_resp = requests.get(f"{base_url}/search", params={
            "key": SOURCE_CHANNEL_API_KEY,
            "channelId": SOURCE_CHANNEL_ID,
            "part": "id",
            "order": "date",
            "maxResults": 10,
            "type": "video",
        }, timeout=15)
        search_resp.raise_for_status()
        video_ids = [i["id"]["videoId"] for i in search_resp.json().get("items", []) if i["id"].get("videoId")]
        if not video_ids:
            print("   📺 Main channel: no recent videos found")
            return None

        # Step 2: Get full details (snippet + duration)
        videos_resp = requests.get(f"{base_url}/videos", params={
            "key": SOURCE_CHANNEL_API_KEY,
            "id": ",".join(video_ids),
            "part": "snippet,contentDetails",
        }, timeout=15)
        videos_resp.raise_for_status()

        import re as _re
        now = datetime.now(pytz.timezone(TIMEZONE))
        for item in videos_resp.json().get("items", []):
            snippet = item.get("snippet", {})
            duration_str = item.get("contentDetails", {}).get("duration", "PT0S")
            m = _re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str)
            if not m:
                continue
            total_s = int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)
            # Long-form = >60s (Shorts cutoff)
            if total_s <= 60:
                continue
            published_str = snippet.get("publishedAt", "")
            try:
                published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
                hours_old = (now - published.astimezone(pytz.timezone(TIMEZONE))).total_seconds() / 3600
            except Exception:
                hours_old = 999
            if hours_old > within_hours:
                continue
            vid_id = item.get("id", "")
            return {
                "video_id": vid_id,
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "tags": snippet.get("tags", []) or [],
                "published_at": published_str,
                "duration_seconds": total_s,
                "vid_url": f"https://www.youtube.com/watch?v={vid_id}",
                "hours_old": round(hours_old, 1),
            }
        print(f"   📺 Main channel: no long-form video uploaded in last {within_hours}h")
        return None
    except Exception as e:
        print(f"   ⚠️ Main channel: fetch failed: {e}")
        return None


def fetch_youtube_captions(video_id):
    """Phase 2: fetch caption transcript via authenticated YouTube Data API.

    Uses the existing OAuth token (force-ssl scope is already granted in
    generate_token.py) — no re-auth needed. The caller must be the video
    owner; since this is invoked against SOURCE_CHANNEL_ID (the user's own
    channel) by an OAuth token issued for that channel, ownership is met.

    Returns the plain-text transcript (timestamps stripped) or None if:
    - YouTube auth fails / token missing
    - The video has no caption tracks
    - The download endpoint denies access (rare on owned videos)

    Caption selection: prefers Hindi/English manual ("standard"), then any
    manual track, then auto-generated ("ASR"). Hinglish videos usually only
    have ASR — that's fine, ASR captures actual spoken words.
    """
    try:
        youtube = get_youtube_service()
        if youtube is None:
            print("   📝 Captions: YouTube auth unavailable — skipping")
            return None

        # 1. List caption tracks for the video
        try:
            list_resp = youtube.captions().list(part="snippet", videoId=video_id).execute()
        except Exception as e:
            print(f"   📝 Captions: list failed: {str(e)[:120]}")
            return None
        tracks = list_resp.get("items", [])
        if not tracks:
            print("   📝 Captions: no tracks available on this video")
            return None

        # 2. Pick best track
        manual_priority = []
        asr_fallback = []
        for t in tracks:
            snip = t.get("snippet", {})
            lang = snip.get("language", "")
            kind = snip.get("trackKind", "")
            if kind == "ASR":
                asr_fallback.append(t)
            elif lang in ("hi", "en"):
                manual_priority.insert(0, t)  # Hindi/English first
            else:
                manual_priority.append(t)
        track = (manual_priority + asr_fallback + tracks)[0]
        track_id = track["id"]
        track_kind = track.get("snippet", {}).get("trackKind", "standard")
        track_lang = track.get("snippet", {}).get("language", "?")
        print(f"   📝 Captions: selected track ({track_lang}/{track_kind})")

        # 3. Download the caption file (SRT format = easy to parse)
        try:
            raw = youtube.captions().download(id=track_id, tfmt="srt").execute()
        except Exception as e:
            print(f"   📝 Captions: download failed: {str(e)[:120]}")
            return None

        srt = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

        # 4. Strip SRT formatting → keep only spoken text
        import re as _re
        lines = []
        for block in srt.strip().split("\n\n"):
            block_lines = block.strip().split("\n")
            if len(block_lines) < 3:
                continue
            # block_lines[0] = index, [1] = timestamp range, [2:] = text
            text = " ".join(block_lines[2:])
            text = _re.sub(r'<[^>]+>', '', text)  # strip <i>, <b>, <c.colorBBBBBB>
            text = _re.sub(r'\{[^}]+\}', '', text)  # strip {\an8} style position tags
            text = text.strip()
            if text:
                lines.append(text)

        transcript = " ".join(lines).strip()
        if not transcript:
            return None
        print(f"   📝 Captions: extracted {len(transcript)} chars from {len(lines)} caption blocks")
        return transcript
    except Exception as e:
        print(f"   ⚠️ Captions: unexpected error: {str(e)[:120]}")
        return None


# Voice corpus — Phase 3 saves transcripts (or descriptions as fallback) of
# the user's main-channel videos here. Phase 4 reads this directory to
# extract style hints (vocabulary, ending patterns) for the daily script gen.
VOICE_CORPUS_DIR = "voice_corpus"
PHASE_STATUS_FILE = "phase_status.json"

# Self-learning voice artifacts (built by build_voice_models, committed to the
# repo like the other tracking JSONs so the daily CI run can read them):
#   voice_vocab.json            — frequency-ranked Devanagari words he uses
#   learned_pronunciations.json — auto roman→Devanagari map from his speech
VOICE_VOCAB_FILE = "voice_vocab.json"
LEARNED_PRON_FILE = "learned_pronunciations.json"
# Public long-form tab of the main channel — lets backfill list videos with
# yt-dlp when the YouTube API key isn't in the environment (local runs).
MAIN_CHANNEL_VIDEOS_URL = "https://www.youtube.com/@BulkPlainTshirt_com/videos"


def _write_phase_status(phase_id: int, status: str, result: str, details: str = ""):
    """Update a single phase's status in phase_status.json. Read-modify-write.

    status: 'active' | 'best-effort' | 'pending' | 'failed'
    result: short token like 'success' | 'fallback-used' | 'no-data-yet'
    details: human-readable details shown on the status tab
    """
    now_iso = datetime.now(pytz.timezone(TIMEZONE)).isoformat()
    try:
        if os.path.exists(PHASE_STATUS_FILE):
            with open(PHASE_STATUS_FILE) as f:
                data = json.load(f)
        else:
            data = {"phases": {}}
        data.setdefault("phases", {})
        data["phases"][str(phase_id)] = {
            "id": phase_id,
            "status": status,
            "last_run": now_iso,
            "last_result": result,
            "details": details,
        }
        data["last_update"] = now_iso
        with open(PHASE_STATUS_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"   ⚠️ Phase status write failed: {e}")


def _read_phase_status():
    """Load phase_status.json or return default skeleton."""
    if os.path.exists(PHASE_STATUS_FILE):
        try:
            with open(PHASE_STATUS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"phases": {}, "last_update": None}


# ── Self-learning voice (2026-07-10) ──────────────────────────────────────
# The corpus was learning from video DESCRIPTIONS (marketing copy) because
# captions().download() is denied on the main channel. These helpers capture
# Ketu's REAL spoken words instead, cheapest source first, and distill them
# into vocabulary + pronunciation artifacts the daily pipeline reads.


_CAPTIONS_IP_BLOCKED = False  # set once YouTube rate-limits caption fetches


def fetch_transcript_unauthenticated(video_id):
    """YouTube auto-captions via youtube-transcript-api — no OAuth needed,
    works on videos this bot doesn't own. Hindi ASR tracks come back in
    Devanagari = his actual spoken words. Same path .github/extract_vocab.py
    already used successfully on this channel (avoids yt-dlp bot-detection
    on CI runners). Hindi tracks only — an English track would be a
    translation, useless for pronunciation learning."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print("   📝 youtube-transcript-api not installed — skipping unauth captions")
        return None
    langs = ["hi", "hi-IN"]
    entries = None
    try:
        # Modern API (>= 1.0): instance method .fetch()
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=langs)
        entries = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else list(fetched)
    except Exception as e1:
        if type(e1).__name__ == "IpBlocked":
            # YouTube rate-limited this IP — every further request will fail
            # too. Flag it so batch callers (backfill) can stop early.
            global _CAPTIONS_IP_BLOCKED
            _CAPTIONS_IP_BLOCKED = True
        # Legacy API (< 1.0) only: don't let its AttributeError on modern
        # versions mask the real error above.
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            try:
                entries = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
            except Exception as e2:
                print(f"   📝 Unauth captions unavailable for {video_id}: {str(e2)[:120]}")
                return None
        else:
            print(f"   📝 Unauth captions unavailable for {video_id}: "
                  f"{type(e1).__name__} {str(e1)[:120]}")
            return None
    if not entries:
        return None

    def _snip(e):
        return e.get("text", "") if isinstance(e, dict) else getattr(e, "text", "")

    text = " ".join(_snip(e) for e in entries if _snip(e))
    text = re.sub(r"\[[^\]]*\]", " ", text)  # [संगीत] / [Music] / [applause]
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


_WHISPER_MODEL_CACHE = {}


def _get_whisper_model(name):
    """Load-once cache — backfill transcribes many videos in one process."""
    if name not in _WHISPER_MODEL_CACHE:
        import whisper
        _WHISPER_MODEL_CACHE[name] = whisper.load_model(name)
    return _WHISPER_MODEL_CACHE[name]


def transcribe_youtube_video(video_id, whisper_model="small", max_seconds=None):
    """Download a public video's audio (yt-dlp) and Whisper-transcribe it to
    Devanagari Hindi. Fallback for videos without a Hindi caption track.
    Slow (local CPU) — never in the daily cron; the Sunday recap caps
    duration via max_seconds. Returns transcript text or None."""
    import subprocess, tempfile, shutil
    url = f"https://www.youtube.com/watch?v={video_id}"
    tmp = tempfile.mkdtemp(prefix="ytaudio_")
    try:
        audio_path = os.path.join(tmp, f"{video_id}.m4a")
        cmd = ["yt-dlp", "-f", "bestaudio[ext=m4a]/bestaudio", "-o", audio_path,
               "--no-playlist", "-q", url]
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        if r.returncode != 0 or not os.path.exists(audio_path):
            err = r.stderr.decode(errors="replace")[:160] if r.stderr else "no output file"
            print(f"   ⚠️ yt-dlp failed for {video_id}: {err}")
            return None
        if max_seconds:
            trimmed = os.path.join(tmp, f"{video_id}_t.m4a")
            subprocess.run(["ffmpeg", "-i", audio_path, "-t", str(max_seconds),
                            "-c", "copy", "-y", trimmed], capture_output=True, timeout=120)
            if os.path.exists(trimmed):
                audio_path = trimmed
        model = _get_whisper_model(whisper_model)
        result = model.transcribe(audio_path, language="hi")
        text = (result.get("text") or "").strip()
        return text or None
    except Exception as e:
        print(f"   ⚠️ transcribe_youtube_video({video_id}) failed: {e}")
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def get_video_speech_text(video_id, allow_whisper=True, whisper_model="small",
                          max_seconds=None):
    """His REAL spoken words, cheapest source first: unauthenticated captions
    → OAuth captions API → yt-dlp+Whisper. Returns (text, source_label) or
    (None, None). The description fallback stays with the callers — it is
    NOT speech and must be labelled as such in the corpus."""
    text = fetch_transcript_unauthenticated(video_id)
    if text and len(text) > 200:
        return text, "captions"
    text = fetch_youtube_captions(video_id)
    if text and len(text) > 200:
        return text, "captions-oauth"
    if allow_whisper:
        text = transcribe_youtube_video(video_id, whisper_model=whisper_model,
                                        max_seconds=max_seconds)
        if text:
            return text, "whisper"
    return None, None


# Never auto-map these romanizations — English homographs / function words
# that would wreck the Hinglish code-switching if forced to Devanagari.
# ("do"/"teen"/"hi" are deliberate hand-curated homograph calls in
# _TTS_HINGLISH_DEVANAGARI — the learner must never own them.)
_PRON_DENYLIST = {
    "do", "to", "hi", "the", "is", "us", "in", "so", "no", "me", "he", "we",
    "an", "or", "of", "on", "at", "be", "by", "it", "as", "up", "teen",
    "are", "use", "man", "men", "was", "has", "had", "for", "and", "not",
    "but", "all", "can", "may", "say", "see", "one", "two", "ten", "our",
    "out", "now", "new", "any", "who", "how", "why", "his", "her", "him",
    "got", "get", "let", "yes", "did", "does", "you", "your", "with", "this",
    "that", "have", "from", "they", "will", "what", "when", "make", "like",
    "time", "just", "know", "take", "come", "some", "them", "then", "than",
    "look", "only", "over", "also", "back", "work", "well", "even", "want",
    "give", "most", "more", "less", "sale", "name", "same", "game", "day",
    "aura", "were", "been", "each", "much", "many", "very", "here", "there",
    # Script-plausible English that Whisper/captions write in Devanagari
    # (मार्केट, स्टार्ट...) — their romanizations round-trip to the English
    # word itself and must never be force-converted (2026-07-10 review)
    "market", "marketing", "plan", "start", "stop", "drop", "sport", "sports",
    "transport", "regular", "maroon", "karate", "sari", "bare", "wale",
    # Colour + logistics loanwords he says in Devanagari (ग्रीन, शिप, पेंट)
    # whose romanization round-trips to the English word itself (2026-07-11)
    "green", "grin", "paint", "ship", "shipping", "blue", "black", "pink",
    "grey", "gray", "yellow", "orange", "purple", "brown", "navy", "cream",
    "beige", "teal", "silver", "olive", "wine", "royal",
    "age", "bar", "bat", "aid", "ate", "die", "lie", "too", "tin", "ham",
    "vet", "usa", "gee", "dis", "chai", "team", "time", "line", "fit",
    "best", "rate", "rest", "test", "type", "call", "care", "case", "cash",
    "city", "free", "full", "gain", "girl", "gold", "good", "hand", "head",
    "help", "high", "home", "idea", "item", "keep", "kind", "last", "late",
    "life", "list", "live", "long", "made", "mail", "main", "mark", "mind",
    "need", "next", "nice", "note", "open", "pack", "page", "paid", "part",
    "past", "phone", "piece", "place", "point", "price", "pure", "real",
    "ready", "right", "road", "room", "rule", "safe", "save", "seat", "sell",
    "send", "shop", "show", "side", "sign", "site", "slow", "sold", "sort",
    "stock", "store", "sure", "tape", "term", "true", "turn", "unit", "used",
    "user", "view", "wait", "wash", "wear", "week", "wide", "wish", "word",
    "year", "your", "zero", "brand", "check", "china", "clear", "cloth",
    "count", "cover", "daily", "early", "extra", "final", "fresh", "front",
    "great", "group", "heavy", "hello", "india", "large", "level", "light",
    "local", "money", "month", "never", "offer", "paper", "party", "photo",
    "plain", "plant", "reply", "sales", "share", "sheet", "short", "small",
    "sound", "speed", "staff", "still", "style", "table", "today", "total",
    "touch", "track", "trade", "train", "under", "video", "visit", "water",
    "wheel", "white", "whole", "world", "worth", "wrong",
    # Business vocab that must stay English in our scripts
    "order", "print", "quality", "sample", "size", "cotton", "gsm", "sada",
}


def _load_expected_english():
    """EXPECTED_ENGLISH from .github/qa_loop.py — words the voice-QA harness
    requires to stay Latin in TTS output. The learner must never map them.
    Best-effort: returns an empty set if the file moves."""
    try:
        qa_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               ".github", "qa_loop.py")
        src = open(qa_path).read()
        s = src.index("EXPECTED_ENGLISH = {")
        e = src.index("}", s) + 1
        ns = {}
        exec(src[s:e], ns)
        return set(ns["EXPECTED_ENGLISH"])
    except Exception:
        return set()


# Devanagari → colloquial Hinglish romanization tables. Deliberately NOT a
# transliteration library: ITRANS/HK render बेचेंगे as "becheMge" which never
# matches how Claude actually spells it in scripts ("bechenge"). These tables
# generate the informal spellings people (and our script prompts) really use.
_DEVA_CONSONANT_ROMAN = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h",
}
_DEVA_NUKTA_OPTIONS = {  # consonant + ़ → common informal spellings
    "क": ("q", "k"), "ख": ("kh",), "ग": ("g",), "ज": ("z", "j"),
    "ड": ("d", "r"), "ढ": ("dh", "rh"), "फ": ("f", "ph"), "य": ("y",),
}
_DEVA_MATRA_OPTIONS = {
    "ा": ("aa", "a"),   # ा
    "ि": ("i",),        # ि
    "ी": ("ee", "i"),   # ी
    "ु": ("u",),        # ु
    "ू": ("oo", "u"),   # ू
    "ृ": ("ri",),       # ृ
    "ॅ": ("e",),        # ॅ
    "े": ("e",),        # े
    "ै": ("ai",),       # ै
    "ॉ": ("o",),        # ॉ
    "ो": ("o",),        # ो
    "ौ": ("au",),       # ौ
}
_DEVA_VOWEL_OPTIONS = {
    "अ": ("a",), "आ": ("aa", "a"), "इ": ("i",), "ई": ("ee", "i"),
    "उ": ("u",), "ऊ": ("oo", "u"), "ऋ": ("ri",), "ए": ("e",),
    "ऐ": ("ai",), "ओ": ("o",), "औ": ("au",), "ऑ": ("o",), "ऍ": ("e",),
}


def _devanagari_to_roman_variants(word, max_variants=12):
    """Colloquial Hinglish spellings of a Devanagari word, most common first:
    बेचेंगे → ['bechenge', 'bechemge'], काम → ['kaam', 'kam', ...].
    Returns [] for tokens containing digits/danda/rare signs. Variants that
    never appear in a script are harmless — they just never match."""
    import unicodedata, itertools
    word = unicodedata.normalize("NFD", word)  # split precomposed nukta (क़ → क+़)
    NUKTA, HALANT = "़", "्"
    ANUSVARA, CHANDRABINDU, VISARGA = "ं", "ँ", "ः"
    chars = [c for c in word if c not in ("‌", "‍")]
    segs, kinds = [], []   # parallel: roman alternatives + segment kind
    pending_a = False      # inherent vowel of the previous consonant

    def flush():
        nonlocal pending_a
        if pending_a:
            segs.append(("a",))
            kinds.append("A")  # inherent schwa — deletion candidate
        pending_a = False

    for idx, ch in enumerate(chars):
        if ch in _DEVA_CONSONANT_ROMAN:
            flush()
            if idx + 1 < len(chars) and chars[idx + 1] == NUKTA:
                segs.append(_DEVA_NUKTA_OPTIONS.get(ch, (_DEVA_CONSONANT_ROMAN[ch],)))
            else:
                segs.append((_DEVA_CONSONANT_ROMAN[ch],))
            kinds.append("C")
            pending_a = True
        elif ch == NUKTA:
            continue  # consumed with its consonant
        elif ch in _DEVA_MATRA_OPTIONS:
            pending_a = False
            segs.append(_DEVA_MATRA_OPTIONS[ch])
            kinds.append("V")
        elif ch == HALANT:
            pending_a = False  # conjunct — no inherent vowel
        elif ch in (ANUSVARA, CHANDRABINDU):
            flush()
            segs.append(("n", "m") if ch == ANUSVARA else ("n",))
            kinds.append("N")
        elif ch == VISARGA:
            flush()
            segs.append(("h",))
            kinds.append("C")
        elif ch in _DEVA_VOWEL_OPTIONS:
            flush()
            segs.append(_DEVA_VOWEL_OPTIONS[ch])
            kinds.append("V")
        else:
            return []  # danda, digits, om, rare signs — not a speech token
    if pending_a:
        segs.append(("", "a"))  # trailing schwa usually drops (काम → kaam first)
        kinds.append("V")
    # Colloquial medial schwa deletion (करना → "karna" not "karana"): the
    # last inherent 'a' before the final syllable usually drops in writing.
    # Deletion-first ordering so the vi-major fill in build_voice_models
    # picks the common spelling as each word's primary variant.
    for i in range(len(segs) - 2, 0, -1):
        if kinds[i] == "A":
            segs[i] = ("", "a")
            break
    # Word-final ा is usually written single-'a' (mera, karna — not meraa)
    if segs and segs[-1] == ("aa", "a"):
        segs[-1] = ("a", "aa")
    variants = []
    for combo in itertools.islice(itertools.product(*segs), 64):
        v = "".join(combo)
        # 4-char floor: every 1-3 letter romanization (do, kya, hai, age,
        # too, bar...) is either hand-curated already or an English homograph
        if re.fullmatch(r"[a-z]{4,20}", v) and v not in variants:
            variants.append(v)
        if len(variants) >= max_variants:
            break
    return variants


def build_voice_models():
    """Distill voice_corpus/*.txt into the two committed artifacts:
    voice_vocab.json (his words, frequency-ranked) + learned_pronunciations.json
    (auto roman→Devanagari; hand-curated _TTS_HINGLISH_DEVANAGARI always wins).
    Speech transcripts (source: captions/whisper) are preferred — description
    files only count while almost no speech exists yet. Idempotent; re-run
    every Sunday and after backfills."""
    import collections
    if not os.path.isdir(VOICE_CORPUS_DIR):
        return
    speech_texts, other_texts = [], []
    for fn in sorted(os.listdir(VOICE_CORPUS_DIR)):
        if not fn.endswith(".txt"):
            continue
        try:
            with open(os.path.join(VOICE_CORPUS_DIR, fn)) as f:
                raw = f.read()
        except Exception:
            continue
        body = "\n".join(l for l in raw.splitlines() if not l.startswith("#"))
        header = raw[:400]
        if "# source: captions" in header or "# source: whisper" in header:
            speech_texts.append(body)
        else:
            other_texts.append(body)  # legacy description-era files
    corpus = "\n".join(speech_texts if len(speech_texts) >= 3
                       else speech_texts + other_texts)
    words = [w.strip("।॥॰ॐऽ०१२३४५६७८९")
             for w in re.findall(r"[ऀ-ॿ]{2,}", corpus)]
    freq = collections.Counter(w for w in words if len(w) >= 2)
    top = [w for w, c in freq.most_common(400) if c >= 2]
    with open(VOICE_VOCAB_FILE, "w") as f:
        json.dump({"top_words": top, "counts": {w: freq[w] for w in top}},
                  f, ensure_ascii=False, indent=2)

    deny = set(_PRON_DENYLIST) | _load_expected_english()
    hand = {k.lower() for k in _TTS_HINGLISH_DEVANAGARI}
    # WORD-level guard, not just key-level: a Devanagari word that is already
    # a hand-map VALUE has its pronunciation curated — its OTHER romanization
    # variants (आगे→'age', करते→'karate') can only ever match English words
    # in real scripts, so the learner must not touch the word at all.
    hand_values = set(_TTS_HINGLISH_DEVANAGARI.values())
    learnable = [w for w in top if w not in hand_values]
    learned = {}
    # Variant-index-major fill: every word gets its PRIMARY colloquial
    # spelling before any word gets its 2nd/3rd variant, so the 800 cap
    # can't starve low-frequency words of their most common spelling.
    all_variants = {w: _devanagari_to_roman_variants(w) for w in learnable}
    for vi in range(12):
        for w in learnable:
            variants = all_variants[w]
            if vi >= len(variants):
                continue
            roman = variants[vi]
            if roman in deny or roman in hand or roman in learned:
                continue
            learned[roman] = w
            if len(learned) >= 800:
                break
        if len(learned) >= 800:
            break
    with open(LEARNED_PRON_FILE, "w") as f:
        json.dump(learned, f, ensure_ascii=False, indent=2)
    global _LEARNED_PRON_CACHE
    _LEARNED_PRON_CACHE = None  # force reload on next normalize_for_tts
    print(f"   🧠 voice models: {len(top)} vocab words, {len(learned)} learned pronunciations"
          f" (from {len(speech_texts)} speech + {len(other_texts)} description files)")


_LEARNED_PRON_CACHE = None


def _get_learned_pronunciations():
    """learned_pronunciations.json, loaded once per process. Both KEYS and
    VALUES are validated so a hand-edited/merge-mangled file can never crash
    the daily render (re.sub replacement templates) or leak junk into the
    spoken text — anything suspicious is silently dropped."""
    global _LEARNED_PRON_CACHE
    if _LEARNED_PRON_CACHE is None:
        loaded = {}
        try:
            if os.path.exists(LEARNED_PRON_FILE):
                with open(LEARNED_PRON_FILE) as f:
                    for k, v in json.load(f).items():
                        if (isinstance(k, str) and isinstance(v, str)
                                and re.fullmatch(r"[a-z]{4,20}", k.lower())
                                and re.fullmatch(r"[ऀ-ॿ‌‍ ]{1,40}", v)):
                            loaded[k.lower()] = v
        except Exception:
            loaded = {}
        _LEARNED_PRON_CACHE = loaded
    return _LEARNED_PRON_CACHE


def extract_voice_corpus_style_hints(max_entries=12):
    """Phase 4: distill voice_corpus/*.txt into prompt guidance — 5 example
    ending sentences from the TAIL of his transcripts (his real sign-offs)
    plus his high-frequency vocabulary from voice_vocab.json.

    Returns a string of style guidance, or '' if corpus is too sparse (<4 files).
    """
    if not os.path.isdir(VOICE_CORPUS_DIR):
        return ""
    import re as _re
    all_files = [f for f in os.listdir(VOICE_CORPUS_DIR) if f.endswith(".txt")]
    if len(all_files) < 4:
        # Not enough corpus yet — Phase 4 activates after 4 weeks of data
        return ""
    # Order: REAL-speech files first (dated Sundays newest-first, then the
    # back-catalog backfills), description-era files only as last resort.
    # A plain reverse sort would rank every backfill-* above every dated
    # file forever ('b' > '2') and starve out new Sunday transcripts.
    speech_dated, speech_backfill, described = [], [], []
    headers = {}
    for fname in all_files:
        try:
            with open(os.path.join(VOICE_CORPUS_DIR, fname)) as f:
                headers[fname] = f.read(400)
        except Exception:
            continue
        is_speech = ("# source: captions" in headers[fname]
                     or "# source: whisper" in headers[fname])
        if not is_speech:
            described.append(fname)
        elif _re.match(r"\d{4}-\d{2}-\d{2}\.txt$", fname):
            speech_dated.append(fname)
        else:
            speech_backfill.append(fname)
    ordered = (sorted(speech_dated, reverse=True) + sorted(speech_backfill)
               + sorted(described, reverse=True))
    # His long-form sign-offs often pitch the website/channel — never feed
    # CTA-flavoured lines back as ending examples (spoken CTA is banned).
    _CTA = _re.compile(r"(?i)subscribe|website|channel|\.com|link|"
                       r"सब्सक्राइब|चैनल|वेबसाइट|लिंक|लाइक|कमेंट|वीडियो")
    endings, n_read = [], 0
    for fname in ordered[:max_entries]:
        try:
            with open(os.path.join(VOICE_CORPUS_DIR, fname)) as f:
                raw = f.read()
        except Exception:
            continue
        body = "\n".join(l for l in raw.splitlines() if not l.startswith("#")).strip()
        if not body:
            continue
        n_read += 1
        # Real sign-offs live at the END of a transcript, so look at the tail
        # (the old code read the first 3000 chars — mid-video for a long form)
        sentences = [x.strip() for x in _re.split(r'[।.!?]\s+', body[-1200:])
                     if len(x.strip()) >= 8]
        if len(sentences) < 3:
            continue  # no reliable sentence structure — skip, no fragments
        for cand in (sentences[-2], sentences[-3]):
            if not _CTA.search(cand):
                endings.append(cand[:120])
                break
    if not n_read:
        return ""
    endings_text = "\n".join(f"   - {e}" for e in endings[:5])
    vocab_line = ""
    try:
        if os.path.exists(VOICE_VOCAB_FILE):
            with open(VOICE_VOCAB_FILE) as f:
                top = json.load(f).get("top_words", [])[:120]
            # Present his words in ROMAN Hinglish — showing Devanagari here
            # made Claude write Devanagari into script_voice, and the karaoke
            # captions (Latin-only CI fonts) lost those words → desync.
            # Canonical spelling: the hand-map KEY when the word is hand-
            # curated (aap/aur/nahi), else the primary generated variant —
            # the generator's spellings (aapa/nheen) are for matching, not
            # for showing Claude.
            hand_rev = {}
            for k, v in _TTS_HINGLISH_DEVANAGARI.items():
                import re as _re_v
                if _re_v.fullmatch(r"[a-z]+", str(k)) and v not in hand_rev:
                    hand_rev[v] = k
            # Hand-curated words only: the rest of his top words are mostly
            # English loanwords in Devanagari (कलर/वेबसाइट) that Claude
            # already writes in English — generated romanizations of those
            # (kalr, vebsaait) would just teach Claude bad spellings.
            roman = []
            for w in top:
                r = hand_rev.get(w)
                if r:
                    roman.append(r)
                if len(roman) >= 60:
                    break
            if roman:
                vocab_line = ("\nHIS HIGH-FREQUENCY WORDS (prefer these — this is "
                              "how he actually talks). Write them in Roman Hinglish "
                              "as usual, NEVER in Devanagari script: "
                              + ", ".join(roman) + "\n")
    except Exception:
        pass
    return (
        f"\n\nUSER VOICE STYLE (from {n_read} recent main-channel video transcripts):\n"
        f"Example ending sentences the user actually uses — match this energy + register:\n"
        f"{endings_text}\n{vocab_line}"
    )


def get_source_channel_top_topics(n=10):
    """Return top N video titles from source channel, ranked by views."""
    videos = fetch_source_channel_insights()
    if not videos:
        return []
    return [v["title"] for v in videos[:n]]


def get_source_channel_category_ranking():
    """Analyze source channel videos and rank categories by avg views.
    Same logic as get_top_performing_categories() but using source channel data."""
    videos = fetch_source_channel_insights()
    if not videos:
        print("   📊 Source category ranking: no data available")
        return []

    cat_stats = {}
    for v in videos:
        title_lower = v["title"].lower()
        views = v["views"]
        for cat_name, cat_data in TOPIC_SERIES_TAGS.items():
            if any(kw in title_lower for kw in cat_data["keywords"]):
                cat_stats.setdefault(cat_name, []).append(views)
                break

    if not cat_stats:
        return []

    ranked = sorted(
        cat_stats.items(),
        key=lambda x: sum(x[1]) / len(x[1]),
        reverse=True,
    )
    return [cat for cat, _ in ranked]


def fetch_source_channel_comments(max_videos=5, max_comments_per_video=20):
    """Fetch top comments from source channel's best videos (read-only).
    Uses commentThreads.list API. Returns list of comment strings.
    Quota cost: ~1 unit per call, max 5 calls = ~5 units."""

    videos = fetch_source_channel_insights()
    if not videos or not SOURCE_CHANNEL_API_KEY:
        print("   💬 Comment mining: skipped (no source channel data)")
        return []

    # Check cache (stored inside source_channel_insights.json)
    if os.path.exists(SOURCE_CHANNEL_CACHE_FILE):
        try:
            with open(SOURCE_CHANNEL_CACHE_FILE, "r") as f:
                cache = json.load(f)
            cached_comments = cache.get("top_comments", [])
            if cached_comments:
                print(f"   💬 Comment mining: {len(cached_comments)} comments from cache")
                return cached_comments
        except Exception as e:
            print(f"   💬 Comment cache read failed: {e}")

    print("   💬 Fetching comments from source channel top videos...")
    base_url = "https://www.googleapis.com/youtube/v3"
    all_comments = []
    fetch_errors = 0

    # Pick top videos by views (most engaged = best comments)
    top_videos = [v for v in videos if v.get("video_id") and v.get("comments", 0) > 0][:max_videos]

    for video in top_videos:
        try:
            resp = requests.get(f"{base_url}/commentThreads", params={
                "key": SOURCE_CHANNEL_API_KEY,
                "videoId": video["video_id"],
                "part": "snippet",
                "order": "relevance",
                "maxResults": max_comments_per_video,
                "textFormat": "plainText",
            }, timeout=10)
            if resp.status_code != 200:
                fetch_errors += 1
                continue
            data = resp.json()

            for item in data.get("items", []):
                comment_text = item["snippet"]["topLevelComment"]["snippet"]["textDisplay"]
                like_count = item["snippet"]["topLevelComment"]["snippet"].get("likeCount", 0)
                # Only keep meaningful comments (>10 chars, not just emojis)
                if len(comment_text) > 10:
                    all_comments.append({
                        "text": comment_text[:200],  # Truncate long comments
                        "likes": like_count,
                        "video_title": video["title"][:60],
                    })
        except Exception as e:
            fetch_errors += 1
            continue

    if fetch_errors:
        print(f"   💬 Comment fetch: {fetch_errors}/{len(top_videos)} videos had errors")

    # Sort by likes — most liked comments = what audience cares about
    all_comments.sort(key=lambda c: c["likes"], reverse=True)
    top_comments = all_comments[:30]  # Keep top 30

    # Save to cache
    if top_comments:
        try:
            with open(SOURCE_CHANNEL_CACHE_FILE, "r") as f:
                cache = json.load(f)
            cache["top_comments"] = top_comments
            with open(SOURCE_CHANNEL_CACHE_FILE, "w") as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"   ⚠️ Comment cache save failed: {e}")

    print(f"   💬 Comment mining: {len(top_comments)} comments collected ({len(top_videos)} videos scanned)")
    return top_comments


def get_audience_questions(n=10):
    """Extract audience questions/requests from source channel comments.
    Returns formatted string for use in prompts."""
    comments = fetch_source_channel_comments()
    if not comments:
        return "No comment data available."

    # Filter for questions (comments with ?, kaise, kya, kyu, etc.)
    question_words = ["?", "kaise", "kya", "kyu", "kyun", "konsa", "kaunsa", "kitna",
                      "how", "what", "why", "which", "best", "suggest", "recommend",
                      "bata", "batao", "samjhao", "explain"]
    questions = [c for c in comments if any(w in c["text"].lower() for w in question_words)]

    # If not enough questions, include most-liked comments
    if len(questions) < 3:
        questions = comments[:n]
    else:
        questions = questions[:n]

    lines = []
    for q in questions:
        lines.append(f"  - \"{q['text'][:100]}\" ({q['likes']} likes, on: {q['video_title']})")
    return "\n".join(lines) if lines else "No relevant comments found."


def get_source_channel_posting_patterns():
    """Analyze source channel videos to find best posting hour (IST).
    Returns dict: {hour: avg_views} for hours that have data."""
    videos = fetch_source_channel_insights()
    if not videos:
        print("   ⏰ Source posting patterns: no data available")
        return {}

    ist = pytz.timezone(TIMEZONE)
    hour_stats = {}  # {hour: [views, views, ...]}

    for v in videos:
        published = v.get("published", "")
        views = v.get("views", 0)
        if not published:
            continue
        try:
            # Parse ISO datetime and convert to IST
            pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            pub_ist = pub_dt.astimezone(ist)
            hour = pub_ist.hour
            hour_stats.setdefault(hour, []).append(views)
        except Exception:
            continue

    if not hour_stats:
        return {}

    # Average views per hour
    return {h: int(sum(views) / len(views)) for h, views in hour_stats.items()}


def get_source_optimized_slot():
    """Return best publish slot based on source channel posting patterns.
    Returns (hour, minute, label) or None."""
    hour_views = get_source_channel_posting_patterns()
    if not hour_views or len(hour_views) < 2:
        return None

    best_hour = max(hour_views, key=hour_views.get)
    best_views = hour_views[best_hour]

    # Only use if significantly better than average (>30%)
    avg_views = sum(hour_views.values()) / len(hour_views)
    if best_views <= avg_views * 1.3:
        return None

    # Find closest PUBLISH_SLOT or use exact hour
    closest_slot = None
    min_diff = 24
    for hour, minute, label in PUBLISH_SLOTS:
        diff = abs(hour - best_hour)
        if diff < min_diff:
            min_diff = diff
            closest_slot = (hour, minute, label)

    # If best hour is far from any slot (>2h), create new slot
    if min_diff > 2:
        label = f"{best_hour}:00 {'AM' if best_hour < 12 else 'PM'}"
        if best_hour > 12:
            label = f"{best_hour - 12}:00 PM"
        elif best_hour == 0:
            label = "12:00 AM"
        return (best_hour, 0, f"{label} (source-optimized)")

    return closest_slot


# ═══════════════════════════════════════════════════════════════════════
# SCRIPT PROMPT
# ═══════════════════════════════════════════════════════════════════════

def _get_recent_clip_prompts():
    """Return recent Veo prompts as a string for deduplication in the script prompt."""
    try:
        if not os.path.exists(CLIP_HISTORY_FILE):
            return "No history yet."
        with open(CLIP_HISTORY_FILE, "r") as f:
            clip_history = json.load(f)
        # Last 5 videos' prompts (compact summary)
        recent = clip_history[-5:]
        lines = []
        for entry in recent:
            for p in entry.get("prompts", [])[:2]:  # First 2 prompts per video
                lines.append(f"  - {p[:80]}...")
        return "\n".join(lines) if lines else "No history yet."
    except Exception:
        return "No history yet."



def _own_channel_performance_signal():
    """Build a feedback-loop signal showing what's worked / failed on OUR
    own bot-uploaded videos so far. Pulls top + bottom from both YouTube
    (engagement_history.json) and Instagram (ig_engagement_history.json).
    Returns a string ready to drop into the script-gen prompt."""
    parts = []
    # YouTube own-channel
    try:
        if os.path.exists(ENGAGEMENT_FILE):
            data = json.load(open(ENGAGEMENT_FILE))
            latest = {}
            for x in data:
                vid = x.get("video_id")
                if not vid: continue
                if vid not in latest or x.get("checked_at", "") > latest[vid].get("checked_at", ""):
                    latest[vid] = x
            vids = [v for v in latest.values() if v.get("views") is not None]
            if len(vids) >= 5:
                top = sorted(vids, key=lambda v: -(v.get("views") or 0))[:5]
                bottom = sorted(vids, key=lambda v: (v.get("views") or 0))[:5]
                parts.append("YOUTUBE OWN-CHANNEL DATA — what works for OUR bot's uploads:")
                parts.append("Top 5 (best performers, mimic these patterns):")
                for v in top:
                    parts.append(f"  ✅ {v.get('views', 0)}v {v.get('likes', 0)}❤  | {v.get('title', '')[:80]}")
                parts.append("Bottom 5 (FLOPPED — AVOID these patterns):")
                for v in bottom:
                    parts.append(f"  ❌ {v.get('views', 0)}v {v.get('likes', 0)}❤  | {v.get('title', '')[:80]}")
    except Exception:
        pass
    # Instagram own-channel
    try:
        if os.path.exists(IG_ENGAGEMENT_FILE):
            data = json.load(open(IG_ENGAGEMENT_FILE))
            checked = [r for r in data if r.get("checked")]
            if len(checked) >= 3:
                top = sorted(checked, key=lambda v: -(v.get("views") or v.get("likes", 0)))[:5]
                parts.append("\nINSTAGRAM OWN-CHANNEL DATA — what works on Reels:")
                for v in top:
                    parts.append(f"  📸 {v.get('views', 0)}v {v.get('likes', 0)}❤ {v.get('shares', 0)}↗  | {v.get('title', '')[:80]}")
    except Exception:
        pass
    if not parts:
        return "(no own-channel performance data yet — first ~10 videos)"
    return "\n".join(parts)


def get_script_prompt(topic):
    return f"""
You are writing a YouTube Short voiceover script. The video is from Sale91.com
(a B2B plain t-shirt manufacturer) but the script must NOT sell anything.
You also need to describe 5 AI video clips that will play during the Short.

BUSINESS CONTEXT (use this knowledge, but do NOT promote the brand in voice):
{BUSINESS_CONTEXT}

TOPIC: {topic}

━━━ AUDIENCE INTELLIGENCE (from our main channel with 50K subs) ━━━
These are PROVEN top-performing video titles from our existing audience — use this to understand
what TONE, ANGLE, and DEPTH works. Your script should match this audience's expectations:
{json.dumps(get_source_channel_top_topics(5), ensure_ascii=False) if get_source_channel_top_topics(5) else "No source data yet — write based on general B2B textile audience."}

━━━ OWN-CHANNEL PERFORMANCE FEEDBACK ━━━
This is what's actually worked (and failed) on the bot's recent uploads.
Mimic the patterns of TOP performers; AVOID the patterns of FLOPS.
{_own_channel_performance_signal()}
{extract_voice_corpus_style_hints()}

━━━ CRITICAL: SPEAKING STYLE ━━━

You are writing EXACTLY like a real Indian textile manufacturer talks — but using
MICRO-STORYTELLING to hook the viewer in the first 2 seconds.

TARGET LENGTH: 6-8 sentences. The Short should be 30-35 seconds long when spoken naturally.
Tight and dense — every sentence earns its place. Still a MINI STORY with a
beginning, middle, and end, just leaner.

STRUCTURE (follow this EVERY time):
1. HOOK (first sentence — HARD 2-SECOND MANDATE) — The FIRST SENTENCE must be a
   PATTERN-INTERRUPT built on LOSS-AVERSION or a SHOCKING NUMBER. Max 10 words.
   It must contain a ₹ amount, a piece count, or a concrete loss.
   NEVER a greeting, NEVER context-setting, NEVER a definition, NEVER "aaj main
   batata hoon". Drop the viewer MID-STORY at the moment of damage:
   - "Ye galti ₹40,000 ki padi."
   - "500 piece ka order, 2 wash mein barbaad."
   - "Ek customer ne ₹50,000 ka order cancel kar diya."
   - "200 GSM bola tha, 160 nikla."
   Sentence 2 then opens the story ("Hua ye tha ki...").

2. PROBLEM BUILD-UP (2-3 sentences) — Build the tension, explain what went wrong:
   - "Problem ye thi ki usne check hi nahi kiya..."
   - "Fark ye hai ki ek mein combed tha, ek mein nahi..."
   - "Maine pucha — sample liya tha? Bola nahi, seedha 500 piece order kar diya..."
   - Add details that make the story feel REAL — quantities, reactions, what happened next

3. KNOWLEDGE DROP (3-4 sentences) — The actual gyaan, with practical examples:
   - Explain the concept with REAL comparisons
   - Give a practical test or check the viewer can do themselves
   - Include specific numbers, methods, or techniques
   - "Dekho... agar tum 180 GSM loge toh summer ke liye theek hai, par printing ke liye 200 minimum rakho"

4. FINAL CLOSER — end on a SHORT, FIRM line, NOT a trail-off. The wrap-up
   sentence may flow, but the LAST line must be 3-6 words, land at full
   energy, and STOP on a hard period. No leading "...", no fade. Rotate
   closers (see the detailed "NATURAL ENDING" section below). The firm
   final line looks like:
   - "Theek hai."
   - "Bas itna hi."
   - "Dhyaan rakhna."
   - "Sample kar lo."
   - "Yehi tha bhai."

Study these REAL examples from the actual business owner — match this tone PERFECTLY:

EXAMPLE 1 (GSM explanation — LONGER STYLE):
"Dekho... ek customer ne mujhe call kiya, bola tshirt bahut patli lag rahi hai,
quality kharab hai. Maine pucha GSM kya order kiya tha? Bola pata nahi, sasti
wali mangai thi. Yehi problem hai. GSM bas fabric ka weight hota hai — jyada
GSM matlab mota fabric, kam GSM matlab patla. Basically kisi bhi kapde ko 1
square meter mein cut karke weight kar doge toh jo bhi gram mein aayega, wahi
GSM hai. Toh agar printing ke liye le rahe ho, 200 GSM minimum rakho. 180 pe
print theek lagta hai par fabric through dikha sakta hai. Aur 220 premium feel
deta hai par cost badh jayegi. Toh bas... pehle decide karo end use kya hai,
phir GSM choose karo... simple hai."

EXAMPLE 2 (Storytelling style — THIS is the target):
"Ek customer aaya tha, bola collar loose ho gaya 5 wash mein. Maine bola collar
ribbing ka type check kiya tha? Nahi kiya tha. Dekho... collar mein 2 type ki
ribbing hoti hai — 1x1 rib aur flat knit. 1x1 rib mein elasticity hoti hai,
toh wo recover karta hai har wash ke baad. Flat knit mein ye nahi hota, toh
stretch hoke waisi reh jaati hai. Ab agar tum premium blank le rahe ho, toh 1x1
rib collar wala lo. Aur ek baat... ribbing ka GSM bhi matter karta hai —
agar collar ka rib patla hai toh jaldi shape kho dega. Bas itna check kar lo,
collar ki complaint kabhi nahi aayegi... simple hai."

━━━ RULES EXTRACTED FROM THESE EXAMPLES ━━━

1. 6-8 SENTENCES for a 30-35 second Short. Hook hard, build fast, drop knowledge, loop back to the hook at the end.
2. FIRST SENTENCE = LOSS/NUMBER PATTERN-INTERRUPT — max 10 words, must carry a
   ₹ amount, piece count, or concrete loss. NEVER a greeting, definition, or
   context-setting. The story unfolds from sentence 2.
3. THEORY AVOID — no enzyme processes, no chemistry, no Wikipedia.
   Give PRACTICAL action: "cut kar lo", "weight kar lo", "try kar lo"
4. HONEST and BLUNT — "kuch bhi nahi kar sakte", "ye common hai"
   Don't sugarcoat. Don't be defensive. Accept reality.
5. COMPARISON STYLE — "jyada GSM matlab mota, kam GSM matlab patla"
6. SIGNATURE ENDINGS (use a DIFFERENT one each video — NEVER repeat the same ending twice in a row):
   "usi ko... bolte hai", "bas...hota hai", "wo jyada theek rahega",
   "simple hai", "itna kar lo bas", "complaint nahi aayegi", "try karke dekh lo"
7. Use "aap/aapka/aapko" — respectful. NEVER "tu/tera/tujhe/bhai/yaar"
8. COMPOUND VERBS — "kar lo", "kar doge", "ho jayega", "dikh jayega",
   "leke try kar lo" — NOT "karo", "kiya", "hoga"
9. NATURAL ENGLISH mix — "basically", "common", "non noticeable",
   "simple", "normal", "quality", "sample", "print", "result"
10. NO selling, NO website name, NO CTA, NO "hamare yahan se lo"
11. INCLUDE SPECIFIC DETAILS — numbers (GSM values, piece counts, prices), names of techniques, comparisons.

━━━ IG REELS RETENTION RULES (this script also runs as an Instagram Reel) ━━━

12. 0-2s OPEN A LOOP — first sentence must POSE a question or set up a contradiction
    that the viewer NEEDS resolved. They scroll if they think they already know the
    answer. Make them feel "ek second ruko, ye toh nahi pata tha."
    Examples:
      - "Ek customer ne ₹50,000 ka order cancel kar diya... pata hai kyu? Ek chhoti si galti."
      - "200 GSM aur 220 GSM dono same lagte hain... but printing pe ek hi survive karta hai."

13. CLOSE THE LOOP AT 70-80% — reveal the answer/lesson roughly 4/5ths into the script,
    NOT at the very end. Open a loop, build tension, deliver payoff with ~6-8s of
    script left for "so what to do" — that tail is where SHARES happen.

14. ONE SHOCKING NUMBER per script — Indian B2B Reels viewers SAVE for numbers they
    can use ("180 GSM", "₹140 cost", "10 piece MOQ", "3 wash mein fade"). Bury one
    surprising number in the middle that makes them want to remember/share.

14b. ₹ AMOUNTS MUST BE NATURAL ROUND NUMBERS THAT INDIANS ACTUALLY SAY.
    Real businessmen in conversation NEVER use awkward decimals like ₹1.2 lakh
    or ₹3.4 lakh — these sound robotic / like a calculator. They use:

    ✅ ALLOWED:
      - Whole lakhs/crores: ₹1 lakh, ₹2 lakh, ₹5 lakh, ₹10 lakh, ₹50 lakh, ₹1 crore
      - Half multiples ONLY: ₹1.5 lakh, ₹2.5 lakh, ₹3.5 lakh, ₹4.5 lakh,
        ₹0.5 lakh, ₹1.5 crore, ₹2.5 crore (these become डेढ़/ढाई/साढ़े/आधा naturally)
      - Round thousands: ₹40,000, ₹50,000, ₹80,000, ₹2,00,000 (= 2 lakh)
      - Specific small prices: ₹49, ₹65, ₹140, ₹185, ₹385 (per-piece rates fine)

    ❌ NEVER USE:
      - ₹1.2 lakh, ₹1.3 lakh, ₹1.7 lakh, ₹2.3 lakh, ₹3.4 lakh — sound artificial
      - ₹1.25 lakh, ₹2.75 crore — too precise for casual speech
      - Anything with .1/.2/.3/.4/.6/.7/.8/.9 decimals on lakh/crore amounts

    If the story needs a precise loss/profit, ROUND to the nearest natural number.
    A ₹1.2 lakh loss → make it "₹1.5 lakh" or "₹1 lakh" in the script.
    A ₹3.4 crore turnover → "₹3.5 crore" or "₹3 crore".

14c. ONE SCREENSHOT MOMENT (mid-video, ~50-65% mark) — write ONE dense 1-2 sentence
    "reference card" the viewer will pause, screenshot, and share: compact rate math
    ("₹140 fabric + ₹18 stitching + ₹22 print = ₹180 landed"), a GSM-to-use-case map
    ("160 summer, 200 printing, 240 premium"), or a 3-point check ("bill, GSM,
    sample — teeno check karo"). Make it self-contained — numbers + labels, no story
    words — so that 2-3s subtitle frame alone is worth saving. Mirror it in
    script_english with the SAME numbers in the SAME order.

15. STRUCTURE FOR REELS GRID DISCOVERY — a viewer scrolling Explore/Reels feed sees
    your video next to 30 others. The first 1.5 seconds must look DIFFERENT from
    a generic talking-head Short. The hook visual + bold caption do this — script's
    job is to EARN the hold past 3s.

16. LOOP-BACK ENDING (CRITICAL FOR REPLAYS) — the FINAL line must semantically
    CONNECT BACK to the opening hook so the video replays seamlessly: reuse the
    hook's key number, word, or image ("...aur wahi ₹40,000 wali galti kabhi nahi
    hogi."). A viewer who loops = double watch time. NO spoken CTA anywhere —
    never say follow/subscribe/website/link/save in the voiceover; the on-screen
    outro card carries that.
    Specificity = credibility. "200 GSM" is better than "thick fabric".

━━━ NATURAL ENDING (CRITICAL — listener must FEEL the wrap-up) ━━━

When a real Indian factory owner finishes a story, the LAST SENTENCE is
SHORT, PUNCHY, and FINAL. Volume reduction alone does NOT make an ending
feel like an ending — the WORDS themselves must signal closure.

REQUIRED STRUCTURE for the last 1-2 sentences:

(a) ALWAYS write a separate VERY SHORT (3-6 word) FINAL sentence after
    the longer wrap-up. This short sentence is the actual "ending feel".

(b) Final sentence patterns that WORK (the ":" is for clarity, don't include it):
    - DECLARATIVE PERIOD: "Bas itna yaad rakho." / "Yehi sab kuch hai."
    - PERSONAL ADDRESS: "Bhai ye galti mat karna." / "Yaar simple hai."
    - PUNCHY CONCLUSION: "Khel khatam." / "Story over."
    - CALL-OUT: "Itna hi." / "Bas."

(c) The final sentence MUST end with a STRONG period (.). NEVER a comma,
    NEVER trailing into silence. The period IS the prosody cue.

(d) Do NOT use formal vocabulary like "दशमलव" (decimal). Always say
    "डेढ़ लाख" not "एक दशमलव पाँच लाख", "ढाई करोड़" not "दो दशमलव पाँच करोड़".
    Or write "1.5 lakh" / "2.5 crore" in Latin and let the model say it
    naturally as "one-point-five lakh".

EXAMPLE STRUCTURE (correct):
    [longer wrap sentence]: "Bas itna check karke order karo, har baar
    quality consistent rahegi."
    [SHORT FINAL SENTENCE]:  "Yehi sab kuch hai."

WRONG (today's video had this — feels mid-thought):
    "Bas itna dhyaan rakh lo, ye galti kabhi nahi hogi."
    (15 words for the ending. Listener doesn't FEEL the close even
     though volume drops — too long, too narrative.)

GOOD endings catalog — these are the ACTUAL ending patterns Ketu uses
in his real YouTube videos (extracted by Whisper-transcribing the last
30 seconds of 8 videos from his @bulkplaintshirt_com channel). Use
these patterns, NOT made-up ones:

PATTERN A — "Theek hai." + signoff (very frequent in real videos):
- "Theek hai."
- "Theek hai bhai."
- "Bas. Theek hai."

PATTERN B — Action command (do this):
- "X kar lo." → "Sample order kar lo." / "Check kar lo bhai."
- "X karo." → "Utilize karo." / "Try kar ke dekh lo."
- "Aap khud try karo."

PATTERN C — Dhyaan rakhna (keep in mind — soft warning):
- "Bas itna dhyaan rakhna."
- "Dhyaan rakhna bhai."
- "Itna khayal kar lo."

PATTERN D — Pre-plan / preparation:
- "Achha pre-plan kar lo."
- "Pehle se taiyari kar lo."
- "Soch ke decision lo."

PATTERN E — Direct conclusion:
- "Bas itna hi."
- "Yehi tha bhai."
- "Sab keh diya."
- "Story khatam."

EXAMPLES from his REAL videos (verbatim from Whisper transcripts):
- "Achha pre-plan karna." (dm-3wqKPkic)
- "Theek hai." (E1B-HKoek5Y, multiple times)
- "Unko utilize karo." (J5g_DfyxxW8)
- "Dhyaan rakhna website mein." (Ic16Ms2vqaY)
- "Apne dhang se ji lo." (j71qiNc-qio)

RULES:
1. Last sentence: 3-7 words MAX.
2. Ends with strong period.
3. Use a DIFFERENT pattern every video (rotate A/B/C/D/E).
4. LOOP-BACK: the wrap-up sentence (the one BEFORE the short closer) must echo
   the hook's key number/word/image, so the ending flows straight back into the
   opening when the video replays.
5. NO spoken CTA — never follow/subscribe/website/link in the voiceover.
6. NEVER use long narrative phrases like "complaint kabhi nahi aayegi"
   or "ye galti kabhi nahi hogi" as the FINAL sentence — those are
   mid-narrative phrases. Use them in the wrap-up sentence BEFORE the
   final short closer if you want.
7. The ending should match Ketu's actual speaking style — direct,
   action-oriented, sometimes with "theek hai" closer.

━━━ NATURAL SPEECH FILLERS (for human feel) ━━━

Add a few NATURAL HINDI FILLERS to make it sound real, not read:

FILLER WORDS (pick 2-3 per script, NOT more):
- "Dekho," (Look/See — opening)
- "Matlab," (Meaning — thinking)
- "Toh basically," (explanation starter)
- "Aur ek baat," (adding a point)
- "Ab dekho," (transitioning)

EXAMPLE with fillers (natural flow):
"Dekho, ek customer ka case batata hoon. 200 piece order kiya, DTG print karwaya,
2 wash mein print fade ho gaya. Matlab, pre-treatment hi nahi kiya tha. Ab DTG
mein ye zaroori hota hai, ink fabric mein absorb hone ke liye pre-treatment lagta hai.
Bina uske ink surface pe rehti hai, wash mein nikal jaati hai. Toh solution simple hai,
pre-treatment spray ya machine use karo, phir print karo. Cost thoda badhega par
return zero ho jayega. Aur ek baat, pre-treatment ka coat uniform hona chahiye,
warna patchy print aayega. Toh bas itna dhyan rakho, complaint nahi aayegi."

CRITICAL RULES for fillers and pauses:
- Use COMMA after fillers, NOT "..." (ellipsis). The TTS engine reads "..." as a very long pause.
- NEVER use "..." anywhere in script_voice. Use comma or dash instead.
- NEVER write elongated sounds like "aaaaaa", "hmmmm", "ummmm", "bekaaaar"
- NEVER write "Hmm..." or "Accha..." — these sound distorted in TTS
- Maximum 2-3 filler words per script, NOT more
- Keep the flow CLEAN and CRISP — fillers are seasoning, not the main dish

━━━ LANGUAGE RULES ━━━

Write in ROMAN HINGLISH — Hindi words in ENGLISH LETTERS (not Devanagari).
- ENGLISH for technical terms: "fabric", "GSM", "weight", "print", "quality",
  "color", "shrinkage", "biowash", "preshrunk", "sample", "cotton"
- HINDI for flow: "agar", "toh", "aur", "mein", "matlab", "wahi",
  "hota hai", "bolte hai", "kar lo", "lelo", "bas"
- Numbers in digits: "200", "160", "10", "2%"

━━━ HOOK TEXT (for on-screen text overlay) ━━━

Write a 3-6 word LOSS/NUMBER hook text that appears on screen for the first
2 seconds. It must be PAIRED with the spoken first sentence — same number,
same loss — so eye and ear hit the same pattern-interrupt together.

Good hook texts (number/loss driven, paired with the spoken hook):
- "₹40,000 KI GALTI"
- "500 PIECE BARBAAD"
- "2 WASH MEIN PRINT KHATAM"
- "200 BOLA, 160 NIKLA"

Bad hook texts (no number, no loss, generic):
- "GSM KA MATLAB KYA HAI"
- "FABRIC QUALITY TIPS"
- "YE GALTI MAT KARNA"

━━━ VIDEO PROMPT RULES ━━━

Write 5 detailed video scene descriptions for AI video generation (Google Veo).
Each clip will be 8 seconds, vertical 9:16 format.

IMPORTANT VIDEO PROMPT GUIDELINES:
- Describe EXACTLY what the camera sees — this is for an AI that generates video
- Include camera angle, lighting, movement, and specific objects
- Focus on t-shirt/textile/manufacturing/printing industry visuals
- CRITICAL: Every clip must START with a visible, well-lit scene from frame 1. NO black intros, NO fade-from-black, NO dark openings. Begin with action immediately.
- CLIP 1 MUST OPEN MID-ACTION — frame 1 is already INSIDE the event: fabric already tearing, print already peeling under a thumb, rejected stack already hitting the table. NO establishing shot, NO hands reaching toward an object, NO scene-setting. The damage/drama is visible in the very first frame.
- Be SPECIFIC: "Close-up of Indian man's hands holding a thick white cotton
  round-neck t-shirt, turning it to show the smooth bio-washed fabric texture,
  warm indoor lighting, slight camera dolly forward" — NOT "a tshirt"
- NO text/words/labels in the video (subtitles are added separately)
- NO people's faces (to avoid AI face artifacts)
- Show HANDS, products, fabrics, machines, packaging — not faces
- Each prompt should be 40-80 words for best results
- Describe REALISTIC scenes that could exist in a real Indian textile business
- Each of the 5 clips must show a DIFFERENT scene — NO repetition between clips

━━━ VISUAL CONTINUITY (CRITICAL FOR PERCEIVED QUALITY) ━━━
The 5 clips will be cut together with NO scene-setting transitions, so they
must feel like ONE cohesive video, not 5 unrelated stock shots.

Pick ONE consistent visual identity at the top of your prompt-set and APPLY IT
TO ALL 5 PROMPTS verbatim:
  • LIGHTING: e.g. "warm tungsten + cool window rim light, late-afternoon mood"
    OR "cool overhead daylight, factory floor". Pick ONE — repeat it in every prompt.
  • COLOR PALETTE: e.g. "cinematic teal + amber" OR "neutral whites + earthy browns".
    Pick ONE — repeat in every prompt.
  • LOCATION FEEL: e.g. "small Tiruppur factory floor with concrete walls, fabric stacks,
    incandescent bulbs" — ALL clips happen in this same world.
  • LENS FEEL: e.g. "shallow DOF, 35mm cinematic, slight handheld" — same lens across all 5.

NEVER mix dark cinematic with bright daylight stock-photo style across clips.
NEVER use US/UK price tags ($), branded labels, or non-Indian context.
The B-roll currency, signage, packaging style must be Indian.

Open each video_prompt with the same one-line "STYLE LOCK" preamble (just copy/
paste it across all 5), then describe that clip's scene in detail.
- AVOID repeating visuals from recent videos. These prompts were used recently (DO NOT reuse similar scenes):
{_get_recent_clip_prompts()}

The 5 clips should follow the story arc:
- Clip 1: HOOK — opens MID-ACTION on the damage/problem already happening (not about to happen)
- Clip 2: CONTEXT — setting the scene, showing the product/situation
- Clip 3: EXPLANATION — the comparison or process being discussed
- Clip 4: DEMONSTRATION — showing the technique, test, or method
- Clip 5: RESOLUTION — the correct result, quality product, or satisfying conclusion

OUTPUT THIS JSON ONLY (no markdown, no code blocks):
{{
    "title": "YouTube title in English, max 70 chars, SEO optimized for printing business",
    "description": "Description in English optimized for BOTH YouTube and Instagram. Include 6-8 hashtags that work on both platforms (Instagram hashtags drive Explore reach — use #tshirtbusiness #wholesale #printingbusiness etc). Include Sale91.com link.",
    "script_voice": "The ROMAN HINGLISH script. 6-8 sentences for 30-35 seconds spoken. First sentence = loss/number pattern-interrupt (max 10 words). Final line loops back to the hook. NO website. NO selling. NO spoken CTA. Pure knowledge with storytelling.",
    "script_english": "ON-SCREEN SUBTITLE TEXT in simple English — paraphrase the Hinglish script so a non-Hindi speaker / deaf viewer can follow easily. SAME NUMBER OF SENTENCES AS script_voice (one English sentence per Hinglish sentence — keeps subtitle timing aligned). Each sentence ≤10 words. Plain language, no jargon (say 'thick fabric' not '240 GSM' if context allows; keep technical terms only when essential like DTF/GSM). Punctuation matches script_voice's sentence breaks. NOT a literal translation — capture the meaning concisely.",
    "hook_text": "3-6 words, UPPERCASE, loss/number driven, paired with the spoken first sentence (same number/loss)",
    "music_mood": "Pick ONE mood for background music that matches this topic's emotion: upbeat | calm | serious | motivational | trendy",
    "video_prompt_1": "HOOK scene — opens MID-ACTION, damage/drama already happening in frame 1. 40-80 words.",
    "video_prompt_2": "CONTEXT scene — setting up the situation. 40-80 words.",
    "video_prompt_3": "EXPLANATION scene — showing the comparison or process. 40-80 words.",
    "video_prompt_4": "DEMONSTRATION scene — the technique or test being shown. 40-80 words.",
    "video_prompt_5": "RESOLUTION scene — the correct result or satisfying conclusion. 40-80 words.",
    "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8"]
}}
"""


# ═══════════════════════════════════════════════════════════════════════
# YOUTUBE HELPERS
# ═══════════════════════════════════════════════════════════════════════

# Analytics tracking file — stores per-slot performance data
ANALYTICS_FILE = f"{WORK_DIR}/slot_analytics.json"


def fetch_recent_video_analytics(youtube):
    """Fetch view counts for recent uploads and map them to publish time slots.
    Returns dict: {slot_label: {"views": total, "count": num_videos, "avg": avg_views}}"""
    try:
        # Get last 21 uploaded videos (3 weeks of daily uploads)
        channels = youtube.channels().list(part="contentDetails", mine=True).execute()
        if not channels.get("items"):
            return None
        uploads_playlist = channels["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

        playlist_items = youtube.playlistItems().list(
            part="snippet", playlistId=uploads_playlist, maxResults=21
        ).execute()

        video_ids = [item["snippet"]["resourceId"]["videoId"]
                     for item in playlist_items.get("items", [])]

        if not video_ids:
            return None

        # Get view counts + publish times
        videos = youtube.videos().list(
            part="statistics,snippet", id=",".join(video_ids)
        ).execute()

        ist = pytz.timezone(TIMEZONE)
        slot_data = {}  # {label: {"views": [], "video_ids": []}}

        for v in videos.get("items", []):
            try:
                views = int(v["statistics"].get("viewCount", 0))
                pub_str = v["snippet"]["publishedAt"]
                # Parse ISO 8601 UTC time
                pub_utc = datetime.strptime(pub_str.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
                pub_utc = pytz.utc.localize(pub_utc)
                pub_ist = pub_utc.astimezone(ist)
                pub_hour = pub_ist.hour

                # Map to nearest slot
                best_slot = None
                best_diff = 999
                for hour, minute, label in PUBLISH_SLOTS:
                    diff = abs(pub_hour - hour)
                    if diff < best_diff:
                        best_diff = diff
                        best_slot = label

                if best_slot:
                    if best_slot not in slot_data:
                        slot_data[best_slot] = {"views": [], "video_ids": []}
                    slot_data[best_slot]["views"].append(views)
                    slot_data[best_slot]["video_ids"].append(v["id"])
            except Exception:
                continue

        # Calculate averages
        result = {}
        for label, data in slot_data.items():
            total = sum(data["views"])
            count = len(data["views"])
            result[label] = {
                "total_views": total,
                "count": count,
                "avg_views": round(total / count) if count > 0 else 0,
            }

        return result

    except Exception as e:
        print(f"   ⚠️ Analytics fetch failed: {e}")
        return None


def get_best_publish_slot(youtube):
    """Analyze past performance and return the best publish time slot.
    Strategy: Main channel data PRIMARY until new channel crosses 1L total views.
    After 1L, new channel's own analytics take priority."""

    total_views = get_new_channel_total_views()
    use_own_data = total_views >= NEW_CHANNEL_VIEWS_THRESHOLD

    if use_own_data:
        print(f"   📊 New channel: {total_views:,} total views (≥1L) — own analytics PRIMARY")
    else:
        print(f"   📊 New channel: {total_views:,} total views (<1L) — main channel data PRIMARY")

    # ── Priority 1 (when ≥1L): Own channel analytics ──
    if use_own_data:
        analytics = None
        if youtube:
            analytics = fetch_recent_video_analytics(youtube)

        if analytics and len(analytics) >= 2:
            # Save analytics to file for tracking
            try:
                with open(ANALYTICS_FILE, "w") as f:
                    json.dump({"last_updated": datetime.now().isoformat(), "slots": analytics}, f, indent=2)
            except Exception:
                pass

            # Find the best performing slot
            best_label = max(analytics, key=lambda k: analytics[k]["avg_views"])
            best_avg = analytics[best_label]["avg_views"]

            print(f"   📊 YouTube Analytics (last 21 videos):")
            for label, data in sorted(analytics.items()):
                marker = " ← BEST" if label == best_label else ""
                print(f"      {label}: {data['avg_views']} avg views ({data['count']} videos){marker}")

            # Use best slot if significantly better (>20% above average)
            all_avgs = [d["avg_views"] for d in analytics.values()]
            overall_avg = sum(all_avgs) / len(all_avgs) if all_avgs else 0

            if best_avg > overall_avg * 1.2:
                print(f"   🏆 Analytics-optimized: using {best_label} (own channel best performer)")
                for hour, minute, label in PUBLISH_SLOTS:
                    if label == best_label:
                        return hour, minute, label
            else:
                print(f"   📊 No clear winner in own data — falling through")

    # ── Priority 2 RETIRED 2026-07-06: source-channel proxy no longer used.
    # Owner's own-channel data (19:00 IST median 79 views vs 21:30 median 31.5)
    # is encoded directly in PUBLISH_SLOT_SCHEDULE; letting the source channel's
    # posting pattern override it defeated the reweighting. Own-channel
    # analytics (Priority 1) resume automatically at ≥1L total views.

    # ── Priority 3: A/B rotation schedule ──
    return None


def get_publish_time(youtube=None):
    ist = pytz.timezone(TIMEZONE)
    now = datetime.now(ist)

    # Try analytics-optimized slot first
    optimized = get_best_publish_slot(youtube)

    if optimized:
        hour, minute, label = optimized
    else:
        # A/B test: pick publish slot based on day of week
        weekday = now.weekday()  # 0=Monday ... 6=Sunday
        slot_idx = PUBLISH_SLOT_SCHEDULE.get(weekday, 0)
        hour, minute, label = PUBLISH_SLOTS[slot_idx]

    today_publish = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= today_publish:
        # If we've passed today's slot, schedule for tomorrow
        tomorrow = now + timedelta(days=1)
        if not optimized:
            tomorrow_weekday = tomorrow.weekday()
            slot_idx = PUBLISH_SLOT_SCHEDULE.get(tomorrow_weekday, 0)
            hour, minute, label = PUBLISH_SLOTS[slot_idx]
        publish_at = tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)
    else:
        publish_at = today_publish

    publish_utc = publish_at.astimezone(pytz.utc)
    print(f"   ⏰ Publish slot: {label} ({['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][publish_at.weekday()]})")
    return publish_at, publish_utc


def get_youtube_service():
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except:
            creds = None

    # Check if token has all required scopes (catches stale tokens missing force-ssl)
    if creds and creds.valid and creds.scopes:
        required = set(SCOPES)
        granted = set(creds.scopes)
        missing_scopes = required - granted
        if missing_scopes:
            print(f"   ⚠️ YouTube token missing scopes: {missing_scopes}")
            print(f"   🔄 Deleting stale token — re-auth needed with full scopes.")
            os.remove(TOKEN_FILE)
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                print("   🔄 YouTube token refreshed!")
                with open(TOKEN_FILE, "w") as f:
                    f.write(creds.to_json())
            except Exception as e:
                if "invalid_scope" in str(e):
                    print(f"   ⚠️ Scope mismatch detected, trying with token's original scopes...")
                    try:
                        # Load without enforcing scopes — let the token use whatever it was granted
                        creds = Credentials.from_authorized_user_file(TOKEN_FILE)
                        creds.refresh(Request())
                        print("   🔄 YouTube token refreshed (with original scopes)!")
                        print("   ⚠️ Note: Re-run generate_token.py to get full scopes (comments, playlists, analytics)")
                        with open(TOKEN_FILE, "w") as f:
                            f.write(creds.to_json())
                    except Exception as e2:
                        print(f"   ❌ Token refresh failed: {e2}")
                        return None
                else:
                    print(f"   ❌ Token refresh failed: {e}")
                    return None
        else:
            print("   ❌ No valid YouTube token. Run generate_token.py to get a new token.")
            return None

    return build("youtube", "v3", credentials=creds)


def upload_to_youtube(youtube, video_path, title, description, tags, topic=""):
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    # SEO enhance
    if UPLOAD_AS_SHORT and "#shorts" not in title.lower():
        if len(title) + 8 <= 100:
            title += " #Shorts"

    # Dynamic hashtags based on topic
    topic_hashtags = get_topic_hashtags(topic)
    hashtag_line = " ".join(topic_hashtags)

    seo_description = f"""{description}

━━━━━━━━━━━━━━━━━━━━━━━━
📦 Order Plain T-shirts: https://sale91.com
━━━━━━━━━━━━━━━━━━━━━━━━

🏭 About Sale91.com:
India's trusted B2B plain t-shirt manufacturer. We knit our own fabric in-house.
180-220 GSM | 100% Cotton | Bio-washed | Pre-shrunk | Ring-spun Combed Cotton
MOQ just 10 pieces | Ready stock | Pan India delivery

Perfect for: DTG Printing | DTF Printing | Screen Printing | Heat Transfer
Custom printing businesses | Merch brands | Corporate orders

{hashtag_line}
"""

    # Dynamic tags: Claude's tags + topic-specific tags + booster tags
    all_tags = list(tags) if tags else []
    topic_specific = get_topic_tags(topic)
    for t in topic_specific:
        if t.lower() not in [x.lower() for x in all_tags]:
            all_tags.append(t)
    # Generic boosters removed — they waste tag budget and add zero topical relevance.
    all_tags = sanitize_tags(all_tags[:30])
    print(f"   🏷️  Tags ({len(all_tags)}): {all_tags[:5]}{'...' if len(all_tags) > 5 else ''}")

    body = {
        "snippet": {
            "title": title[:100],
            "description": seo_description[:5000],
            "tags": all_tags,
            "categoryId": "22",
            "defaultLanguage": "hi",
            "defaultAudioLanguage": "hi"
        },
        "status": {
            "selfDeclaredMadeForKids": False,
            "embeddable": True,
            # YouTube A/S disclosure — required: realistic Veo scenes that didn't occur
            # (own-voice clone alone is exempt, but the visuals are not). Undisclosed
            # synthetic media risks forced labels / removal / YPP suspension.
            "containsSyntheticMedia": True,
        }
    }

    if NEW_TEST_MODE or SINGLE_VEO_TEST:
        body["status"]["privacyStatus"] = "unlisted"
        mode_label = "NEW TEST MODE" if NEW_TEST_MODE else "SINGLE VEO TEST"
        print(f"   🧪 {mode_label} — uploading as UNLISTED (not visible to public)")
    elif SCHEDULE_PUBLISH:
        publish_ist, publish_utc = get_publish_time(youtube=youtube)
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = publish_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        schedule_str = publish_ist.strftime("%d %b %Y, %I:%M %p IST")
        print(f"   📅 Scheduled: {schedule_str}")
    else:
        body["status"]["privacyStatus"] = "public"

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=1024*1024)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    print(f"   📤 Uploading: {title}")
    response = None
    retry = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"   ⬆️  {int(status.progress() * 100)}%...")
        except Exception as e:
            retry += 1
            if retry > 5:
                raise
            time.sleep(random.uniform(1, 2 ** retry))

    vid_id = response.get("id", "?")
    url = f"https://youtube.com/shorts/{vid_id}"
    print(f"  ✅ UPLOADED! {url}")
    return vid_id, url


# ═══════════════════════════════════════════════════════════════════════
# BACKGROUND MUSIC
# ═══════════════════════════════════════════════════════════════════════

# Mood → tags for AI music generation (ACE-Step via Replicate)
MOOD_TO_MUSIC_PROMPT = {
    "upbeat": "upbeat, happy, energetic, instrumental, positive vibes, bright, electronic, pop",
    "calm": "soft, ambient, lo-fi, instrumental, relaxed, gentle piano, chill, downtempo",
    "serious": "deep, cinematic, dramatic, instrumental, serious tone, orchestral, dark",
    "motivational": "motivational, inspiring, corporate, instrumental, uplifting, epic, anthemic",
    "trendy": "modern, trendy, electronic beat, cool, urban, instrumental, trap, hip-hop",
}



def generate_bg_music(mood="calm"):
    """Generate background music using ACE-Step via Replicate API.
    Retries up to 3 times with exponential backoff on transient errors (429, 5xx)."""
    api_token = os.environ.get("REPLICATE_API_TOKEN")
    if not api_token:
        return None

    try:
        import replicate
    except ImportError:
        print("   ⚠️ replicate package not installed — using local music files")
        return None

    tags = MOOD_TO_MUSIC_PROMPT.get(mood, MOOD_TO_MUSIC_PROMPT["calm"])
    music_path = f"{BG_MUSIC_FOLDER}/ai_{mood}_{random.randint(100,999)}.wav"

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            print(f"   🤖 Generating '{mood}' music via Replicate ACE-Step (attempt {attempt}/{max_retries})...")
            output = replicate.run(
                "lucataco/ace-step:280fc4f9ee507577f880a167f639c02622421d8fecf492454320311217b688f1",
                input={
                    "tags": tags,
                    "lyrics": "[instrumental]",
                    "duration": 30,
                    "seed": random.randint(1, 2**31),
                },
            )

            # Handle both FileOutput (SDK >= 1.0) and raw URL string (older SDK)
            if hasattr(output, 'read'):
                audio_bytes = output.read()
            elif isinstance(output, str) and output.startswith("http"):
                from urllib.request import urlopen
                audio_bytes = urlopen(output).read()
            else:
                print(f"   ⚠️ Unexpected Replicate output type: {type(output)}")
                return None

            if audio_bytes:
                with open(music_path, "wb") as f:
                    f.write(audio_bytes)
                print(f"   ✅ AI music generated: {os.path.basename(music_path)}")
                return music_path
            else:
                print("   ⚠️ Replicate returned empty audio")
                return None

        except Exception as e:
            err_str = str(e).lower()
            status = getattr(e, 'status', None) or getattr(e, 'status_code', None)
            is_status_retryable = status is not None and (status == 429 or status >= 500)
            is_msg_retryable = any(k in err_str for k in ("429", "rate limit", "too many", "timeout", "timed out", "502", "503", "504", "unavailable"))
            is_retryable = is_status_retryable or is_msg_retryable
            if is_retryable and attempt < max_retries:
                wait = 2 ** attempt  # 2s, 4s
                print(f"   ⚠️ Replicate ACE-Step attempt {attempt} failed: {e}")
                print(f"   ⏳ Retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"   ⚠️ Replicate ACE-Step failed after {attempt} attempt(s): {e}")
                return None
    return None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║                   TTS PRE-PROCESSING + SARVAM CLIENT                 ║
# ╚══════════════════════════════════════════════════════════════════════╝

# Devanagari numbers 0-100 — ElevenLabs Hindi pipeline pronounces Devanagari
# natively. Latin-script Hinglish ("assi", "aath") gets read as English gibberish.
# IMPORTANT: every number 11-99 is a unique Hindi word, NOT compositional like
# English "thirty-five". Full table required.
_HINDI_NUMBER_BELOW_100 = {
    0: "शून्य", 1: "एक", 2: "दो", 3: "तीन", 4: "चार",
    5: "पाँच", 6: "छह", 7: "सात", 8: "आठ", 9: "नौ",
    10: "दस", 11: "ग्यारह", 12: "बारह", 13: "तेरह", 14: "चौदह",
    15: "पंद्रह", 16: "सोलह", 17: "सत्रह", 18: "अट्ठारह", 19: "उन्नीस",
    20: "बीस", 21: "इक्कीस", 22: "बाईस", 23: "तेईस", 24: "चौबीस",
    25: "पच्चीस", 26: "छब्बीस", 27: "सत्ताईस", 28: "अट्ठाईस", 29: "उनतीस",
    30: "तीस", 31: "इकतीस", 32: "बत्तीस", 33: "तैंतीस", 34: "चौंतीस",
    35: "पैंतीस", 36: "छत्तीस", 37: "सैंतीस", 38: "अड़तीस", 39: "उनतालीस",
    40: "चालीस", 41: "इकतालीस", 42: "बयालीस", 43: "तैंतालीस", 44: "चौवालीस",
    45: "पैंतालीस", 46: "छियालीस", 47: "सैंतालीस", 48: "अड़तालीस", 49: "उनचास",
    50: "पचास", 51: "इक्यावन", 52: "बावन", 53: "तिरपन", 54: "चौवन",
    55: "पचपन", 56: "छप्पन", 57: "सत्तावन", 58: "अट्ठावन", 59: "उनसठ",
    60: "साठ", 61: "इकसठ", 62: "बासठ", 63: "तिरसठ", 64: "चौंसठ",
    65: "पैंसठ", 66: "छियासठ", 67: "सड़सठ", 68: "अड़सठ", 69: "उनहत्तर",
    70: "सत्तर", 71: "इकहत्तर", 72: "बहत्तर", 73: "तिहत्तर", 74: "चौहत्तर",
    75: "पचहत्तर", 76: "छिहत्तर", 77: "सतहत्तर", 78: "अठहत्तर", 79: "उन्यासी",
    80: "अस्सी", 81: "इक्यासी", 82: "बयासी", 83: "तिरासी", 84: "चौरासी",
    85: "पचासी", 86: "छियासी", 87: "सत्तासी", 88: "अठ्ठासी", 89: "नवासी",
    90: "नब्बे", 91: "इक्यानवे", 92: "बानवे", 93: "तिरानवे", 94: "चौरानवे",
    95: "पंचानवे", 96: "छियानवे", 97: "सत्तानवे", 98: "अट्ठानवे", 99: "निन्यानवे",
    100: "सौ",
}


def _hindi_number(n: int) -> str:
    """Convert an integer to spoken Hindi (Devanagari)."""
    if n in _HINDI_NUMBER_BELOW_100:
        return _HINDI_NUMBER_BELOW_100[n]
    if n < 100:
        # Should never happen — table is complete 0-100. Defensive fallback.
        return str(n)
    if n < 1000:
        h, rest = n // 100, n % 100
        head = f"{_HINDI_NUMBER_BELOW_100[h]} सौ"
        return head if rest == 0 else f"{head} {_hindi_number(rest)}"
    if n < 100000:
        thousands, rest = n // 1000, n % 1000
        head = f"{_hindi_number(thousands)} हज़ार"
        return head if rest == 0 else f"{head} {_hindi_number(rest)}"
    if n < 10000000:
        lakhs, rest = n // 100000, n % 100000
        head = f"{_hindi_number(lakhs)} लाख"
        return head if rest == 0 else f"{head} {_hindi_number(rest)}"
    crores, rest = n // 10000000, n % 10000000
    head = f"{_hindi_number(crores)} करोड़"
    return head if rest == 0 else f"{head} {_hindi_number(rest)}"


# Acronyms — keep most as-is. ElevenLabs multilingual+ models handle common
# initialisms (DTG, GSM, B2B, MOQ) natively. We only respell when a model
# version mispronounces a specific one. Empty by default — the original
# capitalized acronym in the script reaches ElevenLabs unchanged.
_TTS_ACRONYM_MAP = {
    # (empty — re-enable specific entries only when a user test confirms it)
}

# Phonetic respelling map — kept minimal.
# Earlier I had aggressive respellings (chalegaa, theek, mut-lub etc.) which
# made ElevenLabs sound WORSE, not better. ElevenLabs Hindi/multilingual is
# smart enough to handle natural Hinglish — over-engineering the input degrades
# its prosody. Only keep entries that are PROVEN broken via user feedback.
_TTS_PHONETIC_MAP = {
    # User-reported mispronunciations — respell so ElevenLabs's multilingual
    # model reads the intended English phonemes. Each entry must be tested.
    # Format: <Latin word as Claude writes> → <Latin respelling that ElevenLabs reads correctly>
    #
    # "combed" → "kohmd" was tuned on eleven_multilingual_v2 (2026-05-08).
    # Production moved to eleven_v3 on 2026-07-06, which reads that respelling
    # WRONG (Ketu report, 80-vs-140 video 2026-07-13). v3 handles real English
    # words natively, so combed is now handled in Devanagari below (कोम्ड) —
    # deterministic on v3 — instead of a Latin respelling. Left this map in
    # place (empty) for future v3-confirmed respellings.
}

# Hinglish (Latin) → Devanagari for high-frequency Hindi words ElevenLabs
# reads as English. Confirmed broken via user feedback: "mat", "thik", "matlab",
# plus their semantic neighbors. English nouns (piece, order, customer, DTF,
# printer, loss) stay in Latin so the multilingual model code-switches naturally.
_TTS_HINGLISH_DEVANAGARI = {
    # User-reported (2026-05-08): mat/thik/assi mispronounced.
    "mat": "मत", "matlab": "मतलब", "thik": "ठीक", "theek": "ठीक",
    # User-reported (2026-07-06): badbu/sada mispronounced in the ₹80k storage
    # video. Multi-word forms FIRST — single-word rewrites below ("gaya" etc.)
    # would break their match if applied earlier.
    "sad gaya": "सड़ गया", "sad jayega": "सड़ जाएगा", "sad jaega": "सड़ जाएगा",
    "sada hua": "सड़ा हुआ", "sadne laga": "सड़ने लगा",
    "badbu": "बदबू", "badboo": "बदबू", "badbudar": "बदबूदार",
    "sadta": "सड़ता", "sadti": "सड़ती", "sadna": "सड़ना", "sadne": "सड़ने",
    "sadega": "सड़ेगा", "sadenge": "सड़ेंगे",
    # NOTE: bare "sada" stays unmapped — ambiguous with सादा (plain), core
    # vocabulary for a plain-tshirt business.
    # High-frequency function words / verbs in our scripts:
    "nahi": "नहीं", "nahin": "नहीं", "haan": "हाँ", "hai": "है", "hain": "हैं",
    "tha": "था", "thi": "थी",
    # NOTE: "the" removed from the blanket map (2026-07-11) — it was turning
    # English "the best/the same" into थे. Hindi थे is clause-final ("hum
    # saath the."), English "the" always precedes a word, so a punctuation
    # lookahead in normalize_for_tts handles it instead.
    "kiya": "किया", "karna": "करना", "karta": "करता", "karti": "करती",
    "bola": "बोला", "boli": "बोली", "bolta": "बोलता",
    "liya": "लिया", "lena": "लेना", "diya": "दिया", "dena": "देना",
    "chala": "चला", "chalega": "चलेगा", "chalti": "चलती",
    "hua": "हुआ", "hui": "हुई", "hota": "होता", "hoti": "होती",
    "mujhe": "मुझे", "tumhe": "तुम्हें", "usse": "उससे",
    "mahine": "महीने", "mahina": "महीना", "saal": "साल", "din": "दिन",
    "galti": "गलती", "loss": "loss",  # leave 'loss' English on purpose
    "rupaye": "रुपये", "rupiya": "रुपया", "rupaiye": "रुपये",
    "hazaar": "हज़ार", "hazar": "हज़ार",
    "hazaron": "हज़ारों", "hazaron_": "हज़ारों",
    "lakh": "लाख", "laakh": "लाख",
    "lakhon": "लाखों", "laakhon": "लाखों",
    "crore": "करोड़", "croron": "करोड़ों", "crorono": "करोड़ों",
    # Function words / postpositions — almost never English in our scripts.
    "ek": "एक", "ne": "ने", "ka": "का", "ki": "की", "ke": "के",
    "ko": "को", "mein": "में", "pe": "पे", "par": "पर", "se": "से",
    "to": "तो", "bhi": "भी",
    "ye": "ये", "yeh": "यह", "woh": "वो", "vo": "वो",
    "aur": "और", "lekin": "लेकिन", "ya": "या", "kyunki": "क्योंकि",
    "agar": "अगर", "jab": "जब", "tab": "तब",
    "kya": "क्या", "kyun": "क्यों", "kaise": "कैसे", "kahan": "कहाँ",
    "phir": "फिर", "abhi": "अभी", "ab": "अब",
    "sab": "सब", "kuch": "कुछ", "koi": "कोई", "bahut": "बहुत",
    "ho": "हो", "hona": "होना", "hoga": "होगा", "hogi": "होगी", "honge": "होंगे",
    "kar": "कर", "karo": "करो", "kare": "करे", "karein": "करें",
    "karte": "करते", "karta": "करता", "karti": "करती",
    "kiya": "किया", "kiye": "किये",
    # Pronouns
    "main": "मैं", "maine": "मैंने", "mera": "मेरा", "meri": "मेरी", "mere": "मेरे",
    "tum": "तुम", "tumhara": "तुम्हारा", "tumhari": "तुम्हारी", "tumhare": "तुम्हारे",
    "aap": "आप", "aapka": "आपका", "aapki": "आपकी", "aapke": "आपके",
    "humne": "हमने", "hum": "हम", "hamara": "हमारा", "hamari": "हमारी", "hamare": "हमारे",
    "uska": "उसका", "uski": "उसकी", "uske": "उसके", "usne": "उसने",
    # Common verbs / state
    "raha": "रहा", "rahi": "रही", "rahe": "रहे",
    "gaya": "गया", "gayi": "गई", "gaye": "गए",
    "aaya": "आया", "aayi": "आई", "aaye": "आए", "aata": "आता", "aati": "आती", "aate": "आते",
    "jaata": "जाता", "jata": "जाता", "jaati": "जाती", "jaate": "जाते",
    "rakha": "रखा", "rakhna": "रखना", "rakhte": "रखते",
    "samjha": "समझा", "samjhi": "समझी", "samajh": "समझ", "samajhna": "समझना",
    "dekh": "देख", "dekha": "देखा", "dekhna": "देखना", "dekhte": "देखते",
    "bola": "बोला", "boli": "बोली", "bole": "बोले",
    "sun": "सुन", "suna": "सुना", "sunna": "सुनना",
    "chahiye": "चाहिए",
    # Adjectives / adverbs
    "accha": "अच्छा", "acchi": "अच्छी", "acche": "अच्छे",
    "zyada": "ज़्यादा", "kam": "कम", "thoda": "थोड़ा", "thodi": "थोड़ी",
    "zaroori": "ज़रूरी", "varna": "वरना",
    "kharab": "खराब", "sahi": "सही",
    "pehle": "पहले", "baad": "बाद",
    "pichhle": "पिछले", "pichhli": "पिछली",
    # Filler / discourse
    "yaar": "यार", "bhai": "भाई", "arre": "अरे",
    "baat": "बात", "mehnat": "मेहनत",
    "mila": "मिला", "milta": "मिलता", "milti": "मिलती",
    "subah": "सुबह", "shaam": "शाम", "raat": "रात",
    "sirf": "सिर्फ", "bilkul": "बिलकुल", "matlab": "मतलब",
    "dete": "देते", "deta": "देता", "deti": "देती",
    "lete": "लेते", "leta": "लेता", "leti": "लेती",
    "bhara": "भरा", "bhari": "भरी", "bharte": "भरते",
    "naya": "नया", "nayi": "नई", "naye": "नए", "purana": "पुराना", "purani": "पुरानी",
    # Cycle 1 additions — common Hinglish words still in Latin after preprocessing.
    # Time/frequency
    "hafte": "हफ़्ते", "hafta": "हफ़्ता", "roz": "रोज़",
    "hamesha": "हमेशा", "kabhi": "कभी", "kab": "कब", "tak": "तक",
    # Pronouns / determiners
    "tu": "तू", "tujh": "तुझ", "tujhe": "तुझे",
    "har": "हर", "koi": "कोई", "kaun": "कौन", "jo": "जो", "jaisa": "जैसा", "jaisi": "जैसी", "jaise": "जैसे",
    "aapko": "आपको", "aapse": "आपसे", "aapne": "आपने",
    "logon": "लोगों", "log": "लोग",
    "si": "सी",
    # Verbs (more inflections)
    "bata": "बता", "batayi": "बताई", "batao": "बताओ", "batana": "बताना",
    "samjha": "समझा", "samjhi": "समझी", "samjhta": "समझता", "samjhti": "समझती",
    "samjhaaya": "समझाया", "samjhaayi": "समझाई", "samjhana": "समझाना",
    "lag": "लग", "lagta": "लगता", "lagti": "लगती", "lagte": "लगते", "laga": "लगा",
    "lage": "लगे", "lagi": "लगी",
    "soch": "सोच", "sochta": "सोचता", "sochti": "सोचती", "sochna": "सोचना",
    "sochke": "सोचके", "sochkar": "सोचकर", "sochkr": "सोचकर",
    "dekho": "देखो", "dekhi": "देखी", "dekhe": "देखे",
    "daal": "डाल", "daala": "डाला", "daali": "डाली", "daalna": "डालना",
    "pi": "पी", "piya": "पिया", "piyo": "पियो",
    "kha": "खा", "khaya": "खाया", "khao": "खाओ",
    "karke": "करके", "karne": "करने",
    "banaya": "बनाया", "banayi": "बनाई", "banaye": "बनाए",
    "banata": "बनाता", "banati": "बनाती", "banate": "बनाते",
    "bhejte": "भेजते", "bheja": "भेजा", "bhejna": "भेजना", "bhej": "भेज",
    "bolne": "बोलने", "bolna": "बोलना",
    "dega": "देगा", "degi": "देगी", "denge": "देंगे",
    "lega": "लेगा", "legi": "लेगी", "lenge": "लेंगे",
    "jaayega": "जाएगा", "jaayegi": "जाएगी", "jaayenge": "जाएँगे", "jaate": "जाते",
    "aaye": "आए", "aati": "आती", "aate": "आते",
    "rahega": "रहेगा", "rahegi": "रहेगी", "rahenge": "रहेंगे",
    # Nouns (high-frequency Hindi)
    "kaam": "काम", "dukaan": "दुकान", "paisa": "पैसा", "paise": "पैसे",
    "maal": "माल", "saath": "साथ", "saamna": "सामना",
    "bharosa": "भरोसा", "dosti": "दोस्ती", "dushmani": "दुश्मनी",
    "nuksaan": "नुकसान", "fayda": "फ़ायदा", "labh": "लाभ",
    "paani": "पानी", "rasta": "रास्ता", "raasta": "रास्ता",
    "duniya": "दुनिया", "zindagi": "ज़िंदगी",
    "ghar": "घर", "mandir": "मंदिर",
    # Adjectives
    "alag": "अलग", "ajeeb": "अजीब",
    "chhota": "छोटा", "chhoti": "छोटी", "chhote": "छोटे",
    "bada": "बड़ा", "badi": "बड़ी", "bade": "बड़े",
    "sasta": "सस्ता", "sasti": "सस्ती", "mehnga": "महँगा", "mehngi": "महँगी",
    "taiyar": "तैयार", "gayab": "गायब", "maujood": "मौजूद",
    "akela": "अकेला", "akeli": "अकेली", "akele": "अकेले",
    "badhi": "बढ़ी", "badha": "बढ़ा", "badhe": "बढ़े", "badhna": "बढ़ना",
    "asaan": "आसान", "mushkil": "मुश्किल",
    # Prepositions / connectors / qualifiers
    "bina": "बिना", "binaa": "बिना",
    "saare": "सारे", "saari": "सारी", "sara": "सारा",
    "sabse": "सबसे", "kuchh": "कुछ",
    "liye": "लिए", "waste": "वास्ते",
    "dono": "दोनों", "donon": "दोनों",
    "wala": "वाला", "wali": "वाली", "wale": "वाले",
    "pata": "पता",
    "dhulai": "धुलाई",
    "ekdum": "एकदम",
    # Filler/respelling for "fir" (alternate of "phir")
    "fir": "फिर",
    # First-person / common state
    "hoon": "हूँ", "hu": "हूँ",
    "hota": "होता", "hoti": "होती", "hote": "होते",
    "kuchh": "कुछ",
    # Light pronoun additions
    "isliye": "इसलिए", "iska": "इसका", "iski": "इसकी", "iske": "इसके",
    "iss": "इस", "us": "उस", "in": "इन", "un": "उन",
    # Cycle 2 additions — high-frequency Hindi words still slipping through.
    # Verbs / inflections
    "khatam": "ख़त्म", "khatm": "ख़त्म",
    "lo": "लो", "le": "ले", "li": "ली",
    "paas": "पास", "pas": "पास",
    "toh": "तो",  # alt spelling
    "zaruri": "ज़रूरी", "zaroori": "ज़रूरी",  # alt spellings
    "ekdam": "एकदम",  # alt of ekdum
    "aaj": "आज", "kal": "कल",
    "aane": "आने", "jaane": "जाने",
    "aisi": "ऐसी", "aisa": "ऐसा", "aise": "ऐसे",
    "apne": "अपने", "apna": "अपना", "apni": "अपनी",
    "baar": "बार", "baari": "बारी",
    "banta": "बनता", "banti": "बनती", "bante": "बनते",
    "bataun": "बताऊँ", "batayegi": "बताएगी", "batayega": "बताएगा",
    "behtar": "बेहतर", "achchha": "अच्छा",
    "chadhti": "चढ़ती", "chadhna": "चढ़ना", "chadha": "चढ़ा",
    "chhat": "छत", "chad": "छत",
    "dhoondhe": "ढूँढ़े", "dhoondhna": "ढूँढ़ना", "dhoondha": "ढूँढ़ा",
    "di": "दी", "diye": "दिये", "diyaa": "दिया",
    "dikhata": "दिखाता", "dikhati": "दिखाती", "dikhana": "दिखाना", "dikha": "दिखा",
    "doosri": "दूसरी", "doosra": "दूसरा", "doosre": "दूसरे",
    "gadbad": "गड़बड़",
    "hath": "हाथ",
    "humare": "हमारे", "humse": "हमसे", "humein": "हमें",
    "mana": "मना",
    "mangao": "मँगाओ", "mangwana": "मँगवाना",
    "mile": "मिले", "milne": "मिलने", "milta": "मिलता", "milte": "मिलते",
    "neeche": "नीचे", "upar": "ऊपर", "andar": "अंदर", "bahar": "बाहर",
    "pada": "पड़ा", "padi": "पड़ी", "padega": "पड़ेगा", "padegi": "पड़ेगी",
    "pehli": "पहली", "pehla": "पहला", "pehle_": "पहले",  # pehle already mapped
    "poora": "पूरा", "poori": "पूरी", "poore": "पूरे",
    "pohchana": "पहुँचाना", "pahuchana": "पहुँचाना", "pohcha": "पहुँचा",
    "samjhao": "समझाओ", "samjho": "समझो",
    "taraf": "तरफ़",
    "wapas": "वापस", "wapis": "वापस",
    "yahan": "यहाँ", "yahin": "यहीं", "wahan": "वहाँ", "wahin": "वहीं",
    "zaroor": "ज़रूर",
    "asli": "असली", "asal": "असल",
    "ekta": "एकता",
    "gunjayish": "गुंजाइश",
    # Cycle 3 additions
    "baje": "बजे", "ghante": "घंटे", "ghanta": "घंटा",
    "aa": "आ", "ja": "जा",
    "aajkal": "आजकल",
    "agle": "अगले", "agla": "अगला", "agli": "अगली",
    "bachta": "बचता", "bachti": "बचती", "bachna": "बचना",
    "badh": "बढ़", "badhega": "बढ़ेगा", "badhegi": "बढ़ेगी",
    "bana": "बना", "bani": "बनी", "banwana": "बनवाना",
    "beta": "बेटा", "beti": "बेटी",
    "chhodi": "छोड़ी", "chhoda": "छोड़ा", "chhodna": "छोड़ना",
    "dhairya": "धैर्य",
    "haar": "हार", "haari": "हारी", "haare": "हारे",
    "hawa": "हवा",
    "hone": "होने", "hoga_": "होगा",
    "jaaye": "जाए", "jaye": "जाए",
    "karenge": "करेंगे", "karni": "करनी", "karunga": "करूँगा",
    "karungi": "करूँगी", "karwana": "करवाना",
    "khela": "खेला", "kheli": "खेली", "khelta": "खेलता", "khelti": "खेलती", "khelna": "खेलना",
    "khush": "ख़ुश", "khushi": "ख़ुशी", "dukh": "दुख", "dukhi": "दुखी",
    "lagega": "लगेगा", "lagegi": "लगेगी",
    "likhne": "लिखने", "likhna": "लिखना", "likha": "लिखा", "likhi": "लिखी", "likho": "लिखो",
    "maani": "मानी", "manna": "मानना",
    "man": "मन", "dimag": "दिमाग",
    "marte": "मारते", "marna": "मारना", "maari": "मारी",
    "mausam": "मौसम",
    "nikal": "निकल", "nikli": "निकली", "nikla": "निकला", "nikalna": "निकलना",
    "padhai": "पढ़ाई", "padhna": "पढ़ना", "padha": "पढ़ा", "padhi": "पढ़ी",
    "pagal": "पागल",
    "pasand": "पसंद", "napasand": "नापसंद",
    "pohchna": "पहुँचना", "pahuchna": "पहुँचना",
    "sake": "सके", "saka": "सका",
    "sakta": "सकता", "sakte": "सकते", "sakti": "सकती",
    "sardi": "सर्दी", "garmi": "गर्मी", "barsaat": "बरसात",
    "seedha": "सीधा", "seedhi": "सीधी",
    "shaadi": "शादी",
    "shuru": "शुरू", "shuruwat": "शुरुआत",
    "suno": "सुनो", "sunna": "सुनना",
    "taanay": "ताने", "taana": "ताना",
    "uthkar": "उठकर", "uthna": "उठना", "utha": "उठा", "uthi": "उठी",
    "papa": "पापा", "mummy": "मम्मी",
    "ladka": "लड़का", "ladki": "लड़की", "ladke": "लड़के", "ladkiyaan": "लड़कियाँ",
    "behen": "बहन", "bhaiya": "भैया", "didi": "दीदी",
    "rishtedaar": "रिश्तेदार", "dost": "दोस्त",
    # Cycle 4 additions
    "dheere": "धीरे",
    "khud": "ख़ुद", "khudko": "ख़ुदको",
    "bachpan": "बचपन", "javaani": "जवानी", "budhaapa": "बुढ़ापा",
    "badhotari": "बढ़ोतरी", "kami": "कमी",
    "banao": "बनाओ", "banata": "बनाता",
    "bandh": "बंद", "khol": "खोल", "khulna": "खुलना",
    "bika": "बिका", "biki": "बिकी", "bikna": "बिकना", "bikega": "बिकेगा", "bikegi": "बिकेगी",
    "bechna": "बेचना", "becha": "बेचा", "bechi": "बेची", "bechte": "बेचते",
    "chalana": "चलाना", "chal": "चल", "chalna": "चलना",
    "cheez": "चीज़",
    "dhula": "धुला", "dhuli": "धुली", "dhona": "धोना",
    "dhoke": "धोके", "dhokar": "धोकर", "dhokr": "धोकर", "dhoye": "धोए", "dhoya": "धोया",
    "dikhane": "दिखाने", "dikhayi": "दिखाई", "dikhaye": "दिखाए",
    "honi": "होनी",
    "jaa": "जा",
    "kapda": "कपड़ा", "kapde": "कपड़े",
    "kara": "किया",  # alternate spelling
    "karwane": "करवाने",
    "kharch": "ख़र्च", "kharcha": "ख़र्चा",
    "lagao": "लगाओ", "lagata": "लगाता", "lagatar": "लगातार",
    "mangwaaya": "मँगवाया",
    "milengi": "मिलेंगी", "milega": "मिलेगा", "milegi": "मिलेगी",
    "mujhse": "मुझसे", "tujhse": "तुझसे", "ussे": "उससे",
    "padosi": "पड़ोसी",
    "padti": "पड़ती",
    "rishte": "रिश्ते",
    "sabhi": "सभी", "sabki": "सबकी", "sabka": "सबका", "sabke": "सबके",
    "seekhne": "सीखने", "seekhna": "सीखना", "sikha": "सीखा", "sikhi": "सीखी", "seekha": "सीखा",
    "taiyari": "तैयारी",
    "ummeed": "उम्मीद", "asha": "आशा",
    "waha": "वहाँ",
    # Common short words I missed
    "thoda_": "थोड़ा",  # already mapped
    "saari_": "सारी",  # already mapped
    "soche": "सोचे",
    # Cycle 5 additions (real production topics)
    "kaunsa": "कौनसा", "kaunsi": "कौनसी", "kaunse": "कौनसे",
    "bekar": "बेकार", "behtareen": "बेहतरीन", "kamaal": "कमाल",
    "kismein": "किसमें", "kisko": "किसको", "kiska": "किसका", "kiski": "किसकी", "kiske": "किसके",
    "leke": "लेके", "deke": "देके",
    "niklega": "निकलेगा", "niklegi": "निकलेगी",
    "pehchano": "पहचानो", "pehchaan": "पहचान",
    "roa": "रोआ",
    # Cycle 6 additions
    "fark": "फ़र्क", "fark_": "फ़र्क",  # alt spellings
    "bas": "बस",
    "dikhta": "दिखता", "dikhti": "दिखती",
    "isi": "इसी", "usi": "उसी",
    "isse": "इससे", "isme": "इसमें", "usme": "उसमें",
    "khata": "खाता", "khaata": "खाता",
    "warna": "वरना",  # alt of varna (already mapped)
    "khareed": "ख़रीद", "khareeda": "ख़रीदा", "khareedna": "ख़रीदना",
    "bechen": "बेचें", "khareeden": "ख़रीदें",
    # Common short connectors
    "haye": "हाय", "uff": "उफ़",
    "achchhi": "अच्छी", "acchhe": "अच्छे",  # alt spellings
    # Cycles 7+8 additions (real production topics, second batch)
    "bikta": "बिकता", "bikti": "बिकती",
    "chalayein": "चलाएँ", "chalega_": "चलेगा",  # already mapped
    "dhul": "धुल", "dhula_": "धुला",  # already mapped
    "dhundein": "ढूँढ़ें", "dhundo": "ढूँढ़ो", "dhundta": "ढूँढ़ता",
    "haath": "हाथ",  # alt spelling of hath
    "jayega": "जाएगा",  # alt of jaayega
    "kama": "कमा", "kamai": "कमाई", "kamana": "कमाना", "kamaye": "कमाए",
    "karega": "करेगा", "karegi": "करेगी",
    "karwai": "करवाई", "karyavahi": "कार्रवाई",
    "kheench": "खींच", "kheecho": "खींचो",
    "kyu": "क्यों",  # alt of kyun
    "lein": "लें", "denge_": "देंगे",  # already mapped
    "ruka": "रुका", "ruki": "रुकी", "rukna": "रुकना",
    "sa": "सा", "se_": "से",  # already mapped; sa is suffix
    "seekhe": "सीखे",
    "wajah": "वजह", "vajah": "वजह",
    # Few more inflections noticed
    "fir_": "फिर",  # already mapped
    "dukan": "दुकान",
    # Cycle 9 additions (20-script production sample)
    "banda": "बंदा", "bande": "बंदे", "bandi": "बंदी",
    "padhta": "पढ़ता", "padhti": "पढ़ती", "padhte": "पढ़ते",
    "rakho": "रखो", "rakhe": "रखे", "rakhe_": "रखें", "rakhi": "रखी",
    "ro": "रो", "roya": "रोया", "roti": "रोती", "rota": "रोता", "rona": "रोना",
    "sach": "सच", "jhooth": "झूठ",
    "sachai": "सच्चाई", "jhuthi": "झूठी",
    "tarika": "तरीक़ा", "tarike": "तरीक़े",
    "toota": "टूटा", "tooti": "टूटी", "tootna": "टूटना",
    "phenkna": "फेंकना", "phenka": "फेंका", "phenk": "फेंक",
    # Cycle 10 final additions
    "badhenge": "बढ़ेंगे", "ghatenge": "घटेंगे",
    "galat": "गलत", "sahihe": "सहीहै",  # गलत without nukta — ElevenLabs reads "ग़" as a non-Hindi consonant ("ghalat" with kh sound); modern Hindi spelling is plain "ग"
    "jeetega": "जीतेगा", "harega": "हारेगा", "jeetegi": "जीतेगी",
    "kitna": "कितना", "kitni": "कितनी", "kitne": "कितने",
    "pucha": "पूछा", "puchhi": "पूछी", "puchhna": "पूछना", "puch": "पूछ",
    "doge": "दोगे", "dogi": "दोगी", "loge": "लोगे", "logi": "लोगी",
    # Common pairs
    "kab_": "कब", "tab_": "तब",
    "kahin": "कहीं", "wahaan": "वहाँ",
    "shayad": "शायद", "zaroori_": "ज़रूरी",
    "pakka": "पक्का", "kachha": "कच्चा",
    "saaf": "साफ़", "ganda": "गंदा",
    "jaldi": "जल्दी", "der": "देर",
    "naam": "नाम", "kaam_": "काम",  # already mapped
    # Spot-check feedback (post-cycle 10): user identified these three
    "kismat": "किस्मत", "kismet": "किस्मत", "kismata": "किस्मत",
    "naseeb": "नसीब", "bhagya": "भाग्य",
    "rishtey": "रिश्ते",  # alt of rishte (already mapped)
    "banane": "बनाने", "banao_": "बनाओ",  # banao already mapped; banane is gerund
    # Bonus inflections of "banana" (to make)
    "banaiye": "बनाइए", "banaayi": "बनाई", "banwaiye": "बनवाइए",
    # Other common gerunds I might have missed
    "karne_": "करने", "dekhna_": "देखना",  # already mapped; placeholders
    "khareedna": "ख़रीदना", "bechna_": "बेचना",  # bechna already
    "padhane": "पढ़ाने", "padhana": "पढ़ाना",
    "samjhana": "समझाना",
    # Exhaustive scan of 133 production topics — Hindi words I missed before
    "patla": "पतला", "patli": "पतली", "patle": "पतले",
    "mota": "मोटा", "moti": "मोटी", "mote": "मोटे",
    "cheezein": "चीज़ें", "cheezon": "चीज़ों",
    "farq": "फ़र्क़", "farak": "फ़र्क़",
    "na": "ना",
    "padta": "पड़ता", "padti_": "पड़ती",  # already mapped
    "aasmaan": "आसमान", "aasman": "आसमान",
    "aayega": "आएगा", "aayegi": "आएगी", "aayenge": "आएँगे",
    "bachega": "बचेगा", "bachegi": "बचेगी",
    "badhti": "बढ़ती",
    "banana": "बनाना",  # Hindi "to make/build". Fruit banana never appears in our textile domain.
    "becho": "बेचो",
    "dene": "देने",
    "dikkat": "दिक्कत",
    "dikta": "दिखता",  # alt of dikhta
    "galtiyan": "ग़लतियाँ", "galtiyon": "ग़लतियों",
    "kariyega": "करियेगा", "kariye": "करिये",
    "karwa": "करवा",
    "kheechta": "खींचता",
    "niche": "नीचे",  # alt of neeche
    "rehta": "रहता", "rehti": "रहती", "rehte": "रहते",
    "rukega": "रुकेगा", "rukegi": "रुकेगी",
    "saste": "सस्ते",
    "shuruat": "शुरुआत",  # alt of shuruwat (already mapped)
    "sochte": "सोचते",
    "tikta": "टिकता", "tikti": "टिकती", "tikne": "टिकने",
    "zameen": "ज़मीन",
    "zaroorat": "ज़रूरत",
    # Critical: "banana" the gerund is a name collision with English fruit.
    # Only safe to map in clearly-Hindi context. Map the *_kar_* forms instead:
    "banane_": "बनाने",  # already mapped
    # Plurals + extras
    "saalon": "सालों", "dinon": "दिनों", "logon_": "लोगों",
    "baatein": "बातें", "baaton": "बातों", "baat_": "बात",
    "din_": "दिन",
    # More inflections of common verbs
    "rakhna_": "रखना", "rakhne": "रखने",  # rakhna already
    "lagti_": "लगती", "lagne": "लगने",  # lagti already
    "milte": "मिलते", "milti_": "मिलती",  # milta/i/e
    "uthte": "उठते", "uthti": "उठती",
    # Full-script QA findings — Claude-generated production scripts have
    # MUCH richer vocabulary than topic headlines.
    # Pronouns + determiners
    "usko": "उसको", "isko": "इसको", "tumko": "तुमको", "humko": "हमको", "mujhko": "मुझको",
    "voh": "वह", "wohi": "वही", "yahi": "यही",
    "humara": "हमारा",  # alt of hamara
    "tumhare": "तुम्हारे",  # also covered above
    # Quantity / comparison
    "itna": "इतना", "itni": "इतनी", "itne": "इतने",
    "utna": "उतना", "utni": "उतनी", "utne": "उतने",
    "jitna": "जितना", "jitni": "जितनी", "jitne": "जितने",
    "kitna_": "कितना",  # already mapped
    "baaki": "बाक़ी", "bohot": "बहुत",  # alt of bahut
    "zara": "ज़रा", "thoda_2": "थोड़ा",  # already mapped
    # Verbs (lots of inflections from Claude scripts)
    "kaha": "कहा", "kahta": "कहता", "kahti": "कहती", "kahna": "कहना", "kahein": "कहें", "kahein_": "कहें",
    "samjhaya": "समझाया",  # alt of samjhaaya
    "ban": "बन", "banna": "बनना", "bante": "बनते", "bani_": "बनी",  # already
    "dikhaya": "दिखाया", "dikhe": "दिखे",
    "chahe": "चाहे", "chahte": "चाहते", "chahti": "चाहती",
    "chalta": "चलता", "chalte": "चलते",
    "de": "दे", "le_": "ले",  # already
    "jahan": "जहाँ",
    "jayenge": "जाएँगे", "jayengi": "जाएँगी", "jayega_": "जाएगा",  # already as jaayega
    "mil": "मिल", "milne_": "मिलने",  # already as milne
    "payega": "पाएगा", "payegi": "पाएगी", "payenge": "पाएँगे",
    "ruko": "रुको", "ruke": "रुके",
    "sabko": "सबको", "sabse_": "सबसे",  # already mapped
    "yaad": "याद", "yaadein": "यादें",
    # 1-occurrence Hindi from full scripts
    "achi": "अच्छी",
    "ankhein": "आँखें", "ankh": "आँख", "aankh": "आँख",
    "atka": "अटका", "atki": "अटकी", "atke": "अटके",
    "badhaao": "बढ़ाओ", "ghatao": "घटाओ",
    "balki": "बल्कि",
    "batata": "बताता", "batati": "बताती", "bataate": "बताते",
    "batau": "बताऊँ", "bataya": "बताया",
    "bech": "बेच", "becho_": "बेचो",  # already
    "bheji": "भेजी",
    "bhago": "भागो", "bhagna": "भागना",
    "bistar": "बिस्तर",
    "chaadar": "चादर",
    "chale": "चले",
    "dhundho": "ढूँढो", "dhundha": "ढूँढ़ा",
    "dhyan": "ध्यान",
    "dikh": "दिख", "dikhna": "दिखना",
    "dunga": "दूँगा", "dungi": "दूँगी",
    "jaao": "जाओ",
    "jabhi": "जभी", "jabki": "जबकि",
    "jagah": "जगह",
    "jayegi": "जाएगी",  # alt
    "jismein": "जिसमें", "jisme": "जिसमें",
    "kamse": "कमसे", "kamsekam": "कमसेकम",
    "karaoge": "कराओगे", "karaya": "कराया",
    "karoge": "करोगे", "karogi": "करोगी",
    "karwani": "करवानी", "karwaya": "करवाया", "karwati": "करवाती", "karwate": "करवाते",
    "khel": "खेल",
    "khich": "खींच",
    "lagein": "लगें", "lagenge": "लगेंगे",
    "lekar": "लेकर", "dekar": "देकर",
    "lelo": "ले लो", "lelena": "ले लेना",
    "manga": "मँगा", "mangwa": "मँगवा",
    "niklenge": "निकलेंगे", "niklega_": "निकलेगा",  # already
    "pad": "पड़",
    "pehnega": "पहनेगा", "pehnegi": "पहनेगी", "pehne": "पहने", "pehna": "पहना", "pehni": "पहनी",
    "pehnke": "पहनके", "pehanke": "पहनके", "pehnkar": "पहनकर", "pehankar": "पहनकर",
    "pehnna": "पहनना", "pehnenge": "पहनेंगे", "pehnegi_": "पहनेगी",
    "raho": "रहो",
    "rahta": "रहता",  # alt of rehta
    "rupay": "रुपये", "rupees": "रुपये",  # add explicit handling of "rupees" English word
    "sambhalo": "सँभालो", "sambhal": "सँभाल",
    "socha": "सोचा",
    "tabhi": "तभी",
    "teesra": "तीसरा", "teesri": "तीसरी",
    "turant": "तुरंत",
    "ulta": "उल्टा", "ulti": "उल्टी",
    "yani": "यानी",
    "zarurat": "ज़रूरत",  # alt of zaroorat
    # Last gaps caught by full-script QA
    "tumne": "तुमने", "humne_": "हमने",  # already mapped
    "wahi": "वही",
    "teen_word": "तीन",  # spelled-out "teen" (3); numerals already converted via regex
    "bik": "बिक", "bikk": "बिक",  # alt of bika/bikega root
    # Spelled-out Hindi numbers (Claude sometimes writes "teen" instead of "3"):
    "ek_word": "एक",  # ek already mapped via "ek": "एक"
    "do_word": "दो",  # avoid English "do"; "do": "दो" already exists in main map
    "char_word": "चार", "chaar": "चार",
    "paanch_word": "पाँच", "panch": "पाँच",
    "chah": "छह", "chha": "छह",  # 6
    "saat_word": "सात",  # saat = 7, but "saat" looks like English "sat" — risky
    "aath_word": "आठ", "aath": "आठ",
    "nau_word": "नौ", "nau": "नौ",
    "das_word": "दस", "das": "दस",
}

# 'teen' is the romanization of तीन (3) but collides with English "teen"
# (teenager). In our textile B2B context, English "teen" essentially never
# appears, while "teen mahine" / "teen saal" / "teen piece" are extremely
# common. Map cautiously.
_TTS_HINGLISH_DEVANAGARI["teen"] = "तीन"

# Full-script QA round 2 — 164 more candidates from 20 fresh production scripts.
_TTS_HINGLISH_DEVANAGARI.update({
    # High-freq Hindi from round 2
    "wo": "वो", "poocha": "पूछा",
    "beech": "बीच",
    "jati": "जाती", "jate": "जाते",
    "udhar": "उधर", "idhar": "इधर",
    "usmein": "उसमें", "ismein": "इसमें",
    "uss": "उस",
    # Verbs
    "achha": "अच्छा", "achhi": "अच्छी",
    "bacha": "बचा", "bacho": "बचो", "bachna": "बचना",
    "banenge": "बनेंगे", "banegi": "बनेगी", "banega": "बनेगा",
    "barbaad": "बर्बाद",
    "bechke": "बेचके", "bechoge": "बेचोगे",
    "bekaar": "बेकार",
    "bhejo": "भेजो",
    "bolunga": "बोलूँगा", "bolungi": "बोलूँगी",
    "chahta": "चाहता",
    "chhapwaega": "छपवाएगा", "chhapwana": "छपवाना",
    "chote": "छोटे",
    "chuki": "चुकी", "chuka": "चुका",
    "dedo": "दे दो", "lelo_": "ले लो",
    "dhundhne": "ढूँढ़ने",
    "dhyaan": "ध्यान",
    "dikhte": "दिखते",
    "doob": "डूब", "dooba": "डूबा",
    "dum": "दम",
    "dusra": "दूसरा", "dusri": "दूसरी", "dusre": "दूसरे",
    "gaddein": "गद्दे",
    "hafto": "हफ़्तों", "haftein": "हफ़्तें",
    "halka": "हल्का", "halki": "हल्की", "halke": "हल्के",
    "hisaab": "हिसाब",
    "jaaoge": "जाओगे",
    "kaafi": "काफ़ी",
    "kaat": "काट", "kaata": "काटा",
    "karwaana": "करवाना", "karwaaya": "करवाया",
    "kisi": "किसी",
    "leli": "ले ली", "liya_": "लिया",  # already
    "lunga": "लूँगा", "lungi": "लूँगी",
    "maana": "माना",
    "maang": "माँग", "maange": "माँगे",
    "naraz": "नाराज़",
    "pachhtaoge": "पछताओगे", "pachhtana": "पछताना",
    "pakad": "पकड़", "pakda": "पकड़ा",
    "poochho": "पूछो",
    "pura": "पूरा",
    "rukja": "रुक जा", "rukjao": "रुक जाओ",
    "rupee": "रुपये",  # singular form
    "samjh": "समझ",
    "sapna": "सपना", "sapne": "सपने",
    "savaal": "सवाल", "javaab": "जवाब", "jawaab": "जवाब",
    "sikho": "सीखो",
    "taaki": "ताकि",
    "tera": "तेरा", "teri": "तेरी", "tere": "तेरे",
    "waisa": "वैसा", "waisi": "वैसी", "waise": "वैसे",
    "waqt": "वक़्त",
    "lakhs": "लाख",  # plural alt of lakh
    "mehenga": "महँगा",  # alt of mehnga
    # Round 3 — alt spellings + long-tail Hindi
    "pichle": "पिछले",  # alt of pichhle
    "yehi": "यही",  # alt of yahi
    "jao": "जाओ",  # alt of jaao
    "pahunch": "पहुँच", "pahuncha": "पहुँचा", "pahunchi": "पहुँची",
    "seekh": "सीख", "sikha_": "सीखा",  # already mapped
    "toot": "टूट", "toot_": "टूटा",  # already mapped
    "aage": "आगे", "peeche": "पीछे",
    "asar": "असर", "prabhav": "प्रभाव",
    "bach": "बच", "bachane": "बचाने", "bachani": "बचानी",
    "baithe": "बैठे", "baitha": "बैठा", "baithi": "बैठी", "baith": "बैठ",
    "bataunga": "बताऊँगा", "bataungi": "बताऊँगी",
    "bees": "बीस",  # 20 word form
    "bhejenge": "भेजेंगे",
    "chaiye": "चाहिए",  # alt of chahiye
    "chakkar": "चक्कर",
    "chuna": "चुना", "chunna": "चुनना", "chunte": "चुनते",
    "daalo": "डालो",
    "dhang": "ढंग",
    "dikhai": "दिखाई", "dikhega": "दिखेगा", "dikhegi": "दिखेगी",
    "doobega": "डूबेगा",
    "hokar": "होकर", "lekar_": "लेकर",  # already
    "isiliye": "इसीलिए",
    "jhanjhat": "झंझट", "jhamela": "झमेला",
    "jhuk": "झुक", "jhukna": "झुकना",
    "jyada": "ज़्यादा",  # alt of zyada
    "kamaao": "कमाओ", "kamaayi": "कमाई",
    "khulne": "खुलने", "khula": "खुला", "khuli": "खुली",
    "lagaya": "लगाया", "lagai": "लगाई", "lagaye": "लगाए",
    "lagana": "लगाना", "lagaane": "लगाने", "lagaan": "लगान",
    "lagaayega": "लगाएगा", "lagaayegi": "लगाएगी",
    "lene": "लेने",
    "loon": "लूँ", "len": "लें",  # alt of lein
    "mangaoge": "मँगाओगे", "mangao_": "मँगाओ",  # already
    "mili": "मिली",
    "nuksan": "नुकसान",  # alt of nuksaan
    "pade": "पड़े", "padi_": "पड़ी",  # already
    "pooche": "पूछे",
    "rakhni": "रखनी",
    "ruk": "रुक",
    "samajhta": "समझता",  # alt of samjhta
    "taki": "ताकि",  # alt of taaki
    "tumse": "तुमसे", "humse_": "हमसे",  # already
    "unhone": "उन्होंने", "humne": "हमने", "tumne_": "तुमने",  # already
    "unka": "उनका", "unki": "उनकी", "unke": "उनके",
    "vapas": "वापस",  # alt of wapas
    "yahaan": "यहाँ",  # alt of yahan
    "zor": "ज़ोर",
    "fas": "फँस", "phans": "फँस",
    # Round 4 long-tail Hindi
    "jaake": "जाके",
    "badhiya": "बढ़िया",
    "aani": "आनी", "aayengi": "आएँगी",
    "acha": "अच्छा",  # alt
    "atkega": "अटकेगा",
    "bataata": "बताता",  # alt
    "bheje": "भेजे",
    "chipakta": "चिपकता", "chipakna": "चिपकना",
    "dalo": "डालो",  # alt of daalo
    "dhoop": "धूप", "chhaaon": "छाँव",
    "dikhne": "दिखने",
    "dubara": "दुबारा", "dobara": "दोबारा",
    "hoke": "होके",
    "humari": "हमारी",
    "konsa": "कौनसा",  # alt of kaunsa
    "maan": "मान",
    "maar": "मार", "mariyega": "मारियेगा",
    "mangwani": "मँगवानी",
    "mast": "मस्त",
    "nikalne": "निकलने", "nikalti": "निकलती",
    "pakke": "पक्के",  # already pakka
    "rakhoge": "रखोगे",
    "rakkho": "रखो",  # alt
    "rehne": "रहने",
    "sookne": "सूखने", "sukha": "सूखा",
    "tarah": "तरह", "tarahein": "तरहें",
    "tikkega": "टिकेगा",  # alt of tikta
    "uspe": "उसपे",
    # Round 5 additions
    "waali": "वाली", "waala": "वाला",
    "dosto": "दोस्तो",
    "maangta": "माँगता", "maangti": "माँगती",
    "ayegi": "आएगी",
    "badho": "बढ़ो",
    "bane": "बने",
    "banwa": "बनवा",
    "bhoolna": "भूलना", "bhool": "भूल", "bhoolega": "भूलेगा",
    "bhot": "बहुत",  # alt of bahut
    "bigad": "बिगड़", "bigda": "बिगड़ा", "bigdi": "बिगड़ी",
    "bol": "बोल", "boli_": "बोली",  # already mapped
    "chalo": "चलो",
    "dekhiye": "देखिये",
    "dekhta": "देखता",
    "dhila": "ढीला", "dhili": "ढीली",
    "dibbe": "डिब्बे", "dibba": "डिब्बा",
    "dobaara": "दुबारा",  # alt
    "fasoge": "फँसोगे", "fasega": "फँसेगा",
    "gusse": "ग़ुस्से", "gussa": "ग़ुस्सा",
    "hazaaron": "हज़ारों",
    "humaare": "हमारे",  # alt
    "jaaenge": "जाएँगे",
    "jor": "ज़ोर",  # alt of zor
    "kaatna": "काटना", "kaato": "काटो",
    "kaisa": "कैसा", "kaisi": "कैसी",
    "karu": "करूँ",
    "kehte": "कहते", "kehta": "कहता",  # alt
    "lagake": "लगाके",
    "nikalni": "निकालनी",
    "paanch": "पाँच",  # word form of 5
    "pahunchega": "पहुँचेगा", "pahunchegi": "पहुँचेगी",
    "rakhke": "रखके",
    "rakhta": "रखता",
    "reh": "रह", "rehna": "रहना",
    "rok": "रोक", "rokna": "रोकना",
    "rukh": "रुख़",
    "saala": "साला",
    "saamne": "सामने",
    "samajhke": "समझके", "samjhke": "समझके",
    "samjhata": "समझाता",
    "tabse": "तबसे", "jabse": "जबसे",
    "teeno": "तीनों",
    # Round 6 long-tail
    "choti": "छोटी",  # alt of chhoti
    "rakh": "रख",
    "jaoge": "जाओगे", "jaogi": "जाओगी",
    "lu": "लूँ", "loon_": "लूँ",
    "phas": "फँस",
    "banani": "बनानी",
    "bolun": "बोलूँ",
    "chalake": "चलाके",
    "chota": "छोटा",  # alt
    "chaubees": "चौबीस",  # 24 word form
    "dalke": "डालके",
    "doston": "दोस्तों",
    "guna": "गुना",
    "hogaye": "हो गए", "hogayi": "हो गई",
    "hume": "हमें",
    "inka": "इनका", "inki": "इनकी", "inke": "इनके",
    # Today's production-feedback fixes (post-launch)
    # User caught these in the published Instagram Reel.
    "utar": "उतर", "utri": "उतरी", "utra": "उतरा",
    "utarna": "उतरना", "utarne": "उतरने", "utrega": "उतरेगा",
    "utri_": "उतरी", "utre": "उतरे",
    "chhap": "छप", "chhapna": "छपना", "chhapa": "छपा", "chhapi": "छपी",
    "lagao_2": "लगाओ",  # already mapped
    "wash_": "वॉश",  # leave English for "wash"
    # Common verbs around "uthna/utarna" family
    "uthwana": "उठवाना", "uthwa": "उठवा",
    "girna": "गिरना", "gira": "गिरा", "girti": "गिरती",
    "ghisna": "घिसना", "ghisa": "घिसा",
    "phatna": "फटना", "phata": "फटा", "phati": "फटी",
    # 2026-05-28 Ketu feedback on published Reel:
    "chipakti": "चिपकती", "chipakte": "चिपकते", "chipakte_": "चिपकते",
    "hissa": "हिस्सा", "hisse": "हिस्से", "hisson": "हिस्सों",
    "sukhao": "सुखाओ", "sukhaao": "सुखाओ", "sukhaav": "सुखाओ",
    "sukhana": "सुखाना", "sukhane": "सुखाने", "sukhaye": "सुखाए",
})

# 'hi' as a particle (emphatic "ही") collides with English greeting "Hi" in
# theory — in our scripts it's always Hindi. Conditional add.
_TTS_HINGLISH_DEVANAGARI["hi"] = "ही"

# ── Ketu-reported mispronunciations, 2026-07-10 ──
# "bechenge" was read as "bechenche" (Tag-missing video, ~7s). The future/
# plural sell-forms were missing. (bechna/becha/bechi/bechte/bechen/becho/
# bech/bechke/bechoge are already mapped above.)
_TTS_HINGLISH_DEVANAGARI.update({
    "bechenge": "बेचेंगे", "becheinge": "बेचेंगे",
    "bechega": "बेचेगा", "bechegi": "बेचेगी",
    "bechunga": "बेचूँगा", "bechungi": "बेचूँगी",
})
# "do" (दो = two) was read as English "do" (300-tshirt video, ~22s). The old
# comment near the number-word map claiming '"do": "दो" already exists' was
# WRONG — it never existed. Homograph with English "do", but in our Hindi-
# dominant B2B scripts standalone "do" is ~always दो (same call we made for
# "teen"→तीन and "hi"→ही). Whole-word only, so "download"/"double" are safe.
_TTS_HINGLISH_DEVANAGARI["do"] = "दो"

# ── Ketu-reported mispronunciations, 2026-07-11 (240 GSM video) ──
# None of these were mapped, and none appear in his 52 transcribed videos
# (corpus count 0) — so the learner couldn't cover them. ElevenLabs guessed:
# "badal" → "baadal" (बादल, cloud), "adhi" → "adi" (aspiration dropped).
_TTS_HINGLISH_DEVANAGARI.update({
    # बदल (change) family — in B2B scripts "badal" is ~never बादल (cloud)
    "badal": "बदल", "badla": "बदला", "badli": "बदली", "badle": "बदले",
    "badalke": "बदलके", "badalna": "बदलना", "badalta": "बदलता",
    "badalti": "बदलती", "badlega": "बदलेगा", "badlegi": "बदलेगी",
    "baadal": "बादल",  # actual cloud, if a monsoon script ever writes it
    # आधा (half) family — "adhi" was read "adi"
    "aadha": "आधा", "adha": "आधा", "aadhi": "आधी", "adhi": "आधी",
    "aadhe": "आधे", "adhe": "आधे",
    # बचाके (having saved) — distinct from बचके (dodge); both now explicit
    "bachake": "बचाके", "bachaake": "बचाके", "bachakar": "बचाकर",
    "bachke": "बचके",
})
# ── Ketu-reported mispronunciations, 2026-07-13 (₹80 vs ₹140 video) ──
# combed/laambe/band all wrong. None in his corpus (count 0). "band" was read
# as English "band" (music), "laambe" as English, and "combed" as garbage —
# the old "kohmd" Latin respelling broke when production moved to eleven_v3.
_TTS_HINGLISH_DEVANAGARI.update({
    # combed cotton → phonetic Devanagari (b is SILENT: /koʊmd/, not "komb-d").
    # v3 reads Devanagari graphemes deterministically; verify via audio_sample.
    "combed": "कोम्ड",
    # लंबा (long) family — "laambe"/"lambe" were read as English.
    "lamba": "लंबा", "lambi": "लंबी", "lambe": "लंबे", "lambey": "लंबे",
    "laamba": "लंबा", "laambi": "लंबी", "laambe": "लंबे", "laambey": "लंबे",
    "lambai": "लंबाई", "lambaai": "लंबाई", "lambaayi": "लंबाई",
    # बंद (closed) — "aankhein band" read as English "band". Homograph with
    # English band/rubber-band, but in his B2B scripts standalone "band" is
    # ~always बंद (dukaan band, aankhein band, band karo) — same call as
    # do/teen/hi. Whole-word only, so "waistband"/"bandwidth" are safe.
    "band": "बंद", "bund": "बंद",
    # आँखें (eyes) — ensure the "aankhe" spelling he uses is covered too
    "aankhe": "आँखें", "aankhein": "आँखें", "ankhe": "आँखें",
})


def normalize_for_tts(text: str) -> str:
    """Normalize a Hinglish script for cleaner TTS output.

    Fixes the issues we keep hearing in production:
      ₹140  → ek so chalis rupaye
      ₹10,000 → das hazaar rupaye
      DTG / DTF / GSM → spelled-out phonetic
      Strips '...' that TTS engines read as long silence.
    """
    import re as _re
    if not text:
        return text

    s = text

    # Multipliers spoken in Hindi
    _MULT = {"k": 1_000, "thousand": 1_000, "hazaar": 1_000, "hazar": 1_000,
             "l": 100_000, "lakh": 100_000, "lac": 100_000, "lacs": 100_000, "lakhs": 100_000,
             "cr": 10_000_000, "crore": 10_000_000, "crores": 10_000_000}

    _MULT_WORDS = {1_000: "हज़ार", 100_000: "लाख", 10_000_000: "करोड़"}

    # Natural Hindi fractions — much more idiomatic than "दशमलव" (which sounds
    # like a textbook). User explicitly said: "I never say दशमलव. I say
    # डेढ़ लाख रुपये / ढाई लाख रुपये." — so use these natural forms.
    _HINDI_HALF_WORDS = {
        # X.5 forms
        (0, "5"): "आधा",            # 0.5
        (1, "5"): "डेढ़",            # 1.5
        (2, "5"): "ढाई",             # 2.5
    }

    def _natural_decimal(int_n: int, dec_part: str) -> str | None:
        """Return natural Hindi fraction for (int, decimal) pair or None.
        Handles: 0.5/1.5/2.5 specially; X.5 for X>=3 → 'साढ़े X'; X.25 → 'सवा X';
        X.75 → 'पौने (X+1)'. Returns None for unsupported fractions (caller falls
        back to digit-by-digit OR keeps Latin)."""
        if not dec_part:
            return None
        # Normalize: trailing zeros (1.50 → 1.5)
        dec_clean = dec_part.rstrip("0") or "0"
        key = (int_n, dec_clean)
        if key in _HINDI_HALF_WORDS:
            return _HINDI_HALF_WORDS[key]
        if dec_clean == "5" and int_n >= 3:
            # 3.5 → साढ़े तीन, 4.5 → साढ़े चार ...
            return f"साढ़े {_hindi_number(int_n)}"
        if dec_clean == "25":
            # 1.25 → सवा एक, 2.25 → सवा दो (less common but supported)
            return f"सवा {_hindi_number(int_n)}"
        if dec_clean == "75":
            # 1.75 → पौने दो, 2.75 → पौने तीन
            return f"पौने {_hindi_number(int_n + 1)}"
        # SAFETY NET: awkward decimal like 1.2 / 1.3 / 1.7 / 3.4 — these are
        # banned in the script-gen prompt but if Claude slips up, round to
        # nearest natural form rather than reading "एक दशमलव दो" / "1.2".
        try:
            d_int = int(dec_clean)
        except ValueError:
            return None
        # 0.X with X<=2 → आधा (round down), X>=3 → next integer
        # X.0 → just whole number (handled at caller, but safe here)
        # X.<5 → just whole X (round down)
        # X.>5 → साढ़े X / next half (round to nearest half)
        if d_int == 0:
            return _hindi_number(int_n)
        # Round to nearest .0 or .5
        # 1.1, 1.2 → 1 (round down to whole)
        # 1.3, 1.4 → 1.5 (डेढ़)
        # 1.6, 1.7 → 1.5 (round down to half)
        # 1.8, 1.9 → 2 (round up to next whole)
        # Decimal as fraction (e.g. "2" = .2, "23" = .23)
        decimal_value = float(f"0.{dec_clean}")
        # Round to nearest 0.5 step
        nearest_half = round(decimal_value * 2) / 2
        new_int = int_n + int(nearest_half)
        new_frac = nearest_half - int(nearest_half)
        if new_frac == 0:
            return _hindi_number(new_int)
        # nearest_half ended in .5
        if new_int == 0:
            return "आधा"
        if new_int == 1:
            return "डेढ़"
        if new_int == 2:
            return "ढाई"
        return f"साढ़े {_hindi_number(new_int)}"

    def _rupee_repl(m):
        int_part = m.group(1).replace(",", "")
        dec_part = m.group(2)  # may be None
        suffix = (m.group(3) or "").strip().lower()
        try:
            int_n = int(int_part)
        except ValueError:
            return m.group(0)
        if suffix in _MULT:
            mult_word = _MULT_WORDS[_MULT[suffix]]
            if dec_part:
                # Try natural Hindi fraction first (डेढ़/ढाई/साढ़े/सवा/पौने)
                natural = _natural_decimal(int_n, dec_part)
                if natural:
                    return f"{natural} {mult_word} रुपये"
                # Fallback: keep as Latin "X.Y" digits — ElevenLabs reads
                # "1.5 लाख" as "one-point-five लाख" which sounds more natural
                # than "एक दशमलव पाँच लाख".
                return f"{int_part}.{dec_part} {mult_word} रुपये"
            return f"{_hindi_number(int_n)} {mult_word} रुपये"
        # No multiplier (e.g. ₹49.50)
        if dec_part:
            natural = _natural_decimal(int_n, dec_part)
            if natural:
                return f"{natural} रुपये"
            return f"{int_part}.{dec_part} रुपये"
        return f"{_hindi_number(int_n)} रुपये"

    # ₹140 / ₹ 1,000 / ₹2 lakh / ₹50K / ₹2.5 crore / Rs.140 / Rs 10,000 / Rs 2L
    # Now also captures optional decimal part (₹2.5 crore).
    suffix_re = r"(K|L|Cr|thousand|hazaar|hazar|lakh|lac|lacs|lakhs|crore|crores)"
    rupee_pat = rf"₹\s*([\d,]+)(?:\.(\d+))?(?:\s*{suffix_re})?\b"
    s = _re.sub(rupee_pat, _rupee_repl, s, flags=_re.IGNORECASE)
    rs_pat = rf"\bRs\.?\s*([\d,]+)(?:\.(\d+))?(?:\s*{suffix_re})?\b"
    s = _re.sub(rs_pat, _rupee_repl, s, flags=_re.IGNORECASE)

    # Bare "2 lakh" / "1.5 lakh" / "50K" / "2 crore" without ₹ prefix — same
    # natural Hindi treatment as ₹X.Y suffix above.
    def _bare_mult_repl(m):
        int_part = m.group(1).replace(",", "")
        dec_part = m.group(2)  # may be None
        suffix = m.group(3).strip().lower()
        try:
            int_n = int(int_part)
            if suffix not in _MULT:
                return m.group(0)
        except ValueError:
            return m.group(0)
        mult_word = _MULT_WORDS[_MULT[suffix]]
        if dec_part:
            natural = _natural_decimal(int_n, dec_part)
            if natural:
                return f"{natural} {mult_word}"
            return f"{int_part}.{dec_part} {mult_word}"
        return f"{_hindi_number(int_n)} {mult_word}"
    bare_mult_pat = r"\b(\d{1,4})(?:\.(\d+))?\s*(K|L|Cr|thousand|hazaar|hazar|lakh|lac|lacs|lakhs|crore|crores)\b"
    s = _re.sub(bare_mult_pat, _bare_mult_repl, s, flags=_re.IGNORECASE)

    # Standalone numbers — convert numbers ≥10 to Hinglish ("80" → "assi", "200" → "do sau").
    # Single digits (1-9) stay as digits — Hinglish code-mixers naturally say
    # "8 piece" not "aath piece"; ElevenLabs multilingual reads them contextually.
    def _bignum_repl(m):
        digits = m.group(0).replace(",", "")
        try:
            n = int(digits)
        except ValueError:
            return m.group(0)
        if n < 10:
            return m.group(0)
        return _hindi_number(n)

    # Match standalone integer (1-7 digits, with optional commas)
    s = _re.sub(r"\b\d[\d,]{0,7}\b", _bignum_repl, s)

    # Acronyms — replace whole-word, case-insensitive, keep surrounding text
    for acr, phon in _TTS_ACRONYM_MAP.items():
        s = _re.sub(rf"\b{acr}\b", phon, s, flags=_re.IGNORECASE)

    # Phonetic respelling of common Hinglish words that ElevenLabs mispronounces.
    # Whole-word, case-insensitive replacement that preserves the intent of the script.
    for word, phon in _TTS_PHONETIC_MAP.items():
        s = _re.sub(rf"\b{word}\b", phon, s, flags=_re.IGNORECASE)

    # Hinglish romanized → Devanagari for high-frequency Hindi words.
    # Whole-word, case-insensitive. English-only nouns are not in the map.
    for word, deva in _TTS_HINGLISH_DEVANAGARI.items():
        s = _re.sub(rf"\b{word}\b", deva, s, flags=_re.IGNORECASE)

    # Hindi past-tense "the" (थे) vs English article "the": थे ends a clause
    # ("hum saath the." / "kam the,"), the article always precedes a word —
    # convert only when punctuation or end-of-text follows.
    s = _re.sub(r'\bthe\b(?=\s*(?:[,.!?;:।…"”’\']|$))', "थे", s, flags=_re.IGNORECASE)

    # "₹15 bachke" is a recurring grammar slip — after a money amount the
    # correct form is बचाके (saving it), not बचके (dodging). Fix it after the
    # maps have run (both roman and Devanagari money words covered).
    # NB: no trailing \b — Python \b treats matras (े) as non-word chars, so
    # \b after Devanagari never matches; explicit lookahead instead.
    s = _re.sub(r"((?:रुपये|रुपया|रुपिया|पैसे|पैसा|लाख|हज़ार|करोड़|"
                r"rupay\w*|rupiya|paisa|paise)\s+)बचके(?=[\s,.!?।…\"']|$)",
                r"\1बचाके", s, flags=_re.IGNORECASE)

    # Auto-learned pronunciations from Ketu's own channel corpus (see
    # build_voice_models). Hand-curated always wins: it ran first, and keys
    # present in _TTS_HINGLISH_DEVANAGARI are skipped as a second guard.
    # Lambda replacement so re.sub never parses the value as a template.
    for word, deva in _get_learned_pronunciations().items():
        if word not in _TTS_HINGLISH_DEVANAGARI:
            s = _re.sub(rf"\b{_re.escape(word)}\b", lambda m, d=deva: d, s,
                        flags=_re.IGNORECASE)

    # Preserve MID-SCRIPT ellipses — ElevenLabs reads "..." as a natural
    # thinking pause, good prosody between sentences. (The very LAST words are
    # different — see FINAL-SENTENCE FINALITY below.)
    s = s.replace("…", "...")  # normalize unicode ellipsis to three dots
    s = _re.sub(r",\s*,", ",", s)
    # Pre-quote pause: speech verb + comma + opening quote reads too fast on
    # ElevenLabs. Em-dash gives a noticeably longer pause before the quote.
    s = _re.sub(
        r'\b(bolo|bola|boli|kaha|kehna|kehte|kehti|bata|batao|batata|batati|samjhao|samjhaya|pucha|poocha)\s*,\s*(?=["“])',
        r'\1 — ',
        s,
        flags=_re.IGNORECASE,
    )
    # FINAL-SENTENCE FINALITY: eleven_v3 reads a trailing "..." as a fade-out.
    # Fine mid-script, wrong on the very last words — force the closer to a
    # hard stop. Handles dots around ?/!/danda ("ho?...", "...!") and a
    # closing quote after the ellipsis (with or without a space before it).
    s = s.rstrip()
    s = _re.sub(r'([?!।])\s*\.{2,}(["”’\']*)$', r'\1\2', s)
    s = _re.sub(r'\.{2,}\s*([।!?]["”’\']*)$', r'\1', s)
    s = _re.sub(r'\.{2,}\s*(["”’\']*)$', r'.\1', s)
    s = _re.sub(r"\s+", " ", s).strip()
    return s


def sarvam_tts_to_mp3(text: str, api_key: str, output_mp3_path: str) -> str:
    """Generate Sarvam TTS audio and write to MP3 (chunks long text automatically).

    Returns the MP3 path on success, raises on failure.
    """
    import requests as _requests
    import base64 as _b64
    import subprocess as _sp

    # Sarvam accepts up to ~1500 chars per request — chunk on sentence boundaries
    chunks: list[str] = []
    buf = ""
    for sentence in text.replace("।", ".").split("."):
        sentence = sentence.strip()
        if not sentence:
            continue
        sentence = sentence + "."
        if len(buf) + len(sentence) + 1 > 1400:
            if buf:
                chunks.append(buf)
            buf = sentence
        else:
            buf = (buf + " " + sentence).strip() if buf else sentence
    if buf:
        chunks.append(buf)
    if not chunks:
        chunks = [text]

    raw_paths: list[str] = []
    for i, chunk in enumerate(chunks):
        resp = _requests.post(
            "https://api.sarvam.ai/text-to-speech",
            headers={
                "api-subscription-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "text": chunk,
                "target_language_code": SARVAM_TARGET_LANG,
                "speaker": SARVAM_SPEAKER,
                "model": SARVAM_MODEL,
                "speech_sample_rate": SARVAM_SAMPLE_RATE,
                "pace": SARVAM_PACE,
                "temperature": SARVAM_TEMPERATURE,
            },
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        audios = data.get("audios") or []
        if not audios:
            raise RuntimeError(f"Sarvam returned no audio (chunk {i+1}/{len(chunks)})")
        wav_path = output_mp3_path.replace(".mp3", f"_chunk{i}.wav")
        with open(wav_path, "wb") as f:
            f.write(_b64.b64decode(audios[0]))
        raw_paths.append(wav_path)

    # Concat WAVs and convert to MP3
    if len(raw_paths) == 1:
        _sp.run(["ffmpeg", "-y", "-i", raw_paths[0], "-codec:a", "libmp3lame",
                 "-q:a", "2", output_mp3_path],
                capture_output=True, timeout=60, check=True)
    else:
        list_path = output_mp3_path + ".concat.txt"
        with open(list_path, "w") as f:
            for p in raw_paths:
                f.write(f"file '{p}'\n")
        _sp.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
                 "-codec:a", "libmp3lame", "-q:a", "2", output_mp3_path],
                capture_output=True, timeout=120, check=True)
        try:
            os.remove(list_path)
        except OSError:
            pass

    for p in raw_paths:
        try:
            os.remove(p)
        except OSError:
            pass

    return output_mp3_path


def load_bg_music(mood="calm"):
    """Load background music via AI generation (ACE-Step on Replicate)."""
    ai_path = generate_bg_music(mood)
    if ai_path:
        return

    existing = glob.glob(f"{BG_MUSIC_FOLDER}/*.mp3") + glob.glob(f"{BG_MUSIC_FOLDER}/*.wav")
    if existing:
        mood_files = [f for f in existing if mood.lower() in os.path.basename(f).lower()]
        print(f"   🎵 {len(existing)} music file(s) available ({len(mood_files)} match '{mood}' mood)")
    else:
        print("   ⚠️ No background music available.")
        print("   💡 Set REPLICATE_API_TOKEN secret for AI-generated music (ACE-Step)")


def _apply_dynamic_volume(music_clip, duration):
    """Apply dynamic volume: louder at start/end, quieter in middle.
    Creates an engaging audio curve instead of flat volume."""
    import numpy as np

    start_dur = min(2.0, duration * 0.1)
    end_dur = min(3.0, duration * 0.15)
    mid_start = start_dur
    mid_end = max(mid_start + 0.1, duration - end_dur)

    def volume_filter(get_frame, t):
        frame = get_frame(t)
        t_arr = np.asarray(t, dtype=float)

        # Vectorized volume curve using np.where (handles both scalar and array t)
        end_progress = np.minimum((t_arr - mid_end) / max(end_dur, 0.1), 1.0)
        end_vol = BG_MUSIC_VOLUME_MID + (BG_MUSIC_VOLUME_END - BG_MUSIC_VOLUME_MID) * end_progress

        ramp_progress = np.minimum((t_arr - mid_start) / 1.0, 1.0)
        ramp_vol = BG_MUSIC_VOLUME_START + (BG_MUSIC_VOLUME_MID - BG_MUSIC_VOLUME_START) * ramp_progress
        mid_vol = np.where(t_arr < mid_start + 1.0, ramp_vol, BG_MUSIC_VOLUME_MID)

        vol = np.where(t_arr < start_dur, BG_MUSIC_VOLUME_START,
                       np.where(t_arr > mid_end, end_vol, mid_vol))

        scale = vol / max(BG_MUSIC_VOLUME, 0.01)
        frame_arr = np.asarray(frame)
        # Reshape scale for broadcasting with (N, channels) audio frames
        if frame_arr.ndim == 2 and np.ndim(scale) >= 1:
            scale = np.expand_dims(scale, -1)
        return (frame_arr * scale).astype(frame_arr.dtype)

    return music_clip.fl(volume_filter, keep_duration=True)


def generate_hook_sfx(duration=0.6):
    """Generate a cinematic bass drop + whoosh sound effect for the hook moment.
    Layered design: sub-bass boom + mid-freq impact + high-freq whoosh + noise burst."""
    if not ADD_HOOK_SFX:
        return None
    try:
        import numpy as np
        from moviepy.audio.AudioClip import AudioClip

        sr = 44100

        def make_frame(t):
            t = np.asarray(t, dtype=np.float64)
            vol = HOOK_SFX_VOLUME

            # Layer 1: Sub-bass boom (40Hz, fast exponential decay)
            sub_env = np.exp(-t * 10)
            sub = np.sin(2 * np.pi * 40 * t) * sub_env * vol * 0.6

            # Layer 2: Mid impact hit (100Hz with pitch drop from 200Hz → 80Hz)
            mid_freq = 200 * np.exp(-t * 4) + 80
            mid_phase = 2 * np.pi * np.cumsum(np.broadcast_to(mid_freq, t.shape) / sr) if t.ndim > 0 else 2 * np.pi * mid_freq * t
            mid_env = np.exp(-t * 7)
            mid = np.sin(mid_phase) * mid_env * vol * 0.4

            # Layer 3: High-freq whoosh (white noise shaped with bandpass feel)
            # Deterministic noise using sine sum (reproducible, no random seed issues)
            whoosh = np.zeros_like(t)
            for f in [2200, 3100, 4500, 5800, 7200]:
                whoosh += np.sin(2 * np.pi * f * t + f * 0.7) * 0.2
            whoosh_env = np.exp(-t * 12) * (1 - np.exp(-t * 80))  # Attack + fast decay
            whoosh = whoosh * whoosh_env * vol * 0.25

            # Layer 4: Transient click (very short, adds punch to the initial hit)
            click_env = np.exp(-t * 60) * (1 - np.exp(-t * 200))
            click = np.sin(2 * np.pi * 1000 * t) * click_env * vol * 0.3

            # Mix all layers
            result = sub + mid + whoosh + click

            # Soft clip to avoid harsh distortion
            result = np.tanh(result * 1.5) * 0.8

            return np.column_stack([result, result])

        sfx = AudioClip(make_frame, duration=duration, fps=sr)
        print("   💥 Hook SFX: cinematic boom + whoosh (4-layer)")
        return sfx
    except Exception as e:
        print(f"   ⚠️ Hook SFX generation failed: {e}")
        return None


def generate_transition_whoosh(duration=0.4):
    """Short rising-pitch whoosh for clip boundaries. Adds 'cuts/min' polish.

    Spectral design: pink-ish noise burst + rising sine sweep + soft envelope.
    Lighter than the hook boom — meant to subtly punctuate, not dominate.
    """
    if not ADD_TRANSITION_SFX:
        return None
    try:
        import numpy as np
        from moviepy.audio.AudioClip import AudioClip

        sr = 44100

        def make_frame(t):
            t = np.asarray(t, dtype=np.float64)
            vol = TRANSITION_SFX_VOLUME

            # Rising sine sweep 600Hz → 2400Hz over the duration
            sweep_freq = 600 + (1800 * (t / max(duration, 0.01)))
            phase = 2 * np.pi * np.cumsum(np.broadcast_to(sweep_freq, t.shape) / sr) \
                if t.ndim > 0 else 2 * np.pi * sweep_freq * t
            sweep = np.sin(phase) * vol * 0.35

            # Pink-ish noise burst (deterministic, sine-sum approximation)
            noise = np.zeros_like(t)
            for f, amp in [(1200, 0.18), (1900, 0.14), (2700, 0.12), (3800, 0.08), (5400, 0.06)]:
                noise += np.sin(2 * np.pi * f * t + f * 0.31) * amp
            noise *= vol

            # Envelope: fast attack, smooth decay (whoosh shape)
            env = (1 - np.exp(-t * 30)) * np.exp(-t * 5)
            result = (sweep + noise) * env

            # Soft clip
            result = np.tanh(result * 1.4) * 0.7
            return np.column_stack([result, result])

        return AudioClip(make_frame, duration=duration, fps=sr)
    except Exception as e:
        print(f"   ⚠️ Transition whoosh generation failed: {e}")
        return None


def build_transition_sfx_layer(num_clips, clip_duration_sec, total_duration):
    """Build an audio layer of transition whooshes positioned at each clip boundary.
    Returns CompositeAudioClip or None if disabled / no transitions.
    """
    if not ADD_TRANSITION_SFX or num_clips <= 1:
        return None
    try:
        from moviepy.audio.AudioClip import CompositeAudioClip
        sfx_clips = []
        whoosh_dur = 0.4
        # Whoosh starts ~0.15s BEFORE each cut so it leads into the new clip
        for i in range(1, num_clips):
            cut_t = i * clip_duration_sec
            start_t = max(0.0, cut_t - 0.15)
            if start_t + whoosh_dur > total_duration:
                continue
            whoosh = generate_transition_whoosh(duration=whoosh_dur)
            if whoosh is None:
                continue
            sfx_clips.append(whoosh.set_start(start_t))
        if not sfx_clips:
            return None
        return CompositeAudioClip(sfx_clips)
    except Exception as e:
        print(f"   ⚠️ Transition SFX layer skipped: {e}")
        return None


def mix_background_music(voice_audio_clip, duration, mood="calm"):
    """Mix background music with voice audio. Prioritizes AI-generated music
    over repo files, then mood-matching. Uses dynamic volume curve."""
    if not ADD_BG_MUSIC:
        return voice_audio_clip

    all_files = glob.glob(f"{BG_MUSIC_FOLDER}/*.mp3") + glob.glob(f"{BG_MUSIC_FOLDER}/*.wav")
    if not all_files:
        return voice_audio_clip

    # Priority: AI-generated music (ai_*) > mood-matching repo files > any file
    ai_files = [f for f in all_files if os.path.basename(f).startswith("ai_")]
    ai_mood_files = [f for f in ai_files if mood.lower() in os.path.basename(f).lower()]
    mood_files = [f for f in all_files if mood.lower() in os.path.basename(f).lower()]

    if ai_mood_files:
        music_files = ai_mood_files
    elif ai_files:
        music_files = ai_files
    elif mood_files:
        music_files = mood_files
    else:
        music_files = all_files

    try:
        # Pick the most recent file (latest generated) from the preferred group
        music_files.sort(key=os.path.getmtime, reverse=True)
        music_path = music_files[0]
        print(f"   🎵 Adding background music: {os.path.basename(music_path)}")

        music_clip = AudioFileClip(music_path)

        # Loop music if shorter than video, or trim if longer
        if music_clip.duration < duration:
            music_clip = audio_loop(music_clip, duration=duration)
        else:
            music_clip = music_clip.subclip(0, duration)

        # Apply dynamic volume (louder start/end, quieter middle)
        music_clip = volumex(music_clip, BG_MUSIC_VOLUME)
        music_clip = _apply_dynamic_volume(music_clip, duration)

        # Fade out at the very end
        from moviepy.audio.fx.audio_fadeout import audio_fadeout
        music_clip = audio_fadeout(music_clip, 2.0)

        # Add hook sound effect at the beginning
        audio_layers = [music_clip, voice_audio_clip]
        hook_sfx = generate_hook_sfx(duration=0.5)
        if hook_sfx:
            audio_layers.append(hook_sfx)

        mixed = CompositeAudioClip(audio_layers)
        print(f"   ✅ Background music mixed with dynamic volume (start:{int(BG_MUSIC_VOLUME_START*100)}% → mid:{int(BG_MUSIC_VOLUME_MID*100)}% → end:{int(BG_MUSIC_VOLUME_END*100)}%)")
        return mixed

    except Exception as e:
        print(f"   ⚠️ Background music mixing failed: {e}")
        return voice_audio_clip


def extract_ambient_audio(clip_paths, total_duration):
    """Extract and concatenate ambient audio from Veo video clips at low volume.
    Returns (ambient_clip, temp_files) — caller must clean up temp_files after rendering."""
    if not clip_paths or VEO_AMBIENT_VOLUME <= 0:
        return None, []

    import subprocess
    audio_clips = []
    temp_audio_files = []

    for clip_path in clip_paths:
        try:
            audio_path = clip_path.replace(".mp4", "_ambient.wav")
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", clip_path, "-vn", "-acodec", "pcm_s16le",
                 "-ar", "44100", "-ac", "2", audio_path],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0 and os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000:
                ac = AudioFileClip(audio_path)
                audio_clips.append(ac)
                temp_audio_files.append(audio_path)
        except Exception:
            pass

    if not audio_clips:
        print("   ℹ️ No ambient audio found in clips (normal for test mode)")
        return None, temp_audio_files

    try:
        ambient = concatenate_audioclips(audio_clips)

        # Adjust to match total video duration
        if ambient.duration < total_duration:
            ambient = audio_loop(ambient, duration=total_duration)
        else:
            ambient = ambient.subclip(0, total_duration)

        ambient = volumex(ambient, VEO_AMBIENT_VOLUME)
        from moviepy.audio.fx.audio_fadeout import audio_fadeout
        ambient = audio_fadeout(ambient, 2.0)
        print(f"   🔊 Veo ambient audio extracted ({len(audio_clips)} clips, {int(VEO_AMBIENT_VOLUME * 100)}% volume)")
        return ambient, temp_audio_files

    except Exception as e:
        print(f"   ⚠️ Ambient audio extraction failed: {e}")
        return None, temp_audio_files


# ═══════════════════════════════════════════════════════════════════════
# SCRIPT QUALITY GATE
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
# TOPIC QUALITY GATE + SMART TOPIC GENERATION
# ═══════════════════════════════════════════════════════════════════════

TOPIC_MAX_CANDIDATES = 5  # Generate this many candidates, pick the best
TOPIC_MIN_SCORE = 25      # Out of 40 — threshold for auto-approval

def search_trending_topics(anthropic_client):
    """Use Claude to brainstorm trending topics based on real channel data + AI knowledge.
    Feeds in: source channel top Shorts with view counts, audience questions,
    thumbnail research patterns, category performance, and seasonal context."""

    # Gather all gold data
    source_videos = fetch_source_channel_insights()
    source_with_views = []
    if source_videos:
        for v in source_videos[:15]:
            source_with_views.append(f"- {v['title']} ({v['views']:,} views, {v['likes']:,} likes)")

    audience_qs = get_audience_questions(10)

    # Category performance — which categories get the most views
    cat_ranking = get_source_channel_category_ranking()
    own_cats = get_top_performing_categories()

    # Thumbnail research — power words and example texts that work
    thumb_research = {}
    if os.path.exists(THUMBNAIL_RESEARCH_FILE):
        try:
            with open(THUMBNAIL_RESEARCH_FILE, "r") as f:
                thumb_research = json.load(f)
        except Exception:
            pass

    prompt = f"""You are a YouTube Shorts content strategist for an Indian B2B t-shirt manufacturer (Sale91.com).

Your job: generate 10 FRESH topic ideas that are likely to get MAXIMUM VIEWS.

STRATEGY:
1. STUDY the real data below — these are ACTUAL Shorts that got real views on our channel
2. Identify PATTERNS — what topics, formats, and angles get the most views?
3. Generate NEW topics that follow winning patterns but with FRESH angles
4. Use audience questions as direct topic inspiration — viewers literally asked for these
5. Consider seasonal trends and search intent

CURRENT CONTEXT:
- Month: {datetime.now(pytz.timezone(TIMEZONE)).strftime('%B %Y')}
- Season in India: {_get_india_season()}
- Business: B2B plain t-shirt manufacturer in Tiruppur/Delhi
- Audience: Custom printing businesses (DTG, DTF, screen print), merch brands, bulk buyers

=== GOLD DATA: REAL PERFORMANCE FROM OUR MAIN CHANNEL (50K subs) ===
These are ACTUAL Shorts with REAL view counts — study what works:
{chr(10).join(source_with_views) if source_with_views else "No source channel data available."}

=== TOP PERFORMING VIDEOS ON OUR SHORTS CHANNEL ===
{json.dumps(get_top_performing_topics(5), ensure_ascii=False) if get_top_performing_topics(5) else "New channel — no data yet."}

=== BEST PERFORMING CATEGORIES (by average views) ===
Main channel: {', '.join(cat_ranking[:5]) if cat_ranking else 'No data'}
Own channel: {', '.join(own_cats[:5]) if own_cats else 'No data yet'}
Instagram: {', '.join(get_top_performing_ig_categories()[:5]) if get_top_performing_ig_categories() else 'No IG data yet'}

=== TOP PERFORMING INSTAGRAM REELS ===
{json.dumps(get_top_performing_ig_topics(5), ensure_ascii=False) if get_top_performing_ig_topics(5) else "No Instagram engagement data yet."}

=== AUDIENCE QUESTIONS (real comments — viewers WANT these topics explained) ===
{audience_qs if audience_qs else "No audience questions available."}

=== THUMBNAIL/HOOK PATTERNS THAT GET CLICKS ===
Power words: {json.dumps(thumb_research.get('power_words', []), ensure_ascii=False)}
Example texts that work: {json.dumps(thumb_research.get('example_texts', []), ensure_ascii=False)}
What patterns perform best: {thumb_research.get('patterns', 'No data yet')}

RULES:
- Each topic must be in Hindi conversational (Hinglish) style
- Practical knowledge, storytelling format — no selling
- Each topic should be specific and contain a hook element
- Generate topics that COMPLEMENT the winners above — similar patterns, fresh angles
- At least 2-3 topics should be inspired by real audience questions
- At least 2-3 should follow the highest-viewed video patterns

OUTPUT: Return ONLY a JSON array of 10 topic strings, nothing else.
Example: ["Topic 1 — detail", "Topic 2 — detail", ...]"""

    try:
        resp = anthropic_client.messages.create(
            model="claude-opus-4-6", max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"): raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        topics = json.loads(raw)
        if isinstance(topics, list) and len(topics) > 0:
            print(f"   🔍 Generated {len(topics)} trending topic candidates")
            return topics
    except Exception as e:
        print(f"   ⚠️ Trending topic search failed: {e}")

    return []


def _get_india_season():
    """Return current season in India for topic relevance."""
    month = datetime.now(pytz.timezone(TIMEZONE)).month
    if month in (3, 4, 5):
        return "Summer approaching — cotton t-shirt demand peak, lightweight fabrics trending"
    elif month in (6, 7, 8, 9):
        return "Monsoon/Rainy season — drying issues, color bleeding concerns, polyester demand"
    elif month in (10, 11):
        return "Festival season (Diwali, Navratri) — corporate gifting orders, custom merch rush"
    else:
        return "Winter — hoodie/sweatshirt demand peak, heavy GSM fabrics trending"


def review_topic(claude_client, topic, topic_history):
    """Claude reviews a topic candidate for search potential, freshness, and content fit.
    Returns (score, feedback) where score is out of 40."""

    recent_topics = topic_history[-20:] if len(topic_history) > 20 else topic_history

    review_prompt = f"""You are a YouTube Shorts content strategist for an Indian B2B t-shirt brand (Sale91.com).

Review this topic candidate and score it for a YouTube Short:

TOPIC: {topic}

RECENTLY USED TOPICS (avoid similar ones):
{json.dumps(recent_topics[-10:], ensure_ascii=False)}

WHAT OUR AUDIENCE IS ASKING (real comments — bonus points if topic answers these):
{get_audience_questions(5)}

Score each (1-10):

1. SEARCH POTENTIAL — Would printing business owners actively search for this on YouTube?
   High: specific problem ("DTG print dhul gaya 2 wash mein — kya galti ki?")
   Low: vague/generic ("fabric ke baare mein jaano")

2. FRESHNESS — Is this genuinely different from recently used topics? Not repetitive?
   High: new angle, untouched subtopic. Low: similar to a recent topic.

3. STORYTELLING FIT — Can this be turned into a compelling 50-sec micro-story with hook?
   High: has natural conflict/problem/surprise. Low: just a definition or list.

4. VIRAL SHAREABILITY — Would someone save this or send it to a fellow business owner?
   High: actionable tip, surprising fact, money-saving advice. Low: common knowledge.

OUTPUT THIS JSON ONLY (no markdown):
{{"score": total_out_of_40, "feedback": "1 sentence — why good or what's wrong"}}"""

    try:
        resp = claude_client.messages.create(
            model="claude-opus-4-6", max_tokens=200,
            messages=[{"role": "user", "content": review_prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"): raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        result = json.loads(raw)
        return result.get("score", 0), result.get("feedback", "")
    except Exception as e:
        print(f"   ⚠️ Topic review failed: {e}")
        return 30, "review error — approved by default"


def _topic_blog_viable(topic, claude_client=None):
    """True if today's topic can also become a blog post (or today isn't a blog day).

    The duplicate check runs BEFORE anything is generated: on blog days
    (Mon/Wed/Fri) the day's topic itself must target a search-query cluster the
    blog hasn't covered, so the blog never silently skips and no generation
    spend is wasted on an unpublishable topic. Non-blog days are unconstrained
    (video topics may repeat freely — YouTube/IG don't punish repeats)."""
    if datetime.now(pytz.timezone(TIMEZONE)).weekday() not in BLOG_WEEKDAYS:
        return True
    slug, sim = blog_cluster_collision(topic, topic, claude_client=claude_client)
    if slug:
        print(f"   🚫 Topic not blog-viable — cluster already covered by '{slug}' (sim {sim:.2f}); trying another")
    return slug is None


def smart_pick_topic(claude_client, topic_bank, topic_history):
    """Smart topic selection:
    1. If unused topics in bank, pick from them but validate with Claude
    2. If bank exhausted, generate trending topics and pick the best one
    On blog days, topics must pass the blog-cluster viability pre-check.
    Returns the selected topic string."""

    unused = [t for t in topic_bank if t not in topic_history]

    if unused:
        # Prefer topics from high-performing categories (engagement-based)
        # Source channel (50K subs) is ALWAYS primary for topic ranking
        top_cats = get_source_channel_category_ranking()
        cat_source = "source channel (primary)"
        if not top_cats:
            top_cats = get_top_performing_categories()
            cat_source = "own channel (fallback)"

        if top_cats:
            # Sort unused topics: ones matching top categories come first
            def _cat_priority(topic):
                t_lower = topic.lower()
                for rank, cat in enumerate(top_cats):
                    cat_data = TOPIC_SERIES_TAGS.get(cat, {})
                    if any(kw in t_lower for kw in cat_data.get("keywords", [])):
                        return rank  # Lower = higher priority
                return len(top_cats)  # No match = lowest priority
            prioritized = sorted(unused, key=_cat_priority)
            # Pick from top 30% (biased towards high-performing categories)
            pool_size = max(3, len(prioritized) // 3)
            pool = prioritized[:pool_size]
            print(f"   📊 Top categories by engagement ({cat_source}): {', '.join(top_cats[:3])}")
        else:
            prioritized = unused[:]
            pool = unused[:]

        # Walk the pool in random order and take the first topic that can also
        # become a blog today; widen past the engagement-biased pool if needed
        # (on blog days freshness beats category bias). Cap the scan so the
        # ambiguous-band Claude judge can't burn unbounded calls.
        random.shuffle(pool)
        scan = pool + [t for t in prioritized if t not in pool]
        candidate = None
        for t in scan[:12]:
            if _topic_blog_viable(t, claude_client):
                candidate = t
                break
        if candidate is None:
            # No blog-viable topic found — pick normally; the publish gate
            # will skip the blog (video/reel still ship).
            candidate = random.choice(pool)

        score, feedback = review_topic(claude_client, candidate, topic_history)
        print(f"   📋 Bank topic score: {score}/40 — {feedback}")
        if score >= TOPIC_MIN_SCORE:
            return candidate
        # If bank topic scored low, try 2 more from bank (blog-viable only)
        for _ in range(2):
            alt = random.choice(unused)
            if alt != candidate and _topic_blog_viable(alt, claude_client):
                alt_score, alt_feedback = review_topic(claude_client, alt, topic_history)
                print(f"   📋 Alt bank topic score: {alt_score}/40 — {alt_feedback}")
                if alt_score > score:
                    candidate, score, feedback = alt, alt_score, alt_feedback
        if score >= 20:  # Accept if reasonably good
            return candidate

    # Bank exhausted or all scored low — generate fresh trending topics
    print("   🧠 Generating fresh trending topics with AI search...")
    trending = search_trending_topics(claude_client)

    if not trending:
        # Absolute fallback: single topic using source channel inspiration
        print("   🔄 Fallback: single topic generation with source channel data...")
        source_titles = get_source_channel_top_topics(5)
        aud_qs = get_audience_questions(3)
        try:
            resp = claude_client.messages.create(
                model="claude-opus-4-6", max_tokens=200,
                messages=[{"role": "user", "content": f"""Generate 1 new YouTube Shorts topic for a B2B plain t-shirt manufacturer.
Style: practical knowledge, no selling. Hindi conversational.
Already used: {json.dumps(topic_history[-10:])}
Top performing Shorts on our channel: {json.dumps(source_titles, ensure_ascii=False) if source_titles else 'No data'}
Top performing Instagram Reels: {json.dumps(get_top_performing_ig_topics(5), ensure_ascii=False) if get_top_performing_ig_topics(5) else 'No IG data'}
Audience questions: {aud_qs if aud_qs else 'No data'}
Generate a topic inspired by the winning patterns above but with a fresh angle. Consider what works on BOTH YouTube and Instagram.
Return ONLY the topic text, nothing else."""}]
            )
            return resp.content[0].text.strip()
        except Exception as e:
            print(f"   ⚠️ Fallback topic generation failed: {e}")
            return "Plain T-Shirt Quality Check — GSM aur Fabric Basics"

    # Score all trending candidates and pick the best (blog-viable first;
    # fall back to the unfiltered list if nothing viable — blog will skip)
    best_topic = trending[0]
    best_score = 0
    candidates_to_review = [t for t in trending if t not in topic_history and _topic_blog_viable(t, claude_client)][:TOPIC_MAX_CANDIDATES]
    if not candidates_to_review:
        candidates_to_review = [t for t in trending if t not in topic_history][:TOPIC_MAX_CANDIDATES]

    for t in candidates_to_review:
        score, feedback = review_topic(claude_client, t, topic_history)
        print(f"   🔍 Candidate: {t[:50]}... → {score}/40 ({feedback})")
        if score > best_score:
            best_score = score
            best_topic = t

    print(f"   ✅ Best topic selected (score: {best_score}/40): {best_topic[:60]}...")
    return best_topic


def review_script(claude_client, script_voice, script_english, topic, video_prompts=None):
    """Claude reviews its own script like a human content creator would.
    Returns (approved: bool, score: int, weakest: str, feedback: str)."""

    prompts_section = ""
    if video_prompts:
        prompts_list = "\n".join(f"  Clip {i+1}: {p}" for i, p in enumerate(video_prompts) if p.strip())
        prompts_section = f"\nVEO VIDEO PROMPTS:\n{prompts_list}\n"

    review_prompt = f"""You are a YouTube Shorts + Instagram Reels content reviewer for an Indian B2B t-shirt brand.
Review this script and decide: is this GOOD ENOUGH to publish?

Remember: this is B2B educational content for printing businesses, NOT entertainment/clickbait.
A factory owner explaining something practical IS valuable — don't expect Bollywood drama.

TOPIC: {topic}
HINDI SCRIPT: {script_voice}
ENGLISH: {script_english}
{prompts_section}
Score each (1-10):

1. HOOK (first 2 seconds) — Does it start with a STORY, customer incident, or surprising fact?
   Bad: starts with a definition ("GSM matlab..."). Good: "Ek customer aaya tha..." or "Pehle main bhi yahi galti karta tha..."

2. NATURAL FEEL — Does it sound like a REAL factory owner talking?
   Bad: sounds like a textbook/script. Good: fillers, compound verbs, blunt honesty.

3. VALUE — Does the viewer LEARN something useful and specific?
   Bad: vague fluff. Good: specific numbers, practical tips, actionable knowledge.

4. ENDING — Does it trail off naturally like a real person finishing?
   Bad: abrupt cut or sounds like more is coming. Good: "...bas yehi hai, simple hai."

5. VIRAL POTENTIAL — Would a printing business owner find this useful enough to save/share?
   Bad: says nothing new. Good: practical tip, surprising fact, common mistake exposed.

6. VISUAL ALIGNMENT — Do the Veo video prompts match the script's specific story?
   Bad: generic prompts like "a factory scene" or "fabric close-up" that could apply to ANY script.
   Good: prompts that show the EXACT scenario being discussed — the specific fabric, the specific test, the specific machine, the specific problem from the script.
   If no video prompts provided, score 6 (neutral).

OUTPUT THIS JSON ONLY (no markdown):
{{"approved": true/false, "total_score": sum_of_6_scores, "weakest": "which area is weakest", "feedback": "1-2 sentences on what's wrong (if rejected) or what's great (if approved)"}}

RULES:
- Approve if total_score >= 36 (out of 60)
- REJECT only if ANY single score is below 4
- Educational B2B content scoring 6-7 per area is GOOD — don't expect 9s and 10s"""

    try:
        resp = claude_client.messages.create(
            model="claude-opus-4-6", max_tokens=300,
            messages=[{"role": "user", "content": review_prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"): raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        review = json.loads(raw)

        approved = review.get("approved", False)
        score = review.get("total_score", 0)
        weakest = review.get("weakest", "unknown")
        feedback = review.get("feedback", "")

        return approved, score, weakest, feedback

    except Exception as e:
        # If review fails, approve by default (don't block pipeline)
        print(f"   ⚠️ Review failed ({e}), approving by default")
        return True, 0, "", "review error"


def optimize_title(claude_client, original_title, script_english, topic):
    """Generate platform-specific titles for YouTube + Instagram.
    Returns a dict {'yt': str, 'ig': str} — different optimizations per platform,
    informed by each platform's own engagement history.

    YouTube audience signal (from engagement_history.json): personal vulnerability
    stories + Hindi-script titles + numerical specificity.
    Instagram audience signal (from ig_engagement_history.json): cost/disaster
    framing + third-person founder stories + comparison hooks.

    Backward compatibility: also exposes a top-level 'best' string for callers
    that only want one title (defaults to YT version).
    """
    print(f"   🏷️ Title optimization: per-platform variants...")

    source_titles = get_source_channel_top_topics(5)
    source_context = ""
    if source_titles:
        source_context = f"""
REFERENCE — these titles got the MOST views on our main channel (50K subs):
{json.dumps(source_titles, ensure_ascii=False)}
Study their patterns — length, keywords, emotional hooks."""

    # Pull each platform's own engagement signal
    ig_summary = get_ig_engagement_summary()
    ig_context = ""
    if ig_summary.get("total_reels_analyzed", 0) > 0:
        top_ig = ig_summary.get("top_reels", [])[:5]
        viral = ig_summary.get("viral_titles", [])[:5]
        ig_context = f"""
INSTAGRAM AUDIENCE SIGNAL (top performers from our own Reels — DIFFERENT from YouTube):
Top by views: {json.dumps([t['title'] for t in top_ig], ensure_ascii=False)}
Above-average share rate: {json.dumps(viral[:5], ensure_ascii=False)}
IG averages: {json.dumps(ig_summary.get('avg_metrics', {}))}

OBSERVED IG PATTERNS (from our data):
- Cost/disaster framing engages: "Cost Trap", "Shut Down", "Cost Lakhs"
- Third-person founder stories work: "He Bought X & Shut Down"
- Comparison + cost: "Spot Color vs CMYK – Ye Galti Margin Kha Gayi"
- Mixed Hindi-English titles work fine on IG"""
    else:
        ig_context = """
INSTAGRAM AUDIENCE SIGNAL: not enough data yet — use these defaults:
- Cost/disaster framing
- Third-person founder stories ("He Bought X")
- Comparison + cost framing"""

    prompt = f"""You are a Shorts/Reels title optimizer for an Indian B2B t-shirt brand
(Sale91.com — wholesale plain t-shirts, printing services, 50K-sub source channel).

CURRENT TITLE: {original_title}
TOPIC: {topic}
SCRIPT SUMMARY: {script_english[:200]}
{source_context}
{ig_context}

YOUTUBE AUDIENCE SIGNAL (from our 110-video history):
- Personal vulnerability stories outperform: "Paise Nahi The — Aur Wo Sabse Achi Baat Thi"
- Hindi-script titles do well: "कम मार्जिन, बड़ा खेल"
- Specific numbers + dramatic framing: "₹40 DTF Film vs ₹100"
- AVOID: generic textile-tech with English titles ("180 GSM vs 200 GSM") — these die at <10 views

YOUR TASK: Generate THREE titles, each platform-tuned:

1. YOUTUBE title — optimized for YouTube Shorts:
   - Max 70 chars
   - Search-discoverable keywords (GSM, DTG, printing, t-shirt etc.)
   - Personal angle / vulnerability or dramatic comparison
   - Hindi script (Devanagari) is OK and even encouraged when title is Hindi-heavy

2. INSTAGRAM title — optimized for Reels Explore page:
   - Max 70 chars
   - Cost/disaster/loss framing OR third-person founder story
   - Comparison + cost ("X vs Y – the trap" pattern)
   - Latin script preferred; mixed Hindi-English fine
   - Curiosity hook — make them stop scrolling

3. BLOG title — optimized for Google Search + AI search engines (ChatGPT/Claude/Perplexity):
   - Max 80 chars
   - **STRICT: NO Devanagari / Hindi script anywhere — use Latin script ONLY.**
     Hinglish (English-with-Hindi-words-in-Latin-letters) is fine. Pure English is also fine.
     ✅ Allowed: "Lost ₹40K on Tri-blend Fabric — Why Indian Printers Avoid Cotton+Polyester+Rayon Mix"
     ✅ Allowed: "240 GSM Ka Asli Trap — Why Heavier Doesn't Mean Better for Bulk Tshirts"
     ❌ Banned:  "240 GSM का झांसा — असली Quality यहाँ छुपी है"
   - **Front-load English keywords** that Indian B2B printers type into Google:
     GSM, DTG, DTF, screen print, plain tshirt wholesale, manufacturer, bulk, MOQ, oversized,
     drop shoulder, polo, hoodie, cotton, fabric, Delhi, India, Tiruppur, ₹, lakh.
   - Include a NUMBER when possible (₹40K, 500 pieces, 240 GSM) — improves CTR + AI citation.
   - **LEAD with the searchable keyword phrase, THEN the hook** — Google weighs the
     first words of the <title>/<h1> most. Put the term Indian printers actually type
     at the START, not buried after a story clause.
     ✅ "DTF vs Screen Print Cost on a ₹500 T-Shirt — Real Breakdown for Indian Printers"
     ✅ "240 GSM vs 180 GSM for Summer — Why 600 Pieces Went Unsold"
     ❌ "He Lost ₹40K — The DTF Mistake Nobody Warns You About"  (keyword buried/absent)
   - Structure: "[keyword phrase Indian buyers search for] — [specific number/scenario hook]"
   - This title becomes the blog's <title>, <h1>, og:title — what Google's crawler indexes and
     what ChatGPT/Claude show when citing the page. Optimize for SEARCH, not feed scroll.
   - **WHY:** Google + AI search engines treat Devanagari titles as Hindi-language content and
     only surface them for Hindi queries. Indian B2B printers search Google in English even
     when they speak Hindi. The blog needs Latin-script titles to rank for English queries.

OUTPUT THIS JSON ONLY (no markdown):
{{"yt_title": "title for YouTube (Hindi script OK)", "ig_title": "title for Instagram", "blog_title": "title for Google/AI search — LATIN SCRIPT ONLY", "rationale": "brief why each was tuned this way"}}"""

    try:
        resp = claude_client.messages.create(
            model="claude-opus-4-6", max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        result = json.loads(raw)

        yt_t = (result.get("yt_title") or "").strip()
        ig_t = (result.get("ig_title") or "").strip()
        blog_t = (result.get("blog_title") or "").strip()
        rationale = result.get("rationale", "")

        # Fallbacks if anything missing
        if not yt_t:
            yt_t = original_title
        if not ig_t:
            ig_t = yt_t
        # blog_t MUST be Latin-script for Google SEO. If empty OR contains Devanagari,
        # we fall back to original_title only if THAT is Latin-script too; otherwise
        # we strip Devanagari from yt_t as a last resort.
        import re as _re
        DEVANAGARI = _re.compile(r'[\u0900-\u097F]')
        if not blog_t or DEVANAGARI.search(blog_t):
            # Try original_title (often Latin-script)
            if not DEVANAGARI.search(original_title or ""):
                blog_t = original_title
            else:
                # Worst case: keep the Hindi yt_t but strip Devanagari letters (ugly but
                # better than indexing Hindi-script titles in Google). User will see this
                # in logs and can re-trigger if needed.
                stripped = DEVANAGARI.sub('', yt_t).strip()
                blog_t = stripped or original_title or "Bulk Plain T-Shirt Wholesale India"

        # Truncate safely
        if len(yt_t) > 100: yt_t = yt_t[:97] + "..."
        if len(ig_t) > 100: ig_t = ig_t[:97] + "..."
        if len(blog_t) > 110: blog_t = blog_t[:107] + "..."

        print(f"   🏷️ YouTube title  (Hindi OK)  : {yt_t}")
        print(f"   📸 Instagram title (mixed OK) : {ig_t}")
        print(f"   📰 Blog title      (Latin only): {blog_t}")
        if rationale:
            print(f"      Rationale: {rationale[:200]}")

        # 'best' returns the blog_title — the SEO-optimized one. Callers that want
        # the YT version should read titles["yt"] explicitly.
        return {"yt": yt_t, "ig": ig_t, "blog": blog_t, "best": blog_t}

    except Exception as e:
        print(f"   ⚠️ Title optimization failed ({e}), using original for both")
        return {"yt": original_title, "ig": original_title, "blog": original_title, "best": original_title}


# ═══════════════════════════════════════════════════════════════════════
# SEO BLOG POST GENERATION
# ═══════════════════════════════════════════════════════════════════════

# Blog config
BLOG_S3_BUCKET = "bulkplaintshirt.com"
BLOG_BASE_URL = "https://www.bulkplaintshirt.com"
BLOG_CLOUDFRONT_DIST_ID = "E21QLU9SBUBY7Z"
BLOG_HISTORY_FILE = "blog_history.json"
INDEXNOW_API_KEY = "sale91com2025indexnow"  # IndexNow key for Bing/Yandex/AI search

# Reddit drafts — daily content for manual posting by employee. We auto-generate
# one ready-to-paste Reddit post per blog (title + body + target sub + image)
# and commit it to reddit_drafts/YYYY-MM-DD.md. The employee opens the file,
# copies the content into Reddit, attaches the hero image, hits submit.
REDDIT_DRAFTS_DIR = "reddit_drafts"
# Whitelist of subreddits that tolerate value-first self-promo with a track
# record of community engagement. DO NOT add subs to this list without
# checking their rules — most subreddits ban any self-promotion entirely.
REDDIT_SUBS_WHITELIST = [
    "r/PrintOnDemand",        # POD sellers — DTG/DTF/blanks topics
    "r/screenprinting",       # screen printing technique focus
    "r/streetwearstartup",    # startup brands ordering blanks
    "r/Entrepreneur",         # ONLY via Sunday weekly self-promo thread
    "r/IndianEntrepreneur",   # India-specific B2B
    "r/SmallBusinessIndia",   # India-specific small biz
    "r/etsy",                 # resellers using blanks for prints
]


def inject_blog_seo(html_content, title, description, blog_url, today, slug, og_image_url=None, vid_id=None, vid_url=None):
    """Inject JSON-LD structured data and sticky bottom bar into blog HTML.
    This runs AFTER Claude generates the HTML, so it's 100% reliable."""
    import re as _re

    # ── 1. Parse FAQ Q&As from the generated HTML for FAQPage schema ──
    faq_pairs = []
    # Match patterns like: Q1: ... / Q: ... in faq-question divs, followed by faq-answer divs
    q_pattern = _re.compile(r'class="faq-question"[^>]*>(?:Q\d*[:.]?\s*)?(.+?)</div>', _re.DOTALL)
    a_pattern = _re.compile(r'class="faq-answer"[^>]*>(.+?)</div>', _re.DOTALL)
    questions = q_pattern.findall(html_content)
    answers = a_pattern.findall(html_content)
    for q, a in zip(questions, answers):
        clean_q = _re.sub(r'<[^>]+>', '', q).strip()
        clean_a = _re.sub(r'<[^>]+>', '', a).strip()
        if clean_q and clean_a:
            faq_pairs.append((clean_q, clean_a))

    # ── 2. Build JSON-LD blocks ──
    import json as _json

    organization_ld = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Sale91.com",
        "alternateName": "BulkPlainTshirt.com",
        "url": "https://sale91.com",
        "logo": "https://www.bulkplaintshirt.com/catalog/img/logo.png",
        "description": "B2B plain t-shirt manufacturer & supplier. Own knitted blank wears from Tiruppur.",
        "address": {
            "@type": "PostalAddress",
            "addressLocality": "Tiruppur",
            "addressRegion": "Tamil Nadu",
            "addressCountry": "IN"
        },
        "contactPoint": {
            "@type": "ContactPoint",
            "url": "https://sale91.com",
            "contactType": "sales"
        }
    }

    breadcrumb_ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://www.bulkplaintshirt.com"},
            {"@type": "ListItem", "position": 2, "name": title, "item": blog_url}
        ]
    }

    # Person schema (E-E-A-T — Experience, Expertise, Authoritativeness, Trustworthiness).
    # Google and AI search engines reward content authored by named, credentialed experts
    # over content from a faceless brand. Linking author → business → sameAs (social
    # accounts) builds an identity graph search engines can verify.
    person_ld = {
        "@context": "https://schema.org",
        "@type": "Person",
        "@id": "https://www.bulkplaintshirt.com/#ketu-r",
        "name": "Ketu R",
        "jobTitle": "Founder & B2B Textile Manufacturing Expert",
        "description": "17+ years in B2B plain t-shirt manufacturing. Founder of Own Knitted Blank Wears (Sale91.com / BulkPlainTshirt.com), which knits its own fabric in Tiruppur and ships PAN-India from its Delhi warehouse.",
        "image": "https://www.bulkplaintshirt.com/catalog/img/ketu-author.webp",
        "url": "https://www.bulkplaintshirt.com/",
        "worksFor": {
            "@type": "Organization",
            "name": "Own Knitted Blank Wears",
            "alternateName": ["Sale91.com", "BulkPlainTshirt.com"],
            "url": "https://www.bulkplaintshirt.com/"
        },
        "knowsAbout": [
            "Plain t-shirt manufacturing",
            "GSM fabric selection",
            "DTG / DTF / screen printing",
            "Bulk textile wholesale",
            "B2B printing business"
        ],
        "sameAs": [
            "https://www.youtube.com/@BulkPlainTshirt_com",
            "https://www.instagram.com/bulkplaintshirt_com/",
            "https://www.facebook.com/ownknitted/"
        ]
    }

    article_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "description": description[:160] if description else title,
        "url": blog_url,
        "datePublished": today,
        "dateModified": today,
        "author": {"@id": "https://www.bulkplaintshirt.com/#ketu-r"},  # → Person above
        "publisher": {
            "@type": "Organization",
            "name": "BulkPlainTshirt.com",
            "logo": {"@type": "ImageObject", "url": "https://www.bulkplaintshirt.com/catalog/img/logo.png"}
        },
        "image": og_image_url or "https://www.bulkplaintshirt.com/catalog/img/logo.png",
        "mainEntityOfPage": {"@type": "WebPage", "@id": blog_url}
    }

    # Speakable schema — tells voice assistants (Google Assistant, Alexa, Siri) which
    # parts of the page to read aloud when a user asks a related voice query. The FAQ
    # section is ideal — it's already Q&A format and reads naturally.
    speakable_ld = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "@id": blog_url + "#webpage",
        "url": blog_url,
        "speakable": {
            "@type": "SpeakableSpecification",
            "cssSelector": [".faq-question", ".faq-answer", "h1"]
        }
    }

    product_ld = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Premium Plain T-Shirts (Wholesale)",
        "description": "Bio-washed, pre-shrunk plain t-shirts for printing businesses. 180-220 GSM, own knitted from Tiruppur.",
        "brand": {"@type": "Brand", "name": "Sale91.com"},
        "url": "https://sale91.com",
        "image": "https://www.bulkplaintshirt.com/catalog/img/logo.png",
        "offers": {
            "@type": "AggregateOffer",
            "lowPrice": "65",
            "highPrice": "250",
            "priceCurrency": "INR",
            "availability": "https://schema.org/InStock",
            "seller": {"@type": "Organization", "name": "Sale91.com"}
        }
        # NOTE: NO aggregateRating here. A hardcoded 4.5/1050-review rating with no
        # real on-page reviews is a Google structured-data policy violation
        # ("spammy structured markup") that risks a sitewide manual action.
    }

    ld_blocks = [organization_ld, person_ld, breadcrumb_ld, article_ld, speakable_ld, product_ld]

    # VideoObject — the page embeds a YouTube video as a thumbnail card (no iframe,
    # by design), so without this Google sees a bare link and can't read the video
    # metadata. embedUrl is the correct/sufficient URL signal for a YouTube-hosted
    # video (no contentUrl — YouTube exposes no raw file). uploadDate gets a fixed
    # IST time only when `today` is a bare YYYY-MM-DD, so the repair path (which
    # passes a full date) never double-appends. Guarded on vid_id: no id → no block.
    if vid_id:
        video_object_ld = {
            "@context": "https://schema.org",
            "@type": "VideoObject",
            "name": title,
            "description": (description[:200] if description else title),
            "thumbnailUrl": [f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"],
            "uploadDate": (f"{today}T09:00:00+05:30" if len(str(today)) == 10 else str(today)),
            "embedUrl": f"https://www.youtube.com/embed/{vid_id}",
        }
        ld_blocks.append(video_object_ld)

    # HowTo schema — best-effort detection. If the blog contains step-style headings
    # (h2/h3 starting with "Step", or 3+ ordered-list <li> items inside the main
    # content), emit a HowTo block. Google rewards HowTo with rich results.
    step_heading_pattern = _re.compile(r'<h[23][^>]*>\s*(?:Step\s+\d+[:.]|\d+\.\s+)([^<]+)</h[23]>', _re.IGNORECASE)
    step_matches = step_heading_pattern.findall(html_content)
    if len(step_matches) >= 3:
        howto_ld = {
            "@context": "https://schema.org",
            "@type": "HowTo",
            "name": title,
            "description": description[:160] if description else title,
            "image": og_image_url or "https://www.bulkplaintshirt.com/catalog/img/logo.png",
            "step": [
                {
                    "@type": "HowToStep",
                    "position": i + 1,
                    "name": _re.sub(r'<[^>]+>', '', step).strip()[:120],
                    "url": f"{blog_url}#step-{i+1}",
                }
                for i, step in enumerate(step_matches[:10])  # cap at 10 steps
            ]
        }
        ld_blocks.append(howto_ld)

    # FAQPage schema (only if FAQs were found)
    if faq_pairs:
        faq_ld = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": q,
                    "acceptedAnswer": {"@type": "Answer", "text": a}
                }
                for q, a in faq_pairs
            ]
        }
        ld_blocks.append(faq_ld)

    # Build script tags
    ld_scripts = "\n".join(
        f'<script type="application/ld+json">{_json.dumps(ld, ensure_ascii=False)}</script>'
        for ld in ld_blocks
    )

    # ── 2b. Author E-E-A-T card (HTML) ──
    # Injected before the sticky bottom bar so it sits at the end of every blog
    # body. Mirrors the Person JSON-LD above so search engines see consistency
    # between visible content and structured data. Image path is overrideable —
    # if /imges/ketu-author.webp doesn't resolve, the alt text + name still
    # provides full E-E-A-T value.
    author_card = (
        '<section class="author-card" itemscope itemtype="https://schema.org/Person" '
        'style="max-width:800px;margin:40px auto;padding:24px;background:#fff;border-radius:14px;'
        'border:1px solid #e8e8e0;box-shadow:0 2px 8px rgba(0,0,0,0.05);display:flex;'
        'gap:20px;align-items:center;flex-wrap:wrap;">'
        '<img src="https://www.bulkplaintshirt.com/catalog/img/ketu-author.webp" '
        'onerror="this.onerror=null;this.src=\'https://www.bulkplaintshirt.com/catalog/img/logo.png\';this.style.padding=\'12px\';this.style.background=\'#fffbe6\';" '
        'alt="Ketu R — Founder, BulkPlainTshirt.com / Sale91.com" '
        'itemprop="image" '
        'style="width:96px;height:96px;border-radius:50%;object-fit:cover;border:3px solid #d4a832;flex-shrink:0;">'
        '<div style="flex:1;min-width:200px;">'
        '<div style="font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:#888;font-weight:600;margin-bottom:4px;">About the Author</div>'
        '<div itemprop="name" style="font-size:18px;font-weight:700;color:#0f3460;">Ketu R</div>'
        '<div itemprop="jobTitle" style="font-size:14px;color:#555;margin-bottom:8px;">Founder, Own Knitted Blank Wears</div>'
        '<div itemprop="description" style="font-size:14px;color:#444;line-height:1.55;">'
        '17+ years in B2B plain t-shirt manufacturing. We knit our own fabric in Tiruppur and ship '
        'PAN-India from our Delhi warehouse to printing businesses across the country. Featured on our '
        '<a href="https://www.youtube.com/@BulkPlainTshirt_com" rel="author noopener" target="_blank" '
        'style="color:#007bff;text-decoration:underline;">YouTube channel</a> with 40K+ subscribers.'
        '</div>'
        '<div style="font-size:12px;color:#999;margin-top:8px;">'
        'Transparency: our articles are AI-assisted drafts built on real production data from our '
        'Tiruppur factory and Delhi warehouse, published by the Sale91.com team.'
        '</div>'
        '</div>'
        '</section>'
    )

    # ── 3. Sticky bottom bar HTML ──
    bottom_bar = (
        '<div style="position:fixed;bottom:0;left:0;width:100%;display:flex;z-index:1000;'
        'box-shadow:0 -2px 8px rgba(0,0,0,0.15);">'
        '<a href="https://sale91.com" style="flex:1;display:flex;align-items:center;justify-content:center;'
        'min-height:50px;background:#1a1a1a;color:#fff;font-size:16px;font-weight:bold;text-decoration:none;">Order Now</a>'
        '<a href="https://whatsapp.sale91.com" style="flex:1;display:flex;align-items:center;justify-content:center;'
        'min-height:50px;background:#25D366;color:#fff;font-size:16px;font-weight:bold;text-decoration:none;">WhatsApp Us</a>'
        '</div>'
    )

    # ── 4. Fix truncated HTML (if max_tokens cut off the output) ──
    if '</body>' not in html_content:
        # Close any open tags and add missing body/html end tags
        html_content += '\n</div></body></html>'
        print(f"   ⚠️ Blog SEO: HTML was truncated — auto-closed tags")
    if '</head>' not in html_content and '<head' in html_content:
        html_content = html_content.replace('<body', '</head>\n<body', 1)

    # ── 4b. Force a correct canonical (www). Claude writes the canonical in the
    # prompt and occasionally drops the "www" — a canonical pointing at a URL that
    # 301-redirects confuses Google's index selection. Own it here, don't trust it.
    canon_tag = f'<link rel="canonical" href="{blog_url}">'
    if _re.search(r'<link[^>]+rel=["\']canonical["\'][^>]*>', html_content, _re.I):
        html_content = _re.sub(r'<link[^>]+rel=["\']canonical["\'][^>]*>', canon_tag, html_content, count=1, flags=_re.I)
    elif '</head>' in html_content:
        html_content = html_content.replace('</head>', f'{canon_tag}\n</head>', 1)

    # ── 5. Inject JSON-LD ──
    if '</head>' in html_content:
        html_content = html_content.replace('</head>', f'{ld_scripts}\n</head>', 1)
    else:
        html_content = html_content.replace('</body>', f'{ld_scripts}\n</body>', 1)

    # ── 6. Inject author card + bottom bar before </body> ──
    # Author card goes BEFORE bottom bar (so it's visible above the sticky footer).
    if 'class="author-card"' not in html_content:
        html_content = html_content.replace('</body>', f'{author_card}\n</body>', 1)
    if 'whatsapp.sale91.com' not in html_content.lower() or 'Order Now' not in html_content:
        html_content = html_content.replace('</body>', f'{bottom_bar}\n</body>', 1)

    # ── 7. Visible byline + date under the H1 (E-E-A-T: real author + freshness).
    # Previously the date/author lived ONLY in JSON-LD; a human-visible byline is
    # a stronger signal for Google's helpful-content system.
    if 'class="bpt-byline"' not in html_content:
        try:
            _d = datetime.strptime((today or "")[:10], "%Y-%m-%d")
            date_disp = _d.strftime("%B %-d, %Y")
        except Exception:
            date_disp = (today or "")[:10]
        byline = (
            '<div class="bpt-byline" style="font-size:13px;color:#666;margin:2px 0 18px;">'
            'By <a href="https://www.bulkplaintshirt.com/#ketu-r" rel="author" '
            'style="color:#0f3460;font-weight:600;text-decoration:none;">Ketu R</a>'
            f'{" · Updated " + date_disp if date_disp else ""}</div>'
        )
        html_content = _re.sub(r'(</h1>)', r'\1\n' + byline, html_content, count=1)

    faq_count = len(faq_pairs)
    ld_count = len(ld_blocks)
    howto_note = " (incl. HowTo)" if len(step_matches) >= 3 else ""
    print(f"   📊 Blog SEO: Injected {ld_count} JSON-LD schemas ({faq_count} FAQs{howto_note}) + author card + sticky bar")
    return html_content


def _load_blog_history_active():
    """Load blog_history.json minus consolidated posts.

    Entries carrying a 'redirect_to' key are duplicate-cluster losers that were
    redirect-consolidated into a winner article — they must stay out of every
    reader-facing surface (sitemap, blog index, RSS, related-links, llms.txt),
    otherwise we keep publishing links to URLs that redirect away."""
    if not os.path.exists(BLOG_HISTORY_FILE):
        return []
    try:
        with open(BLOG_HISTORY_FILE) as f:
            history = json.load(f)
    except Exception:
        return []
    return [h for h in history if not h.get('redirect_to')]


# ── Blog publish gate ──────────────────────────────────────────────────
# June 2026: 35 near-duplicate posts in one month tipped Google's scaled-content
# threshold — new posts land in "Crawled – currently not indexed" and the
# suppression began spilling onto the /catalog/ money pages. Two rules stop it
# from recurring:
#   1. Cadence: blog posts only Mon/Wed/Fri (Shorts/Reels stay daily — YouTube
#      and Instagram don't apply Google's duplication penalty).
#   2. Cluster dedup: never publish a second article targeting the same search
#      query as an existing one — Google indexes one winner per query cluster
#      and files the rest under "Crawled – not indexed", dragging section-wide
#      quality signals down with them.
BLOG_WEEKDAYS = {int(d) for d in os.environ.get("BLOG_WEEKDAYS", "0,2,4").split(",") if d.strip().isdigit()}
FORCE_BLOG = os.environ.get("FORCE_BLOG", "").strip() in ("1", "true", "yes")

_CLUSTER_DROP_TOKENS = {
    # storytelling noise — never part of the search query a page targets
    'mistake', 'mistakes', 'galti', 'galtiyan', 'lost', 'loss', 'ruined', 'gone',
    'wrong', 'failed', 'fail', 'trap', 'secret', 'truth', 'real', 'story', 'nobody',
    'warns', 'warning', 'shocking', 'why', 'how', 'what', 'when', 'which', 'this',
    'that', 'your', 'his', 'her', 'every', 'never', 'always', 'stop', 'avoid',
    # hindi fillers (latin script)
    'kya', 'kyu', 'kyun', 'kaise', 'hota', 'hoti', 'hain', 'hai', 'mein', 'nahi',
    'mat', 'karo', 'kare', 'wala', 'wale', 'aur', 'par', 'yeh', 'woh',
    # cities — swapped between near-duplicates to fake variety
    'delhi', 'mumbai', 'pune', 'surat', 'jaipur', 'ludhiana', 'tiruppur',
    'bangalore', 'bengaluru', 'kolkata', 'chennai', 'hyderabad', 'ahmedabad',
    'noida', 'gurgaon', 'indore',
    # generic filler — appears in nearly every post, carries no cluster signal
    'india', 'indian', 'business', 'owner', 'customer', 'buyer', 'supplier',
    'guide', 'tips', 'complete', 'best', 'top',
    'tshirt', 'tshirts', 'shirt', 'shirts', 'tee', 'tees',
    'piece', 'pieces', 'pcs',
}


def _blog_cluster_tokens(*texts):
    """Reduce topic/title/slug text to the core keyword set identifying which
    search-query cluster an article targets. GSM values stay fused to their
    number (180gsm vs 240gsm are different products = different clusters);
    every other bare number (₹ figures, piece counts, wash counts) is
    storytelling noise that near-duplicates swap around to fake variety."""
    blob = ' '.join(t for t in texts if t).lower()
    blob = re.sub(r'(\d+)\s*[-–]?\s*gsm', r' \1gsm ', blob)
    blob = re.sub(r'(?:₹|rs\.?)\s*[\d,.]+\s*(?:k|lakh|lac|crore)?', ' ', blob)
    blob = re.sub(r'\b\d[\d,.]*\s*(?:k|lakh|lac|crore)\b', ' ', blob)
    blob = re.sub(r'\b\d[\d,.]*\b', ' ', blob)
    blob = re.sub(r'[^a-z0-9]+', ' ', blob)
    return {w for w in blob.split() if len(w) >= 3 and w not in _CLUSTER_DROP_TOKENS}


def blog_cluster_collision(topic, title, claude_client=None):
    """Return (colliding_slug, similarity) if an existing article already targets
    the same search-query cluster, else (None, 0.0).

    Token containment ≥ 0.72 = hard collision. 0.45–0.72 = ambiguous → Claude
    judges search intent. Fails open (no collision) on API errors — the hard
    band alone catches the keyword-swap duplicates behind the June pile-up."""
    cand = _blog_cluster_tokens(topic, title)
    if not cand:
        return None, 0.0
    history = []
    if os.path.exists(BLOG_HISTORY_FILE):
        try:
            with open(BLOG_HISTORY_FILE) as f:
                history = json.load(f)
        except Exception:
            history = []
    scored = []
    for h in history:
        if h.get('redirect_to'):
            continue  # already consolidated away — its winner is a separate entry
        h_toks = _blog_cluster_tokens(h.get('topic', ''), h.get('title', ''),
                                      (h.get('slug') or '').replace('-', ' '))
        if not h_toks:
            continue
        overlap = cand & h_toks
        if len(overlap) < 2:
            continue
        containment = len(overlap) / min(len(cand), len(h_toks))
        # Shared GSM tokens are the strongest duplicate signal (the June dupes
        # were "240 GSM ..." rewrites of each other) but long Hindi-mixed token
        # sets dilute them — weight each shared GSM value up explicitly.
        gsm_overlap = sum(1 for t in overlap if re.match(r'^\d{2,3}gsm$', t))
        if gsm_overlap:
            containment = min(1.0, containment + 0.2 * gsm_overlap)
        scored.append((containment, h.get('slug', ''), h.get('title', '')))
    if not scored:
        return None, 0.0
    scored.sort(reverse=True)
    top_sim, top_slug, _ = scored[0]
    if top_sim >= 0.72:
        return top_slug, top_sim
    ambiguous = [s for s in scored[:5] if s[0] >= 0.40]
    if ambiguous and claude_client:
        listing = "\n".join(f"- {s[1]}: {s[2]}" for s in ambiguous)
        try:
            resp = claude_client.messages.create(
                model="claude-opus-4-6", max_tokens=120,
                messages=[{"role": "user", "content": f"""A blog already has these articles:
{listing}

Proposed new article:
TOPIC: {topic}
TITLE: {title}

Would the new article target the SAME primary Google search query as any listed article (i.e. Google would treat them as near-duplicates and index only one)? Different GSM values, different garment types, or genuinely different buyer questions = NOT the same query.

Answer with ONLY the colliding slug, or NONE."""}]
            )
            ans = resp.content[0].text.strip().strip('"')
            if ans and ans.upper() != "NONE":
                for sim, s_slug, _t in ambiguous:
                    if s_slug and s_slug in ans:
                        return s_slug, sim
        except Exception as e:
            print(f"   ⚠️ Cluster-collision judge failed (fail-open): {e}")
    return None, 0.0


def blog_publish_gate(topic, title, claude_client=None):
    """Decide whether today's run publishes a blog post.

    Returns (ok, reason, fallback_slug). fallback_slug points the IG carousel
    at an existing on-topic article when the blog is skipped, so the daily
    carousel cadence survives blog throttling."""
    if FORCE_BLOG:
        return True, "FORCE_BLOG=1 override", None
    ist_now = datetime.now(pytz.timezone(TIMEZONE))
    if ist_now.weekday() not in BLOG_WEEKDAYS:
        slug, _sim = blog_cluster_collision(topic, title)  # no Claude call — only picks the carousel link
        return False, f"cadence throttle — blog days are Mon/Wed/Fri, today is {ist_now.strftime('%A')}", slug
    slug, sim = blog_cluster_collision(topic, title, claude_client=claude_client)
    if slug:
        return False, f"topic-cluster collision with existing article '{slug}' (similarity {sim:.2f})", slug
    return True, "fresh cluster", None


def generate_blog_slug(title):
    """Convert a title to a URL-friendly slug."""
    import re
    slug = title.lower().strip()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    slug = slug.strip('-')
    # Cap at 60 chars for clean URLs
    if len(slug) > 60:
        slug = slug[:60].rsplit('-', 1)[0]
    return slug


def get_blog_prompt(topic, title, description, script_english, tags, hook_text, vid_id, image_urls=None, related_posts=None, prev_post=None, vid_url=None, slug=None):
    """Build the Claude prompt for generating a full SEO blog post HTML."""
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")
    slug = slug or generate_blog_slug(title)
    blog_url = f"{BLOG_BASE_URL}/p/{slug}.html"
    has_video = bool(vid_id)
    # The bot's published Shorts return "Forbidden" from oEmbed → iframe
    # embeds render as "Video unavailable / Playback on other websites has been
    # disabled" on every blog. Cause is per-video / Shorts-specific (other
    # videos on different channels embed fine via oEmbed). Replacing the iframe
    # with a clickable thumbnail card kills the broken-embed error, keeps a
    # backlink to YouTube, and drives click traffic into the Short feed.
    #
    # vid_url is passed for long-form videos (Sunday recap from main channel)
    # — those use https://www.youtube.com/watch?v=... and CAN actually be
    # embedded, but we use the same thumbnail card for visual consistency.
    yt_short_url = vid_url or f"https://youtube.com/shorts/{vid_id}"
    # Primary thumbnail: the blog's AI-generated hero image (always available on
    # S3 immediately the blog is published). YouTube's Shorts thumbnails take
    # 1-24h to generate — using them as the primary source means the video card
    # shows a gray YT placeholder for the first 24h on every newly-published
    # blog. Hero image is topical, on-brand, and always loads. Fallback chain
    # via onerror: hero → YT hqdefault → solid dark background.
    if image_urls:
        primary_thumb = image_urls[0]  # blog hero, e.g. https://.../p/{slug}-hero.webp
    else:
        primary_thumb = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
    yt_thumbnail_url = primary_thumb
    yt_thumbnail_fallback = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"

    image_instructions = ""
    og_image_url = "https://www.bulkplaintshirt.com/catalog/img/logo.png"
    if image_urls:
        og_image_url = image_urls[0]  # Use first AI image for social sharing
        img_list = "\n".join(f"   - Image {i+1}: {url}" for i, url in enumerate(image_urls))
        image_instructions = f"""
7. IMAGES:
   You have {len(image_urls)} AI-generated images to place throughout the article. Use these EXACT URLs:
{img_list}
   - Place the FIRST image as a hero/banner right after the H1 title (full width, with a relevant alt text)
   - Place remaining images between content sections where they add visual context
   - Use proper <img> tags with descriptive alt text (SEO-friendly, include keywords)
   - Add loading="lazy" to all images except the hero
   - The hero <img> must have fetchpriority="high" (it is the mobile LCP element) and NO loading attribute
   - EVERY <img> must carry explicit width/height attributes so the browser reserves space (zero CLS):
     hero = width="1280" height="720"; other images = width="1024" height="768"
   - Style images: width:100%; height:auto; border-radius:12px; margin:20px 0;
   - Wrap each image in a <figure> with a <figcaption> describing what's shown
   - IMPORTANT: Use {og_image_url} as the og:image and twitter:image in meta tags
"""

    related_instructions = ""
    if related_posts:
        links_list = "\n".join(f"   - {rp['title']}: https://www.bulkplaintshirt.com/p/{rp['slug']}.html (hero image: https://www.bulkplaintshirt.com/p/{rp['slug']}-hero.webp)" for rp in related_posts)
        # Inline link pool — Claude picks the most contextually relevant 2-3 to embed in body
        inline_pool = "\n".join(f"   - \"{rp['title']}\" → https://www.bulkplaintshirt.com/p/{rp['slug']}.html" for rp in related_posts[:7])
        related_instructions = f"""
8. INTERNAL LINKING — TWO REQUIRED PLACEMENTS (CRITICAL for indexing):

   A) INLINE CONTEXTUAL LINKS (highest SEO value — pass page authority):
      Within the body paragraphs, weave in 2-3 natural <a> links to these related articles
      where the topic is contextually relevant. Example: if discussing GSM and a related
      post is about GSM mistakes, link "GSM mistakes" inline to that post's URL.

      AVAILABLE LINK POOL (pick the 2-3 most contextually relevant):
{inline_pool}

      RULES for inline links:
      - Anchor text must be 2-5 words from the related post's topic (NOT the full title)
      - Embed naturally in a sentence — never "click here" or "read more"
      - Style: color:#007bff, text-decoration:underline (so they're visually obvious)
      - DO NOT cluster all inline links in one section — spread across the article body
      - These are SEPARATE from the cards section in (B) below

   B) "MORE ARTICLES" CARD SECTION (after FAQ, before closing CTA):
      Show ALL these as VISUAL CARDS:
{links_list}
      - Responsive flex/grid: 2-3 cards per row on desktop, 1 per row on mobile
      - Each card: hero image (height:140px, object-fit:cover, border-radius:8px 8px 0 0, loading="lazy", onerror="this.style.display='none'") + clickable title link (font-size:14px, font-weight:600, padding:12px)
      - Card styling: background #fff, border-radius:10px, box-shadow:0 2px 6px rgba(0,0,0,0.06), border:1px solid #e8e8e0
      - Wrap container: max-width:800px, margin:30px auto
      - Section heading: H2 "More Articles"
"""

    prev_next_instructions = ""
    if prev_post and prev_post.get('slug'):
        prev_url = f"https://www.bulkplaintshirt.com/p/{prev_post['slug']}.html"
        prev_title_safe = prev_post['title'].replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
        prev_next_instructions = f"""
9. PREV-POST NAVIGATION (place at the very BOTTOM, after "More Articles"):
   Add this exact block (helps Google's crawler walk the archive — chronological link graph):
   <nav class="post-nav" style="max-width:800px;margin:30px auto;padding:20px;border-top:2px solid #d4a832;text-align:center;">
     <a href="{prev_url}" rel="prev" style="color:#1a5c2e;font-weight:600;text-decoration:none;font-size:15px;">← Previous Article: {prev_title_safe}</a>
     <br><br>
     <a href="https://www.bulkplaintshirt.com/p/index.html" style="color:#007bff;font-size:14px;">View All Articles →</a>
   </nav>
"""

    # Video references are conditional: legacy thin-page rewrites have no video,
    # so we drop the video meta lines + the mandatory thumbnail card for those.
    if has_video:
        video_meta_lines = (
            f"VIDEO TITLE: {title}\n"
            f"VIDEO DESCRIPTION: {description}\n"
            f"VIDEO SCRIPT (English): {script_english}\n"
            f"HOOK TEXT: {hook_text}\n"
        )
        video_url_lines = f"YOUTUBE SHORT: {yt_short_url}\nTHUMBNAIL: {yt_thumbnail_url}\n"
        task_source = "based on this YouTube Shorts video topic"
        content_directive = "Expand the video script into a detailed, informative article"
        video_card_req = f"""   - MANDATORY: Include a "Watch the Video" section BEFORE the FAQ section with this EXACT clickable thumbnail card (NOT an iframe — Shorts can't be embedded reliably and showed "Video unavailable" on every blog):
     <a href="{yt_short_url}" target="_blank" rel="noopener" style="display:block;max-width:360px;margin:30px auto;text-decoration:none;border-radius:12px;overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,0.15);background:#000;position:relative;">
       <img src="{yt_thumbnail_url}" alt="Watch on YouTube — {title}" loading="lazy" onerror="if(this.src!='{yt_thumbnail_fallback}'){{this.src='{yt_thumbnail_fallback}';}}else{{this.style.display='none';this.parentNode.style.background='linear-gradient(135deg,#1a1a1a 0%,#3d3d3d 100%)';this.parentNode.style.minHeight='200px';}}" style="width:100%;height:auto;display:block;">
       <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:68px;height:48px;background:rgba(255,0,0,0.9);border-radius:14px;display:flex;align-items:center;justify-content:center;">
         <svg width="28" height="28" viewBox="0 0 24 24" fill="white"><polygon points="6,4 20,12 6,20"/></svg>
       </div>
       <div style="background:#1a1a1a;color:#fff;padding:12px 16px;font-size:14px;font-weight:600;">▶ Watch on YouTube</div>
     </a>
     Do NOT use an <iframe> — it WILL break. The clickable thumbnail above is the ONLY acceptable video element.
"""
    else:
        video_meta_lines = ""
        video_url_lines = ""
        task_source = "on this topic for India's B2B plain t-shirt buyers"
        content_directive = "Write a detailed, original, genuinely useful article on this topic grounded ONLY in the real business facts from BUSINESS CONTEXT and verifiable textile knowledge — never invented specifics"
        video_card_req = ""

    return f"""You are an expert SEO content writer for Sale91.com (BulkPlainTshirt.com), India's leading B2B plain t-shirt manufacturer.

BUSINESS CONTEXT:
{BUSINESS_CONTEXT}

YOUR TASK: Write a comprehensive, 2000+ word SEO blog post {task_source}.

TOPIC: {topic}
{video_meta_lines}TAGS: {', '.join(tags) if tags else 'none'}

BLOG URL: {blog_url}
{video_url_lines}DATE: {today}

OUTPUT FORMAT: Return ONLY the complete HTML document (from <!DOCTYPE html> to </html>). No markdown code fences. No explanation.

REQUIREMENTS:

1. CONTENT (2000+ words):
   - {content_directive}
   - Use H1 for main title, H2 for major sections, H3 for subsections
   - Write in professional English with occasional Hinglish terms where natural (like "GSM", industry terms)
   - Include practical tips and comparisons grounded in the REAL data from BUSINESS CONTEXT above
     (real GSM range 180-430, real MOQ, real discounts, real 30-day throughput, real Tiruppur
     factory + Delhi Khanpur warehouse)
   - TRUTHFULNESS — NON-NEGOTIABLE (Google's scaled-content policy flags fabricated specifics,
     and invented "real customer" stories are why this blog's indexing collapsed in June 2026):
     * NEVER invent customer stories, named customers, customer cities, or "this really happened"
       incident framing. No "a printer from Pune lost ₹40,000" unless it is a documented fact.
     * NEVER invent specific ₹ losses, order quantities, percentages, or dates. Every specific
       number must come from BUSINESS CONTEXT or be a verifiable industry fact.
     * Hypotheticals are allowed ONLY when clearly framed as hypothetical ("Suppose you order
       500 pieces...") — never dressed up as a real event.
   - INFORMATION GAIN — each article must contain at least ONE element found in no other post:
     a concrete spec/decision table built from our real product data, an original step-by-step
     checklist, or a genuine process detail from our own manufacturing/warehouse operations.
     An article that only re-says what other articles already say should not exist.
   - Do NOT re-explain GSM / bio-wash / pre-shrunk basics from scratch — link the first mention
     to https://www.bulkplaintshirt.com/p/FQA.html (one sentence max, then move on)
   - Mention Sale91.com naturally 2-3 times with links to https://sale91.com
   - Reference the product catalog: https://www.bulkplaintshirt.com/catalog/
{video_card_req}   - End with a strong CTA section linking to Sale91.com

2. FAQ SECTION (3-6 Q&As):
   - Questions a buyer would actually type into Google about THIS article's specific topic
   - Do NOT reuse the generic GSM/wholesale/MOQ boilerplate questions that appear on other
     posts — every FAQ must be answerable only by this article
   - Each answer should be 2-4 sentences

3. HTML STRUCTURE:
   - Full <!DOCTYPE html> document
   - Mobile-responsive with viewport meta tag
   - Inline CSS in <style> tag (no external stylesheets) — clean, modern, readable design
   - Max-width container (800px) for content, full-width for header and bottom bar

   HEADER (fixed/sticky at top of page, z-index 1000):
   - Full-width yellow/gold background (use #d4a832 or similar warm gold)
   - "BulkPlainTshirt.com" as a clickable <a> link to https://www.bulkplaintshirt.com — large bold dark text (#1a1a1a), centered, no underline
   - Below it in smaller text: "Own Knitted Blank Wears | <a href='https://sale91.com'>sale91.com</a>"
   - Padding: 12px top/bottom
   - This header must always stay visible at the top when scrolling

   STICKY BOTTOM BAR: Do NOT add any bottom bar — it is injected automatically by the system.
   - Add padding-bottom: 60px to body so content is not hidden behind the auto-injected sticky bar
   - Add padding-top: 80px to body so content is not hidden behind sticky header

   CONTENT AREA:
   - Clean white background, max-width 800px, centered with auto margins
   - Good typography: 16px base font-size, line-height 1.7, font-family system-ui/sans-serif
   - Use DIFFERENT colors for different text elements to create visual hierarchy:
     * H1: dark green (#1a5c2e), bold, font-size 28px
     * H2: dark navy (#0f3460), font-size 22px
     * H3: dark charcoal (#2d2d2d), font-size 18px
     * Body text: #333333 (soft dark)
     * Links: #007bff (blue) with hover underline
     * FAQ questions: #1a5c2e (green), bold
     * Breadcrumb text: #666 (gray), small font
     * Blockquotes/highlights: left border #d4a832 (gold), background #fffdf5
   - Proper spacing between sections (margin 1.5em)
   - Lists should use consistent styling: padding-left 30px, li margin 8px 0, bullet color #d4a832
   - Responsive YouTube embed (16:9 aspect ratio with padding trick)
   - Breadcrumb navigation below header: Home > [Post Title] (do NOT include "Blog" in breadcrumb)

   FOOTER (above the sticky bottom bar):
   - Simple Sale91.com branding and links
   - Keep minimal so it does not clash with sticky bar

4. META TAGS:
   - <title> with " | BulkPlainTshirt.com" suffix
   - meta description (150-160 chars, compelling)
   - canonical URL: {blog_url}
   - Open Graph: og:title, og:description, og:url, og:type=article, og:image (see below)
   - Twitter card meta tags (twitter:image same as og:image)

5. STRUCTURED DATA: Do NOT include any JSON-LD script tags — they will be injected automatically by the system.

6. ADDITIONAL:
   - Add a <link rel="author" href="https://sale91.com"> tag
   - Add a comment <!-- Generated by Sale91.com Blog Bot --> at the top
   - Make sure all JSON-LD is valid JSON (no trailing commas, proper escaping)
   - Total HTML should be well-formatted and readable
{image_instructions}{related_instructions}{prev_next_instructions}
CRITICAL CHECKLIST — your HTML MUST contain ALL of these:
   ✓ Sticky gold header at top (position:fixed) with clickable BulkPlainTshirt.com link
   ✓ YouTube video embed before FAQ section
   ✓ FAQ section with faq-question and faq-answer CSS classes on divs
   ✓ 2-3 INLINE contextual <a> links to related articles within body paragraphs (NOT just in cards)
   ✓ "More Articles" card section after FAQ (if related posts provided)
   ✓ Previous-article navigation block at the very bottom (if provided)
   ✓ body padding-top:80px and padding-bottom:60px
   (Note: JSON-LD schemas and sticky bottom bar are injected automatically — do NOT add them)

REMEMBER: Output ONLY the raw HTML. No markdown fences. No explanation before or after."""


def _generate_image_replicate(prompt, aspect_ratio):
    """Generate a single image via Replicate FLUX Dev. Returns image bytes or None."""
    import replicate
    output = replicate.run(
        "black-forest-labs/flux-dev",
        input={
            "prompt": prompt,
            "num_outputs": 1,
            "aspect_ratio": aspect_ratio,
            "output_format": "webp",
            "output_quality": 85,
            "guidance": 3.5,
        },
    )
    img_output = output[0] if isinstance(output, list) else output
    if hasattr(img_output, 'read'):
        return img_output.read()
    elif isinstance(img_output, str) and img_output.startswith("http"):
        from urllib.request import urlopen
        return urlopen(img_output).read()
    return None


def _generate_image_fal(prompt, aspect_ratio):
    """Generate a single image via fal.ai FLUX Dev. Returns image bytes or None."""
    import fal_client
    # fal.ai uses different aspect ratio format — same as Replicate (e.g. "16:9")
    result = fal_client.subscribe(
        "fal-ai/flux/dev",
        arguments={
            "prompt": prompt,
            "num_images": 1,
            "image_size": {"width": 1280, "height": 720} if aspect_ratio == "16:9" else {"width": 1024, "height": 768},
            "enable_safety_checker": False,
        },
    )
    images = result.get("images", [])
    if images and images[0].get("url"):
        resp = requests.get(images[0]["url"], timeout=60)
        resp.raise_for_status()
        return resp.content
    return None


def generate_blog_images(video_prompts, topic, slug, cost_tracker=None):
    """Generate 3 AI images for the blog post using Replicate FLUX Dev.
    Falls back to fal.ai FLUX Dev if Replicate fails.
    Uses the Veo video prompts as inspiration for image descriptions.
    Returns list of (image_bytes, filename) tuples, or empty list on failure."""
    has_replicate = bool(os.environ.get("REPLICATE_API_TOKEN"))
    has_fal = bool(os.environ.get("FAL_KEY"))

    if not has_replicate and not has_fal:
        print("   📷 Blog images: No REPLICATE_API_TOKEN or FAL_KEY, skipping image generation")
        return []

    # Build 3 image prompts from video scene descriptions
    base_prompts = []
    if video_prompts and len(video_prompts) > 0:
        # Pick prompts 1 (hook), 3 (explanation), 5 (resolution) — or whatever is available
        indices = [0, 2, 4] if len(video_prompts) >= 5 else list(range(min(3, len(video_prompts))))
        for idx in indices[:3]:
            if idx < len(video_prompts) and video_prompts[idx].strip():
                base_prompts.append(video_prompts[idx])

    # Fallback: generic prompts based on topic
    while len(base_prompts) < 3:
        fallbacks = [
            f"Professional photograph of Indian textile manufacturing, {topic}, cotton t-shirts, factory setting, bright lighting",
            f"Close-up of premium cotton fabric, {topic}, showing GSM texture and quality, soft lighting, detailed",
            f"Indian wholesale t-shirt business, {topic}, stacked colorful plain t-shirts, warehouse, commercial photography",
        ]
        base_prompts.append(fallbacks[len(base_prompts)])

    # Enhance prompts for still image generation
    image_style = "professional commercial photography, high quality, sharp focus, well-lit, 4K, realistic"

    results = []
    replicate_failed = False  # sticky flag: if Replicate fails, switch to fal.ai for remaining

    for i, base in enumerate(base_prompts[:3]):
        prompt = f"{base}. {image_style}"
        filename = "hero.webp" if i == 0 else f"img{i}.webp"
        aspect = "16:9" if i == 0 else "4:3"
        img_bytes = None

        # Try Replicate first (unless it already failed)
        if has_replicate and not replicate_failed:
            try:
                print(f"   📷 Blog images: Generating {filename} via Replicate FLUX Dev...")
                img_bytes = _generate_image_replicate(prompt, aspect)
            except Exception as e:
                print(f"   ⚠️ Blog images: Replicate failed for {filename}: {e}")
                replicate_failed = True  # switch to fal.ai for remaining images

        # Fallback to fal.ai
        if not img_bytes and has_fal:
            try:
                provider = "fal.ai FLUX Dev (fallback)" if has_replicate else "fal.ai FLUX Dev"
                print(f"   📷 Blog images: Generating {filename} via {provider}...")
                img_bytes = _generate_image_fal(prompt, aspect)
            except Exception as e:
                print(f"   ⚠️ Blog images: fal.ai failed for {filename}: {e}")

        if img_bytes and len(img_bytes) > 1000:
            results.append((img_bytes, filename))
            print(f"   📷 Blog images: {filename} generated ({len(img_bytes)//1024}KB)")
        elif img_bytes:
            print(f"   ⚠️ Blog images: {filename} too small, skipping")
        else:
            print(f"   ⚠️ Blog images: {filename} generation failed on all providers")

    if results and cost_tracker:
        cost_tracker.track_blog_images(len(results))

    print(f"   📷 Blog images: {len(results)}/3 images generated successfully")
    return results


def generate_blog_post(claude_client, cost_tracker, topic, title, description,
                       script_english, tags, hook_text, vid_id, vid_url,
                       video_prompts=None, force_slug=None):
    """Generate a full SEO blog post HTML using Claude Sonnet.
    force_slug keeps a specific URL (used when rewriting a thin legacy page in place).
    Returns (html_content, slug, blog_url, blog_images) or (None, None, None, []) on failure."""
    print("   📝 Blog: Generating SEO article with images...")

    slug = force_slug or generate_blog_slug(title)
    blog_url = f"{BLOG_BASE_URL}/p/{slug}.html"

    # Step 1: Generate AI images for the blog (if Replicate available)
    blog_images = generate_blog_images(video_prompts, topic, slug, cost_tracker)

    # Build image URLs for the HTML (so Claude can embed them)
    image_urls = []
    for _, filename in blog_images:
        image_urls.append(f"{BLOG_BASE_URL}/p/{slug}-{filename}")

    # Step 2: Load posts for internal linking — TAG-OVERLAP based, not chronological.
    # Google rewards topic-clustered linking. Picking last-5-published gives weak
    # signal because random topics get linked together. Score by Jaccard similarity
    # of tags + slug-keyword overlap, fall back to recency only if nothing matches.
    related_posts = []
    prev_post = None  # chronologically previous (for prev/next nav)
    try:
        history = _load_blog_history_active()
        if history:
            history_sorted = sorted(history, key=lambda h: h.get('date', ''), reverse=True)
            for h in history_sorted:
                if h.get('slug') != slug:
                    prev_post = {"title": h.get('title', ''), "slug": h.get('slug', '')}
                    break

            current_tags = set(t.lower().strip() for t in (tags or []) if t)
            current_slug_words = set(re.findall(r'[a-z]{4,}', slug.lower()))
            scored = []
            for h in history:
                if h.get('slug') == slug:
                    continue
                h_tags = set(t.lower().strip() for t in h.get('tags', []) if t)
                h_slug_words = set(re.findall(r'[a-z]{4,}', h.get('slug', '').lower()))
                tag_overlap = len(current_tags & h_tags)
                slug_overlap = len(current_slug_words & h_slug_words)
                score = tag_overlap * 3 + slug_overlap  # tags weighted 3x
                if score > 0:
                    scored.append((score, h))
            scored.sort(key=lambda x: -x[0])
            related_posts = [
                {"title": h["title"], "slug": h["slug"], "tags": h.get("tags", [])}
                for _, h in scored[:7]
            ]
            # Fallback: if no topic match, use 5 most recent
            if not related_posts:
                related_posts = [
                    {"title": h["title"], "slug": h["slug"]}
                    for h in history_sorted if h.get('slug') != slug
                ][:5]
    except Exception as e:
        print(f"   ⚠️ Blog: related_posts selection error: {e}")

    # Step 3: Generate blog HTML with image URLs + related posts embedded
    prompt = get_blog_prompt(topic, title, description, script_english, tags, hook_text, vid_id, image_urls, related_posts, prev_post=prev_post, vid_url=vid_url)

    try:
        resp = claude_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16000,
            messages=[{"role": "user", "content": prompt}]
        )
        html_content = resp.content[0].text.strip()

        # Track cost
        if cost_tracker and hasattr(cost_tracker, 'track_claude_call'):
            cost_tracker.track_claude_call("sonnet", resp.usage.input_tokens, resp.usage.output_tokens)

        # Clean up if Claude wrapped in markdown fences
        if html_content.startswith("```"):
            html_content = html_content.split("\n", 1)[1].rsplit("```", 1)[0]

        # Basic validation
        if "<!DOCTYPE" not in html_content and "<html" not in html_content:
            print("   ⚠️ Blog: Claude didn't return valid HTML")
            return None, None, None, []

        # Inject JSON-LD schemas + sticky bottom bar (reliable, not prompt-dependent)
        today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")
        first_image_url = image_urls[0] if image_urls else None
        html_content = inject_blog_seo(html_content, title, description, blog_url, today, slug, og_image_url=first_image_url, vid_id=vid_id, vid_url=vid_url)

        word_count = len(html_content.split())
        print(f"   📝 Blog: Generated ~{word_count} words, slug: {slug}")
        print(f"   📝 Blog: URL will be {blog_url}")
        print(f"   📝 Blog: {len(blog_images)} images to upload")

        return html_content, slug, blog_url, blog_images

    except Exception as e:
        print(f"   ⚠️ Blog generation failed: {e}")
        return None, None, None, []


# Manager WhatsApp number used in the "Notify Manager" link on the Reddit
# drafts page. Edit if monitoring should go to a different number.
REDDIT_MANAGER_WHATSAPP = "919336695049"

# Reddit drafts are published to a dedicated GitHub Pages repo (NOT the
# customer-facing bulkplaintshirt.com website) — keeps internal tooling
# separate from production SEO content.
REDDIT_DRAFTS_REPO = "thakyanamtumhara/reddit-drafts"
REDDIT_DRAFTS_PAGE_URL = "https://thakyanamtumhara.github.io/reddit-drafts/"

# Retention window for Reddit draft archives. We keep the last N days of
# dated archives (both S3 HTML files and local JSON files) and delete older.
# 30 = roughly 1 month of historical posts for audit / re-use.
REDDIT_RETENTION_DAYS = 30


def _publish_reddit_to_github_pages(draft: dict, today: str, keep_days: int = REDDIT_RETENTION_DAYS):
    """Push today's Reddit draft to the GitHub Pages repo and prune archives
    older than keep_days. Returns the public page URL on success, None on failure.

    Strategy:
    1. Shallow-clone the reddit-drafts repo to /tmp using GH_PAT.
    2. Write today's archive/YYYY-MM-DD.html.
    3. Delete archive/*.html older than keep_days.
    4. List remaining archives (excluding today), render index.html with a
       "Previous posts" section linking to them.
    5. Commit + push. If no changes (idempotent re-run), silently no-op.

    GH_PAT must have `Contents: write` on the reddit-drafts repo. Failures
    are non-fatal — the daily pipeline continues regardless.
    """
    # REDDIT_DRAFTS_PAT is preferred — a fine-grained PAT scoped specifically to
    # the reddit-drafts repo. Falls back to GH_PAT if the dedicated one isn't set
    # (e.g. local dev runs). The old GH_PAT was created before the reddit-drafts
    # repo existed and didn't have write access to it, so the dedicated secret
    # fixes the 403 errors we saw in production.
    gh_pat = os.environ.get("REDDIT_DRAFTS_PAT", "") or os.environ.get("GH_PAT", "")
    if not gh_pat:
        print("   ⚠️ Reddit: REDDIT_DRAFTS_PAT / GH_PAT not set — skipping GitHub Pages publish")
        return None

    import subprocess
    import shutil
    repo_dir = "/tmp/reddit-drafts-repo"
    shutil.rmtree(repo_dir, ignore_errors=True)

    clone_url = f"https://x-access-token:{gh_pat}@github.com/{REDDIT_DRAFTS_REPO}.git"
    try:
        subprocess.run(
            ["git", "clone", "--depth=1", "--quiet", clone_url, repo_dir],
            check=True, capture_output=True
        )

        archive_dir = os.path.join(repo_dir, "archive")
        os.makedirs(archive_dir, exist_ok=True)

        # 1. Prune old archives first (so they don't show up in the index list)
        cutoff = (datetime.now(pytz.timezone(TIMEZONE)) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        _DATE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})\.html$')
        pruned = 0
        for fname in os.listdir(archive_dir):
            m = _DATE_RE.match(fname)
            if m and m.group(1) < cutoff:
                try:
                    os.remove(os.path.join(archive_dir, fname))
                    pruned += 1
                except Exception:
                    pass
        if pruned:
            print(f"   🧹 Reddit: Pruned {pruned} archive(s) older than {cutoff}")

        # 2. Write today's archive — render WITHOUT archive list (each dated
        #    page is just that day's content, no recursive "previous" list)
        today_archive_html = _render_reddit_html(draft, today, archive_dates=None)
        archive_path = os.path.join(archive_dir, f"{today}.html")
        with open(archive_path, "w") as f:
            f.write(today_archive_html)

        # 3. Collect remaining archive dates (excluding today), newest-first
        archive_dates = []
        for fname in sorted(os.listdir(archive_dir), reverse=True):
            m = _DATE_RE.match(fname)
            if m and m.group(1) != today:
                archive_dates.append(m.group(1))

        # 4. Re-render index.html (today's draft + archive links at bottom)
        index_html = _render_reddit_html(draft, today, archive_dates=archive_dates)
        with open(os.path.join(repo_dir, "index.html"), "w") as f:
            f.write(index_html)

        # 5. Commit + push
        subprocess.run(["git", "-C", repo_dir, "add", "-A"], check=True, capture_output=True)
        commit = subprocess.run(
            ["git", "-C", repo_dir,
             "-c", "user.email=bot@sale91.com",
             "-c", "user.name=Reddit Drafts Bot",
             "commit", "-q", "-m", f"Daily draft: {today}"],
            capture_output=True
        )
        if commit.returncode == 0:
            subprocess.run(["git", "-C", repo_dir, "push", "--quiet"], check=True, capture_output=True)
            print(f"   ✅ Reddit: Pushed to {REDDIT_DRAFTS_PAGE_URL}")
        else:
            # No diff to commit (idempotent re-run on same day) — also OK
            print(f"   ℹ️ Reddit: No archive changes to push")

        return REDDIT_DRAFTS_PAGE_URL
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b'').decode(errors='replace')[:300]
        print(f"   ⚠️ Reddit: git operation failed: {stderr}")
        return None
    except Exception as e:
        print(f"   ⚠️ Reddit: GitHub Pages publish failed: {e}")
        return None
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)


def _build_phases_tab_html() -> str:
    """Build the System Status tab content from phase_status.json.

    Shows all 4 phases as cards with last-run time, status badge, and
    human-readable details. Updates as the bot writes phase_status.json
    on each Sunday (or daily) run.
    """
    import html as _html
    PHASE_NAMES = {
        1: ("Phase 1: Sunday Saturday-recap blog",
            "Every Sunday, fetches your Saturday upload from the main 40K-sub channel and turns it into a blog post + Reddit draft. Replaces Sunday's autonomous Veo run (saves ~$7.58/Sunday)."),
        2: ("Phase 2: Caption transcript fetch",
            "Best-effort: tries to grab the video's caption track via the YouTube Data API. If captions aren't accessible (most cases without OAuth), falls back to using the video description as the source."),
        3: ("Phase 3: Voice corpus accumulation",
            "Saves each week's source text (transcript or description) into voice_corpus/YYYY-MM-DD.txt. Builds a multi-week sample of your voice and vocabulary."),
        4: ("Phase 4: Voice style injection (daily script gen)",
            "Once 4+ weekly corpus entries exist, daily script generation reads voice_corpus/ and includes your real ending-sentence patterns as style guidance. Bot's voice gets closer to yours over time."),
    }
    status_data = _read_phase_status()
    phases = status_data.get("phases", {})
    last_update = status_data.get("last_update", "—")

    badge_color = {
        "active":      ("#1a5c2e", "#e8f5ed", "✅ Active"),
        "best-effort": ("#9c6500", "#fffbe6", "⚠️ Best-effort"),
        "pending":     ("#666",    "#f0f0eb", "⏳ Pending"),
        "failed":      ("#8b1a1a", "#ffe5e5", "❌ Failed"),
    }

    cards = []
    for pid in (1, 2, 3, 4):
        name, blurb = PHASE_NAMES[pid]
        info = phases.get(str(pid), {})
        st = info.get("status", "pending")
        fg, bg, label = badge_color.get(st, badge_color["pending"])
        last_run = info.get("last_run", "—") or "—"
        if last_run != "—":
            last_run = last_run[:19].replace("T", " ")
        details = _html.escape(info.get("details", "Not yet run."))
        cards.append(f'''
    <div class="phase-card">
      <div class="phase-head">
        <span class="phase-name">{_html.escape(name)}</span>
        <span class="phase-badge" style="color:{fg};background:{bg}">{label}</span>
      </div>
      <p class="phase-blurb">{_html.escape(blurb)}</p>
      <div class="phase-meta">
        <span><strong>Last run:</strong> {_html.escape(last_run)}</span>
        <span><strong>Result:</strong> {_html.escape(info.get("last_result", "—"))}</span>
      </div>
      <div class="phase-details">{details}</div>
    </div>''')

    return f'''<div class="phases-wrap" id="phases-tab" style="display:none">
  <div class="phases-summary">
    <strong>System status</strong> · last update: {_html.escape(last_update or "—")}
  </div>
  {"".join(cards)}
</div>'''


def _render_reddit_html(draft: dict, today: str, archive_dates: list = None) -> str:
    """Render the mobile-first HTML page from a parsed Reddit draft.

    Self-contained — inline CSS + JS only. localStorage persists the "done"
    checkbox state per-device so the employee can see today's progress.

    archive_dates: list of YYYY-MM-DD strings (excluding today) — renders a
    "Previous posts" section linking to archive/YYYY-MM-DD.html files.
    """
    import html as _html
    title = _html.escape(draft.get('title', ''))
    body = draft.get('body', '')
    sub = _html.escape(draft.get('target_sub', ''))
    posting_time = _html.escape(draft.get('posting_time', 'Weekday morning, 9-11 AM IST'))
    hero = _html.escape(draft.get('hero_image_url') or '')
    blog_url = _html.escape(draft.get('blog_url', ''))
    engagement = draft.get('engagement_plan') or [
        "Reply to every comment within 12 hours.",
        "Spend 10 minutes commenting helpfully on other posts in the same sub (no links).",
        "Do NOT cross-post to another subreddit today.",
    ]
    engagement_html = "\n".join(f"<li>{_html.escape(item)}</li>" for item in engagement)
    # JS-safe versions (used inside template literals — escape backticks/backslashes)
    def js_str(s):
        return (s or '').replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
    title_js = js_str(draft.get('title', ''))
    body_js = js_str(body)
    sub_js = js_str(draft.get('target_sub', ''))
    body_html = _html.escape(body).replace('\n', '<br>')
    hero_block = (
        f'<img src="{hero}" alt="Hero image for the post" '
        f'style="width:100%;border-radius:10px;display:block;margin-bottom:12px;">'
        f'<a href="{hero}" download class="btn-secondary">⬇ Download image</a>'
    ) if hero else '<p style="color:#888;font-size:14px;">No hero image — post as text-only or attach your own.</p>'

    # System Status tab content
    phases_tab_html = _build_phases_tab_html()
    # Archive section — links to past posts within the 30-day retention window
    archive_html = ""
    if archive_dates:
        items = "\n".join(
            f'<li><a href="archive/{_html.escape(d)}.html">{_html.escape(d)}</a></li>'
            for d in archive_dates
        )
        archive_html = f'''
  <div class="card archive-card">
    <div class="label">Previous posts (last {len(archive_dates)} days)</div>
    <ul class="archive-list">
{items}
    </ul>
  </div>'''

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Reddit Post — {today}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f4f4f0;color:#222;padding:16px 12px 140px;line-height:1.5;font-size:16px}}
.wrap{{max-width:560px;margin:0 auto}}
header{{background:linear-gradient(135deg,#d4a832,#b8860b);color:#fff;padding:18px 16px;border-radius:14px;margin-bottom:16px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.1)}}
header h1{{font-size:22px;margin-bottom:4px}}
header .date{{font-size:14px;opacity:.95}}
.card{{background:#fff;border-radius:14px;padding:16px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.05);border:1px solid #eee}}
.label{{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#888;font-weight:600;margin-bottom:8px}}
.content{{font-size:16px;color:#1a1a1a;word-wrap:break-word}}
.content.title{{font-size:18px;font-weight:600;color:#0f3460}}
.content.body{{line-height:1.65;white-space:pre-wrap}}
.subreddit{{font-size:22px;font-weight:700;color:#ff4500;font-family:Menlo,Monaco,monospace}}
.btn{{width:100%;padding:14px;font-size:15px;font-weight:600;border:none;border-radius:10px;cursor:pointer;margin-top:10px;background:#0f3460;color:#fff;transition:.15s;display:flex;align-items:center;justify-content:center;gap:8px}}
.btn:active{{transform:scale(.98)}}
.btn.copied{{background:#1a5c2e}}
.btn-secondary{{display:inline-block;padding:10px 14px;background:#f0f0eb;color:#333;border-radius:8px;text-decoration:none;font-weight:500;font-size:14px;margin-top:8px}}
ul.engagement{{padding-left:20px;color:#444}}
ul.engagement li{{margin-bottom:8px;font-size:15px}}
.posting-time{{background:#fffbe6;padding:10px 12px;border-radius:8px;border-left:3px solid #d4a832;font-size:15px;color:#5d4a00}}
.archive-list{{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(100px,1fr));gap:6px}}
.archive-list li a{{display:block;padding:10px 8px;background:#f7f5ee;border-radius:8px;text-align:center;color:#555;font-size:13px;text-decoration:none;font-family:Menlo,Monaco,monospace}}
.archive-list li a:hover{{background:#ede9d5;color:#222}}
.tabs{{display:flex;gap:4px;margin:-4px 0 14px;background:#fff;padding:4px;border-radius:12px;border:1px solid #eee}}
.tab-btn{{flex:1;padding:10px 8px;font-size:14px;font-weight:600;background:transparent;border:none;border-radius:8px;color:#888;cursor:pointer;transition:.15s}}
.tab-btn.active{{background:#0f3460;color:#fff}}
.phases-summary{{font-size:13px;color:#666;padding:10px 12px;background:#fff;border-radius:10px;margin-bottom:14px;border:1px solid #eee}}
.phase-card{{background:#fff;border-radius:14px;padding:16px;margin-bottom:12px;border:1px solid #eee;box-shadow:0 1px 4px rgba(0,0,0,.04)}}
.phase-head{{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap}}
.phase-name{{font-weight:700;font-size:15px;color:#0f3460}}
.phase-badge{{font-size:12px;font-weight:600;padding:3px 8px;border-radius:6px;white-space:nowrap}}
.phase-blurb{{font-size:13px;color:#555;margin-bottom:10px;line-height:1.5}}
.phase-meta{{display:flex;gap:14px;font-size:12px;color:#666;margin-bottom:8px;flex-wrap:wrap}}
.phase-details{{font-size:13px;color:#333;background:#f7f5ee;padding:10px;border-radius:8px;font-family:Menlo,Monaco,monospace;word-break:break-word}}
.sticky-bottom{{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:1px solid #ddd;padding:14px 16px;z-index:100;box-shadow:0 -2px 8px rgba(0,0,0,.08)}}
.sticky-inner{{max-width:560px;margin:0 auto;display:flex;gap:10px;align-items:center}}
.checkbox-wrap{{display:flex;align-items:center;gap:10px;flex:1;cursor:pointer;user-select:none}}
.checkbox-wrap input{{width:24px;height:24px;cursor:pointer;accent-color:#1a5c2e}}
.checkbox-wrap span{{font-weight:600;font-size:15px;color:#222}}
.checkbox-wrap.done span{{color:#1a5c2e;text-decoration:line-through;opacity:.7}}
.notify-btn{{background:#25d366;color:#fff;padding:12px 14px;border:none;border-radius:10px;font-weight:600;font-size:14px;text-decoration:none;display:none;align-items:center;gap:6px}}
.notify-btn.show{{display:inline-flex}}
.done-stamp{{font-size:13px;color:#1a5c2e;font-weight:600;margin-top:6px;display:none}}
.done-stamp.show{{display:block}}
@media (min-width:640px){{body{{padding-bottom:120px}}}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>📋 Today's Reddit Post</h1>
    <div class="date">{today}</div>
  </header>

  <div class="tabs">
    <button class="tab-btn active" data-tab="post-tab">📝 Today's post</button>
    <button class="tab-btn" data-tab="phases-tab">⚙️ System status</button>
  </div>

  {phases_tab_html}

  <div id="post-tab">

  <div class="card">
    <div class="label">Target subreddit</div>
    <div class="subreddit" id="sub-text">{sub}</div>
    <button class="btn" onclick="copyText(`{sub_js}`, this)">📋 Copy subreddit name</button>
  </div>

  <div class="card">
    <div class="label">Post title</div>
    <div class="content title">{title}</div>
    <button class="btn" onclick="copyText(`{title_js}`, this)">📋 Copy title</button>
  </div>

  <div class="card">
    <div class="label">Post body</div>
    <div class="content body">{body_html}</div>
    <button class="btn" onclick="copyText(`{body_js}`, this)">📋 Copy body</button>
  </div>

  <div class="card">
    <div class="label">Hero image</div>
    {hero_block}
  </div>

  <div class="card">
    <div class="label">Best time to post</div>
    <div class="posting-time">⏰ {posting_time}</div>
  </div>

  <div class="card">
    <div class="label">After posting — engagement (important!)</div>
    <ul class="engagement">{engagement_html}</ul>
  </div>
{archive_html}
  <div class="done-stamp" id="done-stamp"></div>
  </div><!-- /#post-tab -->
</div>

<div class="sticky-bottom">
  <div class="sticky-inner">
    <label class="checkbox-wrap" id="check-wrap">
      <input type="checkbox" id="done-check">
      <span id="check-label">Mark as posted</span>
    </label>
    <a id="notify-btn" class="notify-btn" href="#" target="_blank" rel="noopener">📱 Notify</a>
  </div>
</div>

<script>
const KEY = 'reddit-posted-{today}';
const sub = `{sub_js}`;
const date = '{today}';
const managerPhone = '{REDDIT_MANAGER_WHATSAPP}';

function copyText(text, btn) {{
  if (navigator.clipboard) {{
    navigator.clipboard.writeText(text).then(() => flashCopied(btn));
  }} else {{
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    try {{ document.execCommand('copy'); flashCopied(btn); }} catch(e) {{}}
    document.body.removeChild(ta);
  }}
}}
function flashCopied(btn) {{
  const orig = btn.textContent;
  btn.textContent = '✓ Copied!';
  btn.classList.add('copied');
  setTimeout(() => {{ btn.textContent = orig; btn.classList.remove('copied'); }}, 1500);
}}

const check = document.getElementById('done-check');
const wrap = document.getElementById('check-wrap');
const label = document.getElementById('check-label');
const stamp = document.getElementById('done-stamp');
const notify = document.getElementById('notify-btn');

// Tab switching — Today's post / System status
document.querySelectorAll('.tab-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const target = btn.getAttribute('data-tab');
    document.getElementById('post-tab').style.display = (target === 'post-tab') ? 'block' : 'none';
    const phasesEl = document.getElementById('phases-tab');
    if (phasesEl) phasesEl.style.display = (target === 'phases-tab') ? 'block' : 'none';
  }});
}});

function refreshState() {{
  const saved = localStorage.getItem(KEY);
  if (saved) {{
    check.checked = true;
    wrap.classList.add('done');
    label.textContent = 'Posted ✓';
    stamp.textContent = '✓ Marked as posted at ' + saved;
    stamp.classList.add('show');
    const msg = encodeURIComponent('✅ Reddit post done for ' + date + ' — posted to ' + sub + ' at ' + saved + '.');
    notify.href = 'https://wa.me/' + managerPhone + '?text=' + msg;
    notify.classList.add('show');
  }}
}}
check.addEventListener('change', () => {{
  if (check.checked) {{
    const now = new Date();
    const hh = String(now.getHours()).padStart(2,'0');
    const mm = String(now.getMinutes()).padStart(2,'0');
    const stampTxt = hh+':'+mm+' IST';
    localStorage.setItem(KEY, stampTxt);
  }} else {{
    localStorage.removeItem(KEY);
  }}
  refreshState();
}});
refreshState();
</script>
</body>
</html>"""


def process_main_channel_recap():
    """Sunday recap pipeline: fetch user's latest Saturday main-channel video
    and generate a blog post + Reddit draft based on it.

    Flow:
    1. Fetch latest long-form video from SOURCE_CHANNEL_ID (last 48h window).
    2. Phase 2: try to fetch captions; fall back to description-only.
    3. Phase 3: save corpus text to voice_corpus/YYYY-MM-DD.txt.
    4. Generate blog images + blog HTML (no Veo prompts; topical fallback prompts).
    5. Publish blog to S3 with the long-form watch URL embedded.
    6. Update sitemap + blog_history + invalidate CloudFront.
    7. Generate Reddit draft for the Sunday page.
    8. Write phase status JSON for the status tab.

    This is called when daily_short.py is invoked with --mode=sunday-recap.
    Sunday's autonomous daily-bot run is skipped at the workflow cron level
    (daily_short.yml cron is `0 12 * * 1-6` — Mon-Sat only).
    """
    print("\n" + "=" * 60)
    print("  🌅 SUNDAY RECAP — main channel weekly long-form pipeline")
    print("=" * 60)

    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")

    # ── Step 1: Fetch Saturday's main-channel upload ──
    video = fetch_latest_main_channel_long_form(within_hours=48)
    if not video:
        msg = "No long-form video uploaded by main channel in last 48h — skipping recap"
        print(f"   ⚠️ {msg}")
        _write_phase_status(1, "active", "no-saturday-upload", msg)
        return False

    print(f"   📺 Found: {video['title']}")
    print(f"   📺 Uploaded {video['hours_old']}h ago · duration {video['duration_seconds']}s")
    print(f"   📺 URL: {video['vid_url']}")

    # ── Step 2: Phase 2 — REAL speech: unauth captions → OAuth captions →
    #    yt-dlp+Whisper → description. Whisper here uses the fast "base"
    #    model on 10 min of audio MAX: transcribe() has no wall-clock bound,
    #    and a long run would eat the 45-min job before the blog publishes.
    transcript, speech_source = get_video_speech_text(video["video_id"],
                                                      whisper_model="base",
                                                      max_seconds=600)
    if transcript:
        print(f"   📝 Phase 2: Got REAL speech via {speech_source} ({len(transcript)} chars)")
        script_source = transcript
        _write_phase_status(2, "active", f"speech-{speech_source}",
                            f"Got {len(transcript)} char {speech_source} transcript "
                            f"for {video['title'][:50]}")
    else:
        speech_source = None
        print(f"   📝 Phase 2: No speech source worked — using description")
        script_source = video["description"]
        _write_phase_status(2, "best-effort", "fallback-to-description",
                            "Captions (unauth + OAuth) and Whisper all failed; "
                            "falling back to video description.")

    # ── Step 3: Save to voice corpus for Phase 4 learning ──
    os.makedirs(VOICE_CORPUS_DIR, exist_ok=True)
    corpus_path = os.path.join(VOICE_CORPUS_DIR, f"{today}.txt")
    try:
        with open(corpus_path, "w") as f:
            f.write(f"# {video['title']}\n# {video['vid_url']}\n# Uploaded: {video['published_at']}\n")
            f.write(f"# source: {speech_source or 'description'}\n\n")
            f.write(script_source)
        corpus_count = len([f for f in os.listdir(VOICE_CORPUS_DIR) if f.endswith(".txt")])
        print(f"   💾 Phase 3: Saved to voice_corpus/ ({corpus_count} entries total)")
        _write_phase_status(3, "active", "saved",
                            f"{corpus_count} corpus entries · target 4+ for Phase 4 activation")
    except Exception as e:
        print(f"   ⚠️ Phase 3: corpus save failed: {e}")
        _write_phase_status(3, "failed", str(e), "Corpus save error")

    # Refresh the self-learning artifacts (voice_vocab.json +
    # learned_pronunciations.json) from the updated corpus every Sunday.
    try:
        build_voice_models()
    except Exception as e:
        print(f"   ⚠️ build_voice_models failed: {e}")

    # ── Step 4-6: Generate blog + publish to S3 ──
    try:
        from anthropic import Anthropic
        claude = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    except Exception as e:
        print(f"   ❌ Cannot init Claude: {e}")
        _write_phase_status(1, "failed", str(e), "Claude init failed")
        return False

    cost = CostTracker() if 'CostTracker' in globals() else None

    # Optimize the blog title (different from YT title — SEO-keyword version)
    try:
        titles = optimize_title(claude, video["title"], script_source[:1500], video["title"])
        blog_title = titles.get("best", video["title"])
    except Exception:
        blog_title = video["title"]

    print(f"   ✍️ Blog title: {blog_title}")

    # The actual blog content uses description + transcript as the source.
    # Hook = first line of description (or first 80 chars).
    hook = (video["description"].split("\n", 1)[0] or video["title"])[:140]
    blog_tags = video.get("tags", [])[:10] or [
        "plain tshirt wholesale", "tshirt manufacturer india",
        "bulk tshirt order", "printing business"
    ]

    blog_html, blog_slug, blog_url, blog_images = generate_blog_post(
        claude_client=claude,
        cost_tracker=cost,
        topic=video["title"],
        title=blog_title,
        description=video["description"][:500] or video["title"],
        script_english=script_source[:3000],
        tags=blog_tags,
        hook_text=hook,
        vid_id=video["video_id"],
        vid_url=video["vid_url"],  # long-form watch URL
        video_prompts=None,  # No Veo prompts — generate_blog_images falls back to topical
    )

    if not blog_html:
        msg = "Blog HTML generation returned empty"
        print(f"   ❌ {msg}")
        _write_phase_status(1, "failed", "blog-gen-empty", msg)
        return False

    # Publish to S3
    if not os.environ.get("AWS_ACCESS_KEY_ID"):
        msg = "AWS credentials missing"
        print(f"   ❌ {msg}")
        _write_phase_status(1, "failed", "no-aws", msg)
        return False

    publish_ok = publish_blog_to_s3(blog_html, blog_slug, blog_title, blog_url,
                                    blog_images, vid_id=video["video_id"], tags=blog_tags)
    if not publish_ok:
        msg = "publish_blog_to_s3 returned False"
        print(f"   ❌ {msg}")
        _write_phase_status(1, "failed", "s3-publish-failed", msg)
        return False

    sunday_excerpt = _extract_blog_excerpt(blog_html, max_words=200)
    save_blog_history(video["title"], blog_title, blog_slug, blog_url, video["vid_url"],
                      tags=blog_tags, description=video["description"][:200],
                      word_count=len(blog_html.split()),
                      excerpt=sunday_excerpt)
    print(f"   ✅ Blog published: {blog_url}")

    # ── Step 7: Reddit draft ──
    hero_url = None
    for img_bytes, fname in (blog_images or []):
        if fname == "hero.webp":
            hero_url = f"{BLOG_BASE_URL}/p/{blog_slug}-hero.webp"
            break
    try:
        generate_reddit_post(
            claude_client=claude,
            cost_tracker=cost,
            topic=video["title"],
            blog_title=blog_title,
            blog_url=blog_url,
            script_english=script_source[:1500],
            tags=blog_tags,
            hero_image_url=hero_url,
        )
    except Exception as e:
        print(f"   ⚠️ Reddit draft non-fatal failure: {e}")

    # IG carousel draft for Monday morning post (10 AM IST)
    try:
        generate_ig_carousel_draft(
            claude_client=claude,
            cost_tracker=cost,
            blog_title=blog_title,
            blog_url=blog_url,
            blog_slug=blog_slug,
            topic=video["title"],
            script_english=script_source[:1500],
            tags=blog_tags,
        )
    except Exception as e:
        print(f"   ⚠️ IG carousel draft non-fatal failure: {e}")

    # ── Phase 1 success status ──
    _write_phase_status(1, "active", "success",
                        f"Recap blog published: {blog_url} · source: '{video['title'][:60]}'")

    # ── Phase 4 status check ──
    corpus_count = len([f for f in os.listdir(VOICE_CORPUS_DIR) if f.endswith(".txt")]) \
        if os.path.isdir(VOICE_CORPUS_DIR) else 0
    if corpus_count >= 4:
        _write_phase_status(4, "active", "ready",
                            f"Voice style hints active in daily script gen ({corpus_count} corpus entries)")
    else:
        _write_phase_status(4, "pending", "waiting-for-corpus",
                            f"Need {4 - corpus_count} more weekly corpus entries before activation "
                            f"(currently {corpus_count})")

    print("\n" + "=" * 60)
    print(f"  ✅ SUNDAY RECAP COMPLETE")
    print(f"  📰 Blog: {blog_url}")
    print(f"  📺 Source: {video['vid_url']}")
    print("=" * 60)
    return True


def generate_reddit_post(claude_client, cost_tracker, topic, blog_title, blog_url,
                         script_english, tags, hero_image_url):
    """Generate ONE ready-to-paste Reddit post and publish it to a mobile-
    friendly hosted page. The employee bookmarks one URL:
        https://www.bulkplaintshirt.com/p/reddit-today.html
    and gets a fresh post every day with copy buttons + a "posted" checkbox.

    Side effects:
    - Saves JSON archive to reddit_drafts/YYYY-MM-DD.json (committed to git).
    - Uploads HTML to S3 at p/reddit-today.html.
    - Uploads HTML to S3 at p/reddit-drafts/YYYY-MM-DD.html (history).
    - Invalidates CloudFront on /p/reddit-today.html.

    Non-fatal on any failure — daily pipeline continues regardless.
    """
    print("   📝 Reddit: Drafting daily post...")
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")
    os.makedirs(REDDIT_DRAFTS_DIR, exist_ok=True)

    subs_list = "\n".join(f"   - {s}" for s in REDDIT_SUBS_WHITELIST)
    tags_str = ", ".join(tags) if tags else "none"

    prompt = f"""You are writing ONE Reddit post for a small Indian B2B textile manufacturer (Sale91.com / bulkplaintshirt.com — plain t-shirts, hoodies, blanks for printing businesses).

CONTEXT:
- Today's blog post: "{blog_title}"
- Blog URL: {blog_url}
- Hero image (already on S3): {hero_image_url or 'none'}
- Topic angle: {topic}
- Source video script (English): {script_english}
- Tags: {tags_str}

GOAL: Write a Reddit post that drives readers to the blog WITHOUT looking promotional. Reddit users hate "buy now" tone — they reward "I made this mistake" stories that share real insight, with the link as a "see photos + full breakdown" add-on at the end.

OUTPUT FORMAT: Return ONLY a valid JSON object (no preamble, no markdown fences, no explanation). Exactly this schema:

{{
  "target_sub": "<one subreddit from the whitelist below — pick the SINGLE most relevant for the topic>",
  "posting_time": "<e.g. 'Weekday morning, 9-11 AM IST' or 'Sunday — r/Entrepreneur weekly self-promo thread only'>",
  "title": "<ONE Reddit-native title, 70 chars max, no emojis. See TITLE RULES below — must sound like a real person posting, not a press release>",
  "body": "<150-220 words. FIRST-PERSON HUMAN VOICE — see BODY VOICE RULES below. End with: 'Full breakdown with photos: {blog_url}' on its own line. Then ONE peer question on the next line. Use plain text with \\n for line breaks — Reddit markdown like **bold** is OK.>",
  "engagement_plan": [
    "Reply to every comment within 12 hours.",
    "Spend 10 minutes commenting helpfully on other posts in r/X today (no links).",
    "Do NOT cross-post to another subreddit today."
  ]
}}

SUBREDDIT WHITELIST — pick ONE that genuinely fits the topic:
{subs_list}

TITLE RULES — the title is THE thing that decides if anyone clicks. Get this right.

The user explicitly flagged that earlier titles like:
   ❌ "Lost ₹40K mixing blanks: embroidery, DTF, screen print same order"
sound like a press release / SEO headline, not a human posting on Reddit.

DO NOT use these title shapes — they all sound robotic:
   ❌ "Lost ₹X [doing Y]:" / "X cost me ₹Y: [list of things]"
   ❌ "How to avoid X mistake (₹Y lesson)"
   ❌ "[Number] [thing] [verb-ed]:" with a colon and bullet-like list
   ❌ Keyword-stuffed: "DTF DTG screen print embroidery comparison India wholesale"
   ❌ Anything that ends with a truncated noun ("...screen print same") — looks AI-cut-off

USE one of these title shapes (real Reddit voice):

  1. **First-person story hook** — talk like a human venting/sharing:
     ✅ "Tried to combine embroidery + DTF + screen on one order. Cost me ₹40k. Sharing what went wrong."
     ✅ "Used 3 different GSM blanks in the same order. Client rejected everything. ₹40k lesson."
     ✅ "Lost ₹40k last week — sharing the dumb mistake so you don't repeat it."

  2. **Question / advice-seeking** — invites comments naturally:
     ✅ "Anyone else struggle with combining embroidery + DTF on the same shirt?"
     ✅ "Has anyone successfully run 3 print methods on one bulk order without quality issues?"
     ✅ "Need advice — client wants embroidery, DTF, and screen on same blank. Is this even doable?"

  3. **PSA / warning** — short and direct:
     ✅ "PSA: don't mix GSM grades within the same order. Lost ₹40k learning this."
     ✅ "Heads up — combining embroidery + DTF + screen on one shirt is a quality disaster."

  4. **Specific scenario opening** — concrete, story-shaped:
     ✅ "Client wanted embroidery + DTF + screen on one bulk order. Here's why I'd never do that again."
     ✅ "500-piece order, 3 different GSMs, 3 different print methods. Big mistake."

ADDITIONAL TITLE RULES:
- Sound human. Use contractions ("don't", "can't", "I've"). Allow common casual phrases ("lost ₹40k", "big mistake", "lesson learned", "be smarter than me").
- The ₹ amount is allowed but NOT required in the title. If included, weave it naturally ("Cost me ₹40k") not as a headline lead.
- Avoid colons + keyword lists. Use one sentence (with a period) or two short sentences.
- Avoid starting with the cost ("Lost ₹40K..." is overused — start with the scenario instead).
- No emojis in title (Reddit hates emoji-titles).
- 60-90 chars ideal range. Longer if a complete thought needs it.
- The title must read like something one of your blog post readers would themselves post after experiencing the problem — not like a headline you'd put on the blog.

BODY VOICE RULES — same priority as the title. User flagged that today's body sounds like a case study, not a human posting.

Today's actual body (DO NOT WRITE LIKE THIS):
   ❌ "A client ordered 500 pieces last month — 200 embroidered polos..."
   ❌ "We used whatever blanks we had..."
   ❌ "The fix: one consistent blank across all three techniques."
   ❌ "If you're running multi-technique orders, this one change will save you from expensive mistakes."

   Problems: third-person framing ("A client...", "We used..."), distant
   professional tone, marketing-pitch ending. Reads like a brand blog
   excerpt. People scroll past this on Reddit.

How a real printer would write the same story (WRITE LIKE THIS):

   ✅ "Got burned hard last month, want to share so you don't repeat my dumb mistake.

   Client ordered 500 pieces — 200 embroidered polos, 200 DTF, 100 screen prints. I figured I'd just use whatever blanks I had in stock: 160 GSM for embroidery, 180 for DTF, 200 for screen. Different blanks, different prices, why not?

   Whole batch rejected. Side-by-side, the pieces looked like they came from three different companies — one was thin and floppy, one was stiff, one was heavy. Same brand, same campaign, totally inconsistent feel.

   Took me a week to figure out the fix. Now I just use ONE blank for the entire order — 200 GSM combed cotton across all three print methods. Embroidery still runs smooth, DTF bonds clean, screen absorbs evenly. Zero returns since.

   ₹40k down the drain before I figured this out. Wish I'd known.

   Full breakdown with photos: <url>

   Anyone else got bitten by mixing blanks across print methods?"

Notice the human elements:
- "I" voice throughout ("I figured", "I just use", "Wish I'd known")
- Self-deprecation ("dumb mistake", "Why not?", "Took me a week")
- Sensory description ("thin and floppy", "stiff", "heavy")
- Conversational connectors ("Got burned hard", "Sounds simple enough", "Took me a week", "Now I just")
- ₹ amount at the END, organic — not a marketing lead
- Peer-to-peer closing question ("Anyone else got bitten by...")

BODY VOICE RULES (apply ALL of these):
1. **First-person mandatory.** "I" / "me" / "my" throughout. Never "we" (sounds corporate). Never "A client ordered..." — use "Client called me last week..." or "Got a job last week for 500 pieces..."
2. **Open with vulnerability** — "Got burned for ₹40k..." / "Made the dumbest mistake last week..." / "Still kicking myself about this one." Real humans admit fault; brands hide it.
3. **Self-deprecation OK** — "I should have known", "Stupid of me", "Was being lazy", "Thought I was being smart" all read as authentic.
4. **Sensory detail beats abstract** — instead of "felt completely different" write "one was thin and floppy, one had stiffness, one was heavy". Help the reader SEE the problem.
5. **Conversational connectors** — "Turns out", "Long story short", "Spoiler:", "ok so", "yeah", "tbh", "basically". Sprinkle 2-3 of these.
6. **No marketing-pitch endings** — never "this one change will save you" / "expert tip" / "pro insight". Just describe what worked and stop.
7. **The ₹ amount goes near the end as a punchline**, not a headline. "₹40k down the drain" hits harder than "I lost ₹40k because..."
8. **End with a peer question** that invites comments — "Anyone else..." / "How do you handle..." / "What would you have done?"

OTHER WRITING RULES:
- NO emojis in title (Reddit hates emoji-titles).
- 1-2 emojis MAX in body, only if naturally placed.
- Use Reddit markdown in body: **bold** for the punchline only, bullet lists for actual lists, no ##H2.
- Forbidden words: amazing, incredible, discover, unleash, revolutionary, transform, premium, journey (sound like ads).
- Use specific numbers, real ₹ amounts, real fabric/GSM specs from the topic.
- Mention "Sale91" or "BulkPlainTshirt" ONLY inside the link URL — never in the body text.
- Replace "r/X" placeholder in engagement_plan with the actual chosen sub.

Return ONLY the JSON object."""

    try:
        resp = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        # Some models prefix with "json" tag
        if raw.lower().startswith("json"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[4:]

        draft = json.loads(raw)
        # Attach context fields used by the HTML renderer
        draft['hero_image_url'] = hero_image_url
        draft['blog_url'] = blog_url

        # Track cost
        if cost_tracker and hasattr(cost_tracker, 'track_claude_call'):
            cost_tracker.track_claude_call("sonnet", resp.usage.input_tokens, resp.usage.output_tokens)

        # Archive JSON locally (committed to git on the yt-shorts-bot repo
        # for raw data history); the rendered HTML is published separately
        # to the reddit-drafts GitHub Pages repo by _publish_reddit_to_github_pages.
        json_path = os.path.join(REDDIT_DRAFTS_DIR, f"{today}.json")
        with open(json_path, "w") as f:
            json.dump(draft, f, ensure_ascii=False, indent=2)

        # Publish to GitHub Pages (separate from bulkplaintshirt.com website).
        # Handles archive write + 30-day pruning + index rebuild internally.
        page_url = _publish_reddit_to_github_pages(draft, today)
        return page_url or json_path
    except Exception as e:
        print(f"   ⚠️ Reddit: Draft generation failed (non-fatal): {e}")
        return None


def repair_existing_blog_posts(s3_client, cloudfront_client):
    """One-time repair: inject JSON-LD + bottom bar into existing blog posts that are missing them."""
    blog_history_path = "blog_history.json"
    if not os.path.exists(blog_history_path):
        return

    try:
        with open(blog_history_path) as f:
            history = json.load(f)
    except Exception:
        return

    repaired = []
    for entry in history:
        slug = entry.get("slug", "")
        title = entry.get("title", "")
        blog_url = entry.get("url", "")
        date = entry.get("date", datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d"))
        date10 = (date or "")[:10]
        vid_url = entry.get("vid_url", "")
        _vm = re.search(r'(?:shorts/|v=|embed/|/vi/)([A-Za-z0-9_-]{11})', vid_url)
        vid_id = _vm.group(1) if _vm else None
        key = f"p/{slug}.html"

        try:
            resp = s3_client.get_object(Bucket=BLOG_S3_BUCKET, Key=key)
            html = resp['Body'].read().decode('utf-8')
            original_html = html
            fixes = []

            # JSON-LD + bottom bar (only if missing)
            if 'application/ld+json' not in html:
                html = inject_blog_seo(html, title, "", blog_url, date10, slug, vid_id=vid_id, vid_url=vid_url)
                fixes.append("JSON-LD + bottom bar")

            # VideoObject backfill for posts that already have JSON-LD but no
            # VideoObject (so Google can read the embedded YouTube video's metadata).
            if vid_id and 'VideoObject' not in html:
                _vo = {
                    "@context": "https://schema.org",
                    "@type": "VideoObject",
                    "name": title,
                    "description": (title or "")[:200],
                    "thumbnailUrl": [f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"],
                    "uploadDate": f"{date10}T09:00:00+05:30" if len(date10) == 10 else date10,
                    "embedUrl": f"https://www.youtube.com/embed/{vid_id}",
                }
                _vo_script = f'<script type="application/ld+json">{json.dumps(_vo, ensure_ascii=False)}</script>'
                if '</head>' in html:
                    html = html.replace('</head>', _vo_script + '\n</head>', 1)
                elif '</body>' in html:
                    html = html.replace('</body>', _vo_script + '\n</body>', 1)
                fixes.append("videoobject")

            # Force canonical to the correct www URL — Claude wrote a non-www
            # canonical on a couple posts, which 301-redirects and confuses index
            # selection. Normalize to the self-referencing www canonical.
            correct_canon = f'<link rel="canonical" href="{BLOG_BASE_URL}/p/{slug}.html">'
            _cm = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]*>', html, re.I)
            if _cm and _cm.group(0) != correct_canon:
                html = html.replace(_cm.group(0), correct_canon, 1)
                fixes.append("canonical")
            elif not _cm and '</head>' in html:
                html = html.replace('</head>', correct_canon + '\n</head>', 1)
                fixes.append("canonical")

            # Author-image onerror infinite-loop fix. The old handler set src to a
            # fallback that also 403s; with no guard, onerror re-fired forever and
            # the page never stopped loading. Add this.onerror=null so it fires once.
            old_onerror = ("onerror=\"this.src='https://www.bulkplaintshirt.com/catalog/img/logo.png'"
                           ";this.style.padding='12px';this.style.background='#fffbe6';\"")
            new_onerror = ("onerror=\"this.onerror=null;this.src='https://www.bulkplaintshirt.com/catalog/img/logo.png'"
                           ";this.style.padding='12px';this.style.background='#fffbe6';\"")
            if old_onerror in html:
                html = html.replace(old_onerror, new_onerror)
                fixes.append("author-image loop")

            # Repoint the avatar primary src off the denied imges/ prefix (403) to
            # the writable catalog/img/ copy, so existing posts stop 403-ing on it.
            old_avatar = "https://www.bulkplaintshirt.com/imges/ketu-author.webp"
            new_avatar = "https://www.bulkplaintshirt.com/catalog/img/ketu-author.webp"
            if old_avatar in html:
                html = html.replace(old_avatar, new_avatar)
                fixes.append("avatar path")

            # Strip the fake Product aggregateRating (4.5/1050 reviews, no real
            # on-page reviews) — a structured-data policy violation baked into
            # every older post. The object has no nested braces, so this is exact.
            fake_rating = re.compile(r',\s*"aggregateRating":\s*\{[^{}]*\}')
            if fake_rating.search(html):
                html = fake_rating.sub('', html)
                fixes.append("fake rating")

            # Location consistency: "knit in Delhi" was wrong (we knit in Tiruppur,
            # ship from the Delhi warehouse). Fix the visible author-card text + the
            # Person-schema description on existing posts.
            loc_subs = [
                ("We knit our own fabric in Delhi and ship to printing businesses across India.",
                 "We knit our own fabric in Tiruppur and ship PAN-India from our Delhi warehouse to printing businesses across the country."),
                ("a Delhi-based manufacturer that knits its own fabric and ships PAN-India.",
                 "which knits its own fabric in Tiruppur and ships PAN-India from its Delhi warehouse."),
            ]
            for _old, _new in loc_subs:
                if _old in html:
                    html = html.replace(_old, _new)
                    if "location" not in fixes:
                        fixes.append("location")

            # Visible byline + date under the H1 (E-E-A-T) for existing posts.
            if 'class="bpt-byline"' not in html and '</h1>' in html:
                try:
                    _d = datetime.strptime((date or "")[:10], "%Y-%m-%d")
                    _disp = _d.strftime("%B %-d, %Y")
                except Exception:
                    _disp = (date or "")[:10]
                _byline = (
                    '<div class="bpt-byline" style="font-size:13px;color:#666;margin:2px 0 18px;">'
                    'By <a href="https://www.bulkplaintshirt.com/#ketu-r" rel="author" '
                    'style="color:#0f3460;font-weight:600;text-decoration:none;">Ketu R</a>'
                    f'{" · Updated " + _disp if _disp else ""}</div>'
                )
                html = re.sub(r'(</h1>)', r'\1\n' + _byline, html, count=1)
                fixes.append("byline")

            if not fixes:
                continue

            s3_client.put_object(
                Bucket=BLOG_S3_BUCKET,
                Key=key,
                Body=html.encode('utf-8'),
                ContentType='text/html; charset=utf-8',
                CacheControl='no-cache'
            )
            repaired.append(slug)
            print(f"   🔧 Repair: Fixed {slug}.html ({', '.join(fixes)})")
        except Exception as e:
            print(f"   ⚠️ Repair: Could not fix {slug}.html: {e}")

    if repaired:
        # Invalidate repaired pages in CloudFront
        try:
            paths = [f'/p/{s}.html' for s in repaired]
            cloudfront_client.create_invalidation(
                DistributionId=BLOG_CLOUDFRONT_DIST_ID,
                InvalidationBatch={
                    'Paths': {'Quantity': len(paths), 'Items': paths},
                    'CallerReference': f"repair-{int(time.time())}"
                }
            )
            print(f"   🔧 Repair: CloudFront invalidation for {len(repaired)} fixed posts")
        except Exception as e:
            print(f"   ⚠️ Repair: CloudFront invalidation failed: {e}")
    else:
        print(f"   ✅ Repair: All existing posts already clean")


def build_sitemap_xml(new_post=None):
    """Build complete sitemap XML from blog_history.json + static pages.

    Args:
        new_post: Optional dict with keys (slug, url, date) for a post not yet in blog_history.
    Returns:
        Complete sitemap XML string for p/map.xml.
    """
    import json as _json

    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")

    # Static pages — NO lastmod: these rarely change, and stamping today's date
    # on every rebuild taught Google to distrust the sitemap's lastmod signal.
    # policy.html / term.html / disclaimer.html are Disallow'd in robots.txt and
    # /p/feed.xml is an RSS feed, not a page — listing them created permanent
    # "Blocked by robots.txt" / junk entries in Search Console. Keep them out.
    static_pages = [
        (f"{BLOG_BASE_URL}/", "daily", "1.0"),
        (f"{BLOG_BASE_URL}/catalog/index.html", "weekly", "0.9"),
        (f"{BLOG_BASE_URL}/p/index.html", "daily", "0.9"),
        (f"{BLOG_BASE_URL}/contactus.html", "monthly", "0.6"),
        (f"{BLOG_BASE_URL}/seller.html", "monthly", "0.6"),
        (f"{BLOG_BASE_URL}/p/FQA.html", "monthly", "0.8"),
        (f"{BLOG_BASE_URL}/calc/shipping-calculator.html", "monthly", "0.7"),
        (f"{BLOG_BASE_URL}/returnpolicy.html", "yearly", "0.3"),
        (f"{BLOG_BASE_URL}/refundpolicy.html", "yearly", "0.3"),
    ]

    # Blog posts from history (redirect-consolidated losers excluded)
    posts = _load_blog_history_active()

    if new_post and new_post.get('slug'):
        if not any(p.get('slug') == new_post['slug'] for p in posts):
            posts.append(new_post)

    # Build URL entries
    urls_xml = ''
    for loc, changefreq, priority in static_pages:
        urls_xml += f'''  <url>
    <loc>{loc}</loc>
    <changefreq>{changefreq}</changefreq>
    <priority>{priority}</priority>
  </url>
'''

    # Deduplicate by slug, newest first
    seen_slugs = set()
    posts.sort(key=lambda p: p.get('date', ''), reverse=True)
    for post in posts:
        p_slug = post.get('slug', '')
        if not p_slug or p_slug in seen_slugs:
            continue
        seen_slugs.add(p_slug)
        p_url = f"{BLOG_BASE_URL}/p/{p_slug}.html"
        p_date = today
        try:
            dt = datetime.fromisoformat(post.get('date', '').replace('Z', '+00:00'))
            p_date = dt.strftime('%Y-%m-%d')
        except Exception:
            pass
        urls_xml += f'''  <url>
    <loc>{p_url}</loc>
    <lastmod>{p_date}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
'''

    # Legacy/manual posts NOT in blog_history.json but live on the site
    # 2026-07-10: 240gsmtshirt, DelhiBIGGESTPlainTShirtWarehouse, wholesale-plain-t-shirts,
    # AcidWashTshirt, Wholesale-Blanks removed — they 301 to their cluster winners now
    # (GSC-data-driven consolidation; redirect map lives in catalog/cloudfront/url-rewrite.js).
    legacy_sitemap_posts = [
        "Biggest-Plain-Tshirt-Warehouse", "build-tshirt-brand",
        "premium-plain-t-shirts-bulk-supplier-india",
        "fast-delivery-plain-t-shirts-maharashtra",
        "plain-t-shirt-wholesale-near-me-delhi-india",
        "dropshipping",
        "wholesale-blank-t-shirts", "Shipping-Method", "acid-wash-tshirts",
        "Dropshoulders", "plainhoodie", "430gsm-dropshoulder-hoodie",
        "b2b-dropshipping-guide", "next-day-train-delivery",
        "how-to-order", "third-party-printing-service",
        "true-bio-rneck", "cloud-dancer-tshirts",
    ]
    for slug in legacy_sitemap_posts:
        if slug not in seen_slugs:
            seen_slugs.add(slug)
            urls_xml += f'''  <url>
    <loc>{BLOG_BASE_URL}/p/{slug}.html</loc>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>
'''

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls_xml}</urlset>'''


def repair_sitemap(s3_client):
    """Rebuild /p/map.xml from blog_history.json + static pages (idempotent).

    The sitemap canonically lives at /p/map.xml (referenced by robots.txt
    via Sitemap: directive, submitted to Google Search Console at this path).
    No root-level /sitemap.xml — IAM blocks bucket-root writes anyway, and
    /p/map.xml is the agreed canonical location.
    """
    try:
        map_xml = build_sitemap_xml()
        s3_client.put_object(
            Bucket=BLOG_S3_BUCKET,
            Key='p/map.xml',
            Body=map_xml.encode('utf-8'),
            ContentType='application/xml; charset=utf-8',
            CacheControl='no-cache',
        )
        print(f"   \U0001f527 Sitemap: Rebuilt /p/map.xml from blog_history")
    except Exception as e:
        print(f"   \u26a0\ufe0f Sitemap: Could not rebuild sitemap: {e}")


def build_blog_index_html(new_post=None):
    """Build complete blog index HTML from blog_history.json (card-based layout).

    Args:
        new_post: Optional dict with keys (date, topic, title, slug, url, vid_url)
                  to include a post not yet saved to blog_history.json.
    Returns:
        Full HTML string for p/index.html.
    """
    import json as _json
    import html as _html

    # Load blog history
    posts = _load_blog_history_active()

    # Append new post if provided (not yet in history)
    if new_post and new_post.get('slug'):
        if not any(p.get('slug') == new_post['slug'] for p in posts):
            posts.append(new_post)

    # Sort newest first
    posts.sort(key=lambda p: p.get('date', ''), reverse=True)

    # Collect unique tags for filter pills
    all_tags = []
    tag_set = set()
    for post in posts:
        for tag in post.get('tags', []):
            t = tag.strip()
            if t and t.lower() not in tag_set:
                tag_set.add(t.lower())
                all_tags.append(t)

    # OG image: use latest post hero, or logo fallback
    og_image = f"{BLOG_BASE_URL}/catalog/img/logo.png"
    if posts:
        og_image = f"{BLOG_BASE_URL}/p/{posts[0].get('slug', '')}-hero.webp"

    # Color palette for hero fallback gradients
    gradients = [
        'linear-gradient(135deg, #d4a832 0%, #b8860b 100%)',
        'linear-gradient(135deg, #1a5c2e 0%, #2d8a4e 100%)',
        'linear-gradient(135deg, #0f3460 0%, #1a6cb4 100%)',
        'linear-gradient(135deg, #8b1a1a 0%, #c0392b 100%)',
        'linear-gradient(135deg, #4a148c 0%, #7b1fa2 100%)',
        'linear-gradient(135deg, #006064 0%, #00897b 100%)',
    ]

    # Build tag filter pills HTML
    tag_pills_html = ''
    if all_tags:
        pills = '<button class="tag-pill active" data-tag="all">All</button>\n'
        for tag in all_tags[:15]:
            safe_tag = _html.escape(tag)
            pills += f'            <button class="tag-pill" data-tag="{safe_tag}">{safe_tag}</button>\n'
        tag_pills_html = f'        <div class="tag-filters">\n            {pills}        </div>'

    # Build post cards HTML
    cards_html = ''
    for idx, post in enumerate(posts):
        p_slug = post.get('slug', '')
        p_title = _html.escape(post.get('title', ''))
        p_topic = _html.escape(post.get('topic', ''))
        p_date = ''
        try:
            dt = datetime.fromisoformat(post.get('date', '').replace('Z', '+00:00'))
            p_date = dt.strftime('%b %d, %Y')
        except Exception:
            pass
        hero_url = f"/p/{p_slug}-hero.webp"
        post_url = f"/p/{p_slug}.html"
        vid_url = post.get('vid_url', '')
        word_count = post.get('word_count', 2000)
        read_min = max(1, round(word_count / 200))
        post_tags = post.get('tags', [])
        first_letter = (post.get('title', 'B')[0] if post.get('title') else 'B').upper()
        gradient = gradients[idx % len(gradients)]
        tags_attr = _html.escape(' '.join(t.strip() for t in post_tags)) if post_tags else ''

        # YouTube play icon overlay (only if vid_url exists)
        play_icon = ''
        if vid_url:
            safe_vid = _html.escape(vid_url)
            play_icon = f'<a href="{safe_vid}" target="_blank" rel="noopener" class="play-btn" title="Watch on YouTube" onclick="event.stopPropagation()"><svg viewBox="0 0 24 24" fill="white" width="28" height="28"><polygon points="5,3 19,12 5,21"/></svg></a>'

        # Tag badges inside card
        tag_badges = ''
        if post_tags:
            badges = ''.join(f'<span class="card-tag">{_html.escape(t.strip())}</span>' for t in post_tags[:3])
            tag_badges = f'<div class="card-tags">{badges}</div>'

        cards_html += f'''        <article class="post-card" data-tags="{tags_attr}" data-title="{p_title}" data-topic="{p_topic}">
            <a href="{post_url}">
                <div class="card-img" style="--fallback-bg:{gradient}">
                    <img src="{hero_url}" alt="{p_title}" loading="lazy" onerror="this.parentElement.classList.add('no-img')">
                    <div class="img-fallback">{first_letter}</div>
                    {play_icon}
                </div>
                <div class="post-card-body">
                    <h3>{p_title}</h3>
                    <p class="topic">{p_topic}</p>
                    <div class="card-meta">
                        <span class="date">{p_date}</span>
                        <span class="read-time">{read_min} min read</span>
                    </div>
                    {tag_badges}
                </div>
            </a>
        </article>
'''

    # Build plain-HTML site navigation + article links for Google crawlability (no JS needed)
    # These ensure search engines discover every page on the site from index.html.
    site_nav_html = '''    <footer class="all-articles">
        <h2>Main Pages</h2>
        <ul>
<li><a href="/">Home</a></li>
<li><a href="/seller.html">About Us</a></li>
<li><a href="/contactus.html">Contact Us</a></li>
<li><a href="/p/FQA.html">FAQ</a></li>
<li><a href="/catalog/index.html">Catalog</a></li>
<li><a href="https://sale91.com">Shop — sale91.com</a></li>
        </ul>
    </footer>'''

    # Legacy/manual posts that are NOT in blog_history.json but exist on the site.
    # These must always appear in footer for search engine crawlability.
    legacy_posts = [
        ("/p/240gsmtshirt.html", "240gsm Dropshoulder Tshirts"),
        ("/p/DelhiBIGGESTPlainTShirtWarehouse.html", "Delhi's BIGGEST Plain T Shirt Warehouse"),
        ("/p/Biggest-Plain-Tshirt-Warehouse.html", "Biggest Plain T shirt Warehouse"),
        ("/p/build-tshirt-brand.html", "Build your own t-shirt brand"),
        ("/p/premium-plain-t-shirts-bulk-supplier-india.html", "Premium Plain T-Shirts in Bulk"),
        ("/p/wholesale-plain-t-shirts.html", "Wholesale Plain T-Shirts for Custom Printing"),
        ("/p/fast-delivery-plain-t-shirts-maharashtra.html", "Fast 2-Day Plain T-Shirt Delivery in Maharashtra"),
        ("/p/plain-t-shirt-wholesale-near-me-delhi-india.html", "Top T Shirt Wholesalers in Delhi"),
        ("/p/dropshipping.html", "B2B Dropshipping Plain T shirt"),
        ("/p/AcidWashTshirt.html", "AcidWash Plain T shirts"),
        ("/p/Wholesale-Blanks.html", "Wholesale Blanks"),
        ("/p/wholesale-blank-t-shirts.html", "Wholesale Blank T-Shirts"),
        ("/p/Shipping-Method.html", "PAN India Fast Delivery for Wholesale Orders"),
        ("/p/acid-wash-tshirts.html", "Acid Wash T-Shirts Wholesale India"),
        ("/p/Dropshoulders.html", "Dropshoulder 240gsm"),
        ("/p/plainhoodie.html", "Plain Hoodies 320gsm, 430gsm"),
        ("/p/430gsm-dropshoulder-hoodie.html", "430gsm Dropshoulder Hoodie"),
        ("/p/b2b-dropshipping-guide.html", "B2B Dropshipping Guide"),
        ("/p/next-day-train-delivery.html", "Next Day Train Delivery"),
        ("/p/how-to-order.html", "How to Order"),
        ("/p/third-party-printing-service.html", "Third Party Printing Service"),
        ("/p/true-bio-rneck.html", "True Bio RNeck T Shirts"),
        ("/p/cloud-dancer-tshirts.html", "Cloud Dancer T shirts"),
    ]

    # Combine blog_history.json posts + legacy posts (deduplicate by URL)
    all_link_items = ''
    seen_urls = set()

    # First: posts from blog_history.json (newest first)
    for p in posts:
        slug = p.get('slug', '')
        if not slug:
            continue
        url = f"/p/{slug}.html"
        if url not in seen_urls:
            seen_urls.add(url)
            all_link_items += f'<li><a href="{url}">{_html.escape(p.get("title", slug))}</a></li>\n'

    # Then: legacy manual posts (skip if already in blog_history)
    for url, title in legacy_posts:
        if url not in seen_urls:
            seen_urls.add(url)
            all_link_items += f'<li><a href="{url}">{_html.escape(title)}</a></li>\n'

    footer_links_html = f'''    <footer class="all-articles">
        <h2>All Articles</h2>
        <ul>
{all_link_items}        </ul>
    </footer>'''

    policy_nav_html = '''    <footer class="all-articles">
        <h2>Policy &amp; Legal</h2>
        <ul>
<li><a href="/policy.html">Privacy Policy</a></li>
<li><a href="/returnpolicy.html">Shipping and Delivery Policy</a></li>
<li><a href="/refundpolicy.html">Return and Refund Policy</a></li>
<li><a href="/term.html">Terms and Conditions</a></li>
<li><a href="/disclaimer.html">Disclaimer</a></li>
        </ul>
    </footer>'''

    footer_links_html = site_nav_html + '\n\n' + footer_links_html + '\n\n' + policy_nav_html

    # Build all site URLs for JSON-LD (posts + main pages + policy pages)
    site_pages = [
        f"{BLOG_BASE_URL}/",
        f"{BLOG_BASE_URL}/seller.html",
        f"{BLOG_BASE_URL}/contactus.html",
        f"{BLOG_BASE_URL}/p/FQA.html",
        f"{BLOG_BASE_URL}/catalog/index.html",
    ]
    post_urls = [f"{BLOG_BASE_URL}/p/{p.get('slug', '')}.html" for p in posts]
    legacy_post_urls = [f"{BLOG_BASE_URL}{url}" for url, _ in legacy_posts
                        if f"{BLOG_BASE_URL}{url}" not in post_urls]
    policy_urls = [
        f"{BLOG_BASE_URL}/policy.html",
        f"{BLOG_BASE_URL}/returnpolicy.html",
        f"{BLOG_BASE_URL}/refundpolicy.html",
        f"{BLOG_BASE_URL}/term.html",
        f"{BLOG_BASE_URL}/disclaimer.html",
    ]
    all_urls = site_pages + post_urls + legacy_post_urls + policy_urls
    collection_ld = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "Plain T-Shirt Blog & Resources",
        "description": "Expert guides on wholesale plain t-shirts, GSM fabric, printing techniques from India's leading B2B manufacturer.",
        "url": f"{BLOG_BASE_URL}/p/index.html",
        "publisher": {
            "@type": "Organization",
            "name": "BulkPlainTshirt.com",
            "url": BLOG_BASE_URL,
            "logo": f"{BLOG_BASE_URL}/catalog/img/logo.png"
        },
        "mainEntity": {
            "@type": "ItemList",
            "numberOfItems": len(all_urls),
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "url": url}
                for i, url in enumerate(all_urls)
            ]
        }
    }
    ld_json = _json.dumps(collection_ld, ensure_ascii=False)

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Plain T-Shirt Blog & Resources | Wholesale Blank Tees | BulkPlainTshirt.com</title>
    <meta name="description" content="Expert guides on wholesale plain t-shirts, GSM fabric selection, printing techniques, and bulk ordering from India&#39;s leading B2B manufacturer. Tiruppur direct.">
    <meta name="keywords" content="plain t-shirt wholesale, bulk blank tees, GSM guide, t-shirt printing, Tiruppur manufacturer, B2B t-shirts">
    <meta property="og:title" content="Plain T-Shirt Blog &amp; Resources | BulkPlainTshirt.com">
    <meta property="og:description" content="Expert guides on wholesale plain t-shirts, GSM fabric selection, printing techniques from India&#39;s leading B2B manufacturer.">
    <meta property="og:type" content="website">
    <meta property="og:url" content="{BLOG_BASE_URL}/p/index.html">
    <meta property="og:image" content="{og_image}">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="Plain T-Shirt Blog &amp; Resources | BulkPlainTshirt.com">
    <meta name="twitter:image" content="{og_image}">
    <link rel="canonical" href="{BLOG_BASE_URL}/p/index.html">
    <link rel="alternate" type="application/rss+xml" title="BulkPlainTshirt Blog RSS" href="{BLOG_BASE_URL}/p/feed.xml">
    <script type="application/ld+json">{ld_json}</script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: system-ui, -apple-system, sans-serif; background: #f5f5f0; color: #333; padding-top: 80px; padding-bottom: 70px; }}
        .header {{ position: fixed; top: 0; left: 0; width: 100%; background: #d4a832; text-align: center; padding: 12px 0; z-index: 1000; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }}
        .header a.brand {{ font-size: 22px; font-weight: bold; color: #1a1a1a; text-decoration: none; }}
        .header .sub {{ font-size: 13px; color: #333; }}
        .header .sub a {{ color: #1a1a1a; text-decoration: underline; }}
        .hero {{ text-align: center; padding: 36px 20px 24px; background: #fff; border-bottom: 1px solid #e8e8e0; }}
        .hero h1 {{ font-size: 26px; color: #1a1a1a; font-weight: 700; }}
        .hero p {{ color: #666; font-size: 15px; margin-top: 8px; max-width: 600px; margin-left: auto; margin-right: auto; line-height: 1.5; }}
        .nav-links {{ max-width: 1100px; margin: 20px auto 0; padding: 0 20px; display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; }}
        .nav-links a {{ background: #fff; padding: 8px 18px; border-radius: 20px; text-decoration: none; color: #333; font-size: 14px; font-weight: 500; box-shadow: 0 1px 4px rgba(0,0,0,0.06); border: 1px solid #e8e8e0; transition: all 0.2s; }}
        .nav-links a:hover {{ background: #d4a832; color: #1a1a1a; border-color: #d4a832; }}
        .search-box {{ max-width: 500px; margin: 20px auto 0; padding: 0 20px; }}
        .search-box input {{ width: 100%; padding: 10px 16px; border: 1px solid #ddd; border-radius: 24px; font-size: 14px; outline: none; background: #fff; transition: border-color 0.2s; }}
        .search-box input:focus {{ border-color: #d4a832; box-shadow: 0 0 0 3px rgba(212,168,50,0.15); }}
        .tag-filters {{ max-width: 1100px; margin: 16px auto 0; padding: 0 20px; display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; }}
        .tag-pill {{ padding: 5px 14px; border-radius: 16px; border: 1px solid #ddd; background: #fff; font-size: 13px; cursor: pointer; transition: all 0.2s; color: #555; }}
        .tag-pill:hover {{ border-color: #d4a832; color: #1a1a1a; }}
        .tag-pill.active {{ background: #d4a832; color: #1a1a1a; border-color: #d4a832; font-weight: 600; }}
        .posts-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 24px; max-width: 1100px; margin: 24px auto; padding: 0 20px; }}
        .post-card {{ background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.06); border: 1px solid #e8e8e0; transition: transform 0.2s, box-shadow 0.2s; position: relative; }}
        .post-card:hover {{ transform: translateY(-3px); box-shadow: 0 8px 24px rgba(0,0,0,0.1); }}
        .post-card a {{ text-decoration: none; color: inherit; display: block; }}
        .post-card.hidden {{ display: none; }}
        .card-img {{ position: relative; width: 100%; height: 200px; overflow: hidden; background: #eee; }}
        .card-img img {{ width: 100%; height: 100%; object-fit: cover; }}
        .card-img .img-fallback {{ display: none; position: absolute; inset: 0; background: var(--fallback-bg); align-items: center; justify-content: center; font-size: 64px; font-weight: 700; color: rgba(255,255,255,0.7); }}
        .card-img.no-img img {{ display: none; }}
        .card-img.no-img .img-fallback {{ display: flex; }}
        .play-btn {{ position: absolute; bottom: 10px; right: 10px; width: 44px; height: 44px; background: rgba(255,0,0,0.85); border-radius: 50%; display: flex; align-items: center; justify-content: center; z-index: 2; transition: transform 0.2s; box-shadow: 0 2px 6px rgba(0,0,0,0.3); }}
        .play-btn:hover {{ transform: scale(1.1); }}
        .play-btn svg {{ margin-left: 2px; }}
        .post-card-body {{ padding: 16px 20px 18px; }}
        .post-card-body h3 {{ font-size: 16px; color: #1a1a1a; line-height: 1.45; font-weight: 600; }}
        .post-card-body .topic {{ font-size: 13px; color: #666; margin-top: 6px; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
        .card-meta {{ display: flex; align-items: center; gap: 12px; margin-top: 10px; font-size: 12px; color: #999; }}
        .card-meta .read-time {{ color: #b08c1a; font-weight: 500; }}
        .card-tags {{ display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }}
        .card-tag {{ padding: 2px 8px; border-radius: 10px; background: #f5f0e0; font-size: 11px; color: #8b6914; }}
        .bottom-bar {{ position: fixed; bottom: 0; left: 0; width: 100%; display: flex; z-index: 1000; box-shadow: 0 -2px 8px rgba(0,0,0,0.15); }}
        .bottom-bar a {{ flex: 1; display: flex; align-items: center; justify-content: center; min-height: 50px; font-size: 16px; font-weight: bold; text-decoration: none; }}
        .bottom-bar .order {{ background: #1a1a1a; color: #fff; }}
        .bottom-bar .whatsapp {{ background: #25D366; color: #fff; }}
        .post-count {{ text-align: center; color: #999; font-size: 13px; margin-top: 16px; }}
        .no-results {{ text-align: center; color: #999; font-size: 15px; padding: 60px 20px; display: none; }}
        .all-articles {{ max-width: 1100px; margin: 40px auto 20px; padding: 24px 20px; background: #fff; border-radius: 12px; border: 1px solid #e8e8e0; }}
        .all-articles h2 {{ font-size: 18px; color: #1a1a1a; margin-bottom: 12px; }}
        .all-articles ul {{ list-style: none; padding: 0; columns: 2; column-gap: 24px; }}
        .all-articles li {{ padding: 4px 0; break-inside: avoid; }}
        .all-articles a {{ color: #333; font-size: 13px; text-decoration: none; line-height: 1.5; }}
        .all-articles a:hover {{ color: #b08c1a; text-decoration: underline; }}
        @media (max-width: 600px) {{
            .posts-grid {{ grid-template-columns: 1fr; gap: 16px; padding: 0 12px; }}
            .hero h1 {{ font-size: 21px; }}
            .hero {{ padding: 24px 16px 18px; }}
            .card-img {{ height: 180px; }}
            .tag-filters {{ padding: 0 12px; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <a class="brand" href="{BLOG_BASE_URL}">BulkPlainTshirt.com</a>
        <br><span class="sub">Own Knitted Blank Wears | <a href="https://sale91.com">sale91.com</a></span>
    </div>

    <section class="hero">
        <h1>Plain T-Shirt Blog</h1>
        <p>Expert guides on wholesale blank tees, GSM fabric, printing techniques &amp; bulk ordering from Tiruppur</p>
    </section>

    <nav class="nav-links">
        <a href="https://sale91.com" style="background:#d4a832;color:#1a1a1a;border-color:#d4a832;font-weight:700;">Buy Now</a>
        <a href="/catalog/index.html">Catalog</a>
        <a href="/p/FQA.html">FAQ</a>
        <a href="https://whatsapp.sale91.com">WhatsApp</a>
    </nav>

    <div class="search-box">
        <input type="text" id="search" placeholder="Search articles..." aria-label="Search articles">
    </div>

{tag_pills_html}

    <p class="post-count">{len(posts)} article{"s" if len(posts) != 1 else ""} published</p>

    <section class="posts-grid" id="posts">
{cards_html}    </section>

    <p class="no-results" id="no-results">No articles match your search.</p>

{footer_links_html}

    <div class="bottom-bar">
        <a class="order" href="https://sale91.com">Order Now</a>
        <a class="whatsapp" href="https://whatsapp.sale91.com">WhatsApp Us</a>
    </div>

    <script>
    (function() {{
        var search = document.getElementById('search');
        var cards = document.querySelectorAll('.post-card');
        var noResults = document.getElementById('no-results');
        var activeTag = 'all';

        function filterCards() {{
            var q = (search.value || '').toLowerCase();
            var visible = 0;
            cards.forEach(function(card) {{
                var title = (card.getAttribute('data-title') || '').toLowerCase();
                var topic = (card.getAttribute('data-topic') || '').toLowerCase();
                var tags = (card.getAttribute('data-tags') || '').toLowerCase();
                var matchSearch = !q || title.indexOf(q) !== -1 || topic.indexOf(q) !== -1 || tags.indexOf(q) !== -1;
                var matchTag = activeTag === 'all' || tags.indexOf(activeTag.toLowerCase()) !== -1;
                if (matchSearch && matchTag) {{
                    card.classList.remove('hidden');
                    visible++;
                }} else {{
                    card.classList.add('hidden');
                }}
            }});
            noResults.style.display = visible === 0 ? 'block' : 'none';
        }}

        if (search) search.addEventListener('input', filterCards);

        document.querySelectorAll('.tag-pill').forEach(function(btn) {{
            btn.addEventListener('click', function() {{
                document.querySelectorAll('.tag-pill').forEach(function(b) {{ b.classList.remove('active'); }});
                btn.classList.add('active');
                activeTag = btn.getAttribute('data-tag') || 'all';
                filterCards();
            }});
        }});
    }})();
    </script>
</body>
</html>'''


def repair_index_html(s3_client):
    """Rebuild index.html with card-based layout from blog_history.json (idempotent)."""
    try:
        index_html = build_blog_index_html()
        s3_client.put_object(
            Bucket=BLOG_S3_BUCKET,
            Key='p/index.html',
            Body=index_html.encode('utf-8'),
            ContentType='text/html; charset=utf-8',
            CacheControl='no-cache'
        )
        print(f"   \U0001f527 Index: Rebuilt card-based blog index")
    except Exception as e:
        print(f"   \u26a0\ufe0f Index: Could not rebuild index.html: {e}")


def build_rss_feed(new_post=None):
    """Build RSS 2.0 feed XML from blog_history.json.

    Args:
        new_post: Optional dict to include a post not yet saved to blog_history.json.
    Returns:
        RSS XML string for p/feed.xml.
    """
    import json as _json
    import html as _html

    posts = _load_blog_history_active()

    if new_post and new_post.get('slug'):
        if not any(p.get('slug') == new_post['slug'] for p in posts):
            posts.append(new_post)

    posts.sort(key=lambda p: p.get('date', ''), reverse=True)

    items_xml = ''
    for post in posts[:50]:
        p_title = _html.escape(post.get('title', ''))
        p_slug = post.get('slug', '')
        p_url = f"{BLOG_BASE_URL}/p/{p_slug}.html"
        p_desc = _html.escape(post.get('description', post.get('topic', '')))
        p_date = ''
        try:
            dt = datetime.fromisoformat(post.get('date', '').replace('Z', '+00:00'))
            p_date = dt.strftime('%a, %d %b %Y %H:%M:%S %z')
        except Exception:
            pass
        hero_url = f"{BLOG_BASE_URL}/p/{p_slug}-hero.webp"

        # Build tag elements
        tags_xml = ''
        for tag in post.get('tags', []):
            tags_xml += f'      <category>{_html.escape(tag.strip())}</category>\n'

        items_xml += f'''    <item>
      <title>{p_title}</title>
      <link>{p_url}</link>
      <guid isPermaLink="true">{p_url}</guid>
      <description>{p_desc}</description>
      <pubDate>{p_date}</pubDate>
      <enclosure url="{hero_url}" type="image/webp"/>
{tags_xml}    </item>
'''

    now_rfc = datetime.now(pytz.timezone(TIMEZONE)).strftime('%a, %d %b %Y %H:%M:%S %z')
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Plain T-Shirt Blog | BulkPlainTshirt.com</title>
    <link>{BLOG_BASE_URL}/p/index.html</link>
    <description>Expert guides on wholesale plain t-shirts, GSM fabric, printing techniques from India's leading B2B manufacturer.</description>
    <language>en</language>
    <lastBuildDate>{now_rfc}</lastBuildDate>
    <atom:link href="{BLOG_BASE_URL}/p/feed.xml" rel="self" type="application/rss+xml"/>
    <image>
      <url>{BLOG_BASE_URL}/catalog/img/logo.png</url>
      <title>BulkPlainTshirt.com</title>
      <link>{BLOG_BASE_URL}</link>
    </image>
{items_xml}  </channel>
</rss>'''


def build_blog_widget_html(max_posts=3):
    """Build a small HTML snippet showing latest blog posts (for embedding in catalog/other pages).

    Returns:
        HTML string for p/blog-widget.html (embeddable snippet).
    """
    import json as _json
    import html as _html

    posts = _load_blog_history_active()

    posts.sort(key=lambda p: p.get('date', ''), reverse=True)
    posts = posts[:max_posts]

    if not posts:
        return ''

    cards = ''
    for post in posts:
        p_slug = post.get('slug', '')
        p_title = _html.escape(post.get('title', ''))
        hero_url = f"{BLOG_BASE_URL}/p/{p_slug}-hero.webp"
        post_url = f"{BLOG_BASE_URL}/p/{p_slug}.html"
        p_date = ''
        try:
            dt = datetime.fromisoformat(post.get('date', '').replace('Z', '+00:00'))
            p_date = dt.strftime('%b %d, %Y')
        except Exception:
            pass
        cards += f'''  <a href="{post_url}" style="display:block;text-decoration:none;color:inherit;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 6px rgba(0,0,0,0.06);border:1px solid #e8e8e0;transition:transform 0.2s;flex:1;min-width:240px;">
    <img src="{hero_url}" alt="{p_title}" loading="lazy" style="width:100%;height:140px;object-fit:cover;" onerror="this.style.display='none'">
    <div style="padding:12px 14px;">
      <div style="font-size:14px;font-weight:600;color:#1a1a1a;line-height:1.4;">{p_title}</div>
      <div style="font-size:11px;color:#999;margin-top:6px;">{p_date}</div>
    </div>
  </a>
'''

    return f'''<!-- BulkPlainTshirt Blog Widget — Latest Articles -->
<div style="max-width:900px;margin:30px auto;padding:0 16px;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;">
    <h3 style="font-size:18px;color:#1a1a1a;font-weight:700;margin:0;">Latest from Our Blog</h3>
    <a href="{BLOG_BASE_URL}/p/index.html" style="font-size:13px;color:#d4a832;text-decoration:none;font-weight:500;">View All &rarr;</a>
  </div>
  <div style="display:flex;gap:16px;flex-wrap:wrap;">
{cards}  </div>
</div>'''


def _extract_blog_excerpt(html_content, max_words=200):
    """Pull a clean text excerpt from a generated blog HTML.

    Strategy: take the first <p> inside the main container that isn't the
    breadcrumb or hero caption. Strip HTML, collapse whitespace, truncate at
    word boundary. Used for llms-full.txt so AI bots get content without
    fetching the full page.
    """
    import re as _re
    # Strip <head>, <style>, <script> first — only want body text
    body_only = _re.sub(r'<head\b[^>]*>.*?</head>', '', html_content, flags=_re.DOTALL | _re.IGNORECASE)
    body_only = _re.sub(r'<(style|script)\b[^>]*>.*?</\1>', '', body_only, flags=_re.DOTALL | _re.IGNORECASE)
    body_only = _re.sub(r'<header\b[^>]*>.*?</header>', '', body_only, flags=_re.DOTALL | _re.IGNORECASE)

    # Collect all <p> texts
    paragraphs = _re.findall(r'<p[^>]*>(.+?)</p>', body_only, flags=_re.DOTALL | _re.IGNORECASE)
    text_chunks = []
    for p in paragraphs:
        clean = _re.sub(r'<[^>]+>', '', p)
        clean = _re.sub(r'\s+', ' ', clean).strip()
        # Skip breadcrumbs / very short fluff / video captions
        if len(clean) < 60:
            continue
        if 'Home' in clean and '›' in clean:
            continue
        text_chunks.append(clean)
        if sum(len(c.split()) for c in text_chunks) >= max_words:
            break

    if not text_chunks:
        return ""
    joined = " ".join(text_chunks)
    words = joined.split()
    if len(words) > max_words:
        joined = " ".join(words[:max_words]) + "..."
    return joined


def _cluster_posts_by_topic(posts):
    """Group blog posts into topic clusters using tag analysis.

    Returns dict[topic_name → list of posts] with semantically meaningful keys
    like 'GSM & fabric weight', 'Printing methods', 'Wholesale mistakes'.

    Each post counted once (first matching cluster wins). Posts that don't
    fit any cluster go to 'general'.
    """
    # ORDER MATTERS — first match wins. List specific clusters before general ones.
    # E.g. a post about "320 GSM hoodie" should land in Hoodies cluster (specific),
    # not in GSM cluster (general) just because "gsm" appears.
    TOPIC_KEYWORDS = {
        "Hoodies & winter wear":    ["hoodie", "sweatshirt", "320 gsm", "430 gsm", "winter"],
        "Streetwear & oversized":   ["oversized", "drop-shoulder", "dropshoulder", "240 gsm", "streetwear", "acid wash", "acid-wash"],
        "Printing methods (DTG/DTF/Screen)": ["dtg", "dtf", "screen print", "sublimation", "vinyl", "htv", "puff"],
        "Bulk wholesale mistakes":  ["mistake", "lakh", "loss", "wasted", "ruined", "lost", "returned"],
        "Fabric quality & testing": ["bio-wash", "biowash", "shrinkage", "stitching", "seam", "spi", "quality test", "colorfast"],
        "Business & pricing":       ["price", "pricing", "moq", "wholesale", "bulk order", "supplier", "mrp", "profit", "margin"],
        "Delivery & dispatch":      ["delivery", "dispatch", "shipping", "train", "pan india"],
        "GSM & fabric weight":      ["gsm", "fabric weight", "180", "200", "220"],
    }
    clusters = {k: [] for k in TOPIC_KEYWORDS}
    clusters["General"] = []

    for post in posts:
        haystack = (post.get('title', '') + ' ' + ' '.join(post.get('tags', []))).lower()
        matched = False
        for topic, kws in TOPIC_KEYWORDS.items():
            if any(kw in haystack for kw in kws):
                clusters[topic].append(post)
                matched = True
                break
        if not matched:
            clusters["General"].append(post)
    return clusters


def _build_and_upload_p_llms_txt(s3_client, current_blog=None):
    """Rebuild /p/llms.txt fresh from blog_history.json each daily run.

    current_blog: dict {title, url, date, tags} for the blog being published
    RIGHT NOW. This is needed because save_blog_history runs AFTER publish_blog_to_s3,
    so blog_history.json doesn't yet contain today's entry when this function fires.
    We splice it in as the newest entry so the rebuild reflects current state.

    Structure (AI-friendly):
    1. Header with author identity + cross-links to other discovery files
    2. Latest 10 articles (with dates) — what's fresh
    3. By topic clusters — what we have depth in
    4. Full chronological list — complete index for bots that want everything
    """
    history = _load_blog_history_active()
    # Splice in the current blog if not already in history (it usually isn't,
    # since save_blog_history runs after this function)
    if current_blog and current_blog.get('url'):
        if not any(h.get('url') == current_blog['url'] for h in history):
            history.append(current_blog)
    history_sorted = sorted(history, key=lambda h: h.get('date', ''), reverse=True)

    out = []
    out.append("# BulkPlainTshirt.com — Blog URL Directory")
    out.append("# Auto-updated daily by yt-shorts-bot from blog_history.json")
    out.append("# Author: Ketu R, Founder — https://www.bulkplaintshirt.com/#ketu-r")
    out.append("# YouTube: https://www.youtube.com/@BulkPlainTshirt_com (40K+ subs)")
    out.append("#")
    out.append("# RELATED DISCOVERY FILES:")
    out.append("#   Short profile:   https://www.bulkplaintshirt.com/llms.txt")
    out.append("#   Rich content:    https://www.bulkplaintshirt.com/llms-full.txt")
    out.append("#   Live excerpts:   https://www.bulkplaintshirt.com/p/llms-full.txt")
    out.append("#   Sitemap (XML):   https://www.bulkplaintshirt.com/p/map.xml")
    out.append("#   Blog hub:        https://www.bulkplaintshirt.com/p/index.html")
    out.append("")

    # Section: Latest 10 with dates
    out.append("## Latest 10 articles (newest first)")
    out.append("")
    for p in history_sorted[:10]:
        d = (p.get('date') or '')[:10]
        out.append(f"- [{d}] {p.get('title','')}: {p.get('url','')}")
    out.append("")

    # Section: By topic
    out.append("## By topic (clustered by content theme)")
    out.append("")
    clusters = _cluster_posts_by_topic(history_sorted)
    for topic, posts in clusters.items():
        if not posts:
            continue
        out.append(f"### {topic} ({len(posts)} articles)")
        for p in posts[:15]:  # cap per cluster
            out.append(f"- {p.get('title','')}: {p.get('url','')}")
        if len(posts) > 15:
            out.append(f"- ...and {len(posts) - 15} more in this topic")
        out.append("")

    # Section: All chronological
    out.append(f"## Full archive ({len(history_sorted)} total articles, newest first)")
    out.append("")
    seen = set()
    for p in history_sorted:
        u = p.get('url', '')
        if not u or u in seen:
            continue
        seen.add(u)
        d = (p.get('date') or '')[:10]
        out.append(f"- [{d}] {p.get('title','')}: {u}")

    body = "\n".join(out) + "\n"
    s3_client.put_object(
        Bucket=BLOG_S3_BUCKET,
        Key='p/llms.txt',
        Body=body.encode('utf-8'),
        ContentType='text/plain; charset=utf-8',
        CacheControl='no-cache',
    )
    print(f"   📤 Blog S3: Rebuilt p/llms.txt ({len(seen)} articles, {len([c for c in clusters.values() if c])} topic clusters)")


def _build_and_upload_llms_full(s3_client, latest_html, latest_title, latest_url, latest_date,
                                 max_articles=30):
    """Build and upload /p/llms-full.txt — the rich AI-search variant of llms.txt
    (per llmstxt.org spec). Includes recent article excerpts so AI bots can cite
    content without crawling each URL individually.

    Cap at max_articles entries — full llms-full.txt can grow large; AI bots
    fetch the whole file each time, so keep payload reasonable.
    """
    posts = _load_blog_history_active()
    posts.sort(key=lambda p: p.get('date', ''), reverse=True)

    # Build the latest entry's excerpt fresh from the HTML we just published
    latest_excerpt = _extract_blog_excerpt(latest_html, max_words=250)

    sections = []
    # ── Header ──
    sections.append(
        "# BulkPlainTshirt.com / Sale91.com — Live Article Index (llms-full.txt at /p/)\n\n"
        "> India's leading B2B plain t-shirt manufacturer. We knit our own fabric in Tiruppur, "
        "manufacture 20+ blank-apparel categories, and ship PAN-India from our Delhi warehouse. "
        "Plain tees, hoodies, dropshoulder, sweatshirts in 180/200/210/220/240/320/430 GSM.\n\n"
        "## About This File\n\n"
        "This is the **dynamic /p/llms-full.txt** — regenerated by our daily content pipeline. "
        "AI assistants (ChatGPT, Claude, Perplexity, Gemini, You.com) can use this for live "
        f"article excerpts WITHOUT crawling each blog URL individually.\n\n"
        f"Last updated: {latest_date}\n\n"
        "**For our full company profile, product catalog, glossary, and citation guidance,**\n"
        "see the static companion: https://www.bulkplaintshirt.com/llms-full.txt\n\n"
        "## Author Identity\n\n"
        "- Author of all content: **Ketu R**, Founder, B2B Textile Manufacturing Expert\n"
        "- 17+ years experience; manufactures in Tiruppur, ships from Delhi warehouse\n"
        "- Identity URI: https://www.bulkplaintshirt.com/#ketu-r\n"
        "- YouTube (40K+ subs): https://www.youtube.com/@BulkPlainTshirt_com\n"
        "- Instagram: https://www.instagram.com/bulkplaintshirt_com/\n\n"
        "## Quick Links\n\n"
        "- B2B order site: https://sale91.com/\n"
        "- WhatsApp: https://whatsapp.sale91.com\n"
        "- Catalog: https://www.bulkplaintshirt.com/catalog/\n"
        "- All blogs hub: https://www.bulkplaintshirt.com/p/index.html\n"
        "- Sitemap: https://www.bulkplaintshirt.com/p/map.xml\n\n"
        "---\n"
    )

    # ── Topic clusters section ──
    clusters = _cluster_posts_by_topic(posts)
    sections.append("\n## Articles by Topic Cluster\n")
    sections.append("\n*Articles grouped by content theme — use this to find depth on a specific area.*\n\n")
    for topic, cluster_posts in clusters.items():
        if not cluster_posts:
            continue
        sections.append(f"\n### {topic} — {len(cluster_posts)} articles\n\n")
        for p in cluster_posts[:8]:  # 8 per cluster preview
            d = (p.get('date') or '')[:10]
            sections.append(f"- [{d}] {p.get('title','')}: {p.get('url','')}\n")
        if len(cluster_posts) > 8:
            sections.append(f"- *...{len(cluster_posts) - 8} more in this cluster*\n")
    sections.append("\n---\n")

    # ── Recent articles with full excerpts ──
    sections.append("\n## Recent Articles with Excerpts (most recent first)\n")

    # ── Most recent (the one we just published — full fresh excerpt) ──
    if latest_excerpt:
        sections.append(
            f"\n### {latest_title}\n\n"
            f"- URL: {latest_url}\n"
            f"- Date: {latest_date}\n\n"
            f"{latest_excerpt}\n\n---\n"
        )

    # ── Older articles from blog_history (excerpts from previously-cached field
    #    if present; otherwise just title + URL). New blogs will accumulate
    #    excerpts over time as they get re-processed. ──
    seen_urls = {latest_url}
    count = 1
    for post in posts:
        if count >= max_articles:
            break
        post_url = post.get('url', '')
        if not post_url or post_url in seen_urls:
            continue
        seen_urls.add(post_url)
        post_title = post.get('title', '')
        post_date = (post.get('date') or '')[:10]
        excerpt = post.get('excerpt', '')  # filled in by save_blog_history starting tonight
        if excerpt:
            sections.append(
                f"\n### {post_title}\n\n"
                f"- URL: {post_url}\n"
                f"- Date: {post_date}\n\n"
                f"{excerpt}\n\n---\n"
            )
        else:
            sections.append(
                f"\n### {post_title}\n\n"
                f"- URL: {post_url}\n"
                f"- Date: {post_date}\n"
            )
        count += 1

    body = "".join(sections)
    s3_client.put_object(
        Bucket=BLOG_S3_BUCKET,
        Key='p/llms-full.txt',
        Body=body.encode('utf-8'),
        ContentType='text/plain; charset=utf-8',
        CacheControl='no-cache'
    )
    size_kb = len(body) // 1024
    print(f"   📤 Blog S3: Built p/llms-full.txt ({count} articles, {size_kb}KB)")


def upload_brand_assets(s3_client, cloudfront_client):
    """Upload the brand avatar + logo to the two S3 keys every blog references.

    These were 403 (missing), which broke the author avatar AND the og:image
    share-preview on every post. Idempotent: re-uploads from the repo copies each
    run (cheap, keeps S3 in sync if we change the source art)."""
    # NOTE: the GitHub IAM user can only PutObject under catalog/img/* and p/* —
    # the imges/ prefix is denied — so the avatar lives under catalog/img/.
    assets = [
        ("assets/brand/ketu-author.webp", "catalog/img/ketu-author.webp", "image/webp"),
        ("assets/brand/logo.png", "catalog/img/logo.png", "image/png"),
    ]
    uploaded = []
    for local_path, s3_key, content_type in assets:
        if not os.path.exists(local_path):
            print(f"   ⚠️ Brand asset missing in repo: {local_path}")
            continue
        try:
            with open(local_path, "rb") as f:
                body = f.read()
            s3_client.put_object(
                Bucket=BLOG_S3_BUCKET,
                Key=s3_key,
                Body=body,
                ContentType=content_type,
                CacheControl="public, max-age=604800",
            )
            uploaded.append("/" + s3_key)
            print(f"   📤 Brand asset: {s3_key} ({len(body)//1024}KB)")
        except Exception as e:
            print(f"   ⚠️ Brand asset upload failed for {s3_key}: {e}")

    if uploaded:
        try:
            cloudfront_client.create_invalidation(
                DistributionId=BLOG_CLOUDFRONT_DIST_ID,
                InvalidationBatch={
                    "Paths": {"Quantity": len(uploaded), "Items": uploaded},
                    "CallerReference": f"brand-{int(time.time())}",
                },
            )
            print(f"   🔧 Brand asset: CloudFront invalidation for {len(uploaded)} files")
        except Exception as e:
            print(f"   ⚠️ Brand asset: CloudFront invalidation failed: {e}")


def inject_backlinks_to_new_post(s3_client, new_slug, new_title, new_url, tags=None, max_targets=3):
    """Add an inbound internal link to a freshly published post FROM its top
    topically-related OLDER posts.

    The generator only links new->old (a new post links to older ones), so the
    newest posts arrive as near-orphans — only the index points at them — which
    is a classic cause of 'Crawled, currently not indexed'. This closes the loop
    by editing a few related older posts to link forward to the new one, so each
    post accrues both forward and backward internal links over time.

    Idempotent: skips a target that already links to new_url; the injected block
    has a stable marker so re-runs update (dedupe + cap) instead of duplicating.
    Returns the list of S3 paths it modified (for CloudFront invalidation)."""
    if not os.path.exists(BLOG_HISTORY_FILE):
        return []
    try:
        with open(BLOG_HISTORY_FILE) as f:
            history = json.load(f)
    except Exception:
        return []

    # Score older posts by tag + slug-word overlap (same heuristic as forward links).
    cur_tags = set(t.lower().strip() for t in (tags or []) if t)
    cur_words = set(re.findall(r'[a-z]{4,}', new_slug.lower()))
    scored = []
    for h in history:
        h_slug = h.get('slug', '')
        if not h_slug or h_slug == new_slug:
            continue
        h_tags = set(t.lower().strip() for t in h.get('tags', []) if t)
        h_words = set(re.findall(r'[a-z]{4,}', h_slug.lower()))
        score = len(cur_tags & h_tags) * 3 + len(cur_words & h_words)
        if score > 0:
            scored.append((score, h))
    scored.sort(key=lambda x: -x[0])
    targets = [h for _, h in scored[:max_targets]]
    if not targets:  # no topical match — fall back to the 2 most recent older posts
        recent = sorted(history, key=lambda h: h.get('date', ''), reverse=True)
        targets = [h for h in recent if h.get('slug') and h.get('slug') != new_slug][:2]

    def esc(s):
        return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;'))

    new_li = (f'<li style="margin:6px 0;"><a href="{new_url}" '
              f'style="color:#007bff;text-decoration:underline;font-size:14px;">{esc(new_title)}</a></li>')
    block_open = '<nav class="bpt-related-fresh" style="max-width:800px;margin:32px auto;padding:0 16px;">'
    block_re = re.compile(r'<nav class="bpt-related-fresh".*?</nav>', re.DOTALL)
    li_re = re.compile(r'<li style="margin:6px 0;"><a href="([^"]+)".*?</a></li>', re.DOTALL)

    modified = []
    for h in targets:
        key = f"p/{h['slug']}.html"
        try:
            resp = s3_client.get_object(Bucket=BLOG_S3_BUCKET, Key=key)
            html = resp['Body'].read().decode('utf-8')
        except Exception as e:
            print(f"   ⚠️ Backlink: could not read {key}: {e}")
            continue

        if new_url in html:
            continue  # already links to the new post (forward link or prior run)

        existing = block_re.search(html)
        items = []
        if existing:
            items = [(u, m) for u, m in [(mm.group(1), mm.group(0)) for mm in li_re.finditer(existing.group(0))]]
        # Prepend new link, dedupe by URL, cap at 5 newest.
        ordered = [new_li] + [m for u, m in items if u != new_url]
        seen, capped = set(), []
        for li in ordered:
            u = re.search(r'href="([^"]+)"', li)
            u = u.group(1) if u else li
            if u in seen:
                continue
            seen.add(u)
            capped.append(li)
            if len(capped) >= 5:
                break
        block = (f'{block_open}\n'
                 f'<h3 style="font-size:16px;color:#0f3460;margin:0 0 10px;">Related guides</h3>\n'
                 f'<ul style="list-style:none;padding:0;margin:0;">\n' + "\n".join(capped) + '\n</ul>\n</nav>')

        if existing:
            html = block_re.sub(lambda _: block, html, count=1)
        elif '<section class="author-card"' in html:
            html = html.replace('<section class="author-card"', f'{block}\n<section class="author-card"', 1)
        elif '</body>' in html:
            html = html.replace('</body>', f'{block}\n</body>', 1)
        else:
            continue

        try:
            s3_client.put_object(
                Bucket=BLOG_S3_BUCKET, Key=key, Body=html.encode('utf-8'),
                ContentType='text/html; charset=utf-8', CacheControl='public, max-age=86400')
            modified.append(f"/p/{h['slug']}.html")
            print(f"   🔗 Backlink: linked {h['slug']}.html -> {new_slug}")
        except Exception as e:
            print(f"   ⚠️ Backlink: could not update {key}: {e}")

    return modified


def backfill_internal_links():
    """One-time mesh builder: walk every post in blog_history and inject an inbound
    link into each one's related posts. Fixes existing orphan posts (discovered
    only via the sitemap → 'crawled, not indexed') that predate the per-publish
    back-link injection. Idempotent — safe to re-run."""
    import boto3
    ak = os.environ.get("AWS_ACCESS_KEY_ID")
    sk = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not ak or not sk:
        print("❌ AWS credentials not found")
        return 1
    s3 = boto3.client("s3", region_name="ap-south-1",
                      aws_access_key_id=ak, aws_secret_access_key=sk)
    cf = boto3.client("cloudfront", region_name="ap-south-1",
                      aws_access_key_id=ak, aws_secret_access_key=sk)
    if not os.path.exists(BLOG_HISTORY_FILE):
        print("❌ blog_history.json not found")
        return 1
    with open(BLOG_HISTORY_FILE) as f:
        history = json.load(f)

    print(f"🔗 Backfill: building internal-link mesh across {len(history)} posts...")
    modified = set()
    for i, p in enumerate(history, 1):
        slug = p.get("slug")
        title = p.get("title")
        if not slug or not title:
            continue
        url = p.get("url") or f"{BLOG_BASE_URL}/p/{slug}.html"
        try:
            paths = inject_backlinks_to_new_post(s3, slug, title, url, tags=p.get("tags"))
            modified.update(paths)
            print(f"   [{i}/{len(history)}] {slug} → linked into {len(paths)} post(s)")
        except Exception as e:
            print(f"   [{i}/{len(history)}] {slug}: error {e}")

    print(f"🔗 Backfill: modified {len(modified)} post file(s)")
    if modified:
        try:
            cf.create_invalidation(
                DistributionId=BLOG_CLOUDFRONT_DIST_ID,
                InvalidationBatch={
                    "Paths": {"Quantity": 1, "Items": ["/p/*"]},
                    "CallerReference": f"backfill-{int(time.time())}",
                },
            )
            print("   🔄 Backfill: CloudFront invalidation /p/* created")
        except Exception as e:
            print(f"   ⚠️ Backfill: CloudFront invalidation failed: {e}")
    return 0


def rewrite_thin_posts(only_slug=None):
    """Rewrite thin legacy pages in place per rewrite_plan.json. Each 'rewrite'
    becomes a full article on its existing URL (forced slug, no video); each
    'redirect' becomes a canonical+meta-refresh stub to its survivor; 'leave' is
    skipped. Originals are backed up to p/_backup/ first. Reuses generate_blog_post
    + publish_blog_to_s3 so SEO treatment matches real posts exactly."""
    import boto3
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    ak = os.environ.get("AWS_ACCESS_KEY_ID")
    sk = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not anthropic_key or not ak or not sk:
        print("❌ Missing ANTHROPIC_API_KEY or AWS credentials")
        return 1
    if not os.path.exists("rewrite_plan.json"):
        print("❌ rewrite_plan.json not found")
        return 1

    claude = anthropic.Anthropic(api_key=anthropic_key)
    cost = CostTracker()
    s3 = boto3.client("s3", region_name="ap-south-1", aws_access_key_id=ak, aws_secret_access_key=sk)
    cf = boto3.client("cloudfront", region_name="ap-south-1", aws_access_key_id=ak, aws_secret_access_key=sk)
    plan = json.load(open("rewrite_plan.json"))

    # Load history so rewritten pages join the corpus (index, sitemap, link mesh).
    history = []
    if os.path.exists(BLOG_HISTORY_FILE):
        history = json.load(open(BLOG_HISTORY_FILE))
    hist_by_slug = {h.get("slug"): h for h in history}
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")

    redirect_invalidations = []
    done = 0
    for p in plan:
        slug = p["slug"]
        if only_slug and slug != only_slug:
            continue
        action = p["action"]
        if action == "leave":
            print(f"   ⏭️  leave {slug}")
            continue

        # Back up the ORIGINAL once. If a backup already exists this page was
        # processed before — skip it in batch mode (so re-runs don't re-bill or
        # clobber the original backup with an already-rewritten copy).
        backup_key = f"p/_backup/{slug}.html"
        backup_exists = True
        try:
            s3.head_object(Bucket=BLOG_S3_BUCKET, Key=backup_key)
        except Exception:
            backup_exists = False
        if backup_exists and not only_slug:
            print(f"   ⏭️  {slug} already processed (backup exists) — skipping")
            continue
        if not backup_exists:
            try:
                s3.copy_object(Bucket=BLOG_S3_BUCKET,
                               CopySource={"Bucket": BLOG_S3_BUCKET, "Key": f"p/{slug}.html"},
                               Key=backup_key)
                print(f"   💾 backed up p/{slug}.html → {backup_key}")
            except Exception as e:
                print(f"   ⚠️ backup skipped for {slug}: {e}")

        if action == "redirect":
            target = f"{BLOG_BASE_URL}/p/{p['redirect_to_slug']}.html"
            stub = (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
                    f'<title>Moved</title><link rel="canonical" href="{target}">'
                    f'<meta http-equiv="refresh" content="0; url={target}">'
                    f'<script>location.replace({json.dumps(target)})</script></head>'
                    f'<body>This page has moved to <a href="{target}">{target}</a>.</body></html>')
            s3.put_object(Bucket=BLOG_S3_BUCKET, Key=f"p/{slug}.html",
                          Body=stub.encode("utf-8"), ContentType="text/html; charset=utf-8",
                          CacheControl="public, max-age=86400")
            history = [h for h in history if h.get("slug") != slug]  # drop from corpus
            redirect_invalidations.append(f"/p/{slug}.html")
            print(f"   ↪️  redirect {slug} → {p['redirect_to_slug']}")
            done += 1
            continue

        # action == "rewrite": generate a full article at the SAME url.
        print(f"   ✍️  rewriting {slug} …")
        html, _, blog_url, blog_images = generate_blog_post(
            claude_client=claude, cost_tracker=cost,
            topic=p["topic"], title=p["new_title"], description=p["meta_description"],
            script_english="", tags=p.get("tags", []), hook_text="",
            vid_id=None, vid_url=None, video_prompts=None, force_slug=slug)
        if not html:
            print(f"   ❌ generation failed for {slug}")
            continue
        # Add/refresh the history entry BEFORE publish so index/sitemap include it.
        entry = hist_by_slug.get(slug)
        if entry:
            entry.update({"title": p["new_title"], "url": blog_url})
        else:
            entry = {"date": today, "title": p["new_title"], "slug": slug,
                     "url": blog_url, "topic": p["topic"][:80], "vid_url": ""}
            history.append(entry); hist_by_slug[slug] = entry
        # publish_blog_to_s3 reads BLOG_HISTORY_FILE for related links, so persist first.
        json.dump(history, open(BLOG_HISTORY_FILE, "w"), ensure_ascii=False, indent=2)
        ok = publish_blog_to_s3(html, slug, p["new_title"], blog_url, blog_images, vid_id=None, tags=p.get("tags", []))
        print(f"   {'✅' if ok else '❌'} {slug}: {len(html.split())} words, published={ok}")
        done += 1

    json.dump(history, open(BLOG_HISTORY_FILE, "w"), ensure_ascii=False, indent=2)
    if redirect_invalidations:
        try:
            cf.create_invalidation(DistributionId=BLOG_CLOUDFRONT_DIST_ID,
                InvalidationBatch={"Paths": {"Quantity": len(redirect_invalidations), "Items": redirect_invalidations},
                                   "CallerReference": f"rewrite-redir-{int(time.time())}"})
        except Exception as e:
            print(f"   ⚠️ redirect invalidation failed: {e}")
    print(f"\n🔁 rewrite-thin: processed {done} page(s)")
    print(cost.summary())
    return 0


def publish_blog_to_s3(html_content, slug, title, blog_url, blog_images=None, vid_id=None, tags=None):
    """Upload blog HTML + images to S3, update index.html, map.xml, llms.txt, and invalidate CloudFront."""
    import boto3

    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")

    aws_key = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret = os.environ.get('AWS_SECRET_ACCESS_KEY')
    if not aws_key or not aws_secret:
        print("   ⚠️ Blog S3: AWS credentials not found, skipping publish")
        return False

    s3 = boto3.client('s3', region_name='ap-south-1',
                       aws_access_key_id=aws_key, aws_secret_access_key=aws_secret)
    cloudfront = boto3.client('cloudfront', region_name='ap-south-1',
                               aws_access_key_id=aws_key, aws_secret_access_key=aws_secret)

    invalidation_paths = []

    # Repair any existing posts missing JSON-LD + bottom bar (one-time, idempotent)
    repair_existing_blog_posts(s3, cloudfront)

    # Repair sitemap: fix non-www URLs + add missing static pages (one-time, idempotent)
    repair_sitemap(s3)

    # Repair index.html: fix misplaced entries (one-time, idempotent)
    repair_index_html(s3)

    # Upload brand avatar + logo (fixes 403 author image + og:image on all posts)
    upload_brand_assets(s3, cloudfront)

    try:
        # ── 1. Upload blog HTML ──
        blog_key = f"p/{slug}.html"
        s3.put_object(
            Bucket=BLOG_S3_BUCKET,
            Key=blog_key,
            Body=html_content.encode('utf-8'),
            ContentType='text/html; charset=utf-8',
            CacheControl='public, max-age=86400'
        )
        print(f"   📤 Blog S3: Uploaded {blog_key}")
        invalidation_paths.append(f"/p/{slug}.html")

        # ── 1b. Upload blog images ──
        if blog_images:
            for img_bytes, filename in blog_images:
                try:
                    img_key = f"p/{slug}-{filename}"
                    content_type = "image/webp" if filename.endswith(".webp") else "image/jpeg"
                    s3.put_object(
                        Bucket=BLOG_S3_BUCKET,
                        Key=img_key,
                        Body=img_bytes,
                        ContentType=content_type,
                        CacheControl='public, max-age=2592000'  # 30 days for images
                    )
                    print(f"   📤 Blog S3: Uploaded {img_key} ({len(img_bytes)//1024}KB)")
                    # IG Graph API rejects .webp; upload a JPEG copy for carousel use
                    if filename.endswith(".webp"):
                        try:
                            from PIL import Image
                            import io as _io
                            jpg_filename = filename.replace(".webp", ".jpg")
                            jpg_key = f"p/{slug}-{jpg_filename}"
                            img_pil = Image.open(_io.BytesIO(img_bytes)).convert("RGB")
                            jpg_buf = _io.BytesIO()
                            img_pil.save(jpg_buf, format="JPEG", quality=85)
                            s3.put_object(
                                Bucket=BLOG_S3_BUCKET,
                                Key=jpg_key,
                                Body=jpg_buf.getvalue(),
                                ContentType="image/jpeg",
                                CacheControl='public, max-age=2592000',
                            )
                            print(f"   📤 Blog S3: Uploaded {jpg_key} (JPEG copy for IG carousel)")
                        except Exception as je:
                            print(f"   ⚠️ Blog S3: JPEG copy failed for {filename}: {je}")
                except Exception as e:
                    print(f"   ⚠️ Blog S3: Image upload failed for {filename}: {e}")

        # ── 2. Rebuild /p/index.html (card-based layout from blog_history) ──
        try:
            today_iso = datetime.now(pytz.timezone(TIMEZONE)).isoformat()
            new_post = {"date": today_iso, "title": title, "slug": slug,
                        "url": blog_url, "topic": "", "vid_url": ""}
            index_html = build_blog_index_html(new_post=new_post)
            s3.put_object(
                Bucket=BLOG_S3_BUCKET,
                Key='p/index.html',
                Body=index_html.encode('utf-8'),
                ContentType='text/html; charset=utf-8',
                CacheControl='no-cache'
            )
            print(f"   \U0001f4e4 Blog S3: Rebuilt card-based index.html with new post")
            invalidation_paths.append('/p/index.html')
        except Exception as e:
            print(f"   \u26a0\ufe0f Blog S3: Could not rebuild index.html: {e}")

        # ── 3. Rebuild /p/map.xml (full sitemap from blog_history) ──
        try:
            new_post_sitemap = {"slug": slug, "url": blog_url,
                                "date": datetime.now(pytz.timezone(TIMEZONE)).isoformat()}
            map_xml = build_sitemap_xml(new_post=new_post_sitemap)
            s3.put_object(
                Bucket=BLOG_S3_BUCKET,
                Key='p/map.xml',
                Body=map_xml.encode('utf-8'),
                ContentType='application/xml; charset=utf-8',
                CacheControl='no-cache'
            )
            print(f"   \U0001f4e4 Blog S3: Rebuilt map.xml with new post")
            invalidation_paths.append('/p/map.xml')
        except Exception as e:
            print(f"   \u26a0\ufe0f Blog S3: Could not rebuild map.xml: {e}")

        # ── 4. Rebuild /p/llms.txt — structured directory ──
        # Old behavior: append-only "title: url" forever (duplicates accumulate).
        # New: full rebuild from blog_history.json with sections — Latest 10 +
        # By Topic clusters + All chronological. Cleaner for AI bots.
        # current_blog passes today's blog explicitly because save_blog_history
        # runs AFTER this function (so blog_history.json doesn't have it yet).
        try:
            _build_and_upload_p_llms_txt(s3, current_blog={
                "title": title,
                "url": blog_url,
                "date": datetime.now(pytz.timezone(TIMEZONE)).isoformat(),
                "tags": [],  # tags not available in this scope; ok — fallback to General cluster
            })
            invalidation_paths.append('/p/llms.txt')
        except Exception as e:
            print(f"   ⚠️ Blog S3: Could not rebuild llms.txt: {e}")

        # ── 4b. Rebuild /p/llms-full.txt ──
        # Rich AI-search version of llms.txt: includes article excerpts so AI bots
        # (ChatGPT, Claude, Perplexity, Gemini) can cite content WITHOUT crawling
        # each URL. Per llmstxt.org spec. Built from blog_history.json metadata
        # plus a fresh excerpt extracted from the new blog's body.
        try:
            _build_and_upload_llms_full(s3, html_content, title, blog_url, today)
            invalidation_paths.append('/p/llms-full.txt')
        except Exception as e:
            print(f"   ⚠️ Blog S3: llms-full.txt skipped: {e}")

        # ── 5. Upload RSS feed ──
        try:
            today_iso = datetime.now(pytz.timezone(TIMEZONE)).isoformat()
            rss_new_post = {"date": today_iso, "title": title, "slug": slug,
                            "url": blog_url, "topic": "", "vid_url": ""}
            rss_xml = build_rss_feed(new_post=rss_new_post)
            s3.put_object(
                Bucket=BLOG_S3_BUCKET,
                Key='p/feed.xml',
                Body=rss_xml.encode('utf-8'),
                ContentType='application/rss+xml; charset=utf-8',
                CacheControl='no-cache'
            )
            print(f"   \U0001f4e4 Blog S3: Updated p/feed.xml (RSS)")
            invalidation_paths.append('/p/feed.xml')
        except Exception as e:
            print(f"   \u26a0\ufe0f Blog S3: Could not update feed.xml: {e}")

        # ── 6. Upload blog widget snippet ──
        try:
            widget_html = build_blog_widget_html()
            if widget_html:
                s3.put_object(
                    Bucket=BLOG_S3_BUCKET,
                    Key='p/blog-widget.html',
                    Body=widget_html.encode('utf-8'),
                    ContentType='text/html; charset=utf-8',
                    CacheControl='no-cache'
                )
                print(f"   \U0001f4e4 Blog S3: Updated p/blog-widget.html (widget)")
                invalidation_paths.append('/p/blog-widget.html')
        except Exception as e:
            print(f"   \u26a0\ufe0f Blog S3: Could not update blog-widget.html: {e}")

        # ── 6c. Inject inbound internal links to this new post from related older
        # posts (fixes new posts arriving as near-orphans → 'crawled, not indexed').
        try:
            back_paths = inject_backlinks_to_new_post(s3, slug, title, blog_url, tags=tags)
            invalidation_paths.extend(back_paths)
        except Exception as e:
            print(f"   ⚠️ Blog S3: backlink injection failed: {e}")

        # ── 7. Invalidate CloudFront ──
        if invalidation_paths:
            try:
                cloudfront.create_invalidation(
                    DistributionId=BLOG_CLOUDFRONT_DIST_ID,
                    InvalidationBatch={
                        'Paths': {
                            'Quantity': len(invalidation_paths),
                            'Items': invalidation_paths
                        },
                        'CallerReference': f"blog-{slug}-{int(time.time())}"
                    }
                )
                print(f"   🔄 Blog S3: CloudFront invalidation created for {len(invalidation_paths)} paths")
            except Exception as e:
                print(f"   ⚠️ Blog S3: CloudFront invalidation failed: {e}")

        # ── 8. Submit to search engines & AI crawlers ──
        submit_to_search_engines(blog_url, s3_client=s3)

        return True

    except Exception as e:
        print(f"   ❌ Blog S3 publish failed: {e}")
        return False


def save_blog_history(topic, title, slug, blog_url, vid_url, tags=None,
                      description="", word_count=0, excerpt=""):
    """Save blog post metadata to blog_history.json for tracking.

    excerpt: 200-word plain-text excerpt extracted from the blog body — used
    by llms-full.txt builder to give AI bots citable content without crawling.
    """
    try:
        history = []
        if os.path.exists(BLOG_HISTORY_FILE):
            with open(BLOG_HISTORY_FILE, "r") as f:
                history = json.load(f)

        history.append({
            "date": datetime.now(pytz.timezone(TIMEZONE)).isoformat(),
            "topic": topic,
            "title": title,
            "slug": slug,
            "url": blog_url,
            "vid_url": vid_url or "",
            "tags": list(tags)[:10] if tags else [],
            "description": (description or "")[:200],
            "word_count": word_count or 0,
            "excerpt": (excerpt or "")[:1500],  # cached for llms-full.txt
        })

        with open(BLOG_HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        print(f"   💾 Blog history saved ({len(history)} total posts)")
    except Exception as e:
        print(f"   ⚠️ Could not save blog history: {e}")


# ═══════════════════════════════════════════════════════════════════════
# SEARCH ENGINE INDEXING & AI SEARCH SUBMISSION
# ═══════════════════════════════════════════════════════════════════════

def ping_google_indexing_api(blog_url):
    """Use Google Indexing API to request instant indexing of a new blog post.
    Requires GOOGLE_SERVICE_ACCOUNT_JSON env var with service account credentials."""
    sa_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    if not sa_json:
        print("   ℹ️ Indexing: GOOGLE_SERVICE_ACCOUNT_JSON not set — skipping Google Indexing API")
        return False

    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as GoogleAuthRequest

        creds_dict = json.loads(sa_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=['https://www.googleapis.com/auth/indexing']
        )
        credentials.refresh(GoogleAuthRequest())

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {credentials.token}'
        }
        payload = {
            'url': blog_url,
            'type': 'URL_UPDATED'
        }
        resp = requests.post(
            'https://indexing.googleapis.com/v3/urlNotifications:publish',
            headers=headers,
            json=payload,
            timeout=15
        )
        if resp.status_code == 200:
            print(f"   ✅ Indexing: Google Indexing API — URL submitted successfully")
            return True
        else:
            print(f"   ⚠️ Indexing: Google Indexing API returned {resp.status_code}: {resp.text[:200]}")
            return False
    except ImportError:
        print("   ⚠️ Indexing: google-auth package not installed — pip install google-auth")
        return False
    except Exception as e:
        print(f"   ⚠️ Indexing: Google Indexing API failed: {e}")
        return False


def ping_indexnow(blog_url):
    """Submit URL to IndexNow API (Bing, Yandex, DuckDuckGo, AI search engines).
    This notifies Bing/Yandex/Naver/Seznam and indirectly feeds AI search like
    ChatGPT (via Bing), Copilot, Perplexity, etc."""
    try:
        payload = {
            'host': 'www.bulkplaintshirt.com',
            'key': INDEXNOW_API_KEY,
            'keyLocation': f'{BLOG_BASE_URL}/p/{INDEXNOW_API_KEY}.txt',
            'urlList': [blog_url]
        }

        # Submit to multiple IndexNow endpoints
        endpoints = [
            'https://api.indexnow.org/indexnow',
            'https://www.bing.com/indexnow',
            'https://yandex.com/indexnow',
        ]

        success = False
        for endpoint in endpoints:
            try:
                resp = requests.post(endpoint, json=payload, timeout=10,
                                     headers={'Content-Type': 'application/json'})
                if resp.status_code in (200, 202):
                    engine = endpoint.split('/')[2]
                    print(f"   ✅ Indexing: IndexNow → {engine} accepted")
                    success = True
                else:
                    engine = endpoint.split('/')[2]
                    print(f"   ⚠️ Indexing: IndexNow → {engine} returned {resp.status_code}")
            except Exception as e:
                engine = endpoint.split('/')[2]
                print(f"   ⚠️ Indexing: IndexNow → {engine} failed: {e}")

        return success
    except Exception as e:
        print(f"   ⚠️ Indexing: IndexNow failed: {e}")
        return False


def ping_search_engine_sitemaps():
    """Ping Google and Bing with updated sitemap URL.
    Simple HTTP GET that tells search engines the sitemap has been updated."""
    sitemap_url = f"{BLOG_BASE_URL}/p/map.xml"
    pings = [
        f"https://www.google.com/ping?sitemap={sitemap_url}",
        f"https://www.bing.com/ping?sitemap={sitemap_url}",
    ]

    for ping_url in pings:
        try:
            resp = requests.get(ping_url, timeout=10)
            engine = ping_url.split('/')[2].replace('www.', '')
            if resp.status_code == 200:
                print(f"   ✅ Indexing: Sitemap ping → {engine} OK")
            else:
                print(f"   ⚠️ Indexing: Sitemap ping → {engine} returned {resp.status_code}")
        except Exception as e:
            engine = ping_url.split('/')[2].replace('www.', '')
            print(f"   ⚠️ Indexing: Sitemap ping → {engine} failed: {e}")


def ensure_indexnow_key_file(s3_client):
    """Upload the IndexNow verification key file to S3 under p/ prefix (one-time, idempotent)."""
    key_file_key = f"p/{INDEXNOW_API_KEY}.txt"
    try:
        # Check if key file already exists
        s3_client.head_object(Bucket=BLOG_S3_BUCKET, Key=key_file_key)
    except Exception:
        # Upload key file
        try:
            s3_client.put_object(
                Bucket=BLOG_S3_BUCKET,
                Key=key_file_key,
                Body=INDEXNOW_API_KEY.encode('utf-8'),
                ContentType='text/plain',
                CacheControl='public, max-age=2592000'
            )
            print(f"   📤 Indexing: Uploaded IndexNow key file ({key_file_key})")
        except Exception as e:
            print(f"   ⚠️ Indexing: Could not upload IndexNow key file: {e}")


def ensure_robots_txt(s3_client):
    """Upload/update robots.txt at domain root for SEO (idempotent).
    Requires s3:PutObject on bucket root. If IAM only allows p/* prefix,
    upload robots.txt manually via AWS Console once."""
    robots_content = f"""User-agent: *
Allow: /
Disallow: /track.html
Disallow: /p/post-template.html
Disallow: /policy.html
Disallow: /shiping-cal.html
Disallow: /term.html
Disallow: /zxcvf.html
Disallow: /bill.html
Disallow: /map3.html
Disallow: /disclaimer.html

Sitemap: {BLOG_BASE_URL}/p/map.xml

# LLM-friendly content index hosted at /llms.txt per llmstxt.org spec.
# AI crawlers (GPTBot, ClaudeBot, PerplexityBot etc.) auto-discover it at
# the conventional root path — NO robots.txt directive needed. Adding one
# (e.g. "LLMs.txt:" or "Llms-txt:") is not in the robots.txt grammar and
# Google flags it as a syntax error in Search Console.

# AI Crawlers welcome
User-agent: GPTBot
Allow: /

User-agent: ChatGPT-User
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: Bingbot
Allow: /

User-agent: Applebot-Extended
Allow: /

User-agent: anthropic-ai
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: Bytespider
Allow: /
"""
    try:
        # Check if robots.txt already exists at root
        s3_client.head_object(Bucket=BLOG_S3_BUCKET, Key='robots.txt')
        # Already exists — skip (manual upload is fine)
    except Exception:
        try:
            s3_client.put_object(
                Bucket=BLOG_S3_BUCKET,
                Key='robots.txt',
                Body=robots_content.encode('utf-8'),
                ContentType='text/plain; charset=utf-8',
                CacheControl='public, max-age=86400'
            )
            print(f"   📤 Indexing: robots.txt uploaded at domain root")
        except Exception as e:
            print(f"   ⚠️ Indexing: robots.txt upload skipped (upload manually to S3 root via AWS Console)")


def submit_to_search_engines(blog_url, s3_client=None):
    """Master function: submit a new blog URL to all search engines and AI crawlers."""
    print(f"   🔍 Indexing: Submitting {blog_url} to search engines & AI...")

    # Ensure IndexNow key file exists in S3 (idempotent, skips if already there)
    if s3_client:
        ensure_indexnow_key_file(s3_client)
        # robots.txt is a one-time setup — upload manually to S3 root via AWS Console
        # No need to re-upload on every blog post

    # 1. Google Indexing API (instant indexing)
    ping_google_indexing_api(blog_url)

    # 2. IndexNow (Bing, Yandex, AI search engines)
    ping_indexnow(blog_url)

    # 3. Sitemap ping — removed (Google deprecated 2023, Bing returns 410)
    # Google Indexing API + IndexNow already cover all major engines

    print(f"   🔍 Indexing: All submissions complete for {blog_url}")


# ═══════════════════════════════════════════════════════════════════════
# VIDEO CLIP VALIDATION
# ═══════════════════════════════════════════════════════════════════════

def validate_video_file(path, min_size_bytes=10_000):
    """Check if a downloaded video file is valid and playable.

    Uses ffprobe to verify the file has a video stream with non-zero dimensions.
    Returns (is_valid, reason_string).
    """
    import subprocess as _sp

    if not os.path.exists(path):
        return False, "file does not exist"

    file_size = os.path.getsize(path)
    if file_size < min_size_bytes:
        return False, f"file too small ({file_size} bytes)"

    try:
        probe = _sp.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30
        )
        if probe.returncode != 0:
            return False, f"ffprobe failed: {probe.stderr[:120]}"

        output = probe.stdout.strip()
        if not output:
            return False, "ffprobe returned no video stream info"

        data = json.loads(output)
        streams = data.get("streams", [])
        if not streams:
            return False, "no video stream found"

        w = streams[0].get("width", 0)
        h = streams[0].get("height", 0)
        if w == 0 or h == 0:
            return False, f"invalid dimensions: {w}x{h}"

        return True, "ok"

    except _sp.TimeoutExpired:
        return False, "ffprobe timed out"
    except Exception as e:
        return False, f"validation error: {str(e)[:80]}"


# ═══════════════════════════════════════════════════════════════════════
# KLING FALLBACK (fal.ai)
# ═══════════════════════════════════════════════════════════════════════

def generate_clip_kling(prompt_text, output_path):
    """Generate a single video clip via Kling (fal.ai) as Veo fallback.

    Returns (output_path, success_bool).
    Requires FAL_KEY env var to be set.
    """
    import fal_client

    for attempt in range(1, KLING_MAX_RETRIES + 1):
        try:
            print(f"      🎬 Kling attempt {attempt}...", end=" ")
            result = fal_client.subscribe(
                KLING_MODEL,
                arguments={
                    "prompt": prompt_text,
                    "duration": KLING_DURATION,
                    "aspect_ratio": KLING_ASPECT_RATIO,
                    "negative_prompt": KLING_NEGATIVE_PROMPT,
                    "cfg_scale": 0.5,
                    "generate_audio": False,
                },
            )
            video_url = result["video"]["url"]
            resp = requests.get(video_url, timeout=120)
            resp.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(resp.content)
            valid, reason = validate_video_file(output_path)
            if not valid:
                print(f"corrupted ({reason}) — retrying")
                if attempt < KLING_MAX_RETRIES:
                    time.sleep(5 * attempt)
                continue
            print("✅")
            return output_path, True
        except Exception as e:
            print(f"error: {str(e)[:80]}")
            if attempt < KLING_MAX_RETRIES:
                time.sleep(5 * attempt)

    return output_path, False


# ═══════════════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("🚀 SALE91.COM — Daily YouTube Short Generator")
    if TEST_MODE:
        print("   🧪 TEST MODE — no Veo clips, no YouTube upload (free run)")
    elif SINGLE_VEO_TEST:
        print("   🧪 SINGLE VEO TEST — 1 real clip + 4 blank, full upload pipeline")
    elif SKIP_CLIPS:
        print("   🧪 SKIP CLIPS — placeholder clips, but will upload to YouTube")
    elif NEW_TEST_MODE:
        print("   🧪 NEW TEST MODE — placeholder clips, full pipeline (upload + Instagram + blog)")
    print(f"   Time: {datetime.now(pytz.timezone(TIMEZONE)).strftime('%d %b %Y, %I:%M %p IST')}")

    # ── Feature Dashboard ──
    elevenlabs_available = bool(os.environ.get('ELEVENLABS_API_KEY'))
    sarvam_available = bool(os.environ.get('SARVAM_API_KEY'))
    ig_available = bool(os.environ.get('INSTAGRAM_ACCESS_TOKEN'))

    print()
    print("   ╔═══════════════════════════════════════════════════╗")
    print("   ║           FEATURE STATUS DASHBOARD                ║")
    print("   ╠═══════════════════════════════════════════════════╣")
    print("   ║ ── MODE ──                                        ║")
    print(f"   ║ Test Mode             : {'ON (free run)' if TEST_MODE else 'OFF (production)':>25} ║")
    print(f"   ║ Skip Clips            : {'ON (placeholders)' if SKIP_CLIPS else 'OFF (Veo clips)':>25} ║")
    print(f"   ║ Single Veo Test       : {'ON (1 real + 4 blank)' if SINGLE_VEO_TEST else 'OFF':>25} ║")
    print(f"   ║ New Test Mode         : {'ON (no Veo, full upload)' if NEW_TEST_MODE else 'OFF':>25} ║")
    print("   ║                                                   ║")
    print("   ║ ── CONTENT INTELLIGENCE ──                        ║")
    print(f"   ║ Source Channel Data    : {'ON' if SOURCE_CHANNEL_ID else 'OFF (no CHANNEL_ID_2)':>25} ║")
    print(f"   ║ Comment Mining         : {'ON' if SOURCE_CHANNEL_API_KEY else 'OFF (no API key)':>25} ║")
    print(f"   ║ Title A/B Optimization : {'ON':>25} ║")
    print(f"   ║ Smart Posting Time     : {'ON':>25} ║")
    print(f"   ║ Script Quality Gate    : {'ON (6 dims, /60)':>25} ║")
    print(f"   ║ Visual Alignment Check : {'ON':>25} ║")
    print(f"   ║ Topic Bank             : {f'{len(TOPIC_BANK)} topics':>25} ║")
    print(f"   ║ Script Max Attempts    : {SCRIPT_MAX_ATTEMPTS:>25} ║")
    print("   ║                                                   ║")
    print("   ║ ── VIDEO GENERATION ──                            ║")
    print(f"   ║ Veo Model              : {VEO_MODEL:>25} ║")
    print(f"   ║ Hero Clip (full Veo)   : {'ON (clip 1)' if VEO_HERO_FULL else 'OFF':>25} ║")
    print(f"   ║ Clips per Video        : {VEO_CLIPS_PER_VIDEO:>25} ║")
    print(f"   ║ Veo Duration/Clip      : {str(VEO_DURATION) + 's':>25} ║")
    print(f"   ║ Veo Retries            : {VEO_MAX_RETRIES:>25} ║")
    print(f"   ║ Veo Poll Timeout       : {str(VEO_POLL_TIMEOUT) + 's':>25} ║")
    print(f"   ║ Veo Retry Wait         : {str(VEO_RETRY_WAIT) + 's':>25} ║")
    print(f"   ║ Clip Loop Guard        : {'ON':>25} ║")
    print(f"   ║ Ken Burns Effect       : {'ON':>25} ║")
    print(f"   ║ Black Intro Trim       : {'ON':>25} ║")
    print(f"   ║ Clip Fade Transition   : {str(CLIP_FADE_DURATION) + 's':>25} ║")
    print(f"   ║ Kling Fallback         : {'ON (fal.ai)' if KLING_ENABLED else 'OFF (no FAL_KEY)':>25} ║")
    if KLING_ENABLED:
        print(f"   ║ Kling Model            : {'v2.6 Pro':>25} ║")
        print(f"   ║ Kling Duration/Clip    : {KLING_DURATION + 's':>25} ║")
    print("   ║                                                   ║")
    print("   ║ ── AUDIO ──                                       ║")
    # Label must match the real call order in generate_voice: ElevenLabs → Sarvam → OpenAI
    if elevenlabs_available:
        _tts_primary = "ElevenLabs Hindi (PVC)"
        _tts_fb = "Sarvam → OpenAI" if sarvam_available else "OpenAI gpt-4o-mini-tts"
    elif sarvam_available:
        _tts_primary = "Sarvam Bulbul v3"
        _tts_fb = "OpenAI gpt-4o-mini-tts"
    else:
        _tts_primary = "OpenAI (no 11Labs/Sarvam)"
        _tts_fb = "(none)"
    print(f"   ║ TTS Primary            : {_tts_primary:>25} ║")
    print(f"   ║ TTS Fallback           : {_tts_fb:>25} ║")
    print(f"   ║ Audio Normalization    : {'ON (-16 LUFS)':>25} ║")
    print(f"   ║ Background Music       : {'ON' if ADD_BG_MUSIC else 'OFF':>25} ║")
    print(f"   ║ Hook SFX (bass drop)   : {'ON' if ADD_HOOK_SFX else 'OFF':>25} ║")
    print("   ║                                                   ║")
    print("   ║ ── OVERLAYS ──                                    ║")
    print(f"   ║ Subtitles             : {'ON' if ADD_SUBTITLES else 'OFF':>25} ║")
    print(f"   ║ Hook Text (scroll-stop): {'ON (' + str(HOOK_DURATION) + 's)' if ADD_HOOK_TEXT else 'OFF':>25} ║")
    print(f"   ║ Watermark             : {'ON' if ADD_WATERMARK else 'OFF':>25} ║")
    print(f"   ║ CTA End Card          : {'ON' if ADD_CTA_OVERLAY else 'OFF':>25} ║")
    print(f"   ║ Thumbnail             : {'ON (yellow highlight)' if GENERATE_THUMBNAIL else 'OFF':>25} ║")
    print("   ║                                                   ║")
    print("   ║ ── PUBLISHING ──                                  ║")
    print(f"   ║ Schedule Publish       : {'ON (A/B slots)' if SCHEDULE_PUBLISH else 'OFF (immediate)':>25} ║")
    print(f"   ║ Upload as Short        : {'ON' if UPLOAD_AS_SHORT else 'OFF':>25} ║")
    print(f"   ║ Auto-Pin Comment       : {'ON' if AUTO_PIN_COMMENT else 'OFF':>25} ║")
    print(f"   ║ Auto-Playlist          : {'ON' if AUTO_PLAYLIST else 'OFF':>25} ║")
    print(f"   ║ Instagram Cross-Post   : {'ON' if CROSS_POST_INSTAGRAM and ig_available else 'OFF (no IG token)' if not ig_available else 'OFF':>25} ║")
    print("   ║                                                   ║")
    print("   ║ ── SAFETY ──                                      ║")
    print(f"   ║ Daily Cost Limit       : {'$' + str(DAILY_COST_LIMIT_USD):>25} ║")
    print(f"   ║ Engagement Feedback    : {'ON (' + str(ENGAGEMENT_CHECK_DELAY_HOURS) + 'h delay)':>25} ║")
    print("   ╚═══════════════════════════════════════════════════╝")
    print()

    # ── 1. API Keys ──
    elevenlabs_key = os.environ.get('ELEVENLABS_API_KEY')
    openai_key = os.environ.get('OPENAI_API_KEY')
    anthropic_key = os.environ.get('ANTHROPIC_API_KEY')
    google_key = os.environ.get('GOOGLE_API_KEY')

    missing = [k for k, v in {
        "OPENAI_API_KEY": openai_key,
        "ANTHROPIC_API_KEY": anthropic_key,
        "GOOGLE_API_KEY": google_key
    }.items() if not v]

    if missing:
        print(f"❌ Missing: {', '.join(missing)}")
        return

    openai_client = OpenAI(api_key=openai_key)
    claude = anthropic.Anthropic(api_key=anthropic_key)

    # Initialize cost tracker
    cost = CostTracker()

    # Circuit breaker: check daily spending limit
    within_limit, today_spend = CostTracker.check_daily_limit()
    if not within_limit:
        print(f"🚫 Daily cost limit exceeded: ${today_spend:.2f} / ${DAILY_COST_LIMIT_USD:.2f}. Skipping today's video.")
        return
    if today_spend > 0:
        print(f"   💰 Today's spend so far: ${today_spend:.2f} / ${DAILY_COST_LIMIT_USD:.2f}")

    from google import genai
    from google.genai import types
    veo_client = genai.Client(api_key=google_key)

    # ── 1b. Fetch source channel insights (read-only, cached 24h) ──
    fetch_source_channel_insights()

    # ── 2. Pick Topic (Smart: trending search + Claude review gate) ──
    topic_history = []
    if os.path.exists(TOPIC_HISTORY_FILE):
        try:
            with open(TOPIC_HISTORY_FILE, "r") as f:
                topic_history = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"   ⚠️ Could not read topic history ({e}), starting fresh")
            topic_history = []

    print("   🎯 Smart topic selection (with quality gate)...")
    fresh_topic = smart_pick_topic(claude, TOPIC_BANK, topic_history)

    topic_history.append(fresh_topic)
    with open(TOPIC_HISTORY_FILE, "w") as f:
        json.dump(topic_history, f, indent=2)
    print(f"   📌 Topic: {fresh_topic}")

    # ── 3. Generate Script (with quality gate) ──
    data = None
    candidate = None  # Track last valid candidate (may be None if all JSON parses fail)
    previous_feedback = ""  # Pass rejection reasons to next attempt
    for attempt in range(1, SCRIPT_MAX_ATTEMPTS + 1):
        print(f"   ✍️ Writing script (attempt {attempt}/{SCRIPT_MAX_ATTEMPTS})...")

        prompt = get_script_prompt(fresh_topic)
        # On retry, tell Claude what was wrong so it can fix it
        if previous_feedback:
            prompt += f"\n\n━━━ IMPORTANT: PREVIOUS ATTEMPT WAS REJECTED ━━━\nReviewer feedback: {previous_feedback}\nFix these issues in your new script. Write a DIFFERENT and BETTER script."

        try:
            resp = claude.messages.create(
                model="claude-opus-4-6", max_tokens=2500,
                messages=[{"role": "user", "content": prompt}]
            )
            cost.track_claude_call("opus", resp.usage.input_tokens, resp.usage.output_tokens)
            raw = resp.content[0].text.strip()
        except Exception as e:
            err_str = str(e).lower()
            is_transient = any(k in err_str for k in ("529", "overloaded", "503", "429", "unavailable", "rate limit", "too many"))
            if is_transient and attempt < SCRIPT_MAX_ATTEMPTS:
                wait = 30 * attempt  # 30s, 60s backoff
                print(f"   ⚠️ Claude API overloaded (attempt {attempt}), retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"   ⚠️ Claude API error (attempt {attempt}): {e}")
            previous_feedback = "API call failed, please try again."
            continue
        if raw.startswith("```"): raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

        # Robust JSON parsing — LLM may return malformed JSON
        try:
            candidate = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"   ⚠️ JSON parse error (attempt {attempt}): {e}")
            # Try to extract JSON object from the response
            json_match = re.search(r'\{[\s\S]*\}', raw)
            if json_match:
                try:
                    candidate = json.loads(json_match.group())
                    print(f"   🔧 Recovered JSON from response")
                except json.JSONDecodeError:
                    print(f"   ❌ Could not recover JSON, retrying...")
                    previous_feedback = "Your previous response was NOT valid JSON. Return ONLY a valid JSON object, no extra text."
                    continue
            else:
                print(f"   ❌ No JSON found in response, retrying...")
                previous_feedback = "Your previous response was NOT valid JSON. Return ONLY a valid JSON object, no extra text."
                continue

        # Validate required keys exist
        if "script_voice" not in candidate or "script_english" not in candidate:
            print(f"   ⚠️ Missing required keys in JSON (attempt {attempt}), retrying...")
            previous_feedback = "Your JSON was missing required keys (script_voice, script_english). Include ALL required fields."
            candidate = None  # Don't keep incomplete candidate as fallback
            continue

        script_voice = candidate["script_voice"]
        script_english = candidate["script_english"]

        print(f"   🗣️ Script: {script_voice[:80]}...")

        # Quality gate: Claude reviews its own script + video prompts alignment
        candidate_prompts = [candidate.get(f"video_prompt_{i}", "") for i in range(1, VEO_CLIPS_PER_VIDEO + 1)]
        approved, score, weakest, feedback = review_script(claude, script_voice, script_english, fresh_topic, candidate_prompts)

        if approved:
            print(f"   ✅ Script APPROVED (score: {score}/60) — {feedback}")
            data = candidate
            break
        else:
            print(f"   ❌ Script REJECTED (score: {score}/60, weak: {weakest})")
            print(f"      Reason: {feedback}")
            previous_feedback = f"Score {score}/60. Weakest: {weakest}. {feedback}"
            if attempt < SCRIPT_MAX_ATTEMPTS:
                print(f"      Regenerating with feedback...")

    # Use last attempt if none were approved (don't waste the topic)
    if data is None:
        if candidate is not None:
            print(f"   ⚠️ No script scored high enough — using best last attempt")
            data = candidate
        else:
            raise RuntimeError(f"All {SCRIPT_MAX_ATTEMPTS} script generation attempts failed (JSON parse errors). Topic: {fresh_topic}")

    script_voice = data["script_voice"]
    # Sanitize script for TTS: collapse 4+ dots to 3 (preserve "..." as prosody hint)
    # and clamp elongated sounds. ElevenLabs reads "..." as a natural trail-off pause,
    # which is exactly the prosody our prompt requests for endings — DON'T strip it.
    script_voice = re.sub(r'\.{4,}', '...', script_voice)  # 4+ dots → 3
    script_voice = re.sub(r'(\w)\1{3,}', lambda m: m.group(0)[:2], script_voice)  # "aaaaaa" → "aa"
    script_voice = re.sub(r',\s*,', ',', script_voice)  # clean double commas

    script_english = data["script_english"]
    titles = optimize_title(claude, data["title"], script_english, fresh_topic)
    # titles is a dict {'yt': ..., 'ig': ..., 'best': ...}
    yt_title = titles["yt"]    # Hindi OK — YouTube algo rewards
    ig_title = titles["ig"]    # Mixed OK — Instagram algo rewards
    blog_title = titles.get("blog") or titles.get("best") or yt_title  # Latin-only — Google + AI search optimized
    yt_description = data["description"]
    yt_tags = data.get("tags", [])

    # Pre-compute the blog URL from the YT title — slug generation is deterministic.
    # Embedding the URL in the YT description gives the new blog a backlink from
    # youtube.com (one of the highest-authority domains on the web). This is the
    # single biggest external indexing signal we can send Google for free, and it
    # also drives organic clicks from Shorts viewers to the blog.
    blog_slug_preview = generate_blog_slug(blog_title)
    blog_url_preview = f"{BLOG_BASE_URL}/p/{blog_slug_preview}.html"
    yt_description = (
        yt_description.rstrip()
        + f"\n\n📖 Full guide (with FAQs + photos): {blog_url_preview}"
    )
    music_mood = data.get("music_mood", "calm")
    hook_text_from_claude = data.get("hook_text", "")
    video_prompts = [data.get(f"video_prompt_{i}","") for i in range(1, VEO_CLIPS_PER_VIDEO + 1)]
    # Filter out empty prompts, keep at least the first 3
    video_prompts = [p for p in video_prompts if p.strip()] or video_prompts[:3]

    # Save Veo prompts to clip history for deduplication in future runs
    try:
        clip_history = []
        if os.path.exists(CLIP_HISTORY_FILE):
            with open(CLIP_HISTORY_FILE, "r") as f:
                clip_history = json.load(f)
        clip_history.append({
            "topic": fresh_topic,
            "prompts": video_prompts,
            "date": datetime.now().isoformat(),
        })
        # Keep last 30 videos worth of prompts
        clip_history = clip_history[-30:]
        with open(CLIP_HISTORY_FILE, "w") as f:
            json.dump(clip_history, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    print(f"   🎵 Mood: {music_mood}")

    # ── 3b. Load Background Music ──
    load_bg_music(music_mood)
    if os.environ.get("REPLICATE_API_TOKEN"):
        cost.track_replicate()

    # ── 4. Generate Voice (ElevenLabs primary → Sarvam → OpenAI fallback) ──
    # ElevenLabs has the best PROSODY for storytelling; Sarvam handles digit/Hindi
    # pronunciation natively. We get the best of both by running normalize_for_tts()
    # FIRST (turns ₹2 lakh → "do laakh rupaye", DTG → "dee tee jee" etc.) then
    # feeding clean text to ElevenLabs's expressive Hindi voice.
    audio_path = f"{WORK_DIR}/voice_{random.randint(100,999)}.mp3"
    voice_ok = False
    sarvam_key = os.environ.get("SARVAM_API_KEY", "").strip()

    tts_input = normalize_for_tts(script_voice)

    # Primary: ElevenLabs Hindi (Emotive — best prosody for storytelling)
    if elevenlabs_key and not voice_ok:
        print("   🎙️ Generating voice (ElevenLabs Hindi — Emotive prosody)...")
        try:
            from elevenlabs import ElevenLabs
            el_client = ElevenLabs(api_key=elevenlabs_key)
            audio_iter = el_client.text_to_speech.convert(
                voice_id=ELEVENLABS_VOICE_ID,
                model_id=ELEVENLABS_MODEL,
                text=tts_input,
                voice_settings=ELEVENLABS_VOICE_SETTINGS,
            )
            with open(audio_path, "wb") as f:
                for chunk in audio_iter:
                    f.write(chunk)
            print("   ✅ Voice: ElevenLabs Hindi (with Hinglish pre-normalization)")
            cost.track_tts("elevenlabs", len(tts_input))
            voice_ok = True
        except Exception as e:
            print(f"   ⚠️ ElevenLabs TTS failed: {e}")
            if SINGLE_VEO_TEST or NEW_TEST_MODE or TEST_MODE:
                print("   🛑 Test mode + ElevenLabs failed → ABORTING before Veo to save cost.")
                return
            print("   🔄 Falling back to Sarvam...")

    # Fallback 1: Sarvam Bulbul v3 (Indian-native Hinglish)
    if sarvam_key and not voice_ok:
        print("   🎙️ Generating voice (Sarvam Bulbul v3 — Hinglish native)...")
        try:
            audio_path = sarvam_tts_to_mp3(tts_input, sarvam_key, audio_path)
            print(f"   ✅ Voice: Sarvam {SARVAM_MODEL} ({SARVAM_SPEAKER})")
            cost.track_tts("sarvam", len(tts_input))
            voice_ok = True
        except Exception as e:
            print(f"   ⚠️ Sarvam TTS failed: {e}")
            if SINGLE_VEO_TEST or NEW_TEST_MODE or TEST_MODE:
                print("   🛑 Test mode + all premium TTS failed → ABORTING before Veo.")
                return
            print("   🔄 Falling back to OpenAI TTS...")

    # Fallback 2: OpenAI gpt-4o-mini-tts
    if not voice_ok:
        print("   🎙️ Generating voice (OpenAI TTS fallback)...")
        try:
            response = openai_client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=TARGET_VOICE,
                input=tts_input,
                instructions=VOICE_INSTRUCTIONS,
                speed=VOICE_SPEED,
                response_format="mp3",
            )
            response.stream_to_file(audio_path)
            print(f"   ✅ Voice: OpenAI {TARGET_VOICE} (fallback)")
            cost.track_tts("openai", len(tts_input))
            voice_ok = True
        except Exception as e:
            print(f"   ❌ OpenAI TTS also failed: {e}")
            return

    # ── 4b. Loudness normalize ONLY (preserve ElevenLabs natural prosody) ──
    # User feedback (2026-05-06): ElevenLabs preview sounds perfect; bot output
    # sounds choppy. Cause: my silenceremove was killing natural inter-word pauses
    # that ElevenLabs deliberately adds for human-feel. ElevenLabs is good enough
    # that we should NOT post-process the speech timing — only adjust loudness.
    # If a specific run is too gappy, set TIGHTEN_SILENCES=1 to enable trimming.
    try:
        import subprocess
        tightened_path = audio_path.replace(".mp3", "_tight.mp3")
        # User feedback: voice felt slightly fast. Default to ~5% slowdown
        # (atempo=0.95). atempo preserves pitch — only timing slows.
        # Override with VOICE_TEMPO env var (e.g. 1.0 = no change, 0.90 = 10% slower).
        tempo = os.environ.get("VOICE_TEMPO", "0.95").strip() or "0.95"
        try:
            tempo_val = float(tempo)
            tempo_val = max(0.5, min(2.0, tempo_val))  # ffmpeg atempo range
        except ValueError:
            tempo_val = 0.95
        tempo_filter = f"atempo={tempo_val:.3f},"
        if os.environ.get("TIGHTEN_SILENCES", "").strip() in ("1", "true", "yes"):
            # Gentle: only kill silences > 1.5s (clearly intro/outro pauses, not prosody)
            af = f"{tempo_filter}silenceremove=stop_periods=-1:stop_duration=1.5:stop_threshold=-32dB:stop_silence=0.6,loudnorm=I=-14:TP=-1.5:LRA=11"
        else:
            af = f"{tempo_filter}loudnorm=I=-14:TP=-1.5:LRA=11"
        subprocess.run([
            "ffmpeg", "-i", audio_path, "-af", af, "-y", tightened_path,
        ], capture_output=True, timeout=60)
        if os.path.exists(tightened_path) and os.path.getsize(tightened_path) > 0:
            os.replace(tightened_path, audio_path)
            print("   🔊 Voice normalized to -14 LUFS (prosody preserved)")
        else:
            print("   ⚠️ Voice post-processing skipped (ffmpeg output empty)")
    except Exception as e:
        print(f"   ⚠️ Voice post-processing skipped: {e}")

    # ── 5. Generate Video Clips (Veo 3.1, Kling fallback) ──
    downloaded_clips = []
    kling_clips = 0  # Track Kling fallback clips across all modes
    veo_clips_count = 0

    if TEST_MODE or SKIP_CLIPS or NEW_TEST_MODE:
        # Test/skip-clips mode: create cheap placeholder clips (solid color) instead of Veo
        label = "TEST MODE" if TEST_MODE else ("NEW TEST MODE" if NEW_TEST_MODE else "SKIP CLIPS")
        print(f"   🧪 {label}: Skipping Veo clips, using placeholder video...")
        for i in range(VEO_CLIPS_PER_VIDEO):
            placeholder_path = f"{WORK_DIR}/test_clip_{i}.mp4"
            colors = [(30, 60, 90), (50, 80, 40), (80, 40, 60), (60, 30, 70), (40, 70, 50)]
            color = colors[i % len(colors)]
            placeholder = ColorClip(size=(VIDEO_WIDTH, VIDEO_HEIGHT), color=color, duration=VEO_DURATION)
            placeholder.write_videofile(placeholder_path, fps=FPS, codec="libx264", logger=None)
            downloaded_clips.append(placeholder_path)
        print(f"   ✅ {len(downloaded_clips)} test clips created (free)")

    elif SINGLE_VEO_TEST:
        # Single Veo test: 1 real Veo clip + 4 blank placeholders
        # Full pipeline runs (upload etc.) — saves ~80% Veo cost
        print(f"   🧪 SINGLE VEO TEST: 1 real clip + {VEO_CLIPS_PER_VIDEO - 1} blank placeholders...")

        # Generate 1 real Veo clip (first prompt)
        prompt_text = video_prompts[0] if video_prompts else "A professional t-shirt manufacturing factory"
        clip_path = f"{WORK_DIR}/veo_clip_0_{random.randint(100,999)}.mp4"
        clip_success = False

        hero_model = VEO_HERO_MODEL if VEO_HERO_FULL else VEO_MODEL
        for attempt in range(1, VEO_MAX_RETRIES + 1):
            try:
                model_label = "FULL" if hero_model != VEO_MODEL else "fast"
                print(f"   ⏳ Hero Clip 1 ({model_label}): attempt {attempt}...", end=" ")
                operation = veo_client.models.generate_videos(
                    model=hero_model,
                    prompt=prompt_text,
                    config=types.GenerateVideosConfig(
                        aspect_ratio=VEO_ASPECT_RATIO,
                        number_of_videos=1,
                        duration_seconds=VEO_DURATION,
                    ),
                )
                poll_start = time.time()
                while not operation.done:
                    if time.time() - poll_start > VEO_POLL_TIMEOUT:
                        raise TimeoutError(f"Veo polling exceeded {VEO_POLL_TIMEOUT}s")
                    time.sleep(10)
                    operation = veo_client.operations.get(operation)

                # Check for operation-level error
                if hasattr(operation, 'error') and operation.error:
                    print(f"generation failed: {str(operation.error)[:120]}")
                    if attempt < VEO_MAX_RETRIES:
                        time.sleep(15)
                    continue

                if operation.response and operation.response.generated_videos:
                    video = operation.response.generated_videos[0]
                    video_data = veo_client.files.download(file=video.video)
                    if not video_data or len(video_data) < 10_000:
                        data_len = len(video_data) if video_data else 0
                        print(f"download too small ({data_len} bytes) — retrying")
                        if attempt < VEO_MAX_RETRIES:
                            time.sleep(15)
                        continue
                    with open(clip_path, "wb") as f:
                        f.write(video_data)
                    print(f"downloaded {len(video_data)} bytes...", end=" ")
                    valid, reason = validate_video_file(clip_path)
                    if not valid:
                        print(f"corrupted ({reason}) — retrying")
                        if attempt < VEO_MAX_RETRIES:
                            time.sleep(15)
                        continue
                    downloaded_clips.append(clip_path)
                    clip_success = True
                    print("✅")
                    break
                else:
                    err_detail = ""
                    if hasattr(operation, 'error') and operation.error:
                        err_detail = f" | error: {str(operation.error)[:80]}"
                    elif hasattr(operation, 'response') and operation.response:
                        err_detail = f" | response has no generated_videos"
                    print(f"empty response{err_detail}")
                    if attempt < VEO_MAX_RETRIES:
                        time.sleep(15)
            except BaseException as e:
                error_msg = str(e)
                if "RESOURCE_EXHAUSTED" in error_msg or "429" in error_msg:
                    wait = VEO_RETRY_WAIT * attempt
                    print(f"rate limited — waiting {wait}s")
                    time.sleep(wait)
                else:
                    print(f"error: {error_msg[:120]}")
                    if attempt < VEO_MAX_RETRIES:
                        time.sleep(15)
                    else:
                        break

        # ── Kling fallback for single Veo test (when Veo rate-limits) ──
        if not clip_success and KLING_ENABLED:
            print(f"   🔄 Kling fallback: generating 1 real clip...")
            print(f"   ⏳ Clip 1 (Kling):")
            _, clip_success = generate_clip_kling(prompt_text, clip_path)
            if clip_success:
                downloaded_clips.append(clip_path)
                cost.track_kling(1)

        if not clip_success:
            print(f"   ⚠️ Real clip failed (Veo + Kling) — falling back to all placeholders")

        # Fill remaining slots with blank (black) placeholders
        for i in range(1, VEO_CLIPS_PER_VIDEO):
            placeholder_path = f"{WORK_DIR}/test_clip_{i}.mp4"
            placeholder = ColorClip(size=(VIDEO_WIDTH, VIDEO_HEIGHT), color=(0, 0, 0), duration=VEO_DURATION)
            placeholder.write_videofile(placeholder_path, fps=FPS, codec="libx264", logger=None)
            downloaded_clips.append(placeholder_path)

        real_count = 1 if clip_success else 0
        cost.track_veo(real_count, hero_full=VEO_HERO_FULL)
        print(f"   ✅ {len(downloaded_clips)} clips ready ({real_count} real Veo + {VEO_CLIPS_PER_VIDEO - real_count} blank)")

    else:
        print(f"   🤖 Generating {VEO_CLIPS_PER_VIDEO} AI clips via Veo 3.1...")
        if KLING_ENABLED:
            print(f"   🔄 Kling fallback: READY (auto-switch on Veo rate limit)")

        use_kling_fallback = False  # Sticky: once Veo rate-limits, switch to Kling
        consecutive_failures = 0  # Early termination: stop if Veo is persistently broken

        for i in range(VEO_CLIPS_PER_VIDEO):
            prompt_text = video_prompts[i] if i < len(video_prompts) else video_prompts[0]
            clip_path = f"{WORK_DIR}/veo_clip_{i}_{random.randint(100,999)}.mp4"
            clip_success = False

            # ── Early termination: if 2+ consecutive clips failed, Veo is likely broken ──
            if consecutive_failures >= 2 and not KLING_ENABLED:
                remaining = VEO_CLIPS_PER_VIDEO - i
                print(f"   ⛔ Veo persistently failing ({consecutive_failures} clips in a row) — skipping {remaining} remaining clip(s)")
                break

            # ── Try Veo first (unless already switched to Kling) ──
            if not use_kling_fallback:
                if i > 0:
                    print(f"   ⏸️ RPM cooldown — waiting 45s...")
                    time.sleep(45)

                for attempt in range(1, VEO_MAX_RETRIES + 1):
                    try:
                        print(f"   ⏳ Clip {i+1}: attempt {attempt}...", end=" ")
                        operation = veo_client.models.generate_videos(
                            model=VEO_MODEL,
                            prompt=prompt_text,
                            config=types.GenerateVideosConfig(
                                aspect_ratio=VEO_ASPECT_RATIO,
                                number_of_videos=1,
                                duration_seconds=VEO_DURATION,
                            ),
                        )
                        poll_start = time.time()
                        while not operation.done:
                            if time.time() - poll_start > VEO_POLL_TIMEOUT:
                                raise TimeoutError(f"Veo polling exceeded {VEO_POLL_TIMEOUT}s")
                            time.sleep(10)
                            operation = veo_client.operations.get(operation)

                        # Check for operation-level error (content filtering, server error, etc.)
                        if hasattr(operation, 'error') and operation.error:
                            print(f"generation failed: {str(operation.error)[:120]}")
                            if attempt < VEO_MAX_RETRIES:
                                time.sleep(15)
                            continue

                        if operation.response and operation.response.generated_videos:
                            video = operation.response.generated_videos[0]
                            video_data = veo_client.files.download(file=video.video)
                            # Validate downloaded bytes before writing
                            if not video_data or len(video_data) < 10_000:
                                data_len = len(video_data) if video_data else 0
                                print(f"download too small ({data_len} bytes) — retrying")
                                if attempt < VEO_MAX_RETRIES:
                                    time.sleep(15)
                                continue
                            with open(clip_path, "wb") as f:
                                f.write(video_data)
                            print(f"downloaded {len(video_data)} bytes...", end=" ")
                            valid, reason = validate_video_file(clip_path)
                            if not valid:
                                print(f"corrupted ({reason}) — retrying")
                                if attempt < VEO_MAX_RETRIES:
                                    time.sleep(15)
                                continue
                            downloaded_clips.append(clip_path)
                            clip_success = True
                            veo_clips_count += 1
                            print("✅")
                            break
                        else:
                            # Log details to help debug empty responses
                            err_detail = ""
                            if hasattr(operation, 'error') and operation.error:
                                err_detail = f" | error: {str(operation.error)[:80]}"
                            elif hasattr(operation, 'response') and operation.response:
                                err_detail = f" | response has no generated_videos"
                            print(f"empty response{err_detail}")
                            if attempt < VEO_MAX_RETRIES:
                                time.sleep(15)
                    except BaseException as e:
                        error_msg = str(e)
                        if "RESOURCE_EXHAUSTED" in error_msg or "429" in error_msg:
                            if KLING_ENABLED:
                                print(f"rate limited — switching to Kling fallback")
                                use_kling_fallback = True
                                break
                            else:
                                wait = VEO_RETRY_WAIT * attempt
                                print(f"rate limited — waiting {wait}s")
                                time.sleep(wait)
                        else:
                            print(f"error: {error_msg[:120]}")
                            if attempt < VEO_MAX_RETRIES:
                                time.sleep(15)
                            else:
                                break

            # ── Kling fallback (rate limit or Veo failure) ──
            if not clip_success and KLING_ENABLED:
                if kling_clips == 0:
                    remaining = VEO_CLIPS_PER_VIDEO - i
                    print(f"   🔄 Kling fallback: generating {remaining} remaining clip(s)...")
                print(f"   ⏳ Clip {i+1} (Kling):")
                _, clip_success = generate_clip_kling(prompt_text, clip_path)
                if clip_success:
                    downloaded_clips.append(clip_path)
                    kling_clips += 1

            if clip_success:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                print(f"   ⚠️ Clip {i+1} failed after all attempts")

        # Summary
        if kling_clips > 0:
            print(f"   ✅ {len(downloaded_clips)} clips ready ({veo_clips_count} Veo + {kling_clips} Kling fallback)")
        else:
            print(f"   ✅ {len(downloaded_clips)} clips ready ({veo_clips_count} Veo)")

    if not downloaded_clips:
        print("❌ No clips generated. Stopping.")
        sys.exit(2)

    if not TEST_MODE and not SKIP_CLIPS and not SINGLE_VEO_TEST and not NEW_TEST_MODE:
        expected = VEO_CLIPS_PER_VIDEO
        got = len(downloaded_clips)
        veo_count = got - kling_clips
        if veo_count > 0:
            cost.track_veo(veo_count, hero_full=VEO_HERO_FULL)
        if kling_clips > 0:
            cost.track_kling(kling_clips)
        if got < expected:
            print(f"   ⚠️ Partial recovery: {got}/{expected} clips succeeded — video will use clip looping to fill duration")
    print(f"   ✅ {len(downloaded_clips)} clips ready")

    # ── 6. Subtitles (Whisper-synced English captions) ──
    # Use script_english (simple paraphrased English) so subtitles are accessible
    # to non-Hindi viewers + deaf/HoH. Claude generates script_english with the
    # SAME NUMBER OF SENTENCES as script_voice, so sentence-level alignment is clean:
    #   - Whisper segments the Hindi audio into N sentences (start/end timestamps)
    #   - Each English sentence inherits its Hindi counterpart's timing
    #   - Long English sentences split into smaller subtitle pieces by MAX_SUBTITLE_DURATION
    subtitle_segments = []
    karaoke_words = []   # [{"text","start","end"}] — Roman-Hinglish words w/ Whisper timing
    sub_source = script_english if script_english else script_voice
    audio_clip_dur = AudioFileClip(audio_path).duration

    # Split English into sentences (use simple punctuation split — Claude follows
    # period/question-mark conventions). Filter empties.
    import re as _re_subs
    eng_sentences = [s.strip() for s in _re_subs.split(r'(?<=[.!?])\s+', sub_source.strip()) if s.strip()]
    if not eng_sentences:
        eng_sentences = [sub_source.strip()]

    # Safe fallback before Whisper attempt: equal-time slicing.
    # Whisper sync below will overwrite with synced timing if successful.
    _fallback_seg_dur = audio_clip_dur / max(len(eng_sentences), 1)
    subtitle_segments = [
        {"text": eng, "start": idx * _fallback_seg_dur, "end": (idx + 1) * _fallback_seg_dur}
        for idx, eng in enumerate(eng_sentences)
    ]

    try:
        import whisper
        wmodel = whisper.load_model("small")
        result = wmodel.transcribe(audio_path, language="hi", word_timestamps=True)
        # Whisper returns segments — typically sentence-ish. Use those for timing.
        # Also keep WORD-level timestamps (timing only — Whisper text is Devanagari, no CI font).
        whisper_segs = [
            {"start": float(s["start"]), "end": float(s["end"]),
             "words": [
                 {"start": float(w["start"]), "end": float(w["end"])}
                 for w in (s.get("words") or [])
                 if w.get("end", 0) > w.get("start", 0)
             ]}
            for s in result.get("segments", [])
            if s.get("end", 0) > s.get("start", 0)
        ]
        cost.track_whisper(audio_clip_dur)

        if whisper_segs and eng_sentences:
            # Whisper sync REPLACES the fallback equal-time slicing, doesn't add to it.
            # (Earlier bug: appending instead of clearing caused 2× subtitles in the
            # rendered video — both fallback and synced versions overlaid.)
            subtitle_segments = []
            n_eng = len(eng_sentences)
            n_wh = len(whisper_segs)
            if n_eng == n_wh:
                # Clean 1:1 mapping
                for k, eng in enumerate(eng_sentences):
                    subtitle_segments.append({
                        "text": eng,
                        "start": whisper_segs[k]["start"],
                        "end": whisper_segs[k]["end"],
                    })
            else:
                # Proportional: bucket Whisper segments to match English count
                ratio = n_wh / n_eng
                for k, eng in enumerate(eng_sentences):
                    si = int(k * ratio)
                    ei = min(int((k + 1) * ratio) - 1, n_wh - 1)
                    if ei < si: ei = si
                    si = min(si, n_wh - 1)
                    subtitle_segments.append({
                        "text": eng,
                        "start": whisper_segs[si]["start"],
                        "end": whisper_segs[ei]["end"],
                    })
            print(f"   ✅ Whisper synced! ({n_eng} English sentences ↔ {n_wh} Hindi audio segments)")
        else:
            # Fallback: equal-time slicing
            seg_dur = audio_clip_dur / max(len(eng_sentences), 1)
            for idx, eng in enumerate(eng_sentences):
                subtitle_segments.append({
                    "text": eng, "start": idx * seg_dur, "end": (idx + 1) * seg_dur
                })

        # ── Karaoke word timings ──
        # Map script_voice words (Roman Hinglish = the ACTUAL spoken words) onto
        # Whisper's word timestamps with a single GLOBAL proportional mapping.
        # (The old per-sentence bucketing collapsed when script sentence count
        # differed from Whisper segment count — ellipses inflate script sentences —
        # cramming words into early buckets: user saw captions race then vanish.)
        # Global j→int(j*m/n) is monotonic by construction, so drift is gradual
        # and coverage always spans the full audio.
        try:
            # Devanagari words in the script render as tofu (CI fonts are
            # Latin-only). Show their Roman spelling via reverse map instead
            # of dropping them — dropped words shrank the caption word list
            # and desynced captions from the voice (Ketu report 2026-07-11).
            _deva_to_roman = {}
            for _k, _v in (list(_TTS_HINGLISH_DEVANAGARI.items())
                           + list(_get_learned_pronunciations().items())):
                if _re_subs.fullmatch(r"[a-z]+", str(_k)) and _v not in _deva_to_roman:
                    _deva_to_roman[_v] = _k

            def _romanize_token(t):
                return _re_subs.sub(r"[ऀ-ॿ]+",
                                    lambda m: _deva_to_roman.get(m.group(0), ""), t)

            script_words_all = [
                w for w in (_romanize_token(t) for t in script_voice.split())
                if w.strip()
            ]
            wtimes_all = [w for seg in whisper_segs for w in seg.get("words", [])]

            if script_words_all:
                n = len(script_words_all)
                if wtimes_all:
                    m = len(wtimes_all)
                    for j, sw in enumerate(script_words_all):
                        i0 = min(int(j * m / n), m - 1)
                        i1 = min(int((j + 1) * m / n), m - 1)
                        st = wtimes_all[i0]["start"]
                        en = wtimes_all[i1]["start"] if i1 > i0 else wtimes_all[i0]["end"]
                        karaoke_words.append({"text": sw, "start": st, "end": max(en, st + 0.08)})
                else:
                    # No word timestamps at all — spread evenly across speech span
                    span_start = whisper_segs[0]["start"] if whisper_segs else 0.2
                    span_end = whisper_segs[-1]["end"] if whisper_segs else max(audio_clip_dur - 0.3, 1.0)
                    per = max(0.12, (span_end - span_start) / n)
                    for j, sw in enumerate(script_words_all):
                        karaoke_words.append({"text": sw, "start": span_start + j * per,
                                              "end": span_start + (j + 1) * per})

                # Monotonic clamp (safety net — global mapping is already ordered)
                for j in range(1, len(karaoke_words)):
                    if karaoke_words[j]["start"] < karaoke_words[j - 1]["start"] + 0.02:
                        karaoke_words[j]["start"] = karaoke_words[j - 1]["start"] + 0.02
                    if karaoke_words[j]["end"] < karaoke_words[j]["start"] + 0.06:
                        karaoke_words[j]["end"] = karaoke_words[j]["start"] + 0.06

                # Coverage sanity: if the mapping ends long before the audio does
                # (or starts absurdly late), the timing source is degenerate —
                # rebuild with an even spread rather than ship racing captions.
                if karaoke_words:
                    cov_end = karaoke_words[-1]["end"]
                    cov_start = karaoke_words[0]["start"]
                    if cov_end < 0.55 * audio_clip_dur or cov_start > 6.0:
                        print(f"   ⚠️ Karaoke coverage degenerate (span {cov_start:.1f}-{cov_end:.1f}s of {audio_clip_dur:.1f}s) — using even spread")
                        karaoke_words = []
                        span_start, span_end = 0.2, max(audio_clip_dur - 0.3, 1.0)
                        per = max(0.12, (span_end - span_start) / n)
                        for j, sw in enumerate(script_words_all):
                            karaoke_words.append({"text": sw, "start": span_start + j * per,
                                                  "end": span_start + (j + 1) * per})
                    print(f"   🎤 Karaoke timing: {len(karaoke_words)} words, span {karaoke_words[0]['start']:.1f}s → {karaoke_words[-1]['end']:.1f}s (audio {audio_clip_dur:.1f}s)")
        except Exception as _ke:
            karaoke_words = []
            print(f"   ⚠️ Karaoke timing failed: {_ke}")

        # Enforce MAX_SUBTITLE_DURATION — if a caption sits >1.8s, split it in half
        try:
            split_segs = []
            for seg in subtitle_segments:
                dur = max(0.0, seg["end"] - seg["start"])
                if dur <= MAX_SUBTITLE_DURATION:
                    split_segs.append(seg)
                    continue
                words_in = seg["text"].split()
                # Split into halves until each piece fits under MAX_SUBTITLE_DURATION
                pieces = max(2, int(dur / MAX_SUBTITLE_DURATION) + 1)
                per = max(1, len(words_in) // pieces)
                slices = [words_in[i:i + per] for i in range(0, len(words_in), per)]
                # Merge tiny tail into previous slice
                if len(slices) > pieces and len(slices[-1]) <= 1 and slices[:-1]:
                    slices[-2].extend(slices[-1])
                    slices = slices[:-1]
                slice_dur = dur / max(len(slices), 1)
                for idx, sl in enumerate(slices):
                    split_segs.append({
                        "text": " ".join(sl),
                        "start": seg["start"] + idx * slice_dur,
                        "end":   seg["start"] + (idx + 1) * slice_dur,
                    })
            if len(split_segs) != len(subtitle_segments):
                print(f"   ✂️ Caption pacing: {len(subtitle_segments)} → {len(split_segs)} (≤{MAX_SUBTITLE_DURATION}s each)")
            subtitle_segments = split_segs
        except Exception as _e:
            print(f"   ⚠️ Caption split skipped: {_e}")
    except Exception:
        pass

    # ── 7. Video Assembly ──
    print("   ✂️ Building video...")
    audio_clip = AudioFileClip(audio_path)
    total_duration = audio_clip.duration + 0.8  # Extra buffer so ending doesn't feel cut

    def smart_crop(clip, tw=1080, th=1920):
        w, h = clip.size
        if (w / h) > (tw / th):
            clip = clip.resize(height=th)
            w2, _ = clip.size
            x1 = max(0, int(w2 / 2 - tw / 2))
            return clip.crop(x1=x1, y1=0, width=tw, height=th)
        else:
            clip = clip.resize(width=tw)
            _, h2 = clip.size
            y1 = max(0, int(h2 / 2 - th / 2))
            return clip.crop(x1=0, y1=y1, width=tw, height=th)

    def apply_ken_burns(clip, zoom_percent=5):
        """Apply slow Ken Burns zoom-in effect. Makes static clips feel alive."""
        try:
            w, h = clip.size
            # Over-size the clip slightly so we can zoom in without black borders
            scale_start = 1.0
            scale_end = 1.0 + (zoom_percent / 100.0)
            dur = clip.duration

            def zoom_frame(get_frame, t):
                progress = t / max(dur, 0.1)
                scale = scale_start + (scale_end - scale_start) * progress
                frame = get_frame(t)
                from PIL import Image
                import numpy as np
                img = Image.fromarray(frame)
                new_w, new_h = int(w * scale), int(h * scale)
                img = img.resize((new_w, new_h), Image.LANCZOS)
                # Center crop back to original size
                left = (new_w - w) // 2
                top = (new_h - h) // 2
                img = img.crop((left, top, left + w, top + h))
                return np.array(img)

            return clip.fl(zoom_frame, keep_duration=True)
        except Exception:
            return clip  # Fallback: return original if zoom fails

    def trim_black_intro(clip, threshold=15, max_trim=3.0):
        """Detect and skip black frames at the start of a clip.
        threshold: average pixel brightness below which a frame is 'black'.
        max_trim: maximum seconds to trim from the start."""
        try:
            import numpy as np
            step = 0.25  # Check every 0.25 seconds
            trim_to = 0.0
            for t in [i * step for i in range(int(max_trim / step) + 1)]:
                if t >= clip.duration:
                    break
                frame = clip.get_frame(t)
                avg_brightness = np.mean(frame)
                if avg_brightness > threshold:
                    trim_to = t
                    break
            if trim_to > 0:
                print(f"      Trimmed {trim_to:.1f}s black intro")
                return clip.subclip(trim_to)
            return clip
        except Exception:
            return clip

    video_objects = []
    placeholder_colors = [(30,60,90), (50,80,40), (80,40,60), (60,30,70), (40,70,50)]
    for clip_idx, fname in enumerate(downloaded_clips):
        try:
            fixed_fname = fname.replace(".mp4", "_fixed.mp4")
            import subprocess
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", fname, "-c:v", "libx264", "-preset", "fast",
                 "-crf", "18", "-an", "-movflags", "+faststart", fixed_fname],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                print(f"   ffmpeg error: {result.stderr[:200]}")
                fixed_fname = fname
            v = VideoFileClip(fixed_fname)
            v = smart_crop(v, VIDEO_WIDTH, VIDEO_HEIGHT)
            v = v.without_audio()
            # Trim black frames from Veo clip intros
            v = trim_black_intro(v)
            # Apply Ken Burns slow zoom effect (makes clips feel cinematic)
            v = apply_ken_burns(v, zoom_percent=3)
            video_objects.append(v)
            print(f"   Loaded clip: {fname} ({v.duration:.1f}s)")
        except Exception as e:
            print(f"   ⚠️ Corrupted clip {fname}: {e} — substituting placeholder")
            color = placeholder_colors[clip_idx % len(placeholder_colors)]
            placeholder = ColorClip(size=(VIDEO_WIDTH, VIDEO_HEIGHT), color=color, duration=VEO_DURATION)
            video_objects.append(placeholder)

    if not video_objects:
        print("❌ No usable clips")
        return

    total_clip_duration = sum(v.duration for v in video_objects)
    if total_clip_duration >= total_duration:
        cd = total_duration / len(video_objects)
        trimmed = [v.subclip(0, min(cd, v.duration)) for v in video_objects]
    else:
        # Loop clips cyclically to fill duration (instead of slow-mo)
        looped = []
        accumulated = 0
        idx = 0
        max_iterations = len(video_objects) * 20  # Safety: prevent infinite loop
        while accumulated < total_duration and idx < max_iterations:
            clip = video_objects[idx % len(video_objects)]
            if clip.duration <= 0:
                print(f"   ⚠️ Clip {idx} has zero duration — skipping")
                idx += 1
                continue
            remaining = total_duration - accumulated
            if clip.duration <= remaining:
                looped.append(clip)
                accumulated += clip.duration
            else:
                looped.append(clip.subclip(0, remaining))
                accumulated += remaining
            idx += 1
        trimmed = looped
        print(f"   🔁 Clips looped ({len(video_objects)} clips → {len(looped)} segments to fill {total_duration:.1f}s)")

    if CLIP_FADE_DURATION > 0 and len(trimmed) > 1:
        # Use padding_and_crossfade for clean transitions (no black gaps)
        try:
            base_video = concatenate_videoclips(trimmed, method="compose", padding=-CLIP_FADE_DURATION)
        except Exception:
            base_video = concatenate_videoclips(trimmed, method="chain")
    else:
        base_video = concatenate_videoclips(trimmed, method="chain")
    if base_video.duration > total_duration:
        base_video = base_video.subclip(0, total_duration)

    print(f"   🎬 Ken Burns zoom applied to all clips")

    # ── 8. Overlays ──
    layers = [base_video]

    # Subtitles — karaoke word-by-word highlight (actual spoken Roman-Hinglish words)
    def _karaoke_font(size):
        from PIL import ImageFont
        for p in KARAOKE_FONT_CANDIDATES:
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
        return ImageFont.load_default()

    def _karaoke_line_img(word_texts, hi_idx):
        """One caption line as RGBA array; word hi_idx yellow, rest white, black stroke."""
        from PIL import Image as _KImg, ImageDraw as _KDraw
        import numpy as _knp
        fsize = KARAOKE_FONTSIZE
        while True:
            font = _karaoke_font(fsize)
            gap = int(fsize * 0.35)
            dd = _KDraw.Draw(_KImg.new("RGBA", (8, 8)))
            boxes = [dd.textbbox((0, 0), wt, font=font, stroke_width=KARAOKE_STROKE_W) for wt in word_texts]
            widths = [b[2] - b[0] for b in boxes]
            total_w = sum(widths) + gap * (len(word_texts) - 1)
            if total_w <= KARAOKE_MAX_LINE_W or fsize <= 40:
                break
            fsize -= 6
        # Draw all words from the SAME origin y — PIL's default anchor is the
        # ascender line, so a shared origin = shared baseline. Shifting each
        # word by its own bbox top (-b[1]) misaligns short lowercase words.
        line_h = max(b[3] for b in boxes)
        pad = KARAOKE_STROKE_W + 6
        img = _KImg.new("RGBA", (total_w + pad * 2, line_h + pad * 2), (0, 0, 0, 0))
        d = _KDraw.Draw(img)
        x = pad
        for i, (wt, b, w_px) in enumerate(zip(word_texts, boxes, widths)):
            color = KARAOKE_HIGHLIGHT_COLOR if i == hi_idx else KARAOKE_BASE_COLOR
            d.text((x - b[0], pad), wt, font=font, fill=color,
                   stroke_width=KARAOKE_STROKE_W, stroke_fill=KARAOKE_STROKE_COLOR)
            x += w_px + gap
        return _knp.array(img)

    karaoke_rendered = False
    if ADD_SUBTITLES and KARAOKE_CAPTIONS and karaoke_words:
        try:
            k_lines, cur_line = [], []
            for kw in karaoke_words:
                cur_line.append(kw)
                if len(cur_line) >= WORDS_PER_SUBTITLE or kw["text"].rstrip().endswith((".", "!", "?")):
                    k_lines.append(cur_line); cur_line = []
            if cur_line:
                k_lines.append(cur_line)
            n_word_clips = 0
            # Build clips locally — layers only gets them if ALL succeed, else a
            # partial karaoke set would composite together with the fallback subs
            k_clips = []
            for li, line in enumerate(k_lines):
                line_start = line[0]["start"]
                line_end = line[-1]["end"] + 0.15
                if li + 1 < len(k_lines):
                    line_end = min(line_end, k_lines[li + 1][0]["start"])
                texts = [(w["text"].strip().strip('.,!?…"') or w["text"].strip()) for w in line]
                for wi, w in enumerate(line):
                    w_start = max(line_start, w["start"])
                    w_end = line[wi + 1]["start"] if wi + 1 < len(line) else line_end
                    w_end = max(w_end, w_start + 0.04)
                    arr = _karaoke_line_img(texts, wi)
                    ih, iw = arr.shape[0], arr.shape[1]
                    ic = (ImageClip(arr)
                          .set_position(((VIDEO_WIDTH - iw) // 2,
                                         int(VIDEO_HEIGHT * KARAOKE_Y_PERCENT) - ih // 2))
                          .set_start(w_start)
                          .set_end(min(w_end, total_duration)))
                    k_clips.append(ic)
                    n_word_clips += 1
            layers.extend(k_clips)
            karaoke_rendered = True
            print(f"   ✅ Karaoke captions: {len(k_lines)} lines / {n_word_clips} word-states")
        except Exception as e:
            print(f"   ⚠️ Karaoke captions failed: {e} — falling back to segment subtitles")

    # Fallback — old segment-level English captions (unchanged)
    if ADD_SUBTITLES and subtitle_segments and not karaoke_rendered:
        for seg in subtitle_segments:
            dur = seg["end"] - seg["start"]
            if dur < 0.1: continue
            try:
                seg_text = seg["text"]
                seg_words = seg_text.split()
                has_keyword = any(w.lower().strip(".,!?") in SUBTITLE_HIGHLIGHT_WORDS for w in seg_words)

                if has_keyword:
                    # Keyword detected — render entire subtitle in yellow (single layer, no overlap)
                    txt = TextClip(seg_text, fontsize=SUBTITLE_FONTSIZE, font=SUBTITLE_FONT,
                        color=SUBTITLE_HIGHLIGHT_COLOR, stroke_color=SUBTITLE_STROKE, stroke_width=SUBTITLE_STROKE_W,
                        method='caption', size=(VIDEO_WIDTH - 160, None), align='center')
                    txt_w, txt_h = txt.size
                    bg_w = min(txt_w + SUBTITLE_BG_PADDING * 2, VIDEO_WIDTH - 40)
                    bg_h = txt_h + SUBTITLE_BG_PADDING * 2
                    bg = ColorClip(size=(bg_w, bg_h), color=SUBTITLE_BG_COLOR).set_opacity(SUBTITLE_BG_OPACITY)
                    sub_y = int(VIDEO_HEIGHT * 0.50)  # CENTER SCREEN
                    bg = bg.set_position(((VIDEO_WIDTH - bg_w) // 2, sub_y - SUBTITLE_BG_PADDING)).set_start(seg["start"]).set_duration(dur)
                    txt = txt.set_position(((VIDEO_WIDTH - txt_w) // 2, sub_y)).set_start(seg["start"]).set_duration(dur)
                    layers.extend([bg, txt])
                else:
                    # No keywords — standard white subtitle
                    txt = TextClip(seg_text, fontsize=SUBTITLE_FONTSIZE, font=SUBTITLE_FONT,
                        color=SUBTITLE_COLOR, stroke_color=SUBTITLE_STROKE, stroke_width=SUBTITLE_STROKE_W,
                        method='caption', size=(VIDEO_WIDTH - 160, None), align='center')
                    txt_w, txt_h = txt.size
                    bg_w = min(txt_w + SUBTITLE_BG_PADDING * 2, VIDEO_WIDTH - 40)
                    bg_h = txt_h + SUBTITLE_BG_PADDING * 2
                    bg = ColorClip(size=(bg_w, bg_h), color=SUBTITLE_BG_COLOR).set_opacity(SUBTITLE_BG_OPACITY)
                    sub_y = int(VIDEO_HEIGHT * 0.50)  # CENTER SCREEN
                    bg = bg.set_position(((VIDEO_WIDTH - bg_w) // 2, sub_y - SUBTITLE_BG_PADDING)).set_start(seg["start"]).set_duration(dur)
                    txt = txt.set_position(((VIDEO_WIDTH - txt_w) // 2, sub_y)).set_start(seg["start"]).set_duration(dur)
                    layers.extend([bg, txt])
            except Exception as e:
                print(f"   ⚠️ Subtitle overlay failed: {e}")

    # Watermark badge — small left-side tag (positioned to avoid YouTube Shorts UI)
    # YT Shorts UI: top = channel name, right = like/comment/share, bottom = desc/music
    # Safe zone: left side, ~17% from top
    if ADD_WATERMARK:
        try:
            wm_txt = TextClip(
                WATERMARK_TEXT,
                fontsize=WATERMARK_FONT_SIZE, font=SUBTITLE_FONT,
                color="white", method='label',
            )
            txt_w, txt_h = wm_txt.size
            badge_w = txt_w + WATERMARK_PADDING_H * 2
            badge_h = txt_h + WATERMARK_PADDING_V * 2

            badge_x = WATERMARK_MARGIN_X
            badge_y = int(VIDEO_HEIGHT * WATERMARK_Y_PERCENT)

            wm_bg = ColorClip(size=(badge_w, badge_h), color=(0, 0, 0))
            wm_bg = wm_bg.set_opacity(WATERMARK_OPACITY).set_position((badge_x, badge_y)).set_duration(total_duration)
            wm_txt = wm_txt.set_opacity(0.9).set_position(
                (badge_x + WATERMARK_PADDING_H, badge_y + WATERMARK_PADDING_V)
            ).set_duration(total_duration)
            layers.extend([wm_bg, wm_txt])
        except Exception as e:
            print(f"   ⚠️ Watermark overlay failed: {e}")

    # Hook — scroll-stopping text overlay (first 2 seconds)
    # Design: large bold white text, first word in YELLOW for attention
    if ADD_HOOK_TEXT:
        try:
            hook_line = hook_text_from_claude.strip().upper() if hook_text_from_claude else " ".join(fresh_topic.split()[:4]).upper()
            hook_words = hook_line.split()[:6]

            # First word = yellow (attention grab), rest = white
            first_word = hook_words[0] if hook_words else ""
            rest_words = " ".join(hook_words[1:]) if len(hook_words) > 1 else ""

            hook_y = int(VIDEO_HEIGHT * 0.18)

            # Semi-transparent dark panel behind text (taller for bigger text)
            # Build text clips first to measure total height
            text_layers = []

            # First word — YELLOW, extra large
            if first_word:
                ht1 = TextClip(first_word, fontsize=80, font=SUBTITLE_FONT, color="#FFD700",
                    stroke_color="black", stroke_width=4, method='caption',
                    size=(VIDEO_WIDTH - 120, None), align='center')
                text_layers.append(("first", ht1))

            # Remaining words — WHITE, large
            if rest_words:
                ht2 = TextClip(rest_words, fontsize=68, font=SUBTITLE_FONT, color="white",
                    stroke_color="black", stroke_width=4, method='caption',
                    size=(VIDEO_WIDTH - 120, None), align='center')
                text_layers.append(("rest", ht2))

            # Calculate total height for background panel
            total_text_h = sum(tc.size[1] for _, tc in text_layers) + 20 * len(text_layers)
            max_text_w = max((tc.size[0] for _, tc in text_layers), default=VIDEO_WIDTH - 200)
            panel_w = min(max_text_w + 80, VIDEO_WIDTH - 40)
            panel_h = total_text_h + 50

            # Dark panel with slight transparency
            hbg = ColorClip(size=(panel_w, panel_h), color=(0, 0, 0)).set_opacity(0.75)
            hbg = hbg.set_position(((VIDEO_WIDTH - panel_w) // 2, hook_y - 15))
            hbg = hbg.set_start(0).set_duration(HOOK_DURATION).crossfadeout(0.4)
            layers.append(hbg)

            # Yellow accent bar on left edge of panel (MrBeast style)
            accent_bar = ColorClip(size=(6, panel_h), color=(255, 215, 0)).set_opacity(0.95)
            accent_bar = accent_bar.set_position(((VIDEO_WIDTH - panel_w) // 2, hook_y - 15))
            accent_bar = accent_bar.set_start(0).set_duration(HOOK_DURATION).crossfadeout(0.4)
            layers.append(accent_bar)

            # Position text clips
            current_y = hook_y + 10
            for label, tc in text_layers:
                tc_w, tc_h = tc.size
                tc = tc.set_position(((VIDEO_WIDTH - tc_w) // 2, current_y))
                tc = tc.set_start(0).set_duration(HOOK_DURATION).crossfadeout(0.4)
                layers.append(tc)
                current_y += tc_h + 15

        except Exception as e:
            print(f"   ⚠️ Hook text overlay failed: {e}")

    # CTA — end-of-video branded strip (professional bar style)
    if ADD_CTA_OVERLAY:
        try:
            cta_start = max(0, total_duration - 4.0)
            cta_dur = 4.0

            # Main branded bar (full-width, slim, orange-red brand color)
            bar_height = 72
            bar_y = int(VIDEO_HEIGHT * 0.80)
            cta_bar = ColorClip(size=(VIDEO_WIDTH, bar_height), color=(230, 60, 20)).set_opacity(0.92)
            cta_bar = cta_bar.set_position((0, bar_y)).set_start(cta_start).set_duration(cta_dur).crossfadein(0.4)

            # Thin accent line on top of bar for depth
            accent_line = ColorClip(size=(VIDEO_WIDTH, 3), color=(255, 255, 255)).set_opacity(0.50)
            accent_line = accent_line.set_position((0, bar_y)).set_start(cta_start).set_duration(cta_dur).crossfadein(0.4)

            # Clean white text (no stroke needed — bar provides contrast)
            cta_txt = TextClip(CTA_TEXT, fontsize=36, font=SUBTITLE_FONT, color="white",
                method='label')
            cta_w, cta_h = cta_txt.size
            cta_txt = cta_txt.set_position(((VIDEO_WIDTH - cta_w) // 2, bar_y + (bar_height - cta_h) // 2))
            cta_txt = cta_txt.set_start(cta_start).set_duration(cta_dur).crossfadein(0.4)

            layers.extend([cta_bar, accent_line, cta_txt])
        except Exception as e:
            print(f"   ⚠️ CTA overlay failed: {e}")

    final_video = CompositeVideoClip(layers, size=(VIDEO_WIDTH, VIDEO_HEIGHT))

    # De-click only — do NOT fade the closer. A real ending lands at full
    # volume on the last word, then stops (a "button", not a trail-off).
    # 1.2s used to fade the punchy final line into silence = no ending feel.
    from moviepy.audio.fx.audio_fadeout import audio_fadeout
    audio_clip = audio_fadeout(audio_clip, 0.25)

    # Extract Veo ambient audio (scene sounds at low volume)
    ambient_clip, ambient_temp_files = extract_ambient_audio(downloaded_clips, total_duration)

    # Mix background music with voice
    mixed_audio = mix_background_music(audio_clip, total_duration, mood=music_mood)

    # Build transition whoosh layer at clip boundaries (1 whoosh per cut)
    num_clips_for_sfx = max(1, len(downloaded_clips))
    cd_for_sfx = total_duration / num_clips_for_sfx
    transition_sfx = build_transition_sfx_layer(num_clips_for_sfx, cd_for_sfx, total_duration)

    # Add Veo ambient audio layer if available
    has_ambient = False
    audio_layers = [mixed_audio]
    if ambient_clip:
        audio_layers.append(ambient_clip)
        has_ambient = True
    if transition_sfx is not None:
        audio_layers.append(transition_sfx)
        print(f"   💥 Added {num_clips_for_sfx - 1} transition whooshes at clip boundaries")
    if len(audio_layers) > 1:
        mixed_audio_with_ambient = CompositeAudioClip(audio_layers)
        amb_str = f" + Veo ambient ({int(VEO_AMBIENT_VOLUME * 100)}%)" if has_ambient else ""
        sfx_str = " + transition SFX" if transition_sfx is not None else ""
        print(f"   ✅ Final audio: voice + BGM{amb_str}{sfx_str}")
    else:
        mixed_audio_with_ambient = mixed_audio

    final_video = final_video.set_audio(mixed_audio_with_ambient)

    # ── 8b. Branded 2-second outro card (replaces orphan black-frame ending) ──
    try:
        outro_dur = 2.0
        outro_bg = ColorClip(size=(VIDEO_WIDTH, VIDEO_HEIGHT),
                             color=(15, 15, 25), duration=outro_dur)
        outro_title = TextClip("Sale91.com", fontsize=132, font=SUBTITLE_FONT,
                               color="white", stroke_color="black", stroke_width=3,
                               method='label')
        otw, oth = outro_title.size
        outro_title = (outro_title
                       .set_position(((VIDEO_WIDTH - otw) // 2, int(VIDEO_HEIGHT * 0.36)))
                       .set_duration(outro_dur)
                       .crossfadein(0.3))
        outro_sub = TextClip("MOQ sirf 10 pieces", fontsize=70, font=SUBTITLE_FONT,
                             color="#FFD700", method='label')
        osw, osh = outro_sub.size
        outro_sub = (outro_sub
                     .set_position(((VIDEO_WIDTH - osw) // 2, int(VIDEO_HEIGHT * 0.50)))
                     .set_duration(outro_dur)
                     .crossfadein(0.3))
        outro_cta = TextClip("Order now → Sale91.com", fontsize=48, font=SUBTITLE_FONT,
                             color="white", method='label')
        ocw, och = outro_cta.size
        outro_cta = (outro_cta
                     .set_position(((VIDEO_WIDTH - ocw) // 2, int(VIDEO_HEIGHT * 0.60)))
                     .set_duration(outro_dur)
                     .crossfadein(0.3))
        outro_card = CompositeVideoClip(
            [outro_bg, outro_title, outro_sub, outro_cta],
            size=(VIDEO_WIDTH, VIDEO_HEIGHT)
        ).set_duration(outro_dur)
        final_video = concatenate_videoclips([final_video, outro_card], method="chain")
        print(f"   🎬 Appended {outro_dur}s outro card")
    except Exception as e:
        print(f"   ⚠️ Outro card skipped: {e}")

    # ── 9. Render (with safety net for ambient audio issues) ──
    filename = f"SHORT_{random.randint(1000,9999)}.mp4"
    output_path = f"{WORK_DIR}/{filename}"
    print(f"   🎬 Rendering {filename}...")
    try:
        final_video.write_videofile(output_path, fps=FPS, codec="libx264", audio_codec="aac",
            preset="medium", bitrate="8000k", threads=4, logger=None)
    except Exception as render_err:
        if has_ambient:
            print(f"   ⚠️ Render failed with ambient audio: {render_err}")
            print(f"   🔄 Retrying WITHOUT ambient audio...")
            final_video = final_video.set_audio(mixed_audio)
            final_video.write_videofile(output_path, fps=FPS, codec="libx264", audio_codec="aac",
                preset="medium", bitrate="8000k", threads=4, logger=None)
        else:
            raise

    print(f"   ✅ Video ready: {output_path}")

    # ── 9b. Generate Thumbnail (AI Pipeline: Claude brief → Gemini image, with basic fallback) ──
    first_clip = downloaded_clips[0] if downloaded_clips else None
    thumbnail_path = None
    if AI_THUMBNAIL:
        thumbnail_path = generate_ai_thumbnail(
            hook_text_from_claude, fresh_topic, script_voice,
            veo_clip_path=first_clip, claude_client=claude,
            genai_client=veo_client, cost_tracker=cost
        )
    if not thumbnail_path:
        thumbnail_path = generate_thumbnail(hook_text_from_claude, fresh_topic, veo_clip_path=first_clip)

    # ── 10. Upload to YouTube ──
    upload_failed = False
    vid_id = None
    if TEST_MODE:
        print(f"\n{'='*60}")
        print(f"  🧪 TEST MODE COMPLETE — video NOT uploaded")
        print(f"  📁 Video saved: {output_path}")
        if thumbnail_path:
            print(f"  🖼️ Thumbnail: {thumbnail_path}")
        print(f"  📌 Title: {yt_title}")
        print(f"  🎵 Mood: {music_mood}")
        print(f"  🏷️ Tags: {', '.join(get_topic_tags(fresh_topic)[:8])}")
        print(f"{'='*60}")
    else:
        print("   📤 Uploading to YouTube...")
        youtube = get_youtube_service()
        if youtube:
            # ── 10a. Check engagement for past videos (feedback loop) ──
            print("   📊 Checking past video engagement...")
            check_past_engagement(youtube)

            # ── 10a-ii. Check Instagram Reel engagement (feedback loop) ──
            print("   📸 Checking Instagram Reel engagement...")
            check_instagram_engagement()

            try:
                vid_id, vid_url = upload_to_youtube(youtube, output_path, yt_title, yt_description, yt_tags, topic=fresh_topic)

                if vid_id and vid_id != "?":
                    if NEW_TEST_MODE or SINGLE_VEO_TEST:
                        # Test modes: skip comment/playlist — video is unlisted for testing only
                        mode_label = "NEW TEST MODE" if NEW_TEST_MODE else "SINGLE VEO TEST"
                        print(f"   🧪 {mode_label} — skipping comment pin & playlist (unlisted test upload)")
                        if thumbnail_path:
                            print(f"   📁 Thumbnail saved locally: {thumbnail_path}")
                    else:
                        # ── 10b. Pin CTA comment (wait for YouTube to process video) ──
                        # Scheduled videos are private — YouTube blocks comments on private videos.
                        # Temporarily switch to unlisted, post comment, then restore scheduled state.
                        original_publish_at = None
                        switched_to_unlisted = False
                        if SCHEDULE_PUBLISH:
                            try:
                                # Save original publishAt before switching
                                vid_status = youtube.videos().list(part="status", id=vid_id).execute()
                                if vid_status.get("items"):
                                    original_publish_at = vid_status["items"][0]["status"].get("publishAt")
                                youtube.videos().update(
                                    part="status",
                                    body={"id": vid_id, "status": {"privacyStatus": "unlisted"}}
                                ).execute()
                                switched_to_unlisted = True
                                print("   🔓 Temporarily set to unlisted for commenting...")
                            except Exception as e:
                                print(f"   ⚠️ Could not switch to unlisted: {e}")

                        print("   ⏳ Waiting 30s for YouTube video processing before commenting...")
                        time.sleep(30)
                        # Custom pinned comment includes the blog URL — gives the blog
                        # a second backlink from this YouTube video (description + comment),
                        # AND drives Shorts viewers to click through to the article.
                        custom_pin = (
                            f"📖 Full guide with photos & FAQs: {blog_url_preview}\n\n"
                            f"📦 Order plain t-shirts (MOQ 10): https://sale91.com\n\n"
                            f"{get_pin_tail()}"
                        )
                        pin_comment(youtube, vid_id, comment_text=custom_pin)

                        # Restore scheduled/private status
                        if switched_to_unlisted and original_publish_at:
                            try:
                                youtube.videos().update(
                                    part="status",
                                    body={
                                        "id": vid_id,
                                        "status": {
                                            "privacyStatus": "private",
                                            "publishAt": original_publish_at,
                                        }
                                    }
                                ).execute()
                                print(f"   🔒 Restored scheduled/private status")
                            except Exception as e:
                                print(f"   ⚠️ Could not restore private status: {e}")
                                print(f"   ℹ️ Video may remain unlisted — check YouTube Studio")

                        # NOTE: YouTube Shorts do NOT support custom thumbnails via the Data API.
                        # The thumbnails.set endpoint returns success but silently ignores it for Shorts.
                        # Custom Shorts thumbnails can only be set via YouTube Studio UI or mobile app.
                        # See: https://issuetracker.google.com/issues/381127084
                        if thumbnail_path:
                            print("   ℹ️ YouTube Shorts: custom thumbnails not supported via API (use YouTube Studio)")
                            print(f"   📁 Thumbnail saved locally: {thumbnail_path}")

                        # ── 10c. Add to series playlist ──
                        add_to_playlist(youtube, vid_id, fresh_topic)

                print(f"\n{'='*60}")
                print(f"  ✅ DAILY SHORT COMPLETE!")
                print(f"  🔗 {vid_url}")
                print(f"  📌 {yt_title}")
                print(f"{'='*60}")
            except Exception as upload_err:
                print(f"   ❌ YouTube upload failed: {upload_err}")
                upload_failed = True
        else:
            print("   ❌ YouTube auth failed. Video saved locally.")
            upload_failed = True

    # ── 10d. Cross-post to Instagram Reels (independent of YouTube success) ──
    if not TEST_MODE:
        # Auto-refresh Instagram token if expiring within 7 days
        if CROSS_POST_INSTAGRAM and os.environ.get("INSTAGRAM_ACCESS_TOKEN"):
            print("\n📸 Instagram Token Check...")
            refresh_instagram_token_if_needed()
        # Use IG-specific title (different patterns work on Reels Explore vs YT search)
        ig_media_id = cross_post_to_instagram(output_path, ig_title, yt_description, fresh_topic, thumbnail_path=thumbnail_path)
        if ig_media_id and not str(ig_media_id).startswith("test:"):
            save_ig_upload_record(ig_media_id, ig_title, fresh_topic, cover_meta=COVER_META)

        # ── 10d2. Cross-post to Facebook Reels + Telegram (dormant until secrets exist) ──
        fb_caption = f"{ig_title}\n\n{yt_description.split(chr(10))[0]}\n\n📦 Order: Sale91.com"
        print("\n📘 Facebook Reel cross-post...")
        publish_fb_reel(output_path, fb_caption)
        print("\n✈️ Telegram channel cross-post...")
        post_telegram_channel(output_path, fb_caption)

    # ── 10e. Generate & Publish SEO Blog Post ──
    if not TEST_MODE and not NEW_TEST_MODE and not SINGLE_VEO_TEST and not upload_failed and vid_id:
        try:
            blog_ok, gate_reason, fallback_slug = blog_publish_gate(fresh_topic, blog_title, claude_client=claude)
        except Exception as e:
            blog_ok, gate_reason, fallback_slug = True, f"gate error (fail-open): {e}", None
        if not blog_ok:
            print(f"   🚫 Blog skipped: {gate_reason}")
            print("      (Shorts/Reels unchanged — the gate only limits Google-facing article volume)")
            # Keep the daily IG carousel alive: link it to the closest existing
            # article (cluster match if any, else the latest post — both have
            # their images already on S3 as {slug}-hero.jpg etc.).
            try:
                active = sorted(_load_blog_history_active(), key=lambda h: h.get('date', ''), reverse=True)
                fb = next((h for h in active if h.get('slug') == fallback_slug), None) or (active[0] if active else None)
                if fb and fb.get('slug'):
                    generate_ig_carousel_draft(
                        claude_client=claude,
                        cost_tracker=cost,
                        blog_title=fb.get('title', ''),
                        blog_url=f"{BLOG_BASE_URL}/p/{fb['slug']}.html",
                        blog_slug=fb['slug'],
                        topic=fresh_topic,
                        script_english=script_english,
                        tags=yt_tags,
                    )
            except Exception as e:
                print(f"   ⚠️ IG carousel draft failed (non-fatal): {e}")
        else:
            try:
                # IMPORTANT: blog uses blog_title (Latin script — Google/AI search optimized),
                # NOT yt_title (Hindi — YouTube algo optimized). yt_title and ig_title stay
                # Hindi for their respective platforms. See optimize_title() docstring.
                blog_html, blog_slug, blog_url, blog_images = generate_blog_post(
                    claude_client=claude,
                    cost_tracker=cost,
                    topic=fresh_topic,
                    title=blog_title,                       # ← Latin script for Google SEO
                    description=yt_description,
                    script_english=script_english,
                    tags=yt_tags,
                    hook_text=hook_text_from_claude,
                    vid_id=vid_id,
                    vid_url=vid_url,
                    video_prompts=video_prompts,
                )

                if blog_html and os.environ.get('AWS_ACCESS_KEY_ID'):
                    if publish_blog_to_s3(blog_html, blog_slug, blog_title, blog_url, blog_images, vid_id=vid_id, tags=yt_tags):
                        excerpt = _extract_blog_excerpt(blog_html, max_words=200) if blog_html else ""
                        save_blog_history(fresh_topic, blog_title, blog_slug, blog_url, vid_url,
                                          tags=yt_tags, description=yt_description,
                                          word_count=len(blog_html.split()) if blog_html else 0,
                                          excerpt=excerpt)
                        print(f"   ✅ Blog published: {blog_url}")

                        # Generate Reddit post draft for the employee to paste manually.
                        # Non-fatal — if this fails, the daily pipeline doesn't break.
                        hero_url = None
                        for img_bytes, fname in (blog_images or []):
                            if fname == "hero.webp":
                                hero_url = f"{BLOG_BASE_URL}/p/{blog_slug}-hero.webp"
                                break
                        try:
                            # Reddit posts go to English-speaking subs (r/PrintOnDemand etc.) —
                            # blog_title (Latin script) is what Reddit users want anyway.
                            generate_reddit_post(
                                claude_client=claude,
                                cost_tracker=cost,
                                topic=fresh_topic,
                                blog_title=blog_title,    # ← Latin script
                                blog_url=blog_url,
                                script_english=script_english,
                                tags=yt_tags,
                                hero_image_url=hero_url,
                            )
                        except Exception as e:
                            print(f"   ⚠️ Reddit draft failed (non-fatal): {e}")

                        # IG carousel draft — separate from the same-day Reel cross-post.
                        # Published next day at 1 PM IST by ig_carousel.yml workflow.
                        # Caption can mix scripts; the prompt handles Reels-style voice.
                        try:
                            generate_ig_carousel_draft(
                                claude_client=claude,
                                cost_tracker=cost,
                                blog_title=blog_title,    # ← Latin script (caption gen handles mixing)
                                blog_url=blog_url,
                                blog_slug=blog_slug,
                                topic=fresh_topic,
                                script_english=script_english,
                                tags=yt_tags,
                                uploaded_filenames=[fn for _, fn in blog_images] if blog_images else None,
                            )
                        except Exception as e:
                            print(f"   ⚠️ IG carousel draft failed (non-fatal): {e}")
                elif blog_html:
                    print("   ⚠️ Blog generated but AWS credentials not found — skipping S3 upload")
                # else: generate_blog_post already printed its warning
            except Exception as e:
                print(f"   ⚠️ Blog generation/publish failed (non-fatal): {e}")
                # Blog failure should NEVER break the video pipeline

    # ── 10f. Cost summary + save ──
    print()
    print(cost.summary())
    cost.save(fresh_topic, yt_title)

    # Save metadata for retry if upload failed
    if upload_failed:
        meta = {
            "title": yt_title,
            "description": yt_description,
            "tags": list(yt_tags) if yt_tags else [],
            "topic": fresh_topic,
            "video_path": output_path,
            "thumbnail_path": thumbnail_path or "",
        }
        meta_path = f"{WORK_DIR}/upload_meta.json"
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)
        print(f"   💾 Upload metadata saved: {meta_path}")

    # Cleanup (keep video + thumbnail if upload failed)
    for f in downloaded_clips:
        try: os.remove(f)
        except: pass
    for f in ambient_temp_files:
        try: os.remove(f)
        except: pass
    try: os.remove(audio_path)
    except: pass
    if thumbnail_path and not upload_failed:
        try: os.remove(thumbnail_path)
        except: pass


def test_ai_thumbnail_standalone(topic=None, script=None, video_path=None, base_image_path=None):
    """Standalone test for AI thumbnail pipeline.
    Run: python daily_short.py --test-thumbnail [--topic "..."] [--script "..."] [--base-image path/to/image.png]
    Generates thumbnail locally without uploading anywhere."""
    import anthropic
    from google import genai

    anthropic_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    google_key = (os.environ.get("GOOGLE_API_KEY") or "").strip()

    if not anthropic_key or not google_key:
        print("❌ Missing ANTHROPIC_API_KEY or GOOGLE_API_KEY environment variables")
        return

    claude_client = anthropic.Anthropic(api_key=anthropic_key)
    genai_client = genai.Client(api_key=google_key)

    # Defaults for testing
    if not topic:
        topic = "T-Shirt Wholesale Business Tips"
    if not script:
        script = (
            "Aaj hum baat karenge wholesale t-shirt business ke baare mein. "
            "Agar aap ₹49 mein plain t-shirts kharidna chahte ho toh Sale91.com pe jaao. "
            "Yahan pe aapko mil jayega bulk mein t-shirts at best price. "
            "MOQ sirf 10 pieces hai, aur pan India delivery available hai."
        )

    hook_text = script.split(".")[0].strip()

    os.makedirs(WORK_DIR, exist_ok=True)

    # If base image provided, create a dummy video clip from it so the pipeline uses it as background
    effective_video_path = video_path
    if base_image_path and os.path.exists(base_image_path):
        from PIL import Image
        # Save resized copy as the frame source — the pipeline extracts frames from video,
        # so we create a 1-second static video from the image
        try:
            base_img = Image.open(base_image_path).convert("RGB")
            base_img = base_img.resize((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.LANCZOS)
            # Create a 1-second video from the static image using ffmpeg
            temp_frame = f"{WORK_DIR}/base_frame_input.png"
            effective_video_path = f"{WORK_DIR}/base_frame_video.mp4"
            base_img.save(temp_frame, "PNG")
            import subprocess
            subprocess.run([
                "ffmpeg", "-y", "-loop", "1", "-i", temp_frame,
                "-t", "1", "-pix_fmt", "yuv420p",
                "-vf", f"scale={THUMBNAIL_WIDTH}:{THUMBNAIL_HEIGHT}",
                effective_video_path,
            ], capture_output=True, timeout=30)
            print(f"  🖼️ Base image loaded: {base_image_path} → 1s video for frame extraction")
        except Exception as e:
            print(f"  ⚠️ Failed to process base image: {e}")
            effective_video_path = video_path

    print(f"\n{'='*60}")
    print(f"  🧪 AI THUMBNAIL TEST MODE")
    print(f"  📌 Topic: {topic}")
    print(f"  🪝 Hook: {hook_text}")
    if base_image_path:
        print(f"  🖼️ Base image: {base_image_path}")
    elif video_path:
        print(f"  🎥 Video: {video_path}")
    else:
        print(f"  🎨 No base image — using gradient background")
    print(f"{'='*60}\n")

    cost = CostTracker()

    thumbnail_path = generate_ai_thumbnail(
        hook_text, topic, script,
        veo_clip_path=effective_video_path,
        claude_client=claude_client,
        genai_client=genai_client,
        cost_tracker=cost,
    )

    print(f"\n{'='*60}")
    if thumbnail_path:
        print(f"  ✅ Thumbnail generated: {thumbnail_path}")
        print(f"  💰 Cost: ${cost.total():.4f}")
    else:
        print(f"  ❌ AI thumbnail failed — falling back to basic...")
        thumbnail_path = generate_thumbnail(hook_text, topic, veo_clip_path=effective_video_path)
        if thumbnail_path:
            print(f"  ✅ Basic thumbnail generated: {thumbnail_path}")
        else:
            print(f"  ❌ All thumbnail generation failed")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    import sys
    # Sunday recap mode — fetch user's Saturday main-channel upload, generate
    # blog + Reddit (no Veo). Triggered by .github/workflows/sunday_recap.yml
    if "--mode=sunday-recap" in sys.argv or "--sunday-recap" in sys.argv:
        ok = process_main_channel_recap()
        sys.exit(0 if ok else 1)
    # IG carousel post mode — read the latest ig_drafts JSON and publish to IG.
    # Triggered by .github/workflows/ig_carousel.yml at 4:30 UTC (10 AM IST).
    if "--mode=ig-carousel-post" in sys.argv or "--ig-carousel-post" in sys.argv:
        ok = post_latest_ig_carousel()
        sys.exit(0 if ok else 1)
    # Brand-asset upload mode — push avatar + logo to S3 without a full video run.
    # Triggered manually by .github/workflows/upload_brand_assets.yml.
    if "--mode=upload-brand-assets" in sys.argv:
        import boto3
        ak = os.environ.get("AWS_ACCESS_KEY_ID")
        sk = os.environ.get("AWS_SECRET_ACCESS_KEY")
        if not ak or not sk:
            print("❌ AWS credentials not found")
            sys.exit(1)
        _s3 = boto3.client("s3", region_name="ap-south-1",
                           aws_access_key_id=ak, aws_secret_access_key=sk)
        _cf = boto3.client("cloudfront", region_name="ap-south-1",
                           aws_access_key_id=ak, aws_secret_access_key=sk)
        upload_brand_assets(_s3, _cf)
        sys.exit(0)
    # Internal-link backfill — one-time mesh build across all existing posts.
    # Triggered manually by .github/workflows/backfill_internal_links.yml.
    if "--mode=backfill-internal-links" in sys.argv:
        sys.exit(backfill_internal_links())
    # Rewrite thin legacy pages in place per rewrite_plan.json. Optional --slug=X
    # to process a single page (test one before the full run). Triggered manually
    # by .github/workflows/rewrite_thin_posts.yml.
    if "--mode=rewrite-thin" in sys.argv:
        only = None
        for a in sys.argv:
            if a.startswith("--slug="):
                only = a.split("=", 1)[1]
        sys.exit(rewrite_thin_posts(only_slug=only))
    # Repair existing posts NOW (JSON-LD, author-image loop, avatar path, fake
    # rating) without waiting for a daily publish. Triggered manually by
    # .github/workflows/repair_existing_posts.yml.
    if "--mode=repair-existing-posts" in sys.argv:
        import boto3
        ak = os.environ.get("AWS_ACCESS_KEY_ID")
        sk = os.environ.get("AWS_SECRET_ACCESS_KEY")
        if not ak or not sk:
            print("❌ AWS credentials not found")
            sys.exit(1)
        _s3 = boto3.client("s3", region_name="ap-south-1",
                           aws_access_key_id=ak, aws_secret_access_key=sk)
        _cf = boto3.client("cloudfront", region_name="ap-south-1",
                           aws_access_key_id=ak, aws_secret_access_key=sk)
        repair_existing_blog_posts(_s3, _cf)
        sys.exit(0)
    if "--test-thumbnail" in sys.argv:
        _topic = None
        _script = None
        _video = None
        _base_image = None
        for i, arg in enumerate(sys.argv):
            if arg == "--topic" and i + 1 < len(sys.argv):
                _topic = sys.argv[i + 1]
            elif arg == "--script" and i + 1 < len(sys.argv):
                _script = sys.argv[i + 1]
            elif arg == "--video" and i + 1 < len(sys.argv):
                _video = sys.argv[i + 1]
            elif arg == "--base-image" and i + 1 < len(sys.argv):
                _base_image = sys.argv[i + 1]
        test_ai_thumbnail_standalone(topic=_topic, script=_script, video_path=_video, base_image_path=_base_image)
    else:
        main()
