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
  HF_API_KEY    — Hugging Face token for AI background music generation (free)
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

# Script quality gate: Claude reviews its own script before proceeding
SCRIPT_MAX_ATTEMPTS = 3

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
- Natural thinking pauses and fillers — "umm", "dekho", "matlab"
- Confident, knowledgeable, casual — NOT formal, NOT scripted, NOT like a narrator
- Medium pace, relaxed delivery
- Trail off naturally at the end of sentences"""
VIDEO_WIDTH, VIDEO_HEIGHT = 1080, 1920
FPS = 30
VEO_CLIPS_PER_VIDEO = 3
VEO_MODEL = "veo-3.1-fast-generate-preview"
VEO_ASPECT_RATIO = "9:16"
VEO_DURATION = 8

# Subtitles
ADD_SUBTITLES = True
SUBTITLE_FONT = "Noto-Sans-Bold"
SUBTITLE_FONTSIZE = 58
SUBTITLE_COLOR = "white"
SUBTITLE_STROKE = "black"
SUBTITLE_STROKE_W = 2
SUBTITLE_BG_COLOR = (0, 0, 0)
SUBTITLE_BG_OPACITY = 0.7
SUBTITLE_BG_PADDING = 16
WORDS_PER_SUBTITLE = 4

# Watermark
ADD_WATERMARK = True
WATERMARK_TEXT = "Sale91.com"
WATERMARK_FONTSIZE = 28
WATERMARK_COLOR = "white"
WATERMARK_OPACITY = 0.8

# Background Music
ADD_BG_MUSIC = True
BG_MUSIC_FOLDER = f"{WORK_DIR}/bg_music"
BG_MUSIC_VOLUME = 0.08

# Veo Ambient Audio (keep Veo's generated scene sounds at low volume)
VEO_AMBIENT_VOLUME = 0.03

# Hook Text
ADD_HOOK_TEXT = True
HOOK_DURATION = 2.5

# Transitions
CLIP_FADE_DURATION = 0.3

# CTA
ADD_CTA_OVERLAY = True
CTA_TEXT = "Sale91.com"

# YouTube
SCHEDULE_PUBLISH = True
PUBLISH_HOUR = 19
PUBLISH_MINUTE = 30
TIMEZONE = "Asia/Kolkata"
UPLOAD_AS_SHORT = True

# Files
TOPIC_HISTORY_FILE = "topic_history.json"  # In repo root for git tracking
CLIP_HISTORY_FILE = f"{WORK_DIR}/clip_history.json"
CLIENT_SECRETS_FILE = f"{WORK_DIR}/client_secret.json"
TOKEN_FILE = f"{WORK_DIR}/youtube_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# ╔══════════════════════════════════════════════════════════════════════╗
# ║                   TOPIC BANK                                         ║
# ╚══════════════════════════════════════════════════════════════════════╝

TOPIC_BANK = [
    "GSM bas fabric ka weight hota hai — kaise check karein ghar pe",
    "180 GSM aur 220 GSM mein kya farq hai — printing ke baad dikta hai",
    "Ring-spun aur open-end yarn — quality mein zameen aasmaan ka fark hai",
    "Biowash ka matlab acchi quality — roa nahi aata fabric mein",
    "Normal 2% shrinkage hota hai — ye common hai kuch nahi kar sakte",
    "Rs 55 wali aur Rs 90 wali tshirt mein quality quality ka farq hota hai",
    "Client ne return kiya? Pre-shrunk nahi tha shayad",
    "Pehla order dene se pehle 5 cheezein confirm kar lo supplier se",
    "10 piece se merch brand start ho sakta hai — high MOQ ki zaroorat nahi",
    "DTG DTF Screen — har method ke liye alag blank tshirt theek rehta hai",
    "Oversized tshirt ka trend hai — GSM aur fit sahi choose kar lo",
    "White tshirt pe dark print — fabric quality matter karti hai",
    "Ek tshirt ki actual cost kya hoti hai — fabric dyeing stitching biowash",
    "Acid wash oversized blank — printing business ke liye next trend hai",
    "Polo tshirt blanks — corporate orders ke liye best quality kaise pehchano",
    "430 GSM hoodie blank — winter mein demand sabse zyada isi ki hoti hai",
    "Combed aur carded cotton — touch karke fark samajh aa jayega",
    "Naya printing business start karna hai? 3 galtiyan mat kariyega",
    "Biowash aur pre-shrunk mein fark hai — dono zaroori hain",
    "Collar 5 wash mein loose ho jaata hai? Collar ribbing ka scene samjho",
    "Side seam aur tubular tshirt — printing ke liye kaunsa better hai",
    "Cotton tshirt mein pilling kyu hoti hai — yarn quality se connection hai",
    "Tshirt ka color 2 wash mein fade ho gaya? Dyeing quality ka issue hai",
]

DEFAULT_TAGS = [
    "plain tshirt", "blank tshirt", "tshirt printing",
    "DTG printing", "DTF printing", "screen printing",
    "t-shirt manufacturer India", "wholesale tshirt",
    "cotton tshirt", "bulk tshirt", "Sale91",
    "printing business", "tshirt supplier"
]


# ═══════════════════════════════════════════════════════════════════════
# SCRIPT PROMPT
# ═══════════════════════════════════════════════════════════════════════

def get_script_prompt(topic):
    return f"""
