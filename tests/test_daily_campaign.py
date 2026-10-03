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

    def test_every_campaign_lesson_validates_and_has_only_its_own_real_clips(self):
        campaign = active_campaign(with_window('2000-01-01', '2999-12-31'))
        lessons = [seed for seed in BANK['seed_lessons'] if cites_campaign(seed, campaign)]
        self.assertGreaterEqual(len(lessons), 6)
        for seed in lessons:
            self.assertTrue(validate_brief(seed, BANK)['evidence'])
            clips = campaign_clips(seed, campaign, ASSETS)
            self.assertTrue(clips, seed['intent_key'])
            mapped = {name for key in seed['fact_ids'] for name in campaign['clips'].get(key, [])}
            self.assertTrue(all(Path(path).stem in mapped for path in clips), (seed['intent_key'], clips))
            self.assertTrue(all(Path(path).is_file() for path in clips))
        textile = next(seed for seed in BANK['seed_lessons'] if not cites_campaign(seed, campaign))
        self.assertEqual(campaign_clips(textile, campaign, ASSETS), [])

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

    def test_launch_texts_are_short_and_carry_the_dtf_site(self):
        campaign = BANK['campaign']
        self.assertLessEqual(len(campaign['cta_text']), 44)
        for key in ('cta_text', 'link', 'ig_line'):
            self.assertIn('dtf.bulkplaintshirt.com', campaign[key])
        self.assertTrue(campaign['link'].startswith('https://'))
        self.assertTrue(all(ord(c) < 128 for c in campaign['cta_text']))


if __name__ == '__main__':
    unittest.main()
