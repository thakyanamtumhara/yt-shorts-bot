import ast
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / 'daily_short.py').read_text())


def load(name, scope):
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / 'daily_short.py'), 'exec'), scope)
    return scope[name]


class FakeClip:
    def __init__(self, path):
        self.path, self.duration = path, 8.0

    def get_frame(self, t):
        return np.full((64, 36, 3), 40 if 'hem' in self.path else 200, dtype=np.uint8)

    def close(self):
        pass


def client(text):
    resp = SimpleNamespace(content=[SimpleNamespace(text=text)], usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    return SimpleNamespace(messages=SimpleNamespace(create=Mock(return_value=resp)))


class CoverFramePickTest(unittest.TestCase):
    def pick(self, exists=True):
        scope = {'os': SimpleNamespace(path=SimpleNamespace(exists=lambda p: exists, basename=os.path.basename)),
                 're': re, 'VideoFileClip': FakeClip, 'THUMBNAIL_WIDTH': 108, 'THUMBNAIL_HEIGHT': 192,
                 'print': lambda *a, **k: None}
        return load('_pick_cover_frame', scope)

    def test_the_frame_claude_names_becomes_the_cover_base(self):
        c = client('3')
        img = self.pick()(c, ['a_neck.mp4', 'b_hem.mp4', 'c.mp4'], 'Hem lesson', 'script', cover_feedback='show the hem')
        self.assertEqual(img.size, (108, 192))
        self.assertEqual(img.getpixel((5, 5)), (40, 40, 40))
        sent = c.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertEqual(sum(1 for part in sent if part['type'] == 'image'), 6)
        self.assertIn('show the hem', sent[-1]['text'])

    def test_no_clear_frame_keeps_the_first_clip(self):
        for answer in ('0', 'none', '7', ''):
            with self.subTest(answer=answer):
                self.assertIsNone(self.pick()(client(answer), ['a.mp4', 'b.mp4'], 't', 's'))

    def test_fewer_than_two_frames_never_calls_claude(self):
        c = client('1')
        self.assertIsNone(self.pick(exists=False)(c, ['a.mp4', 'b.mp4'], 't', 's'))
        c.messages.create.assert_not_called()


class CoverCallSitesTest(unittest.TestCase):
    def test_both_run_covers_offer_every_clip(self):
        source = (ROOT / 'daily_short.py').read_text()
        self.assertEqual(source.count('clip_paths=downloaded_clips'), 2)


if __name__ == '__main__':
    unittest.main()