You are writing a YouTube Short voiceover script. The video is from Sale91.com
(a B2B plain t-shirt manufacturer) but the script must NOT sell anything.
You also need to describe 3 AI video clips that will play during the Short.

BUSINESS CONTEXT (use this knowledge, but do NOT promote the brand in voice):
{BUSINESS_CONTEXT}

TOPIC: {topic}

━━━ CRITICAL: SPEAKING STYLE ━━━

You are writing EXACTLY like a real Indian textile manufacturer talks — but using
MICRO-STORYTELLING to hook the viewer in the first 2 seconds.

STRUCTURE (follow this EVERY time):
1. HOOK (first sentence) — Start with a REAL STORY, shocking fact, or customer incident:
   - "Ek customer aaya tha, bola print dhul gaya 2 wash mein..."
   - "Pehle main bhi yahi galti karta tha..."
   - "Ek baar ek banda 500 piece ka order cancel karwa diya..."
   - "Log sochte hai GSM jyada toh better... galat hai"
   - "Maine ek tshirt 2 saal pehni, ek 2 hafte mein kharab..."
2. PROBLEM/CURIOSITY (middle) — Kya galti hui ya kya fark hai:
   - "Problem ye thi ki usne check hi nahi kiya..."
   - "Fark ye hai ki ek mein combed tha, ek mein nahi..."
3. KNOWLEDGE + NATURAL ENDING (last sentence) — Seedha gyaan, trailing off:
   - "...toh bas itna yaad rakhna, fark dikh jayega."
   - "...simple hai, pehle check kar lo, phir order karo."

Study these REAL examples from the actual business owner — match this tone PERFECTLY:

EXAMPLE 1 (GSM explanation):
"GSM bas fabric ka weight hota hai. Jyada GSM matlab mota fabric, kam GSM
matlab patla. Basically kisi bhi kapde ko 1 square meter mein cut karke uska
weight kar doge toh jo bhi uska gram mein weight aayega, usi ko GSM bolte hai."

EXAMPLE 2 (Biowash):
"Biowash ka matlab acchi quality ki tshirt. Biowash wala jo fabric hota hai,
usmein roa nahi aata hai."

EXAMPLE 3 (Shrinkage):
"Normal 2% shrinkage hota hai tshirt mein, koi bhi tshirt lelo aap, lekin ye
non noticeable hai, pata nahi lagta. Ye common hai, kuch bhi nahi kar sakte."

EXAMPLE 4 (Recommendation):
"Sample leke ek baar try kar lo print karke, wo jyada theek rahega."

EXAMPLE 5 (Storytelling — THIS is the new style to follow):
"Ek customer aaya tha, bola collar loose ho gaya 5 wash mein. Maine bola collar
ribbing ka type check kiya tha? Nahi kiya tha. Dekho... agar 1x1 rib hai toh
tight rahega, flat knit hai toh loose hoga... bas itna check kar lo, problem
nahi aayegi."

━━━ RULES EXTRACTED FROM THESE EXAMPLES ━━━

