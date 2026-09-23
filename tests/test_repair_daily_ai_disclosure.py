import copy
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tools import repair_daily_ai_disclosure as repair


NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
ORIGINAL = {"privacyStatus": "private", "publishAt": repair.PUBLISH_AT, "embeddable": False,
            "license": "youtube", "publicStatsViewable": False, "selfDeclaredMadeForKids": False,
            "uploadStatus": "processed"}


def service(status=None, channel=repair.BOT_CHANNEL, video_id=repair.VIDEO_ID, transform=lambda x: x):
    youtube = Mock()
    youtube.current = copy.deepcopy(ORIGINAL if status is None else status)
    youtube.channels.return_value.list.return_value.execute.return_value = {"items": [{"id": channel}]}
    youtube.videos.return_value.list.return_value.execute.side_effect = lambda: {
        "items": [{"id": video_id, "snippet": {"channelId": channel}, "status": copy.deepcopy(youtube.current)}]}
    def update(*, part, body):
        assert part == "status" and body["id"] == repair.VIDEO_ID
        def execute():
            youtube.current = transform(copy.deepcopy(body["status"]))
            return {"id": repair.VIDEO_ID, "status": copy.deepcopy(body["status"])}
        return SimpleNamespace(execute=execute)
    youtube.videos.return_value.update.side_effect = update
    return youtube


class DisclosureRepairTest(unittest.TestCase):
    def test_dry_run_default_saves_exact_backup_and_never_updates(self):
        youtube = service()
        with TemporaryDirectory() as folder:
            path = Path(folder) / "backup.json"
            report = repair.repair(youtube, path, now=NOW)
            backup = json.loads(path.read_text())
        self.assertEqual(backup["status_before"], ORIGINAL)
        self.assertTrue(backup["proposed_status"]["containsSyntheticMedia"])
        self.assertTrue(report["dry_run"])
        self.assertFalse(report["verified"])
        youtube.videos.return_value.update.assert_not_called()

    def test_apply_preserves_schedule_false_values_and_verifies_exact_id(self):
        youtube = service()
        with TemporaryDirectory() as folder:
            path = Path(folder) / "backup.json"
            report = repair.repair(youtube, path, apply=True, now=NOW)
            backup = json.loads(path.read_text())
        desired = {key: value for key, value in ORIGINAL.items() if key != "uploadStatus"}
        desired["containsSyntheticMedia"] = True
        self.assertEqual(youtube.current, desired)
        self.assertTrue(report["applied"] and report["verified"])
        youtube.videos.return_value.update.assert_called_once()
        # Undo is prepared and tested offline only; never sent to a live API.
        undo = repair.undo_body_from_backup(backup)
        self.assertEqual(undo, {"id": repair.VIDEO_ID, "status": {key: value for key, value in ORIGINAL.items()
                                                                   if key != "uploadStatus"}})
        fake = copy.deepcopy(desired)
        fake = copy.deepcopy(undo["status"])
        self.assertEqual(fake, {key: value for key, value in ORIGINAL.items() if key != "uploadStatus"})

    def test_no_write_without_successful_new_backup(self):
        youtube = service()
        with TemporaryDirectory() as folder:
            path = Path(folder) / "backup.json"
            path.write_text("existing backup")
            with self.assertRaises(FileExistsError):
                repair.repair(youtube, path, apply=True, now=NOW)
            self.assertEqual(path.read_text(), "existing backup")
        youtube.videos.return_value.update.assert_not_called()

    def test_changed_channel_id_or_schedule_refuses_repair(self):
        cases = (service(channel="wrong"), service(video_id="wrong"),
                 service(status={**ORIGINAL, "privacyStatus": "unlisted"}),
                 service(status={**ORIGINAL, "publishAt": "2026-09-25T13:30:00Z"}))
        for youtube in cases:
            with self.subTest(youtube=youtube), TemporaryDirectory() as folder:
                with self.assertRaises(repair.RepairError):
                    repair.repair(youtube, Path(folder) / "backup.json", apply=True, now=NOW)
                youtube.videos.return_value.update.assert_not_called()

    def test_past_scheduled_time_is_not_reapplied(self):
        youtube = service()
        with TemporaryDirectory() as folder, self.assertRaises(repair.RepairError):
            repair.repair(youtube, Path(folder) / "backup.json", apply=True,
                          now=datetime(2026, 9, 25, tzinfo=timezone.utc))
        youtube.videos.return_value.update.assert_not_called()

    def test_already_true_is_read_back_without_another_write(self):
        youtube = service(status={**ORIGINAL, "containsSyntheticMedia": True})
        with TemporaryDirectory() as folder:
            report = repair.repair(youtube, Path(folder) / "backup.json", apply=True, now=NOW)
        self.assertTrue(report["verified"])
        self.assertFalse(report["applied"])
        youtube.videos.return_value.update.assert_not_called()

    def test_failed_readback_leaves_backup_and_fails_without_retry_or_undo(self):
        for key, value in (("containsSyntheticMedia", False), ("privacyStatus", "unlisted"),
                           ("publishAt", "2026-09-25T13:30:00Z"), ("publicStatsViewable", True)):
            youtube = service(transform=lambda status: {**status, key: value})
            with self.subTest(key=key), TemporaryDirectory() as folder:
                path = Path(folder) / "backup.json"
                with self.assertRaises(repair.RepairError):
                    repair.repair(youtube, path, apply=True, now=NOW, sleep=Mock())
                self.assertEqual(json.loads(path.read_text())["status_before"], ORIGINAL)
            youtube.videos.return_value.update.assert_called_once()

    def test_known_get_omission_accepts_and_records_exact_native_true_ack(self):
        youtube = service(transform=lambda status: {key: value for key, value in status.items()
                                                     if key != "containsSyntheticMedia"})
        with TemporaryDirectory() as folder:
            path = Path(folder) / "backup.json"
            report = repair.repair(youtube, path, apply=True, now=NOW)
            saved = json.loads(path.with_name("status-verification.json").read_text())
        self.assertTrue(report["verified"])
        self.assertEqual(report["verification_state"], "accepted_native_true_readback_omitted")
        self.assertIsNone(report["containsSyntheticMedia_after"])
        self.assertTrue(saved["update_acknowledgment"]["status"]["containsSyntheticMedia"])
        self.assertEqual(saved["update_acknowledgment"]["id"], repair.VIDEO_ID)
        youtube.videos.return_value.update.assert_called_once()

    def test_status_mismatch_has_bounded_reads_no_second_write_and_saved_actual_status(self):
        youtube = service(transform=lambda status: {**status, "embeddable": True})
        sleep = Mock()
        with TemporaryDirectory() as folder:
            path = Path(folder) / "backup.json"
            with self.assertRaises(repair.RepairError) as raised:
                repair.repair(youtube, path, apply=True, now=NOW, sleep=sleep)
            saved = json.loads(path.with_name("status-verification.json").read_text())
        self.assertEqual(len(saved["readbacks"]), 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(saved["readbacks"][-1]["status_readback"]["embeddable"])
        self.assertEqual(raised.exception.report, saved)
        youtube.videos.return_value.update.assert_called_once()

    def test_foreign_undo_backup_is_rejected(self):
        with self.assertRaises(repair.RepairError):
            repair.undo_body_from_backup({"format": "daily-ai-disclosure-backup-v1", "video_id": "wrong",
                                         "channel_id": repair.BOT_CHANNEL, "status_before": ORIGINAL})


if __name__ == "__main__":
    unittest.main()
