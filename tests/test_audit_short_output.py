import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave
import zipfile

from tools import audit_short_output as audit


def run_data():
    return {'id': 35884612564, 'status': 'completed', 'conclusion': 'success', 'head_branch': 'main',
            'path': '.github/workflows/daily_short.yml', 'event': 'workflow_dispatch',
            'repository': {'full_name': audit.REPOSITORY}, 'head_repository': {'full_name': audit.REPOSITORY},
            'head_sha': 'a' * 40, 'run_attempt': 1}


def manifest_data():
    asset = lambda name, body: {'file': name, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
    return {'format': 'daily-short-review-v1', 'workflow': {
        'github_repository': audit.REPOSITORY, 'github_run_id': '35884612564',
        'github_run_attempt': '1', 'github_sha': 'a' * 40}, 'test_mode': False, 'ai_generated': True,
        'script': {'voice': 'जर्सी कपड़ा कैसा है?', 'tts_input': 'जर्सी कपड़ा कैसा है?', 'english': 'A jersey lesson.'},
        'source_posts': {'bot_youtube': 'abcdefghijk'}, 'topic': 'Jersey construction',
        'assets': {'video': asset('SHORT_test.mp4', b'video'), 'cover': asset('review_cover.png', b'cover')}}


def archive_data(manifest=None, extra=None, video=b'video'):
    output = BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('review_manifest.json', json.dumps(manifest or manifest_data()))
        archive.writestr('SHORT_test.mp4', video)
        archive.writestr('review_cover.png', b'cover')
        if extra:
            archive.writestr(*extra)
    return output.getvalue()


def assessment(words=('जर्सी', 'कपड़ा')):
    return {'verdict': 'pass', **dict.fromkeys(audit.BOOL_CHECKS, True), 'uncertain': False,
            'heard_text': 'जर्सी कपड़ा कैसा है?', 'notes': 'The answer and final sentence are complete.',
            'issues': [], 'word_checks': [{'word': word, 'heard': word, 'clear': True} for word in words]}


class ProvenanceTest(unittest.TestCase):
    def test_only_numeric_run_ids_are_accepted(self):
        self.assertEqual(audit.run_id('35884612564'), '35884612564')
        for invalid in ('0', '-1', '12; echo token', '../../run', 'https://example.com', '1\n', '', '1' * 21, 12):
            with self.subTest(invalid=invalid), self.assertRaises(audit.AuditError):
                audit.run_id(invalid)

    def test_completed_same_repo_main_daily_workflow_is_required(self):
        self.assertEqual(audit.validate_run(run_data(), '35884612564')['sha'], 'a' * 40)
        for key, value in [('id', 2), ('status', 'in_progress'), ('head_branch', 'feature'),
                           ('path', '.github/workflows/el_plan.yml'), ('event', 'pull_request'),
                           ('head_sha', 'bad'), ('run_attempt', True),
                           ('repository', {'full_name': 'other/repo'}),
                           ('head_repository', {'full_name': 'fork/repo'})]:
            wrong = run_data(); wrong[key] = value
            with self.subTest(key=key), self.assertRaises(audit.AuditError):
                audit.validate_run(wrong, '35884612564')

    def test_manifest_run_attempt_sha_and_normal_mode_must_match(self):
        self.assertEqual(audit.validate_manifest(manifest_data(), run_data()), 'abcdefghijk')
        for field, value in [('github_run_id', '2'), ('github_run_attempt', '2'),
                             ('github_sha', 'b' * 40), ('github_repository', 'other/repo')]:
            wrong = manifest_data(); wrong['workflow'][field] = value
            with self.subTest(field=field), self.assertRaises(audit.AuditError):
                audit.validate_manifest(wrong, run_data())
        for field, value in [('test_mode', True), ('ai_generated', False)]:
            wrong = manifest_data(); wrong[field] = value
            with self.assertRaises(audit.AuditError):
                audit.validate_manifest(wrong, run_data())

    def test_exact_assets_are_verified_before_extraction(self):
        with TemporaryDirectory() as directory:
            manifest, video = audit.validate_archive(archive_data(), run_data(), Path(directory))
            self.assertEqual(video.read_bytes(), b'video')
            self.assertEqual(manifest['assets']['cover']['sha256'], hashlib.sha256(b'cover').hexdigest())
            for data in (archive_data(video=b'tampered'), archive_data(extra=('../escape', 'x'))):
                with self.assertRaises(audit.AuditError):
                    audit.validate_archive(data, run_data(), Path(directory))
            wrong = manifest_data(); wrong['assets']['video']['file'] = '../outside.mp4'
            with self.assertRaises(audit.AuditError):
                audit.validate_archive(archive_data(wrong), run_data(), Path(directory))


class NativeAudioTest(unittest.TestCase):
    def test_pass_requires_full_speech_answer_finished_ending_and_all_business_words(self):
        words = ['जर्सी', 'कपड़ा']
        self.assertTrue(audit.assessment_passes(assessment(), words, 40))
        for key in audit.BOOL_CHECKS:
            value = assessment(); value[key] = False
            self.assertFalse(audit.assessment_passes(value, words, 40))
        for updates in ({'verdict': 'uncertain', 'uncertain': True}, {'verdict': 'fail'}):
            self.assertFalse(audit.assessment_passes({**assessment(), **updates}, words, 40))
        value = assessment(); value['word_checks'].pop()
        with self.assertRaises(audit.AuditError):
            audit.assessment_passes(value, words, 40)
        value = assessment(); value['word_checks'][0]['clear'] = False
        self.assertFalse(audit.assessment_passes(value, words, 40))

    def test_bad_boolean_and_out_of_range_issue_cannot_pass(self):
        value = assessment(); value['complete'] = 'true'
        with self.assertRaises(audit.AuditError):
            audit.assessment_passes(value, ['जर्सी', 'कपड़ा'], 40)
        value = assessment(); value['issues'] = [{'start_seconds': 41, 'end_seconds': 45,
                                                'heard': 'x', 'expected': 'y', 'reason': 'cut'}]
        with self.assertRaises(audit.AuditError):
            audit.assessment_passes(value, ['जर्सी', 'कपड़ा'], 40)

    def test_actual_audio_bytes_not_transcript_only_are_sent_once_to_requested_model(self):
        manifest = manifest_data()
        words = audit.review_words(manifest['script']['tts_input'])
        value = assessment(words)
        provider = {'status': 'completed', 'steps': [{'type': 'model_output',
                    'content': [{'type': 'text', 'text': json.dumps(value)}]}], 'private_internal': 'not-for-report'}
        response = Mock(status_code=200); response.json.return_value = provider
        with TemporaryDirectory() as directory:
            root = Path(directory); wav = root / 'audio.wav'; wav.write_bytes(b'RIFF-audio-fixture')
            with patch.object(audit, 'REPORT', root), patch.dict('os.environ', {'GOOGLE_API_KEY': 'secret'}), \
                    patch.object(audit.requests, 'post', return_value=response) as post:
                result = audit.assess_audio(wav, manifest, 40, report_dir=root)
            self.assertTrue(result['passed'])
            self.assertEqual(post.call_count, 1)
            request = post.call_args.kwargs['json']
            self.assertEqual(request['model'], 'gemini-3.8-flash')
            self.assertFalse(request['store'])
            self.assertEqual(request['input'][1]['type'], 'audio')
            self.assertEqual(request['input'][1]['mime_type'], 'audio/wav')
            self.assertIn('जर्सी', request['input'][0]['text'])
            saved = (root / 'audio-assessment.json').read_text()
            self.assertNotIn('secret', saved)
            self.assertNotIn('not-for-report', saved)
            self.assertIn('not_human_listening', saved)

    def test_incomplete_provider_response_is_not_a_pass_and_is_not_retried(self):
        response = Mock(status_code=200); response.json.return_value = {'status': 'in_progress'}
        with TemporaryDirectory() as directory:
            wav = Path(directory) / 'audio.wav'; wav.write_bytes(b'RIFF-audio-fixture')
            with patch.dict('os.environ', {'GOOGLE_API_KEY': 'secret'}), \
                    patch.object(audit.requests, 'post', return_value=response) as post:
                with self.assertRaises(audit.AuditError):
                    audit.assess_audio(wav, manifest_data(), 40)
                self.assertEqual(post.call_count, 1)


class ExtractionAndReadbackTest(unittest.TestCase):
    def test_full_audio_extraction_has_no_trim_filter_or_denoise(self):
        def process(args, **kwargs):
            if args[0] == 'ffprobe':
                return SimpleNamespace(stdout=json.dumps({'format': {'duration': '1.0'}, 'streams': [
                    {'codec_type': 'video', 'width': 1080, 'height': 1920}, {'codec_type': 'audio', 'duration': '1.0'}]}))
            with wave.open(args[-1], 'wb') as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
                output.writeframes(b'\0\0' * 16000)
            return SimpleNamespace(returncode=0)
        with TemporaryDirectory() as directory, patch.object(audit.subprocess, 'run', side_effect=process) as run:
            wav, info = audit.probe_and_extract(Path(directory) / 'video.mp4', Path(directory))
            self.assertTrue(wav.exists())
            self.assertEqual(info['audio_seconds'], 1.0)
            ffmpeg_args = run.call_args_list[1].args[0]
            for forbidden in ('-ss', '-t', '-to', '-af', '-filter:a', '-filter_complex'):
                self.assertNotIn(forbidden, ffmpeg_args)

    def test_actual_silent_outro_is_allowed_but_truncated_audio_is_rejected(self):
        for extracted_seconds in (36.50, 15.0):
            def process(args, **kwargs):
                if args[0] == 'ffprobe':
                    return SimpleNamespace(stdout=json.dumps({'format': {'duration': '39.2'}, 'streams': [
                        {'codec_type': 'video', 'width': 1080, 'height': 1920},
                        {'codec_type': 'audio', 'duration': '36.498005', 'start_time': '0.0'}]}))
                with wave.open(args[-1], 'wb') as output:
                    output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
                    output.writeframes(b'\0\0' * round(extracted_seconds * 16000))
                return SimpleNamespace(returncode=0)
            with self.subTest(seconds=extracted_seconds), TemporaryDirectory() as directory, \
                    patch.object(audit.subprocess, 'run', side_effect=process):
                if extracted_seconds == 15.0:
                    with self.assertRaisesRegex(audit.AuditError, 'complete source audio'):
                        audit.probe_and_extract(Path(directory) / 'video.mp4', Path(directory))
                else:
                    _, info = audit.probe_and_extract(Path(directory) / 'video.mp4', Path(directory))
                    self.assertEqual(info['duration_seconds'], 39.2)
                    self.assertEqual(info['audio_seconds'], 36.5)
                    self.assertAlmostEqual(info['silent_video_tail_seconds'], 2.701995)

    def test_unknown_or_invalid_source_audio_duration_cannot_pass(self):
        for duration in (None, 'NaN', 'inf', '-1', '50'):
            response = {'format': {'duration': '39.2'}, 'streams': [
                {'codec_type': 'video', 'width': 1080, 'height': 1920},
                {'codec_type': 'audio', 'duration': duration}]}
            with self.subTest(duration=duration), TemporaryDirectory() as directory, \
                    patch.object(audit.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(response))) as run:
                with self.assertRaises(audit.AuditError):
                    audit.probe_and_extract(Path(directory) / 'video.mp4', Path(directory))
                self.assertEqual(run.call_count, 1)

    def service_modules(self, channel, video_channel=None):
        item = {'id': 'abcdefghijk', 'snippet': {'channelId': video_channel or channel, 'title': 'Expected'},
                'status': {'privacyStatus': 'private', 'publishAt': '2026-09-24T13:30:00Z',
                           'containsSyntheticMedia': True, 'uploadStatus': 'processed'}}
        service = Mock()
        service.channels.return_value.list.return_value.execute.return_value = {'items': [{'id': channel}]}
        service.videos.return_value.list.return_value.execute.return_value = {'items': [item]}
        credentials = SimpleNamespace(valid=True)
        modules = {'google.auth.transport.requests': SimpleNamespace(Request=Mock()),
                   'google.oauth2.credentials': SimpleNamespace(Credentials=SimpleNamespace(
                       from_authorized_user_info=Mock(return_value=credentials))),
                   'googleapiclient.discovery': SimpleNamespace(build=Mock(return_value=service))}
        return modules, service

    def test_youtube_readback_requires_bot_oauth_and_exact_video_channel(self):
        for oauth, owner, valid in ((audit.BOT_CHANNEL, audit.BOT_CHANNEL, True),
                                    ('wrong-channel', audit.BOT_CHANNEL, False),
                                    (audit.BOT_CHANNEL, 'wrong-channel', False)):
            modules, service = self.service_modules(oauth, owner)
            with self.subTest(oauth=oauth, owner=owner), patch.dict('sys.modules', modules), \
                    patch.dict('os.environ', {'YOUTUBE_TOKEN_JSON': '{}'}):
                if valid:
                    result = audit.youtube_readback('abcdefghijk', 'Expected')
                    self.assertTrue(result['title_matches_manifest'])
                    self.assertTrue(result['containsSyntheticMedia'])
                    self.assertEqual(result['privacyStatus'], 'private')
                else:
                    with self.assertRaises(audit.AuditError):
                        audit.youtube_readback('abcdefghijk', 'Expected')
            self.assertFalse(any('insert' in str(call) or 'update' in str(call) for call in service.mock_calls))


