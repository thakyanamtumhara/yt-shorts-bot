#!/usr/bin/env python3
"""Upload and schedule one reviewed video on the MAIN YouTube channel."""

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


MAIN_CHANNEL = "UCdgOMA7WO48MYimj6q6mvNQ"
STATE_FORMAT = "upload-main-v1"
TOKEN_PATH = Path.home() / ".yt_main_token.json"
READ_WAIT_SECONDS = 30
READ_POLL_SECONDS = 2
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
STATUS_FIELDS = {
    "embeddable", "license", "publicStatsViewable", "selfDeclaredMadeForKids",
    "containsSyntheticMedia", "privacyStatus",
}
MANIFEST_FIELDS = {
    "video_path", "title", "description", "description_path", "tags",
    "thumbnail_path", "publish_at", "contains_synthetic_media", "ai_music",
    "ai_face", "ai_voice", "language", "category_id", "user_selection",
}


class UploadError(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_fingerprint(path):
    before = path.stat()
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise UploadError("An input changed while it was being checked; finish rendering first.")
    return {"sha256": hasher.hexdigest(), "size": after.st_size}


def timestamp(value, now=None, require_future=True):
    if not isinstance(value, str) or not value.strip():
        raise UploadError("publish_at must be an ISO timestamp with an explicit timezone.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise UploadError("publish_at must be an ISO timestamp with an explicit timezone.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise UploadError("publish_at needs a timezone, for example +05:30 or Z.")
    parsed = parsed.astimezone(timezone.utc).replace(microsecond=0)
    if require_future and parsed <= (now or utc_now()):
        raise UploadError("Refusing a past publish_at: YouTube could publish immediately.")
    return parsed.isoformat().replace("+00:00", "Z")


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise UploadError("Cannot read a valid UTF-8 JSON file: " + str(path)) from None
    if not isinstance(value, dict):
        raise UploadError("Expected a JSON object: " + str(path))
    return value


def input_file(base, value, label):
    if not isinstance(value, str) or not value.strip():
        raise UploadError(label + " is required.")
    path = (base / Path(value).expanduser()).resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise UploadError(label + " must identify a nonempty local file.")
    return path


def probe_video(path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True, check=True, timeout=60,
        )
        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])
        stream = next(s for s in data["streams"] if s.get("codec_type") == "video")
        width, height = int(stream["width"]), int(stream["height"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, StopIteration):
        raise UploadError("ffprobe could not verify a video stream and duration.") from None
    if not math.isfinite(duration) or duration <= 0 or min(width, height) <= 0:
        raise UploadError("The source video has invalid dimensions or duration.")
    return {"duration_seconds": duration, "width": width, "height": height}


def probe_thumbnail(path):
    with path.open("rb") as stream:
        signature = stream.read(8)
    expected = "png" if signature == b"\x89PNG\r\n\x1a\n" else "mjpeg" if signature.startswith(b"\xff\xd8\xff") else None
    if expected is None:
        raise UploadError("The thumbnail must be a valid JPEG or PNG image.")
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height", "-of", "json", str(path)],
            capture_output=True, check=True, timeout=30,
        )
        stream = json.loads(result.stdout)["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
        raise UploadError("The thumbnail must be a valid JPEG or PNG image.") from None
    if stream.get("codec_name") != expected or min(width, height) <= 0:
        raise UploadError("The thumbnail must be a valid JPEG or PNG image.")
    if path.stat().st_size > 2_000_000:
        raise UploadError("Keep the thumbnail below 2 MB for the upload API.")
    return {"mime_type": "image/jpeg" if expected == "mjpeg" else "image/png", "width": width, "height": height}


def validate_user_selection(selection, source_sha256):
    if not isinstance(selection, dict) or set(selection) != {
        "batch_id", "video_id", "version", "sha256", "approval_method", "approved_at",
    }:
        raise UploadError("An exact user selection needs batch, video, version, hash and approval provenance.")
    if selection["approval_method"] != "user_exact_selection":
        raise UploadError("AI-face release requires an explicit user selection, not a machine review.")
    if any(not isinstance(selection[k], str) or not selection[k].strip() for k in selection):
        raise UploadError("User-selection fields must be nonempty strings.")
    if not re.fullmatch(r"[a-f0-9]{64}", selection["sha256"]) or selection["sha256"] != source_sha256:
        raise UploadError("The selected video hash does not match this exact media file.")
    approved = timestamp(selection["approved_at"], require_future=False)
    if datetime.fromisoformat(approved.replace("Z", "+00:00")) > utc_now():
        raise UploadError("The user-selection timestamp cannot be in the future.")
    return copy.deepcopy(selection)


def require_ai_release_selection(metadata, source_sha256):
    if metadata.get("ai_face", False):
        if not metadata.get("user_selection"):
            raise UploadError("AI-face videos are private-only until an exact user selection is recorded.")
        validate_user_selection(metadata["user_selection"], source_sha256)
        if not metadata.get("contains_synthetic_media"):
            raise UploadError("Selected AI-face releases require synthetic-media disclosure.")


def load_manifest(path, now=None):
    path = Path(path).resolve()
    data = read_json(path)
    if set(data) - MANIFEST_FIELDS:
        raise UploadError("Unknown manifest fields; use the documented publishing manifest.")
    video = input_file(path.parent, data.get("video_path"), "video_path")
    thumbnail = input_file(path.parent, data.get("thumbnail_path"), "thumbnail_path")
    title = data.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 100 or any(c in title for c in "<>"):
        raise UploadError("title must be nonempty, at most 100 characters, and contain no angle brackets.")
    if ("description" in data) == ("description_path" in data):
        raise UploadError("Provide exactly one of description or description_path.")
    description = data.get("description")
    if "description_path" in data:
        try:
            description = input_file(path.parent, data["description_path"], "description_path").read_text(encoding="utf-8")
        except UnicodeError:
            raise UploadError("description_path must be UTF-8 text.") from None
    if not isinstance(description, str) or not description.strip() or len(description.encode("utf-8")) > 5000 or any(c in description for c in "<>"):
        raise UploadError("description must be nonempty, at most 5000 UTF-8 bytes, and contain no angle brackets.")
    tags = data.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(t, str) or not t.strip() for t in tags):
        raise UploadError("tags must be a list of nonempty strings.")
    tag_length = sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)
    if tag_length > 500:
        raise UploadError("tags exceed YouTube's 500-character combined limit.")
    for key in ("contains_synthetic_media", "ai_music", "ai_face", "ai_voice"):
        if key in data and not isinstance(data[key], bool):
            raise UploadError(key + " must be true or false.")
    uses_ai = any(data.get(k, False) for k in ("ai_music", "ai_face", "ai_voice"))
    synthetic = data.get("contains_synthetic_media", uses_ai)
    if uses_ai and not synthetic:
        raise UploadError("This uploader requires contains_synthetic_media=true when any AI flag is set.")
    language, category = data.get("language", "hi"), data.get("category_id", "22")
    if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language):
        raise UploadError("language must be a language code such as hi.")
    if not isinstance(category, str) or not category.isdigit():
        raise UploadError("category_id must be a numeric string.")
    publish_at = timestamp(data["publish_at"], now) if data.get("publish_at") is not None else None
    source = file_fingerprint(video)
    source.update(probe_video(video))
    thumb = probe_thumbnail(thumbnail)
    thumb.update(file_fingerprint(thumbnail))
    metadata = {
        "title": title, "description": description, "tags": tags,
        "language": language, "category_id": category, "publish_at": publish_at,
        "contains_synthetic_media": synthetic, "ai_face": data.get("ai_face", False),
        "thumbnail_sha256": thumb["sha256"],
    }
    if "user_selection" in data:
        metadata["user_selection"] = validate_user_selection(data["user_selection"], source["sha256"])
    if publish_at:
        require_ai_release_selection(metadata, source["sha256"])
    return {
        "video_path": video, "thumbnail_path": thumbnail, "source": source,
        "thumbnail": thumb, "metadata": metadata, "metadata_sha256": digest(metadata),
    }


