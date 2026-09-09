import base64
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import private_dialogue_pilot as pilot


class DialogueResumeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.out = Path(self.directory.name) / 'output'
        self.out.mkdir()
        self.key = AESGCM.generate_key(bit_length=256)
        self.source = b'reviewed-source-fixture'
        self.source_hash = hashlib.sha256(self.source).hexdigest()
        self.audio = b'RIFF' + (20).to_bytes(4, 'little') + b'WAVE' + b'private-wav-fixture'
        count = len(pilot.SCRIPT)
        self.pack = {
            'format': pilot.RESUME_FORMAT,
            'original_run_id': pilot.RESUME_ORIGINAL_RUN,
            'original_video_attempts': 0,
            'source_sha256': self.source_hash,
            'script': pilot.SCRIPT,
            'speech_wav_base64': base64.b64encode(self.audio).decode(),
            'speech_wav_sha256': hashlib.sha256(self.audio).hexdigest(),
            'alignment': {
                'characters': list(pilot.SCRIPT),
                'character_start_times_seconds': [i * 11.4 / count for i in range(count)],
                'character_end_times_seconds': [(i + 1) * 11.4 / count for i in range(count)]},
            'original_tts_receipt': {'request_id': 'original-private-receipt',
                                     'characters_requested': count, 'attempts': 1}}
        self.patch(pilot, 'OUT', self.out)
        self.patch(pilot.shared, 'OUT', self.out)
        environment = patch.dict(os.environ, {
            'PRIVATE_DIALOGUE_RESUME_KEY': base64.b64encode(self.key).decode(),
            'REPLICATE_API_TOKEN': 'fake-token', 'PILOT_PUBLIC_KEY': 'mock-public-key',
            'GITHUB_RUN_ATTEMPT': '1'}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.probe = self.patch(pilot.shared, 'probe', return_value=11.517098)

    def patch(self, target, name, *args, **kwargs):
        patcher = patch.object(target, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def encrypted(self, pack=None):
        nonce = os.urandom(12)
        data = json.dumps(self.pack if pack is None else pack, ensure_ascii=False).encode()
        return pilot.RESUME_MAGIC + nonce + AESGCM(self.key).encrypt(nonce, data, pilot.RESUME_FORMAT.encode())

    def storage(self, blob):
        s3 = Mock()
        s3.get_object.side_effect = lambda **kwargs: {'ContentLength': len(blob), 'Body': io.BytesIO(blob)}
        s3.download_file.side_effect = lambda bucket, key, path: Path(path).write_bytes(self.source)
        return s3

    def resume(self, blob=None, expected_hash=None):
        blob = blob or self.encrypted()
        meta = {}
        duration = pilot.resume_speech(self.storage(blob), 'p/reuse.enc',
                                       expected_hash or hashlib.sha256(blob).hexdigest(), self.source_hash, meta)
        return duration, meta

    def test_complete_original_11_second_speech_is_reused_without_modification(self):
        duration, meta = self.resume()
        self.assertEqual(duration, 11.517098)
        self.assertEqual((self.out / 'speech.wav').read_bytes(), self.audio)
        self.assertEqual(meta['tts_requests_this_run'], 0)
        self.assertEqual(meta['original_tts_run_id'], '34377197796')
        self.assertEqual(meta['tts_receipt'], self.pack['original_tts_receipt'])

    def test_changed_encrypted_hash_is_rejected_before_audio_is_written(self):
        with self.assertRaisesRegex(ValueError, 'reviewed hash'):
            self.resume(expected_hash='0' * 64)
        self.assertFalse((self.out / 'speech.wav').exists())

    def test_tampered_ciphertext_and_wrong_secret_fail_authentication(self):
        blob = self.encrypted()
        damaged = blob[:-1] + bytes([blob[-1] ^ 1])
        with self.assertRaisesRegex(ValueError, 'authenticated payload'):
            self.resume(damaged)
        with patch.dict(os.environ, {'PRIVATE_DIALOGUE_RESUME_KEY': base64.b64encode(b'x' * 32).decode()}):
            with self.assertRaisesRegex(ValueError, 'authenticated payload'):
                self.resume(blob)
        self.assertFalse((self.out / 'speech.wav').exists())

    def test_script_source_original_run_and_no_previous_video_are_bound(self):
        for key, value in [('script', pilot.SCRIPT[:-1]), ('source_sha256', '0' * 64),
                           ('original_run_id', 'unreviewed-run'), ('original_video_attempts', 1),
                           ('original_video_attempts', False)]:
            with self.subTest(key=key, value=value):
                pack = {**self.pack, key: value}
                with self.assertRaisesRegex(ValueError, 'original speech-only run'):
                    self.resume(self.encrypted(pack))
        self.assertFalse((self.out / 'speech.wav').exists())

    def test_receipt_audio_hash_and_truncated_alignment_fail_closed(self):
        variants = [
            ({**self.pack, 'original_tts_receipt': {}}, 'receipt'),
            ({**self.pack, 'speech_wav_sha256': '0' * 64}, 'WAV'),
            ({**self.pack, 'alignment': {**self.pack['alignment'], 'characters': list(pilot.SCRIPT[:-1])}},
             'complete script')]
        for pack, expected in variants:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    self.resume(self.encrypted(pack))

    def test_short_long_or_incomplete_audio_is_rejected(self):
        for seconds in (9.99, 20.01):
            with self.subTest(seconds=seconds), self.assertRaisesRegex(ValueError, '10–20s'):
                pilot.validate_complete_speech(seconds, self.pack['alignment'])
        with self.assertRaisesRegex(ValueError, 'timing differs'):
            pilot.validate_complete_speech(10, self.pack['alignment'])
        with self.assertRaisesRegex(ValueError, 'too far before'):
            pilot.validate_complete_speech(15, self.pack['alignment'])

    def main_resume(self, execute=True, pack=None):
        self.out.rmdir()
        blob = self.encrypted(pack)
        s3 = self.storage(blob)
        self.patch(pilot.shared, 'validate_inputs', return_value=Mock())
        self.patch(pilot.shared, 'encrypt_output')
        self.probe.side_effect = [20, 11.517098]
        properties = {name: {} for name in ('video', 'audio', 'enable_dynamic_duration',
                                          'disable_music_track', 'enable_speech_enhancement')}
        model = {'latest_version': {'id': 'reviewed-model-version', 'openapi_schema': {
            'components': {'schemas': {'Input': {'properties': properties}}}}}}
        request = self.patch(pilot, 'request', return_value=Mock(json=lambda: model))
        make_speech = self.patch(pilot, 'make_speech')
        make_video = self.patch(pilot, 'make_video')
        args = ['private_dialogue_pilot.py', '--source-key', 'p/source.mp4', '--source-sha256', self.source_hash,
                '--resume-audio-key', 'p/reuse.enc', '--resume-audio-sha256', hashlib.sha256(blob).hexdigest()]
        if execute:
            args.append('--execute')
        with patch.object(sys, 'argv', args), patch('boto3.client', return_value=s3):
            result = pilot.main()
        return result, request, make_speech, make_video

    def test_execute_resume_calls_only_replicate_preflight_and_one_video_no_tts(self):
        result, request, make_speech, make_video = self.main_resume()
        self.assertEqual(result, 0)
        make_speech.assert_not_called()
        request.assert_called_once()
        self.assertEqual(request.call_args.args, ('GET', pilot.API + '/models/' + pilot.VIDEO_MODEL))
        make_video.assert_called_once()
        self.assertEqual(make_video.call_args.args[0], 11.517098)
        self.assertEqual(make_video.call_args.args[1]['tts_requests_this_run'], 0)

    def test_preflight_resume_never_generates_speech_or_video(self):
        result, request, make_speech, make_video = self.main_resume(execute=False)
        self.assertEqual(result, 0)
        make_speech.assert_not_called()
        make_video.assert_not_called()
        self.assertEqual(request.call_count, 1)

    def test_invalid_resume_never_falls_back_to_voice_generation_or_provider_calls(self):
        result, request, make_speech, make_video = self.main_resume(pack={**self.pack, 'script': 'partial'})
        self.assertEqual(result, 1)
        request.assert_not_called()
        make_speech.assert_not_called()
        make_video.assert_not_called()

    def test_missing_hash_is_rejected_before_any_download_or_generation(self):
        self.patch(pilot.shared, 'validate_inputs', return_value=Mock())
        with patch.object(sys, 'argv', ['pilot', '--source-key', 'p/source.mp4',
                                       '--source-sha256', self.source_hash, '--resume-audio-key', 'p/reuse.enc']), \
             patch('boto3.client') as client:
            with self.assertRaisesRegex(ValueError, 'both'):
                pilot.main()
            client.assert_not_called()

    def test_workflow_resume_is_optional_and_only_encrypted_artifact_uploads(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/private_dialogue_pilot.yml').read_text()
        self.assertIn('resume_audio_key:', workflow)
        self.assertIn('resume_audio_sha256:', workflow)
        self.assertIn('secrets.PRIVATE_DIALOGUE_RESUME_KEY', workflow)
        self.assertIn('path: private-dialogue-pilot.enc', workflow)
        self.assertNotIn('path: dialogue-output', workflow)


if __name__ == '__main__':
    unittest.main()
