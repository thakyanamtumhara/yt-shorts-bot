import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class SourcePostJoinTests(unittest.TestCase):
    def save(self, video_id, environment):
        tree = ast.parse((ROOT / 'daily_short.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'save_ig_upload_record')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'history.json'
            scope = {'os': os, 'json': json, 're': re, 'datetime': datetime,
                     'pytz': SimpleNamespace(timezone=lambda name: timezone.utc), 'TIMEZONE': 'UTC',
                     'IG_ENGAGEMENT_FILE': str(path), 'IG_POST_META': {}}
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / 'daily_short.py'), 'exec'), scope)
            with patch.dict(os.environ, environment, clear=True):
                scope['save_ig_upload_record']('12345', 'English title', 'Topic', bot_youtube_id=video_id)
            return json.loads(path.read_text())[0]

    def test_same_run_pair_survives_separate_titles(self):
        record = self.save('abcdefghijk', {'GITHUB_RUN_ID': '1234', 'GITHUB_REPOSITORY': 'thakyanamtumhara/yt-shorts-bot'})
        self.assertEqual(record['bot_youtube_id'], 'abcdefghijk')
        self.assertEqual(record['workflow_run_id'], '1234')
        self.assertEqual(record['source_repository'], 'thakyanamtumhara/yt-shorts-bot')

    def test_missing_failed_local_or_foreign_run_does_not_claim_provenance(self):
        good = {'GITHUB_RUN_ID': '1234', 'GITHUB_REPOSITORY': 'thakyanamtumhara/yt-shorts-bot'}
        for video_id, env in ((None, good), ('?', good), ('abcdefghijk', {}),
                              ('abcdefghijk', dict(good, GITHUB_REPOSITORY='other/repo'))):
            with self.subTest(video_id=video_id, environment=env):
                record = self.save(video_id, env)
                self.assertNotIn('bot_youtube_id', record)
                self.assertNotIn('workflow_run_id', record)


if __name__ == '__main__':
    unittest.main()
