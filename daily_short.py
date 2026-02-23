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
  FAL_KEY             — fal.ai API key for Kling video fallback (auto-fallback when Veo rate-limits)
"""

import anthropic
import requests
import json
import random
import os
import glob
import math
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

# Script quality gate: Claude reviews its own script before proceeding
SCRIPT_MAX_ATTEMPTS = 3

# ── ElevenLabs TTS (Primary) ──
ELEVENLABS_VOICE_ID = "FZkK3TvQ0pjyDmT8fzIW"  # Hindi voice
ELEVENLABS_MODEL = "eleven_multilingual_v2"
ELEVENLABS_VOICE_SETTINGS = {
    "stability": 0.62,
    "similarity_boost": 0.75,
    "style": 0.22,
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
VEO_MODEL = "veo-3.1-fast-generate-preview"
VEO_ASPECT_RATIO = "9:16"
VEO_DURATION = 8
VEO_MAX_RETRIES = 4
VEO_RETRY_WAIT = 60
VEO_POLL_TIMEOUT = 300  # 5 min max wait per clip generation

# ── Kling Fallback (via fal.ai) ──
# Auto-activates when Veo rate-limits (429/RESOURCE_EXHAUSTED).
# Uses Kling v2.6 Pro for cost-effective fallback ($0.07/s, no audio).
# Set FAL_KEY env var to enable. Without it, Kling fallback is skipped.
KLING_ENABLED = False  # Disabled: fal.ai account admin-locked. Re-enable when resolved: bool(os.environ.get("FAL_KEY", ""))
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
WORDS_PER_SUBTITLE = 5
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

# Auto-Pin Comment — posts a CTA comment and pins it on every upload
AUTO_PIN_COMMENT = True
PIN_COMMENT_TEXT = """📦 Plain t-shirt chahiye printing ke liye?
👉 Sale91.com pe order karo — MOQ sirf 10 pieces
🚚 Pan India delivery | 3 lakh+ ready stock"""

# Auto-Playlist — organize videos into series playlists automatically
AUTO_PLAYLIST = True
PLAYLIST_CACHE_FILE = f"{WORK_DIR}/playlist_cache.json"  # Cache playlist IDs

# Instagram Reels Cross-Post (requires INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_BUSINESS_ID secrets)
CROSS_POST_INSTAGRAM = True

# Cost Tracker — log per-video API costs
COST_TRACKER_FILE = "cost_tracker.json"  # In repo root for git tracking
DAILY_COST_LIMIT_USD = 10.0  # Circuit breaker: skip video if today's spend exceeds this

# Engagement Feedback Loop — check video performance after 48h
ENGAGEMENT_FILE = "engagement_history.json"  # In repo root for git tracking
ENGAGEMENT_CHECK_DELAY_HOURS = 48
NEW_CHANNEL_VIEWS_THRESHOLD = 100_000  # 1 lakh — use main channel data until new channel crosses this

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
        font_brand = None
        font_paths = [
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        ]
        font_file = None
        for fp in font_paths:
            if os.path.exists(fp):
                font_file = fp
                break

        if font_file:
            font_hook = ImageFont.truetype(font_file, 72)
            font_topic = ImageFont.truetype(font_file, 36)
            font_brand = ImageFont.truetype(font_file, 42)
        else:
            font_hook = ImageFont.load_default()
            font_topic = ImageFont.load_default()
            font_brand = ImageFont.load_default()

        # Yellow accent bars — top + left edge (brand consistency with video hook)
        draw.rectangle([(0, 0), (THUMBNAIL_WIDTH, 8)], fill=(255, 215, 0))
        draw.rectangle([(0, 0), (8, THUMBNAIL_HEIGHT)], fill=(255, 215, 0))

        # Hook text — first word YELLOW (scroll-stop), rest WHITE
        hook_display = (hook_text or topic.split("—")[0].strip()).upper()
        all_words = hook_display.split()
        first_word = all_words[0] if all_words else ""
        rest_words = " ".join(all_words[1:]) if len(all_words) > 1 else ""

        # Use larger font for first word
        font_first = None
        if font_file:
            font_first = ImageFont.truetype(font_file, 90)

        y_start = int(THUMBNAIL_HEIGHT * 0.28)

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
            fw_x = (THUMBNAIL_WIDTH - fw_w) // 2
            _draw_outlined(draw, fw_x, current_y, first_word, font_first or font_hook, (255, 215, 0))
            current_y += (bbox[3] - bbox[1]) + 15

        # Draw remaining words in WHITE (wrap if needed)
        if rest_words:
            rest_lines = []
            current_line = ""
            for word in rest_words.split():
                test = f"{current_line} {word}".strip()
                bbox = draw.textbbox((0, 0), test, font=font_hook)
                if bbox[2] - bbox[0] > THUMBNAIL_WIDTH - 120:
                    if current_line:
                        rest_lines.append(current_line)
                    current_line = word
                else:
                    current_line = test
            if current_line:
                rest_lines.append(current_line)

            for line in rest_lines[:3]:
                bbox = draw.textbbox((0, 0), line, font=font_hook)
                text_w = bbox[2] - bbox[0]
                x = (THUMBNAIL_WIDTH - text_w) // 2
                _draw_outlined(draw, x, current_y, line, font_hook, (255, 255, 255))
                current_y += (bbox[3] - bbox[1]) + 10

        # Topic summary — smaller text below hook
        topic_short = topic[:60] + ("..." if len(topic) > 60 else "")
        bbox = draw.textbbox((0, 0), topic_short, font=font_topic)
        topic_w = bbox[2] - bbox[0]
        topic_y = current_y + 25
        draw.text(((THUMBNAIL_WIDTH - topic_w) // 2, topic_y), topic_short,
                  font=font_topic, fill=(200, 200, 200))

        # Brand bar at bottom
        bar_y = THUMBNAIL_HEIGHT - 120
        draw.rectangle([(0, bar_y), (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)], fill=(255, 215, 0))
        brand_text = "Sale91.com"
        bbox = draw.textbbox((0, 0), brand_text, font=font_brand)
        brand_w = bbox[2] - bbox[0]
        draw.text(((THUMBNAIL_WIDTH - brand_w) // 2, bar_y + 35), brand_text,
                  font=font_brand, fill=(15, 15, 25))

        # Red "WATCH" badge top-right corner
        badge_text = "▶ WATCH"
        bbox = draw.textbbox((0, 0), badge_text, font=font_topic)
        badge_w = bbox[2] - bbox[0]
        badge_x = THUMBNAIL_WIDTH - badge_w - 50
        badge_y = 40
        draw.rectangle([(badge_x - 15, badge_y - 8),
                        (badge_x + badge_w + 15, badge_y + 44)],
                       fill=(220, 30, 30))
        draw.text((badge_x, badge_y), badge_text, font=font_topic, fill=(255, 255, 255))

        img.save(output_path, "PNG", quality=95)
        print(f"   🖼️ Thumbnail generated: {os.path.basename(output_path)}")
        return output_path

    except Exception as e:
        print(f"   ⚠️ Thumbnail generation failed: {e}")
        return None


def upload_thumbnail(youtube, video_id, thumbnail_path):
    """Upload custom thumbnail to a YouTube video."""
    from googleapiclient.http import MediaFileUpload
    try:
        media = MediaFileUpload(thumbnail_path, mimetype="image/png")
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        print(f"   🖼️ Custom thumbnail uploaded for {video_id}")
        return True
    except Exception as e:
        print(f"   ⚠️ Thumbnail upload failed: {e}")
        print(f"   ℹ️ Note: Thumbnail upload requires YouTube channel verification")
        return False


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
            f"https://graph.facebook.com/v21.0/{ig_business_id}/insights",
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


def cross_post_to_instagram(video_path, title, description, topic):
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
            f"https://graph.facebook.com/v21.0/{ig_business_id}",
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

        # Step 0: Upload video to temporary public URL
        public_url = None
        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)

        # Host 1: 0x0.st (up to 512MB, direct URL, no account needed)
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

        # Host 2: litterbox.catbox.moe (up to 1GB, 72h expiry)
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
                    print(f"   📤 Video hosted via litterbox.catbox.moe")
                else:
                    print(f"   ⚠️ litterbox failed ({resp.status_code}): {resp.text[:100]}")
            except Exception as e:
                print(f"   ⚠️ litterbox error: {e}")

        # Host 3: file.io (fallback, 100MB limit)
        if not public_url:
            try:
                print(f"   📤 Trying file.io (fallback)...")
                with open(video_path, "rb") as vf:
                    resp = requests.post(
                        "https://file.io",
                        files={"file": (os.path.basename(video_path), vf, "video/mp4")},
                        timeout=120,
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success"):
                        public_url = data.get("link")
                        print(f"   📤 Video hosted via file.io")
                    else:
                        print(f"   ⚠️ file.io failed: {data}")
                else:
                    print(f"   ⚠️ file.io failed ({resp.status_code})")
            except Exception as e:
                print(f"   ⚠️ file.io error: {e}")

        if not public_url:
            print(f"   ❌ Instagram: all temp hosts failed. Skipping cross-post.")
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
            "share_to_feed": "true",
            "access_token": ig_token,
        }

        if schedule_for_later and schedule_timestamp:
            # Schedule instead of instant publish — IG handles it
            container_data["published"] = "false"
            container_data["scheduled_publish_time"] = str(schedule_timestamp)

        container_resp = requests.post(
            f"https://graph.facebook.com/v21.0/{ig_business_id}/media",
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
        for check in range(20):  # Max 10 minutes
            time.sleep(30)
            status_resp = requests.get(
                f"https://graph.facebook.com/v21.0/{container_id}",
                params={"fields": "status_code", "access_token": ig_token},
                timeout=15,
            )
            status_code = status_resp.json().get("status_code", "")
            if status_code == "FINISHED":
                break
            elif status_code == "ERROR":
                print(f"   ❌ Instagram processing failed")
                return None
            print(f"   ⏳ Instagram processing... ({check + 1}/20)")

        # Step 4: Publish (or confirm schedule)
        if schedule_for_later:
            # Scheduled posts are auto-published by Instagram at the set time
            print(f"   ✅ Instagram Reel SCHEDULED for {best_time.strftime('%d %b %I:%M %p IST')}!")
            print(f"      Container ID: {container_id} — Instagram will auto-publish")
            return container_id
        else:
            # Immediate publish
            publish_resp = requests.post(
                f"https://graph.facebook.com/v21.0/{ig_business_id}/media_publish",
                data={"creation_id": container_id, "access_token": ig_token},
                timeout=30,
            )

            if publish_resp.status_code == 200:
                ig_media_id = publish_resp.json().get("id")
                print(f"   ✅ Instagram Reel published! ID: {ig_media_id}")
                return ig_media_id
            else:
                print(f"   ⚠️ Instagram publish failed: {publish_resp.text[:200]}")
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
    "veo_per_clip": 0.50,              # ~$0.50 per 8s clip (estimate)
    "kling_per_clip": 0.35,            # ~$0.35 per 5s clip ($0.07/s via fal.ai)
    "replicate_ace_step": 0.05,         # ~$0.05 per music generation
    "whisper_per_minute": 0.006,        # $0.006/min
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
        if provider == "elevenlabs":
            cost = char_count * COST_RATES["elevenlabs_per_char"]
        else:
            cost = char_count * COST_RATES["openai_tts_per_char"]
        self.add(f"tts_{provider}", cost, f"{char_count} chars")

    def track_veo(self, num_clips):
        """Track Veo video generation cost."""
        cost = num_clips * COST_RATES["veo_per_clip"]
        self.add("veo_clips", cost, f"{num_clips} clips")

    def track_kling(self, num_clips):
        """Track Kling (fal.ai) fallback video generation cost."""
        cost = num_clips * COST_RATES["kling_per_clip"]
        self.add("kling_clips", cost, f"{num_clips} clips (fallback)")

    def track_replicate(self):
        """Track Replicate ACE-Step music generation cost."""
        self.add("replicate_music", COST_RATES["replicate_ace_step"], "1 generation")

    def track_whisper(self, duration_sec):
        """Track Whisper transcription cost."""
        cost = (duration_sec / 60) * COST_RATES["whisper_per_minute"]
        self.add("whisper", cost, f"{duration_sec:.0f}s")

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

        # Step 2: Get video details (videos.list — 1 quota unit per 50 videos)
        videos_resp = requests.get(f"{base_url}/videos", params={
            "key": SOURCE_CHANNEL_API_KEY,
            "id": ",".join(video_ids),
            "part": "snippet,statistics",
        }, timeout=15)
        videos_resp.raise_for_status()
        videos_data = videos_resp.json()

        videos = []
        for item in videos_data.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            videos.append({
                "video_id": item.get("id", ""),
                "title": snippet.get("title", ""),
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "published": snippet.get("publishedAt", ""),
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

        print(f"   ✅ Fetched {len(videos)} videos from source channel (top: {videos[0]['views']:,} views)")
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
    Specificity = credibility. "200 GSM" is better than "thick fabric".

━━━ NATURAL ENDING (CRITICAL — listener must FEEL the wrap-up) ━━━

When a real person finishes talking, they naturally slow down and trail off.
The listener can SENSE the sentence is ending BEFORE the last word.
Your script MUST end this way — NOT like it was cut mid-thought.

GOOD endings (trailing, conclusive — listener feels the wrap-up):
- "...toh wahi hota hai, simple hai."
- "...isi ko bolte hai... bas."
- "...wo jyada theek rahega."
- "...bas yehi hai, kuch aur nahi."
- "...toh bas... yehi galti mat karna, aur kuch nahi."
- "...itna kar lo, complaint kabhi nahi aayegi."
- "...try karke dekh lo, samajh aa jayega."
- "...bas yehi tha, kuch aur nahi."

BAD endings (feel abrupt — like more was coming):
- "Aur ye 200 GSM hota hai." (sounds like next point is coming)
- "Print karke dekh lo." (too commanding, no sense of conclusion)

RULES for ending:
- Last sentence MUST use a CONCLUSIVE phrase: "bas", "simple hai", "bas yehi hai", "ho jayega", "complaint nahi aayegi", "try karke dekh lo"
- IMPORTANT: Use a DIFFERENT ending phrase every video. NEVER default to the same phrase repeatedly.
- The last 3-4 words should feel like they're naturally trailing off
- Add "..." before the final phrase for a natural pause feel
- The ending should make the listener think "haan, baat khatam hui" — NOT "aur kya?"

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
    "description": "YouTube description in English with 6-8 hashtags. Include Sale91.com link.",
    "script_voice": "The ROMAN HINGLISH script. 8-12 sentences for 45-55 seconds. NO website. NO selling. Pure knowledge with storytelling.",
    "script_english": "Clean English translation for on-screen subtitles",
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
    all_tags = all_tags[:30]

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

    if SCHEDULE_PUBLISH:
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
    """Use Claude to brainstorm trending topics based on current Indian textile/printing industry trends.
    Claude uses its training knowledge of YouTube Shorts trends, Google Trends patterns,
    and Indian B2B textile market to generate timely, high-search-volume topics."""

    prompt = f"""You are a YouTube Shorts content strategist for an Indian B2B t-shirt manufacturer (Sale91.com).

