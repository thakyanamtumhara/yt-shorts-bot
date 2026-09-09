import base64
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tools import private_music_pair as pair


class PrivateMusicPairTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        cls.pem = cls.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        cls.prompts = ['First explicit instrumental direction with room for speech.',
                       'Second distinct instrumental direction with room for speech.']

    def decrypt(self, file):
        blob = file.read_bytes()
        size = struct.unpack('>I', blob[:4])[0]
        header = blob[4:4 + size]
        meta = json.loads(header)
        key = self.key.decrypt(base64.b64decode(meta['key']), padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        clear = AESGCM(key).decrypt(base64.b64decode(meta['nonce']), blob[4 + size:], header)
        return zipfile.ZipFile(io.BytesIO(clear))

    def test_two_calls_only_and_only_encrypted_artifact_contains_outputs(self):
        response = Mock(status_code=200, json=lambda: {'steps': [{'type': 'model_output',
            'content': [{'type': 'audio', 'mime_type': 'audio/mpeg',
                         'data': base64.b64encode(b'private-music-marker').decode()}]}]})
        with tempfile.TemporaryDirectory() as directory, patch.object(pair.requests, 'post',
                return_value=response) as post, patch.object(pair.pilot, 'probe', return_value=40):
            out, encrypted = Path(directory) / 'output', Path(directory) / 'result.enc'
            self.assertTrue(pair.generate_pair(*self.prompts, self.pem, 'test-key', out, encrypted))
            self.assertEqual(post.call_count, 2)
            self.assertNotIn(b'private-music-marker', encrypted.read_bytes())
            with self.decrypt(encrypted) as archive:
                self.assertEqual(archive.read('music-a.mp3'), b'private-music-marker')
                self.assertEqual(archive.read('music-b.mp3'), b'private-music-marker')
                meta = json.loads(archive.read('metadata.json'))
                self.assertEqual([r['status'] for r in meta['tracks']], ['succeeded', 'succeeded'])
                self.assertEqual(meta['attempt_limit'], 2)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                pair.generate_pair(*self.prompts, self.pem, 'test-key', out, encrypted)
            self.assertEqual(post.call_count, 2)

    def test_provider_failure_is_not_retried_and_metadata_is_encrypted(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(pair.requests, 'post',
                return_value=Mock(status_code=403)) as post:
            out, encrypted = Path(directory) / 'output', Path(directory) / 'result.enc'
            self.assertFalse(pair.generate_pair(*self.prompts, self.pem, 'test-key', out, encrypted))
            self.assertEqual(post.call_count, 2)
            with self.decrypt(encrypted) as archive:
                meta = json.loads(archive.read('metadata.json'))
                self.assertTrue(all(r['status'] == 'failed' for r in meta['tracks']))
                self.assertTrue(all('403' in r['error'] for r in meta['tracks']))

    def test_invalid_prompts_or_key_stop_before_any_call(self):
        with patch.object(pair.requests, 'post') as post:
            for a, b, pem in [('', self.prompts[1], self.pem),
                               (self.prompts[0], self.prompts[0], self.pem),
                               (*self.prompts, 'invalid key')]:
                with self.assertRaises(ValueError):
                    pair.generate_pair(a, b, pem, 'test-key')
            post.assert_not_called()

    def test_redirect_cannot_forward_the_google_key(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(pair.requests, 'post',
                return_value=Mock(status_code=302)) as post:
            with self.assertRaisesRegex(RuntimeError, '302'):
                pair.generate_track(self.prompts[0], Path(directory) / 'music', 'test-key')
            self.assertFalse(post.call_args.kwargs['allow_redirects'])
            self.assertEqual(post.call_count, 1)


if __name__ == '__main__':
    unittest.main()
