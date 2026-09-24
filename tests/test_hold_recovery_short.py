from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tools import hold_recovery_short as hold


NOW = datetime(2026, 9, 23, 17, tzinfo=timezone.utc)
ORIGINAL = {"privacyStatus": "private", "publishAt": hold.PUBLISH_AT, "embeddable": False,
            "license": "youtube", "publicStatsViewable": False, "selfDeclaredMadeForKids": False,
            "uploadStatus": "processed"}


def service(status=None, channel=hold.BOT_CHANNEL, owner=hold.BOT_CHANNEL, video_id=hold.VIDEO_ID,
            transform=lambda status: status, acknowledge=lambda body: body):
    youtube = Mock()
    youtube.current = deepcopy(ORIGINAL if status is None else status)
    youtube.channels.return_value.list.return_value.execute.return_value = {"items": [{"id": channel}]}
    youtube.videos.return_value.list.return_value.execute.side_effect = lambda: {"items": [
        {"id": video_id, "snippet": {"channelId": owner}, "status": deepcopy(youtube.current)}]}
    def update(*, part, body):
        assert part == "status" and body["id"] == hold.VIDEO_ID
        def execute():
            youtube.current = transform(deepcopy(body["status"]))
            return acknowledge(deepcopy(body))
        return SimpleNamespace(execute=execute)
    youtube.videos.return_value.update.side_effect = update
    return youtube


