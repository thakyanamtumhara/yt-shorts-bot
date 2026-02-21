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

# Hook Text
ADD_HOOK_TEXT = True
HOOK_DURATION = 1.5

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
CLIP_HISTORY_FILE = f"{WORK_DIR}/clip_history.json"
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

# Engagement Feedback Loop — check video performance after 48h
ENGAGEMENT_FILE = "engagement_history.json"  # In repo root for git tracking
ENGAGEMENT_CHECK_DELAY_HOURS = 48

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

        # Yellow accent bar at top
        draw.rectangle([(0, 0), (THUMBNAIL_WIDTH, 8)], fill=(255, 215, 0))

        # Hook text — big, bold, white with yellow highlight words
        hook_display = (hook_text or topic.split("—")[0].strip()).upper()
        # Wrap long text
        hook_words = hook_display.split()
        lines = []
        current_line = ""
        for word in hook_words:
            test = f"{current_line} {word}".strip()
            bbox = draw.textbbox((0, 0), test, font=font_hook)
            if bbox[2] - bbox[0] > THUMBNAIL_WIDTH - 120:
                if current_line:
                    lines.append(current_line)
                current_line = word
            else:
                current_line = test
        if current_line:
            lines.append(current_line)

        # Draw hook text centered, starting at ~35% height
        y_start = int(THUMBNAIL_HEIGHT * 0.30)
        line_height = 90
        for i, line in enumerate(lines[:4]):  # Max 4 lines
            bbox = draw.textbbox((0, 0), line, font=font_hook)
            text_w = bbox[2] - bbox[0]
            x = (THUMBNAIL_WIDTH - text_w) // 2
            y = y_start + i * line_height
            # Black outline
            for dx in [-3, -2, 0, 2, 3]:
                for dy in [-3, -2, 0, 2, 3]:
                    draw.text((x + dx, y + dy), line, font=font_hook, fill=(0, 0, 0))
            # White text
            draw.text((x, y), line, font=font_hook, fill=(255, 255, 255))

        # Topic summary — smaller text below hook
        topic_short = topic[:60] + ("..." if len(topic) > 60 else "")
        bbox = draw.textbbox((0, 0), topic_short, font=font_topic)
        topic_w = bbox[2] - bbox[0]
        topic_y = y_start + len(lines[:4]) * line_height + 30
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

def cross_post_to_instagram(video_path, title, description, topic):
    """Cross-post the video to Instagram Reels via the Instagram Graph API.
    Requires INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_BUSINESS_ID env vars.
    Uses a 2-step flow: create media container → publish."""
    if not CROSS_POST_INSTAGRAM:
        return None

    ig_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    ig_business_id = os.environ.get("INSTAGRAM_BUSINESS_ID")

    if not ig_token or not ig_business_id:
        print("   ℹ️ Instagram cross-post skipped (no INSTAGRAM_ACCESS_TOKEN/INSTAGRAM_BUSINESS_ID)")
        return None

    try:
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

        # Step 1: Create media container (Reels)
        # Build caption: title + hashtags
        topic_hashtags = get_topic_hashtags(topic)
        ig_caption = f"{title}\n\n{description.split(chr(10))[0]}\n\n{' '.join(topic_hashtags[:10])}\n\n📦 Order: Sale91.com"

        container_resp = requests.post(
            f"https://graph.facebook.com/v21.0/{ig_business_id}/media",
            data={
                "media_type": "REELS",
                "video_url": public_url,
                "caption": ig_caption[:2200],  # IG caption limit
                "share_to_feed": "true",
                "access_token": ig_token,
            },
            timeout=30,
        )

        if container_resp.status_code != 200:
            print(f"   ⚠️ Instagram container creation failed: {container_resp.text[:200]}")
            return None

        container_id = container_resp.json().get("id")
        print(f"   📦 Instagram container created: {container_id}")

        # Step 2: Wait for processing (Instagram processes video async)
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

        # Step 3: Publish
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


# ═══════════════════════════════════════════════════════════════════════
# SCRIPT PROMPT
# ═══════════════════════════════════════════════════════════════════════

