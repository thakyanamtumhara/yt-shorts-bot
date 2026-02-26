#!/usr/bin/env python3
"""
Standalone Instagram Reel publish test — zero dependency on daily_short.py.

Usage:
    python test_instagram.py              # auto-generates 5-sec test video
    python test_instagram.py /path/to.mp4 # uses your video

Requires env vars:
    INSTAGRAM_ACCESS_TOKEN
    INSTAGRAM_BUSINESS_ID
"""
import os
import sys
import time
import subprocess
import requests

IG_API_VERSION = "v21.0"

# ── Ensure env vars ──
ig_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
ig_biz_id = os.environ.get("INSTAGRAM_BUSINESS_ID", "").strip()

if not ig_token or not ig_biz_id:
    print("❌ Set these env vars first:")
    print("   INSTAGRAM_ACCESS_TOKEN")
    print("   INSTAGRAM_BUSINESS_ID")
    sys.exit(1)


# ── Generate or use provided test video ──
def make_test_video(path="/tmp/ig_test_video.mp4"):
    """Create a proper 5-sec Reel-quality MP4 with ffmpeg.

    Instagram rejects tiny/static videos (classifies as carousel VIDEO
    instead of REELS). We add visual movement + proper bitrate.
    """
    if os.path.isfile(path) and os.path.getsize(path) > 100000:
        print(f"📹 Reusing existing test video: {path}")
        return path

    print("📹 Generating 5-sec test video (Reel-quality)...")
    # Instagram needs: H.264 Baseline/Main, AAC audio, 1080x1920, 3-60s,
    # proper bitrate (not ultra-low), visual motion (not static frame).
    # Key fixes vs previous attempts:
    #   - 'hue=H=2*PI*t/5' adds color animation (prevents static-frame detection)
    #   - '-b:v 2M' ensures proper bitrate (91KB was too small, got carousel error)
    #   - '-profile:v main -level 4.0' for max Instagram compatibility
    cmds = [
        # Attempt 1: H.264 Main profile + animated color + sine audio
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            "color=c=blue:size=1080x1920:rate=30:duration=5,hue=H=2*PI*t/5:s=3",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=5",
            "-c:v", "libx264", "-profile:v", "main", "-level", "4.0",
            "-preset", "medium", "-b:v", "2M", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart", path,
        ],
        # Attempt 2: simpler animation (if hue filter unavailable)
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            "gradients=size=1080x1920:rate=30:duration=5:speed=1",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=5",
            "-c:v", "libx264", "-preset", "medium", "-b:v", "2M",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart", path,
        ],
        # Attempt 3: basic color + higher bitrate (most compatible)
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            "color=c=blue:size=1080x1920:rate=30:duration=5",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=5",
            "-c:v", "libx264", "-preset", "medium", "-b:v", "2M",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart", path,
        ],
    ]

    for i, cmd in enumerate(cmds):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and os.path.isfile(path) and os.path.getsize(path) > 50000:
            size_kb = os.path.getsize(path) / 1024
            print(f"📹 Test video created (attempt {i+1}): {path} ({size_kb:.0f} KB)")
            # Verify with ffprobe
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "stream=codec_name,width,height,duration,bit_rate",
                 "-of", "compact", path],
                capture_output=True, text=True,
            )
            if probe.stdout:
                print(f"   🔍 {probe.stdout.strip()}")
            return path
        print(f"   ⚠️ Attempt {i+1} failed: {result.stderr[-300:]}")

    print("❌ All ffmpeg attempts failed")
    sys.exit(1)


# ── Upload video to public URL ──
BLOG_S3_BUCKET = "bulkplaintshirt.com"
BLOG_CLOUDFRONT_DIST_ID = "E21QLU9SBUBY7Z"
# Use p/ prefix — IAM user catalogfromgithub only has write access to p/*
S3_TEST_KEY = "p/tmp-ig-test.mp4"


def upload_to_public_host(video_path):
    """Upload video to a publicly accessible URL. Returns URL or None."""
    file_mb = os.path.getsize(video_path) / (1024 * 1024)

    # Primary: S3 + CloudFront (Instagram can always reach this)
    try:
        import boto3
        print(f"   📤 Uploading to S3 ({file_mb:.1f} MB)...")
        s3 = boto3.client("s3")
        s3.upload_file(
            video_path,
            BLOG_S3_BUCKET,
            S3_TEST_KEY,
            ExtraArgs={"ContentType": "video/mp4", "CacheControl": "no-cache"},
        )
        url = f"https://www.bulkplaintshirt.com/{S3_TEST_KEY}"
        print(f"   ✅ Hosted at: {url}")
        return url
    except Exception as e:
        print(f"   ⚠️ S3 upload failed: {e}")

    # Fallback: litterbox.catbox.moe
    try:
        print(f"   📤 Trying litterbox.catbox.moe...")
        with open(video_path, "rb") as f:
            resp = requests.post(
                "https://litterbox.catbox.moe/resources/internals/api.php",
                data={"reqtype": "fileupload", "time": "72h"},
                files={"fileToUpload": (os.path.basename(video_path), f, "video/mp4")},
                timeout=300,
            )
        if resp.status_code == 200 and resp.text.strip().startswith("http"):
            url = resp.text.strip()
            print(f"   ✅ Hosted at: {url}")
            return url
        print(f"   ⚠️ litterbox failed ({resp.status_code}): {resp.text[:100]}")
    except Exception as e:
        print(f"   ⚠️ litterbox error: {e}")

    return None