1. MAX 4-5 SENTENCES. Start with story/hook, end with knowledge. Seedha khatam.
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

BAD endings (feel abrupt — like more was coming):
- "Aur ye 200 GSM hota hai." (sounds like next point is coming)
- "Print karke dekh lo." (too commanding, no sense of conclusion)

RULES for ending:
- Last sentence MUST use a CONCLUSIVE phrase: "bas", "simple hai", "bas yehi hai", "ho jayega", "fark dikh jayega"
- The last 3-4 words should feel like they're naturally trailing off
- Add "..." before the final phrase for a natural pause feel
- The ending should make the listener think "haan, baat khatam hui" — NOT "aur kya?"

━━━ NATURAL SPEECH FILLERS (CRITICAL for human feel) ━━━

The voice MUST sound like a REAL person thinking and talking, NOT a script being read.
Add NATURAL HINDI FILLERS and THINKING PAUSES throughout the script:

FILLER WORDS to use naturally (pick 2-3 per script, don't overdo):
- "Dekho..." (Look/See... — opening filler)
- "Matlab..." (Meaning... — thinking pause)
- "Accha..." (Okay/Right... — transition filler)
- "Hmm..." (thinking sound)
- "Toh basically..." (So basically... — explanation starter)
- "Aur ek baat..." (And one thing... — adding a point)
- "Samjho..." (Understand... — before explaining)
- "Seedhi baat hai..." (Straight talk... — before a direct statement)
- "Ab dekho..." (Now see... — transitioning)

EXAMPLE with fillers (natural flow):
"Dekho... GSM bas fabric ka weight hota hai. Matlab jyada GSM toh mota fabric,
kam GSM toh patla. Basically kisi bhi kapde ko 1 square meter mein cut karke
weight kar doge toh... wahi GSM hota hai. Simple hai."

RULES for fillers:
- Place fillers at SENTENCE STARTS and BEFORE explanations, never mid-word
- Use "..." (ellipsis) after fillers to indicate natural pause
- Don't use more than 3 fillers per script — it should feel natural, not stuttering
- Fillers should FLOW with the sentence, not feel forced

━━━ LANGUAGE RULES ━━━

Write in ROMAN HINGLISH — Hindi words in ENGLISH LETTERS (not Devanagari).
- ENGLISH for technical terms: "fabric", "GSM", "weight", "print", "quality",
  "color", "shrinkage", "biowash", "preshrunk", "sample", "cotton"
- HINDI for flow: "agar", "toh", "aur", "mein", "matlab", "wahi",
  "hota hai", "bolte hai", "kar lo", "lelo", "bas"
- Numbers in digits: "200", "160", "10", "2%"

━━━ VIDEO PROMPT RULES ━━━

Write 3 detailed video scene descriptions for AI video generation (Google Veo).
Each clip will be 8 seconds, vertical 9:16 format.

IMPORTANT VIDEO PROMPT GUIDELINES:
- Describe EXACTLY what the camera sees — this is for an AI that generates video
- Include camera angle, lighting, movement, and specific objects
- Focus on t-shirt/textile/manufacturing/printing industry visuals
- Be SPECIFIC: "Close-up of Indian man's hands holding a thick white cotton
  round-neck t-shirt, turning it to show the smooth bio-washed fabric texture,
  warm indoor lighting, slight camera dolly forward" — NOT "a tshirt"
- NO text/words/labels in the video (subtitles are added separately)
- NO people's faces (to avoid AI face artifacts)
- Show HANDS, products, fabrics, machines, packaging — not faces
- Each prompt should be 40-80 words for best results
- Describe REALISTIC scenes that could exist in a real Indian textile business
- Each of the 3 clips must show a DIFFERENT scene — NO repetition between clips

OUTPUT THIS JSON ONLY (no markdown, no code blocks):
{{
    "title": "YouTube title in English, max 70 chars, SEO optimized for printing business",
    "description": "YouTube description in English with 6-8 hashtags. Include Sale91.com link.",
    "script_voice": "The ROMAN HINGLISH script. MAX 3-4 sentences. NO website. NO selling. Pure knowledge.",
    "script_english": "Clean English translation for on-screen subtitles",
    "music_mood": "Pick ONE mood for background music that matches this topic's emotion: upbeat | calm | serious | motivational | trendy",
    "video_prompt_1": "Detailed 40-80 word visual scene for HOOK — show the PROBLEM or customer situation. E.g., hands examining a faded/damaged t-shirt, a confused person looking at fabric, returned packages on a table.",
    "video_prompt_2": "Detailed 40-80 word visual scene for EXPLANATION — show the PROCESS or comparison. E.g., hands comparing two fabric pieces side by side, close-up of fabric texture difference, weighing fabric on scale.",
    "video_prompt_3": "Detailed 40-80 word visual scene for RESOLUTION — show the CORRECT way or result. E.g., hands holding a perfect quality t-shirt, smooth bio-washed fabric close-up, neatly packed order ready to ship.",
    "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8"]
}}
"""


# ═══════════════════════════════════════════════════════════════════════
# YOUTUBE HELPERS
# ═══════════════════════════════════════════════════════════════════════

def get_publish_time():
    ist = pytz.timezone(TIMEZONE)
    now = datetime.now(ist)
    today_publish = now.replace(hour=PUBLISH_HOUR, minute=PUBLISH_MINUTE, second=0, microsecond=0)
    if now >= today_publish:
        publish_at = today_publish + timedelta(days=1)
    else:
        publish_at = today_publish
    publish_utc = publish_at.astimezone(pytz.utc)
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


def upload_to_youtube(youtube, video_path, title, description, tags):
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    # SEO enhance
    if UPLOAD_AS_SHORT and "#shorts" not in title.lower():
        if len(title) + 8 <= 100:
            title += " #Shorts"

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

#PlainTshirt #BlankTshirt #TshirtPrinting #WholesaleTshirt
#DTGPrinting #DTFPrinting #ScreenPrinting #Sale91
#TshirtManufacturer #CottonTshirt #BulkTshirt #PrintingBusiness
"""

    all_tags = list(tags) if tags else []
    for t in DEFAULT_TAGS:
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
        publish_ist, publish_utc = get_publish_time()
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

