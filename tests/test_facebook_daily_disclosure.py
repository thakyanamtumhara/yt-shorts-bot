import ast
import contextlib
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def response(status, payload):
    return SimpleNamespace(status_code=status, json=lambda: payload, text=str(payload))


class FacebookDailyDisclosureTests(unittest.TestCase):
    def run_publish(self, finish):
        tree = ast.parse((ROOT / 'daily_short.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'publish_fb_reel')
        post = Mock(side_effect=[response(200, {'video_id': '12345', 'upload_url': 'https://rupload.facebook.com/video-upload/v26.0/12345'}), response(200, {'success': True}), finish])
        flags = {}
        scope = {'os': os, 'requests': SimpleNamespace(post=post), 'FB_API_VERSION': 'v26.0',
                 'set_fb_reel_cover': Mock(), 'flag': lambda key, value: flags.update({key: value})}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / 'daily_short.py'), 'exec'), scope)
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / 'test.mp4'; video.write_bytes(b'test')
            out = io.StringIO()
            with patch.dict(os.environ, {'FB_PAGE_ID': '999', 'FB_PAGE_ACCESS_TOKEN': 'test-token'}, clear=True), contextlib.redirect_stdout(out):
                result = scope['publish_fb_reel'](str(video), 'An illustrative textile lesson.')
        self.assertEqual(post.call_count, 3)
        first, _, last = post.call_args_list
        self.assertEqual(first.args[0], 'https://graph.facebook.com/v26.0/999/video_reels')
        self.assertEqual(last.args[0], first.args[0])
        self.assertEqual(last.kwargs['data']['is_ai_generated'], 'true')
        self.assertEqual(last.kwargs['data']['video_id'], '12345')
        return result, flags, out.getvalue()

    def test_acknowledged_flag_keeps_visible_label_verification_separate(self):
        result, flags, log = self.run_publish(response(200, {'success': True}))
        self.assertEqual(result, '12345')
        self.assertEqual(flags['facebook_ai_disclosure']['verification'], 'finish_accepted_native_label_unverified')
        self.assertIn('native label is not independently verified', log)

    def test_flag_rejection_never_retries_without_disclosure(self):
        result, flags, log = self.run_publish(response(400, {'error': {'message': 'is_ai_generated rejected'}}))
        self.assertIsNone(result)
        self.assertEqual(flags['facebook_ai_disclosure']['video_id'], '12345')
        self.assertNotIn('PUBLISHED →', log)

    def test_http_success_without_success_acknowledgment_is_not_published(self):
        for payload in ({}, {'success': False}, {'success': 'true'}):
            with self.subTest(payload=payload):
                result, _, log = self.run_publish(response(200, payload))
                self.assertIsNone(result)
                self.assertNotIn('PUBLISHED →', log)

    def test_published_id_is_not_lost_when_flag_response_conflicts(self):
        result, flags, log = self.run_publish(response(200, {'success': True, 'is_ai_generated': False}))
        self.assertEqual(result, '12345')
        self.assertEqual(flags['facebook_ai_disclosure']['verification'], 'explicit_false_response')
        self.assertNotIn('finish accepted', log)


if __name__ == '__main__':
    unittest.main()
