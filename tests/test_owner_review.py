import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from tools.owner_review import (
    OwnerReviewStop, await_owner_review, file_sha256, publish_review_copy, read_decision, review_prefix, text_overrides,
    valid_decision,
)

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 64


def response(status, decision=None):
    body = {'content': base64.b64encode(json.dumps(decision).encode()).decode()} if decision is not None else {}
    return SimpleNamespace(status_code=status, json=lambda: body)


def fetcher(*answers):
    calls = []
    queue = list(answers)

    def fetch(url, headers, timeout):
        calls.append((url, headers))
        return queue.pop(0) if len(queue) > 1 else queue[0]
    fetch.calls = calls
    return fetch


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def run(fetch, wait=300, poll=30):
    clock = Clock()
    logs = []
    try:
        return await_owner_review(run_id='123', sha=SHA, repo='o/r', token='t', wait_seconds=wait, fetch=fetch,
                                  poll_seconds=poll, sleep=clock.sleep, clock=clock, log=logs.append), clock, logs
    except OwnerReviewStop as stop:
        return stop, clock, logs


class OwnerReviewTests(unittest.TestCase):
    def test_only_a_decision_naming_this_run_and_this_exact_video_counts(self):
        good = {'run_id': '123', 'video_sha256': SHA, 'decision': 'approve', 'reviewer': 'Claude Opus 5.5'}
        self.assertTrue(valid_decision(good, 123, SHA))
        for bad in ({**good, 'run_id': '124'}, {**good, 'video_sha256': 'b' * 64}, {**good, 'decision': 'ok'},
                    {**good, 'reviewer': ' '}, None, 'approve'):
            self.assertFalse(valid_decision(bad, '123', SHA), bad)

    def test_approval_returns_and_publishing_may_go_on(self):
        decision = {'run_id': '123', 'video_sha256': SHA, 'decision': 'approve', 'reviewer': 'Claude Opus 5.5', 'notes': '100%'}
        fetch = fetcher(response(404), response(404), response(200, decision))
        result, clock, _ = run(fetch)
        self.assertEqual(result, decision)
        self.assertEqual(clock.now, 60)
        self.assertIn('/contents/review_decisions/123.json?ref=main', fetch.calls[0][0])
        self.assertEqual(fetch.calls[0][1]['Authorization'], 'Bearer t')

    def test_rejection_stops_with_the_reason(self):
        decision = {'run_id': '123', 'video_sha256': SHA, 'decision': 'reject', 'reviewer': 'Claude Opus 5.5', 'notes': 'caption hides the price'}
        result, _, _ = run(fetcher(response(200, decision)))
        self.assertIsInstance(result, OwnerReviewStop)
        self.assertIn('caption hides the price', str(result))

    def test_a_decision_for_another_video_is_ignored_and_the_deadline_holds_the_short(self):
        other = {'run_id': '123', 'video_sha256': 'b' * 64, 'decision': 'approve', 'reviewer': 'Claude Opus 5.5'}
        result, clock, logs = run(fetcher(response(200, other)), wait=90)
        self.assertIsInstance(result, OwnerReviewStop)
        self.assertIn('nothing is published', str(result))
        self.assertGreaterEqual(clock.now, 90)
        self.assertEqual(len(logs), 1)

    def test_api_errors_and_garbage_are_not_decisions(self):
        self.assertIsNone(read_decision(lambda *a, **k: response(500), 'o/r', 't', '1'))
        self.assertIsNone(read_decision(lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {'content': '!!'}), 'o/r', 't', '1'))

        def boom(*a, **k):
            raise ConnectionError('down')
        self.assertIsNone(read_decision(boom, 'o/r', 't', '1'))

    def test_review_copy_goes_to_its_run_folder_with_the_summary(self):
        uploads, puts = [], []
        s3 = SimpleNamespace(upload_file=lambda *a, **k: uploads.append((a, k)), put_object=lambda **k: puts.append(k))
        with TemporaryDirectory() as folder:
            video = Path(folder) / 'v.mp4'
            video.write_bytes(b'video')
            cover = Path(folder) / 'c.png'
            cover.write_bytes(b'png')
            prefix = publish_review_copy(s3, 'bucket', '987', video, cover, {'video_sha256': file_sha256(video)})
        self.assertEqual(prefix, 'p/review/987')
        self.assertEqual([a[2] for a, _ in uploads], ['p/review/987/video.mp4', 'p/review/987/cover.png'])
        self.assertEqual(puts[0]['Key'], 'p/review/987/review.json')
        with self.assertRaises(OwnerReviewStop):
            review_prefix('../x')

    def test_the_gate_sits_after_the_machine_reviews_and_before_any_upload(self):
        source = (ROOT / 'daily_short.py').read_text()
        gate = source.index('# ── 9z. Owner review gate')
        self.assertLess(source.index('require_native_visual_review(output_path, review_path)'), gate)
        self.assertLess(gate, source.index('# ── 10. Upload to YouTube ──'))
        self.assertIn('return', source[gate:source.index('# ── 10. Upload to YouTube ──')])



