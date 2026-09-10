import base64
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import private_delivery_audition as audition


def wav(seconds=3):
    target = io.BytesIO()
    with wave.open(target, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(44100)
        audio.writeframes(b'\x00\x00' * int(seconds * 44100))
    return target.getvalue()


def pack():
    raw = wav()
    return {'format': audition.FORMAT, 'voice_id': audition.VOICE_ID, 'model_id': audition.MODEL_ID,
            'snippets': [{'id': id_, 'script': 'पहले “सैंपल का फिट” तय करो।',
                          'previous_text': '' if id_ == 'fit-open' else 'सैंपल को साथ रखकर दोनों की नाप मिला लो।',
                          'next_text': 'फिर सही फिट चुनो।' if id_ == 'fit-open' else '',
                          'settings': {'speed': 0.94, 'stability': 0.5, 'style': 0.2}}
                         for id_ in audition.IDS],
            'baselines': {group: {'script': 'पहले सैंपल का फिट तय करो।',
                                  'wav_base64': base64.b64encode(raw).decode(), 'wav_sha256': audition.digest(raw)}
                          for group in audition.GROUPS}}


def alignment(script, duration=3):
    return {'characters': list(script),
            'character_start_times_seconds': [i * (duration - 0.1) / len(script) for i in range(len(script))],
            'character_end_times_seconds': [(i + 1) * (duration - 0.1) / len(script) for i in range(len(script))]}


def comparison(labels=('baseline', 'a', 'b', 'opening')):
    return {'preferred': 'none', 'reason': 'Neither closing improves the final stress audibly.', 'uncertain': False,
            'comparisons': [{'id': id_, 'heard_text': 'पहले सैंपल का फिट तय करो।', 'complete': True,
                'pronunciation_clear': True, 'naturalness_acceptable': False, 'final_word_emphasis': 'weak',
                'hindi_ending_cadence': 'unfinished', 'heard_observations': 'Last करो is flat and sounds continuing.',
                'issues': ['Final cadence remains weak.']} for id_ in labels]}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.etags = {}
        self.fail = False

    def put_object(self, **kwargs):
        if self.fail:
            raise RuntimeError('Storage unavailable')
        key = kwargs['Key']
        if kwargs.get('IfNoneMatch') == '*' and key in self.objects:
            raise RuntimeError('PreconditionFailed')
        if 'IfMatch' in kwargs and kwargs['IfMatch'] != self.etags.get(key):
            raise RuntimeError('PreconditionFailed')
        self.objects[key] = kwargs['Body']
        self.etags[key] = audition.digest(kwargs['Body'])
        return {'ETag': self.etags[key]}


class AuditionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.out = Path(temp.name) / 'out'
        self.out.mkdir()
        self.patch(audition, 'OUT', self.out)
        self.patch(audition.episodes, 'OUT', self.out)
        self.patch(audition.shared, 'OUT', self.out)
        env = patch.dict(os.environ, {'GITHUB_RUN_ID': '12345', 'GITHUB_RUN_ATTEMPT': '1',
            'ELEVENLABS_API_KEY': 'fake-voice-key', 'GOOGLE_API_KEY': 'fake-google-key'}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.s3 = FakeS3()
        self.manifest, self.baselines = audition.validate_pack(pack())
        self.claim = audition.Claim(self.s3, audition.digest(audition.encoded(self.manifest)))
        self.claim.persist()

    def patch(self, target, name, *args, **kwargs):
        patcher = patch.object(target, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def test_only_five_exact_ids_fixed_professional_voice_and_model(self):
        for field, value in [('voice_id', 'other'), ('model_id', 'eleven_v3'),
                             ('snippets', pack()['snippets'] + [pack()['snippets'][0]])]:
            with self.subTest(field=field):
                data = pack()
                data[field] = value
                with self.assertRaises(ValueError):
                    audition.validate_pack(data)
        data = pack()
        data['snippets'][1]['id'] = 'fit-open'
        with self.assertRaises(ValueError):
            audition.validate_pack(data)

    def test_curly_quotes_allowed_but_markup_and_out_of_bounds_settings_denied(self):
        audition.validate_pack(pack())
        cases = [('script', '<break time="1s" /> फिर चुनो।'), ('script', '[emphatic] फिर चुनो।'),
                 ('script', 'क' * 260 + '।'), ('settings', {'speed': 0.89, 'stability': 0.5, 'style': 0.2}),
                 ('settings', {'speed': 0.94, 'stability': 0.2, 'style': 0.2}),
                 ('settings', {'speed': 0.94, 'stability': 0.5, 'style': 0.36}),
                 ('settings', {'speed': True, 'stability': 0.5, 'style': 0.2}),
                 ('settings', {'speed': float('nan'), 'stability': 0.5, 'style': 0.2})]
        for field, value in cases:
            with self.subTest(value=value):
                data = pack()
                data['snippets'][1][field] = value
                with self.assertRaises(ValueError):
                    audition.validate_pack(data)

    def test_context_is_required_for_targeted_join_and_not_spoken_script(self):
        for index, field, value in [(0, 'previous_text', 'पहले ये सुनो।'), (0, 'next_text', ''),
                                    (1, 'previous_text', ''), (1, 'next_text', 'आगे सुनो।')]:
            with self.subTest(index=index, field=field):
                data = pack()
                data['snippets'][index][field] = value
                with self.assertRaises(ValueError):
                    audition.validate_pack(data)

    def test_full_earlier_baseline_is_retained_byte_for_byte_and_hash_bound(self):
        data = pack()
        raw = wav(28.8)
        data['baselines']['fit'].update(wav_base64=base64.b64encode(raw).decode(), wav_sha256=audition.digest(raw))
        manifest, baselines = audition.validate_pack(data)
        self.assertEqual(baselines['fit'], raw)
        self.assertNotIn('wav_base64', manifest['baselines']['fit'])
        data['baselines']['fit']['wav_sha256'] = 'a' * 64
        with self.assertRaises(ValueError):
            audition.validate_pack(data)

    def test_encrypted_input_authenticates_key_aad_and_hash_content(self):
        secret, nonce = b'k' * 32, b'n' * 12
        encrypted = audition.MAGIC + nonce + AESGCM(secret).encrypt(nonce, audition.encoded(pack()), audition.FORMAT.encode())
        self.assertEqual(audition.decrypt_pack(encrypted, secret), pack())
        with self.assertRaises(InvalidTag):
            audition.decrypt_pack(encrypted, b'x' * 32)
        with self.assertRaises(InvalidTag):
            audition.decrypt_pack(encrypted[:-1] + bytes([encrypted[-1] ^ 1]), secret)

    def test_same_manifest_reencrypted_or_new_run_is_not_a_new_paid_attempt(self):
        with patch.dict(os.environ, {'GITHUB_RUN_ID': '99999'}):
            duplicate = audition.Claim(self.s3, audition.digest(audition.encoded(self.manifest)))
            with self.assertRaises(RuntimeError):
                duplicate.persist()
        with patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': '2'}):
            with self.assertRaises(ValueError):
                audition.episodes.guard_execution()

    def test_pending_write_failure_prevents_provider_post(self):
        self.s3.fail = True
        request = self.patch(audition.episodes, 'request')
        with self.assertRaises(RuntimeError):
            audition.make_snippet(self.manifest['snippets'][0], self.claim)
        request.assert_not_called()

    def test_ambiguous_submission_is_claimed_and_never_automatically_retried(self):
        request = self.patch(audition.episodes, 'request', side_effect=TimeoutError('Ambiguous provider submission'))
        with self.assertRaises(TimeoutError):
            audition.make_snippet(self.manifest['snippets'][0], self.claim)
        state = json.loads(self.s3.objects[self.claim.key])
        self.assertEqual(state['stages']['fit-open'], {'attempts': 1, 'status': 'pending'})
        with self.assertRaises(ValueError):
            audition.make_snippet(self.manifest['snippets'][0], self.claim)
        self.assertEqual(request.call_count, 1)

    def test_stale_state_cannot_overwrite_a_returned_receipt(self):
        stale = audition.Claim(self.s3, self.claim.state['fingerprint'])
        stale.etag = self.claim.etag
        self.claim.begin('fit-open')
        self.claim.finish('fit-open', 'receipt-one')
        with self.assertRaises(RuntimeError):
            stale.persist()
        self.assertEqual(json.loads(self.s3.objects[self.claim.key])['stages']['fit-open']['receipt'], 'receipt-one')

    def test_exact_alignment_includes_hindi_marks_quotes_and_complete_sentence(self):
        script = '“साइज़” का सही फिट चुनो।'
        audition.validate_alignment(script, 3, alignment(script))
        for changed in [script.replace('़', ''), script[:-1], script.replace('“', '')]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                audition.validate_alignment(script, 3, alignment(changed))
        for duration in [float('nan'), 19]:
            with self.assertRaises(ValueError):
                audition.validate_alignment(script, duration, alignment(script))
        timestamps = alignment(script)
        timestamps['character_end_times_seconds'][-1] = 4
        with self.assertRaises(ValueError):
            audition.validate_alignment(script, 3, timestamps)

    def mock_voice(self, script):
        response = Mock(headers={'request-id': 'voice-request', 'character-cost': str(len(script))})
        response.json.return_value = {'audio_base64': base64.b64encode(b'fake-mp3').decode(), 'alignment': alignment(script)}
        request = self.patch(audition.episodes, 'request', return_value=response)
        run = self.patch(audition.shared, 'run', side_effect=lambda *args: Path(args[-1]).write_bytes(wav()))
        self.patch(audition.shared, 'probe', return_value=3)
        return request, run

    def test_one_tts_preserves_identity_context_and_only_allowed_audio_chain(self):
        snippet = self.manifest['snippets'][1]
        request, run = self.mock_voice(snippet['script'])
        audition.make_snippet(snippet, self.claim)
        self.assertEqual(request.call_count, 1)
        payload = request.call_args.kwargs['json']
        self.assertEqual(payload['text'], snippet['script'])
        self.assertEqual(payload['previous_text'], snippet['previous_text'])
        self.assertNotIn('next_text', payload)
        self.assertEqual(payload['model_id'], audition.MODEL_ID)
        self.assertIn(audition.VOICE_ID, request.call_args.args[1])
        self.assertEqual(payload['voice_settings']['similarity_boost'], 0.75)
        self.assertIn('highpass=f=60,loudnorm=I=-16:TP=-1.5:LRA=11', run.call_args.args)
        self.assertNotIn('-t', run.call_args.args)
        self.assertEqual(self.claim.state['stages'][snippet['id']]['attempts'], 1)
        self.assertFalse((self.out / (snippet['id'] + '-raw.mp3')).exists())

    def test_bad_alignment_preserves_paid_result_but_no_regeneration(self):
        snippet = self.manifest['snippets'][1]
        request, _ = self.mock_voice(snippet['script'][:-1])
        with self.assertRaises(ValueError):
            audition.make_snippet(snippet, self.claim)
        self.assertTrue((self.out / (snippet['id'] + '.wav')).exists())
        self.assertTrue((self.out / (snippet['id'] + '-timestamps.json')).exists())
        with self.assertRaises(ValueError):
            audition.make_snippet(snippet, self.claim)
        self.assertEqual(request.call_count, 1)

    def test_exact_five_tts_and_two_comparisons_no_video_stage(self):
        speech = self.patch(audition, 'make_snippet')
        compare = self.patch(audition, 'compare_group')
        audition.run_audition(self.manifest, self.claim)
        self.assertEqual([c.args[0]['id'] for c in speech.call_args_list], list(audition.IDS))
        self.assertEqual([c.args[0] for c in compare.call_args_list], list(audition.GROUPS))
        with self.assertRaises(ValueError):
            self.claim.begin('video')

    def test_native_comparison_can_reject_both_without_forcing_pass_or_retry(self):
        for stem in ('fit-baseline', 'fit-close-a', 'fit-close-b', 'fit-open'):
            (self.out / (stem + '.wav')).write_bytes(wav())
        result = comparison()
        provider = {'id': 'qa-one', 'status': 'completed', 'steps': [{'type': 'model_output',
                    'content': [{'type': 'text', 'text': json.dumps(result)}]}]}
        request = self.patch(audition.episodes, 'request', return_value=Mock(json=Mock(return_value=provider)))
        audition.compare_group('fit', self.manifest, self.claim)
        saved = json.loads((self.out / 'fit-comparison.json').read_text())
        self.assertEqual(saved['assessment']['preferred'], 'none')
        self.assertFalse(saved['automatic_selection'])
        self.assertEqual(saved['review_type'], 'machine_native_audio_not_human_listening')
        self.assertEqual(request.call_count, 1)
        payload = request.call_args.kwargs['json']
        self.assertFalse(payload['store'])
        self.assertEqual(sum(p['type'] == 'audio' for p in payload['input']), 4)
        self.assertIn('quote marks', payload['input'][0]['text'])
        self.assertIn('मीडियम', payload['input'][0]['text'])

    def test_missing_native_sample_or_unspecific_observation_is_not_accepted(self):
        result = comparison()
        result['comparisons'].pop()
        with self.assertRaises(ValueError):
            audition.validate_comparison(result, ['baseline', 'a', 'b', 'opening'])
        result = comparison()
        result['comparisons'][0]['heard_observations'] = ''
        with self.assertRaises(ValueError):
            audition.validate_comparison(result, ['baseline', 'a', 'b', 'opening'])

    def test_preflight_denies_unready_voice_before_any_generation(self):
        request = self.patch(audition.episodes, 'request', return_value=Mock(json=Mock(return_value={
            'voice_id': audition.VOICE_ID, 'category': 'professional', 'fine_tuning': {'state': {audition.MODEL_ID: 'not_started'}}})))
        with self.assertRaises(ValueError):
            audition.preflight(self.manifest)
        self.assertEqual(request.call_args.args[0], 'GET')
        self.assertEqual(request.call_count, 1)

    def test_validate_only_path_never_claims_or_generates_and_output_is_encrypted(self):
        target = self.out / 'main-output'
        self.patch(audition, 'OUT', target)
        self.patch(audition.shared, 'validate_inputs', return_value='validated-public-key')
        encrypt = self.patch(audition.shared, 'encrypt_output')
        self.patch(audition, 'preflight')
        execute = self.patch(audition, 'run_audition')
        claims = self.patch(audition, 'Claim')
        self.patch(audition.episodes, 'read_s3', return_value=b'encrypted')
        self.patch(audition, 'decrypt_pack', return_value=pack())
        with patch.dict(os.environ, {'PILOT_PUBLIC_KEY': 'public', 'PRIVATE_DELIVERY_KEY': base64.b64encode(b'k' * 32).decode()}), \
             patch('boto3.client'), patch.object(sys, 'argv', ['audition', '--pack-key', 'p/a.enc', '--pack-sha256', 'a' * 64]):
            self.assertEqual(audition.main(), 0)
        claims.assert_not_called()
        execute.assert_not_called()
        encrypt.assert_called_once_with('validated-public-key', 'private-delivery-audition.enc')
        self.assertEqual(json.loads((target / 'result.json').read_text())['status'], 'validated_no_generation')


if __name__ == '__main__':
    unittest.main()
