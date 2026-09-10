import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

import requests


SPEC = importlib.util.spec_from_file_location("selective_main_review", Path(__file__).resolve().parents[1] / "tools/selective_main_review.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
NOW = datetime(2026, 9, 10, 8, tzinfo=timezone.utc)


class FakeInstagram:
    def __init__(self, history):
        self.media = {r["media_id"]: {"id": r["media_id"], "timestamp": r["published_at"],
                      "media_type": "VIDEO", "media_product_type": "REELS", "permalink": "https://www.instagram.com/reel/example/"}
                      for r in history}
        self.metrics = {r["media_id"]: {"views": 1200, "reach": 1000, "shares": 100 - i,
                        "saves": 30, "avg_watch_time_ms": 15000} for i, r in enumerate(history)}
        self.requested = []

    def owned_media(self, ids):
        return {key: self.media[key] for key in ids}

    def snapshot(self, media_id):
        self.requested.append(media_id)
        return self.metrics[media_id]


def fixture(count=8):
    history, yt, sources, archives = [], [], [], {}
    for i in range(count):
        ig_id, yt_id = str(17800000000000000 + i), f"v{i:010d}"
        history.append({"media_id": ig_id, "published_at": (NOW - timedelta(days=8 + i)).isoformat(), "title": f"Buyer question {i}", "trial": i % 2 == 1})
        yt.append({"video_id": yt_id})
        source = {"instagram_id": ig_id, "bot_youtube_id": yt_id, "ai_generated": True,
                  "bot_channel_id": m.BOT_CHANNEL, "workflow_run_id": i + 1}
        sources.append(source)
        archives[ig_id] = {**source, "master_verified": True, "master_sha256": hashlib.sha256(f"video{i}".encode()).hexdigest(),
                          "cover_sha256": hashlib.sha256(f"cover{i}".encode()).hexdigest()}
    ledger = {"format": "selective-main-review-v1", "known_sources": sources, "reviews": [], "promotions": []}
    return history, yt, ledger, archives, FakeInstagram(history)


def approval(source):
    return {"instagram_id": source["instagram_id"], "decision": "approved", "reviewer": "review fixture",
            "reviewed_at": NOW.isoformat(), "reviewed_video_sha256": source["master_sha256"],
            "reviewed_cover_sha256": source["cover_sha256"], "checks": {key: True for key in m.REVIEW_CHECKS},
            "ai_flags": {"ai_visuals": True, "ai_voice": True, "ai_music": True,
                         "ai_face": False, "containsSyntheticMedia": True}}


class SelectionTests(unittest.TestCase):
    def run_batch(self, values):
        return m.review_batch(*values, NOW)

    def test_unreviewed_master_is_hold_not_automatic_approval(self):
        report = self.run_batch(fixture())
        self.assertEqual(report["status"], "HOLD")
        self.assertIsNone(report["candidate_for_main"])
        self.assertIsNotNone(report["priority_for_full_review"])
        self.assertEqual(report["batch"][0]["shares_per_reach"], 0.1)
        self.assertEqual(report["batch"][0]["paid_organic_split"], "unknown")

    def test_one_candidate_requires_all_reviews_and_exact_master(self):
        values = fixture()
        source = next(iter(values[3].values()))
        values[2]["reviews"].append(approval(source))
        report = self.run_batch(values)
        self.assertEqual(report["status"], "CANDIDATE_FOR_MAIN_REVIEW")
        self.assertEqual(report["candidate_for_main"]["video_sha256"], source["master_sha256"])
        self.assertIs(report["publishes"], False)

    def test_legacy_ledger_cannot_assert_a_verified_master(self):
        history, yt, ledger, archives, ig = fixture()
        source = next(iter(archives.values()))
        ledger["known_sources"][0].update(source)
        ledger["reviews"].append(approval(source))
        report = self.run_batch((history, yt, ledger, {}, ig))
        self.assertIsNone(report["candidate_for_main"])
        self.assertFalse(report["batch"][0]["master_verified"])

    def test_hash_change_or_missing_ai_flag_prevents_candidate(self):
        for defect in ("hash", "cover_hash", "ai_flag", "face", "facts"):
            with self.subTest(defect=defect):
                values = fixture()
                source = next(iter(values[3].values()))
                review = approval(source)
                if defect == "hash": review["reviewed_video_sha256"] = "0" * 64
                if defect == "cover_hash": review["reviewed_cover_sha256"] = "0" * 64
                if defect == "ai_flag": del review["ai_flags"]["ai_music"]
                if defect == "face": review["ai_flags"]["ai_face"] = True
                if defect == "facts": review["checks"]["facts"] = False
                values[2]["reviews"].append(review)
                self.assertIsNone(self.run_batch(values)["candidate_for_main"])

    def test_explicit_reject_cannot_win_on_shares(self):
        values = fixture()
        first = values[0][0]["media_id"]
        values[2]["reviews"].append({"instagram_id": first, "decision": "rejected", "reasons": ["Unsupported temperature prescription"]})
        values[4].metrics[first]["shares"] = 100000
        report = self.run_batch(values)
        self.assertEqual(report["batch"][0]["content_status"], "REJECT")
        self.assertNotEqual(report["priority_for_full_review"]["instagram_id"], first)
        self.assertIsNone(report["candidate_for_main"])

    def test_unknown_real_and_immature_sources_do_not_receive_metrics(self):
        values = fixture()
        del values[2]["known_sources"][0]
        del values[3][values[0][0]["media_id"]]
        young_id = values[0][1]["media_id"]
        values[0][1]["published_at"] = NOW.isoformat()
        report = self.run_batch(values)
        self.assertEqual(report["excluded_counts"]["unknown_origin_or_join"], 1)
        self.assertEqual(report["excluded_counts"]["immature"], 1)
        self.assertNotIn(young_id, values[4].requested)

    def test_live_publish_time_takes_precedence_over_old_history(self):
        values = fixture()
        first = values[0][0]["media_id"]
        values[4].media[first]["timestamp"] = NOW.isoformat()
        report = self.run_batch(values)
        self.assertEqual(report["excluded_counts"]["immature"], 1)
        self.assertNotIn(first, values[4].requested)

    def test_no_repeat_source_or_master_after_main_reservation(self):
        values = fixture()
        first = next(iter(values[3].values()))
        values[2]["promotions"].append({"bot_youtube_id": first["bot_youtube_id"], "main_youtube_id": None, "status": "reserved"})
        report = self.run_batch(values)
        self.assertEqual(report["excluded_counts"]["already_promoted_or_reserved"], 1)
        self.assertNotIn(first["instagram_id"], values[4].requested)

    def test_cap_ten_and_hold_below_seven(self):
        self.assertEqual(len(self.run_batch(fixture(14))["batch"]), 10)
        values = fixture(6)
        values[2]["reviews"].append(approval(next(iter(values[3].values()))))
        report = self.run_batch(values)
        self.assertIsNone(report["candidate_for_main"])
        self.assertTrue(any("Fewer than7" in reason for reason in report["batch_hold_reasons"]))

    def test_zero_reach_is_missing_ratio_and_not_eligible(self):
        values = fixture()
        for metric in values[4].metrics.values(): metric.update(reach=0, shares=0, saves=0)
        values[2]["reviews"].append(approval(next(iter(values[3].values()))))
        report = self.run_batch(values)
        self.assertIsNone(report["candidate_for_main"])
        self.assertIsNone(report["batch"][0]["shares_per_reach"])

    def test_wrong_bot_origin_and_conflicting_join_refused(self):
        for defect in ("channel", "join"):
            values = fixture()
            if defect == "channel": values[2]["known_sources"][0]["bot_channel_id"] = m.MAIN_CHANNEL
            else: next(iter(values[3].values()))["bot_youtube_id"] = "other000000"
            with self.assertRaises(m.ReviewError): self.run_batch(values)


class NetworkAndArchiveTests(unittest.TestCase):
    def test_auth_and_transport_error_do_not_become_empty_data(self):
        session = Mock()
        session.get.return_value.status_code = 403
        session.get.return_value.text = "sensitive body must not be printed"
        with self.assertRaisesRegex(m.ReviewError, "HTTP 403"):
            m.safe_get(session, "https://example.test", service="Instagram")
        session.get.side_effect = requests.ConnectionError("sensitive URL")
        with self.assertRaisesRegex(m.ReviewError, "transport failed"):
            m.safe_get(session, "https://example.test", service="Instagram")

    def test_missing_metric_does_not_become_zero(self):
        client = m.Instagram("private-test-token", m.IG_ACCOUNT)
        client.get = Mock(return_value={"data": [{"name": "views", "values": [{"value": 123}]}]})
        with self.assertRaisesRegex(m.ReviewError, "omitted required metrics"):
            client.snapshot("17800000000000000")

    def test_wrong_instagram_identity_and_unowned_source_refused(self):
        client = m.Instagram("private-test-token", m.IG_ACCOUNT)
        client.get = Mock(return_value={"id": m.IG_ACCOUNT, "username": "wrong"})
        with self.assertRaisesRegex(m.ReviewError, "identity"):
            client.owned_media({"17800000000000000"})
        client.get = Mock(side_effect=[{"id": m.IG_ACCOUNT, "username": m.IG_USERNAME}, {"data": []}])
        with self.assertRaisesRegex(m.ReviewError, "ownership"):
            client.owned_media({"17800000000000000"})

    def make_archive(self, path, corrupt=False, test_mode=False):
        video, cover = b"original video", b"reviewed cover"
        manifest = {"format": "daily-short-review-v1", "ai_generated": True, "test_mode": test_mode,
                    "source_posts": {"bot_youtube": "v0000000000", "instagram": "17800000000000000"},
                    "workflow": {"github_repository": m.REPOSITORY, "github_run_id": "123"},
                    "assets": {kind: {"file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                               for kind, name, data in [("video", "short.mp4", video), ("cover", "cover.png", cover)]}}
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("review_manifest.json", json.dumps(manifest))
            archive.writestr("short.mp4", b"changed video!" if corrupt else video)
            archive.writestr("cover.png", cover)

    def test_original_archive_hashes_checked_and_test_runs_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.zip"
            self.make_archive(path)
            source = m.archive_source(path, artifact_id=456, run_id=123)
            self.assertTrue(source["master_verified"])
            self.assertNotIn("script", source)
            self.make_archive(path, corrupt=True)
            with self.assertRaises(m.ReviewError): m.archive_source(path)
            self.make_archive(path, test_mode=True)
            self.assertIsNone(m.archive_source(path))

    def test_archive_run_must_match_github_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.zip"
            self.make_archive(path)
            with self.assertRaisesRegex(m.ReviewError, "workflow ID"):
                m.archive_source(path, run_id=999)

    def test_failed_or_other_workflow_archive_never_downloaded(self):
        item = {"id": 456, "name": "rendered-video-backup", "created_at": (NOW - timedelta(days=8)).isoformat(),
                "expired": False, "size_in_bytes": 1000, "workflow_run": {"id": 123}}
        for defect in ("failed", "wrong_workflow", "other_branch", "other_repo"):
            run = {"path": ".github/workflows/daily_short.yml", "head_branch": "main", "conclusion": "success",
                   "head_repository": {"full_name": m.REPOSITORY}}
            if defect == "failed": run["conclusion"] = "failure"
            if defect == "wrong_workflow": run["path"] = ".github/workflows/other.yml"
            if defect == "other_branch": run["head_branch"] = "experiment"
            if defect == "other_repo": run["head_repository"]["full_name"] = "other/repository"
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as directory:
                with patch.object(m, "safe_get", side_effect=[{"artifacts": [item]}, run]) as get:
                    sources, scanned = m.discover_archives("private-test-token", NOW, directory)
                self.assertFalse(sources)
                self.assertEqual(get.call_count, 2)
                self.assertIn("excluded", scanned[0])


if __name__ == "__main__":
    unittest.main()
