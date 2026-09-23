import json
from unittest import TestCase
from unittest.mock import Mock, patch

from tools import audit_short_output as audit


MEDIA = '18618843040045479'
MANIFEST = {'source_posts': {'instagram': MEDIA}}
ENV = {'INSTAGRAM_ACCESS_TOKEN': 'private-token', 'INSTAGRAM_BUSINESS_ID': audit.IG_ACCOUNT}


def reply(body, status=200):
    response = Mock(status_code=status)
    response.json.return_value = body
    return response


def replies(*, owner_id=audit.IG_ACCOUNT, disclosure=True, owner_unsupported=False):
    return [reply({'id': audit.IG_ACCOUNT, 'username': 'bulkplaintshirt_com'}),
            reply({'id': MEDIA, 'media_type': 'VIDEO', 'media_product_type': 'REELS',
                   'username': 'bulkplaintshirt_com',
                   'permalink': 'https://www.instagram.com/reel/ABC12345678/?access_token=private-token'}),
            reply({'error': {'code': 100, 'message': 'private-token'}}, 400) if owner_unsupported
            else reply({'id': MEDIA, 'owner': {'id': owner_id}}),
            reply({'error': {'code': 100, 'message': 'private-token'}}, 400) if disclosure is None
            else reply({'id': MEDIA, 'is_ai_generated': disclosure})]


class InstagramReadbackTest(TestCase):
    def test_exact_media_owner_and_native_flag_are_read_without_writes_or_secret_output(self):
        with patch.dict('os.environ', ENV), patch.object(audit.requests, 'get', side_effect=replies()) as get, \
                patch.object(audit.requests, 'post') as post:
            result = audit.instagram_readback(MANIFEST)
        self.assertEqual(result['media_id'], MEDIA)
        self.assertTrue(result['owner_verified'])
        self.assertEqual(result['owner_verification'], 'exact_owner_id')
        self.assertTrue(result['published_media_readable'])
        self.assertEqual(result['native_ai_disclosure'], 'api_true')
        self.assertEqual(result['native_label_visible'], 'not_independently_checked')
        self.assertEqual(result['public_visitor_access'], 'not_independently_checked')
        self.assertNotIn('private-token', json.dumps(result))
        self.assertEqual(len(get.call_args_list), 4)
        for index, call in enumerate(get.call_args_list):
            self.assertEqual(call.args[0], audit.FB_GRAPH + '/' + (audit.IG_ACCOUNT if index == 0 else MEDIA))
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertNotIn('access_token', call.kwargs['params'])
        post.assert_not_called()

    def test_native_field_unavailable_does_not_hide_publication_evidence(self):
        with patch.dict('os.environ', ENV), patch.object(audit.requests, 'get', side_effect=replies(disclosure=None)):
            result = audit.instagram_readback(MANIFEST)
        self.assertTrue(result['published_media_readable'])
        self.assertEqual(result['native_ai_disclosure'], 'unverified')
        self.assertEqual(result['reads']['is_ai_generated'], {'state': 'read_unavailable', 'http_status': 400, 'code': 100})
        self.assertNotIn('private-token', json.dumps(result))

    def test_false_native_flag_is_not_treated_as_unavailable(self):
        with patch.dict('os.environ', ENV), patch.object(audit.requests, 'get', side_effect=replies(disclosure=False)):
            result = audit.instagram_readback(MANIFEST)
        self.assertEqual(result['native_ai_disclosure'], 'api_false')

    def test_optional_owner_uses_only_exact_username_of_the_verified_account(self):
        with patch.dict('os.environ', ENV), patch.object(audit.requests, 'get', side_effect=replies(owner_unsupported=True)):
            result = audit.instagram_readback(MANIFEST)
        self.assertTrue(result['published_media_readable'])
        self.assertEqual(result['owner_verification'], 'exact_username_of_verified_account')
        responses = replies(owner_unsupported=True)
        responses[1].json.return_value.pop('username')
        with patch.dict('os.environ', ENV), patch.object(audit.requests, 'get', side_effect=responses):
            result = audit.instagram_readback(MANIFEST)
        self.assertFalse(result['published_media_readable'])
        self.assertEqual(result['owner_verification'], 'unverified')

    def test_wrong_owner_or_mismatched_username_is_rejected(self):
        for wrong_owner in (True, False):
            responses = replies(owner_id='999' if wrong_owner else audit.IG_ACCOUNT)
            if not wrong_owner:
                responses[1].json.return_value['username'] = 'other_account'
            with patch.dict('os.environ', ENV), patch.object(audit.requests, 'get', side_effect=responses) as get:
                result = audit.instagram_readback(MANIFEST)
            self.assertEqual(result['state'], 'owner_mismatch')
            self.assertFalse(result['published_media_readable'])
            self.assertEqual(result['native_ai_disclosure'], 'unverified')
            self.assertEqual(get.call_count, 3)

    def test_missing_invalid_id_and_configured_account_mismatch_make_no_requests(self):
        for source, env, expected in (({}, ENV, 'manifest_id_absent'),
                ({'source_posts': {'instagram': '../me'}}, ENV, 'invalid_manifest_id'),
                (MANIFEST, {**ENV, 'INSTAGRAM_BUSINESS_ID': '999'}, 'configured_account_mismatch')):
            with patch.dict('os.environ', env), patch.object(audit.requests, 'get') as get:
                result = audit.instagram_readback(source)
            self.assertEqual(result['state'], expected)
            get.assert_not_called()

    def test_wrong_response_id_or_network_error_is_safe_and_unverified(self):
        for responses in ([reply({'id': '999', 'username': 'private-token'})],
                          [audit.requests.RequestException('private-token')]):
            with patch.dict('os.environ', ENV), patch.object(audit.requests, 'get', side_effect=responses):
                result = audit.instagram_readback(MANIFEST)
            self.assertEqual(result['state'], 'account_readback_unavailable')
            self.assertNotIn('private-token', json.dumps(result))