def get_script_prompt(topic):
    return f"""
You are writing a YouTube Short voiceover script. The video is from Sale91.com
(a B2B plain t-shirt manufacturer) but the script must NOT sell anything.
You also need to describe 5 AI video clips that will play during the Short.

BUSINESS CONTEXT (use this knowledge, but do NOT promote the brand in voice):
{BUSINESS_CONTEXT}

TOPIC: {topic}

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

4. NATURAL ENDING (1-2 sentences) — Trail off conclusively:
   - "...toh bas itna yaad rakhna, fark dikh jayega."
   - "...simple hai, pehle check kar lo, phir order karo."
   - "...bas yehi galti mat karna, aur kuch nahi."

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
6. SIGNATURE ENDINGS — "usi ko... bolte hai", "bas...hota hai",
   "wo jyada theek rahega", "simple hai", "fark dikh jayega"
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
- "...bas itna samajh lo, fark dikh jayega."
- "...toh wahi hota hai, simple hai."
- "...isi ko bolte hai... bas."
- "...wo jyada theek rahega."
- "...bas yehi hai, kuch aur nahi."
- "...toh bas... yehi galti mat karna, aur kuch nahi."

BAD endings (feel abrupt — like more was coming):
- "Aur ye 200 GSM hota hai." (sounds like next point is coming)
- "Print karke dekh lo." (too commanding, no sense of conclusion)

RULES for ending:
- Last sentence MUST use a CONCLUSIVE phrase: "bas", "simple hai", "bas yehi hai", "ho jayega", "fark dikh jayega"
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
    Falls back to the A/B rotation schedule if analytics are unavailable."""

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

        # Use best slot if it has significantly more views (>20% better than average)
        all_avgs = [d["avg_views"] for d in analytics.values()]
        overall_avg = sum(all_avgs) / len(all_avgs) if all_avgs else 0

        if best_avg > overall_avg * 1.2:
            print(f"   🏆 Analytics-optimized: using {best_label} (best performer)")
            # Find the slot tuple
            for hour, minute, label in PUBLISH_SLOTS:
                if label == best_label:
                    return hour, minute, label
        else:
            print(f"   📊 No clear winner yet — continuing A/B rotation")

    # Fallback: A/B rotation schedule
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
            from google.auth.transport.requests import Request
            try:
                creds.refresh(Request())
                print("   🔄 YouTube token refreshed!")
                with open(TOKEN_FILE, "w") as f:
                    f.write(creds.to_json())
            except Exception as e:
                print(f"   ❌ Token refresh failed: {e}")
                return None
        else:
            print("   ❌ No valid YouTube token. Run Colab Block 4 once to get token.")
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

TOP PERFORMING VIDEOS (make MORE topics like these — they got the most views):
{json.dumps(get_top_performing_topics(5), ensure_ascii=False) if get_top_performing_topics(5) else "No engagement data yet — generate based on search trends."}

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
        # Pick from bank, but validate — ensure it's timely
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


