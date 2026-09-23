import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from tools import audit_short_output as audit


VIDEO_ID = '1234567890123456'


def manifest():
    return {'run_flags': {'facebook_ai_disclosure': {
        'video_id': VIDEO_ID, 'requested': True, 'state': 'finish_accepted_native_label_unverified'}}}


def response(data, status=200):
    value = Mock(status_code=status)
    value.json.return_value = data
    return value


class FacebookPublicationTest(unittest.TestCase):
    def test_ready_is_processing_only_not_publication(self):
        ready = {'status': {'video_status': 'ready'}}
        result = audit.facebook_publication(ready)
        self.assertTrue(result['processing_ready'])
        self.assertFalse(result['publication_confirmed_by_api'])
        self.assertIsNone(result['public_by_api'])
        self.assertEqual(result['publication_state'], 'publication_unverified')
        ready['status']['publishing_phase'] = {'status': 'not_started'}
        ready['published'] = False
        result = audit.facebook_publication(ready)
        self.assertEqual(result['publication_state'], 'not_published')
        self.assertFalse(result['public_by_api'])

    def test_actual_publishing_phase_and_privacy_are_separate(self):
        data = {'status': {'video_status': 'ready', 'publishing_phase': {
            'status': 'complete', 'publish_status': 'published'}}}
        result = audit.facebook_publication(data)
        self.assertTrue(result['publication_confirmed_by_api'])
        self.assertIsNone(result['public_by_api'])
        data.update(published=True, privacy={'value': 'EVERYONE'})
        self.assertTrue(audit.facebook_publication(data)['public_by_api'])
        data['privacy']['value'] = 'SELF'
        self.assertFalse(audit.facebook_publication(data)['public_by_api'])

    def test_contradictory_flags_and_phase_are_not_a_publication_pass(self):
        for published, phase in (
            (False, {'status': 'complete', 'publish_status': 'published'}),
            (True, {'status': 'not_started'}),
            (True, {'status': 'complete', 'publish_status': 'scheduled'}),
        ):
            result = audit.facebook_publication({'published': published, 'privacy': {'value': 'EVERYONE'},
                'status': {'video_status': 'ready', 'publishing_phase': phase}})
            self.assertEqual(result['publication_state'], 'conflicting_publication_signals')
            self.assertFalse(result['publication_confirmed_by_api'])
            self.assertIsNone(result['public_by_api'])

    def test_only_boolean_published_is_evidence(self):
        for value in ('true', 1, None):
            result = audit.facebook_publication({'published': value, 'privacy': 'EVERYONE'})
            self.assertIsNone(result['published'])
            self.assertFalse(result['publication_confirmed_by_api'])