class StateStore:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_name(self.path.name + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise UploadError("Another uploader is using this state file.") from None
            yield self
        finally:
            os.close(descriptor)

    def read(self):
        if not self.path.exists():
            return None
        state = read_json(self.path)
        if state.get("format") != STATE_FORMAT or state.get("channel_id") != MAIN_CHANNEL:
            raise UploadError("This is not a MAIN uploader state file.")
        if not isinstance(state.get("metadata"), dict) or digest(state["metadata"]) != state.get("metadata_sha256"):
            raise UploadError("The recorded metadata hash does not match; refusing modified state.")
        return state

    def save(self, state):
        state["updated_at"] = utc_now().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_path = tempfile.mkstemp(prefix="." + self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
            parent_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


class YouTube:
    def __init__(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        credentials = Credentials.from_authorized_user_file(str(TOKEN_PATH))
        required = {"https://www.googleapis.com/auth/youtube", "https://www.googleapis.com/auth/youtube.force-ssl"}
        if not required.intersection(credentials.scopes or []):
            raise UploadError("MAIN credentials need a YouTube metadata-write scope for the private hold.")
        if not credentials.valid:
            if not credentials.refresh_token:
                raise UploadError("MAIN credentials need renewed authorization.")
            credentials.refresh(Request())
        self.api = build("youtube", "v3", credentials=credentials, cache_discovery=False)

    def channel_id(self):
        items = self.api.channels().list(part="id", mine=True).execute(num_retries=0).get("items", [])
        return items[0]["id"] if len(items) == 1 else None

    def get_video(self, video_id):
        data = self.api.videos().list(part="snippet,status,processingDetails", id=video_id).execute(num_retries=0)
        return next((v for v in data.get("items", []) if v.get("id") == video_id), None)

    def insert_video(self, path, body):
        from googleapiclient.http import MediaFileUpload
        request = self.api.videos().insert(
            part="snippet,status", body=body,
            media_body=MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4"),
        )
        response = None
        while response is None:
            progress, response = request.next_chunk(num_retries=0)
            if progress:
                print("Uploading: %d%%" % (progress.progress() * 100), file=sys.stderr, flush=True)
        return response.get("id")

    def set_thumbnail(self, video_id, path, mime_type):
        from googleapiclient.http import MediaFileUpload
        self.api.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(path), mimetype=mime_type)).execute(num_retries=0)

    def update_status(self, video_id, status):
        return self.api.videos().update(part="status", body={"id": video_id, "status": status}).execute(num_retries=0)


def require_main(api):
    if api.channel_id() != MAIN_CHANNEL:
        raise UploadError("Wrong authenticated channel; refusing all MAIN upload mutations.")


def own_video(api, state):
    video_id = state.get("video_id")
    if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
        raise UploadError("No uploaded video ID recorded by this tool; refusing to operate on an arbitrary ID.")
    video = api.get_video(video_id)
    if not video or video.get("snippet", {}).get("channelId") != MAIN_CHANNEL:
        raise UploadError("The recorded video is absent or does not belong to MAIN.")
    return video


def private_status(status):
    result = {k: copy.deepcopy(v) for k, v in status.items() if k in STATUS_FIELDS}
    result["privacyStatus"] = "private"
    return result


def assert_copy(video, metadata):
    snippet, status = video.get("snippet", {}), video.get("status", {})
    if (snippet.get("title") != metadata["title"] or snippet.get("description", "") != metadata["description"]
            or sorted(snippet.get("tags", [])) != sorted(metadata["tags"])
            or bool(status.get("containsSyntheticMedia", False)) != metadata["contains_synthetic_media"]):
        raise UploadError("Uploaded metadata differs from the recorded copy; refusing automatic follow-up.")


def verify_private(video, publish_at=None):
    status = video.get("status", {})
    actual = timestamp(status["publishAt"], require_future=False) if status.get("publishAt") else None
    if status.get("privacyStatus") != "private" or actual != publish_at:
        raise UploadError("Private/schedule readback did not match; use the recorded private-hold action.")


def wait_readback(api, state, validate, video=None):
    deadline = time.monotonic() + READ_WAIT_SECONDS
    while True:
        current = video if video is not None else own_video(api, state)
        video = None
        try:
            validate(current)
        except UploadError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(READ_POLL_SECONDS, remaining))
        else:
            return current


