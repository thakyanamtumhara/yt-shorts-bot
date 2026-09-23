import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import textwrap
import unittest
from unittest.mock import Mock, patch

from tools.youtube_status import (
    MUTABLE_STATUS_FIELDS, StatusRestorationError, StatusSafetyError,
    post_daily_ai_comment,
)


VIDEO_ID = "aB3dE5gH7_-"
NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
ORIGINAL = {
    "privacyStatus": "private", "publishAt": "2026-09-24T13:30:00Z",
    "embeddable": False, "license": "creativeCommon", "publicStatsViewable": False,
    "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False,
    "uploadStatus": "processed", "madeForKids": False,
}


class FakeYouTube:
    def __init__(self, status=None):
        self.status = copy.deepcopy(ORIGINAL if status is None else status)
        self.updates = []
        self.reads = 0
        self.original_response = None
        self.restore_transform = lambda value: value
        self.fail_update = None
        self.fail_after_apply = False

    def videos(self):
        return self

    def list(self, *, part, id):
        assert part == "status" and id == VIDEO_ID

        def execute():
            self.reads += 1
            if self.reads == 1 and self.original_response is not None:
                return copy.deepcopy(self.original_response)
            status = copy.deepcopy(self.status)
            if self.reads > 1:
                status = self.restore_transform(status)
            return {"items": [{"id": VIDEO_ID, "status": status}]}

        return SimpleNamespace(execute=execute)

    def update(self, *, part, body):
        assert part == "status" and body["id"] == VIDEO_ID

        def execute():
            self.updates.append(copy.deepcopy(body["status"]))
            failing = self.fail_update == len(self.updates)
            if failing and not self.fail_after_apply:
                raise RuntimeError("Request failed")
            # API replacement semantics: omitted mutable fields disappear.
            self.status = copy.deepcopy(body["status"])
            if failing:
                raise RuntimeError("Acknowledgment lost")
            return {"id": VIDEO_ID, "status": copy.deepcopy(self.status)}

        return SimpleNamespace(execute=execute)


