import copy
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image


SPEC = importlib.util.spec_from_file_location("upload_main", Path(__file__).parents[1] / "tools" / "upload_main.py")
uploader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(uploader)
FUTURE = "2099-09-19T13:30:00Z"
VIDEO_ID = "ABCDEFGHIJK"


class FakeYouTube:
    def __init__(self, channel=None):
        self.channel = channel or uploader.MAIN_CHANNEL
        self.videos = {}
        self.inserts = []
        self.thumbnails = []
        self.updates = []
        self.insert_error = None
        self.thumbnail_error = None
        self.before_thumbnail = None
        self.before_update = None
        self.after_insert = None
        self.read_responses = []
        self.read_count = 0
        self.ignore_schedule = False

    def channel_id(self):
        return self.channel

    def get_video(self, video_id):
        self.read_count += 1
        if self.read_responses:
            return copy.deepcopy(self.read_responses.pop(0))
        return copy.deepcopy(self.videos.get(video_id))

    def insert_video(self, path, body):
        self.inserts.append(copy.deepcopy(body))
        if self.insert_error:
            raise self.insert_error
        video = copy.deepcopy(body)
        video["id"] = VIDEO_ID
        video["snippet"]["channelId"] = uploader.MAIN_CHANNEL
        video["status"].update(uploadStatus="processed", license="youtube", embeddable=True)
        self.videos[VIDEO_ID] = video
        if self.after_insert:
            self.after_insert()
        return VIDEO_ID

    def set_thumbnail(self, video_id, path, mime_type):
        if self.before_thumbnail:
            self.before_thumbnail()
        self.thumbnails.append(video_id)
        if self.thumbnail_error:
            raise self.thumbnail_error

    def update_status(self, video_id, status):
        if self.before_update:
            self.before_update(status)
        self.updates.append(copy.deepcopy(status))
        new_status = copy.deepcopy(status)
        if self.ignore_schedule:
            new_status.pop("publishAt", None)
        new_status["uploadStatus"] = "processed"
        self.videos[video_id]["status"] = new_status


class MissingNativeGETYouTube(FakeYouTube):
    def __init__(self):
        super().__init__()
        self.get_ai_value = None
        self.ack_mode = "true"
        self.omit_schedule_ack = False
        self.update_attempts = 0

    def get_video(self, video_id):
        video = super().get_video(video_id)
        if video:
            if self.get_ai_value is None:
                video["status"].pop("containsSyntheticMedia", None)
            else:
                video["status"]["containsSyntheticMedia"] = self.get_ai_value
        return video

    def update_status(self, video_id, status):
        self.update_attempts += 1
        super().update_status(video_id, status)
        response = copy.deepcopy(self.videos[video_id])
        mode = "missing" if self.omit_schedule_ack and status.get("publishAt") else self.ack_mode
        if mode == "missing":
            response["status"].pop("containsSyntheticMedia", None)
        elif mode == "false":
            response["status"]["containsSyntheticMedia"] = False
        elif mode == "wrong-id":
            response["id"] = "ZYXWVUTSRQP"
        return response