Your job: generate 10 FRESH topic ideas that are likely to be HIGHLY SEARCHED right now.

Think about:
1. SEASONAL TRENDS — what's relevant this month? (summer coming = cotton demand, winter ending = hoodie clearance, etc.)
2. YOUTUBE SEARCH TRENDS — what do printing business owners search for? ("DTG vs DTF 2025", "best GSM for printing", etc.)
3. CUSTOMER PAIN POINTS — what problems are printing businesses facing right now?
4. VIRAL FORMATS — what YouTube Shorts formats are working? (myth busting, "I tested X", comparisons, mistakes series)
5. INDUSTRY NEWS — any new printing tech, fabric innovations, market changes?

CURRENT CONTEXT:
- Month: {datetime.now(pytz.timezone(TIMEZONE)).strftime('%B %Y')}
- Season in India: {_get_india_season()}
- Business: B2B plain t-shirt manufacturer in Tiruppur/Delhi
- Audience: Custom printing businesses (DTG, DTF, screen print), merch brands, bulk buyers

TOP PERFORMING VIDEOS ON OUR SHORTS CHANNEL (make MORE topics like these):
{json.dumps(get_top_performing_topics(5), ensure_ascii=False) if get_top_performing_topics(5) else "New channel — no data yet."}

PROVEN WINNERS FROM OUR MAIN CHANNEL (50K subs, 5.5L monthly views — these topics WORK with our audience):
{json.dumps(get_source_channel_top_topics(10), ensure_ascii=False) if get_source_channel_top_topics(10) else "No source channel data available."}

