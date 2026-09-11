import ast
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.instagram_ai_disclosure import (
    InstagramAIDisclosureError, create_ai_container, raise_for_disclosure_rejection,
)

ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / 'daily_short.py').read_text())


def load_function(name, scope):
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / 'daily_short.py'), 'exec'), scope)
    return scope[name]


def response(code=200, **body):
    return SimpleNamespace(status_code=code, json=lambda: body, text=json.dumps(body))


class NativeDisclosureGuardTests(unittest.TestCase):
    def test_missing_false_and_wrong_type_never_make_request(self):
        for value in (None, False, 'false', 1, 0, '', 'TRUE'):
            client = Mock()
            with self.subTest(flag=value), self.assertRaises(InstagramAIDisclosureError):
                create_ai_container(client, 'https://example.invalid/media', {'is_ai_generated': value})
            client.post.assert_not_called()

    def test_explicit_rejection_and_false_echo_stop(self):
        for resp in (response(400, error={'message': 'Unsupported is_ai_generated parameter', 'code': 100}), response(id='123', is_ai_generated=False)):
            client = Mock(post=Mock(return_value=resp))
            with self.assertRaises(InstagramAIDisclosureError):
                create_ai_container(client, 'url', {'is_ai_generated': 'true'})
            self.assertEqual(client.post.call_count, 1)

    def test_success_does_not_claim_native_readback(self):
        audit = {}
        client = Mock(post=Mock(return_value=response(id='123')))
        payload = {'is_ai_generated': 'true', 'video_url': 'same-video'}
        create_ai_container(client, 'url', payload, audit=audit)
        payload.pop('is_ai_generated')
        self.assertEqual(client.post.call_args.kwargs['data']['is_ai_generated'], 'true')
        self.assertEqual(audit['ai_disclosure'], {'requested': True, 'container_id': '123', 'verification': 'create_accepted_native_label_unverified'})