def review_script(claude_client, script_voice, script_english, topic):
    """Claude reviews its own script like a human content creator would.
    Returns (approved: bool, feedback: str)."""

    review_prompt = f"""You are a YouTube Shorts content reviewer for an Indian B2B t-shirt brand.
Review this script and decide: is this GOOD ENOUGH to publish?

Remember: this is B2B educational content for printing businesses, NOT entertainment/clickbait.
A factory owner explaining something practical IS valuable — don't expect Bollywood drama.

TOPIC: {topic}
HINDI SCRIPT: {script_voice}
ENGLISH: {script_english}

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

OUTPUT THIS JSON ONLY (no markdown):
{{"approved": true/false, "total_score": sum_of_5_scores, "weakest": "which area is weakest", "feedback": "1-2 sentences on what's wrong (if rejected) or what's great (if approved)"}}

RULES:
- Approve if total_score >= 30 (out of 50)
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


# ═══════════════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("🚀 SALE91.COM — Daily YouTube Short Generator")
    if TEST_MODE:
        print("   🧪 TEST MODE — no Veo clips, no YouTube upload (free run)")
    elif SKIP_CLIPS:
        print("   🧪 SKIP CLIPS — placeholder clips, but will upload to YouTube")
    print(f"   Time: {datetime.now(pytz.timezone(TIMEZONE)).strftime('%d %b %Y, %I:%M %p IST')}")
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

    from google import genai
    from google.genai import types
    veo_client = genai.Client(api_key=google_key)

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

        # Quality gate: Claude reviews its own script
        approved, score, weakest, feedback = review_script(claude, script_voice, script_english, fresh_topic)

        if approved:
            print(f"   ✅ Script APPROVED (score: {score}/50) — {feedback}")
            data = candidate
            break
        else:
            print(f"   ❌ Script REJECTED (score: {score}/50, weak: {weakest})")
            print(f"      Reason: {feedback}")
            previous_feedback = f"Score {score}/50. Weakest: {weakest}. {feedback}"
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
    yt_title = data["title"]
    yt_description = data["description"]
    yt_tags = data.get("tags", [])
    music_mood = data.get("music_mood", "calm")
    hook_text_from_claude = data.get("hook_text", "")
    video_prompts = [data.get(f"video_prompt_{i}","") for i in range(1, VEO_CLIPS_PER_VIDEO + 1)]
    # Filter out empty prompts, keep at least the first 3
    video_prompts = [p for p in video_prompts if p.strip()] or video_prompts[:3]

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

    # ── 5. Generate Video Clips (Veo 3.1) ──
    downloaded_clips = []
    VEO_MAX_RETRIES = 5
    VEO_RETRY_WAIT = 90

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

    if not TEST_MODE and not SKIP_CLIPS:
        print(f"   🤖 Generating {VEO_CLIPS_PER_VIDEO} AI clips via Veo 3.1...")
        for i in range(VEO_CLIPS_PER_VIDEO):
            if i > 0 and i % 2 == 0:
                print(f"   ⏸️ RPM limit — waiting 60s...")
                time.sleep(60)

            prompt_text = video_prompts[i] if i < len(video_prompts) else video_prompts[0]
            clip_path = f"{WORK_DIR}/veo_clip_{i}_{random.randint(100,999)}.mp4"
            clip_success = False

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
                            generate_audio=False,  # Skip audio — we add our own voice + music
                        ),
                    )
                    while not operation.done:
                        time.sleep(10)
                        operation = veo_client.operations.get(operation)

                    if operation.response and operation.response.generated_videos:
                        video = operation.response.generated_videos[0]
                        video_data = veo_client.files.download(file=video.video)
                        with open(clip_path, "wb") as f:
                            f.write(video_data)
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

            if not clip_success:
                print(f"   ⚠️ Clip {i+1} failed after {VEO_MAX_RETRIES} attempts")

    if not downloaded_clips:
        print("❌ No clips generated. Stopping.")
        return

    if not TEST_MODE and not SKIP_CLIPS:
        cost.track_veo(len(downloaded_clips))
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
    for fname in downloaded_clips:
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
            print(f"   Failed to load {fname}: {e}")

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
        while accumulated < total_duration:
            clip = video_objects[idx % len(video_objects)]
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
            except: pass

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
        except: pass

    # Hook — curiosity-driven text from Claude (or fallback to topic words)
    if ADD_HOOK_TEXT:
        try:
            hook_line = hook_text_from_claude.strip().upper() if hook_text_from_claude else " ".join(fresh_topic.split()[:4]).upper()
            # Truncate to max 4 words (punchier for Shorts scroll-stop)
            hook_words = hook_line.split()[:4]
            hook_line = " ".join(hook_words)

            ht = TextClip(hook_line, fontsize=56, font=SUBTITLE_FONT, color="white",
                stroke_color="black", stroke_width=3, method='caption',
                size=(VIDEO_WIDTH - 180, None), align='center')
            ht_w, ht_h = ht.size
            hbg = ColorClip(size=(ht_w + 40, ht_h + 30), color=(0,0,0)).set_opacity(0.80)
            hbg = hbg.set_position(((VIDEO_WIDTH-ht_w-40)//2, int(VIDEO_HEIGHT*0.22))).set_start(0).set_duration(HOOK_DURATION).crossfadeout(0.3)
            ht = ht.set_position(((VIDEO_WIDTH-ht_w)//2, int(VIDEO_HEIGHT*0.22)+15)).set_start(0).set_duration(HOOK_DURATION).crossfadeout(0.3)
            layers.extend([hbg, ht])
        except: pass

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
        except: pass

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
