import ast
from copy import deepcopy
from datetime import datetime
from email.utils import parsedate_to_datetime
import io
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open
import xml.etree.ElementTree as ET

import pytz


ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / 'daily_short.py').read_text())
BASE = 'https://www.bulkplaintshirt.com'
POSTS = [
    {'slug': 'corrected-guide', 'title': 'Corrected cotton guide', 'topic': 'Cotton preparation',
     'date': '2026-09-22T09:00:00+05:30', 'modified': '2026-09-23T21:00:00+05:30',
     'hero_image': None, 'vid_url': '', 'description': 'A sourced buying guide.',
     'editorial_corrected_at': '2026-09-23T21:00:00+05:30'},
    {'slug': 'legacy-guide', 'title': 'Legacy guide', 'date': '2026-09-20T09:00:00+05:30'},
    {'slug': 'custom-guide', 'title': 'Custom guide', 'date': '2026-09-01T09:00:00+05:30',
     'modified': '2026-09-24T09:00:00+05:30',
     'hero_image': 'https://images.example.com/approved.jpg?size=800&view=front'},
]


def load_functions(*names, posts=None, **extra):
    scope = {'json': json, 're': re, 'os': os, 'datetime': datetime, 'pytz': pytz,
             'TIMEZONE': 'Asia/Kolkata', 'BLOG_BASE_URL': BASE,
             'BLOG_HISTORY_FILE': '/__missing_blog_history_fixture__.json',
             '_load_blog_history_active': lambda: deepcopy(POSTS if posts is None else posts),
             **extra}
    names = set(names) | {'_blog_hero_url', '_blog_lastmod'}
    selected = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=selected, type_ignores=[]), 'daily_short.py', 'exec'), scope)
    return scope


