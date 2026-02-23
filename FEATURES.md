# Sale91.com — YouTube Shorts Bot: Complete Feature Reference

> **File:** `daily_short.py` (4064 lines) | **Last updated:** Feb 2026
> **Brand:** Sale91.com — B2B plain t-shirt manufacturer (Tiruppur + Delhi)
> **Channels:** New Shorts channel + Main channel (50K subs, 5.5L monthly views)

---

## Table of Contents

1. [Configuration & Constants](#1-configuration--constants)
2. [Topic Bank & Series System](#2-topic-bank--series-system)
3. [Smart Topic Selection](#3-smart-topic-selection)
4. [Source Channel Intelligence](#4-source-channel-intelligence-main-channel-50k)
5. [Engagement Feedback Loop](#5-engagement-feedback-loop)
6. [1 Lakh Views Threshold (Hybrid Analytics)](#6-1-lakh-views-threshold-hybrid-analytics)
7. [Script Generation & Quality Gate](#7-script-generation--quality-gate)
8. [Title Optimization](#8-title-optimization)
9. [Video Clip Generation (Veo + Kling)](#9-video-clip-generation-veo--kling)
10. [Video Processing & Effects](#10-video-processing--effects)
11. [Text-to-Speech (ElevenLabs + OpenAI)](#11-text-to-speech-elevenlabs--openai)
12. [Background Music & Sound Effects](#12-background-music--sound-effects)
13. [Subtitles & Overlays](#13-subtitles--overlays)
14. [Thumbnail Generation](#14-thumbnail-generation)
15. [YouTube Publishing](#15-youtube-publishing)
16. [Publish Time Analytics (A/B Testing)](#16-publish-time-analytics-ab-testing)
17. [Auto-Pin Comment](#17-auto-pin-comment)
18. [Auto-Playlist Organization](#18-auto-playlist-organization)
19. [Instagram Reels Cross-Post](#19-instagram-reels-cross-post)
20. [Cost Tracking & Budget Control](#20-cost-tracking--budget-control)
21. [GitHub Actions Automation](#21-github-actions-automation)
22. [Data Persistence (JSON Files)](#22-data-persistence-json-files)
23. [Feature Dashboard](#23-feature-dashboard)
24. [Dependencies](#24-dependencies)
25. [Environment Variables](#25-environment-variables)

---

## 1. Configuration & Constants

### Working Directory
- **Path:** `/tmp/yt_shorts` (line 51)
- Auto-creates: `bg_music/`, `my_clips/` subdirectories

### Business Context
- **Lines:** 60-94
- Hardcoded brand info: Sale91.com, Tiruppur/Delhi, product catalog, pricing, MOQ, key facts
- Used in script generation prompt for Claude

### Video Settings
| Constant | Value | Line |
|----------|-------|------|
| `VIDEO_WIDTH` | 1080 | 145 |
| `VIDEO_HEIGHT` | 1920 | 145 |
| `FPS` | 30 | 146 |
| `VEO_CLIPS_PER_VIDEO` | 5 | 147 |
| `VEO_MODEL` | `veo-3.1-fast-generate-preview` | 148 |
| `VEO_ASPECT_RATIO` | `9:16` | 149 |
| `VEO_DURATION` | 8 seconds | 150 |
| `VEO_MAX_RETRIES` | 4 | 151 |
| `VEO_RETRY_WAIT` | 60 seconds | 152 |
| `VEO_POLL_TIMEOUT` | 300 seconds (5 min) | 153 |

### Test Modes
| Mode | Env Var | Line | Behavior |
|------|---------|------|----------|
| Test Mode | `TEST_MODE=1` | 101 | No Veo clips, no upload (free run) |
| Skip Clips | `SKIP_CLIPS=1` | 103 | Placeholder clips but full upload pipeline |
| Single Veo Test | `SINGLE_VEO_TEST=1` | 106 | 1 real Veo clip + 4 blank, full upload (~80% cost savings) |

---

## 2. Topic Bank & Series System

### Topic Bank
- **Lines:** 304-428
- **Count:** 110 curated topics
- **7 Series:**

| Series | Topics | Keywords Example |
|--------|--------|------------------|
| Fabric & GSM Knowledge | 20 | gsm, fabric, cotton, yarn, jersey |
| Customer Stories & Incidents | 20 | customer, client, return, order |
| Printing Methods Deep Dive | 15 | dtg, dtf, screen print, sublimation |
| Business Tips & Mistakes | 15 | business, pricing, moq, margin |
| Quality Checks & Testing | 15 | biowash, pilling, colorfastness |
| Product & Style Knowledge | 15 | oversized, polo, hoodie, acid wash |
| Myth Busters | 10 | myth, galat, sochte |

### Series Tags System
- **`TOPIC_SERIES_TAGS`** (lines 437-485): Maps series → keywords + tags
- **`get_topic_tags(topic)`** (line 488): Returns topic-specific tags by keyword matching
- **`get_topic_hashtags(topic)`** (line 504): Generates series-specific hashtags for YouTube description
- **Base tags always included:** `#Sale91`, `#PlainTshirt`, `#TshirtManufacturer`

---

## 3. Smart Topic Selection

### Function: `smart_pick_topic(claude_client, topic_bank, topic_history)`
- **Lines:** 2812-2897
- **Priority chain:**

```
1. Unused topics in bank → sort by category engagement → pick from top 30%
   ├─ Category source: main channel (if <1L views) or own channel (if ≥1L views)
   ├─ Score with Claude (/40) → accept if ≥25
   └─ Try 2 alternatives if first scored low → accept if ≥20

2. Bank exhausted → AI generate trending topics
   ├─ search_trending_topics() → 10 candidates
   ├─ Score all → pick best (>20)
   └─ Fallback: single Claude topic generation
```

### Trending Topic Generation: `search_trending_topics(anthropic_client)`
- **Lines:** 2695-2720
- **Model:** `claude-sonnet-4-5-20250929`
- **Inputs:** Current month, India season, own channel top topics, source channel top topics, audience questions
- **Output:** JSON array of 10 topic strings

### Topic Review Gate: `review_topic(claude_client, topic, topic_history)`
- **Lines:** 2762-2809
- **Model:** `claude-sonnet-4-5-20250929`
- **Scoring dimensions (40 points max):**
  1. Search potential (1-10)
  2. Freshness (1-10)
  3. Storytelling fit (1-10)
  4. Viral shareability (1-10)
- **Auto-approval:** `TOPIC_MIN_SCORE = 25` (line 2693)
- **Max candidates:** `TOPIC_MAX_CANDIDATES = 5` (line 2692)

### Season Detection: `_get_india_season()`
- **Lines:** 2749-2759
- Maps month → season context (Summer/Monsoon/Festival/Winter)

---

## 4. Source Channel Intelligence (Main Channel, 50K)

### Fetch Source Insights: `fetch_source_channel_insights()`
- **Lines:** 1531-1615
- **API:** YouTube Data API v3 (read-only, via `SOURCE_CHANNEL_API_KEY`)
- **Env vars:** `CHANNEL_ID_2`, `YOUTUBE_API_KEY_1`
- **Cache:** `source_channel_insights.json` (24h TTL, line 1541-1551)
- **Data fetched:** 50 most recent videos with snippet + statistics
- **Quota:** ~101 units (1x search.list=100 + 1x videos.list=1)

### Top Topics: `get_source_channel_top_topics(n=10)`
- **Lines:** 1618-1623
- Returns top N video titles ranked by views

### Category Ranking: `get_source_channel_category_ranking()`
- **Lines:** 1626-1651
- Ranks categories by average views using `TOPIC_SERIES_TAGS` keyword matching
- Same logic as own-channel `get_top_performing_categories()`

### Comment Mining: `fetch_source_channel_comments(max_videos=5, max_comments_per_video=20)`
- **Lines:** 1654-1732
- Fetches top comments from top 5 videos (by engagement)
- Filters: >10 chars, sorted by likes, keeps top 30
- Cached inside `source_channel_insights.json`
- **Quota:** ~5 units (1 per video)

### Audience Questions: `get_audience_questions(n=10)`
- **Lines:** 1735-1757
- Filters comments containing question words: `?`, `kaise`, `kya`, `kyu`, `how`, `what`, `why`, `suggest`, etc.
- Used in trending topic generation + script prompts

### Posting Patterns: `get_source_channel_posting_patterns()`
- **Lines:** 1760-1789
- Analyzes source video publish times (converted to IST)
- Returns `{hour: avg_views}` dict

### Optimized Slot: `get_source_optimized_slot()`
- **Lines:** 1792-1825
- Finds best posting hour with >30% above-average performance
- Maps to nearest `PUBLISH_SLOT` or creates custom slot if >2h away

---

## 5. Engagement Feedback Loop

### Check Past Engagement: `check_past_engagement(youtube)`
- **Lines:** 1371-1444
- **Trigger:** Called before YouTube upload in `main()` (line 3958)
- **Delay:** `ENGAGEMENT_CHECK_DELAY_HOURS = 48` (line 292)
- **Process:**
  1. Fetches last 10 uploaded videos via YouTube API
  2. Skips already-tracked videos and videos < 48h old
  3. Records: `video_id`, `title`, `published_at`, `checked_at`, `hours_since_publish`, `views`, `likes`, `comments`, `publish_hour_ist`
- **Storage:** `engagement_history.json` (repo root, git-tracked)

### Top Performing Topics: `get_top_performing_topics(n=5)`
- **Lines:** 1447-1470
- Sorts engagement history by views (primary) + likes (secondary)
- Returns top N video titles

### Top Performing Categories: `get_top_performing_categories()`
- **Lines:** 1473-1508
- Aggregates views per category via `TOPIC_SERIES_TAGS` keyword matching
- Ranks categories by average views
- Used in `smart_pick_topic()` for category-biased selection

### New Channel Total Views: `get_new_channel_total_views()`
- **Lines:** 1512-1523
- Sums all views from `engagement_history.json`
- Used to decide if new channel has crossed 1 lakh threshold

---

## 6. 1 Lakh Views Threshold (Hybrid Analytics)

### Constant
- **`NEW_CHANNEL_VIEWS_THRESHOLD = 100_000`** (line 293)

### Logic
Both **publish time** and **engagement/category** decisions use this threshold:

| New Channel Total Views | Publish Time Priority | Category Priority |
|------------------------|----------------------|-------------------|
| **< 1,00,000** | Main channel posting patterns → A/B rotation | Main channel categories → Own channel fallback |
| **≥ 1,00,000** | Own channel analytics → Main channel fallback → A/B rotation | Own channel categories → Main channel fallback |

### Implementation Points
1. **`get_best_publish_slot()`** (lines 2153-2202): Checks `get_new_channel_total_views()` → skips own analytics when <1L
2. **`smart_pick_topic()`** (lines 2823-2835): Swaps category source priority based on threshold

### Console Output
```
📊 New channel: 12,450 total views (<1L) — main channel data PRIMARY
📊 New channel: 1,23,000 total views (≥1L) — own analytics PRIMARY
```

---

## 7. Script Generation & Quality Gate

### Script Prompt: `get_script_prompt(topic)`
- **Lines:** 1849-2069
- **Massive prompt (~3000 tokens)** covering:
  - Business context (Sale91.com products, pricing)
  - Audience intelligence from main channel
  - Speaking style rules (Hinglish, storytelling, compound verbs)
  - Structure: Hook → Problem Build-up → Knowledge Drop → Natural Ending
  - 2 full example scripts with extracted rules
  - Natural speech fillers (Dekho, Matlab, Toh basically)
  - Language rules (Roman Hinglish, English for technical terms)
  - Hook text rules (max 4 words, curiosity-driven)
  - Video prompt rules (5 clips, 40-80 words each, story arc)
  - Recent Veo prompts for deduplication

### Script Output Format
```json
{
  "title": "YouTube title, max 70 chars, SEO optimized",
  "description": "YouTube description with hashtags",
  "script_voice": "Roman Hinglish script, 8-12 sentences, 45-55 seconds",
  "script_english": "English translation for subtitles",
  "hook_text": "Max 4 words, UPPERCASE",
  "music_mood": "upbeat|calm|serious|motivational|trendy",
  "video_prompt_1-5": "Veo scene descriptions, 40-80 words each",
  "tags": ["tag1", "tag2", ...]
}
```

### Quality Gate: `review_script(claude_client, ...)`
- **Lines:** 2900-2968
- **Model:** `claude-opus-4-6` (line 2951)
- **6 scoring dimensions (60 points max):**
  1. Hook (first 2 seconds) — story/customer incident?
  2. Natural feel — sounds like real factory owner?
  3. Value — viewer learns something specific?
  4. Ending — trails off naturally?
  5. Viral potential — worth saving/sharing?
  6. Visual alignment — Veo prompts match script?
- **Approval threshold:** score ≥ 36/60, no single score below 4
- **Max attempts:** `SCRIPT_MAX_ATTEMPTS = 3` (line 109)
- **Feedback loop:** Rejection reasons passed to next attempt prompt

### Post-Processing (line 3264-3268)
- `...` replaced with `,` (TTS reads ellipsis as long pause)
- Elongated sounds trimmed (`aaaaaa` → `aa`)
- Double commas cleaned

---

## 8. Title Optimization

### Function: `optimize_title(claude_client, original_title, script_english, topic)`
- **Lines:** 2971-3043
- **Model:** `claude-sonnet-4-5-20250929`
- **Process:**
  1. Takes original title from script generation
  2. Includes source channel's top 5 titles as reference
  3. Generates 3 alternative titles in different styles:
     - Question style: "Why Does Your DTG Print Fade?"
     - Shock/Number style: "Rs 45 vs Rs 90: The Difference"
     - Mistake/Warning style: "Stop Making This GSM Mistake"
  4. Picks best for CTR with reason
  5. Max 70 chars (YouTube Shorts mobile visibility)

---

## 9. Video Clip Generation (Veo + Kling)

### Veo 3.1 (Primary)
- **Model:** `veo-3.1-fast-generate-preview` (line 148)
- **API:** Google GenAI (`google.genai`)
- **5 clips per video** following story arc:
  1. HOOK — problem/dramatic moment
  2. CONTEXT — setting the scene
  3. EXPLANATION — comparison/process
  4. DEMONSTRATION — technique/test
  5. RESOLUTION — correct result/conclusion
- **Duration:** 8 seconds per clip
- **Retries:** Up to 4 attempts with exponential backoff
- **RPM cooldown:** 45 seconds between clips (line 3474)
- **Poll timeout:** 5 min max per clip (10s polling interval)
- **Cost:** ~$0.50/clip

### Kling Fallback (via fal.ai)
- **Function:** `generate_clip_kling(prompt_text, output_path)` (line 3050)
- **Model:** `fal-ai/kling-video/v2.6/pro/text-to-video` (line 160)
- **Trigger:** Auto-activates on Veo `429/RESOURCE_EXHAUSTED` errors
- **Sticky fallback:** Once triggered, all remaining clips use Kling (line 3464)
- **Duration:** 5 seconds (line 161)
- **Retries:** 3 attempts (line 163)
- **Negative prompt:** `blur, distort, low quality, text, watermark, face, human face`
- **Cost:** ~$0.35/clip ($0.07/s)
- **Requires:** `FAL_KEY` env var

### Visual Deduplication
- **Clip History:** `clip_history.json` stores last 30 videos' Veo prompts (lines 3281-3296)
- **Dedup prompt:** `_get_recent_clip_prompts()` (line 1831) injects recent prompts into script prompt

---

## 10. Video Processing & Effects

### Smart Crop: `smart_crop(clip, tw=1080, th=1920)`
- **Lines:** 3605-3616
- Auto-crops any aspect ratio to 9:16 Shorts format
- Centers crop on the middle of the frame

### Ken Burns Zoom: `apply_ken_burns(clip, zoom_percent=5)`
- **Lines:** 3618-3644
- Slow 3% cinematic zoom-in over clip duration (line 3688: `zoom_percent=3`)
- Makes static AI clips feel alive and cinematic
- Uses PIL for per-frame resizing + center crop

### Black Frame Trimming: `trim_black_intro(clip, threshold=15, max_trim=3.0)`
- **Lines:** 3646-3667
- Detects dark frames at clip start (avg brightness < 15)
- Checks every 0.25s, trims up to 3 seconds

### Clip Looping
- **Lines:** 3703-3722
- When total clip duration < voice duration, loops clips cyclically
- No slow-motion — natural speed throughout

### Crossfade Transitions
- **`CLIP_FADE_DURATION = 0.3`** seconds (line 228)
- Uses `concatenate_videoclips(method="compose", padding=-0.3)`

### Video Codec & Render
- **Codec:** H.264 (`libx264`)
- **CRF:** 18 (high quality, line 3676)
- **Bitrate:** 8000k (line 3922)
- **FPS:** 30
- **Preset:** medium
- **Threads:** 4

---

## 11. Text-to-Speech (ElevenLabs + OpenAI)

### ElevenLabs (Primary)
- **Voice ID:** `FZkK3TvQ0pjyDmT8fzIW` — Hindi voice (line 112)
- **Model:** `eleven_multilingual_v2` (line 113)
- **Settings:** stability=0.62, similarity=0.75, style=0.22, speaker_boost=true (lines 114-119)
- **Cost:** ~$0.30/1K chars

### OpenAI TTS (Fallback)
- **Model:** `gpt-4o-mini-tts` (line 3336)
- **Voice:** `ash` (line 122)
- **Speed:** 1.0 (line 123)
- **Voice Instructions** (lines 124-144): Detailed Hinglish pronunciation rules
  - Hindi words = native Indian pronunciation
  - English technical terms keep English pronunciation
  - Speaking style: confident, casual, like explaining over chai
  - No filler sounds (umm, hmm), short natural pauses

### Audio Normalization
- **Lines:** 3351-3366
- **Method:** ffmpeg `loudnorm` filter
- **Target:** -16 LUFS, TP=-1.5, LRA=11
- Ensures consistent volume across all videos

---

## 12. Background Music & Sound Effects

### AI Music Generation: `generate_bg_music(mood="calm")`
- **Lines:** 2405-2468
- **API:** Replicate ACE-Step (line 2426)
- **Model:** `lucataco/ace-step:280fc4f9ee...`
- **Duration:** 30 seconds per generation
- **Retries:** 3 attempts with exponential backoff (2s, 4s)
- **Cost:** ~$0.05/generation

### Mood Mapping: `MOOD_TO_MUSIC_PROMPT`
- **Lines:** 2395-2401
- 5 moods → prompt tags:
  - `upbeat`: energetic, electronic, pop
  - `calm`: soft, ambient, lo-fi, gentle piano
  - `serious`: deep, cinematic, dramatic, orchestral
  - `motivational`: inspiring, corporate, uplifting
  - `trendy`: modern, electronic beat, urban, trap

### Music Loading Priority: `load_bg_music(mood)` + `mix_background_music()`
- **Lines:** 2471-2632
- **Priority:** AI-generated mood-match → AI-generated any → mood-matching file → any file

### Dynamic Volume Curve: `_apply_dynamic_volume(music_clip, duration)`
- **Lines:** 2486-2518
- **3-zone curve:**
  - Start (first 2s): 15% — energy at hook
  - Middle: 5% — voice dominant
  - End (last 3s): 12% — emotional close
- Uses numpy vectorized operations for smooth transitions

### Hook Sound Effect: `generate_hook_sfx(duration=0.6)`
- **Lines:** 2521-2571
- **4-layer cinematic design:**
  1. Sub-bass boom (40Hz, exponential decay)
  2. Mid impact hit (200→80Hz pitch drop)
  3. High-freq whoosh (deterministic sine sum at 2200-7200Hz)
  4. Transient click (1000Hz, fast attack+decay)
- **Volume:** `HOOK_SFX_VOLUME = 0.25` (line 221)
- Soft-clipped via `tanh` to avoid distortion

### Audio Fadeout
- 2-second fadeout at end of background music (line 2618)
- 1.2-second fadeout on voice audio (line 3897)

### Veo Ambient Audio: `extract_ambient_audio(clip_paths, total_duration)`
- **Lines:** 2635-2681
- **Currently disabled:** `VEO_AMBIENT_VOLUME = 0` (line 217)
- Extracts audio tracks from Veo clips at low volume
- Loops/trims to match video duration

---

## 13. Subtitles & Overlays

### Whisper Transcription
- **Lines:** 3575-3598
- **Model:** whisper `small`
- **Language:** Hindi (`hi`)
- **Word-level timestamps** for accurate sync
- **Fallback:** Equal-duration segments if Whisper fails

### Subtitle Configuration
| Setting | Value | Line |
|---------|-------|------|
| Font | `Noto-Sans-Bold` | 168 |
| Font Size | 62 | 169 |
| Color | white | 170 |
| Stroke | black, width 2 | 171-172 |
| BG Color | (0,0,0) @ 70% opacity | 173-174 |
| BG Padding | 16px | 175 |
| Words per segment | 5 | 176 |
| Position | 50% vertical (center screen) | 3759 |

### Keyword Highlighting
- **`SUBTITLE_HIGHLIGHT_WORDS`** (lines 179-195): 70+ keywords
- Categories: GSM values, fabric terms, printing methods, business terms, product types, manufacturing terms
- Highlighted in **yellow** (`SUBTITLE_HIGHLIGHT_COLOR`, line 178)
- Entire segment turns yellow if any word matches

### Hook Text Overlay
- **Lines:** 3807-3864
- **Duration:** `HOOK_DURATION = 2.0` seconds (line 225)
- **Design:** First word YELLOW (#FFD700, 80px), remaining words WHITE (68px)
- Semi-transparent dark panel (75% opacity) behind text
- Yellow accent bar on left edge (MrBeast-inspired style)
- Crossfadeout at 0.4 seconds

### Watermark Badge
- **Lines:** 3782-3803
- **Text:** `Sale91.com` (line 199)
- **Position:** Left side, 17% from top (avoids YouTube UI)
- **Font size:** 24px, white on black badge @ 70% opacity
- **Padding:** 12px horizontal, 6px vertical

### CTA End Card
- **Lines:** 3867-3891
- **Text:** `Sale91.com — MOQ sirf 10 pieces` (line 232)
- **Duration:** Last 4 seconds of video
- **Design:** Full-width orange-red bar at 80% height, white accent line, crossfadein at 0.4s

---

## 14. Thumbnail Generation

### Function: `generate_thumbnail(hook_text, topic, output_path, veo_clip_path)`
- **Lines:** 540-716
- **Dimensions:** 1080 x 1920 (vertical for Shorts)

### Frame Extraction
- Samples Veo clip at 25%, 40%, 50%, 60% timestamps
- Picks frame with highest contrast (standard deviation of pixel values)
- Darkens to 70% brightness, boosts contrast to 130%
- **Fallback:** Dark gradient background if no clip available

### Layout
1. **Yellow accent bars** — top + left edge (brand consistency)
2. **Hook text** — first word YELLOW (90px, outlined), rest WHITE (72px)
3. **Topic summary** — smaller gray text below hook (36px)
4. **Brand bar** — yellow bar at bottom with "Sale91.com" (42px)
5. **Watch badge** — red "▶ WATCH" badge top-right corner

### Upload: `upload_thumbnail(youtube, video_id, thumbnail_path)`
- **Lines:** 719-730
- Requires YouTube channel verification

---

## 15. YouTube Publishing

### OAuth: `get_youtube_service()`
- **Lines:** 2248-2299
- **Scopes:** upload, readonly, force-ssl (comments + playlists)
- **Token file:** `/tmp/yt_shorts/youtube_token.json`
- **Auto-refresh:** Refreshes expired tokens
- **Scope validation:** Detects missing scopes, deletes stale tokens
- **Fallback:** Tries refresh without enforcing scopes (line 2282)

### Upload: `upload_to_youtube(youtube, video_path, title, description, tags, topic)`
- **Lines:** 2302-2387
- **SEO enhancements:**
  - Auto-appends `#Shorts` to title if not present (line 2307-2309)
  - Dynamic hashtags per topic series (line 2312)
  - Booster tags: `shorts`, `youtubeshorts`, `viral`, `trending` (line 2338-2340)
  - Max 30 tags (line 2341)
- **Description template:** SEO description + Sale91.com link + product details + hashtags
- **Category:** 22 (Entertainment)
- **Language:** Hindi (audio + description)
- **Resumable upload:** 1MB chunks with retry (up to 5 retries, exponential backoff)
- **Privacy:** Private (scheduled) or Public

---

## 16. Publish Time Analytics (A/B Testing)

### Publish Slots
- **`PUBLISH_SLOTS`** (lines 241-245):
  1. 9:30 PM — Post-dinner scroll
  2. 11:00 AM — Chai break / office downtime
  3. 7:00 PM — Commute / pre-dinner scroll

### Weekly Rotation: `PUBLISH_SLOT_SCHEDULE`
- **Lines:** 247-255
- Mon/Thu → 11 AM, Tue/Fri → 7 PM, Wed/Sat/Sun → 9:30 PM

### Analytics Fetch: `fetch_recent_video_analytics(youtube)`
- **Lines:** 2080-2151
- Fetches last 21 videos (3 weeks of daily uploads)
- Maps publish time (IST) to nearest `PUBLISH_SLOT`
- Calculates per-slot: total_views, count, avg_views
- **Storage:** `slot_analytics.json` in `/tmp/yt_shorts/`

### Best Slot Selection: `get_best_publish_slot(youtube)`
- **Lines:** 2153-2202
- **Flow (with 1L threshold):**
  1. If ≥1L views: Own analytics → needs ≥2 slots + 20% variance
  2. Source channel posting patterns (PRIMARY when <1L)
  3. A/B rotation schedule (fallback)

### Publish Time: `get_publish_time(youtube=None)`
- **Lines:** 2220-2245
- Schedules for today or tomorrow based on current IST time
- If today's slot has passed, uses tomorrow's slot

---

## 17. Auto-Pin Comment

### Function: `pin_comment(youtube, video_id, comment_text=None)`
- **Lines:** 733-801
- **Default text** (lines 275-277):
  ```
  📦 Plain t-shirt chahiye printing ke liye?
  👉 Sale91.com pe order karo — MOQ sirf 10 pieces
  🚚 Pan India delivery | 3 lakh+ ready stock
  ```
- **Retry:** 3 attempts with 30s × attempt delay (handles 403/processing errors)
- **Workaround for scheduled videos** (lines 3971-4008):
  1. Switches video to unlisted temporarily
  2. Posts + pins comment
  3. Restores private + scheduled status

---

## 18. Auto-Playlist Organization

### Playlist Config: `SERIES_PLAYLISTS`
- **Lines:** 809-838
- 7 playlists matching 7 topic series
- Each has branded title + Hindi/English description

### Function: `add_to_playlist(youtube, video_id, topic)`
- **Lines:** 871-929
- **Process:**
  1. Detect series via `_detect_series()` (keyword matching)
  2. Check playlist cache (`playlist_cache.json`)
  3. Search existing playlists if not cached
  4. Create new playlist if not found
  5. Add video to playlist

---

## 19. Instagram Reels Cross-Post

### Function: `cross_post_to_instagram(video_path, title, description, topic)`
- **Lines:** 1025-1234
- **API:** Instagram Graph API v21.0
- **Env vars:** `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_BUSINESS_ID`

### Token Validation
- Pre-flight `/me` check (line 1048)
- Error code 190 = expired token (line 1058)
- Subcode 463 = token needs regeneration

### Video Hosting (3-host fallback chain)
1. **0x0.st** — up to 512MB, direct URL, no account
2. **litterbox.catbox.moe** — up to 1GB, 72h expiry
3. **file.io** — 100MB limit, fallback

### Best Time Detection: `get_instagram_best_time(ig_token, ig_business_id)`
- **Lines:** 936-1022
- **API:** Instagram Insights (`online_followers` metric)
- Returns hourly follower activity for today's weekday
- Picks best future hour (at least 15 min from now)
- Falls back to tomorrow's best hour if all peaks passed

### Publishing Flow
1. Create media container (REELS, `media_type: "REELS"`)
2. Wait for processing (30s × 20 polls = max 10 min)
3. Publish immediately or schedule for peak time
- **Caption:** Title + first description line + 10 hashtags + Sale91.com

---

## 20. Cost Tracking & Budget Control

### Class: `CostTracker`
- **Lines:** 1256-1364

### Cost Rates: `COST_RATES`
- **Lines:** 1242-1253

| Service | Rate | Unit |
|---------|------|------|
| Claude Sonnet input | $0.003 | per 1K tokens |
| Claude Sonnet output | $0.015 | per 1K tokens |
| Claude Opus input | $0.015 | per 1K tokens |
| Claude Opus output | $0.075 | per 1K tokens |
| OpenAI TTS | $0.000015 | per char |
| ElevenLabs TTS | $0.00003 | per char |
| Veo clip | $0.50 | per 8s clip |
| Kling clip | $0.35 | per 5s clip |
| Replicate ACE-Step | $0.05 | per generation |
| Whisper | $0.006 | per minute |

### Methods
- `track_claude_call(model, input_tokens, output_tokens)` — Auto-detects Opus vs Sonnet
- `track_tts(provider, char_count)` — ElevenLabs or OpenAI
- `track_veo(num_clips)` / `track_kling(num_clips)`
- `track_replicate()` / `track_whisper(duration_sec)`
- `summary()` — Prints formatted cost breakdown + duration
- `save(topic, title)` — Appends to `cost_tracker.json`

### Daily Circuit Breaker: `CostTracker.check_daily_limit()`
- **Lines:** 1348-1364
- **Limit:** `DAILY_COST_LIMIT_USD = 10.0` (line 288)
- Sums today's entries from `cost_tracker.json`
- Skips video generation if limit exceeded

---

## 21. GitHub Actions Automation

### Daily Short Workflow: `.github/workflows/daily_short.yml`
- **Schedule:** `cron: '0 12 * * *'` (12 PM UTC daily)
- **Manual dispatch:** 3 test mode options (test_mode, skip_clips, single_veo_test)
- **Concurrency:** Single-threaded, cancel-in-progress
- **Timeout:** 75 minutes
- **Python:** 3.12
- **System deps:** ImageMagick, FFmpeg, Noto fonts
- **Steps:**
  1. Checkout repo
  2. Setup Python + system deps
  3. Install pip packages
  4. Restore YouTube OAuth credentials from secrets
  5. Run `daily_short.py` with all env vars
  6. Git commit + push updated tracking files (always, even on failure)
  7. Upload test video artifact (test mode only, 3-day retention)
  8. Save failed upload artifact (always, for retry workflow)
  9. Create GitHub issue on failure (with dedup — comments on existing open issue)

### Retry Upload Workflow: `.github/workflows/retry_upload.yml`
- **Trigger:** Manual dispatch with `run_id` parameter
- **Timeout:** 10 minutes
- Downloads failed video artifact from specified run
- Re-runs `retry_upload.py` with YouTube credentials

### Git Auto-Commit (line 85-97)
- Tracks: `topic_history.json`, `cost_tracker.json`, `engagement_history.json`, `clip_history.json`, `source_channel_insights.json`
- Commit message: `[skip ci]` to prevent infinite loop
- Pulls with rebase before push

### Failure Notification (lines 121-143)
- Creates GitHub issue with `bot-failure` label
- If open issue exists, adds comment instead
- Includes run URL, trigger type, and troubleshooting hints

---

## 22. Data Persistence (JSON Files)

| File | Location | Git Tracked | Purpose | Schema |
|------|----------|-------------|---------|--------|
| `topic_history.json` | Repo root | Yes | All used topics (dedup) | `["topic1", "topic2", ...]` |
| `clip_history.json` | Repo root | Yes | Last 30 videos' Veo prompts | `[{topic, prompts[], date}]` |
| `cost_tracker.json` | Repo root | Yes | Per-run API costs | `[{date, topic, title, total_usd, duration_min, breakdown{}}]` |
| `engagement_history.json` | Repo root | Yes | 48h video performance | `[{video_id, title, views, likes, comments, publish_hour_ist}]` |
| `source_channel_insights.json` | Repo root | Yes | Main channel data + comments (24h cache) | `{fetched_at, channel_id, videos[], top_comments[]}` |
| `playlist_cache.json` | `/tmp/yt_shorts/` | No | Series → Playlist ID map | `{series_name: playlist_id}` |
| `slot_analytics.json` | `/tmp/yt_shorts/` | No | Publish time performance | `{last_updated, slots{label: {total_views, count, avg_views}}}` |

---

## 23. Feature Dashboard

### Startup Dashboard
- **Lines:** 3101-3164
- Prints 50+ line feature matrix on every run
- Sections: Mode, Content Intelligence, Video Generation, Audio, Overlays, Publishing, Safety
- Shows ON/OFF status for each feature + key settings

---

## 24. Dependencies

### Python Packages (`requirements.txt`)
```
anthropic          — Claude API
openai             — OpenAI TTS + Whisper
elevenlabs         — ElevenLabs TTS
moviepy==1.0.3     — Video editing
Pillow             — Image processing (thumbnails)
openai-whisper     — Speech-to-text (subtitles)
google-auth-oauthlib — YouTube OAuth
google-api-python-client — YouTube Data API v3
google-genai       — Google Veo 3.1
pytz               — Timezone handling
replicate          — ACE-Step music generation
fal-client         — Kling video fallback
```

### System Dependencies (installed in GitHub Actions)
- `imagemagick` — TextClip rendering for MoviePy
- `ffmpeg` — Audio normalization, video encoding
- `fonts-noto-core` — Noto Sans Bold font for subtitles

---

## 25. Environment Variables

### Required
| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude (script gen, topic review, title optimization) |
| `OPENAI_API_KEY` | OpenAI TTS fallback + Whisper subtitles |
| `GOOGLE_API_KEY` | Google Veo 3.1 video generation |

### Optional (enable features)
| Variable | Feature |
|----------|---------|
| `ELEVENLABS_API_KEY` | ElevenLabs TTS (primary voice) |
| `REPLICATE_API_TOKEN` | AI background music (ACE-Step) |
| `FAL_KEY` | Kling video fallback (fal.ai) |
| `INSTAGRAM_ACCESS_TOKEN` | Instagram Reels cross-post |
| `INSTAGRAM_BUSINESS_ID` | Instagram account ID |
| `YOUTUBE_API_KEY_1` | Source channel API key (read-only) |
| `CHANNEL_ID_2` | Source channel ID (50K subs) |

### YouTube OAuth (stored as GitHub secrets)
| Secret | File |
|--------|------|
| `YOUTUBE_TOKEN_JSON` | `/tmp/yt_shorts/youtube_token.json` |
| `CLIENT_SECRET_JSON` | `/tmp/yt_shorts/client_secret.json` |

### Test Mode Flags
| Variable | Purpose |
|----------|---------|
| `TEST_MODE=1` | No Veo, no upload (free run) |
| `SKIP_CLIPS=1` | Placeholder clips, full upload |
| `SINGLE_VEO_TEST=1` | 1 real clip + 4 blank, full upload |

---

## Pipeline Flow (Main Execution)

```
main() — Lines 3091-4064

 1. Feature Dashboard (print status matrix)
 2. API key validation (Anthropic, OpenAI, Google)
 3. Cost circuit breaker (check daily $10 limit)
 4. Fetch source channel insights (24h cache)
 5. Smart topic selection (bank → trending → fallback)
 6. Script generation (up to 3 attempts with quality gate)
 7. Title optimization (A/B variants)
 8. Script post-processing (sanitize for TTS)
 9. Save Veo prompts to clip history
10. Generate background music (ACE-Step)
11. Generate voice (ElevenLabs → OpenAI fallback)
12. Normalize audio (-16 LUFS)
13. Generate video clips (Veo → Kling fallback)
14. Whisper transcription (word-level timestamps)
15. Video assembly:
    a. Smart crop (9:16)
    b. Black intro trim
    c. Ken Burns zoom
    d. Clip looping / crossfade
    e. Subtitle overlays (keyword highlighting)
    f. Watermark badge
    g. Hook text overlay
    h. CTA end card
16. Audio mixing (voice + BG music + hook SFX + ambient)
17. Render (H.264, 8000k bitrate)
18. Generate thumbnail (Veo frame + brand overlay)
19. YouTube upload (scheduled publish)
20. Check past engagement (48h feedback loop)
21. Upload thumbnail
22. Pin CTA comment (unlisted → comment → restore schedule)
23. Add to series playlist
24. Instagram Reels cross-post (with best-time scheduling)
25. Cost summary + save
26. Cleanup temp files
```
