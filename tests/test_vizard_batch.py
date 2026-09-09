import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import vizard_batch as batch


class VizardBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.video = self.directory / "clip.mp4"
        self.video.write_bytes(b"finished video fixture")
        self.manifest_path = self.directory / "manifest.json"
        self.state_path = self.directory / "state.json"
        self.raw = {
            "name": "Warehouse sample",
            "local_video_path": str(self.video),
            "video_url": "https://www.bulkplaintshirt.com/p/warehouse-clip.mp4",
            "legs": {p: {"post": f"{p}: Check a sample at sale91.com", "when_ist": "2030-01-01 20:00"}
                     for p in batch.PLATFORMS},
        }
        self.now = batch.vizard.ist_millis("2029-12-01 12:00")[0] / 1000
        self.calls = []

    def load(self):
        self.manifest_path.write_text(json.dumps(self.raw))
        return batch.load_manifest(self.manifest_path, probe=lambda path: 10.5)

    def api(self, path, body=None):
        self.calls.append((path, body))
        saved = json.loads(self.state_path.read_text())
        if path == "/project/create":
            self.assertEqual(saved["create_status"], "started")
            self.assertEqual(body["getClips"], 0)
            for key in ("subtitleSwitch", "headlineSwitch", "emojiSwitch", "highlightSwitch",
                        "autoBrollSwitch", "removeSilenceSwitch"):
                self.assertEqual(body[key], 0)
            return {"code": 2000, "projectId": 123}
        if path == "/project/query/123":
            self.assertEqual(saved["projectId"], 123)
            return {"code": 2000, "videos": [{"videoId": 456}]}
        self.assertEqual(path, "/project/publish-video")
        self.assertEqual(saved["finalVideoId"], 456)
        platform = next(p for p in batch.PLATFORMS if batch.vizard.ACCOUNTS[p][0] == body["socialAccountId"])
        self.assertEqual(saved["legs"][platform]["status"], "started")
        self.assertGreater(body["publishTime"], self.now * 1000)
        return {"code": 2000, "receipt": platform}

    def run_batch(self, execute=False, api=None, **kwargs):
        manifest, fingerprint = self.load()
        return batch.run(manifest, fingerprint, self.state_path, execute=execute,
                         api=api or self.api, asset_check=lambda url, size: None,
                         now=lambda: self.now, **kwargs)

    def test_dry_run_has_no_api_mutations(self):
        result = self.run_batch()
        self.assertEqual(result["mode"], "dry-run")
        self.assertEqual(self.calls, [])
        self.assertFalse(self.state_path.exists())

    def test_invalid_date_blocks_before_create(self):
        for invalid in ("", "2030-02-30 20:00", "2030-01-01T20:00", "2030-1-1 20:00", None):
            with self.subTest(invalid=invalid):
                self.raw["legs"]["li"]["when_ist"] = invalid
                with self.assertRaisesRegex(batch.BatchError, "invalid when_ist"):
                    self.run_batch(execute=True)
        self.assertEqual(self.calls, [])

    def test_past_date_blocks_all_mutations(self):
        self.raw["legs"]["li"]["when_ist"] = "2029-01-01 20:00"
        with self.assertRaisesRegex(batch.BatchError, "future"):
            self.run_batch(execute=True)
        self.assertEqual(self.calls, [])

    def test_each_overlong_post_blocks_before_create(self):
        for platform in batch.PLATFORMS:
            original = self.raw["legs"][platform]["post"]
            self.raw["legs"][platform]["post"] = "a" * (batch.vizard.CHAR_LIMITS[platform] + 1)
            with self.subTest(platform=platform), self.assertRaisesRegex(batch.BatchError, "characters"):
                self.run_batch(execute=True)
            self.raw["legs"][platform]["post"] = original
        self.assertEqual(self.calls, [])

    def test_linkedin_unsupported_characters_block_before_create(self):
        self.raw["legs"]["li"]["post"] = "Check stock (and samples)"
        with self.assertRaisesRegex(batch.BatchError, "parentheses"):
            self.run_batch(execute=True)
        self.assertEqual(self.calls, [])

    def test_x_url_weight_and_emoji_are_counted(self):
        for post in ("a" * 260 + " sale91.com", "😀" * 141):
            self.raw["legs"]["x"]["post"] = post
            with self.subTest(post=post), self.assertRaisesRegex(batch.BatchError, "weighted count"):
                self.run_batch(execute=True)
        self.assertEqual(self.calls, [])

    def test_foreign_asset_is_rejected(self):
        for url in ("http://www.bulkplaintshirt.com/p/x.mp4", "https://bulkplaintshirt.com.evil.test/p/x.mp4",
                    "https://example.com/p/x.mp4", "https://www.bulkplaintshirt.com/p/../x.mp4"):
            self.raw["video_url"] = url
            with self.subTest(url=url), self.assertRaises(batch.BatchError):
                self.run_batch(execute=True)
        self.assertEqual(self.calls, [])

    def test_duration_must_fit_all_three_platforms(self):
        for duration in (2.9, 140, 141, float("nan")):
            output = json.dumps({"format": {"duration": str(duration)}, "streams": [{"codec_type": "video"}]})
            with self.subTest(duration=duration), patch.object(batch.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output)), self.assertRaises(batch.BatchError):
                batch.probe_video(self.video)

    def test_asset_mismatch_blocks_before_create(self):
        manifest, fingerprint = self.load()
        def bad_asset(url, size):
            raise batch.BatchError("asset mismatch")
        with self.assertRaisesRegex(batch.BatchError, "asset mismatch"):
            batch.run(manifest, fingerprint, self.state_path, execute=True, api=self.api,
                      asset_check=bad_asset, now=lambda: self.now)
        self.assertEqual(self.calls, [])

    def test_asset_preflight_reads_only_32_bytes_with_or_without_range_support(self):
        for status, headers in ((206, {"Content-Range": "bytes 0-31/2048"}),
                                (200, {"Content-Length": "2048"})):
            response = unittest.mock.MagicMock()
            response.__enter__.return_value = response
            response.status = status
            response.headers = {"Content-Type": "video/mp4", **headers}
            response.geturl.return_value = self.raw["video_url"]
            response.read.return_value = b"\x00\x00\x00\x20ftyp" + b"0" * 24
            with self.subTest(status=status), patch.object(batch.urllib.request, "urlopen", return_value=response) as opened:
                batch.check_asset(self.raw["video_url"], 2048)
                request = opened.call_args.args[0]
                self.assertEqual(request.get_method(), "GET")
                self.assertEqual(request.get_header("Range"), "bytes=0-31")
                self.assertIn("Sale91VideoPreflight", request.get_header("User-agent"))
                response.read.assert_called_once_with(32)
                response.__exit__.assert_called_once()

    def test_asset_preflight_rejects_wrong_length_mime_signature_or_redirect(self):
        for field, value in (("range", "bytes 0-31/2049"), ("mime", "text/html"),
                             ("signature", b"<html>not a video"), ("url", "https://evil.test/p/video.mp4")):
            response = unittest.mock.MagicMock()
            response.__enter__.return_value = response
            response.status = 206
            response.headers = {"Content-Type": value if field == "mime" else "video/mp4",
                                "Content-Range": value if field == "range" else "bytes 0-31/2048"}
            response.geturl.return_value = value if field == "url" else self.raw["video_url"]
            response.read.return_value = value if field == "signature" else b"\x00\x00\x00\x20ftyp" + b"0" * 24
            with self.subTest(field=field), patch.object(batch.urllib.request, "urlopen", return_value=response), self.assertRaises(batch.BatchError):
                batch.check_asset(self.raw["video_url"], 2048)

    def test_successful_run_saves_every_receipt_and_retry_does_nothing(self):
        result = self.run_batch(execute=True)
        self.assertEqual(list(result["legs"].values()), ["accepted"] * 3)
        saved = json.loads(self.state_path.read_text())
        self.assertEqual(saved["create_receipt"]["projectId"], 123)
        self.assertEqual([saved["legs"][p]["receipt"]["receipt"] for p in batch.PLATFORMS], list(batch.PLATFORMS))
        self.assertEqual(self.state_path.stat().st_mode & 0o777, 0o600)
        self.calls.clear()
        self.run_batch(execute=True)
        self.assertEqual(self.calls, [])

    def test_changed_manifest_or_local_video_cannot_reuse_state(self):
        self.run_batch(execute=True)
        self.calls.clear()
        self.raw["name"] += " changed"
        with self.assertRaisesRegex(batch.BatchError, "changed manifest"):
            self.run_batch(execute=True)
        self.raw["name"] = "Warehouse sample"
        self.video.write_bytes(b"different edited video")
        with self.assertRaisesRegex(batch.BatchError, "changed manifest"):
            self.run_batch(execute=True)
        self.assertEqual(self.calls, [])

    def test_partial_retry_skips_acknowledged_leg(self):
        manifest, fingerprint = self.load()
        with batch.State(self.state_path, fingerprint) as store:
            store.data.update({"projectId": 123, "finalVideoId": 456, "create_status": "accepted"})
            store.data["legs"]["fb"] = {"status": "accepted", "receipt": {"code": 2000, "receipt": "already saved"}}
            store.save()
        result = self.run_batch(execute=True)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual([body["socialAccountId"] for _, body in self.calls],
                         [batch.vizard.ACCOUNTS[p][0] for p in ("x", "li")])
        self.assertEqual(result["legs"]["fb"], "accepted")

    def test_poll_timeout_resumes_the_same_project(self):
        def processing(path, body=None):
            if path.startswith("/project/query/"):
                self.calls.append((path, body))
                return {"code": 1000}
            return self.api(path, body)
        with self.assertRaisesRegex(batch.BatchError, "timed out"):
            self.run_batch(execute=True, api=processing, wait_seconds=0)
        self.calls.clear()
        self.run_batch(execute=True)
        self.assertEqual(self.calls[0][0], "/project/query/123")
        self.assertNotIn("/project/create", [p for p, _ in self.calls])

    def test_ambiguous_create_never_automatically_repeats(self):
        def lost_create(path, body=None):
            self.calls.append((path, body))
            raise TimeoutError("response missing")
        with self.assertRaisesRegex(batch.BatchError, "response lost"):
            self.run_batch(execute=True, api=lost_create)
        self.calls.clear()
        with self.assertRaisesRegex(batch.BatchError, "unconfirmed"):
            self.run_batch(execute=True)
        self.assertEqual(self.calls, [])

    def test_ambiguous_publish_blocks_duplicate_and_preserves_http_error(self):
        def lost_publish(path, body=None):
            if path == "/project/publish-video":
                raise urllib.error.HTTPError("https://example.invalid", 502, "Bad Gateway", {}, io.BytesIO(b"provider response"))
            return self.api(path, body)
        with self.assertRaisesRegex(batch.BatchError, "publish response lost"):
            self.run_batch(execute=True, api=lost_publish)
        saved = json.loads(self.state_path.read_text())
        self.assertEqual(saved["legs"]["fb"]["error"]["http_status"], 502)
        self.assertEqual(saved["legs"]["fb"]["error"]["response"], "provider response")
        self.calls.clear()
        with self.assertRaisesRegex(batch.BatchError, "automatic duplicate blocked"):
            self.run_batch(execute=True)
        self.assertEqual(self.calls, [])

    def test_publish_time_is_rechecked_after_processing(self):
        def slow_processing(path, body=None):
            receipt = self.api(path, body)
            if path.startswith("/project/query/"):
                self.now = batch.vizard.ist_millis("2030-01-02 12:00")[0] / 1000
            return receipt
        with self.assertRaisesRegex(batch.BatchError, "future"):
            self.run_batch(execute=True, api=slow_processing)
        self.assertFalse(any(path == "/project/publish-video" for path, _ in self.calls))


if __name__ == "__main__":
    unittest.main()