class ScheduledCommentTest(unittest.TestCase):
    def call(self, youtube, callback=None, **kwargs):
        return post_daily_ai_comment(
            youtube, VIDEO_ID, kwargs.pop("comment_text", "Which fabric side do you print?"),
            callback or Mock(return_value="comment-id"), scheduled=kwargs.pop("scheduled", True),
            sleep=Mock(), now=NOW, **kwargs)

    def test_mutable_fields_and_false_values_survive_both_updates(self):
        youtube = FakeYouTube()
        callback = Mock(side_effect=lambda: self.assertEqual(youtube.status["privacyStatus"], "unlisted") or "comment-id")
        self.assertEqual(self.call(youtube, callback), "comment-id")
        expected = {key: value for key, value in ORIGINAL.items() if key in MUTABLE_STATUS_FIELDS}
        expected["containsSyntheticMedia"] = True
        self.assertEqual(youtube.status, expected)
        temporary = {key: value for key, value in expected.items() if key != "publishAt"}
        temporary["privacyStatus"] = "unlisted"
        self.assertEqual(youtube.updates, [temporary, expected])
        self.assertEqual(youtube.reads, 2)
        callback.assert_called_once()

    def test_callback_exception_always_restores_schedule_and_ai_disclosure(self):
        youtube = FakeYouTube()
        callback = Mock(side_effect=ValueError("Comment failed"))
        with self.assertRaisesRegex(ValueError, "Comment failed"):
            self.call(youtube, callback)
        self.assertEqual(youtube.status["privacyStatus"], "private")
        self.assertEqual(youtube.status["publishAt"], ORIGINAL["publishAt"])
        self.assertIs(youtube.status["containsSyntheticMedia"], True)
        self.assertEqual(youtube.reads, 2)

    def test_missing_question_or_disabled_comments_do_no_network_or_wait(self):
        for options in ({"comment_text": None}, {"comment_text": "  "}, {"enabled": False}):
            youtube = FakeYouTube()
            callback, sleep = Mock(), Mock()
            post_daily_ai_comment(youtube, VIDEO_ID, options.get("comment_text", "Question?"),
                                  callback, scheduled=True, enabled=options.get("enabled", True), sleep=sleep)
            self.assertEqual(youtube.reads, 0)
            self.assertEqual(youtube.updates, [])
            callback.assert_not_called()
            sleep.assert_not_called()

    def test_missing_or_wrong_original_video_never_switches_or_comments(self):
        for response in ({"items": []}, {"items": [{"id": "wrong-id", "status": ORIGINAL}]},
                         {"items": [{"id": VIDEO_ID}]}, {"items": [{"id": VIDEO_ID, "status": {}}]}):
            youtube = FakeYouTube()
            youtube.original_response = response
            callback = Mock()
            with self.subTest(response=response), self.assertRaises(StatusSafetyError):
                self.call(youtube, callback)
            self.assertEqual(youtube.updates, [])
            callback.assert_not_called()

    def test_missing_invalid_near_or_past_schedule_never_switches(self):
        values = (None, "bad", "2026-09-24T13:30:00", NOW.isoformat(),
                  (NOW + timedelta(minutes=4)).isoformat())
        for value in values:
            youtube = FakeYouTube({**ORIGINAL, "publishAt": value})
            callback = Mock()
            with self.subTest(value=value), self.assertRaises(StatusSafetyError):
                self.call(youtube, callback)
            self.assertEqual(youtube.updates, [])
            callback.assert_not_called()
        youtube = FakeYouTube({**ORIGINAL, "privacyStatus": "unlisted"})
        with self.assertRaises(StatusSafetyError):
            self.call(youtube)
        self.assertEqual(youtube.updates, [])

    def test_failed_switch_acknowledgment_still_restores(self):
        youtube = FakeYouTube()
        youtube.fail_update, youtube.fail_after_apply = 1, True
        callback = Mock()
        with self.assertRaisesRegex(RuntimeError, "Acknowledgment lost"):
            self.call(youtube, callback)
        self.assertEqual(youtube.status["privacyStatus"], "private")
        self.assertEqual(youtube.status["publishAt"], ORIGINAL["publishAt"])
        self.assertIs(youtube.status["containsSyntheticMedia"], True)
        callback.assert_not_called()

    def test_failed_restore_is_reported_even_if_callback_also_fails(self):
        youtube = FakeYouTube()
        youtube.fail_update = 2
        with self.assertRaisesRegex(StatusRestorationError, "could not be verified"):
            self.call(youtube, Mock(side_effect=ValueError("Comment failed")))
        self.assertEqual(youtube.status["privacyStatus"], "unlisted")

    def test_lost_restore_acknowledgment_can_only_pass_with_good_readback(self):
        youtube = FakeYouTube()
        youtube.fail_update, youtube.fail_after_apply = 2, True
        self.assertEqual(self.call(youtube), "comment-id")
        self.assertEqual(youtube.reads, 2)

    def test_false_ai_flag_changed_privacy_missing_schedule_or_changed_fields_fail_readback(self):
        for key, value in (("containsSyntheticMedia", False), ("privacyStatus", "unlisted"),
                           ("publishAt", None), ("publicStatsViewable", True)):
            youtube = FakeYouTube()
            youtube.restore_transform = lambda status: {**status, key: value}
            with self.subTest(key=key), self.assertRaises(StatusRestorationError):
                self.call(youtube)

    def test_equivalent_schedule_timezone_is_accepted(self):
        youtube = FakeYouTube()
        youtube.restore_transform = lambda status: {**status, "publishAt": "2026-09-24T19:00:00+05:30"}
        self.assertEqual(self.call(youtube), "comment-id")

    def test_non_scheduled_public_upload_needs_no_status_mutation(self):
        youtube = FakeYouTube()
        callback = Mock(return_value="comment-id")
        self.assertEqual(self.call(youtube, callback, scheduled=False), "comment-id")
        callback.assert_called_once()
        self.assertEqual(youtube.reads, 0)
        self.assertEqual(youtube.updates, [])