class FacebookReadbackTest(unittest.TestCase):
    def test_exact_manifest_id_gets_only_read_requests_and_unsupported_ai_is_unverified(self):
        replies = [
            response({'id': VIDEO_ID, 'status': {'video_status': 'ready', 'publishing_phase': {'status': 'not_started'}},
                      'permalink_url': '/reel/' + VIDEO_ID + '/?access_token=do-not-copy'}),
            response({'id': VIDEO_ID, 'published': False}),
            response({'id': VIDEO_ID, 'privacy': {'value': 'EVERYONE', 'description': 'do-not-copy'}}),
            response({'error': {'message': 'do-not-copy', 'code': 100, 'error_subcode': 33, 'fbtrace_id': 'do-not-copy'}}, 400),
        ]
        with patch.dict('os.environ', {'FB_PAGE_ACCESS_TOKEN': 'do-not-copy'}), \
                patch.object(audit.requests, 'get', side_effect=replies) as get, \
                patch.object(audit.requests, 'post') as post:
            result = audit.facebook_readback(manifest())
        self.assertEqual(get.call_count, 4)
        post.assert_not_called()
        for call in get.call_args_list:
            self.assertEqual(call.args[0], audit.FB_GRAPH + '/' + VIDEO_ID)
            self.assertEqual(call.kwargs['headers'], {'Authorization': 'Bearer do-not-copy'})
            self.assertNotIn('access_token', call.kwargs['params'])
            self.assertFalse(call.kwargs['allow_redirects'])
        self.assertEqual(result['publication_state'], 'not_published')
        self.assertEqual(result['native_ai_disclosure'], 'unverified')
        self.assertEqual(result['reads']['is_ai_generated'], {'state': 'read_unavailable', 'http_status': 400,
                                                            'code': 100, 'error_subcode': 33})
        self.assertNotIn('do-not-copy', json.dumps(result))
        self.assertEqual(result['permalink_url'], 'https://www.facebook.com/reel/' + VIDEO_ID + '/')

    def test_ai_boolean_api_read_does_not_claim_visible_label_verification(self):
        for flag, expected in ((True, 'api_true'), (False, 'api_false')):
            replies = [response({'id': VIDEO_ID, 'status': {'video_status': 'ready'}}),
                       response({'id': VIDEO_ID, 'published': True}),
                       response({'id': VIDEO_ID, 'privacy': {'value': 'EVERYONE'}}),
                       response({'id': VIDEO_ID, 'is_ai_generated': flag})]
            with patch.dict('os.environ', {'FB_PAGE_ACCESS_TOKEN': 'secret'}), \
                    patch.object(audit.requests, 'get', side_effect=replies):
                result = audit.facebook_readback(manifest())
            self.assertEqual(result['native_ai_disclosure'], expected)
            self.assertEqual(result['native_label_visible'], 'not_independently_checked')
            self.assertTrue(result['public_by_api'])

    def test_missing_or_invalid_manifest_id_never_looks_up_other_uploads(self):
        for source in ({}, {'run_flags': {'facebook_ai_disclosure': {'video_id': '../me'}}}):
            with patch.object(audit.requests, 'get') as get:
                result = audit.facebook_readback(source)
            get.assert_not_called()
            self.assertIn(result['state'], ('manifest_id_absent', 'invalid_manifest_id'))

    def test_identity_mismatch_and_network_exceptions_are_safe(self):
        with patch.dict('os.environ', {'FB_PAGE_ACCESS_TOKEN': 'secret'}):
            with patch.object(audit.requests, 'get', return_value=response({'id': '999', 'published': True})):
                result = audit.facebook_readback(manifest())
            self.assertEqual(result['reads']['status_and_permalink']['state'], 'identity_mismatch')
            with patch.object(audit.requests, 'get', side_effect=audit.requests.RequestException('secret-error-body')):
                result = audit.facebook_readback(manifest())
            self.assertEqual(result['reads']['status_and_permalink']['state'], 'network_error')
            self.assertNotIn('secret', json.dumps(result))

    def test_unavailable_facebook_does_not_skip_full_native_audio_review(self):
        source = manifest()
        source.update({'assets': {kind: {'file': kind, 'bytes': 1, 'sha256': 'a' * 64}
                                  for kind in ('video', 'cover')},
                       'source_posts': {'bot_youtube': 'abcdefghijk'}, 'titles': {'youtube': 'Title'}})
        youtube = {'uploadStatus': 'processed', 'containsSyntheticMedia': True, 'title_matches_manifest': True}
        with TemporaryDirectory() as directory, patch.object(audit, 'REPORT', Path(directory)), \
                patch.object(audit, 'github_json', return_value={}), \
                patch.object(audit, 'validate_run', return_value={}), \
                patch.object(audit, 'download_artifact', return_value=(b'zip', 12)), \
                patch.object(audit, 'validate_archive', return_value=(source, Path('video.mp4'))), \
                patch.object(audit, 'facebook_readback', return_value={'state': 'readback_unavailable'}), \
                patch.object(audit, 'youtube_readback', return_value=youtube), \
                patch.object(audit, 'probe_and_extract', return_value=(Path('audio.wav'), {'duration_seconds': 40})), \
                patch.object(audit, 'assess_audio', return_value={'passed': True}) as audio, \
                patch('builtins.print'):
            self.assertEqual(audit.main(['--run-id', '35884612564']), 0)
            audio.assert_called_once()
            saved = json.loads((Path(directory) / 'report.json').read_text())
            self.assertIn('Facebook readback is reported separately', saved['pass_scope'])
            self.assertEqual(saved['facebook']['state'], 'readback_unavailable')


if __name__ == '__main__':
    unittest.main()