# ── Instagram publish flow ──
def test_instagram_publish(video_path):
    """Full Instagram Reel publish test."""

    # Step 1: Verify token
    print(f"\n🔑 Verifying token (len={len(ig_token)}, prefix={ig_token[:6]}...)...")
    me_resp = requests.get(
        f"https://graph.facebook.com/{IG_API_VERSION}/{ig_biz_id}",
        params={"fields": "id,name,username", "access_token": ig_token},
        timeout=10,
    )
    if me_resp.status_code != 200:
        err = me_resp.json().get("error", {})
        print(f"❌ Token invalid (code {err.get('code')}): {err.get('message', me_resp.text[:150])}")
        if err.get("code") == 190:
            print("   🔑 Token EXPIRED — refresh at developers.facebook.com/tools/explorer/")
        return False
    ig_info = me_resp.json()
    print(f"✅ Token valid — account: {ig_info.get('name', ig_info.get('username', ig_info.get('id')))}")

    # Step 2: Upload to public host
    public_url = upload_to_public_host(video_path)
    if not public_url:
        print("❌ All upload hosts failed")
        return False

    # Step 3: Create container (REELS — no share_to_feed, it causes carousel error)
    print(f"\n📦 Creating Reel container ({IG_API_VERSION})...")
    caption = "Test Reel — Sale91 Plain T-Shirts 🧵\n\n#Sale91 #PlainTshirt #Test"
    container_resp = requests.post(
        f"https://graph.facebook.com/{IG_API_VERSION}/{ig_biz_id}/media",
        data={
            "media_type": "REELS",
            "video_url": public_url,
            "caption": caption,
            "access_token": ig_token,
        },
        timeout=30,
    )
    if container_resp.status_code != 200:
        print(f"❌ Container creation failed: {container_resp.text[:300]}")
        return False

    container_id = container_resp.json().get("id")
    print(f"✅ Container created: {container_id}")

    # Step 4: Wait for processing
    print("\n⏳ Waiting for Instagram to process video...")
    for check in range(20):
        time.sleep(30)
        status_resp = requests.get(
            f"https://graph.facebook.com/{IG_API_VERSION}/{container_id}",
            params={"fields": "status_code,status", "access_token": ig_token},
            timeout=15,
        )
        status_data = status_resp.json()
        status_code = status_data.get("status_code", "")

        if status_code == "FINISHED":
            print(f"✅ Processing complete!")
            break
        elif status_code == "ERROR":
            print(f"❌ Processing failed: {status_data.get('status', {})}")
            return False
        print(f"   ⏳ Processing... ({check+1}/20, status={status_code})")
    else:
        print("❌ Processing timed out after 10 minutes")
        return False

    # Step 4b: Verify container type (must be REELS, not VIDEO)
    try:
        verify_resp = requests.get(
            f"https://graph.facebook.com/{IG_API_VERSION}/{container_id}",
            params={"fields": "status_code,media_type,media_product_type", "access_token": ig_token},
            timeout=15,
        )
        vd = verify_resp.json()
        m_type = vd.get('media_type', '?')
        m_product = vd.get('media_product_type', '?')
        print(f"🔍 Container: media_type={m_type}, product={m_product}")
        if m_type == "VIDEO" and m_product != "REELS":
            print("⚠️ Instagram classified as VIDEO (not REELS) — publish may fail with carousel error")
    except Exception:
        pass

    # Step 5: Publish
    print(f"\n🚀 Publishing ({IG_API_VERSION})...")
    pub_resp = requests.post(
        f"https://graph.facebook.com/{IG_API_VERSION}/{ig_biz_id}/media_publish",
        data={"creation_id": container_id, "access_token": ig_token},
        timeout=30,
    )
    if pub_resp.status_code == 200:
        media_id = pub_resp.json().get("id")
        print(f"✅ PUBLISHED! Instagram media ID: {media_id}")
        return True

    error_text = pub_resp.text[:500]
    print(f"❌ Publish failed: {error_text}")

    # Diagnose common errors
    if "2207089" in error_text or "carousel" in error_text.lower():
        print("\n📋 DIAGNOSIS: Carousel error (2207089)")
        print("   This means Instagram classified the video as VIDEO instead of REELS.")
        print("   Possible causes:")
        print("   1. Video URL host not fully supported (litterbox/catbox)")
        print("   2. Video too small/static (Instagram needs visual motion)")
        print("   3. API version issue")
        print("   Fix: ensure S3 upload works (check IAM permissions)")
    elif "190" in error_text or "expired" in error_text.lower():
        print("\n📋 DIAGNOSIS: Token expired — refresh at developers.facebook.com/tools/explorer/")

    return False


# ── Main ──
if __name__ == "__main__":
    print("=" * 60)
    print("🧪 Instagram Reel Publish Test (standalone)")
    print("=" * 60)

    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        video_path = sys.argv[1]
        print(f"📹 Using provided video: {video_path}")
    else:
        video_path = make_test_video()

    file_mb = os.path.getsize(video_path) / (1024 * 1024)
    print(f"📹 Video: {video_path} ({file_mb:.1f} MB)")

    print("\n" + "=" * 60)
    success = test_instagram_publish(video_path)
    print("=" * 60)

    if success:
        print("✅ TEST PASSED")
    else:
        print("❌ TEST FAILED")
        sys.exit(1)