# Mood → text prompt for AI music generation (Meta MusicGen Large via HF Pro)
MOOD_TO_MUSIC_PROMPT = {
    "upbeat": "upbeat happy energetic instrumental music, positive vibes, bright, no vocals",
    "calm": "soft ambient lo-fi instrumental music, relaxed, gentle piano, no vocals",
    "serious": "deep cinematic dramatic instrumental music, serious tone, no vocals",
    "motivational": "motivational inspiring corporate instrumental music, uplifting, no vocals",
    "trendy": "modern trendy electronic beat, cool urban instrumental, no vocals",
}

# Repo-level bg_music/ folder (fallback — persists across runs, committed to git)
REPO_BG_MUSIC_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bg_music")


def _copy_repo_music_to_workdir():
    """Copy music files from repo bg_music/ to working directory."""
    if not os.path.isdir(REPO_BG_MUSIC_FOLDER):
        return
    import shutil
    for ext in ("*.mp3", "*.wav"):
        for src in glob.glob(f"{REPO_BG_MUSIC_FOLDER}/{ext}"):
            dst = os.path.join(BG_MUSIC_FOLDER, os.path.basename(src))
            if not os.path.exists(dst):
                shutil.copy2(src, dst)


def generate_bg_music(mood="calm"):
    """Generate background music using Meta MusicGen Large via Hugging Face Inference API (requires HF Pro)."""
    hf_key = os.environ.get("HF_API_KEY")
    if not hf_key:
        return None

    prompt = MOOD_TO_MUSIC_PROMPT.get(mood, MOOD_TO_MUSIC_PROMPT["calm"])
    music_path = f"{BG_MUSIC_FOLDER}/ai_{mood}_{random.randint(100,999)}.wav"

    HF_API_URL = "https://router.huggingface.co/hf-inference/models/facebook/musicgen-large"

    try:
        print(f"   🤖 AI generating '{mood}' background music...")
        response = requests.post(
            HF_API_URL,
            headers={"Authorization": f"Bearer {hf_key}"},
            json={"inputs": prompt},
            timeout=180,
        )

        if response.status_code == 503:
            # Model is loading, wait and retry once
            wait_time = response.json().get("estimated_time", 30)
            print(f"   ⏳ MusicGen model loading, waiting {int(wait_time)}s...")
            time.sleep(min(wait_time, 60))
            response = requests.post(
                HF_API_URL,
                headers={"Authorization": f"Bearer {hf_key}"},
                json={"inputs": prompt},
                timeout=180,
            )

        if response.status_code == 200:
            with open(music_path, "wb") as f:
                f.write(response.content)
            print(f"   ✅ AI music generated: {os.path.basename(music_path)}")
            return music_path
        else:
            print(f"   ⚠️ MusicGen API returned {response.status_code}: {response.text[:100]}")
            return None

    except Exception as e:
        print(f"   ⚠️ AI music generation failed: {e}")
        return None


