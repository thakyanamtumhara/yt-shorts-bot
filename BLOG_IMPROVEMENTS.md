# Blog SEO + AI-Search Improvement Proposals
**Reference run**: 2026-05-06 daily cron (in progress at time of analysis)
**Code analysed**: `daily_short.py` — `inject_blog_seo()`, `get_blog_prompt()`, `generate_blog_post()`, `build_sitemap_xml()`, `publish_blog_to_s3()`

---

## Summary of Current State

Already implemented ✅:
- Article, Organization, BreadcrumbList, Product, FAQPage JSON-LD schemas
- Open Graph tags (title, description, url, image, type=article)
- Twitter card meta tags
- FAQ section with faq-question/faq-answer CSS classes
- Internal linking (last 5 related posts)
- AI-generated images with lazy loading + alt text
- H1/H2/H3 hierarchy (in Claude prompt spec)
- Mobile-responsive layout (viewport meta, 800px max-width)
- Sitemap at `/p/map.xml` rebuilt daily
- RSS feed (`/p/feed.xml`)
- `llms.txt` at `/p/llms.txt`
- IndexNow (Bing/Yandex/AI search)
- Google Indexing API
- CloudFront invalidation on publish
- Canonical URL, breadcrumb nav

---

## Ranked Improvement Plan

---

### #1 — Remove / replace fabricated Product aggregateRating
**Impact**: 🔴 Critical — Google Trust  
**Effort**: S  
**File**: `daily_short.py:4646–4651`

The `Product` schema has hardcoded `ratingValue: 4.5, reviewCount: 1050`. These are invented numbers that can trigger a Google Manual Action for fake structured data, harming all 70+ blog posts.

**Fix**:
```python
# In inject_blog_seo(), replace product_ld with:
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
    # aggregateRating REMOVED — add back only when backed by verified reviews
}
```
**Expected impact**: Removes fake-review risk. Preserves price/availability signals Google uses for B2B product pages.

---

### #2 — Fix llms.txt: root path + proper markdown format
**Impact**: 🟠 High — AI search (Perplexity, ChatGPT/Bing, Claude)  
**Effort**: S  
**File**: `daily_short.py:5887–5907`