def wait_processed(api, state, wait_seconds=180):
    deadline = time.monotonic() + wait_seconds
    while True:
        video = own_video(api, state)
        status = video.get("status", {}).get("uploadStatus")
        processing = video.get("processingDetails", {}).get("processingStatus")
        if status in ("failed", "rejected", "deleted") or processing in ("failed", "terminated"):
            raise UploadError("YouTube did not process this upload successfully; its ID remains recorded.")
        if status == "processed":
            return video
        if time.monotonic() >= deadline:
            raise UploadError("YouTube is still processing. Retry with the same manifest and state; it will reuse the ID.")
        print("Waiting for YouTube processing; uploaded ID is safely recorded.", file=sys.stderr, flush=True)
        time.sleep(min(10, max(0, deadline - time.monotonic())))


def initial_body(metadata):
    return {
        "snippet": {
            "title": metadata["title"], "description": metadata["description"],
            "tags": metadata["tags"], "categoryId": metadata["category_id"],
            "defaultLanguage": metadata["language"],
        },
        "status": {
            "privacyStatus": "private", "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": metadata["contains_synthetic_media"],
        },
    }


def check_retry(state, plan):
    if state["source"]["sha256"] != plan["source"]["sha256"] or state["source"]["size"] != plan["source"]["size"]:
        raise UploadError("Source fingerprint changed; this state cannot be reused for another render.")
    if state["metadata_sha256"] != plan["metadata_sha256"]:
        raise UploadError("Metadata or thumbnail changed; this state cannot be reused silently.")
    if state.get("held_private"):
        raise UploadError("This upload was held private. Use an explicit --schedule-at action to schedule it again.")
    if not state.get("video_id") and state.get("phase") != "validated":
        raise UploadError("Previous upload outcome is uncertain. Inspect MAIN uploads; a second insert is blocked.")


