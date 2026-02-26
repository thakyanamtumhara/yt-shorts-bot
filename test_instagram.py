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
    """Create a minimal 5-sec MP4 with ffmpeg (color + silent audio)."""
    if os.path.isfile(path) and os.path.getsize(path) > 1000:
        print(f"📹 Reusing existing test video: {path}")
        return path

    print("📹 Generating 5-sec test video...")
    # Instagram requires: H.264 video, AAC audio, 3-60s, proper MP4 container.
    # Key: use 'duration' in lavfi source (not -t/-shortest which can produce 0-byte files).
    cmds = [
        # Attempt 1: H.264 + AAC (standard)
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=blue:size=1080x1920:rate=30:duration=5",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=5",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", path,
        ],
        # Attempt 2: mpeg4 fallback (if libx264 missing)
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=blue:size=1080x1920:rate=30:duration=5",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=5",
            "-c:v", "mpeg4", "-q:v", "5", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", path,
        ],
    ]

    for i, cmd in enumerate(cmds):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and os.path.isfile(path) and os.path.getsize(path) > 10000:
            size_kb = os.path.getsize(path) / 1024
            print(f"📹 Test video created (attempt {i+1}): {path} ({size_kb:.0f} KB)")
            return path
        print(f"   ⚠️ Attempt {i+1} failed: {result.stderr[-300:]}")

    print("❌ All ffmpeg attempts failed")
    sys.exit(1)


# ── Upload video to public URL ──
def upload_to_public_host(video_path):
    """Upload video to a temporary public host. Returns URL or None."""
    file_mb = os.path.getsize(video_path) / (1024 * 1024)

    # Host 1: 0x0.st
    try:
        print(f"   📤 Uploading to 0x0.st ({file_mb:.1f} MB)...")
        with open(video_path, "rb") as f:
            resp = requests.post("https://0x0.st", files={"file": (os.path.basename(video_path), f, "video/mp4")}, timeout=300)
        if resp.status_code == 200 and resp.text.strip().startswith("http"):
            url = resp.text.strip()
            print(f"   ✅ Hosted at: {url}")
            return url
        print(f"   ⚠️ 0x0.st failed ({resp.status_code}): {resp.text[:100]}")
    except Exception as e:
        print(f"   ⚠️ 0x0.st error: {e}")

    # Host 2: litterbox.catbox.moe
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

    # Step 3: Create container
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

    # Step 4b: Verify container type
    try:
        verify_resp = requests.get(
            f"https://graph.facebook.com/{IG_API_VERSION}/{container_id}",
            params={"fields": "status_code,media_type,media_product_type", "access_token": ig_token},
            timeout=15,
        )
        vd = verify_resp.json()
        print(f"🔍 Container: media_type={vd.get('media_type','?')}, product={vd.get('media_product_type','?')}")
    except Exception:
        pass

    # Step 5: Publish (try v21.0, fallback v20.0)
    for api_ver in [IG_API_VERSION, "v20.0"]:
        print(f"\n🚀 Publishing ({api_ver})...")
        pub_resp = requests.post(
            f"https://graph.facebook.com/{api_ver}/{ig_biz_id}/media_publish",
            data={"creation_id": container_id, "access_token": ig_token},
            timeout=30,
        )
        if pub_resp.status_code == 200:
            media_id = pub_resp.json().get("id")
            print(f"✅ PUBLISHED! Instagram media ID: {media_id} (api={api_ver})")
            return True

        error_text = pub_resp.text[:300]
        print(f"⚠️ Publish failed ({api_ver}): {error_text}")
        if "2207089" in error_text or "carousel" in error_text.lower():
            print("🔄 Carousel error — trying fallback version...")
            time.sleep(5)
            continue
        else:
            break

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
