import ast
from datetime import datetime
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import pytz


ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / 'daily_short.py').read_text())


def load_functions(*names, **extra):
    scope = {'json': json, 're': re, 'datetime': datetime, 'pytz': pytz,
             'TIMEZONE': 'Asia/Kolkata', **extra}
    selected = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=selected, type_ignores=[]), 'daily_short.py', 'exec'), scope)
    return scope


def reviewer(value=None, error=None):
    response = SimpleNamespace(content=[SimpleNamespace(text=json.dumps(value))], stop_reason='end_turn',
                               usage=SimpleNamespace(input_tokens=12, output_tokens=8))
    return SimpleNamespace(messages=SimpleNamespace(create=Mock(side_effect=error, return_value=response)))


class EditorialGuardTest(unittest.TestCase):
    def setUp(self):
        self.scope = load_functions('_auto_content_text', '_auto_content_hold_reason',
                                    '_review_derived_content', '_carousel_hashtags',
                                    'publish_blog_to_s3', 'publish_ig_carousel')

    def test_observed_bad_publication_claims_are_held(self):
        samples = [
            'Acid Wash Oversize at ₹238 per piece, today’s actual price.',
            'Biowash costs Rs. 12 extra per piece.',
            'Varsity jacket available now.',
            'Rub combed cotton near your ear; sound identifies carded cotton.',
            'In monsoon bump your heat press to 170°C.',
            'All our products are 100% cotton.',
            '<p>Cost is &#8377;238.</p>',
            '',
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(self.scope['_auto_content_hold_reason'](sample))

    def test_supported_explanation_and_live_link_pass_local_gate(self):
        for sample in [
            'Knit loops can move under tension. Stretch alone does not tell you the fibre composition.',
            'Check the live catalogue for the rate and the stock page for your exact colour and size.',
            'Compare a 240 GSM sample with the size chart. Which fit do your buyers request?',
        ]:
            self.assertIsNone(self.scope['_auto_content_hold_reason'](sample))

    def test_no_network_or_s3_write_for_bad_content(self):
        self.assertFalse(self.scope['publish_blog_to_s3'](
            '<html><body>Varsity jacket</body></html>', 'varsity', 'Jackets', 'https://example.com'))
        self.assertIsNone(self.scope['publish_ig_carousel'](['https://example.com/a.jpg'], 'Buy for ₹238'))

    def test_review_must_explicitly_support_content_and_answer(self):
        fn = self.scope['_review_derived_content']
        for value in [None, {}, {'supported': True},
                      {'supported': 'true', 'useful_answer': True, 'reason': 'OK'},
                      {'supported': True, 'useful_answer': False, 'reason': 'No answer'},
                      {'supported': False, 'useful_answer': True, 'reason': 'Unsupported claim'}]:
            with self.subTest(value=value):
                self.assertFalse(fn(reviewer(value), None, 'Knit loops move.',
                                    'Reviewed source on knit loops.', 'article', 'same-model')[0])
        good = {'supported': True, 'useful_answer': True, 'reason': 'Within supplied loop evidence.'}
        self.assertTrue(fn(reviewer(good), None, 'Knit loops move.', 'Reviewed loop facts.',
                           'article', 'same-model')[0])

    def test_review_errors_and_missing_source_hold_without_guessing(self):
        fn = self.scope['_review_derived_content']
        client = reviewer(error=RuntimeError('unavailable'))
        self.assertFalse(fn(client, None, 'Knit loops move.', 'Evidence.', 'article', 'same-model')[0])
        client = reviewer()
        self.assertFalse(fn(client, None, 'Knit loops move.', '', 'article', 'same-model')[0])
        client.messages.create.assert_not_called()

    def test_truncated_review_cannot_approve_even_valid_json(self):
        client = reviewer({'supported': True, 'useful_answer': True, 'reason': 'Looks fine'})
        client.messages.create.return_value.stop_reason = 'max_tokens'
        self.assertFalse(self.scope['_review_derived_content'](
            client, None, 'Loops move.', 'Reviewed loop facts.', 'article', 'same-model')[0])

    def test_known_bad_claim_is_blocked_before_paid_review(self):
        client = reviewer({'supported': True, 'useful_answer': True, 'reason': 'Wrong approval'})
        result = self.scope['_review_derived_content'](client, None, '₹238 today', 'Old source', 'caption', 'same-model')
        self.assertFalse(result[0])
        client.messages.create.assert_not_called()

    def test_hashtags_are_valid_unique_and_limited(self):
        self.assertEqual(self.scope['_carousel_hashtags'](
            ['#GSM', '#gsm', '#cotton', '#bio wash', '#knits', '#blanks', '#textile', '#overflow']),
            ['#gsm', '#cotton', '#knits', '#blanks', '#textile'])

    def test_missing_newest_images_do_not_hide_older_usable_article(self):
        generator = Mock(side_effect=[None, 'draft.json'])
        scope = load_functions('_generate_existing_article_carousel',
                               generate_ig_carousel_draft=generator,
                               BLOG_BASE_URL='https://www.bulkplaintshirt.com')
        articles = [
            {'slug': 'new-no-images', 'title': 'Knit loops', 'excerpt': 'Loop explanation', 'tags': ['knit']},
            {'slug': 'older-with-images', 'title': 'Cotton preparation', 'topic': 'Combing',
             'excerpt': 'Combing removes short fibres.', 'tags': ['combed']},
        ]
        self.assertEqual(scope['_generate_existing_article_carousel'](None, None, articles), 'draft.json')
        actual = generator.call_args.kwargs
        self.assertEqual(actual['blog_slug'], 'older-with-images')
        self.assertEqual(actual['topic'], 'Combing')
        self.assertEqual(actual['script_english'], 'Combing removes short fibres.')
        self.assertEqual(actual['tags'], ['combed'])

    def test_fallback_search_is_bounded_when_all_articles_fail(self):
        generator = Mock(return_value=None)
        scope = load_functions('_generate_existing_article_carousel',
                               generate_ig_carousel_draft=generator, BLOG_BASE_URL='https://example.com')
        articles = [{'slug': str(i), 'title': 'Knit loops', 'excerpt': 'Loops move.'} for i in range(12)]
        self.assertIsNone(scope['_generate_existing_article_carousel'](None, None, articles))
        self.assertEqual(generator.call_count, 5)

    def test_blog_prompt_does_not_force_length_or_copy_stale_business_numbers(self):
        scope = load_functions('get_blog_prompt', generate_blog_slug=lambda title: 'safe-title',
                               BLOG_BASE_URL='https://www.bulkplaintshirt.com',
                               _editorial_evidence=lambda topic: 'Reviewed facts')
        prompt = scope['get_blog_prompt']('Knit loops', 'Knit loops', 'Description',
                                         'Loops explain stretch.', [], 'Why stretch?', 'ABCDEFGHIJK')
        self.assertNotIn('2000+', prompt)
        self.assertNotIn('1,25,232', prompt)
        self.assertNotIn('Rs 2/pc', prompt)
        self.assertIn('first paragraph', prompt)
        self.assertIn('primary-source facts', prompt)

    def test_article_schema_does_not_invent_price_or_stock(self):
        scope = load_functions('inject_blog_seo')
        output = scope['inject_blog_seo'](
            '<!DOCTYPE html><html><head><title>Loops</title></head><body><h1>Loops</h1><p>Loops move.</p></body></html>',
            'Loops', 'Knitted loop structure', 'https://example.com/loops.html', '2026-09-23', 'loops')
        schemas = [json.loads(text) for text in re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', output, re.S)]
        types = [item['@type'] for item in schemas]
        self.assertIn('Article', types)
        self.assertIn('BreadcrumbList', types)
        self.assertNotIn('Product', types)
        self.assertNotIn('AggregateOffer', output)
        self.assertNotIn('schema.org/InStock', output)
        self.assertNotIn('real production data', output)
        self.assertNotIn('17+ years', output)
        self.assertNotIn('40K+', output)
        self.assertIn('sources cited in the article', output)
        self.assertIn('Illustrations are not photographs of product tests', output)


if __name__ == '__main__':
    unittest.main()
