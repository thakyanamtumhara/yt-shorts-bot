import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
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
