import copy
from datetime import date
from pathlib import Path
import unittest

from tools.daily_topic_selection import (
    TopicHold, active_campaign, brainstorming_context, campaign_clips, campaign_prompt, choose_with_campaign,
    cites_campaign, load_bank, validate_brief,
)

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'assets' / 'dtf_demo'
BANK = load_bank()


def with_window(start, until):
    bank = copy.deepcopy(BANK)
    bank['campaign'] = {**bank['campaign'], 'from': start, 'until': until}
    return bank


class DailyCampaignTests(unittest.TestCase):
    def test_window_is_inclusive_and_dated_in_india_time(self):
        bank = with_window('2026-10-03', '2026-10-14')
        self.assertIsNotNone(active_campaign(bank, date(2026, 10, 3)))
        self.assertIsNotNone(active_campaign(bank, date(2026, 10, 14)))
        self.assertIsNone(active_campaign(bank, date(2026, 10, 2)))
        self.assertIsNone(active_campaign(bank, date(2026, 10, 15)))

    def test_unknown_fact_ids_are_ignored_and_an_empty_campaign_is_off(self):
        bank = with_window('2000-01-01', '2999-12-31')
        bank['campaign']['fact_ids'] = ['not_a_fact']
        self.assertIsNone(active_campaign(bank))
        bank['campaign']['fact_ids'] = ['not_a_fact', 'dtf_press_settings']
        self.assertEqual(active_campaign(bank)['fact_ids'], ['dtf_press_settings'])
        self.assertIsNone(active_campaign({'facts': {}}))

    def test_every_campaign_lesson_validates_and_gets_one_recording_of_its_own_topic(self):
        campaign = active_campaign(with_window('2000-01-01', '2999-12-31'))
        lessons = {seed['intent_key']: seed for seed in BANK['seed_lessons'] if cites_campaign(seed, campaign)}
        expected = {'dtf_canva_png_3125': 'canva', 'dtf_dpi_label_not_pixels': 'resolution',
                    'dtf_transparent_background': 'transparent', 'dtf_gang_sheet_layout': 'gang',
                    'dtf_pieces_means_sheets': 'pieces', 'dtf_press_165_180': 'press'}
        self.assertEqual(set(expected), set(lessons))
        for key, seed in lessons.items():
            self.assertTrue(validate_brief(seed, BANK)['evidence'])
            clips = campaign_clips(seed, campaign, ASSETS)
            self.assertEqual([Path(path).stem for path in clips], [expected[key]], key)
            self.assertTrue(Path(clips[0]).is_file())
        textile = next(seed for seed in BANK['seed_lessons'] if not cites_campaign(seed, campaign))
        self.assertEqual(campaign_clips(textile, campaign, ASSETS), [])

    def test_the_broad_launch_fact_yields_to_a_specific_one(self):
        campaign = active_campaign(with_window('2000-01-01', '2999-12-31'))
        stem = lambda ids: [Path(p).stem for p in campaign_clips({'fact_ids': ids}, campaign, ASSETS)]
        self.assertEqual(stem(['dtf_service_launch']), ['launch'])
        self.assertEqual(stem(['dtf_service_launch', 'dtf_transparent_background']), ['transparent'])
        self.assertEqual(stem(['dtf_min_resolution', 'dtf_canva_png_size']), ['resolution'])
        self.assertEqual(stem(['dtf_canva_png_size', 'dtf_min_resolution']), ['canva'])

    def test_every_recording_is_a_full_screen_clip_long_enough_to_play_once(self):
        import json
        import subprocess
        campaign = BANK['campaign']
        names = {name for names in campaign['clips'].values() for name in names}
        self.assertEqual(names, {p.stem for p in ASSETS.glob('*.mp4')})
        for name in names:
            probe = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height:format=duration',
                                               '-of', 'json', str(ASSETS / f'{name}.mp4')], check=True, capture_output=True, text=True).stdout)
            self.assertEqual((probe['streams'][0]['width'], probe['streams'][0]['height']), (1080, 1920), name)
            self.assertGreaterEqual(float(probe['format']['duration']), 25, name)

    def test_missing_files_and_odd_names_never_become_clips(self):
        campaign = copy.deepcopy(active_campaign(with_window('2000-01-01', '2999-12-31')))
        campaign['clips'] = {'dtf_press_settings': ['../secret', 'missing', 'press']}
        seed = next(s for s in BANK['seed_lessons'] if s['fact_ids'] == ['dtf_press_settings'])
        self.assertEqual([Path(p).name for p in campaign_clips(seed, campaign, ASSETS)], ['press.mp4'])

    def test_campaign_lessons_are_chosen_first_and_the_ordinary_choice_is_the_fallback(self):
        bank = with_window('2000-01-01', '2999-12-31')
        campaign = active_campaign(bank)
        dtf = next(s for s in bank['seed_lessons'] if cites_campaign(s, campaign))
        textile = next(s for s in bank['seed_lessons'] if not cites_campaign(s, campaign))
        scores = {dtf['intent_key']: 26, textile['intent_key']: 39}
        chosen = choose_with_campaign([textile, dtf], bank=bank, history=(), viable=lambda _: True,
                                      review=lambda brief: (scores[brief['intent_key']], 'ok'))
        self.assertEqual(chosen.brief['intent_key'], dtf['intent_key'])
        scores[dtf['intent_key']] = 10
        chosen = choose_with_campaign([textile, dtf], bank=bank, history=(), viable=lambda _: True,
                                      review=lambda brief: (scores[brief['intent_key']], 'ok'))
        self.assertEqual(chosen.brief['intent_key'], textile['intent_key'])
        outside = with_window('2000-01-01', '2000-01-02')
        chosen = choose_with_campaign([dtf, textile], bank=outside, history=(), viable=lambda _: True,
                                      review=lambda brief: ({dtf['intent_key']: 30, textile['intent_key']: 35}[brief['intent_key']], 'ok'))
        self.assertEqual(chosen.brief['intent_key'], textile['intent_key'])
        with self.assertRaises(TopicHold):
            choose_with_campaign([dtf, textile], bank=bank, history=(), viable=lambda _: True, review=lambda brief: (5, 'weak'))

    def test_brainstorm_puts_campaign_facts_first_and_says_no_spoken_sales_line(self):
        bank = with_window('2000-01-01', '2999-12-31')
        context = brainstorming_context(bank, [])
        campaign = context['campaign']
        self.assertEqual(context['preferred_fact_ids'][:len(campaign['fact_ids'])], campaign['fact_ids'])
        text = campaign_prompt(campaign)
        self.assertIn('no sales line and no website name', text)
        for key in campaign['fact_ids']:
            self.assertIn(key, text)
        self.assertEqual(campaign_prompt(None), '')
        self.assertIsNone(brainstorming_context(with_window('2000-01-01', '2000-01-02'), [])['campaign'])

    def test_campaign_text_is_only_for_campaign_lessons(self):
        from tools.daily_topic_selection import SelectedTopic, campaign_text
        seed = next(s for s in BANK['seed_lessons'] if s['fact_ids'] == ['dtf_press_settings'])
        inside, outside = date(2026, 10, 5), date(2026, 10, 20)
        self.assertIn('Sale91.com', campaign_text(SelectedTopic(seed), 'description_rule', inside))
        self.assertEqual(campaign_text(SelectedTopic(seed), 'description_rule', outside), '')
        self.assertEqual(campaign_text('Printing samples', 'description_rule', inside), '')

    def test_launch_texts_are_short_and_carry_the_dtf_site(self):
        campaign = BANK['campaign']
        self.assertEqual(campaign['outro']['cta'], 'dtf.bulkplaintshirt.com')
        rule = campaign['description_rule']
        self.assertIn('never write that DTF sheets can be ordered on Sale91.com', rule)
        self.assertNotIn('"', rule)
        self.assertNotIn('MOQ', ' '.join(campaign['outro'].values()))
        self.assertLessEqual(len(campaign['cta_text']), 44)
        for key in ('cta_text', 'link', 'ig_line'):
            self.assertIn('dtf.bulkplaintshirt.com', campaign[key])
        self.assertTrue(campaign['link'].startswith('https://'))
        self.assertTrue(all(ord(c) < 128 for c in campaign['cta_text']))