def schedule_owned(api, store, state, publish_at, video=None, now=None):
    require_ai_release_selection(state["metadata"], state["source"]["sha256"])
    require_main(api)
    if not state.get("thumbnail_uploaded"):
        raise UploadError("Finish the thumbnail upload with the same manifest and state before scheduling.")
    publish_at = timestamp(publish_at, now)
    video = video or own_video(api, state)
    if video.get("id") != state.get("video_id") or video.get("snippet", {}).get("channelId") != MAIN_CHANNEL:
        raise UploadError("The recorded video does not belong to MAIN.")
    if video.get("status", {}).get("privacyStatus") != "private":
        raise UploadError("Scheduling requires this recorded video to still be private.")
    if video.get("status", {}).get("uploadStatus") != "processed":
        raise UploadError("Wait for this recorded upload to finish processing before scheduling.")
    assert_copy(video, state["metadata"])
    hold = private_status(video["status"])
    state["undo"] = {"action": "hold-private", "video_id": state["video_id"], "status": hold}
    state["metadata"]["publish_at"] = publish_at
    state["metadata_sha256"] = digest(state["metadata"])
    state["held_private"] = False
    state["phase"] = "scheduling"
    store.save(state)
    actual = video["status"].get("publishAt")
    if not actual or timestamp(actual, require_future=False) != publish_at:
        api.update_status(state["video_id"], dict(hold, publishAt=publish_at))
    def validate_scheduled(current):
        verify_private(current, publish_at)
        assert_copy(current, state["metadata"])
    wait_readback(api, state, validate_scheduled)
    state["phase"] = "scheduled"
    store.save(state)
    return state


def upload(api, store, plan, wait_seconds=180):
    state = store.read()
    if state:
        check_retry(state, plan)
    require_main(api)
    if state is None:
        state = {
            "format": STATE_FORMAT, "channel_id": MAIN_CHANNEL, "created_at": utc_now().isoformat(),
            "source": plan["source"], "metadata": copy.deepcopy(plan["metadata"]),
            "metadata_sha256": plan["metadata_sha256"], "phase": "validated",
        }
        store.save(state)
    if not state.get("video_id"):
        if file_fingerprint(plan["video_path"]) != {k: plan["source"][k] for k in ("sha256", "size")}:
            raise UploadError("Source changed after validation; refusing to upload.")
        state["phase"] = "upload_started"
        store.save(state)
        try:
            video_id = api.insert_video(plan["video_path"], initial_body(plan["metadata"]))
            if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
                raise UploadError("Upload returned no valid ID; do not retry as a new upload.")
            state["video_id"] = video_id
            state["phase"] = "uploaded"
            store.save(state)
        except Exception:
            if not state.get("video_id"):
                state["phase"] = "upload_outcome_uncertain"
                store.save(state)
            raise
        if file_fingerprint(plan["video_path"]) != {k: plan["source"][k] for k in ("sha256", "size")}:
            raise UploadError("Source changed during upload; the recorded upload remains private.")
    video = wait_processed(api, state, wait_seconds)
    video = wait_readback(api, state, lambda current: assert_copy(current, state["metadata"]), video)
    if video["status"].get("privacyStatus") != "private":
        raise UploadError("This recorded upload is already public/unlisted; refusing automatic changes.")
    actual = video["status"].get("publishAt")
    desired = plan["metadata"]["publish_at"]
    if state.get("phase") == "scheduled" and desired and not actual:
        raise UploadError("The confirmed remote schedule was removed; use an explicit --schedule-at action to restore it.")
    if actual and timestamp(actual, require_future=False) != desired:
        raise UploadError("The remote schedule changed; use an explicit --schedule-at or --hold-private action.")
    if not state.get("thumbnail_uploaded"):
        if file_fingerprint(plan["thumbnail_path"])["sha256"] != plan["thumbnail"]["sha256"]:
            raise UploadError("Thumbnail changed after validation; refusing the follow-up.")
        api.set_thumbnail(state["video_id"], plan["thumbnail_path"], plan["thumbnail"]["mime_type"])
        state["thumbnail_uploaded"] = True
        store.save(state)
    if desired:
        return schedule_owned(api, store, state, desired, video)
    wait_readback(api, state, verify_private)
    state["phase"] = "private_ready"
    store.save(state)
    return state


