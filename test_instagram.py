#!/usr/bin/env python3
"""
Standalone Instagram Reel publish test — zero dependency on daily_short.py.

Usage:
    python test_instagram.py                  # full test: generate video + S3 upload + IG publish
    python test_instagram.py --upload-only    # only test S3 upload (no IG publish)
    python test_instagram.py /path/to.mp4     # use your video instead of auto-generated

Requires env vars:
    INSTAGRAM_ACCESS_TOKEN
    INSTAGRAM_BUSINESS_ID
    AWS credentials (for S3 upload — same IAM as blog)
"""
import os
import sys
import time
import subprocess
import requests

IG_API_VERSION = "v21.0"
BLOG_S3_BUCKET = "bulkplaintshirt.com"
BLOG_BASE_URL = "https://www.bulkplaintshirt.com"

# ── Ensure env vars ──
ig_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
ig_biz_id = os.environ.get("INSTAGRAM_BUSINESS_ID", "").strip()

upload_only = "--upload-only" in sys.argv


# ── Generate or use provided test video ──
def make_test_video(path="/tmp/ig_test_video.mp4"):
    """Create a realistic ~55MB Reel-quality MP4 with ffmpeg.

    Real daily_short.py videos are 30-60 sec, ~50-60MB.
    This generates a similar-sized video to properly test S3 upload.
      - 30 seconds @ 15Mbps = ~55MB
      - 1080x1920 vertical (Reel format)
      - H.264 Main profile
      - Color animation (not static — Instagram rejects static frames)
      - AAC audio
    """
    # Always regenerate to ensure proper quality
    if os.path.isfile(path):
        os.remove(path)

    target_mb = 55
    duration = 30  # seconds
    # bitrate = target_size / duration → 55MB / 30s ≈ 15 Mbps
    bitrate = f"{int(target_mb * 8 / duration)}M"
    print(f"📹 Generating {duration}-sec test video (~{target_mb}MB, {bitrate}bps)...")

    cmds = [
        # Attempt 1: H.264 Main + hue animation + text overlay + sine audio
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            f"color=c=0x1a5276:size=1080x1920:rate=30:duration={duration},"
            f"hue=H=2*PI*t/5:s=3,"
            f"drawtext=text='Sale91 Test Reel':fontsize=60:fontcolor=white:"
            f"x=(w-text_w)/2:y=(h-text_h)/2",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={duration}",
            "-c:v", "libx264", "-profile:v", "main", "-level", "4.0",
            "-preset", "medium", "-b:v", bitrate, "-maxrate", "18M", "-bufsize", "20M",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart", path,
        ],
        # Attempt 2: without drawtext (if font not available)
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            f"color=c=0x1a5276:size=1080x1920:rate=30:duration={duration},"
            f"hue=H=2*PI*t/5:s=3",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={duration}",
            "-c:v", "libx264", "-profile:v", "main", "-level", "4.0",
            "-preset", "medium", "-b:v", bitrate, "-maxrate", "18M", "-bufsize", "20M",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart", path,
        ],
        # Attempt 3: basic color + high bitrate (most compatible ffmpeg)
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            f"color=c=blue:size=1080x1920:rate=30:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={duration}",
            "-c:v", "libx264", "-preset", "medium", "-b:v", bitrate,
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart", path,
        ],
    ]

    for i, cmd in enumerate(cmds):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and os.path.isfile(path) and os.path.getsize(path) > 100000:
            size_mb = os.path.getsize(path) / (1024 * 1024)
            print(f"📹 Test video created (attempt {i+1}): {path} ({size_mb:.1f} MB)")
            # Verify with ffprobe
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration,size:stream=codec_name,width,height,bit_rate",
                 "-of", "compact", path],
                capture_output=True, text=True,
            )
            if probe.stdout:
                for line in probe.stdout.strip().split('\n'):
                    print(f"   🔍 {line}")
            return path
        print(f"   ⚠️ Attempt {i+1} failed: {result.stderr[-300:]}")

    print("❌ All ffmpeg attempts failed")
    sys.exit(1)


# ── Upload video to S3 (same as main daily_short.py flow) ──
def upload_to_s3(video_path):
    """Upload video to S3/CloudFront. Returns (public_url, s3_key) or (None, None)."""
    file_mb = os.path.getsize(video_path) / (1024 * 1024)
    s3_key = f"p/ig-test-{int(time.time())}.mp4"

    try:
        import boto3
        print(f"\n📤 Uploading to S3/CloudFront ({file_mb:.1f} MB)...")
        print(f"   Bucket: {BLOG_S3_BUCKET}")
        print(f"   Key:    {s3_key}")
        s3 = boto3.client("s3")
        s3.upload_file(
            video_path,
            BLOG_S3_BUCKET,
            s3_key,
            ExtraArgs={"ContentType": "video/mp4", "CacheControl": "no-cache"},
        )
        url = f"{BLOG_BASE_URL}/{s3_key}"
        print(f"   ✅ S3 upload SUCCESS!")
        print(f"   🌐 Public URL: {url}")

        # Verify the URL is accessible
        print(f"   🔍 Verifying URL is reachable...")
        head_resp = requests.head(url, timeout=10, allow_redirects=True)
        print(f"   🔍 HTTP {head_resp.status_code} | Content-Type: {head_resp.headers.get('Content-Type', '?')} | Size: {head_resp.headers.get('Content-Length', '?')} bytes")

        if head_resp.status_code == 200:
            print(f"   ✅ URL is publicly accessible — Instagram can fetch this!")
        else:
            print(f"   ⚠️ URL returned {head_resp.status_code} — might not work with Instagram")

        return url, s3_key
    except ImportError:
        print(f"   ❌ boto3 not installed — run: pip install boto3")
        return None, None
    except Exception as e:
        print(f"   ❌ S3 upload FAILED: {e}")
        if "AccessDenied" in str(e):
            print(f"   💡 IAM user doesn't have s3:PutObject on {BLOG_S3_BUCKET}/{s3_key}")
            print(f"      Key must start with 'p/' for catalogfromgithub IAM user")
        return None, None