class HistoryPresentationTest(unittest.TestCase):
    def setUp(self):
        self.scope = load_functions('build_blog_index_html', 'build_rss_feed',
                                    'build_blog_widget_html', 'build_sitemap_xml')

    def test_withdrawn_photos_disappear_from_all_discovery_surfaces(self):
        index = self.scope['build_blog_index_html']()
        widget = self.scope['build_blog_widget_html']()
        feed = self.scope['build_rss_feed']()
        for output in (index, widget, feed):
            self.assertNotIn('corrected-guide-hero.webp', output)
            self.assertIn('legacy-guide-hero.webp', output)
            self.assertIn('approved.jpg?size=800&amp;view=front', output)
        self.assertIn(f'<meta property="og:image" content="{BASE}/catalog/img/logo.png">', index)
        card = re.search(r'<article\b.*?</article>', index, re.S).group(0)
        self.assertIn('card-img no-img', card)
        self.assertNotIn('<img ', card)
        self.assertIn('Corrected cotton guide', card)
        self.assertIn('/p/corrected-guide.html?v=2026-09-23', card)
        self.assertIn('/p/corrected-guide.html?v=2026-09-23', widget)
        items = ET.fromstring(feed).findall('./channel/item')
        self.assertEqual(items[0].findtext('link'), f'{BASE}/p/corrected-guide.html?v=2026-09-23')
        self.assertEqual(items[0].findtext('guid'), f'{BASE}/p/corrected-guide.html')
        self.assertIsNone(items[0].find('enclosure'))
        self.assertEqual(items[1].find('enclosure').attrib['url'], f'{BASE}/p/legacy-guide-hero.webp')
        self.assertEqual(items[2].find('enclosure').attrib,
                         {'url': POSTS[2]['hero_image'], 'type': 'image/jpeg'})

    def test_correction_changes_lastmod_but_not_publication_or_order(self):
        xml = ET.fromstring(self.scope['build_sitemap_xml']())
        ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        entries = {entry.find('sm:loc', ns).text: entry for entry in xml.findall('sm:url', ns)}
        self.assertEqual(entries[f'{BASE}/p/corrected-guide.html'].find('sm:lastmod', ns).text,
                         '2026-09-23')
        self.assertEqual(entries[f'{BASE}/p/legacy-guide.html'].find('sm:lastmod', ns).text,
                         '2026-09-20')
        items = ET.fromstring(self.scope['build_rss_feed']()).findall('./channel/item')
        self.assertEqual([item.findtext('title') for item in items], [p['title'] for p in POSTS])
        self.assertEqual(parsedate_to_datetime(items[0].findtext('pubDate')).isoformat(), POSTS[0]['date'])
        self.assertIn('Sep 22, 2026', self.scope['build_blog_widget_html']())

    def test_bad_correction_date_uses_original_and_unknown_date_is_not_fabricated(self):
        scope = load_functions('build_sitemap_xml', posts=[
            {'slug': 'bad-modified', 'date': '2026-08-01', 'modified': 'not-a-date'},
            {'slug': 'unknown-date', 'date': 'unknown', 'modified': None},
        ])
        xml = ET.fromstring(scope['build_sitemap_xml']())
        ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        entries = {entry.find('sm:loc', ns).text: entry for entry in xml.findall('sm:url', ns)}
        self.assertEqual(entries[f'{BASE}/p/bad-modified.html'].find('sm:lastmod', ns).text, '2026-08-01')
        self.assertIsNone(entries[f'{BASE}/p/unknown-date.html'].find('sm:lastmod', ns))

    def test_related_cards_keep_explicit_no_image_and_legacy_fallback(self):
        scope = load_functions('get_blog_prompt', generate_blog_slug=lambda title: 'new-guide',
                               _editorial_evidence=lambda topic: 'Reviewed cotton facts.')
        prompt = scope['get_blog_prompt']('Cotton', 'Cotton', 'Buying guide', 'Combing removes short fibres.',
                                          [], 'Cotton choice?', None, related_posts=POSTS)
        self.assertIn('corrected-guide.html (text-only card; no image is approved)', prompt)
        self.assertNotIn('corrected-guide-hero.webp', prompt)
        self.assertIn(f'hero image: {BASE}/p/legacy-guide-hero.webp', prompt)
        self.assertIn(POSTS[2]['hero_image'], prompt)

    def test_withdrawn_image_article_is_not_reused_for_carousel(self):
        generator = Mock(return_value='legacy-draft.json')
        scope = load_functions('_generate_existing_article_carousel', generate_ig_carousel_draft=generator)
        self.assertEqual(scope['_generate_existing_article_carousel'](None, None, POSTS), 'legacy-draft.json')
        generator.assert_called_once()
        self.assertEqual(generator.call_args.kwargs['blog_slug'], 'legacy-guide')

    def test_hero_override_accepts_site_path_and_never_restores_null_or_unsafe_image(self):
        helper = self.scope['_blog_hero_url']
        self.assertEqual(helper({'slug': 'old'}), f'{BASE}/p/old-hero.webp')
        self.assertEqual(helper({'slug': 'new', 'hero_image': '/p/approved.webp'}), f'{BASE}/p/approved.webp')
        for value in (None, '', '  ', False, '//unapproved.example/image.jpg', 'javascript:alert(1)'):
            self.assertIsNone(helper({'slug': 'withdrawn', 'hero_image': value}))


class CuratedRepairTest(unittest.TestCase):
    def repair(self, records, html=''):
        s3 = Mock()
        s3.get_object.return_value = {'Body': io.BytesIO(html.encode())}
        cloudfront = Mock()
        scope = load_functions('repair_existing_blog_posts', BLOG_S3_BUCKET='test-bucket',
                               os=SimpleNamespace(path=SimpleNamespace(exists=lambda _: True)),
                               open=mock_open(read_data=json.dumps(records)),
                               inject_blog_seo=Mock())
        scope['repair_existing_blog_posts'](s3, cloudfront)
        return s3, cloudfront, scope

    def test_curated_article_is_not_downloaded_or_backfilled(self):
        record = {**POSTS[0], 'vid_url': 'https://youtube.com/watch?v=ABCDEFGHIJK'}
        s3, cloudfront, scope = self.repair([record])
        s3.get_object.assert_not_called()
        s3.put_object.assert_not_called()
        scope['inject_blog_seo'].assert_not_called()
        cloudfront.create_invalidation.assert_not_called()

    def test_null_video_does_not_crash_legacy_repair(self):
        record = {**POSTS[1], 'vid_url': None}
        html = (f'<html><head><link rel="canonical" href="{BASE}/p/legacy-guide.html">'
                '<script type="application/ld+json">{}</script></head>'
                '<body><h1>Guide</h1><div class="bpt-byline">Original date</div></body></html>')
        s3, cloudfront, scope = self.repair([record], html)
        s3.get_object.assert_called_once()
        s3.put_object.assert_not_called()
        scope['inject_blog_seo'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