class DailyCommentIntegrationTest(unittest.TestCase):
    def comment_stage(self, *, question="Useful buyer question?", helper_error=None):
        source = (Path(__file__).resolve().parents[1] / "daily_short.py").read_text()
        start = source.index("                        # ── 10b. Add the buyer question")
        end = source.index("                        # thumbnails.set", start)
        scope = {"get_pin_tail": lambda topic: question, "fresh_topic": "Lesson", "AUTO_PIN_COMMENT": True,
                 "youtube": object(), "vid_id": VIDEO_ID, "SCHEDULE_PUBLISH": True,
                 "pin_comment": Mock(), "flag": Mock(), "status_restore_error": None}
        with patch("tools.youtube_status.post_daily_ai_comment", side_effect=helper_error) as helper:
            exec(compile(textwrap.dedent(source[start:end]), "comment_stage", "exec"), scope)
        return scope, helper

    def test_daily_missing_question_does_not_invoke_status_helper(self):
        _, helper = self.comment_stage(question=None)
        helper.assert_not_called()

    def test_daily_records_restore_failure_separately_from_upload(self):
        scope, _ = self.comment_stage(helper_error=StatusRestorationError("Restore unverified"))
        self.assertEqual(scope["status_restore_error"], "Restore unverified")
        scope["flag"].assert_called_once_with("youtube_status_restore", False)

    def test_daily_safe_comment_failure_keeps_upload_without_status_alarm(self):
        scope, _ = self.comment_stage(helper_error=StatusSafetyError("Missing schedule"))
        self.assertIsNone(scope["status_restore_error"])
        scope["flag"].assert_called_once_with("youtube_comment", False)

    def test_post_upload_failure_archives_exact_id_before_failing_without_retry_metadata(self):
        source = (Path(__file__).resolve().parents[1] / "daily_short.py").read_text()
        start = source.index("    # Save metadata for retry if upload failed")
        end = source.index("\n\ndef test_ai_thumbnail_standalone", start)
        for failure in ("status_restore_error", "post_upload_error"):
            with self.subTest(failure=failure), TemporaryDirectory() as folder:
                video, cover = Path(folder) / "video.mp4", Path(folder) / "cover.png"
                video.write_bytes(b"rendered video")
                cover.write_bytes(b"rendered cover")
                scope = {"upload_failed": False, "status_restore_error": None, "post_upload_error": None,
                         failure: "Unverified", "WORK_DIR": folder, "output_path": str(video),
                         "thumbnail_path": str(cover), "fresh_topic": "Supported lesson", "yt_title": "Title",
                         "ig_title": "IG title", "script_voice": "Complete voice", "tts_input": "Complete voice",
                         "script_english": "Complete lesson", "vid_id": VIDEO_ID, "ig_media_id": "ig-id",
                         "TEST_MODE": False, "RUN_FLAGS": {}, "flag": Mock(), "downloaded_clips": [],
                         "ambient_temp_files": [], "audio_path": str(Path(folder) / "audio.wav"), "os": os}
                with self.assertRaisesRegex(RuntimeError, "do not reupload"):
                    exec(compile(textwrap.dedent(source[start:end]), "archive_stage", "exec"), scope)
                manifest = json.loads((Path(folder) / "review_manifest.json").read_text())
                self.assertEqual(manifest["source_posts"]["bot_youtube"], VIDEO_ID)
                self.assertFalse((Path(folder) / "upload_meta.json").exists())
                self.assertTrue(cover.exists())


if __name__ == "__main__":
    unittest.main()