class TextOverrideTests(unittest.TestCase):
    def test_only_known_non_empty_fields_within_limits_are_taken(self):
        decision = {'decision': 'approve', 'youtube_title': '  Canva DTF Sheet Blurry? 96 vs 300 DPI  ',
                    'youtube_description': 'Body text.', 'notes': 'x', 'video_sha256': SHA}
        self.assertEqual(text_overrides(decision), {'youtube_title': 'Canva DTF Sheet Blurry? 96 vs 300 DPI',
                                                    'youtube_description': 'Body text.'})
        self.assertEqual(text_overrides({'decision': 'approve'}), {})

    def test_bad_corrections_stop_the_run(self):
        for bad in ({'youtube_title': ''}, {'youtube_title': 'x' * 101}, {'instagram_title': 5},
                    {'youtube_description': 'y' * 4001}, {'youtube_description': '   '}):
            with self.assertRaises(OwnerReviewStop):
                text_overrides(bad)

    def test_the_run_applies_corrections_only_after_an_approval_and_through_the_price_gate(self):
        source = (ROOT / 'daily_short.py').read_text()
        gate = source[source.index('# ── 9z. Owner review gate'):source.index('# ── 10. Upload to YouTube ──')]
        self.assertLess(gate.index('await_owner_review('), gate.index('text_overrides(review_decision)'))
        self.assertLess(gate.index('text_overrides(review_decision)'), gate.index('unsupported_amounts(_text, _rates)'))
        self.assertIn('loose_rupee_words(_text)', gate)
        self.assertIn('yt_title = corrections.get("youtube_title", yt_title)', gate)


class CloudReviewDispatchTests(unittest.TestCase):
    def dispatcher(self):
        import ast, os
        source = (ROOT / 'daily_short.py').read_text()
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'dispatch_cloud_review')
        scope = {'os': os}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'daily_short.py', 'exec'), scope)
        return scope['dispatch_cloud_review']

    def test_held_short_dispatches_its_own_cloud_review(self):
        from unittest.mock import patch
        calls = []
        post = lambda url, **kwargs: calls.append((url, kwargs)) or SimpleNamespace(status_code=204)
        with patch.dict('os.environ', {'GITHUB_REPOSITORY': 'owner/repo', 'GH_TOKEN_REVIEW': 'token'}):
            self.assertTrue(self.dispatcher()('123', post=post))
        url, kwargs = calls[0]
        self.assertEqual(url, 'https://api.github.com/repos/owner/repo/actions/workflows/ai_final_review.yml/dispatches')
        self.assertEqual(kwargs['json'], {'ref': 'main', 'inputs': {'run_id': '123'}})

    def test_a_failed_dispatch_never_stops_the_wait(self):
        from unittest.mock import patch
        def broken(url, **kwargs):
            raise OSError('network')
        with patch.dict('os.environ', {'GITHUB_REPOSITORY': 'owner/repo', 'GH_TOKEN_REVIEW': 'token'}):
            self.assertFalse(self.dispatcher()('123', post=lambda url, **kwargs: SimpleNamespace(status_code=403)))
            self.assertFalse(self.dispatcher()('123', post=broken))
            self.assertFalse(self.dispatcher()('12a', post=broken))
        with patch.dict('os.environ', {'GITHUB_REPOSITORY': '', 'GH_TOKEN_REVIEW': ''}):
            self.assertFalse(self.dispatcher()('123', post=broken))

    def test_dispatch_happens_after_the_copy_is_held_and_before_the_wait(self):
        source = (ROOT / 'daily_short.py').read_text()
        held = source.index('review_folder = publish_review_copy(')
        self.assertLess(held, source.index('dispatch_cloud_review(review_run)'))
        self.assertLess(source.index('dispatch_cloud_review(review_run)'), source.index('review_decision = await_owner_review('))


if __name__ == '__main__':
    unittest.main()
