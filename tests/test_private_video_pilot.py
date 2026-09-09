import base64
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tools import private_video_pilot as pilot


class PilotPrivacyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        cls.pem = cls.key.public_key().public_bytes(serialization.Encoding.PEM,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def test_only_recipient_can_open_artifact_and_tampering_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            original = pilot.OUT
            try:
                pilot.OUT = Path(directory) / 'files'
                pilot.OUT.mkdir()
                (pilot.OUT / 'voice.txt').write_text('private-preview-marker')
                dest = Path(directory) / 'encrypted'
                pilot.encrypt_output(self.key.public_key(), dest)
                blob = dest.read_bytes()
                self.assertNotIn(b'private-preview-marker', blob)
                length = struct.unpack('>I', blob[:4])[0]
                header = blob[4:4 + length]
                meta = json.loads(header)
                key = self.key.decrypt(base64.b64decode(meta['key']), padding.OAEP(
                    mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
                nonce = base64.b64decode(meta['nonce'])
                ciphertext = blob[4 + length:]
                plain = AESGCM(key).decrypt(nonce, ciphertext, header)
                with zipfile.ZipFile(io.BytesIO(plain)) as archive:
                    self.assertEqual(archive.read('voice.txt'), b'private-preview-marker')
                with self.assertRaises(InvalidTag):
                    AESGCM(key).decrypt(nonce, ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]), header)
                wrong = rsa.generate_private_key(public_exponent=65537, key_size=3072)
                with self.assertRaises(ValueError):
                    wrong.decrypt(base64.b64decode(meta['key']), padding.OAEP(
                        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
            finally:
                pilot.OUT = original

    def test_spend_and_source_limits_are_checked_before_generation(self):
        for source, seconds, portrait in [('p/../../secret.mp4', 18, 5),
                                           ('p/test.mp4', 21, 5), ('p/test.mp4', 18, 100)]:
            with self.assertRaises(ValueError):
                pilot.validate_inputs(source, seconds, portrait, self.pem)
        self.assertIsInstance(pilot.validate_inputs('p/wt-fits-r8k.mp4', 18.6, 7.1, self.pem), rsa.RSAPublicKey)

    def test_changed_provider_schema_stops_before_paid_request(self):
        with patch.dict('os.environ', {'REPLICATE_API_TOKEN': 'test-key'}), \
             patch.object(pilot, 'request', return_value=Mock(json=lambda: {})) as request:
            with self.assertRaisesRegex(RuntimeError, 'schema changed'):
                pilot.replicate_avatar(10.16)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[0], 'GET')

    def test_unexpected_output_host_never_receives_account_token(self):
        model = {'latest_version': {'openapi_schema': {'components': {'schemas': {
            'Input': {'properties': {'image': {}, 'audio': {}, 'fast_mode': {}}}}}}}}
        queued = {'id': 'test', 'urls': {'get': 'https://api.replicate.com/v1/predictions/test',
                                       'cancel': 'https://api.replicate.com/v1/predictions/test/cancel'}}
        completed = {'status': 'succeeded', 'output': 'https://untrusted.invalid/video.mp4'}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(pilot, 'OUT', Path(directory)), \
             patch.dict('os.environ', {'REPLICATE_API_TOKEN': 'test-key'}), \
             patch.object(pilot, 'run'), patch.object(pilot, 'data_url', return_value='data:test'), \
             patch.object(pilot, 'request') as request:
            request.side_effect = [Mock(json=lambda: model), Mock(json=lambda: queued),
                                   Mock(json=lambda: completed)]
            with self.assertRaisesRegex(RuntimeError, 'output host'):
                pilot.replicate_avatar(10.16)
            self.assertEqual(request.call_count, 3)
            self.assertEqual(request.call_args_list[1].kwargs['headers']['Cancel-After'], '12m')
            self.assertTrue((Path(directory) / 'avatar-queue-private.json').exists())


if __name__ == '__main__':
    unittest.main()