class RealScreenLayoutSourceTests(unittest.TestCase):
    source = (ROOT / 'daily_short.py').read_text()

    def test_one_recording_is_fitted_to_the_narration_never_looped(self):
        block = self.source[self.source.index('from tools.daily_visual_timeline import assemble_visual_timeline, fit_screen_recording'):]
        block = block[:block.index('# ── 8. Overlays ──')]
        self.assertIn('if real_clips:', block)
        self.assertLess(block.index('fit_screen_recording(video_objects[0], total_duration)'), block.index('assemble_visual_timeline(video_objects'))

    def test_hook_cta_and_end_card_have_real_screen_branches(self):
        self.assertIn('if ADD_HOOK_TEXT and real_clips:', self.source)
        self.assertIn('if ADD_CTA_OVERLAY and real_clips:', self.source)
        self.assertIn('campaign_outro = _campaign.get("outro")', self.source)
        self.assertIn('real_scene=bool(real_clips)', self.source)
        captions = self.source[self.source.index('for wi, w in enumerate(line):'):]
        captions = captions[:captions.index('k_clips.append(ic)')]
        self.assertIn('w_start = max(w_start, HOOK_DURATION)', captions)
        fallback = self.source[self.source.index('# Fallback — old segment-level English captions'):]
        self.assertLess(fallback.index('and real_clips:'), fallback.index('TextClip('))


