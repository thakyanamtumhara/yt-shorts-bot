"""Preserve scheduled daily AI video status while adding an owner comment."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
import time


MUTABLE_STATUS_FIELDS = frozenset({
    "embeddable", "license", "privacyStatus", "publicStatsViewable",
    "publishAt", "selfDeclaredMadeForKids", "containsSyntheticMedia",
})


class StatusSafetyError(RuntimeError):
    pass


class StatusRestorationError(StatusSafetyError):
    pass


def _timestamp(value):
    if not isinstance(value, str):
        raise StatusSafetyError("Scheduled video has no valid publishAt; comment skipped")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise StatusSafetyError("Scheduled video has invalid publishAt; comment skipped") from None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise StatusSafetyError("Scheduled video publishAt has no timezone; comment skipped")
    return stamp.astimezone(timezone.utc)


def _read_status(youtube, video_id):
    response = youtube.videos().list(part="status", id=video_id).execute()
    items = response.get("items", [])
    if (len(items) != 1 or items[0].get("id") != video_id
            or not isinstance(items[0].get("status"), dict)
            or not items[0]["status"].get("privacyStatus")):
        raise StatusSafetyError("Exact video status unavailable; comment skipped")
    return deepcopy(items[0]["status"])


def _matches_status(actual, expected):
    for field, value in expected.items():
        if field == "publishAt":
            if _timestamp(actual.get(field)) != _timestamp(value):
                return False
        elif type(actual.get(field)) is not type(value) or actual[field] != value:
            return False
    return True


def post_daily_ai_comment(youtube, video_id, comment_text, post_comment, *,
                          scheduled, enabled=True, sleep=time.sleep, now=None):
    """Call post_comment only with usable text, restoring all known mutable status.

    The callback takes no arguments. This helper is only for known daily AI videos;
    every status write explicitly retains their native synthetic-media disclosure.
    """
    if not enabled or not isinstance(comment_text, str) or not comment_text.strip():
        return None
    if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise StatusSafetyError("Invalid video ID; comment skipped")
    if not scheduled:
        sleep(30)
        return post_comment()

    original = _read_status(youtube, video_id)
    if original["privacyStatus"] != "private":
        raise StatusSafetyError("Scheduled video is not private; comment skipped")
    publish_at = _timestamp(original.get("publishAt"))
    current_time = now or datetime.now(timezone.utc)
    if publish_at <= current_time + timedelta(minutes=5):
        raise StatusSafetyError("Scheduled publication is too close or past; comment skipped")
    restore = {key: deepcopy(value) for key, value in original.items()
               if key in MUTABLE_STATUS_FIELDS}
    restore["containsSyntheticMedia"] = True
    temporary = {key: deepcopy(value) for key, value in restore.items() if key != "publishAt"}
    temporary["privacyStatus"] = "unlisted"

    try:
        youtube.videos().update(
            part="status", body={"id": video_id, "status": temporary}).execute()
        sleep(30)
        return post_comment()
    finally:
        # Even a lost switch/restore acknowledgment can have changed the video.
        try:
            youtube.videos().update(
                part="status", body={"id": video_id, "status": restore}).execute()
        except Exception:
            pass
        try:
            if not _matches_status(_read_status(youtube, video_id), restore):
                raise StatusRestorationError("Status readback did not match the original scheduled state")
        except Exception as error:
            raise StatusRestorationError(
                "YouTube schedule/AI disclosure restoration could not be verified; check the exact video"
            ) from error
