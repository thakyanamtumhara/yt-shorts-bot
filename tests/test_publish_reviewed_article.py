import base64
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("reviewed", Path(__file__).resolve().parents[1] / "tools/publish_reviewed_article.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class FakeS3:
    def __init__(self, objects):
        self.objects = {key: {"data": data, "etag": m.digest(data), "content_type": "text/html", "cache_control": "no-cache"} for key, data in objects.items()}
        self.writes = []
        self.fail_after = None
        self.race_key = None

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        return {"Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(Prefix)][:MaxKeys]}

    def get_object(self, Bucket, Key):
        obj = self.objects[Key]
        return {"Body": io.BytesIO(obj["data"]), "ETag": obj["etag"], "ContentType": obj["content_type"], "CacheControl": obj["cache_control"]}

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl, **condition):
        if self.race_key == Key:
            self.race_key = None
            self.objects[Key] = {"data": b"concurrent edit", "etag": "other", "content_type": ContentType, "cache_control": CacheControl}
        old = self.objects.get(Key)
        if condition.get("IfNoneMatch") == "*" and old:
            raise RuntimeError("412")
        if "IfMatch" in condition and (not old or condition["IfMatch"] != old["etag"]):
            raise RuntimeError("412")
        self.objects[Key] = {"data": Body, "etag": m.digest(Body), "content_type": ContentType, "cache_control": CacheControl}
        self.writes.append(("put", Key))
        if self.fail_after == Key:
            self.fail_after = None
            raise RuntimeError("response lost")
        return {"ETag": m.digest(Body)}

    def delete_object(self, Bucket, Key, IfMatch):
        if self.objects[Key]["etag"] != IfMatch:
            raise RuntimeError("412")
        del self.objects[Key]
        self.writes.append(("delete", Key))


def fixture():
    slug = "reviewed-warehouse"
    url = m.BASE + f"/p/{slug}.html"
    history = {"slug": slug, "url": url, "title": "Reviewed warehouse", "date": "2026-09-20T19:00:00+05:30", "vid_url": "https://www.youtube.com/watch?v=UQBlxoDQ2Dw", "description": "Check stock & samples", "tags": [], "word_count": 605, "topic": "stock"}
    files = []
    for suffix, data, content_type in [(".html", f'<!doctype html><html>{url} UQBlxoDQ2Dw</html>'.encode(), "text/html; charset=utf-8"), ("-hero.webp", b"reviewed hero", "image/webp"), ("-stock-room.jpg", b"reviewed source", "image/jpeg")]:
        files.append({"key": "p/" + slug + suffix, "data": base64.b64encode(data).decode(), "sha256": m.digest(data), "content_type": content_type})
    payload = {"history": history, "files": files, "card_html": f'<article class="post-card"><a href="/p/{slug}.html">Reviewed</a></article>'}
    meta = {"schema_version": 1, "id": "warehouse-2026-09", "status": "reviewed", "publish_at": history["date"], "source_video_id": "UQBlxoDQ2Dw", "slug": slug}
    job = m.encrypt_payload(meta, payload, b"k" * 32)
    schema = {"@type": "CollectionPage", "mainEntity": {"@type": "ItemList", "numberOfItems": 2, "itemListElement": [{"position": 1, "url": m.BASE + "/"}, {"position": 2, "url": m.BASE + "/p/old.html"}]}}
    index = '<script type="application/ld+json">' + json.dumps(schema) + '</script><p class="post-count">1 articles published</p><section class="posts-grid" id="posts"><article class="post-card"><a href="/p/old.html">Old</a></article></section><h2>All Articles</h2>\n        <ul><li><a href="/p/old.html">Old</a></li></ul>'
    objects = {"p/index.html": index.encode(), "p/map.xml": b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.org/old</loc></url></urlset>', "p/feed.xml": b'<rss><channel><lastBuildDate>old</lastBuildDate><item><guid>https://example.org/old</guid></item></channel></rss>'}
    return job, payload, objects


class ReviewedTests(unittest.TestCase):
    def setUp(self):
        self.job, self.payload, objects = fixture()
        self.original = objects
        self.fake = FakeS3(objects)
        self.storage = m.Storage(self.fake)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.history = Path(self.tmp.name) / "blog_history.json"
        self.history.write_text("[]")
        self.now = datetime(2026, 9, 20, 14, tzinfo=timezone.utc)

    def publish(self):
        return m.publish(self.job, self.payload, self.storage, self.history, self.now, lambda _: True)

    def public_writes(self):
        return [operation for operation in self.fake.writes if not operation[1].startswith(m.PREFIX)]

    def test_authenticated_ciphertext_and_metadata(self):
        self.assertEqual(m.decrypt_payload(self.job, b"k" * 32), self.payload)
        altered = {**self.job, "publish_at": "2026-09-19T19:00:00+05:30"}
        with self.assertRaisesRegex(m.Refused, "authentication"):
            m.decrypt_payload(altered, b"k" * 32)
        with self.assertRaises(m.Refused):
            m.decrypt_payload(self.job, b"z" * 32)
        self.assertNotIn("Check stock", json.dumps(self.job))

    def test_future_guard_makes_no_writes_or_public_check(self):
        with self.assertRaisesRegex(m.Refused, "not due"):
            m.publish(self.job, self.payload, self.storage, self.history, datetime(2026, 9, 19, tzinfo=timezone.utc), lambda _: self.fail("public check too early"))
        self.assertFalse(self.fake.writes)

    def test_private_source_blocks_all_writes(self):
        with self.assertRaisesRegex(m.Refused, "not publicly"):
            m.publish(self.job, self.payload, self.storage, self.history, self.now, lambda _: False)
        self.assertFalse(self.fake.writes)

    def test_unlisted_source_rejected_before_oembed(self):
        response = io.BytesIO(json.dumps({"items": [{"id": self.job["source_video_id"], "status": {"privacyStatus": "unlisted"}}]}).encode())
        response.status = 200
        with patch.dict(m.os.environ, {"YOUTUBE_API_KEY_1": "test-key"}), patch.object(m, "urlopen", return_value=response) as request:
            self.assertFalse(m.source_available(self.job["source_video_id"]))
            self.assertEqual(request.call_count, 1)

    def test_valid_empty_api_response_is_private_future_source(self):
        response = io.BytesIO(b'{"items":[]}')
        response.status = 200
        with patch.dict(m.os.environ, {"YOUTUBE_API_KEY_1": "test-key"}), patch.object(m, "urlopen", return_value=response):
            check = m.verify_only(self.job, self.payload, self.storage, datetime(2026, 9, 9, tzinfo=timezone.utc), m.source_available)
            self.assertFalse(check["source_public"])
            self.assertTrue(check["future_guard_active"])
        self.assertFalse(self.fake.writes)

    def test_invalid_key_or_transport_fails_future_verification_without_secret(self):
        with patch.dict(m.os.environ, {"YOUTUBE_API_KEY_1": "secret-test-value"}), patch.object(m, "urlopen", side_effect=RuntimeError("403 key=secret-test-value")):
            with self.assertRaisesRegex(m.Refused, "verification unavailable") as raised:
                m.verify_only(self.job, self.payload, self.storage, datetime(2026, 9, 9, tzinfo=timezone.utc), m.source_available)
            self.assertNotIn("secret-test-value", str(raised.exception))
        self.assertFalse(self.fake.writes)

    def test_missing_key_and_malformed_api_response_fail(self):
        with patch.dict(m.os.environ, {}, clear=True):
            with self.assertRaisesRegex(m.Refused, "is missing"):
                m.source_available(self.job["source_video_id"])
        response = io.BytesIO(b'{"unexpected":"data"}')
        response.status = 200
        with patch.dict(m.os.environ, {"YOUTUBE_API_KEY_1": "test-key"}), patch.object(m, "urlopen", return_value=response):
            with self.assertRaisesRegex(m.Refused, "response validation failed"):
                m.source_available(self.job["source_video_id"])

    def test_public_source_with_broken_oembed_fails_verification(self):
        response = io.BytesIO(json.dumps({"items": [{"id": self.job["source_video_id"], "status": {"privacyStatus": "public"}}]}).encode())
        response.status = 200
        with patch.dict(m.os.environ, {"YOUTUBE_API_KEY_1": "test-key"}), patch.object(m, "urlopen", side_effect=[response, RuntimeError("network")]):
            with self.assertRaisesRegex(m.Refused, "oEmbed check failed"):
                m.source_available(self.job["source_video_id"])

    def test_six_owned_writes_and_existing_aggregates_preserved(self):
        tx = self.publish()
        self.assertEqual(len(self.public_writes()), 6)
        self.assertEqual(tx.state["phase"], "published")
        index = self.fake.objects["p/index.html"]["data"].decode()
        self.assertIn('2 articles published', index)
        self.assertIn('<article class="post-card"><a href="/p/old.html">Old</a></article>', index)
        self.assertIn('https://example.org/old', self.fake.objects["p/map.xml"]["data"].decode())
        self.assertIn('<item><guid>https://example.org/old</guid></item>', self.fake.objects["p/feed.xml"]["data"].decode())
        self.assertEqual(json.loads(self.history.read_text()), [self.payload["history"]])
        self.assertEqual(self.fake.writes[0][1], tx.key)
        for step in tx.state["steps"]:
            if step.get("backup_key"):
                self.assertLess(self.fake.writes.index(("put", step["backup_key"])), self.fake.writes.index(("put", step["key"])))

    def test_rerun_no_public_duplicate_and_recovers_history(self):
        self.publish()
        self.history.write_text("[]")
        self.publish()
        self.assertEqual(len(self.public_writes()), 6)
        self.assertEqual(len(json.loads(self.history.read_text())), 1)

    def test_lost_article_response_reconciles_without_reupload(self):
        key = self.payload["files"][0]["key"]
        self.fake.fail_after = key
        with self.assertRaises(m.Refused):
            self.publish()
        self.publish()
        self.assertEqual(self.public_writes().count(("put", key)), 1)

    def test_lost_index_response_uses_original_backup(self):
        self.fake.fail_after = "p/index.html"
        with self.assertRaises(m.Refused):
            self.publish()
        self.publish()
        self.assertEqual(self.public_writes().count(("put", "p/index.html")), 1)
        self.assertEqual(self.fake.objects["p/index.html"]["data"].count(b'<article class="post-card"'), 2)

    def test_concurrent_change_is_not_overwritten(self):
        self.fake.race_key = "p/index.html"
        with self.assertRaises(m.Refused):
            self.publish()
        self.assertEqual(self.fake.objects["p/index.html"]["data"], b"concurrent edit")
        with self.assertRaisesRegex(m.Refused, "concurrently"):
            self.publish()

    def test_reviewed_copy_change_blocked_by_receipt(self):
        self.publish()
        changed = {**self.job, "ciphertext": self.job["ciphertext"] + "A"}
        with self.assertRaisesRegex(m.Refused, "Queue changed"):
            m.Transaction(self.storage, changed)

    def test_existing_article_not_claimed_as_owned(self):
        key = self.payload["files"][0]["key"]
        self.fake.objects[key] = {"data": b"other", "etag": "other", "content_type": "text/html", "cache_control": "no-cache"}
        with self.assertRaisesRegex(m.Refused, "Unexpected existing"):
            self.publish()
        self.assertEqual(self.fake.objects[key]["data"], b"other")

    def test_undo_restores_aggregates_deletes_only_owned_new_keys(self):
        tx = self.publish()
        tx.undo()
        for key, body in self.original.items():
            self.assertEqual(self.fake.objects[key]["data"], body)
        for item in self.payload["files"]:
            self.assertNotIn(item["key"], self.fake.objects)
        self.assertEqual(tx.state["phase"], "rolled_back")
        count = len(self.fake.writes)
        tx.undo()
        self.assertEqual(len(self.fake.writes), count)
        with self.assertRaisesRegex(m.Refused, "rolled back"):
            self.publish()

    def test_undo_refuses_later_site_edits_before_any_delete(self):
        tx = self.publish()
        self.fake.objects["p/index.html"]["data"] = b"new work"
        self.fake.writes.clear()
        with self.assertRaisesRegex(m.Refused, "later work"):
            tx.undo()
        self.assertFalse(self.fake.writes)

    def test_missing_s3_key_uses_exact_prefix_listing(self):
        with patch.object(self.fake, "get_object", side_effect=AssertionError("missing key GET should not happen")):
            self.assertIsNone(self.storage.get("p/absent"))
        with patch.object(self.fake, "list_objects_v2", side_effect=RuntimeError("403")):
            with self.assertRaisesRegex(m.Refused, "S3 read failed"):
                self.storage.get("p/absent")
        with patch.object(self.fake, "get_object", side_effect=RuntimeError("404 after listing")):
            with self.assertRaisesRegex(m.Refused, "S3 read failed"):
                self.storage.get("p/index.html")

    def test_later_day_cannot_backdate_new_publication(self):
        self.now = datetime(2026, 9, 21, 14, tzinfo=timezone.utc)
        with self.assertRaisesRegex(m.Refused, "Publication day changed"):
            self.publish()
        self.assertFalse(self.fake.writes)

    def test_rss_enclosure_has_exact_asset_length(self):
        feed = m.append_xml("p/feed.xml", self.original["p/feed.xml"], self.payload)
        root = m.ET.fromstring(feed)
        enclosure = root.find("./channel/item/enclosure")
        self.assertEqual(int(enclosure.attrib["length"]), len(b"reviewed hero"))

    def test_future_today_reserves_slot_tomorrow_allows_standard(self):
        due, handled = m.queue_choice([self.job], datetime(2026, 9, 20, 12, tzinfo=timezone.utc))
        self.assertFalse(due)
        self.assertTrue(handled)
        self.assertFalse(m.queue_choice([self.job], datetime(2026, 9, 19, 12, tzinfo=timezone.utc))[1])

    def test_default_dry_run_does_not_need_secret_or_cloud_clients(self):
        queue = Path(self.tmp.name) / "queue"
        queue.mkdir()
        (queue / "draft.json").write_text(json.dumps(self.job))
        with patch.object(m, "secret_key", side_effect=AssertionError("decrypted on dry run")):
            m.main(["--queue-dir", str(queue)])

    def test_verify_future_private_draft_reads_but_never_writes(self):
        check = m.verify_only(self.job, self.payload, self.storage, datetime(2026, 9, 9, tzinfo=timezone.utc), lambda _: False)
        self.assertTrue(check["future_guard_active"])
        self.assertFalse(check["source_public"])
        self.assertEqual(check["writes"], 0)
        self.assertFalse(self.fake.writes)
        self.assertEqual(self.history.read_text(), "[]")

    def test_verify_due_private_source_fails_without_writes(self):
        with self.assertRaisesRegex(m.Refused, "Due source"):
            m.verify_only(self.job, self.payload, self.storage, self.now, lambda _: False)
        self.assertFalse(self.fake.writes)

    def test_verify_cannot_be_combined_with_execute(self):
        with self.assertRaisesRegex(m.Refused, "cannot mutate"):
            m.main(["--verify-only", "--execute"])

    def test_unknown_layout_fails_before_aggregate_write(self):
        self.fake.objects["p/index.html"]["data"] = b"<html>new site layout</html>"
        with self.assertRaisesRegex(m.Refused, "no article cards"):
            self.publish()
        self.assertFalse(self.fake.writes)

    def test_aggregate_change_between_plan_and_backup_refused(self):
        tx = m.Transaction(self.storage, self.job)
        old = self.fake.objects["p/index.html"]["data"]
        self.fake.objects["p/index.html"]["data"] = old + b"new concurrent content"
        with self.assertRaisesRegex(m.Refused, "changed after planning"):
            tx.write("p/index.html", b"planned body", "text/html", aggregate=True, expected_previous=m.digest(old))
        self.assertFalse(self.fake.writes)

    def test_history_collision_fails_before_any_write(self):
        self.history.write_text(json.dumps([{**self.payload["history"], "title": "Other publication"}]))
        with self.assertRaisesRegex(m.Refused, "history entry differs"):
            self.publish()
        self.assertFalse(self.fake.writes)

    def test_workflow_runs_reviewed_first_and_fails_closed(self):
        path = Path(__file__).resolve().parents[1] / ".github/workflows/sunday_recap.yml"
        workflow = path.read_text()
        self.assertLess(workflow.index("Publish due reviewed"), workflow.index("Run Sunday recap"))
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("if: steps.reviewed.outputs.handled_today != 'true'", workflow)
        self.assertNotIn("continue-on-error", workflow)
        self.assertIn("git add blog_history.json", workflow)
        self.assertIn("python3 tools/publish_reviewed_article.py --verify-only", workflow)
        self.assertIn("if: always() && inputs.verify_only != true", workflow)


if __name__ == "__main__":
    unittest.main()