def cleanup_s3(s3_key):
    """Delete temp video from S3 after test."""
    if not s3_key:
        return
    try:
        import boto3
        boto3.client("s3").delete_object(Bucket=BLOG_S3_BUCKET, Key=s3_key)
        print(f"🧹 Cleaned up S3: {s3_key}")
    except Exception as e:
        print(f"⚠️ S3 cleanup failed (not critical): {e}")


# ── Instagram publish flow ──
def test_instagram_publish(video_path):
    """Full Instagram Reel publish test."""

    if not ig_token or not ig_biz_id:
        print("❌ Set INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_BUSINESS_ID env vars")
        return False

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

    # Step 2: Upload to S3/CloudFront (same as main flow)
    public_url, s3_key = upload_to_s3(video_path)
    if not public_url:
        print("❌ S3 upload failed — cannot proceed with Instagram test")
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
        cleanup_s3(s3_key)
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
            cleanup_s3(s3_key)
            return False
        print(f"   ⏳ Processing... ({check+1}/20, status={status_code})")
    else:
        print("❌ Processing timed out after 10 minutes")
        cleanup_s3(s3_key)
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
            print("⚠️ Instagram classified as VIDEO (not REELS) — publish may fail")
    except Exception:
        pass

    # Step 5: Publish
    print(f"\n🚀 Publishing ({IG_API_VERSION})...")
    pub_resp = requests.post(
        f"https://graph.facebook.com/{IG_API_VERSION}/{ig_biz_id}/media_publish",
        data={"creation_id": container_id, "access_token": ig_token},
        timeout=30,
    )

    # Cleanup S3 regardless of publish result
    cleanup_s3(s3_key)

    if pub_resp.status_code == 200:
        media_id = pub_resp.json().get("id")
        print(f"✅ PUBLISHED! Instagram media ID: {media_id}")
        return True

    error_text = pub_resp.text[:500]
    print(f"❌ Publish failed: {error_text}")

    if "2207089" in error_text or "carousel" in error_text.lower():
        print("\n📋 DIAGNOSIS: Carousel error (2207089)")
        print("   Instagram classified video as VIDEO instead of REELS.")
        print("   Since S3 upload worked, this is likely a video format issue.")
    elif "190" in error_text or "expired" in error_text.lower():
        print("\n📋 DIAGNOSIS: Token expired — refresh at developers.facebook.com/tools/explorer/")

    return False


# ── Main ──
if __name__ == "__main__":
    print("=" * 60)
    print("🧪 Instagram Reel Publish Test")
    print("=" * 60)

    # Parse args
    video_path = None
    for arg in sys.argv[1:]:
        if not arg.startswith("--") and os.path.isfile(arg):
            video_path = arg
            break

    if video_path:
        print(f"📹 Using provided video: {video_path}")
    else:
        video_path = make_test_video()

    file_mb = os.path.getsize(video_path) / (1024 * 1024)
    print(f"📹 Video: {video_path} ({file_mb:.1f} MB)")

    if upload_only:
        # Just test S3 upload — no Instagram publish
        print("\n" + "=" * 60)
        print("🧪 MODE: Upload-only (testing S3 only, no Instagram publish)")
        print("=" * 60)
        url, s3_key = upload_to_s3(video_path)
        if url:
            print(f"\n✅ S3 UPLOAD TEST PASSED")
            print(f"   Video is live at: {url}")
            print(f"   Run without --upload-only to also test Instagram publish")
            # Don't cleanup — let user verify the URL manually
            print(f"\n   To cleanup later: aws s3 rm s3://{BLOG_S3_BUCKET}/{s3_key}")
        else:
            print(f"\n❌ S3 UPLOAD TEST FAILED")
            sys.exit(1)
    else:
        # Full test: S3 upload + Instagram publish
        if not ig_token or not ig_biz_id:
            print("\n❌ Set these env vars for full test:")
            print("   INSTAGRAM_ACCESS_TOKEN")
            print("   INSTAGRAM_BUSINESS_ID")
            print("\n   Or use --upload-only to just test S3 upload")
            sys.exit(1)

        print("\n" + "=" * 60)
        success = test_instagram_publish(video_path)
        print("=" * 60)

        if success:
            print("✅ TEST PASSED")
        else:
            print("❌ TEST FAILED")
            sys.exit(1)