def hold_private(api, store):
    state = store.read()
    if not state:
        raise UploadError("No uploaded video is recorded in this state file.")
    require_main(api)
    video = own_video(api, state)
    status = private_status(video["status"])
    if state["metadata"]["contains_synthetic_media"]:
        status["containsSyntheticMedia"] = True
    state["held_private"] = True
    state["phase"] = "holding_private"
    state["metadata"]["publish_at"] = None
    state["metadata_sha256"] = digest(state["metadata"])
    store.save(state)
    if video["status"].get("privacyStatus") != "private" or video["status"].get("publishAt"):
        api.update_status(state["video_id"], status)
    wait_readback(api, state, verify_private)
    state["phase"] = "held_private"
    store.save(state)
    return state


def state_summary(state):
    return {
        "channel_id": MAIN_CHANNEL, "video_id": state.get("video_id"), "phase": state["phase"],
        "publish_at": state["metadata"].get("publish_at"),
        "containsSyntheticMedia": state["metadata"]["contains_synthetic_media"],
        "ai_face": state["metadata"].get("ai_face", False),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--manifest", type=Path)
    actions.add_argument("--hold-private", action="store_true")
    actions.add_argument("--schedule-at", metavar="ISO_TIMESTAMP")
    parser.add_argument("--state", required=True, type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Local-only dry run; execution always verifies MAIN.")
    parser.add_argument("--wait-seconds", type=int, default=180)
    args = parser.parse_args(argv)
    if args.execute and args.offline:
        parser.error("--offline is only available for dry runs")
    if not 0 <= args.wait_seconds <= 600:
        parser.error("--wait-seconds must be between 0 and 600")
    try:
        plan = load_manifest(args.manifest) if args.manifest else None
        store = StateStore(args.state)
        with store.locked():
            state = store.read()
            if state and plan:
                check_retry(state, plan)
            if not plan and not state:
                raise UploadError("This action requires an existing state file with its uploaded ID.")
            publish_at = timestamp(args.schedule_at) if args.schedule_at else None
            if publish_at:
                require_ai_release_selection(state["metadata"], state["source"]["sha256"])
            api = None if args.offline else YouTube()
            if api:
                require_main(api)
                if state and state.get("video_id"):
                    own_video(api, state)
            if not args.execute:
                summary = {
                    "dry_run": True, "channel_id": MAIN_CHANNEL, "channel_checked": api is not None,
                    "operation": "upload" if plan else "hold-private" if args.hold_private else "schedule",
                    "video_id": state.get("video_id") if state else None,
                    "publish_at": plan["metadata"]["publish_at"] if plan else publish_at,
                }
                if plan:
                    summary.update(duration_seconds=plan["source"]["duration_seconds"],
                                   title=plan["metadata"]["title"], source_sha256=plan["source"]["sha256"],
                                   metadata_sha256=plan["metadata_sha256"],
                                   containsSyntheticMedia=plan["metadata"]["contains_synthetic_media"],
                                   ai_face=plan["metadata"]["ai_face"])
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                return 0
            if plan:
                state = upload(api, store, plan, args.wait_seconds)
            elif args.hold_private:
                state = hold_private(api, store)
            else:
                state = schedule_owned(api, store, state, publish_at)
            print(json.dumps(state_summary(state), ensure_ascii=False, indent=2))
            print("Private hold: use --hold-private --state with this same local state file.")
            return 0
    except UploadError as error:
        print("Error: " + str(error), file=sys.stderr)
    except Exception as error:
        status = getattr(getattr(error, "resp", None), "status", None)
        detail = (" HTTP " + str(status)) if isinstance(status, int) else ""
        print("Operation stopped: " + type(error).__name__ + detail + ". Credentials and response bodies omitted; reuse the recorded state.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
