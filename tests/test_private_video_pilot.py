import base64
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
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


if __name__ == '__main__':
    unittest.main()