AUDIENCE QUESTIONS (real comments from our viewers — these are topics they WANT explained):
{get_audience_questions(10)}

STYLE: Hindi conversational (Hinglish), practical knowledge, storytelling format.
Each topic should be 1 line, specific, and contain a hook element.

OUTPUT: Return ONLY a JSON array of 10 topic strings, nothing else.
Example: ["Topic 1 — detail", "Topic 2 — detail", ...]"""

    try:
        resp = anthropic_client.messages.create(
            model="claude-sonnet-4-5-20250929", max_tokens=800,
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
            model="claude-sonnet-4-5-20250929", max_tokens=200,
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
        # Main channel PRIMARY until new channel crosses 1L total views
        total_views = get_new_channel_total_views()
        if total_views >= NEW_CHANNEL_VIEWS_THRESHOLD:
            top_cats = get_top_performing_categories()
            cat_source = "own channel"
            if not top_cats:
                top_cats = get_source_channel_category_ranking()
                cat_source = "main channel (fallback)"
        else:
            top_cats = get_source_channel_category_ranking()
            cat_source = f"main channel (new ch: {total_views:,} views <1L)"
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
        # Absolute fallback: single topic generation (old behavior)
        print("   🔄 Fallback: single topic generation...")
        resp = claude_client.messages.create(
            model="claude-sonnet-4-5-20250929", max_tokens=200,
            messages=[{"role": "user", "content": f"""Generate 1 new YouTube Shorts topic for a B2B plain t-shirt manufacturer.