class ManifestDisclosureTest(unittest.TestCase):
    def evidence(self):
        manifest = manifest_data()
        video_id = manifest['source_posts']['bot_youtube']
        expected = {'privacyStatus': 'private', 'publishAt': '2026-09-24T13:30:00Z',
                    'embeddable': False, 'publicStatsViewable': False, 'containsSyntheticMedia': True}
        manifest['run_flags'] = {'youtube_status_evidence': {
            'verified': True, 'video_id': video_id, 'request': {'id': video_id, 'status': expected},
            'acknowledgment': {'id': video_id, 'status': {'containsSyntheticMedia': True}}}}
        readback = {'video_id': video_id, 'channel_id': audit.BOT_CHANNEL,
                    'mutable_status': {key: value for key, value in expected.items() if key != 'containsSyntheticMedia'}}
        return manifest, readback

    def test_exact_verified_manifest_ack_with_matching_owner_get_accepts_omission(self):
        manifest, readback = self.evidence()
        result = audit.youtube_disclosure_verification(manifest, readback)
        self.assertTrue(result['verified'])
        self.assertEqual(result['state'], 'accepted_native_true_readback_omitted')

    def test_old_manifest_or_generic_requested_flag_does_not_prove_disclosure(self):
        _, readback = self.evidence()
        for flags in ({}, {'youtube_status_evidence': {'requested': True}},
                      {'youtube_status_evidence': {'verified': True}}):
            manifest = manifest_data(); manifest['run_flags'] = flags
            self.assertFalse(audit.youtube_disclosure_verification(manifest, readback)['verified'])

    def test_explicit_false_malformed_or_changed_owner_status_blocks_ack(self):
        for changes in ({'containsSyntheticMedia': False}, {'containsSyntheticMedia': None},
                        {'containsSyntheticMedia': 'true'}, {'embeddable': True},
                        {'publishAt': '2026-09-25T13:30:00Z'}, {'privacyStatus': 'unlisted'}):
            manifest, readback = self.evidence()
            readback['mutable_status'].update(changes)
            with self.subTest(changes=changes):
                self.assertFalse(audit.youtube_disclosure_verification(manifest, readback)['verified'])

    def test_wrong_or_incomplete_ack_fails(self):
        for location, field, value in (('request', 'id', 'wrong-video'), ('acknowledgment', 'id', 'wrong-video'),
                                      ('acknowledgment', 'status', {}),
                                      ('acknowledgment', 'status', {'containsSyntheticMedia': False}),
                                      (None, 'video_id', 'wrong-video')):
            manifest, readback = self.evidence()
            saved = manifest['run_flags']['youtube_status_evidence']
            target = saved[location] if location else saved
            target[field] = value
            with self.subTest(location=location, field=field):
                self.assertFalse(audit.youtube_disclosure_verification(manifest, readback)['verified'])

    def test_delayed_get_can_verify_raw_exact_ack_despite_initial_mismatch(self):
        manifest, readback = self.evidence()
        saved = manifest['run_flags']['youtube_status_evidence']
        saved.update(verified=False, state='status_mismatch', status_readback={'privacyStatus': 'private'})
        self.assertTrue(audit.youtube_disclosure_verification(manifest, readback)['verified'])
        readback['mutable_status'].pop('publishAt')
        self.assertFalse(audit.youtube_disclosure_verification(manifest, readback)['verified'])

    def test_direct_true_owner_get_needs_no_saved_ack_but_owner_must_match(self):
        _, readback = self.evidence()
        readback['mutable_status']['containsSyntheticMedia'] = True
        self.assertTrue(audit.youtube_disclosure_verification(manifest_data(), readback)['verified'])
        readback['channel_id'] = 'wrong-channel'
        self.assertFalse(audit.youtube_disclosure_verification(manifest_data(), readback)['verified'])


if __name__ == '__main__':
    unittest.main()