def load_bg_music(mood="calm"):
    """Load background music: AI generate first, fall back to repo files."""
    # Step 1: Try AI generation (best — unique music per video, mood-matched)
    ai_path = generate_bg_music(mood)
    if ai_path:
        return

    # Step 2: Fall back to repo music files
    _copy_repo_music_to_workdir()

    existing = glob.glob(f"{BG_MUSIC_FOLDER}/*.mp3") + glob.glob(f"{BG_MUSIC_FOLDER}/*.wav")
    if existing:
        mood_files = [f for f in existing if mood.lower() in os.path.basename(f).lower()]
        print(f"   🎵 {len(existing)} music file(s) available ({len(mood_files)} match '{mood}' mood)")
    else:
        print("   ⚠️ No background music available.")
        print("   💡 Option 1: Set HF_API_KEY secret (HF Pro $9/mo) for AI-generated music")
        print("   💡 Option 2: Add .mp3 files to bg_music/ folder (calm_lofi.mp3, upbeat_beat.mp3, etc.)")


def mix_background_music(voice_audio_clip, duration, mood="calm"):
    """Mix background music with voice audio. Prefers mood-matching files."""
    if not ADD_BG_MUSIC:
        return voice_audio_clip

    all_files = glob.glob(f"{BG_MUSIC_FOLDER}/*.mp3") + glob.glob(f"{BG_MUSIC_FOLDER}/*.wav")
    if not all_files:
        return voice_audio_clip

    # Prefer files matching the mood (e.g., "calm_12345.mp3" or "calm_lofi.mp3")
    mood_files = [f for f in all_files if mood.lower() in os.path.basename(f).lower()]
    music_files = mood_files if mood_files else all_files

    try:
        music_path = random.choice(music_files)
        print(f"   🎵 Adding background music: {os.path.basename(music_path)}")

        music_clip = AudioFileClip(music_path)

        # Loop music if shorter than video, or trim if longer
        if music_clip.duration < duration:
            music_clip = audio_loop(music_clip, duration=duration)
        else:
            music_clip = music_clip.subclip(0, duration)

        # Reduce music volume and fade out at the end
        music_clip = volumex(music_clip, BG_MUSIC_VOLUME)
        from moviepy.audio.fx.audio_fadeout import audio_fadeout
        music_clip = audio_fadeout(music_clip, 2.0)

        # Composite: voice on top, music underneath
        mixed = CompositeAudioClip([music_clip, voice_audio_clip])
        print(f"   ✅ Background music mixed at {int(BG_MUSIC_VOLUME * 100)}% volume")
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

