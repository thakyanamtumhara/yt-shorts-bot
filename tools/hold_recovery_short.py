"""Preview or hold only the scheduled recovery Short GMaTnJRoiV8 for quality review."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.repair_daily_ai_disclosure import VIDEO_ID, BOT_CHANNEL, PUBLISH_AT
from tools.youtube_status import MUTABLE_STATUS_FIELDS, _timestamp, status_verification


REPORT = Path("recovery-short-quality-hold")
BACKUP_FORMAT = "recovery-short-quality-hold-backup-v1"
SOURCE_RUN_ID = "35884612564"


def select_target(video_id):
    global VIDEO_ID, PUBLISH_AT, SOURCE_RUN_ID, REPORT
    targets = {"GMaTnJRoiV8": ("2026-09-24T13:30:00Z", "35884612564"),
               "HHqmZdnSQEs": ("2026-09-25T13:30:00Z", "36008837674")}
    if video_id not in targets:
        raise HoldError("Video is not an exact reviewed quality-hold target")
    VIDEO_ID = video_id
    PUBLISH_AT, SOURCE_RUN_ID = targets[video_id]
    REPORT = Path("recovery-short-quality-hold")


class HoldError(RuntimeError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report


def _read(youtube):
    items = youtube.videos().list(part="snippet,status", id=VIDEO_ID).execute().get("items", [])
    if (len(items) != 1 or items[0].get("id") != VIDEO_ID
            or (items[0].get("snippet") or {}).get("channelId") != BOT_CHANNEL
            or not isinstance(items[0].get("status"), dict)):
        raise HoldError("Exact recovery video and BOT owner could not be verified")
    return deepcopy(items[0]["status"])


def _check_schedule(status, now):
    try:
        correct_time = _timestamp(status.get("publishAt")) == _timestamp(PUBLISH_AT)
        future = _timestamp(PUBLISH_AT) > now
    except Exception:
        raise HoldError("Original private future schedule is unavailable; hold refused") from None
    if status.get("privacyStatus") != "private" or not correct_time or not future:
        raise HoldError("Original private future schedule changed; hold refused")


def _mutable_status(status):
    value = {key: deepcopy(item) for key, item in status.items() if key in MUTABLE_STATUS_FIELDS}
    value["containsSyntheticMedia"] = True
    return value


def undo_body_from_backup(backup):
    """Prepare the exact original schedule with native AI disclosure retained; no API call."""
    if (not isinstance(backup, dict) or backup.get("format") != BACKUP_FORMAT
            or backup.get("video_id") != VIDEO_ID or backup.get("channel_id") != BOT_CHANNEL
            or backup.get("source_run_id") != SOURCE_RUN_ID
            or not isinstance(backup.get("status_before"), dict)):
        raise HoldError("Backup does not belong to the exact recovery Short")
    original = backup["status_before"]
    try:
        schedule_matches = _timestamp(original.get("publishAt")) == _timestamp(PUBLISH_AT)
    except Exception:
        schedule_matches = False
    if original.get("privacyStatus") != "private" or not schedule_matches:
        raise HoldError("Backup does not preserve the exact original private schedule")
    return {"id": VIDEO_ID, "status": _mutable_status(original)}


def offline_undo_check(backup, held_status):
    undo = undo_body_from_backup(backup)
    original = _mutable_status(backup["status_before"])
    if held_status != {key: value for key, value in original.items() if key != "publishAt"}:
        raise HoldError("Proposed hold changes settings beyond the scheduled publication")
    restored = deepcopy(held_status)
    for key in MUTABLE_STATUS_FIELDS:
        restored.pop(key, None)
    restored.update(deepcopy(undo["status"]))
    if restored != original or restored.get("publishAt") != backup["status_before"]["publishAt"]:
        raise HoldError("Offline restore did not recover the original mutable settings")
    return undo


def _save_new(path, value):
    with Path(path).open("x", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, indent=2)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())


def _hold_evidence(expected, actual, acknowledgment):
    evidence = status_verification(VIDEO_ID, expected, actual, acknowledgment)
    no_schedule = isinstance(actual, dict) and "publishAt" not in actual
    evidence["publish_at_absent"] = no_schedule
    if not no_schedule:
        evidence.update(verified=False, state="publish_at_not_removed")
    return evidence


def hold(youtube, backup_path, *, apply=False, now=None, sleep=time.sleep):
    current_time = lambda: now if now is not None else datetime.now(timezone.utc)
    channels = youtube.channels().list(part="id", mine=True).execute().get("items", [])
    if len(channels) != 1 or channels[0].get("id") != BOT_CHANNEL:
        raise HoldError("Credential is not the expected BOT channel")
    original = _read(youtube)
    _check_schedule(original, current_time())
    held = _mutable_status(original)
    held.pop("publishAt")
    backup = {"format": BACKUP_FORMAT, "video_id": VIDEO_ID, "channel_id": BOT_CHANNEL,
              "source_run_id": SOURCE_RUN_ID, "status_before": original,
              "proposed_hold_status": held,
              "native_disclosure_policy": "Retain true for this known AI video, including when GET omits the field."}
    undo = offline_undo_check(backup, held)
    backup.update(undo_body=undo, offline_undo_verified=True)
    backup_path = Path(backup_path)
    _save_new(backup_path, backup)
    persisted = json.loads(backup_path.read_text(encoding="utf-8"))
    if persisted != backup or offline_undo_check(persisted, held) != undo:
        raise HoldError("Persisted backup or offline restore verification failed; no write attempted")
    _save_new(backup_path.with_name("undo-status-body.json"), undo)
    result = {"video_id": VIDEO_ID, "channel_id": BOT_CHANNEL, "dry_run": not apply,
              "write_attempted": False, "write_acknowledged": False, "verified": False,
              "original_publish_at": original["publishAt"], "proposed_privacy_status": "private",
              "proposed_publish_at_absent": True, "offline_undo_verified": True,
              "backup_sha256": hashlib.sha256(backup_path.read_bytes()).hexdigest(),
              "undo_requires": "Recheck exact BOT owner, held private state and future original schedule before restoring."}
    if not apply:
        return result
    latest = _read(youtube)
    _check_schedule(latest, current_time())
    if _mutable_status(latest) != _mutable_status(original):
        raise HoldError("Mutable settings changed after backup; no write attempted", result)
    result.update(write_attempted=True, readbacks=[])
    acknowledgment = None
    try:
        acknowledgment = youtube.videos().update(part="status", body={"id": VIDEO_ID, "status": held}).execute()
        result["write_acknowledged"] = True
    except Exception as error:
        result["write_error"] = type(error).__name__
    result["update_acknowledgment"] = status_verification(VIDEO_ID, held, None, acknowledgment)["acknowledgment"]
    evidence_path = backup_path.with_name("status-verification.json")
    evidence_path.write_text(json.dumps(result, indent=2) + "\n")
    for attempt in range(3):
        try:
            actual = _read(youtube)
            evidence = _hold_evidence(held, actual, acknowledgment)
            result["readbacks"].append(evidence)
            result.update(verified=evidence["verified"], verification_state=evidence["state"],
                          privacy_status_after=actual.get("privacyStatus"),
                          publish_at_absent=evidence["publish_at_absent"],
                          contains_synthetic_media_after=actual.get("containsSyntheticMedia"))
        except Exception as error:
            result["readbacks"].append({"read_error": type(error).__name__})
        evidence_path.write_text(json.dumps(result, indent=2) + "\n")
        if result["verified"]:
            return result
        if result.get("verification_state") not in (None, "status_mismatch", "publish_at_not_removed"):
            break
        if attempt < 2:
            sleep(2)
    raise HoldError("Quality hold could not be verified; no second write or automatic restore attempted", result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--video-id", choices=("GMaTnJRoiV8", "HHqmZdnSQEs"), default="GMaTnJRoiV8")
    args = parser.parse_args(argv)
    select_target(args.video_id)
    REPORT.mkdir(exist_ok=True)
    result = {"video_id": VIDEO_ID, "dry_run": not args.apply, "verified": False}
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        credentials = Credentials.from_authorized_user_info(json.loads(os.environ["YOUTUBE_TOKEN_JSON"]))
        if not credentials.valid:
            credentials.refresh(Request())
        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        result = hold(youtube, REPORT / "status-backup.json", apply=args.apply)
    except Exception as error:
        if isinstance(error, HoldError) and error.report:
            result = error.report
        result["error"] = str(error) if isinstance(error, HoldError) else type(error).__name__
    (REPORT / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
