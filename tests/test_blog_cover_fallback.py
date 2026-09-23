import ast
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PIL import Image
import pytz


ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / 'daily_short.py').read_text())


def load_functions(**extra):
    scope = {'json': json, 're': re, 'datetime': datetime, 'pytz': pytz,
             'TIMEZONE': 'Asia/Kolkata', 'BLOG_BASE_URL': 'https://example.com', **extra}
    nodes = [node for node in TREE.body if isinstance(node, ast.FunctionDef)
             and node.name in ('_blog_cover_fallback', 'generate_blog_post')]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'daily_short.py', 'exec'), scope)
    return scope


class BlogCoverFallbackTest(unittest.TestCase):
    def test_missing_corrupt_small_and_wrong_format_are_rejected(self):
        fallback = load_functions()['_blog_cover_fallback']
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            self.assertEqual(fallback(None), [])
            self.assertEqual(fallback(folder / 'missing.png'), [])
            (folder / 'broken.png').write_bytes(b'not an image')
            self.assertEqual(fallback(folder / 'broken.png'), [])
            Image.new('RGB', (599, 900)).save(folder / 'small.png')
            self.assertEqual(fallback(folder / 'small.png'), [])
            Image.new('RGB', (800, 800)).save(folder / 'wrong.webp')
            self.assertEqual(fallback(folder / 'wrong.webp'), [])
            Image.new('RGB', (800, 800)).save(folder / 'spoofed.png', format='GIF')
            self.assertEqual(fallback(folder / 'spoofed.png'), [])

    def test_real_png_and_jpeg_become_same_dimensions_webp_without_source_mutation(self):
        fallback = load_functions()['_blog_cover_fallback']
        with TemporaryDirectory() as directory:
            for suffix in ('png', 'jpg'):
                path = Path(directory) / f'cover.{suffix}'
                Image.new('RGB', (900, 600), 'orange').save(path)
                original = path.read_bytes()
                result = fallback(path)
                self.assertEqual(result[0][1], 'hero.webp')
                with Image.open(BytesIO(result[0][0])) as converted:
                    self.assertEqual(converted.format, 'WEBP')
                    self.assertEqual(converted.size, (900, 600))
                self.assertEqual(path.read_bytes(), original)

    def generate(self, images, path=None):
        scope = load_functions(
            _editorial_evidence=lambda topic: '', _auto_content_hold_reason=lambda text: None,
            generate_blog_slug=lambda title: 'lesson', generate_blog_images=Mock(return_value=images),
            _load_blog_history_active=lambda: [], get_blog_prompt=Mock(return_value='Write the lesson'),
            _review_derived_content=lambda *args: (True, 'Supported'),
            inject_blog_seo=lambda html, *args, **kwargs: html, _prose_word_count=lambda html: 200)
        response = SimpleNamespace(content=[SimpleNamespace(text='<html>Supported lesson</html>')],
                                   stop_reason='end_turn')
        client = SimpleNamespace(messages=SimpleNamespace(create=Mock(return_value=response)))
        result = scope['generate_blog_post'](client, None, 'Lesson', 'Title', 'Description',
            'Supported explanation.', [], 'Hook', 'video', 'https://video.example', fallback_image_path=path)
        return result, client

    def test_provider_images_stay_preferred_even_with_valid_fallback(self):
        existing = [(b'existing-provider-image', 'hero.webp')]
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'cover.png'
            Image.new('RGB', (800, 800), 'white').save(path)
            result, client = self.generate(existing, path)
        self.assertEqual(result[3], existing)
        self.assertNotIn('existing cover', client.messages.create.call_args.kwargs['messages'][0]['content'])

    def test_provider_failure_uses_explicit_current_cover_and_labels_it_illustrative(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'cover.png'
            Image.new('RGB', (800, 800), 'white').save(path)
            result, client = self.generate([], path)
        self.assertEqual([name for _, name in result[3]], ['hero.webp'])
        self.assertIn('never as evidence of a real fabric test',
                      client.messages.create.call_args.kwargs['messages'][0]['content'])

    def test_no_implicit_cover_for_legacy_or_unrelated_article(self):
        result, _ = self.generate([])
        self.assertEqual(result[3], [])
        calls = [node for node in ast.walk(TREE) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'generate_blog_post']
        explicit = [keyword.value for call in calls for keyword in call.keywords
                    if keyword.arg == 'fallback_image_path']
        self.assertEqual(len(explicit), 1)
        self.assertIsInstance(explicit[0], ast.Name)
        self.assertEqual(explicit[0].id, 'thumbnail_path')


if __name__ == '__main__':
    unittest.main()
