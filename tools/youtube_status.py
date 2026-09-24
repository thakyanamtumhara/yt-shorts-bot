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


def status_verification(video_id, expected, actual, acknowledgment=None):
    response = acknowledgment if isinstance(acknowledgment, dict) else {}
    returned = response.get("status") if isinstance(response.get("status"), dict) else {}
    evidence = {"video_id": video_id, "verified": False,
                "request": {"id": video_id, "status": deepcopy(expected)},
                "acknowledgment": {"id": response.get("id"), "status": {
                    key: deepcopy(value) for key, value in returned.items() if key in MUTABLE_STATUS_FIELDS}},
                "status_readback": deepcopy(actual), "state": "status_mismatch"}
    other_fields = {key: value for key, value in expected.items() if key != "containsSyntheticMedia"}
    try:
        if not isinstance(actual, dict) or not _matches_status(actual, other_fields):
            return evidence
    except (StatusSafetyError, KeyError, TypeError):
        return evidence
    if response.get("id") is not None and response["id"] != video_id:
        return {**evidence, "state": "wrong_acknowledgment_video"}
    if "containsSyntheticMedia" in returned and returned["containsSyntheticMedia"] is not True:
        return {**evidence, "state": "acknowledgment_native_not_true"}
    if "containsSyntheticMedia" in actual:
        if actual["containsSyntheticMedia"] is not True:
            return {**evidence, "state": "readback_native_not_true"}
        return {**evidence, "verified": True, "state": "readback_native_true"}
    if response.get("id") == video_id and returned.get("containsSyntheticMedia") is True:
        return {**evidence, "verified": True, "state": "accepted_native_true_readback_omitted"}
    return {**evidence, "state": "readback_omitted_without_true_acknowledgment"}


def post_daily_ai_comment(youtube, video_id, comment_text, post_comment, *,
                          scheduled, enabled=True, sleep=time.sleep, now=None, record_evidence=None):
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
        acknowledgment = None
        try:
            acknowledgment = youtube.videos().update(
                part="status", body={"id": video_id, "status": restore}).execute()
        except Exception:
            pass
        try:
            if record_evidence:
                record_evidence(status_verification(video_id, restore, None, acknowledgment))
            for delay in (0, 5, 15, 30):
                if delay:
                    sleep(delay)
                evidence = status_verification(video_id, restore, _read_status(youtube, video_id), acknowledgment)
                if record_evidence:
                    record_evidence(evidence)
                if evidence['verified'] or evidence['state'] != 'status_mismatch':
                    break
            if not evidence["verified"]:
                raise StatusRestorationError("Status evidence did not verify the original scheduled state")
        except Exception as error:
            raise StatusRestorationError(
                "YouTube schedule/AI disclosure restoration could not be verified; check the exact video"
            ) from error