class CampaignDescriptionTests(unittest.TestCase):
    def load(self):
        import ast
        path = ROOT / 'daily_short.py'
        tree = ast.parse(path.read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'campaign_safe_description']
        lines = {'on': True}
        import re
        state = {'re': re, 'campaign_line': lambda topic, key: 'https://dtf.bulkplaintshirt.com/' if lines['on'] else ''}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), state)
        return state['campaign_safe_description'], lines

    def test_launch_lesson_description_never_sends_dtf_buyers_to_sale91(self):
        clean, lines = self.load()
        text = 'Why is your Canva DTF sheet blurry?\n\nOrder DTF sheets & plain t-shirts: Sale91.com\n\n#dtfprinting'
        self.assertEqual(clean('t', text), 'Why is your Canva DTF sheet blurry?\n\n#dtfprinting')
        lines['on'] = False
        self.assertEqual(clean('t', text), text)

    def test_the_description_and_captions_use_the_dtf_rules(self):
        source = (ROOT / 'daily_short.py').read_text()
        self.assertIn('yt_description = campaign_safe_description(fresh_topic, data["description"])', source)
        self.assertIn('campaign_text(topic, "description_rule") or "Include Sale91.com link."', source)
        self.assertIn('(_launch or "📦 Order: Sale91.com")', source)


class TestModeNeverPublishesTests(unittest.TestCase):
    def test_new_test_mode_skips_the_facebook_reel_and_the_telegram_post(self):
        source = (ROOT / 'daily_short.py').read_text()
        block = source[source.index('# ── 10d2. Cross-post to Facebook Reels + Telegram'):source.index('# ── 10e. Generate & Publish SEO Blog Post ──')]
        guard = block.index('if not TEST_MODE and NEW_TEST_MODE:')
        self.assertLess(guard, block.index('publish_fb_reel('))
        self.assertLess(guard, block.index('post_telegram_channel('))
        self.assertIn('elif not TEST_MODE:', block[guard:block.index('publish_fb_reel(')])


if __name__ == '__main__':
    unittest.main()
