import copy
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import os

from PIL import Image


SPEC = importlib.util.spec_from_file_location("curated_feed", Path(__file__).parents[1] / "tools/curated_feed.py")
feed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(feed)
NOW = datetime(2026, 9, 20, 13, 10, tzinfo=feed.IST)


class FakeS3Error(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.get_error = None
        self.reject_state = lambda state: False

    def get_object(self, Bucket, Key):
        if self.get_error:
            raise self.get_error
        if Key not in self.objects:
            raise FakeS3Error("NoSuchKey")
        raw, etag = self.objects[Key]
        return {"Body": io.BytesIO(raw), "ETag": etag}

    def put_object(self, **kwargs):
        key, raw = kwargs["Key"], kwargs["Body"]
        state = json.loads(raw)
        self.puts.append(copy.deepcopy(kwargs))
        if self.reject_state(state):
            raise FakeS3Error("ServiceUnavailable")
        previous = self.objects.get(key)
        if kwargs.get("IfNoneMatch") == "*":
            if previous:
                raise FakeS3Error("PreconditionFailed")
        elif not previous or kwargs.get("IfMatch") != previous[1]:
            raise FakeS3Error("PreconditionFailed")
        etag = '"' + hashlib.sha256(raw).hexdigest() + '"'
        self.objects[key] = (raw, etag)
        return {"ETag": etag}

    def list_objects_v2(self, Bucket, Prefix, **kwargs):
        return {"Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(Prefix)], "IsTruncated": False}

    def state(self, job_id):
        return json.loads(self.objects["automation-state/ig-curated/" + job_id + ".json"][0])


class FakeInstagram:
    def __init__(self):
        self.created = []
        self.published = []
        self.responses = {}
        self.account = {"id": feed.ACCOUNT_ID, "username": feed.ACCOUNT_USERNAME}
        self.create_error = None
        self.publish_error = None
        self.before_create = None
        self.before_publish = None
        self.before_media = None
        self.reads = []

    def identity(self):
        return self.account

    def create(self, body):
        if self.before_create:
            self.before_create(body)
        self.created.append(copy.deepcopy(body))
        if self.create_error:
            raise self.create_error
        return str(1000 + len(self.created))

    def status(self, container_id):
        self.reads.append(container_id)
        responses = self.responses.get(container_id, ["FINISHED"])
        return responses.pop(0) if len(responses) > 1 else responses[0]

    def publish(self, container_id):
        if self.before_publish:
            self.before_publish()
        self.published.append(container_id)
        if self.publish_error:
            raise self.publish_error
        return "9999"

    def media(self, media_id):
        if self.before_media:
            self.before_media()
        return {"id": media_id, "username": feed.ACCOUNT_USERNAME, "caption": self.created[-1]["caption"], "permalink": "https://www.instagram.com/p/real-test-id/"}


class CuratedFeedSafeguards(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"CURATED_STATE_BUCKET": ""})
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.queue = self.base / "feed_queue"
        self.queue.mkdir()
        self.api = FakeInstagram()
        self.elapsed = 0
        self.clock = patch.object(feed.time, "monotonic", side_effect=lambda: self.elapsed)
        self.sleep = patch.object(feed.time, "sleep", side_effect=self.advance)
        self.clock.start(); self.sleep.start()
        self.addCleanup(self.clock.stop); self.addCleanup(self.sleep.stop)
        self.checks = []

    def advance(self, seconds):
        self.elapsed += seconds

    def assets(self, job):
        self.checks.append(job["id"])

    def job(self, name="checklist", images=2, due=None):
        value = {"id": name, "status": "reviewed", "publish_at": (due or NOW - timedelta(minutes=10)).isoformat(), "caption": "Your actual buying question?", "image_urls": [f"https://example.com/{name}-{i}.jpg" for i in range(images)], "image_sha256": [hashlib.sha256(str(i).encode()).hexdigest() for i in range(images)], "alt_texts": [f"Actual slide {i}" for i in range(images)]}
        path = self.queue / (name + ".json")
        path.write_text(json.dumps(value))
        return feed.load_job(path)

    def store(self, job):
        return feed.StateStore(self.queue / "state" / (job["id"] + ".json"))

    def run_job(self, job, **kwargs):
        return feed.run_job(self.api, job, self.store(job), clock=lambda: NOW, assets_check=self.assets, **kwargs)

    def run_queue(self, **kwargs):
        return feed.run_queue(self.queue, api_factory=lambda: self.api, clock=lambda: NOW, assets_check=self.assets, **kwargs)

    def test_future_job_cannot_create_or_publish(self):
        job = self.job(due=NOW + timedelta(hours=1))
        with self.assertRaisesRegex(feed.FeedError, "not due"):
            self.run_job(job, execute=True)
        self.assertEqual(self.api.created, [])
        self.assertEqual(self.api.published, [])
        self.assertFalse(self.store(job).path.exists())

    def test_empty_or_other_day_queue_falls_back_to_standard(self):
        self.assertFalse(self.run_queue(execute=True)["handled_today"])
        self.job(due=NOW + timedelta(days=1))
        self.assertEqual(self.run_queue(execute=True)["reason"], "standard_fallback")
        self.assertEqual(self.api.created, [])

    def test_later_today_reserves_feed_slot_without_publishing_early(self):
        self.job(due=NOW + timedelta(hours=1))
        self.assertEqual(self.run_queue(execute=True)["reason"], "curated_reserved_later_today")
        self.assertEqual(self.api.created, [])

    def test_default_dry_run_checks_assets_and_account_but_never_mutates(self):
        job = self.job()
        result = self.run_job(job)
        self.assertTrue(result["dry_run"])
        self.assertEqual(self.checks, [job["id"]])
        self.assertEqual(self.api.created, [])
        self.assertFalse(self.store(job).path.exists())

    def test_wrong_live_account_blocks_all_posts(self):
        self.api.account["username"] = "another_account"
        with self.assertRaisesRegex(feed.FeedError, "Live Instagram"):
            self.run_job(self.job(), execute=True)
        self.assertEqual(self.api.created, [])

    def test_child_error_stops_before_parent_and_publish_with_id_saved(self):
        job = self.job()
        self.api.responses["1001"] = ["ERROR"]
        with self.assertRaisesRegex(feed.FeedError, "container is ERROR"):
            self.run_job(job, execute=True)
        self.assertEqual(self.store(job).read()["child_ids"], ["1001"])
        self.assertEqual(len(self.api.created), 1)
        self.assertEqual(self.api.published, [])

    def test_child_and_parent_must_finish_before_publish(self):
        self.api.responses = {"1001": ["IN_PROGRESS", "FINISHED"], "1003": ["IN_PROGRESS", "FINISHED"]}
        job = self.job()
        self.api.before_publish = lambda: self.assertEqual(self.store(job).read()["parent_id"], "1003")
        state = self.run_job(job, execute=True)
        self.assertEqual(state["phase"], "published")
        self.assertEqual(self.elapsed, 2 * feed.POLL_SECONDS)
        self.assertEqual(self.api.created[-1]["children"], "1001,1002")
        self.assertEqual(self.api.created[0]["alt_text"], job["alt_texts"][0])
        self.assertEqual(self.api.published, ["1003"])

    def test_pending_forever_does_not_publish_after_deadline(self):
        self.api.responses["1001"] = ["IN_PROGRESS"]
        with self.assertRaisesRegex(feed.FeedError, "has not reached FINISHED"):
            self.run_job(self.job(), execute=True)
        self.assertEqual(self.elapsed, feed.WAIT_SECONDS)
        self.assertEqual(self.api.published, [])

    def test_single_image_uses_its_own_caption_and_no_carousel_children(self):
        job = self.job(images=1)
        self.run_job(job, execute=True)
        self.assertEqual(len(self.api.created), 1)
        self.assertEqual(self.api.created[0]["caption"], job["caption"])
        self.assertNotIn("children", self.api.created[0])

    def test_one_curated_post_per_day_including_successful_reruns(self):
        self.job("first", images=1)
        self.job("second", images=1)
        self.run_queue(execute=True)
        retry = self.run_queue(execute=True)
        self.assertEqual(retry["reason"], "curated_already_published_today")
        self.assertEqual(len(self.api.published), 1)

    def test_removing_published_job_does_not_erase_same_day_history(self):
        self.job("first", images=1)
        self.run_queue(execute=True)
        (self.queue / "first.json").unlink()
        self.job("second", images=1)
        self.assertTrue(self.run_queue(execute=True)["handled_today"])
        self.assertEqual(len(self.api.published), 1)

    def test_ambiguous_child_creation_is_not_retried(self):
        job = self.job()
        self.api.create_error = TimeoutError("request outcome unknown")
        with self.assertRaises(TimeoutError):
            self.run_job(job, execute=True)
        self.api.create_error = None
        with self.assertRaisesRegex(feed.FeedError, "POST outcome is uncertain"):
            self.run_job(job, execute=True)
        self.assertEqual(len(self.api.created), 1)

    def test_ambiguous_publish_is_not_retried_or_allowed_to_fall_back(self):
        self.job(images=1)
        self.api.publish_error = TimeoutError("response lost")
        with self.assertRaises(TimeoutError):
            self.run_queue(execute=True)
        self.api.publish_error = None
        with self.assertRaisesRegex(feed.FeedError, "POST outcome is uncertain"):
            self.run_queue(execute=True)
        self.assertEqual(len(self.api.published), 1)

    def test_media_id_is_saved_before_readback_failure(self):
        job = self.job(images=1)
        def fail_readback():
            self.assertEqual(self.store(job).read()["media_id"], "9999")
            raise TimeoutError()
        self.api.before_media = fail_readback
        with self.assertRaises(TimeoutError):
            self.run_queue(execute=True)
        self.assertTrue(self.run_queue(execute=True)["handled_today"])
        self.assertEqual(len(self.api.published), 1)

    def test_copy_or_hash_changes_cannot_reuse_existing_containers(self):
        job = self.job()
        self.api.responses["1001"] = ["IN_PROGRESS"]
        with self.assertRaises(feed.FeedError):
            self.run_job(job, execute=True)
        changed = copy.deepcopy(job)
        changed["caption"] = "Changed promise"
        with self.assertRaisesRegex(feed.FeedError, "metadata changed"):
            self.run_job(changed, execute=True)
        self.assertEqual(len(self.api.created), 1)

    def test_prepare_only_can_prepare_future_job_without_touching_queue_state(self):
        job = self.job(due=NOW + timedelta(days=6))
        private_store = feed.StateStore(self.base / "private-check.json")
        state = feed.run_job(self.api, job, private_store, execute=True, prepare_only=True, clock=lambda: NOW, assets_check=self.assets)
        self.assertEqual(state["phase"], "prepared")
        self.assertEqual(state["mode"], "prepare_only")
        self.assertFalse(self.store(job).path.exists())
        self.assertEqual(self.api.published, [])
        with self.assertRaisesRegex(feed.FeedError, "another account or mode"):
            feed.run_job(self.api, job, private_store, execute=True, clock=lambda: NOW + timedelta(days=7), assets_check=self.assets)

    def test_prepare_cli_rejects_state_inside_queue_before_client_creation(self):
        job = self.job()
        with patch.object(feed, "Instagram") as client, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                feed.main(["--queue-dir", str(self.queue), "--job", str(self.queue / "checklist.json"), "--prepare-only", "--state", str(self.store(job).path), "--execute"])
        client.assert_not_called()

    def test_jpeg_format_size_and_fingerprint_are_verified(self):
        output = io.BytesIO()
        Image.new("RGB", (1080, 1350), "blue").save(output, format="JPEG")
        raw = output.getvalue()
        feed.validate_image_bytes(raw, hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(feed.FeedError, "SHA256"):
            feed.validate_image_bytes(raw, "0" * 64)
        output = io.BytesIO()
        Image.new("RGB", (10, 10), "blue").save(output, format="PNG")
        with self.assertRaisesRegex(feed.FeedError, "RGB JPEG"):
            feed.validate_image_bytes(output.getvalue(), hashlib.sha256(output.getvalue()).hexdigest())
        with patch.object(feed, "MAX_IMAGE_BYTES", len(raw) - 1):
            with self.assertRaisesRegex(feed.FeedError, "size"):
                feed.validate_image_bytes(raw, hashlib.sha256(raw).hexdigest())

    def test_unreviewed_or_ai_flagged_jobs_are_rejected(self):
        self.job()
        path = self.queue / "checklist.json"
        value = json.loads(path.read_text())
        value["status"] = "draft"
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(feed.FeedError, "status=reviewed"):
            feed.load_job(path)
        value["status"] = "reviewed"
        value["ai_face"] = True
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(feed.FeedError, "AI flags"):
            feed.load_job(path)

    def test_workflow_runs_curated_first_and_persists_failure_state(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/ig_carousel.yml").read_text()
        self.assertLess(workflow.index("tools/curated_feed.py"), workflow.index("run: python3 daily_short.py"))
        self.assertIn("if: steps.curated.outputs.handled_today != 'true'", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn("git add feed_queue/", workflow)
        self.assertIn("git add ig_drafts/", workflow)
        self.assertIn("CURATED_STATE_BUCKET: bulkplaintshirt.com", workflow)
        self.assertIn("CURATED_STATE_PREFIX: automation-state/ig-curated", workflow)
        self.assertIn("path: feed_queue/state/*.json", workflow)

    def remote(self):
        s3 = FakeS3()
        return s3, feed.S3StateBackend(s3, "bulkplaintshirt.com", "automation-state/ig-curated")

    def test_remote_pending_is_acknowledged_before_each_graph_post(self):
        job = self.job()
        s3, backend = self.remote()
        def before_create(body):
            self.assertTrue(s3.state(job["id"])["pending"])
        self.api.before_create = before_create
        self.api.before_publish = lambda: self.assertEqual(s3.state(job["id"])["pending"], "media")
        self.api.before_media = lambda: self.assertEqual(s3.state(job["id"])["media_id"], "9999")
        self.run_queue(execute=True, state_backend=backend)
        saved = s3.state(job["id"])
        self.assertEqual(saved["media_id"], "9999")
        self.assertEqual(saved["phase"], "published")
        self.assertFalse(set(saved) - feed.REMOTE_FIELDS)
        self.assertNotIn("caption", saved)
        self.assertNotIn("permalink", saved)

    def test_remote_pending_write_failure_prevents_the_graph_post(self):
        self.job(images=1)
        s3, backend = self.remote()
        s3.reject_state = lambda state: bool(state.get("pending"))
        with self.assertRaisesRegex(feed.FeedError, "Remote feed state write"):
            self.run_queue(execute=True, state_backend=backend)
        self.assertEqual(self.api.created, [])
        self.assertEqual(self.api.published, [])

    def test_remote_pending_survives_lost_publish_and_disposable_checkout(self):
        job = self.job(images=1)
        s3, backend = self.remote()
        self.api.publish_error = TimeoutError("Graph accepted but response was lost")
        with self.assertRaises(TimeoutError):
            self.run_queue(execute=True, state_backend=backend)
        self.assertEqual(s3.state(job["id"])["pending"], "media")
        self.store(job).path.unlink()
        self.api.publish_error = None
        with self.assertRaisesRegex(feed.FeedError, "POST outcome is uncertain"):
            self.run_queue(execute=True, state_backend=backend)
        self.assertEqual(len(self.api.published), 1)

    def test_remote_failure_after_graph_id_keeps_pending_and_local_recovery_id(self):
        job = self.job(images=1)
        s3, backend = self.remote()
        s3.reject_state = lambda state: bool(state.get("media_id"))
        with self.assertRaisesRegex(feed.FeedError, "Remote feed state write"):
            self.run_queue(execute=True, state_backend=backend)
        self.assertEqual(s3.state(job["id"])["pending"], "media")
        self.assertEqual(self.store(job).read()["media_id"], "9999")
        s3.reject_state = lambda state: False
        with self.assertRaisesRegex(feed.FeedError, "POST outcome is uncertain"):
            self.run_queue(execute=True, state_backend=backend)
        self.assertEqual(len(self.api.published), 1)
        self.assertEqual(self.store(job).read()["media_id"], "9999")

    def test_remote_read_error_never_falls_back_to_local_completed_state(self):
        job = self.job(images=1)
        self.run_queue(execute=True)
        self.api.created.clear()
        self.api.published.clear()
        s3, backend = self.remote()
        s3.get_error = FakeS3Error("AccessDenied")
        with self.assertRaisesRegex(feed.FeedError, "local fallback is forbidden"):
            self.run_queue(execute=True, state_backend=backend)
        self.assertEqual(self.api.created, [])
        self.assertEqual(self.api.published, [])

    def test_conditional_create_prevents_two_initial_writers(self):
        job = self.job(images=1)
        s3, backend = self.remote()
        first = feed.StateStore(self.base / "one" / "checklist.json", backend)
        second = feed.StateStore(self.base / "two" / "checklist.json", backend)
        self.assertIsNone(first.read())
        self.assertIsNone(second.read())
        state = feed.bind_state(None, job, "publish")
        first.save(state)
        with self.assertRaisesRegex(feed.FeedError, "lost a race"):
            second.save(state)
        self.assertEqual(s3.puts[0]["IfNoneMatch"], "*")

    def test_conditional_update_does_not_overwrite_another_pending_request(self):
        job = self.job(images=1)
        s3, backend = self.remote()
        first = feed.StateStore(self.base / "one" / "checklist.json", backend)
        second = feed.StateStore(self.base / "two" / "checklist.json", backend)
        first.read()
        first.save(feed.bind_state(None, job, "publish"))
        one, two = first.read(), second.read()
        one.update({"pending": "parent", "phase": "parent_requested"})
        first.save(one)
        two.update({"pending": "media", "phase": "media_requested"})
        with self.assertRaisesRegex(feed.FeedError, "lost a race"):
            second.save(two)
        self.assertEqual(s3.state(job["id"])["pending"], "parent")
        self.assertIn("IfMatch", s3.puts[-1])

    def test_remote_history_still_blocks_second_post_without_local_files(self):
        first = self.job("first", images=1)
        s3, backend = self.remote()
        self.run_queue(execute=True, state_backend=backend)
        self.store(first).path.unlink()
        (self.queue / "first.json").unlink()
        self.job("second", images=1)
        result = self.run_queue(execute=True, state_backend=backend)
        self.assertEqual(result["reason"], "curated_already_published_today")
        self.assertEqual(len(self.api.published), 1)

    def test_prepare_cli_ignores_remote_backend_environment(self):
        self.job()
        with patch.dict(os.environ, {"CURATED_STATE_BUCKET": "bulkplaintshirt.com"}), patch.object(feed, "state_backend_from_environment") as factory, patch.object(feed, "Instagram", return_value=self.api), patch.object(feed, "run_job", return_value={"prepared": True}) as runner:
            result = feed.main(["--queue-dir", str(self.queue), "--job", str(self.queue / "checklist.json"),
                                "--prepare-only", "--state", str(self.base / "private-state.json"), "--execute"])
        self.assertEqual(result, 0)
        factory.assert_not_called()
        self.assertIsNone(runner.call_args.args[2].backend)


if __name__ == "__main__":
    unittest.main()
