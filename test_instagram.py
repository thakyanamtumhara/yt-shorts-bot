#!/usr/bin/env python3
"""
Minimal test script — runs ONLY the Instagram Reel cross-post flow.

Usage:
    # With a real video file:
    python test_instagram.py /path/to/video.mp4

    # Without a video (downloads a 5-sec test clip):
    python test_instagram.py

Requires env vars:
    INSTAGRAM_ACCESS_TOKEN
    INSTAGRAM_BUSINESS_ID
"""
import os
import sys
import subprocess

# ── Ensure env vars are set ──
ig_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
ig_biz_id = os.environ.get("INSTAGRAM_BUSINESS_ID", "").strip()

if not ig_token or not ig_biz_id:
    print("❌ Set these env vars first:")
    print("   export INSTAGRAM_ACCESS_TOKEN='EAA...'")
    print("   export INSTAGRAM_BUSINESS_ID='17841...'")
    sys.exit(1)

# ── Get or create a test video ──
if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
    video_path = sys.argv[1]
    print(f"📹 Using provided video: {video_path}")
else:
    video_path = "/tmp/ig_test_video.mp4"
    if not os.path.isfile(video_path):
        print("📹 No video provided — generating 5-sec test clip with ffmpeg...")
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "color=c=blue:size=1080x1920:rate=30:d=5",
            "-vf", "drawtext=text='Instagram Test':fontsize=60:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "5",
            "-shortest", video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # fallback without drawtext (missing font)
            cmd2 = [
                "ffmpeg", "-y", "-f", "lavfi", "-i",
                "color=c=blue:size=1080x1920:rate=30:d=5",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "5",
                "-shortest", video_path,
            ]
            subprocess.run(cmd2, capture_output=True, text=True, check=True)
        print(f"📹 Test video created: {video_path}")
    else:
        print(f"📹 Reusing existing test video: {video_path}")

file_mb = os.path.getsize(video_path) / (1024 * 1024)
print(f"   Size: {file_mb:.1f} MB")

# ── Import and run ──
print("\n" + "=" * 60)
print("🚀 Running Instagram cross-post (v21.0 API)...")
print("=" * 60 + "\n")

# Import from main module
from daily_short import cross_post_to_instagram

result = cross_post_to_instagram(
    video_path=video_path,
    title="Test Reel — Sale91 Plain T-Shirts",
    description="Testing Instagram Reel publish flow.\nThis is a test post.",
    topic="quality_checks",
)

print("\n" + "=" * 60)
if result:
    print(f"✅ SUCCESS! Instagram media ID: {result}")
else:
    print("❌ FAILED — check logs above for details")
print("=" * 60)