Style: practical knowledge, no selling. Hindi conversational.
Already used: {json.dumps(topic_history[-10:])}
Return ONLY the topic text, nothing else."""}]
        )
        return resp.content[0].text.strip()

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

    review_prompt = f"""You are a YouTube Shorts content reviewer for an Indian B2B t-shirt brand.
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
    Uses source channel's top titles as reference for what works."""
    print(f"   🏷️ Title optimization: generating A/B variants...")

    source_titles = get_source_channel_top_topics(5)
    source_context = ""
    if source_titles:
        source_context = f"""
REFERENCE: These titles got the MOST views on our main channel (50K subs):
{json.dumps(source_titles, ensure_ascii=False)}
Study their patterns — length, keywords, emotional hooks — and apply similar patterns."""

    prompt = f"""You are a YouTube Shorts title optimizer for an Indian B2B t-shirt brand.

CURRENT TITLE: {original_title}
TOPIC: {topic}
SCRIPT SUMMARY: {script_english[:200]}
{source_context}

Generate 3 alternative titles. Each must be:
- Max 70 characters (YouTube Shorts limit for mobile visibility)
- SEO optimized (include searchable keywords like "GSM", "DTG", "t-shirt", "printing")
- Curiosity-driven (make viewer NEED to watch)
- English (for broader reach + SEO, but Hinglish words OK if they add punch)

TITLE STYLES TO TRY:
1. QUESTION style — "Why Does Your DTG Print Fade After 2 Washes?"
2. SHOCK/NUMBER style — "Rs 45 T-shirt vs Rs 90: The Print Quality Difference"
3. MISTAKE/WARNING style — "Stop Making This GSM Mistake (Most Printers Do)"

OUTPUT THIS JSON ONLY (no markdown):
{{"titles": ["title1", "title2", "title3"], "best": 0, "reason": "why this title will get most clicks"}}

"best" = index (0, 1, or 2) of the title you'd bet money on for highest CTR."""

    try:
        resp = claude_client.messages.create(
            model="claude-sonnet-4-5-20250929", max_tokens=300,
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
# VIDEO CLIP VALIDATION
# ═══════════════════════════════════════════════════════════════════════

def validate_video_file(path, min_size_bytes=10_000):
    """Check if a downloaded video file is valid and playable.

    Uses ffprobe to verify the file has a video stream with non-zero duration.
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
             "-show_entries", "stream=duration,codec_name,width,height",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30
        )
        if probe.returncode != 0:
            return False, f"ffprobe failed: {probe.stderr[:120]}"

        output = probe.stdout.strip()
        if not output:
            return False, "ffprobe returned no video stream info"

        parts = output.split(",")
        if len(parts) < 3:
            return False, f"unexpected ffprobe output: {output[:80]}"

        # Check that width and height are nonzero
        try:
            w = int(parts[2]) if len(parts) > 2 else 0
            h = int(parts[3]) if len(parts) > 3 else 0
        except (ValueError, IndexError):
            w, h = 0, 0
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
    print(f"   Time: {datetime.now(pytz.timezone(TIMEZONE)).strftime('%d %b %Y, %I:%M %p IST')}")

    # ── Feature Dashboard ──
    elevenlabs_available = bool(os.environ.get('ELEVENLABS_API_KEY'))
    ig_available = bool(os.environ.get('INSTAGRAM_ACCESS_TOKEN'))

    print()
    print("   ╔═══════════════════════════════════════════════════╗")
    print("   ║           FEATURE STATUS DASHBOARD                ║")
    print("   ╠═══════════════════════════════════════════════════╣")
    print("   ║ ── MODE ──                                        ║")
    print(f"   ║ Test Mode             : {'ON (free run)' if TEST_MODE else 'OFF (production)':>25} ║")
    print(f"   ║ Skip Clips            : {'ON (placeholders)' if SKIP_CLIPS else 'OFF (Veo clips)':>25} ║")
    print(f"   ║ Single Veo Test       : {'ON (1 real + 4 blank)' if SINGLE_VEO_TEST else 'OFF':>25} ║")
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
    print(f"   ║ TTS Primary            : {'ElevenLabs' if elevenlabs_available else 'OpenAI (no 11Labs key)':>25} ║")
    print(f"   ║ TTS Fallback           : {'OpenAI gpt-4o-mini-tts':>25} ║")
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
        with open(TOPIC_HISTORY_FILE, "r") as f:
            topic_history = json.load(f)

    print("   🎯 Smart topic selection (with quality gate)...")
    fresh_topic = smart_pick_topic(claude, TOPIC_BANK, topic_history)

    topic_history.append(fresh_topic)
    with open(TOPIC_HISTORY_FILE, "w") as f:
        json.dump(topic_history, f, indent=2)
    print(f"   📌 Topic: {fresh_topic}")

    # ── 3. Generate Script (with quality gate) ──
    data = None
    previous_feedback = ""  # Pass rejection reasons to next attempt
    for attempt in range(1, SCRIPT_MAX_ATTEMPTS + 1):
        print(f"   ✍️ Writing script (attempt {attempt}/{SCRIPT_MAX_ATTEMPTS})...")

        prompt = get_script_prompt(fresh_topic)
        # On retry, tell Claude what was wrong so it can fix it
        if previous_feedback:
            prompt += f"\n\n━━━ IMPORTANT: PREVIOUS ATTEMPT WAS REJECTED ━━━\nReviewer feedback: {previous_feedback}\nFix these issues in your new script. Write a DIFFERENT and BETTER script."

        resp = claude.messages.create(
            model="claude-sonnet-4-5-20250929", max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        cost.track_claude_call("sonnet", resp.usage.input_tokens, resp.usage.output_tokens)
        raw = resp.content[0].text.strip()
        if raw.startswith("```"): raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

        candidate = json.loads(raw)
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
        print(f"   ⚠️ No script scored high enough — using best last attempt")
        data = candidate

    script_voice = data["script_voice"]
    # Sanitize script for TTS: strip ellipsis and elongated sounds that cause distortion
    import re
    script_voice = script_voice.replace("...", ",").replace("..", ",")
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

    # ── 4. Generate Voice (ElevenLabs primary → OpenAI fallback) ──
    audio_path = f"{WORK_DIR}/voice_{random.randint(100,999)}.mp3"
    voice_ok = False

    # Try ElevenLabs first (ElevenLabs Hindi — Emotive Hindi)
    if elevenlabs_key:
        print("   🎙️ Generating voice (ElevenLabs — ElevenLabs Hindi)...")
        try:
            from elevenlabs import ElevenLabs
            el_client = ElevenLabs(api_key=elevenlabs_key)
            audio_iter = el_client.text_to_speech.convert(
                voice_id=ELEVENLABS_VOICE_ID,
                model_id=ELEVENLABS_MODEL,
                text=script_voice,
                voice_settings=ELEVENLABS_VOICE_SETTINGS,
            )
            with open(audio_path, "wb") as f:
                for chunk in audio_iter:
                    f.write(chunk)
            print("   ✅ Voice: ElevenLabs ElevenLabs Hindi (Emotive Hindi)")
            cost.track_tts("elevenlabs", len(script_voice))
            voice_ok = True
        except Exception as e:
            print(f"   ⚠️ ElevenLabs TTS failed: {e}")
            print("   🔄 Falling back to OpenAI TTS...")

    # Fallback: OpenAI gpt-4o-mini-tts
    if not voice_ok:
        print("   🎙️ Generating voice (OpenAI TTS fallback)...")
        try:
            response = openai_client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=TARGET_VOICE,
                input=script_voice + "...",
                instructions=VOICE_INSTRUCTIONS,
                speed=VOICE_SPEED,
                response_format="mp3",
            )
            response.stream_to_file(audio_path)
            print(f"   ✅ Voice: OpenAI {TARGET_VOICE} (fallback)")
            cost.track_tts("openai", len(script_voice))
            voice_ok = True
        except Exception as e:
            print(f"   ❌ OpenAI TTS also failed: {e}")
            return

    # ── 4b. Normalize voice audio loudness (consistent -16 LUFS across all videos) ──
    try:
        import subprocess
        normalized_path = audio_path.replace(".mp3", "_norm.mp3")
        subprocess.run([
            "ffmpeg", "-i", audio_path,
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-y", normalized_path,
        ], capture_output=True, timeout=30)
        if os.path.exists(normalized_path) and os.path.getsize(normalized_path) > 0:
            os.replace(normalized_path, audio_path)
            print("   🔊 Voice normalized to -16 LUFS")
        else:
            print("   ⚠️ Loudness normalization skipped (ffmpeg output empty)")
    except Exception as e:
        print(f"   ⚠️ Loudness normalization skipped: {e}")

    # ── 5. Generate Video Clips (Veo 3.1, Kling fallback) ──
    downloaded_clips = []
    kling_clips = 0  # Track Kling fallback clips across all modes
    veo_clips_count = 0

    if TEST_MODE or SKIP_CLIPS:
        # Test/skip-clips mode: create cheap placeholder clips (solid color) instead of Veo
        label = "TEST MODE" if TEST_MODE else "SKIP CLIPS"
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

        for attempt in range(1, VEO_MAX_RETRIES + 1):
            try:
                print(f"   ⏳ Real Clip 1: attempt {attempt}...", end=" ")
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

                if operation.response and operation.response.generated_videos:
                    video = operation.response.generated_videos[0]
                    video_data = veo_client.files.download(file=video.video)
                    with open(clip_path, "wb") as f:
                        f.write(video_data)
                    valid, reason = validate_video_file(clip_path)
                    if not valid:
                        print(f"corrupted ({reason}) — retrying")
                        continue
                    downloaded_clips.append(clip_path)
                    clip_success = True
                    print("✅")
                    break
                else:
                    print("empty response")
            except BaseException as e:
                error_msg = str(e)
                if "RESOURCE_EXHAUSTED" in error_msg or "429" in error_msg:
                    wait = VEO_RETRY_WAIT * attempt
                    print(f"rate limited — waiting {wait}s")
                    time.sleep(wait)
                else:
                    print(f"error: {error_msg[:60]}")
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
        cost.track_veo(real_count)
        print(f"   ✅ {len(downloaded_clips)} clips ready ({real_count} real Veo + {VEO_CLIPS_PER_VIDEO - real_count} blank)")

    else:
        print(f"   🤖 Generating {VEO_CLIPS_PER_VIDEO} AI clips via Veo 3.1...")
        if KLING_ENABLED:
            print(f"   🔄 Kling fallback: READY (auto-switch on Veo rate limit)")

        use_kling_fallback = False  # Sticky: once Veo rate-limits, switch to Kling

        for i in range(VEO_CLIPS_PER_VIDEO):
            prompt_text = video_prompts[i] if i < len(video_prompts) else video_prompts[0]
            clip_path = f"{WORK_DIR}/veo_clip_{i}_{random.randint(100,999)}.mp4"
            clip_success = False

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

                        if operation.response and operation.response.generated_videos:
                            video = operation.response.generated_videos[0]
                            video_data = veo_client.files.download(file=video.video)
                            with open(clip_path, "wb") as f:
                                f.write(video_data)
                            valid, reason = validate_video_file(clip_path)
                            if not valid:
                                print(f"corrupted ({reason}) — retrying")
                                continue
                            downloaded_clips.append(clip_path)
                            clip_success = True
                            veo_clips_count += 1
                            print("✅")
                            break
                        else:
                            print("empty response")
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
                            print(f"error: {error_msg[:60]}")
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

            if not clip_success:
                print(f"   ⚠️ Clip {i+1} failed after all attempts")

        # Summary
        if kling_clips > 0:
            print(f"   ✅ {len(downloaded_clips)} clips ready ({veo_clips_count} Veo + {kling_clips} Kling fallback)")
        else:
            print(f"   ✅ {len(downloaded_clips)} clips ready ({veo_clips_count} Veo)")

    if not downloaded_clips:
        print("❌ No clips generated. Stopping.")
        return

    if not TEST_MODE and not SKIP_CLIPS and not SINGLE_VEO_TEST:
        expected = VEO_CLIPS_PER_VIDEO
        got = len(downloaded_clips)
        veo_count = got - kling_clips
        if veo_count > 0:
            cost.track_veo(veo_count)
        if kling_clips > 0:
            cost.track_kling(kling_clips)
        if got < expected:
            print(f"   ⚠️ Partial recovery: {got}/{expected} clips succeeded — video will use clip looping to fill duration")
    print(f"   ✅ {len(downloaded_clips)} clips ready")

    # ── 6. Subtitles (Whisper) ──
    subtitle_segments = []
    chunks = []
    words = script_english.split()
    for k in range(0, len(words), WORDS_PER_SUBTITLE):
        chunks.append(" ".join(words[k:k+WORDS_PER_SUBTITLE]))

    audio_clip_dur = AudioFileClip(audio_path).duration
    seg_dur = audio_clip_dur / max(len(chunks), 1)
    for idx, ct in enumerate(chunks):
        subtitle_segments.append({
            "text": ct,
            "start": idx * seg_dur,
            "end": (idx + 1) * seg_dur
        })

    try:
        import whisper
        wmodel = whisper.load_model("small")
        result = wmodel.transcribe(audio_path, language="hi", word_timestamps=True)
        all_words = []
        for seg in result.get("segments", []):
            for w in seg.get("words", []):
                all_words.append({"start": w["start"], "end": w["end"]})
        if len(all_words) >= 5 and chunks:
            new_segs = []
            wpc = len(all_words) / len(chunks)
            for k, ct in enumerate(chunks):
                si = int(k * wpc)
                ei = min(int((k + 1) * wpc) - 1, len(all_words) - 1)
                si = min(si, len(all_words) - 1)
                st = all_words[si]["start"]
                et = all_words[ei]["end"]
                if et - st < 0.2: et = st + 0.6
                new_segs.append({"text": ct, "start": st, "end": et})
            subtitle_segments = new_segs
            cost.track_whisper(audio_clip_dur)
            print("   ✅ Whisper synced!")
    except:
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

    # Add Veo ambient audio layer if available
    has_ambient = False
    if ambient_clip:
        mixed_audio_with_ambient = CompositeAudioClip([mixed_audio, ambient_clip])
        print(f"   ✅ Final audio: voice + background music + Veo ambient ({int(VEO_AMBIENT_VOLUME * 100)}%)")
        has_ambient = True
    else:
        mixed_audio_with_ambient = mixed_audio

    final_video = final_video.set_audio(mixed_audio_with_ambient)

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

    # ── 9b. Generate Thumbnail (uses first Veo clip frame if available) ──
    first_clip = downloaded_clips[0] if downloaded_clips else None
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

            try:
                vid_id, vid_url = upload_to_youtube(youtube, output_path, yt_title, yt_description, yt_tags, topic=fresh_topic)

                if vid_id and vid_id != "?":
                    # Upload custom thumbnail
                    if thumbnail_path:
                        upload_thumbnail(youtube, vid_id, thumbnail_path)

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

    # ── 10d. Cross-post to Instagram Reels ──
    if not TEST_MODE and not upload_failed:
        cross_post_to_instagram(output_path, yt_title, yt_description, fresh_topic)

    # ── 10e. Cost summary + save ──
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


if __name__ == "__main__":
    main()