3. VALUE — Does the viewer LEARN something useful in under 15 seconds?
   Bad: vague fluff. Good: specific, practical, actionable knowledge.

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
            model="claude-sonnet-4-5-20250929", max_tokens=300,
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
    print(f"   Time: {datetime.now(pytz.timezone(TIMEZONE)).strftime('%d %b %Y, %I:%M %p IST')}")
    print()

    # ── 1. API Keys ──
    elevenlabs_key = None  # Deprecated — using OpenAI TTS now
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

    from google import genai
    from google.genai import types
    veo_client = genai.Client(api_key=google_key)

    # ── 2. Pick Topic ──
    topic_history = []
    if os.path.exists(TOPIC_HISTORY_FILE):
        with open(TOPIC_HISTORY_FILE, "r") as f:
            topic_history = json.load(f)

    unused = [t for t in TOPIC_BANK if t not in topic_history]
    if unused:
        fresh_topic = random.choice(unused)
    else:
        print("   🧠 All topics used — Claude generating new one...")
        resp = claude.messages.create(
            model="claude-sonnet-4-5-20250929", max_tokens=200,
            messages=[{"role": "user", "content": f"""Generate 1 new YouTube Shorts topic for a B2B plain t-shirt manufacturer.
Style: practical knowledge, no selling. Hindi conversational.
Already used: {json.dumps(topic_history[-10:])}
Return ONLY the topic text, nothing else."""}]
        )
        fresh_topic = resp.content[0].text.strip()

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
    script_english = data["script_english"]
    yt_title = data["title"]
    yt_description = data["description"]
    yt_tags = data.get("tags", [])
    music_mood = data.get("music_mood", "calm")
    video_prompts = [data.get("video_prompt_1",""), data.get("video_prompt_2",""), data.get("video_prompt_3","")]

    print(f"   🎵 Mood: {music_mood}")

    # ── 3b. Load Background Music ──
    load_bg_music(music_mood)

    # ── 4. Generate Voice (OpenAI gpt-4o-mini-tts) ──
    print("   🎙️ Generating voice (OpenAI TTS)...")
    audio_path = f"{WORK_DIR}/voice_{random.randint(100,999)}.mp3"

    try:
        response = openai_client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice=TARGET_VOICE,
            input=script_voice + "...",  # Trailing ellipsis prevents last-word cutoff
            instructions=VOICE_INSTRUCTIONS,
            speed=VOICE_SPEED,
            response_format="mp3",
        )
        response.stream_to_file(audio_path)
        print(f"   ✅ Voice: OpenAI {TARGET_VOICE} (native Hindi)")
    except Exception as e:
        print(f"   ❌ OpenAI TTS failed: {e}")
        return

    # ── 5. Generate Video Clips (Veo 3.1) ──
    downloaded_clips = []
    VEO_MAX_RETRIES = 5
    VEO_RETRY_WAIT = 90

    if TEST_MODE:
        # Test mode: create cheap placeholder clips (solid color) instead of Veo
        print("   🧪 TEST MODE: Skipping Veo clips, using placeholder video...")
        for i in range(VEO_CLIPS_PER_VIDEO):
            placeholder_path = f"{WORK_DIR}/test_clip_{i}.mp4"
            colors = [(30, 60, 90), (50, 80, 40), (80, 40, 60)]
            color = colors[i % len(colors)]
            placeholder = ColorClip(size=(VIDEO_WIDTH, VIDEO_HEIGHT), color=color, duration=VEO_DURATION)
            placeholder.write_videofile(placeholder_path, fps=FPS, codec="libx264", logger=None)
            downloaded_clips.append(placeholder_path)
        print(f"   ✅ {len(downloaded_clips)} test clips created (free)")

    if not TEST_MODE:
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
        wmodel = whisper.load_model("base")
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
        sf = max(total_clip_duration / total_duration, 0.5)
        from moviepy.video.fx.speedx import speedx
        trimmed = [speedx(v, sf) for v in video_objects]

    if CLIP_FADE_DURATION > 0 and len(trimmed) > 1:
        for idx in range(len(trimmed)):
            if idx > 0: trimmed[idx] = trimmed[idx].crossfadein(CLIP_FADE_DURATION)
            if idx < len(trimmed) - 1: trimmed[idx] = trimmed[idx].crossfadeout(CLIP_FADE_DURATION)

    base_video = concatenate_videoclips(trimmed, method="compose")
    if base_video.duration > total_duration:
        base_video = base_video.subclip(0, total_duration)

    # ── 8. Overlays ──
    layers = [base_video]

    # Subtitles
    if ADD_SUBTITLES and subtitle_segments:
        for seg in subtitle_segments:
            dur = seg["end"] - seg["start"]
            if dur < 0.1: continue
            try:
                txt = TextClip(seg["text"], fontsize=SUBTITLE_FONTSIZE, font=SUBTITLE_FONT,
                    color=SUBTITLE_COLOR, stroke_color=SUBTITLE_STROKE, stroke_width=SUBTITLE_STROKE_W,
                    method='caption', size=(VIDEO_WIDTH - 160, None), align='center')
                txt_w, txt_h = txt.size
                bg_w = min(txt_w + SUBTITLE_BG_PADDING * 2, VIDEO_WIDTH - 40)
                bg_h = txt_h + SUBTITLE_BG_PADDING * 2
                bg = ColorClip(size=(bg_w, bg_h), color=SUBTITLE_BG_COLOR).set_opacity(SUBTITLE_BG_OPACITY)
                sub_y = int(VIDEO_HEIGHT * 0.75)
                bg = bg.set_position(((VIDEO_WIDTH - bg_w) // 2, sub_y - SUBTITLE_BG_PADDING)).set_start(seg["start"]).set_duration(dur)
                txt = txt.set_position(((VIDEO_WIDTH - txt_w) // 2, sub_y)).set_start(seg["start"]).set_duration(dur)
                layers.extend([bg, txt])
            except: pass

    # Watermark
    if ADD_WATERMARK:
        try:
            wm = TextClip(WATERMARK_TEXT, fontsize=WATERMARK_FONTSIZE, font=SUBTITLE_FONT,
                color=WATERMARK_COLOR, stroke_color="black", stroke_width=1, method='label')
            wm = wm.set_opacity(WATERMARK_OPACITY).set_position((20, VIDEO_HEIGHT - 55)).set_duration(total_duration)
            layers.append(wm)
        except: pass

    # Hook
    if ADD_HOOK_TEXT:
        try:
            hook_line = " ".join(fresh_topic.split()[:6]).upper()
            ht = TextClip(hook_line, fontsize=52, font=SUBTITLE_FONT, color="white",
                stroke_color="black", stroke_width=3, method='caption',
                size=(VIDEO_WIDTH - 200, None), align='center')
            ht_w, ht_h = ht.size
            hbg = ColorClip(size=(ht_w + 40, ht_h + 30), color=(0,0,0)).set_opacity(0.75)
            hbg = hbg.set_position(((VIDEO_WIDTH-ht_w-40)//2, int(VIDEO_HEIGHT*0.35))).set_start(0).set_duration(HOOK_DURATION).crossfadeout(0.4)
            ht = ht.set_position(((VIDEO_WIDTH-ht_w)//2, int(VIDEO_HEIGHT*0.35)+15)).set_start(0).set_duration(HOOK_DURATION).crossfadeout(0.4)
            layers.extend([hbg, ht])
        except: pass

    # CTA
    if ADD_CTA_OVERLAY:
        try:
            cta = TextClip(CTA_TEXT, fontsize=44, font=SUBTITLE_FONT, color="white",
                stroke_color="black", stroke_width=2, method='label')
            cta = cta.set_position(("center", 0.88), relative=True).set_start(max(0, total_duration-3.5)).set_duration(3.5).crossfadein(0.3)
            layers.append(cta)
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

    # ── 10. Upload to YouTube ──
    if TEST_MODE:
        print(f"\n{'='*60}")
        print(f"  🧪 TEST MODE COMPLETE — video NOT uploaded")
        print(f"  📁 Video saved: {output_path}")
        print(f"  📌 Title: {yt_title}")
        print(f"  🎵 Mood: {music_mood}")
        print(f"{'='*60}")
    else:
        print("   📤 Uploading to YouTube...")
        youtube = get_youtube_service()
        if youtube:
            vid_id, vid_url = upload_to_youtube(youtube, output_path, yt_title, yt_description, yt_tags)
            print(f"\n{'='*60}")
            print(f"  ✅ DAILY SHORT COMPLETE!")
            print(f"  🔗 {vid_url}")
            print(f"  📌 {yt_title}")
            print(f"{'='*60}")
        else:
            print("   ❌ YouTube auth failed. Video saved locally.")

    # Cleanup
    for f in downloaded_clips:
        try: os.remove(f)
        except: pass
    for f in ambient_temp_files:
        try: os.remove(f)
        except: pass
    try: os.remove(audio_path)
    except: pass


if __name__ == "__main__":
    main()
