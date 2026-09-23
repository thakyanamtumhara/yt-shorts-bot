import json
from io import BytesIO
import unittest
from unittest.mock import Mock, patch
import urllib.error

import social_watch as watch


VIDEO_ID = "1921570022137051"


class FacebookWatcherTest(unittest.TestCase):
    def check(self, status, published=None, privacy=None, *, legacy=False, errors=None):
        state = {"facebook": {VIDEO_ID: {"confirmed": legacy, "queued_at": "2026-09-24 00:00"}}}
        fields = {"status": {"id": VIDEO_ID, "status": status},
                  "published": {"id": VIDEO_ID, "published": published},
                  "privacy": {"id": VIDEO_ID, "privacy": privacy}}
        def read(vid, field):
            self.assertEqual(vid, VIDEO_ID)
            return (None, errors[field]) if errors and field in errors else (fields[field], None)
        with patch.object(watch, "FB_TOKEN", "token"), \
                patch.object(watch, "facebook_read_fields", side_effect=read), \
                patch.object(watch, "token_healthy", side_effect=AssertionError("Do not gate Facebook on IG")):
            alerts, info = watch.check_facebook(state)
        return state["facebook"][VIDEO_ID], alerts, info

    def test_ready_alone_remains_pending_without_claiming_failed_publication(self):
        meta, alerts, _ = self.check({"video_status": "ready"})
        self.assertFalse(meta["confirmed"])
        self.assertEqual(meta["publication_check"]["state"], "publication_unverified")
        self.assertIn("processing ready alone is insufficient", alerts[0])
        self.assertNotIn("not live", alerts[0])
        self.assertNotIn("publish it", alerts[0])

    def test_exact_recovery_api_shape_confirms_publication_and_public_privacy(self):
        meta, alerts, _ = self.check({"video_status": "ready", "publishing_phase": {
            "status": "complete", "publish_status": "published"}}, True, {"value": "EVERYONE"})
        self.assertTrue(meta["confirmed"])
        self.assertEqual(meta["confirmation_method"], "published_public_api_v1")
        self.assertEqual(meta["publication_check"]["state"], "published_public_by_api")
        self.assertEqual(alerts, [])

    def test_published_boolean_can_confirm_with_readable_public_privacy(self):
        meta, alerts, _ = self.check({"video_status": "ready"}, True, {"value": "EVERYONE"})
        self.assertTrue(meta["confirmed"])
        self.assertEqual(alerts, [])

    def test_supported_phase_can_replace_unavailable_published_field(self):
        meta, alerts, _ = self.check({"video_status": "ready", "publishing_phase": {
            "status": "complete", "publish_status": "published"}}, privacy={"value": "EVERYONE"},
            errors={"published": "HTTP 400"})
        self.assertTrue(meta["confirmed"])
        self.assertEqual(meta["publication_check"]["read_errors"], {"published": "HTTP 400"})
        self.assertEqual(alerts, [])

    def test_unavailable_privacy_does_not_invent_public_confirmation(self):
        meta, alerts, _ = self.check({"video_status": "ready"}, True, errors={"privacy": "HTTP 400"})
        self.assertFalse(meta["confirmed"])
        self.assertEqual(meta["publication_check"]["state"], "published_privacy_unverified")
        self.assertIn("publication is confirmed, but public privacy could not be verified", alerts[0])

    def test_false_pending_conflicting_and_nonpublic_stay_pending(self):
        cases = [
            ({"video_status": "ready"}, False, "EVERYONE", "not_published_by_api"),
            ({"publishing_phase": {"status": "in_progress"}}, None, "EVERYONE", "publication_pending"),
            ({"publishing_phase": {"status": "not_started"}}, True, "EVERYONE", "conflicting_publication_signals"),
            ({"publishing_phase": {"status": "complete", "publish_status": "published"}}, False, "EVERYONE", "conflicting_publication_signals"),
            ({"video_status": "ready"}, True, "SELF", "not_public_by_api"),
        ]
        for status, published, privacy, expected in cases:
            with self.subTest(expected=expected):
                meta, alerts, _ = self.check(status, published, {"value": privacy})
                self.assertFalse(meta["confirmed"])
                self.assertEqual(meta["publication_check"]["state"], expected)
                self.assertEqual(len(alerts), 1)

    def test_legacy_ready_confirmation_is_rechecked(self):
        meta, alerts, _ = self.check({"video_status": "ready"}, legacy=True)
        self.assertFalse(meta["confirmed"])
        self.assertEqual(len(alerts), 1)

    def test_verified_entries_are_skipped_even_if_token_missing(self):
        state = {"facebook": {VIDEO_ID: {"confirmed": True, "confirmation_method": "published_public_api_v1"}}}
        with patch.object(watch, "FB_TOKEN", ""), patch.object(watch, "facebook_read_fields") as read:
            self.assertEqual(watch.check_facebook(state), ([], []))
        read.assert_not_called()

    def test_missing_token_or_invalid_id_does_not_claim_all_clear_or_request_other_ids(self):
        with patch.object(watch, "FB_TOKEN", ""), patch.object(watch, "facebook_read_fields") as read:
            alerts, _ = watch.check_facebook({"facebook": {VIDEO_ID: {"confirmed": False}}})
            self.assertIn("cannot be verified", alerts[0])
        read.assert_not_called()
        with patch.object(watch, "FB_TOKEN", "token"), patch.object(watch, "facebook_read_fields") as read:
            alerts, _ = watch.check_facebook({"facebook": {"../me": {"confirmed": False}}})
            self.assertIn("ID is invalid", alerts[0])
        read.assert_not_called()

    def test_unavailable_read_is_unknown_not_deleted(self):
        meta, alerts, _ = self.check({}, errors={"status": "HTTP 403"})
        self.assertFalse(meta["confirmed"])
        self.assertEqual(meta["publication_check"]["state"], "read_unavailable")
        self.assertIn("publication remains unknown", alerts[0])
        self.assertNotIn("deleted", alerts[0])

    def test_requests_use_exact_id_bearer_header_and_sanitize_errors(self):
        context = Mock()
        context.__enter__ = Mock(return_value=BytesIO(json.dumps({"id": VIDEO_ID, "published": True}).encode()))
        context.__exit__ = Mock(return_value=False)
        with patch.object(watch, "FB_TOKEN", "private-token"), \
                patch.object(watch.urllib.request, "urlopen", return_value=context) as request:
            data, error = watch.facebook_read_fields(VIDEO_ID, "published")
        self.assertTrue(data["published"])
        self.assertIsNone(error)
        req = request.call_args.args[0]
        self.assertEqual(req.get_method(), "GET")
        self.assertNotIn("private-token", req.full_url)
        self.assertEqual(req.headers["Authorization"], "Bearer private-token")
        self.assertIn("/" + VIDEO_ID + "?", req.full_url)
        for failure in (urllib.error.HTTPError("https://secret", 403, "private-token", {}, None),
                        RuntimeError("private-token")):
            with patch.object(watch.urllib.request, "urlopen", side_effect=failure):
                data, error = watch.facebook_read_fields(VIDEO_ID, "published")
            self.assertIsNone(data)
            self.assertNotIn("private-token", error)

    def test_mismatched_response_id_is_rejected(self):
        context = Mock()
        context.__enter__ = Mock(return_value=BytesIO(b'{"id":"999","published":true}'))
        context.__exit__ = Mock(return_value=False)
        with patch.object(watch.urllib.request, "urlopen", return_value=context):
            data, error = watch.facebook_read_fields(VIDEO_ID, "published")
        self.assertIsNone(data)
        self.assertEqual(error, "response ID mismatch")


if __name__ == "__main__":
    unittest.main()
