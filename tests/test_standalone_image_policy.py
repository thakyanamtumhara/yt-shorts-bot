import ast
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PIL import Image
import pytz

from tools.standalone_image_policy import blog_image_metadata, standalone_image_urls


ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / 'daily_short.py').read_text())
BASE = 'https://www.bulkplaintshirt.com'
SLUG = 'pique-fabric-explained-why-polo-t-shirt-texture-is-a-knit'


def load_functions(*names, **extra):
    scope = {'json': json, 're': re, 'os': os, 'datetime': datetime, 'timedelta': timedelta,
             'pytz': pytz, 'TIMEZONE': 'Asia/Kolkata', 'BLOG_BASE_URL': BASE,
             'IG_DRAFT_MAX_AGE_DAYS': 4, **extra}
    nodes = [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'daily_short.py', 'exec'), scope)
    return scope


class ImagePolicyTest(unittest.TestCase):
    def test_cover_metadata_cannot_be_used_as_standalone_jpeg(self):
        metadata = blog_image_metadata([(b'cover', 'reel-cover.webp')], SLUG, BASE)
        self.assertEqual(metadata['image_assets'][0]['origin'], 'reel_cover')
        self.assertEqual(metadata['hero_image'], f'{BASE}/p/{SLUG}-reel-cover.webp')
        self.assertEqual(standalone_image_urls([metadata['hero_image'].replace('.webp', '.jpg')],
                                               metadata['image_assets']), [])

    def test_old_pique_hero_url_has_no_implicit_permission(self):
        old_url = f'{BASE}/p/{SLUG}-hero.jpg'
        self.assertEqual(standalone_image_urls([old_url], None), [])
        self.assertEqual(standalone_image_urls([old_url], []), [])
        self.assertEqual(standalone_image_urls([old_url], [{'url': old_url, 'origin': 'unknown'}]), [])

    def test_independent_images_and_approved_real_photos_survive(self):
        assets = blog_image_metadata([(b'a', 'hero.webp'), (b'b', 'img1.webp')], 'cotton', BASE)['image_assets']
        real = {'url': 'https://photos.example.com/actual-sample.jpg', 'origin': 'curated_photo', 'approved': True}
        urls = [a['url'].replace('.webp', '.jpg') for a in assets] + [real['url']]
        self.assertEqual(standalone_image_urls(urls, assets + [real]), urls)
        self.assertEqual(standalone_image_urls([real['url']], [{**real, 'approved': False}]), [])

    def test_relabelled_or_reencoded_cover_alias_is_still_denied(self):
        url = f'{BASE}/p/lesson-reel-cover.jpg'
        self.assertEqual(standalone_image_urls([url], [{'url': url, 'origin': 'independent_generation'}]), [])
        assets = blog_image_metadata([(b'identical', 'reel-cover.webp'), (b'identical', 'hero.webp')], 'lesson', BASE)['image_assets']
        self.assertEqual(standalone_image_urls([assets[1]['url'].replace('.webp', '.jpg')], assets), [])

    def test_mixed_batch_drops_cover_and_duplicate_slides_but_keeps_independent_image(self):
        assets = blog_image_metadata([(b'cover', 'reel-cover.webp'), (b'useful', 'hero.webp'),
                                     (b'useful', 'img1.webp')], 'lesson', BASE)['image_assets']
        urls = [a['url'].replace('.webp', '.jpg') for a in assets]
        self.assertEqual(standalone_image_urls(urls + [urls[1] + '?v=2'], assets), [urls[1]])

    def test_conflicting_origin_evidence_fails_closed(self):
        url = f'{BASE}/p/lesson-hero.jpg'
        self.assertEqual(standalone_image_urls([url], [{'url': url, 'origin': 'independent_generation'},
                                                     {'url': url, 'origin': 'reel_cover'}]), [])