Current: stored at `/p/llms.txt` with format `Title: URL\n`. AI crawlers (Perplexity, Claude search) look for `/llms.txt` at root and expect the [llms.txt spec](https://llmstxt.org) markdown format.

**Fix** (in `publish_blog_to_s3`):
```python
# ── 4. Update root /llms.txt (AI crawler standard) ──
try:
    try:
        resp = s3.get_object(Bucket=BLOG_S3_BUCKET, Key='llms.txt')
        llms_content = resp['Body'].read().decode('utf-8')
    except s3.exceptions.NoSuchKey:
        llms_content = (
            "# BulkPlainTshirt.com\n"
            "> B2B plain t-shirt manufacturer & wholesale supplier. "
            "Own knitted blank wears from Tiruppur, India. Visit: https://sale91.com\n\n"
            "## Blog Posts\n"
        )
    # Prepend new post to keep newest first
    new_line = f"- [{title}]({blog_url}): {description[:120] if description else topic}\n"
    # Insert after "## Blog Posts\n"
    if "## Blog Posts\n" in llms_content:
        llms_content = llms_content.replace(
            "## Blog Posts\n",
            f"## Blog Posts\n{new_line}"
        )
    else:
        llms_content += new_line

    s3.put_object(
        Bucket=BLOG_S3_BUCKET,
        Key='llms.txt',          # ROOT — not /p/llms.txt
        Body=llms_content.encode('utf-8'),
        ContentType='text/plain; charset=utf-8',
        CacheControl='no-cache'
    )
    invalidation_paths.append('/llms.txt')
except Exception as e:
    print(f"   ⚠️ Blog S3: Could not update root llms.txt: {e}")
```
**Expected impact**: Perplexity, Claude search, and ChatGPT now discover and cite your posts. Compound value as post count grows.

---

### #3 — Add missing Article schema fields: keywords, inLanguage, wordCount, articleSection
**Impact**: 🟠 High — Google + AI search content understanding  
**Effort**: S  
**File**: `daily_short.py:4612–4628`

Google's structured data guidelines and AI search engines use `keywords`, `inLanguage`, `wordCount`, and `articleSection` to categorize and surface content. All are missing.

**Fix** (in `inject_blog_seo()`, extend `article_ld`):
```python
# Derive articleSection from existing series-detection logic
def _get_article_section(title_and_desc: str) -> str:
    t = title_and_desc.lower()
    if any(k in t for k in ["gsm", "fabric", "cotton", "yarn", "knit"]): return "Fabric Knowledge"
    if any(k in t for k in ["dtg", "dtf", "screen print", "sublimation"]): return "Printing Techniques"
    if any(k in t for k in ["customer", "client", "return", "order"]): return "Customer Stories"
    if any(k in t for k in ["pricing", "margin", "profit", "business"]): return "Business Tips"
    return "Plain T-Shirt Guide"

article_ld = {
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": title,
    "description": description[:160] if description else title,
    "url": blog_url,
    "datePublished": today,
    "dateModified": today,
    "inLanguage": "en-IN",                          # ← ADD
    "wordCount": len(html_content.split()),         # ← ADD (approx)
    "articleSection": _get_article_section(f"{title} {description}"),  # ← ADD
    "keywords": ", ".join(tags[:8]) if tags else "plain tshirts, bulk wholesale, GSM, printing business",  # ← ADD
    "author": {
        "@type": "Person",                          # ← CHANGE from Organization
        "name": "Ankit",
        "url": "https://sale91.com",
        "worksFor": {"@type": "Organization", "name": "Sale91.com"}
    },
    "publisher": {
        "@type": "Organization",
        "name": "BulkPlainTshirt.com",
        "logo": {"@type": "ImageObject", "url": "https://www.bulkplaintshirt.com/catalog/img/logo.png"}
    },
    "image": og_image_url or "https://www.bulkplaintshirt.com/catalog/img/logo.png",
    "mainEntityOfPage": {"@type": "WebPage", "@id": blog_url}
}
```
**Expected impact**: Better content categorization in Google Discover. `Person` author type improves E-E-A-T signals (Google's experience/expertise/authoritativeness/trust framework). `inLanguage: en-IN` improves India-regional targeting.

---

### #4 — Add missing Open Graph article tags + og:locale + og:site_name
**Impact**: 🟡 Medium — LinkedIn/WhatsApp/Facebook sharing (key B2B channels)  
**Effort**: S  
**File**: `daily_short.py:4848–4853` (get_blog_prompt OG section)

Missing: `og:locale`, `og:site_name`, `article:published_time`, `article:tag`. The B2B target audience shares on WhatsApp and LinkedIn, which parse these tags for rich previews.

**Fix** (add to the Claude prompt's META TAGS requirement, and also inject programmatically in `inject_blog_seo` since Claude may miss them):
```python
# In inject_blog_seo(), after building ld_scripts, inject missing OG tags:
og_extras = f'''
    <meta property="og:locale" content="en_IN" />
    <meta property="og:site_name" content="BulkPlainTshirt.com" />
    <meta property="article:published_time" content="{today}T00:00:00+05:30" />
    <meta property="article:author" content="https://sale91.com" />
'''
if tags:
    for tag in tags[:5]:
        og_extras += f'    <meta property="article:tag" content="{tag}" />\n'

if '</head>' in html_content:
    html_content = html_content.replace('</head>', f'{og_extras}</head>', 1)
```
**Expected impact**: Rich link previews on WhatsApp Business (critical for Indian B2B) and LinkedIn. `article:tag` improves Open Graph classification.

---

### #5 — Add `<link rel="preload">` for hero image in inject_blog_seo
**Impact**: 🟡 Medium — Core Web Vitals (LCP = ranking signal)  
**Effort**: S  
**File**: `daily_short.py:4697–4701`

The hero image is the Largest Contentful Paint element. Without `rel="preload"` the browser discovers it late (after parsing HTML → CSS → rendering). This hurts LCP score which is a direct Google ranking factor.

**Fix** (in `inject_blog_seo()`, inject preload if `og_image_url` is set):
```python
# Insert BEFORE existing JSON-LD injection (step 5)
if og_image_url and '</head>' in html_content:
    preload_tag = f'<link rel="preload" as="image" href="{og_image_url}" fetchpriority="high">\n'
    html_content = html_content.replace('</head>', f'{preload_tag}</head>', 1)
```
**Expected impact**: ~15-30% improvement in LCP on mobile (Lighthouse). Google PageSpeed Insights will stop flagging the hero image as render-blocking.

---

### #6 — Add robots.txt with Sitemap reference
**Impact**: 🟡 Medium — Crawl efficiency for all search engines  
**Effort**: S  
**File**: New code in `publish_blog_to_s3()` or one-time `repair_sitemap()`

The code generates `p/map.xml` but no `robots.txt` references it. Googlebot and AI crawlers look for Sitemap in robots.txt first (it's the most reliable discovery path).

**Fix** (add after sitemap upload in `repair_sitemap()`):
```python
def ensure_robots_txt(s3_client):
    """Upload robots.txt with Sitemap reference if not already present."""
    try:
        try:
            s3_client.get_object(Bucket=BLOG_S3_BUCKET, Key='robots.txt')
            return  # Already exists — don't overwrite (may have custom rules)
        except s3_client.exceptions.NoSuchKey:
            pass
        robots_content = (
            "User-agent: *\n"
            "Allow: /\n"
            "\n"
            "Sitemap: https://www.bulkplaintshirt.com/p/map.xml\n"
            "Sitemap: https://www.bulkplaintshirt.com/p/feed.xml\n"
        )
        s3_client.put_object(
            Bucket=BLOG_S3_BUCKET,
            Key='robots.txt',
            Body=robots_content.encode('utf-8'),
            ContentType='text/plain',
            CacheControl='public, max-age=86400'
        )
        print("   🤖 robots.txt: Created with Sitemap reference")
    except Exception as e:
        print(f"   ⚠️ robots.txt: Could not create: {e}")
```
**Expected impact**: Faster sitemap discovery by Googlebot, Bingbot, and AI search crawlers. Particularly important for Perplexity which follows robots.txt Sitemap directives.

---

### #7 — Upgrade blog Claude model from sonnet-4-5 to sonnet-4-6
**Impact**: 🟡 Medium — Blog HTML quality + fewer FAQ extraction failures  
**Effort**: S  
**File**: `daily_short.py:5031`

Current: `model="claude-sonnet-4-5-20250929"` (retired soon). Sonnet 4.6 produces tighter HTML, better follows the faq-question/faq-answer class spec, and handles the long blog prompt more reliably.

**Fix**:
```python
# daily_short.py:5031
resp = claude_client.messages.create(
    model="claude-sonnet-4-6",          # was: claude-sonnet-4-5-20250929
    max_tokens=16000,
    messages=[{"role": "user", "content": prompt}]
)
```
**Expected impact**: Better FAQ extraction hit rate (currently 0 FAQs = no FAQPage schema). Cleaner HTML = faster parse by search engines. No cost change ($3/M input, $15/M output same as sonnet-4-5).

---

### #8 — Add FAQ count validation warning
**Impact**: 🟡 Medium — Silent failure detection  
**Effort**: S  
**File**: `daily_short.py:4707`

When Claude's HTML uses slightly different class names (e.g. `faq-q` instead of `faq-question`), the regex silently extracts 0 FAQs, producing no FAQPage schema. This happens more often than expected with long prompts.

**Fix** (in `inject_blog_seo()` after FAQ extraction):
```python
if not faq_pairs:
    print(f"   ⚠️ Blog SEO: 0 FAQs extracted — Claude may not have used 'faq-question'/'faq-answer' classes")
    print(f"   ⚠️ Blog SEO: FAQPage schema will NOT be injected. Check HTML output.")
else:
    print(f"   ✅ Blog SEO: Extracted {len(faq_pairs)} FAQs for FAQPage schema")
```
Additionally, add explicit CSS class validation in the prompt:
```
CRITICAL: FAQ items MUST use EXACTLY these CSS classes or FAQPage schema will fail:
  <div class="faq-question">Question text here</div>
  <div class="faq-answer">Answer text here</div>
```
**Expected impact**: Faster detection when FAQPage schema is missing. FAQs in search results (rich snippets) can 2-3× CTR for "how to" queries.

---

### #9 — Change blog HTML Cache-Control for better freshness
**Impact**: 🟢 Low — Browser cache correctness  
**Effort**: S  
**File**: `daily_short.py:5830`

Current: `CacheControl='public, max-age=86400'` — browsers cache blog HTML for 24h. If a post is updated (slug collision, repair), users see stale content. CloudFront invalidation only clears the CDN edge, not browser caches.

**Fix**:
```python
s3.put_object(
    Bucket=BLOG_S3_BUCKET,
    Key=blog_key,
    Body=html_content.encode('utf-8'),
    ContentType='text/html; charset=utf-8',
    CacheControl='no-cache, s-maxage=86400'  # CDN: 24h | Browser: always revalidate
)
```
**Expected impact**: Zero browser-stale-cache issues. CDN still serves fast from edge with 24h TTL. Negligible origin traffic increase (browsers send conditional GET, CDN responds with 304 if unchanged).

---

### #10 — Add `<link rel="sitemap">` to each blog post head
**Impact**: 🟢 Low — Crawl depth improvement  
**Effort**: S  
**File**: `daily_short.py:4697` (inject_blog_seo)

Googlebot and AI crawlers discovering a post directly (e.g. via social share) can find the full sitemap through this tag without needing to visit the home page first.

**Fix** (add to `inject_blog_seo` injection):
```python
sitemap_link = '<link rel="sitemap" type="application/xml" title="Sitemap" href="/p/map.xml">\n'
if '</head>' in html_content:
    html_content = html_content.replace('</head>', f'{sitemap_link}</head>', 1)
```
**Expected impact**: Minor improvement in crawl graph coverage. Ensures every blog post, when found standalone, helps crawlers find all 70+ posts.

---

## Quick Reference

| # | Change | Impact | Effort | File:Line |
|---|--------|--------|--------|-----------|
| 1 | Remove fabricated aggregateRating | 🔴 Critical | S | 4646–4651 |
| 2 | Fix llms.txt path + format | 🟠 High | S | 5887–5907 |
| 3 | Article schema: keywords/inLanguage/wordCount/articleSection + Person author | 🟠 High | S | 4612–4628 |
| 4 | OG tags: locale/site_name/article:published_time/article:tag | 🟡 Medium | S | 4848–4853 |
| 5 | `<link rel="preload">` for hero image | 🟡 Medium | S | 4697–4701 |
| 6 | robots.txt with Sitemap reference | 🟡 Medium | S | new code |
| 7 | Upgrade blog model to claude-sonnet-4-6 | 🟡 Medium | S | 5031 |
| 8 | FAQ count validation warning | 🟡 Medium | S | 4707 |
| 9 | Blog HTML Cache-Control: no-cache + s-maxage | 🟢 Low | S | 5830 |
| 10 | `<link rel="sitemap">` in each post head | 🟢 Low | S | 4697 |

Reply `apply 1,3` or `apply all` to ship any of these.