class RecoveryHoldTest(unittest.TestCase):
    def test_single_jersey_hold_is_exact_and_undo_preserves_schedule(self):
        try:
            hold.select_target('HHqmZdnSQEs')
            status = {**ORIGINAL, 'publishAt': hold.PUBLISH_AT}
            youtube = service(status=status, video_id=hold.VIDEO_ID)
            with TemporaryDirectory() as directory:
                path = Path(directory) / 'status-backup.json'
                result = hold.hold(youtube, path, apply=True, now=NOW)
                backup = json.loads(path.read_text())
                self.assertEqual(backup['source_run_id'], '36008837674')
                self.assertEqual(hold.undo_body_from_backup(backup)['status']['publishAt'], '2026-09-25T13:30:00Z')
            self.assertTrue(result['verified'])
            self.assertEqual(result['video_id'], 'HHqmZdnSQEs')
            with self.assertRaises(hold.HoldError):
                hold.select_target('not-target')
        finally:
            hold.select_target('GMaTnJRoiV8')

    def test_default_preview_saves_exact_original_and_tested_restore_without_write(self):
        youtube = service()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "status-backup.json"
            result = hold.hold(youtube, path, now=NOW)
            backup = json.loads(path.read_text())
            undo = json.loads(path.with_name("undo-status-body.json").read_text())
        self.assertEqual(backup["status_before"], ORIGINAL)
        self.assertTrue(backup["offline_undo_verified"])
        self.assertNotIn("publishAt", backup["proposed_hold_status"])
        self.assertEqual(undo, hold.undo_body_from_backup(backup))
        self.assertEqual(undo["status"]["publishAt"], hold.PUBLISH_AT)
        self.assertTrue(undo["status"]["containsSyntheticMedia"])
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["write_attempted"])
        youtube.videos.return_value.update.assert_not_called()

    def test_backup_and_restore_body_exist_before_the_single_write(self):
        youtube = service()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "status-backup.json"
            original_update = youtube.videos.return_value.update.side_effect
            def update(**kwargs):
                backup = json.loads(path.read_text())
                undo = json.loads(path.with_name("undo-status-body.json").read_text())
                self.assertEqual(backup["status_before"], ORIGINAL)
                self.assertEqual(hold.offline_undo_check(backup, kwargs["body"]["status"]), undo)
                return original_update(**kwargs)
            youtube.videos.return_value.update.side_effect = update
            result = hold.hold(youtube, path, apply=True, now=NOW)
        expected = {key: value for key, value in ORIGINAL.items() if key in hold.MUTABLE_STATUS_FIELDS and key != "publishAt"}
        expected["containsSyntheticMedia"] = True
        self.assertEqual(youtube.current, expected)
        self.assertTrue(result["verified"] and result["publish_at_absent"])
        self.assertFalse(youtube.current["embeddable"])
        self.assertFalse(youtube.current["publicStatsViewable"])
        self.assertFalse(youtube.current["selfDeclaredMadeForKids"])
        youtube.videos.return_value.update.assert_called_once()

    def test_existing_backup_or_restore_file_refuses_all_writes(self):
        for name in ("status-backup.json", "undo-status-body.json"):
            youtube = service()
            with self.subTest(name=name), TemporaryDirectory() as directory:
                path = Path(directory) / "status-backup.json"
                (Path(directory) / name).write_text("preserve me")
                with self.assertRaises(FileExistsError):
                    hold.hold(youtube, path, apply=True, now=NOW)
                self.assertEqual((Path(directory) / name).read_text(), "preserve me")
            youtube.videos.return_value.update.assert_not_called()

    def test_wrong_owner_id_changed_privacy_schedule_or_past_time_refuses_hold(self):
        cases = [service(channel="other"), service(owner="other"), service(video_id="other"),
                 service(status={**ORIGINAL, "privacyStatus": "public"}),
                 service(status={**ORIGINAL, "publishAt": "2026-09-25T13:30:00Z"}),
                 service(status={key: value for key, value in ORIGINAL.items() if key != "publishAt"})]
        for youtube in cases:
            with TemporaryDirectory() as directory, self.assertRaises(hold.HoldError):
                hold.hold(youtube, Path(directory) / "status-backup.json", apply=True, now=NOW)
            youtube.videos.return_value.update.assert_not_called()
        youtube = service()
        with TemporaryDirectory() as directory, self.assertRaises(hold.HoldError):
            hold.hold(youtube, Path(directory) / "status-backup.json", apply=True,
                      now=datetime(2026, 9, 24, 13, 30, tzinfo=timezone.utc))
        youtube.videos.return_value.update.assert_not_called()

    def test_changed_setting_after_backup_cannot_be_overwritten(self):
        youtube = service()
        reads = youtube.videos.return_value.list.return_value.execute
        original_read = reads.side_effect
        count = 0
        def read():
            nonlocal count
            count += 1
            if count == 2:
                youtube.current["publicStatsViewable"] = True
            return original_read()
        reads.side_effect = read
        with TemporaryDirectory() as directory, self.assertRaises(hold.HoldError):
            hold.hold(youtube, Path(directory) / "status-backup.json", apply=True, now=NOW)
        youtube.videos.return_value.update.assert_not_called()

    def test_get_native_omission_accepts_only_exact_true_acknowledgment(self):
        omit_native = lambda status: {key: value for key, value in status.items() if key != "containsSyntheticMedia"}
        youtube = service(transform=omit_native)
        with TemporaryDirectory() as directory:
            result = hold.hold(youtube, Path(directory) / "status-backup.json", apply=True, now=NOW)
        self.assertTrue(result["verified"])
        self.assertEqual(result["verification_state"], "accepted_native_true_readback_omitted")
        self.assertTrue(result["update_acknowledgment"]["status"]["containsSyntheticMedia"])
        for acknowledge in (lambda body: {"id": "other", "status": body["status"]},
                            lambda body: {"id": hold.VIDEO_ID, "status": omit_native(body["status"])},
                            lambda body: {"id": hold.VIDEO_ID, "status": {**body["status"], "containsSyntheticMedia": False}}):
            youtube = service(transform=omit_native, acknowledge=acknowledge)
            with TemporaryDirectory() as directory, self.assertRaises(hold.HoldError):
                hold.hold(youtube, Path(directory) / "status-backup.json", apply=True, now=NOW, sleep=Mock())
            youtube.videos.return_value.update.assert_called_once()

    def test_schedule_remaining_even_null_never_passes_and_no_repeated_write(self):
        for value in (hold.PUBLISH_AT, None, ""):
            youtube = service(transform=lambda status: {**status, "publishAt": value})
            sleep = Mock()
            with self.subTest(value=value), TemporaryDirectory() as directory:
                path = Path(directory) / "status-backup.json"
                with self.assertRaises(hold.HoldError) as raised:
                    hold.hold(youtube, path, apply=True, now=NOW, sleep=sleep)
                report = json.loads(path.with_name("status-verification.json").read_text())
                self.assertEqual(raised.exception.report, report)
                self.assertFalse(report["verified"])
                self.assertEqual(report["verification_state"], "publish_at_not_removed")
                self.assertEqual(len(report["readbacks"]), 3)
            self.assertEqual(sleep.call_count, 2)
            youtube.videos.return_value.update.assert_called_once()

    def test_other_status_or_native_false_readback_cannot_pass(self):
        for key, value in (("privacyStatus", "unlisted"), ("containsSyntheticMedia", False),
                           ("embeddable", True), ("selfDeclaredMadeForKids", True)):
            youtube = service(transform=lambda status: {**status, key: value})
            with self.subTest(key=key), TemporaryDirectory() as directory, self.assertRaises(hold.HoldError):
                hold.hold(youtube, Path(directory) / "status-backup.json", apply=True, now=NOW, sleep=Mock())
            youtube.videos.return_value.update.assert_called_once()

    def test_lost_write_ack_is_not_retried_and_exception_body_is_not_reported(self):
        youtube = service()
        youtube.videos.return_value.update.return_value.execute.side_effect = RuntimeError("private-token")
        youtube.videos.return_value.update.side_effect = None
        with TemporaryDirectory() as directory:
            path = Path(directory) / "status-backup.json"
            with self.assertRaises(hold.HoldError) as raised:
                hold.hold(youtube, path, apply=True, now=NOW, sleep=Mock())
            report = json.loads(path.with_name("status-verification.json").read_text())
            self.assertEqual(raised.exception.report, report)
            self.assertNotIn("private-token", json.dumps(report))
            self.assertEqual(report["write_error"], "RuntimeError")
        youtube.videos.return_value.update.assert_called_once()

    def test_foreign_backup_and_hold_mutating_other_settings_fail_offline(self):
        backup = {"format": hold.BACKUP_FORMAT, "video_id": hold.VIDEO_ID, "channel_id": hold.BOT_CHANNEL,
                  "source_run_id": "35884612564", "status_before": ORIGINAL}
        for key, value in (("video_id", "other"), ("channel_id", "other"), ("source_run_id", "other")):
            with self.subTest(key=key), self.assertRaises(hold.HoldError):
                hold.undo_body_from_backup({**backup, key: value})
        wrong_hold = hold._mutable_status(ORIGINAL)
        wrong_hold.pop("publishAt")
        wrong_hold["embeddable"] = True
        with self.assertRaises(hold.HoldError):
            hold.offline_undo_check(backup, wrong_hold)


if __name__ == "__main__":
    unittest.main()
