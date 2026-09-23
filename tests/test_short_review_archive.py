import hashlib
import ast
import json
import os
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import patch

from tools.short_review_archive import save_review_archive


class ReviewArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.video = self.root / 'SHORT_example.mp4'
        self.video.write_bytes(b'original rendered video')
        self.cover = self.root / 'thumbnail_example.png'
        self.cover.write_bytes(b'original cover')
        self.args = dict(
            video_path=self.video, thumbnail_path=self.cover, topic='Sample checks',
            youtube_title='Check a sample', instagram_title='Your first sample check?',
            script_voice='Check silai', tts_input='Check सिलाई', script_english='Check stitching',
            youtube_id='abcdefghijk', instagram_id='123456789', test_mode=False,
            run_flags={'tts': 'elevenlabs', 'veo_fallback': False},
        )

    def test_successful_publish_cleanup_cannot_remove_review_cover(self):
        with patch.dict(os.environ, {'GITHUB_RUN_ID': '1234', 'SECRET_TOKEN': 'not-for-archive'}):
            output = save_review_archive(**self.args)
        self.cover.unlink()
        record = json.loads(output.read_text())
        for kind in ('video', 'cover'):
            item = record['assets'][kind]
            body = (output.parent / item['file']).read_bytes()
            self.assertEqual(item['bytes'], len(body))
            self.assertEqual(item['sha256'], hashlib.sha256(body).hexdigest())
        self.assertEqual(record['source_posts'], {'bot_youtube': 'abcdefghijk', 'instagram': '123456789'})
        self.assertEqual(record['script']['tts_input'], 'Check सिलाई')
        self.assertEqual(record['workflow']['github_run_id'], '1234')
        self.assertNotIn('SECRET_TOKEN', output.read_text())
        self.assertNotIn('not-for-archive', output.read_text())
        self.assertFalse(record['main_publication_approved'])
        self.assertEqual(self.video.read_bytes(), b'original rendered video')

    def test_partial_or_test_runs_are_retained_without_invented_posts(self):
        self.cover.unlink()
        self.args.update(youtube_id=None, instagram_id=None, test_mode=True)
        record = json.loads(save_review_archive(**self.args).read_text())
        self.assertIsNone(record['assets']['cover'])
        self.assertEqual(record['source_posts'], {'bot_youtube': None, 'instagram': None})
        self.assertTrue(record['test_mode'])
        self.assertEqual(record['review_status'], 'unreviewed')
        self.assertNotIn('normalized_voice', record['assets'])

    def test_normalized_voice_is_copied_exactly_and_survives_source_cleanup(self):
        source = self.root / 'voice_123.wav'
        with wave.open(str(source), 'wb') as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
            output.writeframes(b'\x01\x00' * 1600)
        before = source.read_bytes()
        self.args['normalized_voice_path'] = source
        manifest = json.loads(save_review_archive(**self.args).read_text())
        source.unlink()
        asset = manifest['assets']['normalized_voice']
        archived = self.root / asset['file']
        self.assertEqual(archived.read_bytes(), before)
        self.assertEqual(asset['bytes'], len(before))
        self.assertEqual(asset['sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(asset['stage'], 'normalized_tts_before_fade_and_mix')
        self.assertEqual(asset['file'], 'review_normalized_voice.wav')
        self.assertNotIn(str(self.root), json.dumps(asset))

    def test_explicit_missing_or_empty_voice_cannot_claim_diagnostic_backup(self):
        source = self.root / 'missing.mp3'
        self.args['normalized_voice_path'] = source
        with self.assertRaises(FileNotFoundError):
            save_review_archive(**self.args)
        source.write_bytes(b'')
        with self.assertRaises(ValueError):
            save_review_archive(**self.args)
        self.assertFalse((self.root / 'review_manifest.json').exists())

    def test_both_daily_archives_use_exact_audiofileclip_source_and_workflow_retains_copy(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / 'daily_short.py').read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'save_review_archive']
        self.assertEqual(len(calls), 2)
        for call in calls:
            voice = next(keyword.value for keyword in call.keywords if keyword.arg == 'normalized_voice_path')
            self.assertIsInstance(voice, ast.Name)
            self.assertEqual(voice.id, 'audio_path')
        self.assertIn('/tmp/yt_shorts/review_normalized_voice.*', (root / '.github/workflows/daily_short.yml').read_text())

    def test_missing_or_empty_video_never_creates_manifest(self):
        for empty in (True, False):
            with self.subTest(empty=empty):
                if empty:
                    self.video.write_bytes(b'')
                else:
                    self.video.unlink()
                with self.assertRaises((ValueError, FileNotFoundError)):
                    save_review_archive(**self.args)
                self.assertFalse((self.root / 'review_manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