class UploadSafeguards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.video = self.base / "reviewed.mp4"
        self.video.write_bytes(b"unique reviewed video content")
        self.thumbnail = self.base / "cover.jpg"
        Image.new("RGB", (1280, 720), "blue").save(self.thumbnail)
        self.manifest = self.base / "publish.json"
        self.data = {
            "video_path": self.video.name, "thumbnail_path": self.thumbnail.name,
            "title": "Warehouse fit guide", "description": "See the actual measurements.",
            "tags": ["plain tshirt", "fit"],
        }
        self.store = uploader.StateStore(self.base / "upload-state.json")
        self.api = FakeYouTube()
        self.probe = patch.object(uploader, "probe_video", return_value={"duration_seconds": 45.0, "width": 1080, "height": 1920})
        self.probe.start()
        self.addCleanup(self.probe.stop)
        self.elapsed = 0
        self.clock = patch.object(uploader.time, "monotonic", side_effect=lambda: self.elapsed)
        self.sleep = patch.object(uploader.time, "sleep", side_effect=self.advance_clock)
        self.clock.start()
        self.sleep.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.sleep.stop)

    def advance_clock(self, seconds):
        self.elapsed += seconds

    def plan(self, **changes):
        self.data.update(changes)
        self.manifest.write_text(json.dumps(self.data), encoding="utf-8")
        return uploader.load_manifest(self.manifest)

    def test_wrong_channel_prevents_insert(self):
        self.api.channel = "UCHZbA84OiM9COlTQ4JcVgeQ"
        with self.assertRaisesRegex(uploader.UploadError, "Wrong authenticated channel"):
            uploader.upload(self.api, self.store, self.plan())
        self.assertEqual(self.api.inserts, [])
        self.assertFalse(self.store.path.exists())

    def test_id_saved_before_thumbnail_failure_and_retry_reuses_it(self):
        plan = self.plan()
        self.api.before_thumbnail = lambda: self.assertEqual(self.store.read()["video_id"], VIDEO_ID)
        self.api.thumbnail_error = RuntimeError("thumbnail unavailable")
        with self.assertRaises(RuntimeError):
            uploader.upload(self.api, self.store, plan)
        self.assertEqual(self.store.read()["video_id"], VIDEO_ID)
        self.api.thumbnail_error = None
        state = uploader.upload(self.api, self.store, plan)
        self.assertEqual(len(self.api.inserts), 1)
        self.assertEqual(state["phase"], "private_ready")
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_processed_copy_converges_without_repeating_insert(self):
        def stale_copy():
            old = copy.deepcopy(self.api.videos[VIDEO_ID])
            old["snippet"]["title"] = ""
            self.api.read_responses = [old, old]

        self.api.after_insert = stale_copy
        state = uploader.upload(self.api, self.store, self.plan())
        self.assertEqual(state["phase"], "private_ready")
        self.assertEqual(len(self.api.inserts), 1)
        self.assertEqual(len(self.api.thumbnails), 1)
        self.assertEqual(self.api.updates, [])
        self.assertEqual(self.elapsed, 4)

    def test_persistent_copy_mismatch_stops_at_deadline_with_id_saved(self):
        def bad_copy():
            self.api.videos[VIDEO_ID]["snippet"]["title"] = "A different title"

        self.api.after_insert = bad_copy
        with self.assertRaisesRegex(uploader.UploadError, "metadata differs"):
            uploader.upload(self.api, self.store, self.plan())
        self.assertEqual(self.elapsed, uploader.READ_WAIT_SECONDS)
        self.assertEqual(self.store.read()["video_id"], VIDEO_ID)
        self.assertEqual(len(self.api.inserts), 1)
        self.assertEqual(self.api.thumbnails, [])
        self.assertEqual(self.api.updates, [])

    def test_ambiguous_insert_is_never_repeated(self):
        plan = self.plan()
        self.api.insert_error = TimeoutError("outcome unknown")
        with self.assertRaises(TimeoutError):
            uploader.upload(self.api, self.store, plan)
        self.api.insert_error = None
        with self.assertRaisesRegex(uploader.UploadError, "second insert is blocked"):
            uploader.upload(self.api, self.store, plan)
        self.assertEqual(len(self.api.inserts), 1)

    def test_changed_source_same_path_cannot_reuse_id(self):
        uploader.upload(self.api, self.store, self.plan())
        self.video.write_bytes(b"different render")
        with self.assertRaisesRegex(uploader.UploadError, "Source fingerprint changed"):
            uploader.upload(self.api, self.store, self.plan())
        self.assertEqual(len(self.api.inserts), 1)

    def test_changed_metadata_cannot_reuse_id(self):
        uploader.upload(self.api, self.store, self.plan())
        with self.assertRaisesRegex(uploader.UploadError, "Metadata or thumbnail changed"):
            uploader.upload(self.api, self.store, self.plan(title="A different promised topic"))
        self.assertEqual(len(self.api.inserts), 1)

    def test_changed_thumbnail_cannot_reuse_id(self):
        uploader.upload(self.api, self.store, self.plan())
        Image.new("RGB", (1280, 720), "yellow").save(self.thumbnail)
        with self.assertRaisesRegex(uploader.UploadError, "Metadata or thumbnail changed"):
            uploader.upload(self.api, self.store, self.plan())
        self.assertEqual(len(self.api.thumbnails), 1)

    def test_undeclared_ai_cannot_be_forced_false(self):
        self.assertFalse(self.plan()["metadata"]["contains_synthetic_media"])
        self.assertTrue(self.plan(ai_music=True)["metadata"]["contains_synthetic_media"])
        with self.assertRaisesRegex(uploader.UploadError, "requires contains_synthetic_media=true"):
            self.plan(contains_synthetic_media=False)

    def test_disclosure_is_native_and_initial_upload_is_unscheduled_private(self):
        uploader.upload(self.api, self.store, self.plan(ai_music=True, publish_at=FUTURE))
        initial = self.api.inserts[0]["status"]
        self.assertTrue(initial["containsSyntheticMedia"])
        self.assertEqual(initial["privacyStatus"], "private")
        self.assertNotIn("publishAt", initial)

    def test_ai_face_with_publish_date_is_rejected_before_upload(self):
        with self.assertRaisesRegex(uploader.UploadError, "AI-face videos are private-only"):
            self.plan(ai_face=True, publish_at=FUTURE)
        self.assertFalse(self.store.path.exists())
        self.assertEqual(self.api.inserts, [])

    def test_private_ai_face_state_cannot_be_scheduled_by_cli_or_helper(self):
        uploader.upload(self.api, self.store, self.plan(ai_face=True))
        self.assertTrue(self.store.read()["metadata"]["ai_face"])
        self.assertTrue(self.store.read()["metadata"]["contains_synthetic_media"])
        with self.assertRaisesRegex(uploader.UploadError, "AI-face videos are private-only"):
            uploader.schedule_owned(self.api, self.store, self.store.read(), FUTURE)
        stderr = io.StringIO()
        with patch.object(uploader, "YouTube") as client, redirect_stderr(stderr):
            code = uploader.main(["--schedule-at", FUTURE, "--state", str(self.store.path), "--execute"])
        self.assertEqual(code, 1)
        client.assert_not_called()
        self.assertIn("AI-face videos are private-only", stderr.getvalue())
        self.assertEqual(self.api.updates, [])
        self.assertEqual(self.api.videos[VIDEO_ID]["status"]["privacyStatus"], "private")

    def selection(self, **changes):
        result = {
            "batch_id": "warehouse-20260911", "video_id": "WH09", "version": "1.0",
            "sha256": uploader.file_fingerprint(self.video)["sha256"],
            "approved_at": "2026-09-11T00:00:00Z", "approval_method": "user_exact_selection",
        }
        result.update(changes)
        return result

    def test_selected_ai_face_schedules_exact_file_and_hold_preserves_disclosure(self):
        plan = self.plan(ai_face=True, publish_at=FUTURE, user_selection=self.selection())
        state = uploader.upload(self.api, self.store, plan)
        self.assertEqual(state["phase"], "scheduled")
        self.assertEqual(state["metadata"]["user_selection"]["sha256"], state["source"]["sha256"])
        self.assertTrue(self.api.videos[VIDEO_ID]["status"]["containsSyntheticMedia"])
        uploader.hold_private(self.api, self.store)
        self.assertNotIn("publishAt", self.api.videos[VIDEO_ID]["status"])
        self.assertTrue(self.api.videos[VIDEO_ID]["status"]["containsSyntheticMedia"])

    def test_selection_for_other_render_is_rejected(self):
        with self.assertRaisesRegex(uploader.UploadError, "selected video hash"):
            self.plan(ai_face=True, publish_at=FUTURE, user_selection=self.selection(sha256="0" * 64))
        self.assertEqual(self.api.inserts, [])

    def test_machine_review_cannot_authorize_ai_release(self):
        with self.assertRaisesRegex(uploader.UploadError, "explicit user selection"):
            self.plan(ai_face=True, publish_at=FUTURE, user_selection=self.selection(approval_method="machine_pass"))

    def test_changed_state_source_cannot_use_earlier_selection(self):
        uploader.upload(self.api, self.store, self.plan(ai_face=True, user_selection=self.selection()))
        state = self.store.read()
        state["source"]["sha256"] = "1" * 64
        with self.assertRaisesRegex(uploader.UploadError, "selected video hash"):
            uploader.schedule_owned(self.api, self.store, state, FUTURE)
        self.assertEqual(self.api.updates, [])

    def test_schedule_readback_requires_disclosure_to_remain_true(self):
        plan = self.plan(ai_face=True, publish_at=FUTURE, user_selection=self.selection())
        self.api.before_update = lambda status: status.update(containsSyntheticMedia=False)
        with self.assertRaisesRegex(uploader.UploadError, "metadata differs"):
            uploader.upload(self.api, self.store, plan)
        self.assertNotEqual(self.store.read()["phase"], "scheduled")

    def test_schedule_prepares_undo_then_hold_cancels_and_verifies(self):
        plan = self.plan(publish_at=FUTURE)

        def check_undo(status):
            if "publishAt" in status:
                saved = self.store.read()
                self.assertEqual(saved["undo"]["video_id"], VIDEO_ID)
                self.assertEqual(saved["undo"]["status"]["privacyStatus"], "private")
                self.assertNotIn("publishAt", saved["undo"]["status"])

        self.api.before_update = check_undo
        uploader.upload(self.api, self.store, plan)
        self.assertEqual(self.api.videos[VIDEO_ID]["status"]["publishAt"], FUTURE)
        state = uploader.hold_private(self.api, self.store)
        self.assertEqual(state["phase"], "held_private")
        self.assertNotIn("publishAt", self.api.videos[VIDEO_ID]["status"])
        self.assertEqual(self.api.videos[VIDEO_ID]["status"]["privacyStatus"], "private")
        self.assertEqual(self.api.videos[VIDEO_ID]["status"]["license"], "youtube")
        with self.assertRaises(uploader.UploadError):
            uploader.upload(self.api, self.store, plan)
        uploader.schedule_owned(self.api, self.store, self.store.read(), FUTURE)
        self.assertEqual(self.store.read()["phase"], "scheduled")
        self.assertEqual(len(self.api.inserts), 1)

    def test_bad_schedule_readback_keeps_recorded_undo(self):
        self.api.ignore_schedule = True
        with self.assertRaisesRegex(uploader.UploadError, "readback did not match"):
            uploader.upload(self.api, self.store, self.plan(publish_at=FUTURE))
        self.assertEqual(self.store.read()["video_id"], VIDEO_ID)
        self.assertIn("undo", self.store.read())
        self.assertEqual(self.elapsed, uploader.READ_WAIT_SECONDS)
        self.assertEqual(len(self.api.updates), 1)
        uploader.hold_private(self.api, self.store)
        self.assertEqual(self.store.read()["phase"], "held_private")

    def test_schedule_readback_converges_with_one_status_write(self):
        def stale_status(status):
            self.api.read_responses = [copy.deepcopy(self.api.videos[VIDEO_ID])] * 2

        self.api.before_update = stale_status
        state = uploader.upload(self.api, self.store, self.plan(publish_at=FUTURE))
        self.assertEqual(state["phase"], "scheduled")
        self.assertEqual(len(self.api.inserts), 1)
        self.assertEqual(len(self.api.updates), 1)
        self.assertEqual(self.elapsed, 4)

    def test_hold_readback_converges_with_one_hold_write(self):
        uploader.upload(self.api, self.store, self.plan(publish_at=FUTURE))

        def stale_status(status):
            self.api.read_responses = [copy.deepcopy(self.api.videos[VIDEO_ID])] * 2

        self.api.before_update = stale_status
        state = uploader.hold_private(self.api, self.store)
        self.assertEqual(state["phase"], "held_private")
        self.assertEqual(len(self.api.updates), 2)
        self.assertNotIn("publishAt", self.api.updates[-1])
        self.assertEqual(self.elapsed, 4)

    def test_persistent_hold_readback_stops_at_deadline_without_rewriting(self):
        uploader.upload(self.api, self.store, self.plan(publish_at=FUTURE))

        def stale_status(status):
            self.api.read_responses = [copy.deepcopy(self.api.videos[VIDEO_ID])] * 100

        self.api.before_update = stale_status
        with self.assertRaisesRegex(uploader.UploadError, "readback did not match"):
            uploader.hold_private(self.api, self.store)
        self.assertEqual(self.elapsed, uploader.READ_WAIT_SECONDS)
        self.assertEqual(len(self.api.updates), 2)
        self.assertEqual(self.store.read()["phase"], "holding_private")
        self.assertTrue(self.store.read()["held_private"])

    def test_hold_requires_recorded_id_and_live_main_ownership(self):
        with self.assertRaisesRegex(uploader.UploadError, "No uploaded video"):
            uploader.hold_private(self.api, self.store)
        uploader.upload(self.api, self.store, self.plan())
        self.api.videos[VIDEO_ID]["snippet"]["channelId"] = "another-channel"
        with self.assertRaisesRegex(uploader.UploadError, "does not belong to MAIN"):
            uploader.hold_private(self.api, self.store)
        self.assertEqual(self.api.updates, [])

    def test_schedule_requires_completed_thumbnail(self):
        plan = self.plan()
        self.api.thumbnail_error = RuntimeError("thumbnail unavailable")
        with self.assertRaises(RuntimeError):
            uploader.upload(self.api, self.store, plan)
        with self.assertRaisesRegex(uploader.UploadError, "Finish the thumbnail upload"):
            uploader.schedule_owned(self.api, self.store, self.store.read(), FUTURE)
        self.assertEqual(self.api.updates, [])

    def test_explicit_schedule_rechecks_authenticated_channel(self):
        uploader.upload(self.api, self.store, self.plan())
        self.api.channel = "UCHZbA84OiM9COlTQ4JcVgeQ"
        with self.assertRaisesRegex(uploader.UploadError, "Wrong authenticated channel"):
            uploader.schedule_owned(self.api, self.store, self.store.read(), FUTURE)
        self.assertEqual(self.api.updates, [])

    def test_remote_copy_changes_are_not_overwritten_on_retry(self):
        plan = self.plan()
        uploader.upload(self.api, self.store, plan)
        self.api.videos[VIDEO_ID]["snippet"]["title"] = "A manual revision"
        with self.assertRaisesRegex(uploader.UploadError, "metadata differs"):
            uploader.upload(self.api, self.store, plan)
        self.assertEqual(self.api.updates, [])

    def test_studio_schedule_cancellation_is_not_undone_by_retry(self):
        plan = self.plan(publish_at=FUTURE)
        uploader.upload(self.api, self.store, plan)
        self.assertEqual(self.store.read()["phase"], "scheduled")
        del self.api.videos[VIDEO_ID]["status"]["publishAt"]
        mutations_before_retry = len(self.api.updates)
        with self.assertRaisesRegex(uploader.UploadError, "confirmed remote schedule was removed"):
            uploader.upload(self.api, self.store, plan)
        self.assertEqual(len(self.api.updates), mutations_before_retry)
        self.assertNotIn("publishAt", self.api.videos[VIDEO_ID]["status"])
        self.assertEqual(len(self.api.inserts), 1)
        uploader.schedule_owned(self.api, self.store, self.store.read(), FUTURE)
        self.assertEqual(self.api.videos[VIDEO_ID]["status"]["publishAt"], FUTURE)

    def test_retry_can_finish_a_schedule_request_that_never_reached_api(self):
        plan = self.plan(publish_at=FUTURE)

        def fail_before_mutation(status):
            raise TimeoutError("request was not sent")

        self.api.before_update = fail_before_mutation
        with self.assertRaises(TimeoutError):
            uploader.upload(self.api, self.store, plan)
        self.assertEqual(self.store.read()["phase"], "scheduling")
        self.assertNotIn("publishAt", self.api.videos[VIDEO_ID]["status"])
        self.api.before_update = None
        uploader.upload(self.api, self.store, plan)
        self.assertEqual(self.store.read()["phase"], "scheduled")
        self.assertEqual(len(self.api.inserts), 1)

    def test_schedule_timestamps_require_timezone_and_future(self):
        now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(uploader.timestamp("2026-09-19T19:00:00+05:30", now), "2026-09-19T13:30:00Z")
        for value in ("2026-09-19 19:00", "2026-09-09T10:00:00Z"):
            with self.assertRaises(uploader.UploadError):
                uploader.timestamp(value, now)

    def test_status_hold_preserves_mutable_fields_only(self):
        old = {
            "privacyStatus": "private", "publishAt": FUTURE, "uploadStatus": "processed",
            "madeForKids": False, "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True, "license": "creativeCommon", "embeddable": False,
        }
        held = uploader.private_status(old)
        self.assertNotIn("publishAt", held)
        self.assertNotIn("uploadStatus", held)
        self.assertNotIn("madeForKids", held)
        self.assertTrue(held["containsSyntheticMedia"])
        self.assertEqual(held["license"], "creativeCommon")
        self.assertFalse(held["embeddable"])

    def test_two_processes_cannot_use_same_state(self):
        other = uploader.StateStore(self.store.path)
        with self.store.locked():
            with self.assertRaisesRegex(uploader.UploadError, "Another uploader"):
                with other.locked():
                    self.fail("Concurrent state lock was accepted")

    def test_cli_does_not_print_remote_exception_payload(self):
        self.plan()
        stderr = io.StringIO()
        with patch.object(uploader, "YouTube", side_effect=RuntimeError("do-not-log-this-token")), redirect_stderr(stderr):
            code = uploader.main(["--manifest", str(self.manifest), "--state", str(self.store.path), "--dry-run"])
        self.assertEqual(code, 1)
        self.assertNotIn("do-not-log-this-token", stderr.getvalue())
        self.assertIn("response bodies omitted", stderr.getvalue())


