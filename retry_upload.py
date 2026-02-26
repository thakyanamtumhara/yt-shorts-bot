#!/usr/bin/env python3
"""
Retry YouTube upload for a previously generated video.
Reads video + metadata from /tmp/yt_shorts/ and uploads.
Used by the retry-upload GitHub Actions workflow.
"""
import json, os, sys, time, random, re

WORK_DIR = os.environ.get("WORK_DIR", "/tmp/yt_shorts")
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
TOKEN_FILE = f"{WORK_DIR}/youtube_token.json"
CLIENT_SECRETS_FILE = f"{WORK_DIR}/client_secret.json"
META_FILE = f"{WORK_DIR}/upload_meta.json"

# Import upload settings from main script
UPLOAD_AS_SHORT = True
SCHEDULE_PUBLISH = True
SUBTITLE_FONT = "Noto-Sans"


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
                print("   Token refreshed!")
                with open(TOKEN_FILE, "w") as f:
                    f.write(creds.to_json())
            except Exception as e:
                print(f"   Token refresh failed: {e}")
                return None
        else:
            print("   No valid YouTube token.")
            return None

    return build("youtube", "v3", credentials=creds)


def sanitize_tags(tags):
    """Clean and validate tags for YouTube API (prevents invalidTags error)."""
    cleaned = []
    total_chars = 0
    seen = set()
    for tag in tags:
        if not isinstance(tag, str):
            tag = str(tag)
        tag = tag.strip()
        tag = re.sub(r'[<>",]', '', tag)
        tag = re.sub(r'\s+', ' ', tag)
        tag = tag[:100]
        if not tag or tag.lower() in seen:
            continue
        if total_chars + len(tag) > 500:
            break
        seen.add(tag.lower())
        cleaned.append(tag)
        total_chars += len(tag)
    return cleaned


def upload_video(youtube, video_path, title, description, tags, topic=""):
    from googleapiclient.http import MediaFileUpload

    if UPLOAD_AS_SHORT and "#shorts" not in title.lower():
        if len(title) + 8 <= 100:
            title += " #Shorts"

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": sanitize_tags(tags[:30]),
            "categoryId": "22",
            "defaultLanguage": "hi",
            "defaultAudioLanguage": "hi"
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
            "embeddable": True,
        }
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=1024*1024)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    print(f"   Uploading: {title}")
    response = None
    retry = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"   {int(status.progress() * 100)}%...")
        except Exception as e:
            retry += 1
            if retry > 5:
                raise
            time.sleep(random.uniform(1, 2 ** retry))

    vid_id = response.get("id", "?")
    url = f"https://youtube.com/shorts/{vid_id}"
    print(f"   UPLOADED! {url}")
    return vid_id, url


def upload_thumbnail(youtube, video_id, thumbnail_path):
    from googleapiclient.http import MediaFileUpload
    try:
        media = MediaFileUpload(thumbnail_path, mimetype="image/png")
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        print(f"   Thumbnail uploaded for {video_id}")
    except Exception as e:
        print(f"   Thumbnail upload failed: {e}")


def main():
    if not os.path.exists(META_FILE):
        print(f"No metadata file found at {META_FILE}")
        sys.exit(1)

    with open(META_FILE) as f:
        meta = json.load(f)

    video_path = meta["video_path"]
    if not os.path.exists(video_path):
        print(f"Video file not found: {video_path}")
        sys.exit(1)

    print(f"   Video: {video_path}")
    print(f"   Title: {meta['title']}")

    youtube = get_youtube_service()
    if not youtube:
        print("YouTube auth failed!")
        sys.exit(1)

    vid_id, vid_url = upload_video(
        youtube, video_path,
        meta["title"], meta["description"],
        meta.get("tags", []), meta.get("topic", "")
    )

    thumbnail_path = meta.get("thumbnail_path", "")
    if thumbnail_path and os.path.exists(thumbnail_path) and vid_id != "?":
        upload_thumbnail(youtube, vid_id, thumbnail_path)

    print(f"\n{'='*60}")
    print(f"  RETRY UPLOAD COMPLETE!")
    print(f"  {vid_url}")
    print(f"  {meta['title']}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
