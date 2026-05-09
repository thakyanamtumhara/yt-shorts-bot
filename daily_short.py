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
    VideoFileClip, AudioFileClip, TextClip,
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
ADD_SUBTITLES = True
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

# A/B Test Publish Times — rotate between 3 slots to find best engagement
# Each slot: (hour, minute, label)
PUBLISH_SLOTS = [
    (21, 30, "9:30 PM"),   # Original — post-dinner scroll time
    (11,  0, "11:00 AM"),  # Morning — chai break / office downtime
    (19,  0, "7:00 PM"),   # Evening — commute / pre-dinner scroll
]
# Rotation: Mon/Thu → 11 AM, Tue/Fri → 7 PM, Wed/Sat/Sun → 9:30 PM
PUBLISH_SLOT_SCHEDULE = {
    0: 1,  # Monday    → 11:00 AM
    1: 2,  # Tuesday   → 7:00 PM
    2: 0,  # Wednesday → 9:30 PM
    3: 1,  # Thursday  → 11:00 AM
    4: 2,  # Friday    → 7:00 PM
    5: 0,  # Saturday  → 9:30 PM
    6: 0,  # Sunday    → 9:30 PM
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

# AI Thumbnail (Claude text strategy + Gemini image generation)
AI_THUMBNAIL = True
AI_THUMBNAIL_GEMINI_MODEL = "gemini-3-pro-image-preview"
AI_THUMBNAIL_GEMINI_FALLBACK = "gemini-3.1-flash-image-preview"
THUMBNAIL_RESEARCH_FILE = "thumbnail_research.json"  # Weekly research cache
THUMBNAIL_RESEARCH_MAX_AGE_DAYS = 7

# Auto-Pin Comment — posts a CTA comment and pins it on every upload
AUTO_PIN_COMMENT = True
PIN_COMMENT_TEXT = """🤔 Aapka next question kya hai? Comment mein puchho 👇

📦 Plain t-shirt for printing? → Sale91.com (MOQ 10 pieces, Pan India)"""

# Auto-Playlist — organize videos into series playlists automatically
AUTO_PLAYLIST = True
PLAYLIST_CACHE_FILE = f"{WORK_DIR}/playlist_cache.json"  # Cache playlist IDs

# Instagram Reels Cross-Post (requires INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_BUSINESS_ID secrets)
CROSS_POST_INSTAGRAM = True
IG_API_VERSION = "v21.0"  # v22.0 causes "Carousel item cannot be published standalone" error

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


# ═══════════════════════════════════════════════════════════════════════
# THUMBNAIL GENERATION
# ═══════════════════════════════════════════════════════════════════════

def generate_thumbnail(hook_text, topic, output_path=None, veo_clip_path=None):
    """Generate a branded thumbnail image for YouTube SEO.
    If veo_clip_path is provided, extracts the best frame from the first Veo clip
    and overlays text on it (much more clickable than a plain gradient).
    Returns the file path to the thumbnail PNG, or None on failure."""
    if not GENERATE_THUMBNAIL:
        return None

    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
        import numpy as np

        if output_path is None:
            output_path = f"{WORK_DIR}/thumbnail_{random.randint(100,999)}.png"

        # Try to extract a frame from the first Veo clip (hook scene)
        bg_from_clip = False
        if veo_clip_path and os.path.exists(veo_clip_path):
            try:
                clip = VideoFileClip(veo_clip_path)
                # Sample frames at 25%, 40%, 50% of clip duration — pick the most "interesting" one
                # (highest contrast/color variance = more visually striking)
                best_frame = None
                best_score = -1
                for t_pct in [0.25, 0.40, 0.50, 0.60]:
                    t = min(t_pct * clip.duration, clip.duration - 0.1)
                    frame = clip.get_frame(t)
                    # Score: standard deviation of pixel values (higher = more visual contrast)
                    score = float(np.std(frame))
                    if score > best_score:
                        best_score = score
                        best_frame = frame

                if best_frame is not None:
                    img = Image.fromarray(best_frame)
                    img = img.resize((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.LANCZOS)
                    # Darken slightly for text readability + boost contrast
                    enhancer = ImageEnhance.Brightness(img)
                    img = enhancer.enhance(0.7)
                    enhancer = ImageEnhance.Contrast(img)
                    img = enhancer.enhance(1.3)
                    bg_from_clip = True
                    print(f"   🖼️ Thumbnail: using Veo frame (contrast score: {best_score:.0f})")

                clip.close()
            except Exception as e:
                print(f"   ⚠️ Veo frame extraction failed: {e}, falling back to gradient")

        # Fallback: gradient background
        if not bg_from_clip:
            img = Image.new("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), color=(15, 15, 25))
            draw_bg = ImageDraw.Draw(img)
            for y in range(THUMBNAIL_HEIGHT):
                ratio = y / THUMBNAIL_HEIGHT
                r = int(15 + 60 * ratio)
                g = int(15 - 10 * ratio)
                b = int(25 - 15 * ratio)
                r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
                draw_bg.line([(0, y), (THUMBNAIL_WIDTH, y)], fill=(r, g, b))

        draw = ImageDraw.Draw(img)

        # Try to load a good font, fall back to default
        font_hook = None
        font_topic = None
        font_paths = [
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",  # Supports Hindi/Devanagari
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        font_file = None
        for fp in font_paths:
            if os.path.exists(fp):
                font_file = fp
                break

        if font_file:
            font_hook = ImageFont.truetype(font_file, 72)
            font_topic = ImageFont.truetype(font_file, 36)
        else:
            font_hook = ImageFont.load_default()
            font_topic = ImageFont.load_default()

        # SAFE ZONE for 9:16 Reels: top 10% and bottom 20% are covered by YouTube UI
        safe_top = int(THUMBNAIL_HEIGHT * 0.15)       # Start text at 15% from top
        safe_bottom = int(THUMBNAIL_HEIGHT * 0.80)    # Nothing below 80%
        safe_left = int(THUMBNAIL_WIDTH * 0.10)       # 10% left margin
        safe_right = int(THUMBNAIL_WIDTH * 0.90)      # 10% right margin
        safe_width = safe_right - safe_left

        # Hook text — first word YELLOW (scroll-stop), rest WHITE
        hook_display = (hook_text or topic.split("—")[0].strip()).upper()
        all_words = hook_display.split()
        first_word = all_words[0] if all_words else ""
        rest_words = " ".join(all_words[1:]) if len(all_words) > 1 else ""

        # Use larger font for first word
        font_first = None
        if font_file:
            font_first = ImageFont.truetype(font_file, 90)

        y_start = safe_top

        def _draw_outlined(draw, x, y, text, font, fill, outline=(0, 0, 0), width=4):
            for dx in range(-width, width + 1):
                for dy in range(-width, width + 1):
                    if dx * dx + dy * dy <= width * width:
                        draw.text((x + dx, y + dy), text, font=font, fill=outline)
            draw.text((x, y), text, font=font, fill=fill)

        # Draw first word in YELLOW (big)
        current_y = y_start
        if first_word:
            bbox = draw.textbbox((0, 0), first_word, font=font_first or font_hook)
            fw_w = bbox[2] - bbox[0]
            fw_x = max(safe_left, (THUMBNAIL_WIDTH - fw_w) // 2)
            _draw_outlined(draw, fw_x, current_y, first_word, font_first or font_hook, (255, 215, 0))
            current_y += (bbox[3] - bbox[1]) + 15

        # Draw remaining words in WHITE (wrap within safe zone)
        if rest_words:
            rest_lines = []
            current_line = ""
            for word in rest_words.split():
                test = f"{current_line} {word}".strip()
                bbox = draw.textbbox((0, 0), test, font=font_hook)
                if bbox[2] - bbox[0] > safe_width:
                    if current_line:
                        rest_lines.append(current_line)
                    current_line = word
                else:
                    current_line = test
            if current_line:
                rest_lines.append(current_line)

            for line in rest_lines[:3]:
                if current_y > safe_bottom - 50:
                    break  # Don't overflow into bottom unsafe zone
                bbox = draw.textbbox((0, 0), line, font=font_hook)
                text_w = bbox[2] - bbox[0]
                x = max(safe_left, (THUMBNAIL_WIDTH - text_w) // 2)
                _draw_outlined(draw, x, current_y, line, font_hook, (255, 255, 255))
                current_y += (bbox[3] - bbox[1]) + 10

        # Topic summary — smaller text below hook (only if within safe zone)
        if current_y + 60 < safe_bottom:
            topic_short = topic[:60] + ("..." if len(topic) > 60 else "")
            bbox = draw.textbbox((0, 0), topic_short, font=font_topic)
            topic_w = bbox[2] - bbox[0]
            topic_y = current_y + 25
            draw.text((max(safe_left, (THUMBNAIL_WIDTH - topic_w) // 2), topic_y), topic_short,
                      font=font_topic, fill=(200, 200, 200))

        img.save(output_path, "PNG", quality=95)
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
            print(f"   ⚠️ Thumbnail file not created or too small")
            return None
        print(f"   🖼️ Thumbnail generated: {os.path.basename(output_path)}")
        return output_path

    except Exception as e:
        print(f"   ⚠️ Thumbnail generation failed: {e}")
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
        "layout": "Text top 15-45% for 9:16, face on one side text on other, rule of thirds",
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
                    f"{ig_research_context}\n"
                    "RESEARCH TASK:\n"
                    "Analyze top-performing Reels and Shorts in the Indian business niche. "
                    "Focus on: t-shirt business, wholesale business, bulk selling, garment industry, small business ideas India, low investment business. "
                    "Also analyze trending Reels in the broader 'business/money' niche in India for thumbnail inspiration.\n"
                    "For each pattern you identify, consider: what text is on the thumbnail, what colors are used, what emotions/expressions appear, what layout works.\n\n"
                    "IMPORTANT CONTEXT:\n"
                    "- These are VERTICAL 9:16 Reel/Shorts thumbnails\n"
                    "- Viewers see these as tiny previews on mobile phones while scrolling\n"
                    "- On Instagram: thumbnail appears as Reel cover on profile grid and Explore page\n"
                    "- Top 10% and bottom 20% may be covered by UI — text must be in the 15%-45% from top zone\n"
                    "- No brand names, URLs, or watermarks — ONLY the hook text goes on the thumbnail\n"
                    "- Think like a viewer scrolling on their phone — what makes them STOP and click?\n\n"
                    "Return a JSON object (no markdown fencing) with these fields:\n"
                    "- power_words: array of 10-15 Hindi/Hinglish power words that get clicks on Reels/Shorts (e.g., Secret, सच, Mistake, Free, Shocking, Reality, Truth, Hack)\n"
                    "- best_colors: object with 'text' (array of 4-5 hex codes — best: Yellow #FFD700, White #FFFFFF, Red #FF0000, Orange #FF6600) and 'stroke' (hex code for outline, usually black)\n"
                    "- text_rules: string summarizing best practices for Reels thumbnail text — max 3-4 words, Hinglish performs best, include numbers/prices, create curiosity/urgency (max 2 sentences)\n"
                    "- layout: string summarizing layout rules for 9:16 Reels thumbnails — face on one side text on other, rule of thirds, safe zone 15%-45% from top (max 2 sentences)\n"
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
        "You are given a reference image (the base frame for the thumbnail) along with the video topic, hook, and script. "
        "Generate a DETAILED thumbnail brief that a designer (Gemini) will use to place text on this exact image.\n\n"
        "THUMBNAIL TEXT RULES:\n"
        "- CRITICAL: MAXIMUM 3-4 WORDS ONLY. Count your words — if more than 4 words, you MUST shorten it. Shorter = better CTR.\n"
        "  Good examples (3-4 words): '₹49 T-Shirt Secret', 'Fabric गलती!', 'GSM का Truth', 'Print Crack क्यों?', '300 Piece RETURN?'\n"
        "  BAD examples (too long): '₹49 में T-Shirt Business कैसे करें' (7 words — WAY too long!)\n"
        "- Use Hindi-English mix (Hinglish) — this performs best in India\n"
        "- Include a number or price if relevant (numbers get clicks)\n"
        "- Create curiosity or urgency\n"
        "- Use power words: Secret, Free, Shocking, Reality, Truth, Mistake, Hack, सच, गलती\n"
        "- Think like a viewer scrolling on their phone — what makes them STOP and click?\n"
        "- If the topic is broad, pick the sharpest angle that creates maximum curiosity\n\n"
        "OUTPUT FORMAT — Return EXACTLY this format (the designer will read this directly):\n\n"
        "=== THUMBNAIL BRIEF ===\n"
        "Format: Reel 9:16\n"
        "Thumbnail Text: [your chosen text — 3-4 words MAXIMUM, Hinglish]\n"
        "Text Color: [hex code] ([color name])\n"
        "Text Position: [Top-Left / Top-Center / Top-Right] ([specific placement description based on the image — e.g., 'above the person's head, on the white wall/ceiling area'])\n"
        "Text Effect: [describe stroke/outline color + shadow details — e.g., 'White stroke (thick outline, 4-5px) + black drop shadow for maximum pop']\n"
        "Font Style: Bold, Impact/Block style — extra thick weight\n"
        "Additional Design Notes:\n\n"
        "[Describe what you see in the reference image — person, setting, products, background]\n\n"
        "[Specific placement instruction — WHERE exactly to place text relative to the person/products. e.g., 'Place the text ABOVE his head in the clean ceiling/wall zone — do NOT cover his face']\n\n"
        "[Text sizing instruction — e.g., 'The text is only 2-3 words in Hindi — make it BIG, filling the entire top third of the frame']\n\n"
        "[Color/contrast instruction — e.g., 'Red text (#FF0000) with thick white outline (#FFFFFF, 4-5px) and subtle black shadow so it pops against the light background']\n\n"
        "[What NOT to add — e.g., 'Do NOT add any other design elements — the warehouse stock behind him already tells the story visually']\n\n"
        "[Bottom half instruction — e.g., 'Keep the bottom half completely clean — just his face and the stock bags']\n\n"
        "[Readability note — 'This is a Reel thumbnail so text must be readable even at small mobile preview size']\n\n"
        "Video Title: [suggested Hindi/Hinglish title for context — NOT placed on thumbnail]\n"
        "=== END BRIEF ===\n\n"
        "IMPORTANT:\n"
        "- Look at the reference image carefully and describe it accurately\n"
        "- Give SPECIFIC placement instructions based on what you see (not generic)\n"
        "- Best performing text colors for Indian YouTube: Yellow (#FFD700), White (#FFFFFF), Red (#FF0000), Orange (#FF6600)\n"
        "- The designer will keep the base image EXACTLY as-is and only add text on top\n"
        "- Text + face must live in the 25%-65% vertical band — that band survives BOTH YT Shorts UI AND\n"
        "  the Instagram profile-grid 4:5 crop. Top 12% and bottom 25% will be cropped/covered.\n"
        "- FACE-FIRST RULE: The thumbnail MUST have a clear human face making eye contact (shocked or\n"
        "  pointing-at-text expression). If the reference image lacks a face, instruct the designer to\n"
        "  ADD a realistic Indian factory-owner face (man 30-45y, simple shirt, warm lighting) in the\n"
        "  upper-center band. Faces drive 2-3× higher CTR than object-only thumbnails on Instagram.\n\n"
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

        # Extract the thumbnail text from the brief for logging
        thumb_text = "?"
        thumb_color = "?"
        for line in brief_text.split("\n"):
            if line.strip().startswith("Thumbnail Text:"):
                thumb_text = line.split(":", 1)[1].strip()
            elif line.strip().startswith("Text Color:"):
                thumb_color = line.split(":", 1)[1].strip()

        # Enforce strict 3-4 word limit — truncate if Claude exceeded it
        words = thumb_text.split()
        if len(words) > 4:
            print(f"   ⚠️ Thumbnail text too long ({len(words)} words): \"{thumb_text}\"")
            thumb_text_short = " ".join(words[:4])
            print(f"   ✂️ Truncated to 4 words: \"{thumb_text_short}\"")
            # Update the brief text too so Gemini gets the truncated version
            brief_text = brief_text.replace(thumb_text, thumb_text_short)
            thumb_text = thumb_text_short

        print(f"   ✅ Thumbnail brief: \"{thumb_text}\" | color: {thumb_color}")

        # Return both the full brief text (for Gemini) and parsed fields (for logging/fallback)
        return {
            "brief_text": brief_text,
            "text": thumb_text,
            "color": thumb_color,
        }

    except Exception as e:
        print(f"   ⚠️ Thumbnail brief generation failed: {e}")
        return None


def generate_ai_thumbnail(hook_text, topic, script_text, veo_clip_path=None,
                          claude_client=None, genai_client=None, cost_tracker=None):
    """Generate a high-quality AI thumbnail using Claude (detailed brief with vision) + Gemini Pro (image).
    1. Extract best Veo frame
    2. Send frame to Claude Opus — Claude sees the image and generates a detailed design brief
    3. Send frame + Claude's full brief to Gemini Pro — Gemini places text on untouched image
    Falls back to basic generate_thumbnail() on failure. Returns thumbnail file path or None."""
    if not AI_THUMBNAIL or not claude_client or not genai_client:
        return None

    try:
        from PIL import Image, ImageEnhance
        from google.genai import types
        import numpy as np

        print("   🤖 AI Thumbnail Pipeline: Claude brief (with vision) → Gemini Pro image...")

        # Step 1: Get/refresh research patterns
        research = refresh_thumbnail_research(claude_client)

        # Step 2: Extract best frame from Veo clip FIRST (so Claude can see it)
        frame_image = None
        if veo_clip_path and os.path.exists(veo_clip_path):
            try:
                clip = VideoFileClip(veo_clip_path)
                best_frame = None
                best_score = -1
                for t_pct in [0.25, 0.40, 0.50, 0.60]:
                    t = min(t_pct * clip.duration, clip.duration - 0.1)
                    frame = clip.get_frame(t)
                    score = float(np.std(frame))
                    if score > best_score:
                        best_score = score
                        best_frame = frame
                if best_frame is not None:
                    frame_image = Image.fromarray(best_frame)
                    frame_image = frame_image.resize((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.LANCZOS)
                    print(f"   🖼️ AI thumbnail: using Veo frame (contrast score: {best_score:.0f})")
                clip.close()
            except Exception as e:
                print(f"   ⚠️ Frame extraction failed: {e}")

        # Create gradient fallback if no frame
        if frame_image is None:
            frame_image = Image.new("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), (15, 15, 25))
            from PIL import ImageDraw
            draw = ImageDraw.Draw(frame_image)
            for y in range(THUMBNAIL_HEIGHT):
                r = int(15 + (40 * y / THUMBNAIL_HEIGHT))
                g = int(15 + (20 * y / THUMBNAIL_HEIGHT))
                b = int(25 + (15 * y / THUMBNAIL_HEIGHT))
                draw.line([(0, y), (THUMBNAIL_WIDTH, y)], fill=(r, g, b))
            print("   🖼️ AI thumbnail: using gradient background (no clip available)")

        # Step 3: Generate detailed thumbnail brief via Claude (with frame image for vision)
        source_insights = get_source_channel_top_topics(5)
        audience_qs = get_audience_questions(5)
        _ig_brief = get_ig_engagement_summary()
        brief = generate_thumbnail_brief(
            claude_client, script_text, hook_text, topic, research,
            source_insights=source_insights, audience_qs=audience_qs,
            cost_tracker=cost_tracker, frame_image=frame_image,
            ig_summary=_ig_brief
        )
        if not brief:
            print("   ⚠️ AI thumbnail: brief generation failed, falling back to basic")
            return None

        # Step 4: Send frame image + Claude's full detailed brief to Gemini Pro
        # Claude's brief_text contains the complete design direction — Gemini just executes it
        claude_brief = brief.get("brief_text", "")

        gemini_prompt = (
            "You are a professional YouTube thumbnail designer. You receive a reference image and a design brief, "
            "and you create a high-CTR YouTube thumbnail by adding text and design elements ON TOP of the reference image. "
            "You are a DESIGN EXECUTOR — you do NOT research or change the text/title. You only design.\n\n"
            "BUSINESS CONTEXT: Indian wholesale bulk plain t-shirt brand\n"
            "AUDIENCE: Indian viewers (Hindi/Hinglish text is common)\n"
            "STYLE: Bold, high-contrast, attention-grabbing — typical top Indian YouTube channel style\n\n"
            f"{claude_brief}\n\n"
            "SAFE ZONE RULES (CRITICAL — NEVER BREAK THESE):\n"
            "- This is a 9:16 Reel thumbnail (1080x1920). The Instagram profile grid will crop this to 4:5 (~1080x1350)\n"
            "  showing only the MIDDLE portion. YouTube Shorts UI also covers edges.\n"
            "- TOP 12% of image: BLOCKED (platform UI / IG grid crop). NEVER place text here.\n"
            "- BOTTOM 25% of image: BLOCKED (YouTube Shorts UI + IG grid crop). NEVER place text here.\n"
            "- LEFT/RIGHT 8%: edge margin. Avoid text here.\n"
            "- ALL critical text and the FACE must be within 25%-65% from the top — that is the\n"
            "  visible center band that survives both YT Shorts UI and IG profile-grid 4:5 crop.\n\n"
            "FACE / EYES RULE (HIGHEST CTR LEVER — MUST FOLLOW):\n"
            "- The cover MUST contain a clear human face making EYE CONTACT with the camera.\n"
            "- Face should occupy 25-40% of the visible frame, positioned in the upper-center band.\n"
            "- Expression: SHOCKED, surprised, or pointing-at-text — never neutral / closed-mouth.\n"
            "- Eyes wide open, looking directly into the lens (parasocial scroll-stop trigger).\n"
            "- If the reference image has no face, ADD a realistic Indian factory-owner / customer face\n"
            "  in the upper-center band (men 30-45y, beard ok, simple shirt, warm skin tones).\n"
            "- NEVER show the back of a head, blurry face, or face cropped at the eyes.\n\n"
            "IMAGE RULES (NEVER BREAK THESE):\n"
            "- Output exactly 1080x1920 pixels (Reel 9:16)\n"
            "- The reference image is the BASE/BACKGROUND. You may zoom, recompose, or add a face\n"
            "  on top, but keep the same overall scene/colors so it matches the video opener.\n"
            "- Keep face and product clearly visible at all times\n\n"
            "TEXT RULES (NEVER BREAK THESE):\n"
            "- ONLY use the EXACT thumbnail text from the brief above — NOTHING ELSE\n"
            "- Do NOT add ANY extra text — no website URLs, no brand names, no watermarks, no labels, no subtitles\n"
            "- Do NOT add 'Sale91', 'Sale91.com', 'WATCH', or any text not in the brief\n"
            "- Maximum 4-5 words total on the entire thumbnail — the brief text ONLY\n"
            "- Text must be READABLE on a mobile phone screen (imagine 4cm wide)\n"
            "- Use BOLD, thick, block-style fonts — NEVER thin or decorative fonts\n"
            "- Every letter must have a visible stroke (outline) AND shadow for readability\n"
            "- NEVER place text over my face\n"
            "- Text should fill 30-40% of the thumbnail area\n\n"
            "DO NOT:\n"
            "- ❌ Change the thumbnail text — use it EXACTLY as provided\n"
            "- ❌ Add extra text beyond what's in the brief\n"
            "- ❌ Generate a completely new image — my reference photo is the base\n"
            "- ❌ Use thin, script, or decorative fonts\n"
            "- ❌ Place text over my face or the main product\n\n"
            "MUST DO:\n"
            "- ✅ Keep my original image INTACT as the base — do NOT modify it\n"
            "- ✅ Add bold readable text with proper stroke/shadow as specified in the brief\n"
            "- ✅ Follow the brief EXACTLY — the brief has specific placement and color instructions\n"
            "- ✅ Generate ONE best thumbnail\n"
            "- ✅ Respect safe zones — no text in bottom 40% for Reels\n"
        )

        output_path = f"{WORK_DIR}/thumbnail_{random.randint(100,999)}.png"

        # Try primary model (Pro), then fallback (Flash)
        # Each model gets up to 3 retries with exponential backoff for transient errors (503/429)
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
        GEMINI_IMAGE_TIMEOUT = 120  # seconds per model attempt (2 min — keep CI fast)
        GEMINI_IMAGE_MAX_RETRIES = 3

        for model_name in [AI_THUMBNAIL_GEMINI_MODEL, AI_THUMBNAIL_GEMINI_FALLBACK]:
            for attempt in range(1, GEMINI_IMAGE_MAX_RETRIES + 1):
                try:
                    retry_label = f" (attempt {attempt}/{GEMINI_IMAGE_MAX_RETRIES})" if attempt > 1 else ""
                    print(f"   🎨 Generating thumbnail via Gemini ({model_name}){retry_label} [timeout={GEMINI_IMAGE_TIMEOUT}s]...")

                    def _call_gemini(m=model_name):
                        return genai_client.models.generate_content(
                            model=m,
                            contents=[gemini_prompt, frame_image],
                            config=types.GenerateContentConfig(
                                response_modalities=["TEXT", "IMAGE"],
                            ),
                        )

                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(_call_gemini)
                        response = future.result(timeout=GEMINI_IMAGE_TIMEOUT)

                    # Extract generated image from response
                    for part in response.parts:
                        if part.inline_data is not None:
                            generated_img = part.as_image()
                            # Ensure PIL Image (some Gemini models return non-PIL types)
                            if not isinstance(generated_img, Image.Image):
                                import io
                                generated_img = Image.open(io.BytesIO(part.inline_data.data))
                            # Resize to exact thumbnail dimensions
                            generated_img = generated_img.resize((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.LANCZOS)
                            generated_img.save(output_path, "PNG", quality=95)
                            if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
                                print(f"   ⚠️ AI thumbnail file not created or too small")
                                break
                            print(f"   ✅ AI thumbnail generated: {output_path}")
                            if cost_tracker:
                                cost_tracker.track_gemini_image()
                            return output_path

                    print(f"   ⚠️ Gemini ({model_name}) returned no image")
                    break  # No image in response — skip retries, try next model

                except FuturesTimeout:
                    print(f"   ⚠️ Gemini ({model_name}) timed out after {GEMINI_IMAGE_TIMEOUT}s")
                    break  # Timeout — skip retries, try next model

                except Exception as e:
                    err_str = str(e).lower()
                    is_transient = any(k in err_str for k in ("500", "internal", "503", "429", "unavailable", "rate limit", "too many", "overloaded", "high demand", "resource exhausted"))
                    if is_transient and attempt < GEMINI_IMAGE_MAX_RETRIES:
                        wait = 15 * attempt  # 15s, 30s, 45s — give Gemini time to recover
                        print(f"   ⚠️ Gemini ({model_name}) attempt {attempt} failed: {e}")
                        print(f"   ⏳ Retrying in {wait}s (API is busy, backing off)...")
                        time.sleep(wait)
                        continue
                    print(f"   ⚠️ Gemini ({model_name}) failed: {e}")
                    break  # Non-transient error or out of retries — try next model

            else:
                # All retries exhausted for this model
                pass

            if model_name == AI_THUMBNAIL_GEMINI_MODEL:
                print(f"   🔄 Trying fallback model...")

        print("   ❌ AI thumbnail: all Gemini models failed, falling back to basic")
        return None

    except Exception as e:
        print(f"   ⚠️ AI thumbnail pipeline failed: {e}")
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
    """Query Instagram Insights API to find the best posting hour today.

    Uses the 'online_followers' metric which returns hourly follower
    activity for each day of the week (0-23 hours, timezone of the account).
    Returns a datetime (IST) for the next best posting slot, or None on failure.
    """
    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    today_weekday = now_ist.weekday()  # 0=Mon, 6=Sun

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
            return None

        data = resp.json().get("data", [])
        if not data:
            print(f"   ⚠️ No Instagram Insights data returned")
            return None

        # online_followers returns {day_name: {hour: count}} for each day
        values = data[0].get("values", [])
        if not values:
            return None

        # Map day index to Instagram's day names
        ig_day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        today_name = ig_day_names[today_weekday]

        # Get today's hourly data
        today_data = None
        for v in values:
            day_data = v.get("value", {})
            if today_name in day_data:
                today_data = day_data[today_name]
                break

        if not today_data:
            # Fallback: try to get any day's data and use it
            for v in values:
                day_data = v.get("value", {})
                if day_data:
                    # Use the first available day
                    first_day = list(day_data.keys())[0]
                    today_data = day_data[first_day]
                    print(f"   ℹ️ Using {first_day}'s data as proxy for today")
                    break

        if not today_data:
            return None

        # today_data is {hour_str: follower_count}
        # Find the top 3 hours with most online followers
        sorted_hours = sorted(today_data.items(), key=lambda x: x[1], reverse=True)
        print(f"   📊 Instagram audience peak hours today ({today_name}):")
        for h, count in sorted_hours[:3]:
            print(f"      {int(h):02d}:00 IST — {count:,} followers online")

        # Pick the best hour that's still in the future (at least 15 min from now)
        min_schedule_time = now_ist + timedelta(minutes=15)
        for hour_str, _ in sorted_hours:
            hour = int(hour_str)
            candidate = now_ist.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate > min_schedule_time:
                print(f"   🕐 Best posting slot: {candidate.strftime('%I:%M %p IST')} ({today_name})")
                return candidate

        # All peak hours have passed today — schedule for tomorrow's best hour
        best_hour = int(sorted_hours[0][0])
        tomorrow = now_ist + timedelta(days=1)
        candidate = tomorrow.replace(hour=best_hour, minute=0, second=0, microsecond=0)
        print(f"   🕐 Today's peak passed — scheduling for tomorrow {candidate.strftime('%I:%M %p IST')}")
        return candidate

    except Exception as e:
        print(f"   ⚠️ Instagram best-time detection failed: {e}")
        return None


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
        topic_hashtags = get_topic_hashtags(topic)
        ig_caption = f"{title}\n\n{description.split(chr(10))[0]}\n\n{' '.join(topic_hashtags[:10])}\n\n📦 Order: Sale91.com"

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

        if schedule_for_later and schedule_timestamp:
            # Schedule instead of instant publish — IG handles it
            container_data["published"] = "false"
            container_data["scheduled_publish_time"] = str(schedule_timestamp)

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

def save_ig_upload_record(ig_media_id, title, topic):
    """Save Instagram media ID after upload for later engagement checking."""
    try:
        records = []
        if os.path.exists(IG_ENGAGEMENT_FILE):
            with open(IG_ENGAGEMENT_FILE, "r") as f:
                records = json.load(f)

        ist = pytz.timezone(TIMEZONE)
        now = datetime.now(ist)

        records.append({
            "media_id": str(ig_media_id),
            "title": title,
            "topic": topic,
            "published_at": now.isoformat(),
            "checked": False,
        })

        with open(IG_ENGAGEMENT_FILE, "w") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        print(f"   📝 Instagram media ID saved for engagement tracking")
    except Exception as e:
        print(f"   ⚠️ Failed to save IG upload record: {e}")


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
            # Scheduled posts have "scheduled:" prefix — strip it for API call
            if media_id.startswith("scheduled:"):
                media_id = media_id.replace("scheduled:", "", 1)

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
                    print(f"   ⚠️ IG insights failed for {media_id}: {media_data['error'].get('message', '')}")
                    continue

                # Fetch Reels-specific insights (views, reach, shares, saves)
                insights_resp = requests.get(
                    f"https://graph.facebook.com/{IG_API_VERSION}/{media_id}/insights",
                    params={
                        "metric": "plays,reach,saved,shares",
                        "access_token": ig_token,
                    },
                    timeout=15,
                )
                insights_data = insights_resp.json()

                # Parse insights
                ig_views = 0
                ig_reach = 0
                ig_saves = 0
                ig_shares = 0

                for metric in insights_data.get("data", []):
                    name = metric.get("name", "")
                    values = metric.get("values", [{}])
                    val = values[0].get("value", 0) if values else 0
                    if name == "plays":
                        ig_views = val
                    elif name == "reach":
                        ig_reach = val
                    elif name == "saved":
                        ig_saves = val
                    elif name == "shares":
                        ig_shares = val

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

        checked = [r for r in records if r.get("checked")]
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

        checked = [r for r in records if r.get("checked")]
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
        checked = [r for r in records if r.get("checked")]
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

━━━ CRITICAL: SPEAKING STYLE ━━━

You are writing EXACTLY like a real Indian textile manufacturer talks — but using
MICRO-STORYTELLING to hook the viewer in the first 2 seconds.

TARGET LENGTH: 8-12 sentences. The Short should be 45-55 seconds long when spoken naturally.
This is NOT a quick tip — this is a MINI STORY with a beginning, middle, and end.

STRUCTURE (follow this EVERY time):
1. HOOK (first 1-2 sentences) — Start with a REAL STORY, shocking fact, or customer incident:
   - "Ek customer aaya tha, bola print dhul gaya 2 wash mein..."
   - "Pehle main bhi yahi galti karta tha..."
   - "Ek baar ek banda 500 piece ka order cancel karwa diya..."
   - "Log sochte hai GSM jyada toh better... galat hai"
   - "Maine ek tshirt 2 saal pehni, ek 2 hafte mein kharab..."

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

4. NATURAL ENDING (1-2 sentences) — Trail off conclusively (ROTATE — use a DIFFERENT ending each video):
   - "...simple hai, pehle check kar lo, phir order karo."
   - "...bas yehi galti mat karna, aur kuch nahi."
   - "...toh wahi hota hai, bas."
   - "...itna kar lo, complaint nahi aayegi."
   - "...wo jyada theek rahega, try karke dekh lo."

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

1. 8-12 SENTENCES for a 45-55 second Short. Start with story/hook, build up, drop knowledge, end naturally.
2. FIRST SENTENCE = HOOK — customer story, personal experience, ya surprising fact.
   NEVER start with a definition or explanation. ALWAYS start with a STORY.
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
    NOT at the very end. Top-tier Reels open a loop, build tension, deliver payoff with
    ~10-15s of script left for "so what to do" — that 10s is where SHARES happen.

14. ONE SHOCKING NUMBER per script — Indian B2B Reels viewers SAVE for numbers they
    can use ("180 GSM", "₹140 cost", "10 piece MOQ", "3 wash mein fade"). Bury one
    surprising number in the middle that makes them want to remember/share.

15. STRUCTURE FOR REELS GRID DISCOVERY — a viewer scrolling Explore/Reels feed sees
    your video next to 30 others. The first 1.5 seconds must look DIFFERENT from
    a generic talking-head Short. The hook visual + bold caption do this — script's
    job is to EARN the hold past 3s.

16. NEVER end on a slow trailing-off when the script could END on the saveable insight.
    The Hindi "...bas yehi hota hai, simple hai" style is good for YT but it costs the
    Reels share-rate. Better: end the LAST sentence with the actionable takeaway, then
    let the outro card carry the rest. Trade off: slightly less natural-feeling close
    in exchange for higher save-and-share rate on IG.
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
4. NEVER use long narrative phrases like "complaint kabhi nahi aayegi"
   or "ye galti kabhi nahi hogi" as the FINAL sentence — those are
   mid-narrative phrases. Use them in the wrap-up sentence BEFORE the
   final short closer if you want.
5. The ending should match Ketu's actual speaking style — direct,
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

Write a short CURIOSITY-DRIVEN hook text (max 4 words) that appears on screen
for the first 1.5 seconds. This must make the viewer STOP SCROLLING instantly.

Good hook texts:
- "YE GALTI MAT KARNA..."
- "99% LOG YE NAHI JAANTE"
- "EK CUSTOMER NE BATAYA..."
- "SHOCKING QUALITY DIFFERENCE..."
- "PEHLE YE CHECK KARO"

Bad hook texts (boring, no curiosity):
- "GSM KA MATLAB KYA HAI"
- "FABRIC QUALITY TIPS"

━━━ VIDEO PROMPT RULES ━━━

Write 5 detailed video scene descriptions for AI video generation (Google Veo).
Each clip will be 8 seconds, vertical 9:16 format.

IMPORTANT VIDEO PROMPT GUIDELINES:
- Describe EXACTLY what the camera sees — this is for an AI that generates video
- Include camera angle, lighting, movement, and specific objects
- Focus on t-shirt/textile/manufacturing/printing industry visuals
- CRITICAL: Every clip must START with a visible, well-lit scene from frame 1. NO black intros, NO fade-from-black, NO dark openings. Begin with action immediately.
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
- Clip 1: HOOK — the problem or dramatic moment
- Clip 2: CONTEXT — setting the scene, showing the product/situation
- Clip 3: EXPLANATION — the comparison or process being discussed
- Clip 4: DEMONSTRATION — showing the technique, test, or method
- Clip 5: RESOLUTION — the correct result, quality product, or satisfying conclusion

OUTPUT THIS JSON ONLY (no markdown, no code blocks):
{{
    "title": "YouTube title in English, max 70 chars, SEO optimized for printing business",
    "description": "Description in English optimized for BOTH YouTube and Instagram. Include 6-8 hashtags that work on both platforms (Instagram hashtags drive Explore reach — use #tshirtbusiness #wholesale #printingbusiness etc). Include Sale91.com link.",
    "script_voice": "The ROMAN HINGLISH script. 8-12 sentences for 45-55 seconds. NO website. NO selling. Pure knowledge with storytelling.",
    "script_english": "ON-SCREEN SUBTITLE TEXT in simple English — paraphrase the Hinglish script so a non-Hindi speaker / deaf viewer can follow easily. SAME NUMBER OF SENTENCES AS script_voice (one English sentence per Hinglish sentence — keeps subtitle timing aligned). Each sentence ≤10 words. Plain language, no jargon (say 'thick fabric' not '240 GSM' if context allows; keep technical terms only when essential like DTF/GSM). Punctuation matches script_voice's sentence breaks. NOT a literal translation — capture the meaning concisely.",
    "hook_text": "Max 4 words, UPPERCASE, punchy curiosity-driven text for on-screen hook overlay",
    "music_mood": "Pick ONE mood for background music that matches this topic's emotion: upbeat | calm | serious | motivational | trendy",
    "video_prompt_1": "HOOK scene — the problem or dramatic moment. 40-80 words.",
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

    # ── Priority 2: Source/main channel posting patterns ──
    # PRIMARY when <1L views, FALLBACK when ≥1L views
    source_slot = get_source_optimized_slot()
    if source_slot:
        hour_views = get_source_channel_posting_patterns()
        print(f"   📊 Main channel posting patterns (IST):")
        for h in sorted(hour_views):
            print(f"      {h}:00 → {hour_views[h]:,} avg views")
        print(f"   🏆 Main-channel-optimized: using {source_slot[2]}")
        return source_slot

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
    for t in ["shorts", "youtubeshorts", "viral", "trending"]:
        if t not in [x.lower() for x in all_tags]:
            all_tags.append(t)
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
    "combed": "kohmd",  # 2026-05-08: produced "kom-BED" instead of "KOMD"
}

# Hinglish (Latin) → Devanagari for high-frequency Hindi words ElevenLabs
# reads as English. Confirmed broken via user feedback: "mat", "thik", "matlab",
# plus their semantic neighbors. English nouns (piece, order, customer, DTF,
# printer, loss) stay in Latin so the multilingual model code-switches naturally.
_TTS_HINGLISH_DEVANAGARI = {
    # User-reported (2026-05-08): mat/thik/assi mispronounced.
    "mat": "मत", "matlab": "मतलब", "thik": "ठीक", "theek": "ठीक",
    # High-frequency function words / verbs in our scripts:
    "nahi": "नहीं", "nahin": "नहीं", "haan": "हाँ", "hai": "है", "hain": "हैं",
    "tha": "था", "thi": "थी", "the": "थे",
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
    "lakh": "लाख", "laakh": "लाख",
    "crore": "करोड़",
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
    "galat": "ग़लत", "sahihe": "सहीहै",
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
})

# 'hi' as a particle (emphatic "ही") collides with English greeting "Hi" in
# theory — in our scripts it's always Hindi. Conditional add.
_TTS_HINGLISH_DEVANAGARI["hi"] = "ही"


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
        return None

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

    # Preserve ellipses — ElevenLabs reads "..." as a natural trail-off pause,
    # which is exactly the prosody our prompt requests for ending sentences.
    # (Earlier we stripped ellipses for Sarvam, but ElevenLabs handles them well.)
    s = s.replace("…", "...")  # normalize unicode ellipsis to three dots
    s = _re.sub(r",\s*,", ",", s)
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


def smart_pick_topic(claude_client, topic_bank, topic_history):
    """Smart topic selection:
    1. If unused topics in bank, pick from them but validate with Claude
    2. If bank exhausted, generate trending topics and pick the best one
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
            candidate = random.choice(prioritized[:pool_size])
            print(f"   📊 Top categories by engagement ({cat_source}): {', '.join(top_cats[:3])}")
        else:
            candidate = random.choice(unused)

        score, feedback = review_topic(claude_client, candidate, topic_history)
        print(f"   📋 Bank topic score: {score}/40 — {feedback}")
        if score >= TOPIC_MIN_SCORE:
            return candidate
        # If bank topic scored low, try 2 more from bank
        for _ in range(2):
            alt = random.choice(unused)
            if alt != candidate:
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

    # Score all trending candidates and pick the best
    best_topic = trending[0]
    best_score = 0
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
    """Generate 3 title variants and pick the best one for CTR.
    Uses source channel's top titles as reference."""
    print(f"   🏷️ Title optimization: generating A/B variants...")

    source_titles = get_source_channel_top_topics(5)
    source_context = ""
    if source_titles:
        source_context = f"""
REFERENCE: These titles got the MOST views on our main channel (50K subs):
{json.dumps(source_titles, ensure_ascii=False)}
Study their patterns — length, keywords, emotional hooks — and apply similar patterns."""

    prompt = f"""You are a YouTube Shorts + Instagram Reels title optimizer and JUDGE for an Indian B2B t-shirt brand.

CURRENT TITLE: {original_title}
TOPIC: {topic}
SCRIPT SUMMARY: {script_english[:200]}
{source_context}

Generate 3 alternative titles. Each must be:
- Max 70 characters (YouTube Shorts limit for mobile visibility)
- SEO optimized (include searchable keywords like "GSM", "DTG", "t-shirt", "printing")
- Curiosity-driven (make viewer NEED to watch)
- English (for broader reach + SEO, but Hinglish words OK if they add punch)
- Work on BOTH YouTube Shorts AND Instagram Reels (same title used on both platforms)

PLATFORM TIPS:
- YouTube: Search discoverability matters — include keywords people search for
- Instagram: Explore page + hashtag reach — emotional hooks, curiosity, trending phrases
- BOTH: Mobile-first (70 char max), number/price reveals get clicks, controversy/mistakes format works

TITLE STYLES TO TRY:
1. QUESTION style — "Why Does Your DTG Print Fade After 2 Washes?"
2. SHOCK/NUMBER style — "Rs 45 T-shirt vs Rs 90: The Print Quality Difference"
3. MISTAKE/WARNING style — "Stop Making This GSM Mistake (Most Printers Do)"

YOUR JOB AS JUDGE: Pick the ONE title that will get the MOST views across BOTH YouTube and Instagram combined.

OUTPUT THIS JSON ONLY (no markdown):
{{"titles": ["title1", "title2", "title3"], "best": 0, "reason": "why this title will get most clicks on both YouTube and Instagram"}}

"best" = index (0, 1, or 2) of the title you'd bet money on for highest CTR across both platforms."""

    try:
        resp = claude_client.messages.create(
            model="claude-opus-4-6", max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"): raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        result = json.loads(raw)

        titles = result.get("titles", [])
        best_idx = result.get("best", 0)
        reason = result.get("reason", "")

        if not titles:
            return original_title

        # Validate best_idx
        if best_idx < 0 or best_idx >= len(titles):
            best_idx = 0

        best_title = titles[best_idx]

        # Ensure it's not too long
        if len(best_title) > 100:
            best_title = best_title[:97] + "..."

        print(f"   🏷️ Title variants:")
        for i, t in enumerate(titles):
            marker = " ← PICKED" if i == best_idx else ""
            print(f"      {i+1}. {t}{marker}")
        print(f"      Reason: {reason}")

        return best_title

    except Exception as e:
        print(f"   ⚠️ Title optimization failed ({e}), using original")
        return original_title


# ═══════════════════════════════════════════════════════════════════════
# SEO BLOG POST GENERATION
# ═══════════════════════════════════════════════════════════════════════

# Blog config
BLOG_S3_BUCKET = "bulkplaintshirt.com"
BLOG_BASE_URL = "https://www.bulkplaintshirt.com"
BLOG_CLOUDFRONT_DIST_ID = "E21QLU9SBUBY7Z"
BLOG_HISTORY_FILE = "blog_history.json"
INDEXNOW_API_KEY = "sale91com2025indexnow"  # IndexNow key for Bing/Yandex/AI search


def inject_blog_seo(html_content, title, description, blog_url, today, slug, og_image_url=None):
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

    article_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "description": description[:160] if description else title,
        "url": blog_url,
        "datePublished": today,
        "dateModified": today,
        "author": {"@type": "Organization", "name": "Sale91.com", "url": "https://sale91.com"},
        "publisher": {
            "@type": "Organization",
            "name": "BulkPlainTshirt.com",
            "logo": {"@type": "ImageObject", "url": "https://www.bulkplaintshirt.com/catalog/img/logo.png"}
        },
        "image": og_image_url or "https://www.bulkplaintshirt.com/catalog/img/logo.png",
        "mainEntityOfPage": {"@type": "WebPage", "@id": blog_url}
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
        },
        "aggregateRating": {
            "@type": "AggregateRating",
            "ratingValue": "4.5",
            "reviewCount": "1050",
            "bestRating": "5"
        }
    }

    ld_blocks = [organization_ld, breadcrumb_ld, article_ld, product_ld]

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

    # ── 5. Inject JSON-LD ──
    if '</head>' in html_content:
        html_content = html_content.replace('</head>', f'{ld_scripts}\n</head>', 1)
    else:
        html_content = html_content.replace('</body>', f'{ld_scripts}\n</body>', 1)

    # ── 6. Inject bottom bar before </body> (only if not already present) ──
    if 'whatsapp.sale91.com' not in html_content.lower() or 'Order Now' not in html_content:
        html_content = html_content.replace('</body>', f'{bottom_bar}\n</body>', 1)

    faq_count = len(faq_pairs)
    ld_count = len(ld_blocks)
    print(f"   📊 Blog SEO: Injected {ld_count} JSON-LD schemas ({faq_count} FAQs) + sticky bottom bar")
    return html_content


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


def get_blog_prompt(topic, title, description, script_english, tags, hook_text, vid_id, image_urls=None, related_posts=None):
    """Build the Claude prompt for generating a full SEO blog post HTML."""
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")
    slug = generate_blog_slug(title)
    blog_url = f"{BLOG_BASE_URL}/p/{slug}.html"
    yt_embed_url = f"https://www.youtube.com/embed/{vid_id}"

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
   - Style images: width:100%; border-radius:12px; margin:20px 0;
   - Wrap each image in a <figure> with a <figcaption> describing what's shown
   - IMPORTANT: Use {og_image_url} as the og:image and twitter:image in meta tags
"""

    related_instructions = ""
    if related_posts:
        links_list = "\n".join(f"   - {rp['title']}: https://www.bulkplaintshirt.com/p/{rp['slug']}.html (hero image: https://www.bulkplaintshirt.com/p/{rp['slug']}-hero.webp)" for rp in related_posts)
        related_instructions = f"""
8. RELATED ARTICLES (Internal Linking — Card Layout — IMPORTANT for SEO):
   Add a "More Articles" section AFTER the FAQ section with these links as VISUAL CARDS:
{links_list}
   - Use a responsive flex/grid layout: 2-3 cards per row on desktop, 1 per row on mobile
   - Each card should contain:
     * The hero image (img tag, height:140px, object-fit:cover, border-radius:8px 8px 0 0, loading="lazy", add onerror="this.style.display='none'" fallback)
     * The article title as a clickable <a> link (font-size:14px, font-weight:600, padding:12px)
   - Card styling: background #fff, border-radius:10px, box-shadow:0 2px 6px rgba(0,0,0,0.06), border:1px solid #e8e8e0
   - Wrap in a container with: max-width:800px, margin:30px auto
   - Section heading: "More Articles" with H2 styling
   - This is CRITICAL for SEO internal linking — do NOT skip this section
"""

    return f"""You are an expert SEO content writer for Sale91.com (BulkPlainTshirt.com), India's leading B2B plain t-shirt manufacturer.

BUSINESS CONTEXT:
{BUSINESS_CONTEXT}

YOUR TASK: Write a comprehensive, 2000+ word SEO blog post based on this YouTube Shorts video topic.

TOPIC: {topic}
VIDEO TITLE: {title}
VIDEO DESCRIPTION: {description}
VIDEO SCRIPT (English): {script_english}
HOOK TEXT: {hook_text}
TAGS: {', '.join(tags) if tags else 'none'}

BLOG URL: {blog_url}
YOUTUBE EMBED: {yt_embed_url}
DATE: {today}

OUTPUT FORMAT: Return ONLY the complete HTML document (from <!DOCTYPE html> to </html>). No markdown code fences. No explanation.

REQUIREMENTS:

1. CONTENT (2000+ words):
   - Expand the video script into a detailed, informative article
   - Use H1 for main title, H2 for major sections, H3 for subsections
   - Write in professional English with occasional Hinglish terms where natural (like "GSM", industry terms)
   - Include practical tips, comparisons, and real-world examples from Indian textile industry
   - Mention Sale91.com naturally 2-3 times with links to https://sale91.com
   - Reference the product catalog: https://www.bulkplaintshirt.com/catalog/
   - MANDATORY: Include a "Watch the Video" section with this EXACT YouTube embed code:
     <div class="video-wrapper" style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;margin:20px 0;border-radius:12px;">
       <iframe src="{yt_embed_url}" style="position:absolute;top:0;left:0;width:100%;height:100%;border:0;" allowfullscreen loading="lazy"></iframe>
     </div>
     Place this video embed BEFORE the FAQ section. Do NOT skip the video embed — it is required.
   - End with a strong CTA section linking to Sale91.com

2. FAQ SECTION (5-8 Q&As):
   - Add an FAQ section with questions people actually search for
   - Related to the topic, GSM, fabric, printing, wholesale t-shirts
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
{image_instructions}{related_instructions}
CRITICAL CHECKLIST — your HTML MUST contain ALL of these:
   ✓ Sticky gold header at top (position:fixed) with clickable BulkPlainTshirt.com link
   ✓ YouTube video embed before FAQ section
   ✓ FAQ section with faq-question and faq-answer CSS classes on divs
   ✓ Related Articles section after FAQ (if related posts provided)
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
                       video_prompts=None):
    """Generate a full SEO blog post HTML using Claude Sonnet.
    Returns (html_content, slug, blog_url, blog_images) or (None, None, None, []) on failure."""
    print("   📝 Blog: Generating SEO article with images...")

    slug = generate_blog_slug(title)
    blog_url = f"{BLOG_BASE_URL}/p/{slug}.html"

    # Step 1: Generate AI images for the blog (if Replicate available)
    blog_images = generate_blog_images(video_prompts, topic, slug, cost_tracker)

    # Build image URLs for the HTML (so Claude can embed them)
    image_urls = []
    for _, filename in blog_images:
        image_urls.append(f"{BLOG_BASE_URL}/p/{slug}-{filename}")

    # Step 2: Load recent posts for internal linking
    related_posts = []
    try:
        if os.path.exists(BLOG_HISTORY_FILE):
            with open(BLOG_HISTORY_FILE) as f:
                history = json.load(f)
            # Get last 5 posts (excluding current slug)
            related_posts = [
                {"title": h["title"], "slug": h["slug"]}
                for h in reversed(history)
                if h.get("slug") != slug
            ][:5]
    except Exception:
        pass

    # Step 3: Generate blog HTML with image URLs + related posts embedded
    prompt = get_blog_prompt(topic, title, description, script_english, tags, hook_text, vid_id, image_urls, related_posts)

    try:
        resp = claude_client.messages.create(
            model="claude-sonnet-4-5-20250929",
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
        html_content = inject_blog_seo(html_content, title, description, blog_url, today, slug, og_image_url=first_image_url)

        word_count = len(html_content.split())
        print(f"   📝 Blog: Generated ~{word_count} words, slug: {slug}")
        print(f"   📝 Blog: URL will be {blog_url}")
        print(f"   📝 Blog: {len(blog_images)} images to upload")

        return html_content, slug, blog_url, blog_images

    except Exception as e:
        print(f"   ⚠️ Blog generation failed: {e}")
        return None, None, None, []


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
        key = f"p/{slug}.html"

        try:
            resp = s3_client.get_object(Bucket=BLOG_S3_BUCKET, Key=key)
            html = resp['Body'].read().decode('utf-8')

            # Skip if already has JSON-LD (already repaired)
            if 'application/ld+json' in html:
                continue

            # Inject JSON-LD + bottom bar
            html = inject_blog_seo(html, title, "", blog_url, date, slug)

            s3_client.put_object(
                Bucket=BLOG_S3_BUCKET,
                Key=key,
                Body=html.encode('utf-8'),
                ContentType='text/html; charset=utf-8',
                CacheControl='no-cache'
            )
            repaired.append(slug)
            print(f"   🔧 Repair: Fixed {slug}.html (JSON-LD + bottom bar)")
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
        print(f"   ✅ Repair: All existing posts already have JSON-LD")


def build_sitemap_xml(new_post=None):
    """Build complete sitemap XML from blog_history.json + static pages.

    Args:
        new_post: Optional dict with keys (slug, url, date) for a post not yet in blog_history.
    Returns:
        Complete sitemap XML string for p/map.xml.
    """
    import json as _json

    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")

    # Static pages — every page on the site for full crawlability
    static_pages = [
        (f"{BLOG_BASE_URL}/", today, "daily", "1.0"),
        (f"{BLOG_BASE_URL}/catalog/index.html", today, "weekly", "0.9"),
        (f"{BLOG_BASE_URL}/p/index.html", today, "daily", "0.9"),
        (f"{BLOG_BASE_URL}/p/feed.xml", today, "daily", "0.5"),
        (f"{BLOG_BASE_URL}/contactus.html", today, "monthly", "0.6"),
        (f"{BLOG_BASE_URL}/seller.html", today, "monthly", "0.6"),
        (f"{BLOG_BASE_URL}/p/FQA.html", today, "monthly", "0.8"),
        (f"{BLOG_BASE_URL}/calc/shipping-calculator.html", today, "monthly", "0.7"),
        (f"{BLOG_BASE_URL}/policy.html", today, "yearly", "0.3"),
        (f"{BLOG_BASE_URL}/returnpolicy.html", today, "yearly", "0.3"),
        (f"{BLOG_BASE_URL}/refundpolicy.html", today, "yearly", "0.3"),
        (f"{BLOG_BASE_URL}/term.html", today, "yearly", "0.3"),
        (f"{BLOG_BASE_URL}/disclaimer.html", today, "yearly", "0.3"),
    ]

    # Blog posts from history
    posts = []
    if os.path.exists(BLOG_HISTORY_FILE):
        try:
            with open(BLOG_HISTORY_FILE) as f:
                posts = _json.load(f)
        except Exception:
            posts = []

    if new_post and new_post.get('slug'):
        if not any(p.get('slug') == new_post['slug'] for p in posts):
            posts.append(new_post)

    # Build URL entries
    urls_xml = ''
    for loc, lastmod, changefreq, priority in static_pages:
        urls_xml += f'''  <url>
    <loc>{loc}</loc>
    <lastmod>{lastmod}</lastmod>
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
    legacy_sitemap_posts = [
        "240gsmtshirt", "DelhiBIGGESTPlainTShirtWarehouse",
        "Biggest-Plain-Tshirt-Warehouse", "build-tshirt-brand",
        "premium-plain-t-shirts-bulk-supplier-india", "wholesale-plain-t-shirts",
        "fast-delivery-plain-t-shirts-maharashtra",
        "plain-t-shirt-wholesale-near-me-delhi-india",
        "dropshipping", "AcidWashTshirt",
        "Price-Drop-240gsm-Dropshoulder-Tshirts", "Wholesale-Blanks",
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
    <lastmod>{today}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>
'''

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls_xml}</urlset>'''


def repair_sitemap(s3_client):
    """Rebuild sitemap from blog_history.json + static pages (idempotent)."""
    try:
        map_xml = build_sitemap_xml()
        s3_client.put_object(
            Bucket=BLOG_S3_BUCKET,
            Key='p/map.xml',
            Body=map_xml.encode('utf-8'),
            ContentType='application/xml; charset=utf-8',
            CacheControl='no-cache'
        )
        print(f"   \U0001f527 Sitemap: Rebuilt map.xml from blog_history")
    except Exception as e:
        print(f"   \u26a0\ufe0f Sitemap: Could not rebuild map.xml: {e}")


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
    posts = []
    if os.path.exists(BLOG_HISTORY_FILE):
        try:
            with open(BLOG_HISTORY_FILE) as f:
                posts = _json.load(f)
        except Exception:
            posts = []

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
        ("/p/Price-Drop-240gsm-Dropshoulder-Tshirts.html", "Price Drop 240gsm Dropshoulder T shirt"),
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

    posts = []
    if os.path.exists(BLOG_HISTORY_FILE):
        try:
            with open(BLOG_HISTORY_FILE) as f:
                posts = _json.load(f)
        except Exception:
            posts = []

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

    posts = []
    if os.path.exists(BLOG_HISTORY_FILE):
        try:
            with open(BLOG_HISTORY_FILE) as f:
                posts = _json.load(f)
        except Exception:
            posts = []

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


def publish_blog_to_s3(html_content, slug, title, blog_url, blog_images=None, vid_id=None):
    """Upload blog HTML + images to S3, update index.html, map.xml, llms.txt, and invalidate CloudFront."""
    import boto3

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

        # ── 4. Update /p/llms.txt ──
        try:
            try:
                resp = s3.get_object(Bucket=BLOG_S3_BUCKET, Key='p/llms.txt')
                llms_content = resp['Body'].read().decode('utf-8')
            except s3.exceptions.NoSuchKey:
                llms_content = f"# BulkPlainTshirt.com Blog Posts\n# B2B Plain T-shirt Manufacturer - Sale91.com\n# For LLM crawlers and AI assistants\n\n"

            llms_content += f"{title}: {blog_url}\n"

            s3.put_object(
                Bucket=BLOG_S3_BUCKET,
                Key='p/llms.txt',
                Body=llms_content.encode('utf-8'),
                ContentType='text/plain; charset=utf-8',
                CacheControl='no-cache'
            )
            print(f"   📤 Blog S3: Updated p/llms.txt")
            invalidation_paths.append('/p/llms.txt')
        except Exception as e:
            print(f"   ⚠️ Blog S3: Could not update llms.txt: {e}")

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
                      description="", word_count=0):
    """Save blog post metadata to blog_history.json for tracking."""
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

# LLM-friendly content index
# See https://llmstxt.org
LLMs.txt: {BLOG_BASE_URL}/p/llms.txt

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
    if sarvam_available:
        _tts_primary = "Sarvam Bulbul v3"
        _tts_fb = "ElevenLabs → OpenAI"
    elif elevenlabs_available:
        _tts_primary = "ElevenLabs Hindi"
        _tts_fb = "OpenAI gpt-4o-mini-tts"
    else:
        _tts_primary = "OpenAI (no Sarvam/11Labs)"
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
    yt_title = optimize_title(claude, data["title"], script_english, fresh_topic)
    yt_description = data["description"]
    yt_tags = data.get("tags", [])
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
                input=tts_input + "...",
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
        return

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
        whisper_segs = [
            {"start": float(s["start"]), "end": float(s["end"])}
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

    # Subtitles — CENTER SCREEN with keyword highlighting
    if ADD_SUBTITLES and subtitle_segments:
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
            hook_words = hook_line.split()[:4]

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

    # Gradual voice fade-out at the end (natural trailing off)
    from moviepy.audio.fx.audio_fadeout import audio_fadeout
    audio_clip = audio_fadeout(audio_clip, 1.2)

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
                        pin_comment(youtube, vid_id)

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
        ig_media_id = cross_post_to_instagram(output_path, yt_title, yt_description, fresh_topic, thumbnail_path=thumbnail_path)
        if ig_media_id and not str(ig_media_id).startswith("test:"):
            save_ig_upload_record(ig_media_id, yt_title, fresh_topic)

    # ── 10e. Generate & Publish SEO Blog Post ──
    if not TEST_MODE and not NEW_TEST_MODE and not SINGLE_VEO_TEST and not upload_failed and vid_id:
        try:
            blog_html, blog_slug, blog_url, blog_images = generate_blog_post(
                claude_client=claude,
                cost_tracker=cost,
                topic=fresh_topic,
                title=yt_title,
                description=yt_description,
                script_english=script_english,
                tags=yt_tags,
                hook_text=hook_text_from_claude,
                vid_id=vid_id,
                vid_url=vid_url,
                video_prompts=video_prompts,
            )

            if blog_html and os.environ.get('AWS_ACCESS_KEY_ID'):
                if publish_blog_to_s3(blog_html, blog_slug, yt_title, blog_url, blog_images, vid_id=vid_id):
                    save_blog_history(fresh_topic, yt_title, blog_slug, blog_url, vid_url,
                                      tags=yt_tags, description=yt_description,
                                      word_count=len(blog_html.split()) if blog_html else 0)
                    print(f"   ✅ Blog published: {blog_url}")
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