class ActualDailyPublisherTests(unittest.TestCase):
    def run_reel(self, creates, publishes=None, trial=False, collaborators=False):
        creates, publishes = list(creates), list(publishes or [response(id='published-1')])
        calls = []
        def post(url, data, **kwargs):
            calls.append((url, dict(data)))
            return publishes.pop(0) if url.endswith('/media_publish') else creates.pop(0)
        def get(url, **kwargs):
            if url.endswith('/business'):
                return response(id='business', name='Test account')
            return response(status_code='FINISHED', media_type='REELS', media_product_type='REELS')
        fixed_time = datetime(2026, 9, 11, 12, 0)
        metadata = {'ai_disclosure': {'container_id': 'old-container'}}
        env = {'INSTAGRAM_ACCESS_TOKEN': 'test-token-not-a-secret-0123456789', 'INSTAGRAM_BUSINESS_ID': 'business', 'IG_TRIAL_WEEKDAYS': '4' if trial else '6'}
        if collaborators:
            env['IG_COLLABORATORS'] = 'test-collaborator'
        scope = {
            'requests': SimpleNamespace(post=post, get=get),
            'os': SimpleNamespace(environ=env, path=SimpleNamespace(getsize=lambda _: 100, exists=lambda _: False)),
            'time': SimpleNamespace(time=lambda: 1700000000, sleep=Mock()),
            'datetime': SimpleNamespace(now=lambda _: fixed_time),
            'pytz': SimpleNamespace(timezone=lambda _: None), 'json': json, 'print': Mock(),
            'CROSS_POST_INSTAGRAM': True, 'IG_API_VERSION': 'v21.0', 'TIMEZONE': 'Asia/Kolkata',
            'IG_POST_META': metadata, 'BLOG_S3_BUCKET': 'test-bucket', 'BLOG_BASE_URL': 'https://example.invalid',
            'get_instagram_best_time': lambda *args: None,
            'get_ig_hashtags': lambda _: [], 'get_ig_seo_line': lambda *args: 'Useful buyer lesson',
            'get_ig_cta_line': lambda: 'What do you check?', 'NEW_TEST_MODE': False, 'SINGLE_VEO_TEST': False,
            '_ig_post_publish_extras': Mock(), 'create_ai_container': create_ai_container,
            'raise_for_disclosure_rejection': raise_for_disclosure_rejection,
        }
        publish = load_function('cross_post_to_instagram', scope)
        with patch.dict(sys.modules, {'boto3': SimpleNamespace(client=lambda _: Mock())}):
            result = publish('same-approved-video.mp4', 'Title', 'Description', 'Topic')
        return result, calls, scope

    def test_rejected_flag_stops_before_optional_retry_or_publish(self):
        result, calls, scope = self.run_reel([response(400, error={'message': 'Invalid is_ai_generated', 'code': 100})], trial=True, collaborators=True)
        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]['is_ai_generated'], 'true')
        self.assertNotIn('ai_disclosure', scope['IG_POST_META'])
        scope['_ig_post_publish_extras'].assert_not_called()

    def test_generic_rejection_can_never_drop_flag(self):
        rejected = response(400, error={'message': 'Invalid parameter', 'code': 100})
        result, calls, _ = self.run_reel([rejected, rejected, rejected], trial=True, collaborators=True)
        self.assertIsNone(result)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(url.endswith('/media') and data['is_ai_generated'] == 'true' for url, data in calls))
        self.assertEqual(len({data['video_url'] for _, data in calls}), 1)

    def test_optional_trial_failure_retains_disclosure_and_publishes_once(self):
        result, calls, scope = self.run_reel([response(400, error={'message': 'trial_params unsupported', 'code': 100}), response(id='container-2')], trial=True)
        self.assertEqual(result, 'published-1')
        creates = [data for url, data in calls if url.endswith('/media')]
        self.assertEqual(len(creates), 2)
        self.assertTrue(all(data['is_ai_generated'] == 'true' for data in creates))
        self.assertEqual(creates[0]['video_url'], creates[1]['video_url'])
        self.assertEqual([data['creation_id'] for url, data in calls if url.endswith('/media_publish')], ['container-2'])
        self.assertEqual(scope['IG_POST_META']['ai_disclosure']['container_id'], 'container-2')

    def test_success_without_native_get_proof_is_explicitly_unverified(self):
        result, calls, scope = self.run_reel([response(id='container-1')])
        self.assertEqual(result, 'published-1')
        self.assertEqual(len(calls), 2)
        self.assertEqual(scope['IG_POST_META']['ai_disclosure']['verification'], 'create_accepted_native_label_unverified')
        scope['_ig_post_publish_extras'].assert_called_once()

    def test_no_container_id_or_false_flag_echo_cannot_publish(self):
        for resp in (response(), response(id='container-1', is_ai_generated=False)):
            with self.subTest(body=resp.json()):
                result, calls, _ = self.run_reel([resp])
                self.assertIsNone(result)
                self.assertEqual(len(calls), 1)

    def test_publish_disclosure_rejection_never_creates_replacement(self):
        result, calls, _ = self.run_reel([response(id='container-1')], publishes=[response(400, error={'message': 'is_ai_generated not accepted'})], trial=True)
        self.assertIsNone(result)
        self.assertEqual(len(calls), 2)

    def test_no_trial_replacement_also_keeps_required_flag(self):
        result, calls, _ = self.run_reel(
            [response(id='container-1'), response(400, error={'message': 'is_ai_generated unsupported'})],
            publishes=[response(400, error={'message': 'Trial not eligible'})], trial=True,
        )
        self.assertIsNone(result)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(data['is_ai_generated'] == 'true' for url, data in calls if url.endswith('/media')))
        self.assertEqual(sum(url.endswith('/media_publish') for url, _ in calls), 1)

    def test_successful_publish_id_is_preserved_without_verified_label(self):
        result, calls, scope = self.run_reel(
            [response(id='container-1')], publishes=[response(id='published-1', is_ai_generated=False)], trial=True,
        )
        self.assertEqual(result, 'published-1')
        self.assertEqual(len(calls), 2)
        self.assertEqual(scope['IG_POST_META']['ai_disclosure']['verification'], 'create_accepted_native_label_unverified')

    def test_single_photo_flag_rejection_has_no_unlabeled_retry(self):
        client = Mock(post=Mock(return_value=response(400, error={'message': 'Unsupported is_ai_generated', 'code': 100})))
        scope = {'os': SimpleNamespace(environ={'INSTAGRAM_ACCESS_TOKEN': 'test', 'INSTAGRAM_BUSINESS_ID': 'business'}), 'requests': client, 'IG_API_VERSION': 'v21.0', 'print': Mock(), 'create_ai_container': create_ai_container}
        fn = load_function('publish_ig_carousel', scope)
        self.assertIsNone(fn(['https://example.invalid/one.png'], 'Useful fabric lesson'))
        self.assertEqual(client.post.call_count, 1)
        self.assertEqual(client.post.call_args.kwargs['data']['is_ai_generated'], 'true')

    def test_single_photo_success_keeps_flag_and_publishes_once(self):
        client = Mock(post=Mock(side_effect=[response(id='photo-container'), response(id='photo-media')]), get=Mock(return_value=response(status_code='FINISHED')))
        scope = {'os': SimpleNamespace(environ={'INSTAGRAM_ACCESS_TOKEN': 'test', 'INSTAGRAM_BUSINESS_ID': 'business'}), 'requests': client, 'IG_API_VERSION': 'v21.0', 'print': Mock(), 'create_ai_container': create_ai_container}
        self.assertEqual(load_function('publish_ig_carousel', scope)(['https://example.invalid/one.png'], 'Useful fabric lesson'), 'photo-media')
        self.assertEqual(client.post.call_count, 2)
        self.assertEqual(client.post.call_args_list[0].kwargs['data']['is_ai_generated'], 'true')
        self.assertEqual(client.post.call_args_list[1].kwargs['data']['creation_id'], 'photo-container')

    def test_engagement_record_preserves_unverified_disclosure_status(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'records.json'
            receipt = {'requested': True, 'container_id': 'container-1', 'verification': 'create_accepted_native_label_unverified'}
            scope = {'os': os, 'json': json, 'pytz': SimpleNamespace(timezone=lambda _: None), 'datetime': datetime, 'TIMEZONE': 'Asia/Kolkata', 'IG_ENGAGEMENT_FILE': str(path), 'IG_POST_META': {'ai_disclosure': receipt}, 'print': Mock()}
            load_function('save_ig_upload_record', scope)('published-1', 'Title', 'Topic')
            self.assertEqual(json.loads(path.read_text())[0]['ai_disclosure'], receipt)


if __name__ == '__main__':
    unittest.main()
