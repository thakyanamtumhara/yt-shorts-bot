import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("curated_feed_alert", Path(__file__).parents[1] / "tools/curated_feed_alert.py")
alert = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(alert)


class CuratedFeedAlertTest(unittest.TestCase):
    def queue(self, published_at, publish_at="2026-11-10T13:00:00+05:30"):
        root = Path(tempfile.mkdtemp())
        (root / "state").mkdir()
        (root / "kids.json").write_text(json.dumps({"id": "kidshood-photo", "publish_at": publish_at}))
        (root / "other.json").write_text(json.dumps({"id": "other", "publish_at": publish_at}))
        (root / "state" / "kidshood-photo.json").write_text(json.dumps({"published_at": published_at}))
        return root

    def test_on_time_is_silent(self):
        queue = self.queue("2026-11-10T07:33:10+00:00")
        self.assertEqual(alert.problems({"reason": "curated_published", "job_id": "kidshood-photo"}, queue), [])

    def test_late_names_the_photo_and_its_due_time(self):
        queue = self.queue("2026-11-10T14:15:00+00:00")
        self.assertEqual(alert.problems({"reason": "curated_published", "job_id": "kidshood-photo"}, queue),
                         ["- kidshood-photo went out 405 min late (due 10 Nov 13:00 IST)"])

    def test_no_photo_this_run_is_silent(self):
        queue = self.queue("2026-11-10T14:15:00+00:00")
        for result in (None, {}, {"reason": "standard_fallback"}, {"reason": "curated_already_published_today", "job_id": "kidshood-photo"}):
            self.assertEqual(alert.problems(result, queue), [])

    def test_published_but_unreadable_time_is_reported(self):
        queue = self.queue("2026-11-10T07:33:10+00:00")
        (queue / "state" / "kidshood-photo.json").write_text("{")
        self.assertEqual(alert.problems({"reason": "curated_published", "job_id": "kidshood-photo"}, queue),
                         ["- kidshood-photo was published, but its time could not be checked"])

    def test_missing_result_file_is_silent(self):
        self.assertEqual(alert.main("/nonexistent/result.json", "/nonexistent"), 0)


if __name__ == "__main__":
    unittest.main()
