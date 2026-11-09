import datetime
import os
import unittest
from unittest.mock import Mock, patch

from tools import omni_video as omni


class PickModel(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("OMNI_MODEL", None)

    def tearDown(self):
        os.environ.pop("OMNI_MODEL", None)
        if self._saved is not None:
            os.environ["OMNI_MODEL"] = self._saved

    def test_before_the_google_switch_keeps_1_1(self):
        self.assertEqual(omni.pick_model(datetime.date(2026, 12, 2)), omni.OMNI_11)

    def test_from_3_dec_asks_1_2(self):
        self.assertEqual(omni.pick_model(datetime.date(2026, 12, 3)), omni.OMNI_12)
        self.assertEqual(omni.pick_model(datetime.date(2027, 1, 15)), omni.OMNI_12)

    def test_override_wins_and_blank_override_is_ignored(self):
        os.environ["OMNI_MODEL"] = "gemini-omni-1.1-flash"
        self.assertEqual(omni.pick_model(datetime.date(2026, 12, 10)), omni.OMNI_11)
        os.environ["OMNI_MODEL"] = "   "
        self.assertEqual(omni.pick_model(datetime.date(2026, 12, 10)), omni.OMNI_12)

    def test_prices_follow_the_model(self):
        self.assertAlmostEqual(omni.price_per_second(omni.OMNI_11), 0.10136, places=5)
        self.assertAlmostEqual(omni.price_per_second(omni.OMNI_12), 0.069504, places=6)

    def test_only_1_1_lacks_the_duration_field(self):
        self.assertFalse(omni.takes_duration(omni.OMNI_11))
        self.assertTrue(omni.takes_duration(omni.OMNI_12))


class RequestBody(unittest.TestCase):
    def body_for(self, model, seconds):
        refused = Mock(status_code=400, text="stop here")
        with patch.object(omni, "MODEL", model), patch.object(omni.requests, "post", return_value=refused) as post:
            omni.generate_clip("a folded tee", "/tmp/never-written.mp4", seconds=seconds, api_key="test")
        return post.call_args.kwargs["json"]

    def test_1_1_body_has_no_duration(self):
        body = self.body_for(omni.OMNI_11, 8)
        self.assertEqual(body["model"], omni.OMNI_11)
        self.assertNotIn("duration", body["response_format"])

    def test_1_2_body_asks_the_clip_length(self):
        body = self.body_for(omni.OMNI_12, 8)
        self.assertEqual(body["model"], omni.OMNI_12)
        self.assertEqual(body["response_format"]["duration"], "8s")
        self.assertEqual(body["response_format"]["resolution"], "720p")

    def test_1_2_duration_is_a_whole_second_inside_3_to_15(self):
        self.assertEqual(self.body_for(omni.OMNI_12, 8.0)["response_format"]["duration"], "8s")
        self.assertEqual(self.body_for(omni.OMNI_12, 2)["response_format"]["duration"], "3s")
        self.assertEqual(self.body_for(omni.OMNI_12, 20)["response_format"]["duration"], "15s")


if __name__ == "__main__":
    unittest.main()