class NativeAIResponseEvidence(unittest.TestCase):
    setUp = UploadSafeguards.setUp
    advance_clock = UploadSafeguards.advance_clock
    plan = UploadSafeguards.plan
    selection = UploadSafeguards.selection

    def missing_api(self):
        self.api = MissingNativeGETYouTube()
        return self.api

    def seed_existing_private(self, plan):
        self.api.insert_video(plan["video_path"], uploader.initial_body(plan["metadata"]))
        self.api.inserts.clear()
        state = {"format": uploader.STATE_FORMAT, "channel_id": uploader.MAIN_CHANNEL,
                 "source": plan["source"], "metadata": copy.deepcopy(plan["metadata"]),
                 "metadata_sha256": plan["metadata_sha256"], "phase": "uploaded", "video_id": VIDEO_ID}
        self.store.save(state)
        return state

    def test_existing_upload_get_omission_uses_exact_status_ack_then_thumbnail_schedule(self):
        self.missing_api()
        plan = self.plan(ai_face=True, user_selection=self.selection(), publish_at=FUTURE)
        self.seed_existing_private(plan)
        def intent_before_write(status):
            receipt = self.store.read()["native_ai_writes"][-1]
            self.assertEqual(receipt["outcome"], "intent_saved")
            self.assertEqual(receipt["request"]["status"], status)
            self.assertEqual(receipt["source_sha256"], plan["source"]["sha256"])
            self.assertEqual(receipt["metadata_sha256"], self.store.read()["metadata_sha256"])
            self.assertTrue(self.store.read()["undo"]["status"]["containsSyntheticMedia"])
        self.api.before_update = intent_before_write
        state = uploader.upload(self.api, self.store, plan)
        self.assertEqual(state["phase"], "scheduled")
        self.assertEqual(self.api.inserts, [])
        self.assertEqual(self.api.thumbnails, [VIDEO_ID])
        self.assertEqual(len(self.api.updates), 2)
        self.assertNotIn("publishAt", self.api.updates[0])
        self.assertEqual(self.api.updates[1]["publishAt"], FUTURE)
        self.assertTrue(all(s["containsSyntheticMedia"] is True for s in self.api.updates))
        self.assertEqual([r["operation"] for r in state["native_ai_writes"]], ["confirm-native-ai", "schedule"])
        self.assertTrue(uploader.native_ai_acknowledged(state, state["metadata"]))
        again = uploader.upload(self.api, self.store, plan)
        self.assertEqual(state["native_ai_writes"], again["native_ai_writes"])
        self.assertEqual(state["schedule_generation"], again["schedule_generation"])
        self.assertEqual(self.api.inserts, [])
        self.assertEqual(len(self.api.updates), 2)
        self.assertEqual(len(self.api.thumbnails), 1)

    def test_explicit_get_false_cannot_be_overridden_with_an_ack(self):
        self.missing_api()
        plan = self.plan(ai_face=True, user_selection=self.selection())
        state = uploader.upload(self.api, self.store, plan)
        self.api.get_ai_value = False
        with self.assertRaisesRegex(uploader.UploadError, "explicit native AI disclosure"):
            uploader.schedule_owned(self.api, self.store, state, FUTURE)
        self.assertEqual(len(self.api.updates), 1)

    def test_initial_get_false_stops_before_confirmation_or_thumbnail(self):
        self.missing_api().get_ai_value = False
        plan = self.plan(ai_face=True, user_selection=self.selection())
        self.seed_existing_private(plan)
        with self.assertRaisesRegex(uploader.UploadError, "explicit native AI disclosure"):
            uploader.upload(self.api, self.store, plan)
        self.assertEqual(self.api.updates, [])
        self.assertEqual(self.api.thumbnails, [])

    def test_missing_or_false_or_wrong_id_update_ack_cannot_schedule_or_repeat(self):
        for mode in ("missing", "false", "wrong-id"):
            with self.subTest(mode=mode):
                self.store.path.unlink(missing_ok=True)
                self.missing_api().ack_mode = mode
                plan = self.plan(ai_face=True, user_selection=self.selection(), publish_at=FUTURE)
                self.seed_existing_private(plan)
                with self.assertRaises(uploader.UploadError):
                    uploader.upload(self.api, self.store, plan)
                self.assertEqual(self.api.update_attempts, 1)
                self.assertEqual(self.api.thumbnails, [])
                with self.assertRaises(uploader.UploadError):
                    uploader.upload(self.api, self.store, plan)
                self.assertEqual(self.api.update_attempts, 1)
                self.assertEqual(self.api.inserts, [])
                self.assertEqual(self.store.read()["video_id"], VIDEO_ID)

    def test_ambiguous_confirmation_does_not_retry_or_reupload(self):
        self.missing_api()
        plan = self.plan(ai_face=True, user_selection=self.selection())
        self.seed_existing_private(plan)
        self.api.before_update = lambda _: (_ for _ in ()).throw(TimeoutError("unknown outcome"))
        with self.assertRaises(TimeoutError):
            uploader.upload(self.api, self.store, plan)
        self.assertEqual(self.store.read()["native_ai_writes"][-1]["outcome"], "outcome_uncertain")
        self.api.before_update = None
        with self.assertRaisesRegex(uploader.UploadError, "already been attempted"):
            uploader.upload(self.api, self.store, plan)
        self.assertEqual(self.api.update_attempts, 1)
        self.assertEqual(self.api.inserts, [])

    def test_schedule_needs_its_own_ack_when_get_omits(self):
        self.missing_api().omit_schedule_ack = True
        plan = self.plan(ai_face=True, user_selection=self.selection(), publish_at=FUTURE)
        with self.assertRaisesRegex(uploader.UploadError, "no matching official update acknowledgment"):
            uploader.upload(self.api, self.store, plan)
        state = self.store.read()
        self.assertEqual(state["phase"], "scheduling")
        self.assertEqual(state["native_ai_writes"][0]["outcome"], "acknowledged")
        self.assertEqual(state["native_ai_writes"][-1]["outcome"], "returned_without_native_ai_ack")
        self.assertFalse(uploader.native_ai_acknowledged(state, state["metadata"]))
        with self.assertRaises(uploader.UploadError):
            uploader.upload(self.api, self.store, plan)
        self.assertEqual(self.api.update_attempts, 2)
        self.assertEqual(len(self.api.inserts), 1)
        uploader.hold_private(self.api, self.store)
        self.assertNotIn("publishAt", self.api.videos[VIDEO_ID]["status"])

    def test_confirmation_readback_still_requires_correct_owner_copy_and_private_state(self):
        for change in ("owner", "title", "privacy"):
            with self.subTest(change=change):
                self.store.path.unlink(missing_ok=True)
                self.missing_api()
                plan = self.plan(ai_face=True, user_selection=self.selection())
                self.seed_existing_private(plan)
                def mutate(status):
                    if change == "owner": self.api.videos[VIDEO_ID]["snippet"]["channelId"] = "another-channel"
                    elif change == "title": self.api.videos[VIDEO_ID]["snippet"]["title"] = "Changed copy"
                    else: status["privacyStatus"] = "public"
                self.api.before_update = mutate
                with self.assertRaises(uploader.UploadError):
                    uploader.upload(self.api, self.store, plan)
                self.assertEqual(self.api.thumbnails, [])
                self.assertEqual(self.api.update_attempts, 1)

    def test_schedule_get_date_still_required_despite_true_ack(self):
        self.missing_api().ignore_schedule = True
        with self.assertRaisesRegex(uploader.UploadError, "readback did not match"):
            uploader.upload(self.api, self.store, self.plan(ai_face=True, user_selection=self.selection(), publish_at=FUTURE))
        self.assertNotEqual(self.store.read()["phase"], "scheduled")
        self.assertEqual(self.api.update_attempts, 2)

    def test_receipt_cannot_be_used_for_wrong_video_channel_source_metadata_or_response(self):
        self.missing_api()
        state = uploader.upload(self.api, self.store, self.plan(ai_music=True))
        for key in ("video_id", "channel_id", "source_sha256", "metadata_sha256", "response_sha256", "request_sha256"):
            with self.subTest(key=key):
                changed = copy.deepcopy(state)
                changed["native_ai_writes"][-1][key] = "wrong"
                self.assertFalse(uploader.native_ai_acknowledged(changed, changed["metadata"]))
        self.assertFalse(uploader.native_ai_acknowledged(state, dict(state["metadata"], title="Different title")))

    def test_saved_request_or_caption_alone_cannot_stand_in_for_server_ack(self):
        self.missing_api()
        state = uploader.upload(self.api, self.store, self.plan(ai_music=True))
        state["native_ai_writes"][-1].pop("response")
        state["metadata"]["description"] = "AI-generated content"
        self.assertFalse(uploader.native_ai_acknowledged(state, state["metadata"]))

    def test_explicit_new_schedule_after_private_hold_can_reuse_ack_workflow(self):
        self.missing_api()
        state = uploader.upload(self.api, self.store, self.plan(ai_face=True, user_selection=self.selection(), publish_at=FUTURE))
        uploader.hold_private(self.api, self.store)
        state = uploader.schedule_owned(self.api, self.store, self.store.read(), FUTURE)
        self.assertEqual(state["phase"], "scheduled")
        self.assertEqual(self.api.videos[VIDEO_ID]["status"]["publishAt"], FUTURE)
        self.assertEqual(len(self.api.inserts), 1)


if __name__ == "__main__":
    unittest.main()
