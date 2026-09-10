import base64
import io
import json
import os
from pathlib import Path
import sys
import struct
import tempfile
import unittest
import zipfile
from unittest.mock import Mock, patch

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import private_dialogue_episodes as pilot


SCRIPT = ' '.join(['कपड़ा'] * 47) + ' फिट चेक करो।'


def episode(id_='fit'):
    return {'id': id_, 'source_key': 'p/' + id_ + '.enc', 'source_sha256': 'a' * 64,
            'source_has_original_audio': True, 'source_encrypted': True, 'script': SCRIPT}


def alignment(script=SCRIPT, duration=22):
    count = len(script)
    return {'characters': list(script),
            'character_start_times_seconds': [i * (duration - 0.1) / count for i in range(count)],
            'character_end_times_seconds': [(i + 1) * (duration - 0.1) / count for i in range(count)]}


def assessment(words=('फिट', 'कपड़ा')):
    return {'verdict': 'pass', 'complete': True, 'pronunciation_clear': True,
            'naturalness_acceptable': True, 'uncertain': False, 'heard_text': SCRIPT,
            'notes': 'Machine review only.', 'issues': [],
            'word_checks': [{'word': w, 'heard': w, 'clear': True} for w in words]}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.etags = {}
        self.writes = []
        self.fail = False

    def get_object(self, Bucket, Key):
        data = self.objects[Key]
        return {'ContentLength': len(data), 'Body': io.BytesIO(data)}

    def put_object(self, **kwargs):
        if self.fail:
            raise RuntimeError('Simulated storage failure')
        key = kwargs['Key']
        if kwargs.get('IfNoneMatch') == '*' and key in self.objects:
            raise RuntimeError('PreconditionFailed')
        if 'IfMatch' in kwargs and kwargs['IfMatch'] != self.etags.get(key):
            raise RuntimeError('PreconditionFailed')
        self.objects[key] = kwargs['Body']
        self.etags[key] = '"' + pilot.digest(kwargs['Body']) + '"'
        self.writes.append(kwargs)
        return {'ETag': self.etags[key]}


class EpisodeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.out = Path(temp.name) / 'out'
        self.out.mkdir()
        self.set_patch(pilot, 'OUT', self.out)
        self.set_patch(pilot.shared, 'OUT', self.out)
        env = patch.dict(os.environ, {'GITHUB_RUN_ID': '12345', 'GITHUB_RUN_ATTEMPT': '1',
                         'ELEVENLABS_API_KEY': 'fake-eleven', 'GOOGLE_API_KEY': 'fake-google',
                         'REPLICATE_API_TOKEN': 'fake-replicate', 'PILOT_PUBLIC_KEY': 'mock-pem'}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.s3 = FakeS3()
        self.claim = pilot.Claim(self.s3, 'f' * 64, ['fit', 'print-sample'])
        self.claim.persist()

    def set_patch(self, target, name, *args, **kwargs):
        patcher = patch.object(target, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def test_only_one_or_two_reviewed_unique_episodes_with_bounded_hindi_scripts(self):
        valid = {'format': pilot.FORMAT, 'episodes': [episode(), episode('print-sample')]}
        self.assertEqual(len(pilot.validate_manifest(valid)), 2)
        invalids = [[], [episode()] * 2, [episode(), episode('print-sample'), episode()],
                    [{**episode(), 'id': 'third'}], [{**episode(), 'script': 'short'}],
                    [{**episode(), 'script': ' '.join(['कपड़ा'] * 56) + '।'}],
                    [{**episode(), 'script': SCRIPT + ' https://example.com'}],
                    [{**episode(), 'source_encrypted': False}],
                    [{**episode(), 'source_key': 'p/source.mp4'}],
                    [{**episode(), 'source_has_original_audio': False}],
                    [{**episode(), 'watch_words': ['not-in-script']}]]
        for items in invalids:
            with self.subTest(items=items), self.assertRaises(ValueError):
                pilot.validate_manifest({'format': pilot.FORMAT, 'episodes': items})

    def test_encrypted_source_authenticates_episode_id_and_exact_plaintext_hash(self):
        key, nonce = AESGCM.generate_key(bit_length=256), b'n' * 12
        source = b'\x00\x00\x00\x18ftypisom' + b'private-source-fixture'
        item = {**episode(), 'source_sha256': pilot.digest(source)}
        blob = pilot.SOURCE_MAGIC + nonce + AESGCM(key).encrypt(nonce, source, b'fit')
        self.assertEqual(pilot.decrypt_source(blob, item, key), source)
        for changed in [{**item, 'id': 'print-sample'}, {**item, 'source_sha256': '0' * 64}]:
            with self.assertRaises((ValueError, InvalidTag)):
                pilot.decrypt_source(blob, changed, key)
        with self.assertRaises(InvalidTag):
            pilot.decrypt_source(blob[:-1] + bytes([blob[-1] ^ 1]), item, key)

    def test_duration_alignment_and_complete_script_are_required_without_truncation(self):
        pilot.validate_speech(SCRIPT, 22, alignment(), 30)
        for duration, times in [(30.01, alignment()), (9.9, alignment()),
                                (22, alignment(SCRIPT[:-1])), (24, alignment()),
                                (22, {**alignment(), 'character_end_times_seconds': [float('nan')] * len(SCRIPT)})]:
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                pilot.validate_speech(SCRIPT, duration, times, 30)
        with self.assertRaises(ValueError):
            pilot.validate_speech(SCRIPT, 22, alignment(), 21)

    def test_source_requires_original_audio_stream_and_thirty_second_portrait(self):
        info = {'format': {'duration': '30'}, 'streams': [
            {'codec_type': 'video', 'width': 1080, 'height': 1920}, {'codec_type': 'audio'}]}
        with patch.object(pilot, 'media_info', return_value=info):
            self.assertEqual(pilot.source_info(Path('fixture.mp4')), 30)
        for changed in [{**info, 'streams': info['streams'][:1]}, {**info, 'format': {'duration': '20'}}]:
            with patch.object(pilot, 'media_info', return_value=changed), self.assertRaises(ValueError):
                pilot.source_info(Path('fixture.mp4'))

    def test_rerun_denied_and_fresh_duplicate_dispatch_cannot_replace_persistent_claim(self):
        pilot.guard_execution()
        with patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': '2'}), self.assertRaises(ValueError):
            pilot.guard_execution()
        with patch.dict(os.environ, {'GITHUB_RUN_ID': '67890'}):
            duplicate = pilot.Claim(self.s3, 'f' * 64, ['fit', 'print-sample'])
            with self.assertRaisesRegex(RuntimeError, 'PreconditionFailed'):
                duplicate.persist()
        self.assertEqual(json.loads(self.s3.objects[self.claim.key])['run_id'], '12345')

    def test_pending_storage_failure_prevents_paid_tts(self):
        self.s3.fail = True
        with patch.object(pilot, 'request') as request, self.assertRaises(RuntimeError):
            pilot.make_speech(episode(), 30, self.claim)
        request.assert_not_called()

    def test_ambiguous_tts_is_saved_pending_and_never_retried(self):
        with patch.object(pilot, 'request', side_effect=TimeoutError('ambiguous')) as request:
            with self.assertRaises(TimeoutError):
                pilot.make_speech(episode(), 30, self.claim)
            with self.assertRaises(ValueError):
                pilot.make_speech(episode(), 30, self.claim)
        self.assertEqual(request.call_count, 1)
        stored = json.loads(self.s3.objects[self.claim.key])
        self.assertEqual(stored['episodes']['fit']['tts'], {'status': 'pending', 'attempts': 1})

    def test_tts_sends_exact_script_fixed_voice_and_only_allowed_filter(self):
        data = {'audio_base64': base64.b64encode(b'ID3-fixture').decode(), 'alignment': alignment()}
        with patch.object(pilot, 'request', return_value=Mock(json=lambda: data, headers={'request-id': 'tts-1'})) as request, \
             patch.object(pilot.shared, 'run') as run, patch.object(pilot.shared, 'probe', return_value=22):
            self.assertEqual(pilot.make_speech(episode(), 30, self.claim), 22)
        payload = request.call_args.kwargs['json']
        self.assertEqual(payload['text'], SCRIPT)
        self.assertEqual(payload['model_id'], 'eleven_multilingual_v2')
        self.assertIn(pilot.VOICE_ID, request.call_args.args[1])
        self.assertEqual(run.call_args.args[run.call_args.args.index('-af') + 1],
                         'highpass=f=60,loudnorm=I=-16:TP=-1.5:LRA=11')

    def test_native_audio_request_contains_wav_and_structured_machine_review(self):
        (self.out / 'fit-speech.wav').write_bytes(b'RIFF-private-speech')
        result = {'id': 'qa-1', 'status': 'completed', 'steps': [{'type': 'model_output',
                  'content': [{'type': 'text', 'text': json.dumps(assessment())}]}]}
        with patch.object(pilot, 'request', return_value=Mock(json=lambda: result)) as request:
            pilot.assess_speech(episode(), self.claim)
        payload = request.call_args.kwargs['json']
        self.assertEqual(payload['model'], pilot.QA_MODEL)
        self.assertFalse(payload['store'])
        self.assertEqual(base64.b64decode(payload['input'][1]['data']), b'RIFF-private-speech')
        self.assertEqual(payload['input'][1]['mime_type'], 'audio/wav')
        self.assertEqual(payload['response_format']['mime_type'], 'application/json')
        self.assertEqual(self.claim.state['episodes']['fit']['audio_qa']['status'], 'passed')
        saved = json.loads((self.out / 'fit-audio-qa.json').read_bytes())
        self.assertEqual(saved['review_type'], 'machine_native_audio_not_human_listening')

    def test_any_audio_uncertainty_defect_or_missing_word_blocks_lips(self):
        pilot.validate_assessment(assessment(), ['फिट', 'कपड़ा'])
        for key, value in [('verdict', 'uncertain'), ('uncertain', True), ('complete', False),
                           ('pronunciation_clear', False), ('naturalness_acceptable', False),
                           ('issues', [{'reason': 'unclear word'}]), ('word_checks', []), ('heard_text', '')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                pilot.validate_assessment({**assessment(), key: value}, ['फिट', 'कपड़ा'])
        with patch.object(pilot, 'upload') as upload, patch.object(pilot.shared, 'run') as run, self.assertRaises(ValueError):
            pilot.make_video(episode(), 22, self.claim)
        upload.assert_not_called()
        run.assert_not_called()

    def test_two_episode_pipeline_has_one_of_each_step_and_stops_on_failed_audio(self):
        items = [episode(), episode('print-sample')]
        with patch.object(pilot, 'make_speech', return_value=22) as tts, \
             patch.object(pilot, 'assess_speech') as qa, patch.object(pilot, 'make_video') as video:
            pilot.run_episodes(items, {'fit': 30, 'print-sample': 30}, self.claim)
        self.assertEqual((tts.call_count, qa.call_count, video.call_count), (2, 2, 2))
        with patch.object(pilot, 'make_speech', return_value=22) as tts, \
             patch.object(pilot, 'assess_speech', side_effect=ValueError('unclear Hindi')) as qa, \
             patch.object(pilot, 'make_video') as video, self.assertRaises(ValueError):
            pilot.run_episodes(items, {'fit': 30, 'print-sample': 30}, self.claim)
        self.assertEqual((tts.call_count, qa.call_count, video.call_count), (1, 1, 0))

    def test_untrusted_output_urls_and_redirects_cannot_receive_provider_credentials(self):
        pilot.validate_url('https://a.replicate.delivery/output.mp4?token=private', 'output')
        for url in ['http://replicate.delivery/x', 'https://replicate.delivery.evil.example/x',
                    'https://replicate.delivery@evil.example/x', 'https://replicate.delivery:444/x',
                    'https://evil.example/x', 'https://replicate.delivery/x#fragment']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                pilot.validate_url(url, 'output')
        response = Mock(status_code=302, text='private signed URL')
        with patch.object(pilot.SESSION, 'request', return_value=response) as request, self.assertRaises(RuntimeError):
            pilot.request('GET', 'https://replicate.delivery/output.mp4')
        self.assertFalse(request.call_args.kwargs['allow_redirects'])

    def test_video_submission_is_once_retains_source_audio_and_disables_voice_changes(self):
        self.claim.begin('fit', 'audio_qa')
        self.claim.finish('fit', 'audio_qa', 'passed', 'qa-1')
        queued = {'id': 'video1', 'urls': {'get': pilot.REPLICATE + '/predictions/video1',
                                        'cancel': pilot.REPLICATE + '/predictions/video1/cancel'}}
        failed = {'id': 'video1', 'status': 'failed'}
        with patch.object(pilot.shared, 'run') as run, patch.object(pilot.shared, 'probe', return_value=22), \
             patch.object(pilot, 'upload', return_value=pilot.REPLICATE + '/files/file1'), \
             patch.object(pilot, 'request', side_effect=[Mock(json=lambda: queued), Mock(json=lambda: failed)]) as request:
            with self.assertRaises(RuntimeError):
                pilot.make_video(episode(), 22, self.claim)
            with self.assertRaises(ValueError):
                pilot.make_video(episode(), 22, self.claim)
        posts = [c for c in request.call_args_list if c.args[0] == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertIn('0:a:0', run.call_args_list[0].args)
        self.assertEqual(posts[0].kwargs['headers']['Cancel-After'], '12m')
        self.assertIs(posts[0].kwargs['json']['input']['enable_dynamic_duration'], False)
        self.assertIs(posts[0].kwargs['json']['input']['enable_speech_enhancement'], False)

    def test_twelve_minute_timeout_cancels_without_second_prediction(self):
        self.claim.begin('fit', 'audio_qa')
        self.claim.finish('fit', 'audio_qa', 'passed', 'qa-1')
        queued = {'id': 'v1', 'urls': {'get': pilot.REPLICATE + '/predictions/v1',
                                     'cancel': pilot.REPLICATE + '/predictions/v1/cancel'}}
        with patch.object(pilot.shared, 'run'), patch.object(pilot.shared, 'probe', return_value=22), \
             patch.object(pilot, 'upload', return_value=pilot.REPLICATE + '/files/f1'), \
             patch.object(pilot.time, 'monotonic', side_effect=[0, 721]), \
             patch.object(pilot, 'request', side_effect=[Mock(json=lambda: queued), Mock()]) as request, \
             self.assertRaises(RuntimeError):
            pilot.make_video(episode(), 22, self.claim)
        self.assertEqual([c.args[1] for c in request.call_args_list],
                         [pilot.REPLICATE + '/models/' + pilot.VIDEO_MODEL + '/predictions', queued['urls']['cancel']])

    def test_manifest_hash_failure_prevents_all_provider_requests_and_still_encrypts(self):
        for item in self.out.iterdir():
            item.unlink()
        self.out.rmdir()
        self.s3.objects['p/manifest.json'] = b'changed-bytes'
        args = ['runner', '--manifest-key', 'p/manifest.json', '--manifest-sha256', 'a' * 64]
        with patch.object(sys, 'argv', args), patch('boto3.client', return_value=self.s3), \
             patch.object(pilot.shared, 'validate_inputs', return_value=Mock()), \
             patch.object(pilot.shared, 'encrypt_output') as encrypt, patch.object(pilot, 'request') as request:
            self.assertEqual(pilot.main(), 1)
        request.assert_not_called()
        encrypt.assert_called_once()

    def test_dryrun_validates_encrypted_sources_without_claim_or_paid_calls(self):
        key = AESGCM.generate_key(bit_length=256)
        source = b'\x00\x00\x00\x18ftypisom' + b'private-source-fixture'
        item = {**episode(), 'source_sha256': pilot.digest(source)}
        raw = pilot.encoded({'format': pilot.FORMAT, 'episodes': [item]})
        self.s3.objects['p/manifest.json'] = raw
        self.s3.objects[item['source_key']] = pilot.SOURCE_MAGIC + b'n' * 12 + AESGCM(key).encrypt(b'n' * 12, source, b'fit')
        for file in self.out.iterdir():
            file.unlink()
        self.out.rmdir()
        writes_before = len(self.s3.writes)
        args = ['runner', '--manifest-key', 'p/manifest.json', '--manifest-sha256', pilot.digest(raw)]
        with patch.object(sys, 'argv', args), patch('boto3.client', return_value=self.s3), \
             patch.dict(os.environ, {'PRIVATE_EPISODES_KEY': base64.b64encode(key).decode()}), \
             patch.object(pilot.shared, 'validate_inputs', return_value=Mock()), \
             patch.object(pilot.shared, 'encrypt_output'), patch.object(pilot, 'source_info', return_value=30), \
             patch.object(pilot, 'preflight') as preflight, patch.object(pilot, 'run_episodes') as generate:
            self.assertEqual(pilot.main(), 0)
        preflight.assert_called_once()
        generate.assert_not_called()
        self.assertEqual(len(self.s3.writes), writes_before)

    def test_private_artifact_roundtrip_contains_sources_speech_and_diagnostics(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        (self.out / 'fit-source.mp4').write_bytes(b'private-warehouse-source')
        (self.out / 'fit-speech.wav').write_bytes(b'private-own-voice')
        pilot.save('fit-audio-qa.json', {'review_type': 'machine_native_audio_not_human_listening'})
        destination = self.out.parent / 'result.enc'
        pilot.shared.encrypt_output(key.public_key(), destination)
        data = destination.read_bytes()
        self.assertNotIn(b'private-own-voice', data)
        length = struct.unpack('>I', data[:4])[0]
        header_bytes = data[4:4 + length]
        header = json.loads(header_bytes)
        secret = key.decrypt(base64.b64decode(header['key']), padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        plain = AESGCM(secret).decrypt(base64.b64decode(header['nonce']), data[4 + length:], header_bytes)
        with zipfile.ZipFile(io.BytesIO(plain)) as archive:
            self.assertEqual(archive.read('fit-source.mp4'), b'private-warehouse-source')
            self.assertEqual(archive.read('fit-speech.wav'), b'private-own-voice')
            self.assertIn('fit-audio-qa.json', archive.namelist())

    def test_stale_claim_cannot_overwrite_pending_receipt(self):
        previous_etag = self.claim.etag
        self.claim.begin('fit', 'tts')
        self.claim.etag = previous_etag
        with self.assertRaisesRegex(RuntimeError, 'PreconditionFailed'):
            self.claim.finish('fit', 'tts', 'returned', 'tts1')
        self.assertEqual(json.loads(self.s3.objects[self.claim.key])['episodes']['fit']['tts']['status'], 'pending')

    def test_workflow_only_manual_default_dryrun_encrypted_artifact(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/private_dialogue_episodes.yml').read_text()
        self.assertIn('workflow_dispatch:', workflow)
        self.assertIn('default: false', workflow)
        self.assertIn('path: private-dialogue-episodes.enc', workflow)
        self.assertIn('secrets.PRIVATE_EPISODES_KEY', workflow)
        self.assertNotIn('schedule:', workflow)
        self.assertNotIn('contents: write', workflow)


if __name__ == '__main__':
    unittest.main()
