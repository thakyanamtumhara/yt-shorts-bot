"""One-video repair for the verified daily AI output from run 35884612564."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.youtube_status import MUTABLE_STATUS_FIELDS, _timestamp, status_verification


VIDEO_ID = "GMaTnJRoiV8"
BOT_CHANNEL = "UCHZbA84OiM9COlTQ4JcVgeQ"
PUBLISH_AT = "2026-09-24T13:30:00Z"
REPORT = Path("native-disclosure-repair")


class RepairError(RuntimeError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report


def _read(youtube):
    items = youtube.videos().list(part="snippet,status", id=VIDEO_ID).execute().get("items", [])
    if (len(items) != 1 or items[0].get("id") != VIDEO_ID
            or (items[0].get("snippet") or {}).get("channelId") != BOT_CHANNEL
            or not isinstance(items[0].get("status"), dict)):
        raise RepairError("Exact recovery video and BOT channel could not be verified")
    return deepcopy(items[0]["status"])


def _check_schedule(status, now):
    if (status.get("privacyStatus") != "private"
            or _timestamp(status.get("publishAt")) != _timestamp(PUBLISH_AT)
            or _timestamp(PUBLISH_AT) <= now):
        raise RepairError("Original private future schedule changed; repair refused")


def undo_body_from_backup(backup):
    """Build the original-status body for offline undo verification; no API call."""
    if (backup.get("format") != "daily-ai-disclosure-backup-v1"
            or backup.get("video_id") != VIDEO_ID or backup.get("channel_id") != BOT_CHANNEL
            or not isinstance(backup.get("status_before"), dict)):
        raise RepairError("Backup does not belong to the exact recovery video")
    status = backup["status_before"]
    if status.get("privacyStatus") != "private" or _timestamp(status.get("publishAt")) != _timestamp(PUBLISH_AT):
        raise RepairError("Backup does not preserve the known schedule")
    return {"id": VIDEO_ID, "status": {key: deepcopy(value) for key, value in status.items()
                                      if key in MUTABLE_STATUS_FIELDS}}


def repair(youtube, backup_path, *, apply=False, now=None, sleep=time.sleep):
    channels = youtube.channels().list(part="id", mine=True).execute().get("items", [])
    if len(channels) != 1 or channels[0].get("id") != BOT_CHANNEL:
        raise RepairError("Credential is not the expected BOT channel")
    original = _read(youtube)
    _check_schedule(original, now or datetime.now(timezone.utc))
    backup = {"format": "daily-ai-disclosure-backup-v1", "video_id": VIDEO_ID,
              "channel_id": BOT_CHANNEL, "source_run_id": "35884612564", "status_before": original}
    body = undo_body_from_backup(backup)
    body["status"]["containsSyntheticMedia"] = True
    backup["proposed_status"] = deepcopy(body["status"])
    with Path(backup_path).open("x", encoding="utf-8") as target:
        json.dump(backup, target, ensure_ascii=False, indent=2)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())
    result = {"video_id": VIDEO_ID, "channel_id": BOT_CHANNEL, "applied": False,
              "privacyStatus": original["privacyStatus"], "publishAt": original["publishAt"],
              "containsSyntheticMedia_before": original.get("containsSyntheticMedia"),
              "containsSyntheticMedia_after": original.get("containsSyntheticMedia"), "verified": False}
    if not apply:
        return {**result, "dry_run": True, "proposed_containsSyntheticMedia": True}
    result.update(dry_run=False, readbacks=[])
    acknowledgment = None
    if original.get("containsSyntheticMedia") is not True:
        result["write_attempted"] = True
        try:
            acknowledgment = youtube.videos().update(part="status", body=body).execute()
            result["applied"] = True
        except Exception as error:
            result["write_error"] = type(error).__name__
    result["update_acknowledgment"] = status_verification(
        VIDEO_ID, body["status"], None, acknowledgment)["acknowledgment"]
    Path(backup_path).with_name("status-verification.json").write_text(json.dumps(result, indent=2) + "\n")
    for attempt in range(3):
        try:
            actual = _read(youtube)
            evidence = status_verification(VIDEO_ID, body["status"], actual, acknowledgment)
            result["readbacks"].append(evidence)
            result.update(containsSyntheticMedia_after=actual.get("containsSyntheticMedia"),
                          verified=evidence["verified"], verification_state=evidence["state"])
        except Exception as error:
            result["readbacks"].append({"read_error": type(error).__name__})
        Path(backup_path).with_name("status-verification.json").write_text(json.dumps(result, indent=2) + "\n")
        if result["verified"]:
            return result
        if result.get("verification_state") not in (None, "status_mismatch"):
            break
        if attempt < 2:
            sleep(2)
    raise RepairError("Repair status evidence could not be verified; no second write attempted", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    REPORT.mkdir(exist_ok=True)
    result = {"video_id": VIDEO_ID, "verified": False, "dry_run": not args.apply}
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        credentials = Credentials.from_authorized_user_info(json.loads(os.environ["YOUTUBE_TOKEN_JSON"]))
        if not credentials.valid:
            credentials.refresh(Request())
        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        result = repair(youtube, REPORT / "status-backup.json", apply=args.apply)
    except Exception as error:
        if isinstance(error, RepairError) and error.report:
            result = error.report
        result["error"] = str(error) if isinstance(error, RepairError) else type(error).__name__
    (REPORT / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