class ImageRailRegressionTest(unittest.TestCase):
    def scope(self, folder):
        return load_functions('_blog_cover_fallback', 'save_blog_history', '_load_blog_history_active',
                              'generate_ig_carousel_draft', '_carousel_hashtags', 'publish_ig_carousel',
                              'post_latest_ig_carousel', '_tombstone_ig_draft',
                              BLOG_HISTORY_FILE=str(folder / 'blog_history.json'),
                              IG_CAROUSEL_DRAFTS_DIR=str(folder / 'drafts'),
                              _editorial_evidence=lambda topic: 'Reviewed fact.',
                              _auto_content_hold_reason=lambda value: None,
                              _review_derived_content=lambda *args: (True, 'Supported'),
                              _reachable_image_urls=Mock(side_effect=lambda urls: urls))

    def draft(self, scope, client, **extra):
        return scope['generate_ig_carousel_draft'](client, None, 'Pique', f'{BASE}/p/{SLUG}.html',
                                                  SLUG, 'Pique', 'Tuck stitches form the texture.', [], **extra)

    def test_actual_cover_fallback_through_history_holds_before_paid_caption(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp);scope = self.scope(folder)
            cover = folder / 'cover.png';Image.new('RGB', (800, 800), 'orange').save(cover)
            images = scope['_blog_cover_fallback'](cover)
            scope['save_blog_history']('Pique', 'Pique', SLUG, 'https://example.com', '', blog_images=images)
            record = json.loads((folder / 'blog_history.json').read_text())[0]
            self.assertEqual(record['image_assets'][0]['origin'], 'reel_cover')
            client = Mock()
            self.assertIsNone(self.draft(scope, client, uploaded_filenames=[name for _, name in images]))
            client.messages.create.assert_not_called()
            scope['_reachable_image_urls'].assert_not_called()
            self.assertEqual(list((folder / 'drafts').glob('*.json')), [])

    def test_independent_generated_assets_retain_provenance_in_saved_draft(self):
        with TemporaryDirectory() as tmp:
            folder=Path(tmp);scope=self.scope(folder)
            images=[(b'independent illustration', 'hero.webp')]
            scope['save_blog_history']('Pique', 'Pique', SLUG, 'https://example.com', '', blog_images=images)
            response=SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(text=json.dumps({'caption':'Pique describes construction.', 'hashtags':['#pique']}))])
            client=SimpleNamespace(messages=SimpleNamespace(create=Mock(return_value=response)))
            path=self.draft(scope, client, uploaded_filenames=['hero.webp'])
            self.assertIsNotNone(path)
            draft=json.loads(Path(path).read_text())
            self.assertEqual(draft['image_assets'][0]['origin'], 'independent_generation')
            self.assertEqual(draft['image_urls'], [f'{BASE}/p/{SLUG}-hero.jpg'])
            self.assertFalse(draft['posted'])

    def test_legacy_fallback_cannot_guess_reachable_images(self):
        with TemporaryDirectory() as tmp:
            scope=self.scope(Path(tmp));client=Mock()
            self.assertIsNone(self.draft(scope,client))
            scope['_reachable_image_urls'].assert_not_called()
            client.messages.create.assert_not_called()

    def test_publish_entrypoint_blocks_old_or_cover_draft_before_graph_api(self):
        with TemporaryDirectory() as tmp:
            scope=self.scope(Path(tmp));scope['requests']=Mock()
            for assets in (None, [{'url':f'{BASE}/p/{SLUG}-hero.webp','origin':'reel_cover'}]):
                self.assertIsNone(scope['publish_ig_carousel']([f'{BASE}/p/{SLUG}-hero.jpg'],
                                  'Pique describes construction.', image_assets=assets))
            scope['requests'].post.assert_not_called()

    def test_queue_holds_old_cover_without_reporting_it_published(self):
        with TemporaryDirectory() as tmp:
            folder=Path(tmp);scope=self.scope(folder);drafts=folder/'drafts';drafts.mkdir()
            day=datetime.now(pytz.timezone('Asia/Kolkata')).date()
            path=drafts/(day.isoformat()+'.json')
            path.write_text(json.dumps({'posted':False,'media_id':None,'image_urls':[f'{BASE}/p/{SLUG}-hero.jpg']}))
            scope['publish_ig_carousel']=Mock(return_value='must-not-publish')
            self.assertTrue(scope['post_latest_ig_carousel']())
            actual=json.loads(path.read_text())
            self.assertFalse(actual['posted']);self.assertTrue(actual['held']);self.assertIsNone(actual['media_id'])
            scope['publish_ig_carousel'].assert_not_called()
            scope['_reachable_image_urls'].assert_not_called()

    def test_held_newest_draft_does_not_block_older_independent_post(self):
        with TemporaryDirectory() as tmp:
            folder=Path(tmp);scope=self.scope(folder);drafts=folder/'drafts';drafts.mkdir()
            day=datetime.now(pytz.timezone('Asia/Kolkata')).date()
            (drafts/(day.isoformat()+'.json')).write_text(json.dumps({'posted':False,'image_urls':[f'{BASE}/p/{SLUG}-hero.jpg']}))
            assets=blog_image_metadata([(b'new visual','hero.webp')],'independent',BASE)['image_assets']
            path=drafts/((day-timedelta(days=1)).isoformat()+'.json')
            path.write_text(json.dumps({'posted':False,'image_urls':[assets[0]['url'].replace('.webp','.jpg')],
                                       'image_assets':assets,'caption':'Useful explanation.'}))
            scope['publish_ig_carousel']=Mock(return_value='published-independent')
            self.assertTrue(scope['post_latest_ig_carousel']())
            scope['publish_ig_carousel'].assert_called_once()
            self.assertEqual(scope['publish_ig_carousel'].call_args.kwargs['image_assets'],assets)
            self.assertTrue(json.loads(path.read_text())['posted'])


if __name__ == '__main__':
    unittest.main()
