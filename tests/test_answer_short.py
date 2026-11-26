from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from tools import content_watch, urgent_actions
from tools.answer_short import demand, facts, write

IST = timezone(timedelta(hours=5, minutes=30))
RATE = {'claim': 'Oversize 240gsm. 10 or more pieces: ₹205. Fewer than 10 pieces: ₹245.', 'limits': 'exact',
        'source_url': 'https://www.bulkplaintshirt.com/', 'source_title': 'rates', 'checked_on': '2026-11-25',
        'amounts': [205, 245]}
SHEET = {
    'today': '2026-11-25',
    'facts': {'rate_oversize_240gsm': RATE,
              'order_from_one_piece': {'claim': 'One piece can be ordered as a sample.', 'limits': 'no free sample'}},
    'cards': {'rate_oversize_240gsm': {'product': 'Oversize 240gsm', 'tiers': [{'sizes': 'XS – XXL', 'rate': 205}],
                                       'sample': 245, 'bulk_from': 10, 'detail': ''}},
    'visuals': {'rate_oversize_240gsm': 'oversize-240gsm'},
    'gallery': {'oversize-240gsm': {'videos': ['https://x/a.mp4'], 'posters': [''], 'photos': []}},
}
QUESTIONS = [{'q': '240 gsm oversized t shirt wholesale', 'yt': 1, 'google': 1, 'rank': 0}]


def good_plan():
    says = ['240 GSM oversized T-shirt ka wholesale rate kya hai?',
            'Hamari website pe aaj ka rate ₹205 per piece hai.',
            'Ye bulk rate hai, 10 piece ya usse zyada pe lagta hai.',
            '10 se kam piece pe sample rate ₹245 lagta hai.',
            'Fabric 100% cotton biowash hai, size XS se XXL tak.',
            'GST 5% aur delivery alag hai, pehle 1 piece sample mangwa ke dekh lo.',
            'Aap kaunsa GSM lete ho, 180 ya 240?']
    return {'search_phrase': '240 gsm oversized t shirt wholesale', 'also_matches': [], 'intent_key': 'os240_rate',
            'fact_ids': ['rate_oversize_240gsm', 'order_from_one_piece'], 'hook_text': '240 GSM oversized ka rate?',
            'cover': ['240 GSM OVERSIZED', 'Wholesale rate kya hai?'],
            'lines': [{'say': say, 'show': 'rate_oversize_240gsm', 'card': 'rate_oversize_240gsm' if index == 1 else ''}
                      for index, say in enumerate(says)],
            'comment_question': 'Aap kaunsa GSM lete ho, 180 ya 240?', 'title_youtube': '240 GSM Oversized T-Shirt Wholesale Rate',
            'title_instagram': '240 GSM oversized ka wholesale rate?', 'description': 'Today the bulk rate is on our website.',
            'tags': ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']}


class Demand(unittest.TestCase):
    def test_foreign_and_unanswerable_searches_are_left_out(self):
        answers = {'yt': ['plain t shirt wholesale in delhi', 'plain t shirt wholesale price in bangladesh',
                          'dtf printing machine price', 'oversized t shirt wholesale tamil'], 'google': []}
        ranked, failures = demand.collect(seeds=['x'], fetch=lambda query, source: answers[source], pause=0)
        self.assertEqual([entry['q'] for entry in ranked], ['plain t shirt wholesale in delhi'])
        self.assertEqual(failures, 0)

    def test_a_failed_read_never_replaces_the_saved_list(self):
        def broken(query, source):
            raise OSError('no network')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bank.json'
            path.write_text('kept')
            with self.assertRaises(RuntimeError):
                demand.refresh(path=path, fetch=broken, pause=0)
            self.assertEqual(path.read_text(), 'kept')


class Facts(unittest.TestCase):
    def test_gallery_folder_of_each_kind_of_product_name(self):
        folders = {'oversize-240gsm', 'hoodie-320gsm', 'hoodie-430gsm', 'acidwash-os', 'true-bio-rneck-sale',
                   'true-bio-rneck', 'sweatshirt', 'cotton-polo-sale'}
        for product, folder in (('Oversize 240gsm', 'oversize-240gsm'), ('Hoodie 320gsm-2', 'hoodie-320gsm'),
                                ('Dropsho Hoodie 430gsm', 'hoodie-430gsm'), ('AcidWash OS', 'acidwash-os'),
                                ('Sale: True Bio Rneck', 'true-bio-rneck-sale'), ('Sweatshirt-2', 'sweatshirt'),
                                ('Sale: Polo Tshirt', 'cotton-polo-sale'), ('Kids Rneck', None)):
            self.assertEqual(facts.folder_for(product, folders), folder, product)

    def test_gallery_never_offers_a_closed_product(self):
        text = ('window.PH_PHOTOS={"varsity-jacket":[{"u":"/ph/v/1.webp","c":"Black"}],"sweatshirt":[{"u":"/ph/s/1.webp","c":"Grey"}]};'
                'window.PH_VIDEOS={"sweatshirt":[{"src":"/ph/vid/s.mp4","poster":"/ph/vid/s.jpg"}]};')
        found = facts.gallery(text)
        self.assertEqual(list(found), ['sweatshirt'])
        self.assertEqual(found['sweatshirt']['videos'], ['https://www.bulkplaintshirt.com/ph/vid/s.mp4'])

    def test_allowed_amounts_are_rates_differences_and_round_quantities(self):
        sheet = {'rate_a': {'amounts': [205, 245]}, 'rate_b': {'amounts': [150]}, 'carton_discount': {'amounts': [4]},
                 'dtf_rate': {'amounts': [130]}}
        allowed = facts.allowed_amounts(sheet, ['rate_a', 'rate_b', 'carton_discount', 'dtf_rate'])
        for value in (205, 245, 150, 4, 130, 55, 40, 95, 2050, 10250, 1500, 260, 1300):
            self.assertIn(value, allowed)
        for value in (200, 199, 499, 210, 129):
            self.assertNotIn(value, allowed)

    def test_dtf_price_is_read_exactly_or_not_at_all(self):
        fact = facts.dtf_fact('2026-11-25', json.dumps({'pricePerMetrePaise': 13000, 'gstBp': 1800, 'acceptingOrders': True}))
        self.assertEqual(fact['dtf_rate']['amounts'], [130])
        self.assertIn('₹130 per metre', fact['dtf_rate']['claim'])
        for bad in ({'pricePerMetrePaise': 13050, 'gstBp': 1800, 'acceptingOrders': True},
                    {'pricePerMetrePaise': 13000, 'gstBp': 1800, 'acceptingOrders': False}):
            with self.assertRaises(ValueError):
                facts.dtf_fact('2026-11-25', json.dumps(bad))


class Pictures(unittest.TestCase):
    def sheet(self):
        photos = [{'u': f'https://x/ph/os/{n}.webp', 'c': colour} for n, colour in
                  enumerate(['Black', 'Black', 'Black', 'White', 'White', 'White', 'Navy', 'Navy'], 1)]
        return {'visuals': {'rate_os': 'os'},
                'gallery': {'os': {'videos': ['https://x/ph/vid/a.mp4', 'https://x/ph/vid/b.mp4'], 'photos': photos}}}

    def plan(self, lines=4):
        return {'lines': [{'say': 'x', 'show': 'rate_os', 'card': ''} for _ in range(lines)]}

    def test_a_picture_not_shown_lately_goes_first(self):
        from tools.answer_short.render import plan_pictures
        spans = [(index * 4.0, index * 4.0 + 4.0) for index in range(4)]
        first = [shot['key'] for shot in plan_pictures(self.plan(), self.sheet(), spans)]
        self.assertEqual(first, ['os/a.mp4', 'os/b.mp4', 'os/1.webp', 'os/a.mp4'])
        again = [shot['key'] for shot in plan_pictures(self.plan(), self.sheet(), spans, recent=['os/a.mp4', 'os/1.webp'])]
        self.assertEqual(again[:3], ['os/b.mp4', 'os/a.mp4', 'os/4.webp'])

    def test_photos_take_turns_by_colour_and_a_clip_plays_on(self):
        from tools.answer_short.render import by_colour_turn, plan_pictures
        order = [url.rsplit('/', 1)[-1] for url in by_colour_turn(self.sheet()['gallery']['os']['photos'])]
        self.assertEqual(order[:4], ['1.webp', '4.webp', '7.webp', '2.webp'])
        spans = [(index * 4.0, index * 4.0 + 4.0) for index in range(4)]
        shots = plan_pictures(self.plan(), self.sheet(), spans)
        self.assertEqual((shots[0]['from'], shots[3]['from']), (0.0, 4.0))

    def test_a_product_name_is_never_split_over_two_caption_lines(self):
        from tools.answer_short.render import caption_lines
        said = 'par 88% cotton, 12% polyester Non Bio ₹107 per piece hai. supercombed True Bio Round neck ₹158 per piece hai.'
        words = [{'raw': token, 'text': token.strip('.,')} for token in said.split()]
        lines = [' '.join(word['text'] for word in line) for line in caption_lines(words)]
        self.assertEqual(lines, ['par 88% cotton', '12% polyester', 'Non Bio ₹107', 'per piece hai',
                                 'supercombed True Bio', 'Round neck ₹158', 'per piece hai'])
        self.assertTrue(all(len(line.split()) <= 3 for line in lines))


class Plan(unittest.TestCase):
    def test_a_sound_plan_passes(self):
        self.assertEqual(write.code_problems(good_plan(), SHEET, QUESTIONS, []), [])

    def problems(self, change):
        plan = good_plan()
        change(plan)
        return ' | '.join(write.code_problems(plan, SHEET, QUESTIONS, []))

    def test_a_rupee_amount_outside_the_cited_facts_is_refused(self):
        def change(plan):
            plan['lines'][1]['say'] = 'Hamari website pe aaj ka rate ₹199 per piece hai.'
        self.assertIn('rupee amounts not in the cited facts: [199]', self.problems(change))

    def test_a_rate_in_words_is_refused(self):
        def change(plan):
            plan['lines'][3]['say'] = '10 se kam piece pe sample rate do sau rupaye lagta hai.'
        self.assertIn('digits with the ₹ sign', self.problems(change))

    def test_hype_and_closed_products_are_refused(self):
        def change(plan):
            plan['lines'][4]['say'] = 'Ye sabse sasta hai aur quality ki guarantee hai.'
        found = self.problems(change)
        self.assertIn('guarantee', found)
        self.assertIn('sabse sast', found)

    def test_the_question_must_come_from_the_searched_list(self):
        def change(plan):
            plan['search_phrase'] = 'something nobody searched'
        self.assertIn('copied exactly from SEARCHED QUESTIONS', self.problems(change))

    def test_an_answered_question_is_not_answered_again(self):
        found = write.code_problems(good_plan(), SHEET, QUESTIONS, [{'intent_key': 'os240_rate'}])
        self.assertTrue(any('already answered' in item for item in found))
        left = write.open_questions({'queries': QUESTIONS}, [{'search_phrase': '240 gsm oversized t shirt wholesale'}])
        self.assertEqual(left, [])

    def test_every_line_needs_a_real_picture_and_one_sentence(self):
        def change(plan):
            plan['lines'][2]['show'] = 'rate_unknown'
            plan['lines'][4]['say'] = 'Fabric cotton hai. Size XS se XXL tak.'
        found = self.problems(change)
        self.assertIn('line 3 "show" must be a fact id from REAL PICTURES', found)
        self.assertIn('line 5 holds more than one sentence', found)

    def test_a_web_address_is_never_spoken(self):
        def change(plan):
            plan['lines'][1]['say'] = 'sale91.com pe aaj ka rate ₹205 per piece hai.'
        self.assertIn('never say a web address', self.problems(change))


class Watch(unittest.TestCase):
    def folder(self, last='2026-11-13', runs=()):
        root = Path(tempfile.mkdtemp())
        (root / 'lesson_history.json').write_text(json.dumps([{'published_on': last}]))
        (root / 'ig_engagement_history.json').write_text('[]')
        (root / 'daily_short_status.json').write_text(json.dumps({'format': 'daily-short-status-v1', 'runs': list(runs)}))
        return root

    def test_sundays_and_a_day_still_running_are_not_missed_days(self):
        friday = date(2026, 11, 20)
        self.assertEqual(content_watch.missed_working_days(friday, datetime(2026, 11, 21, 12, 0, tzinfo=IST)), 0)
        self.assertEqual(content_watch.missed_working_days(friday, datetime(2026, 11, 21, 18, 0, tzinfo=IST)), 1)
        self.assertEqual(content_watch.missed_working_days(friday, datetime(2026, 11, 23, 9, 0, tzinfo=IST)), 1)
        self.assertEqual(content_watch.missed_working_days(friday, datetime(2026, 11, 23, 18, 0, tzinfo=IST)), 2)

    def test_one_missed_day_is_quiet_two_go_on_the_bar_with_the_reason(self):
        runs = [{'date': '2026-11-24', 'published': False, 'reason': 'no new topic passed the checks', 'run': '45361099714'}]
        root = self.folder(last='2026-11-23', runs=runs)
        quiet = content_watch.verdict(root, datetime(2026, 11, 24, 18, 0, tzinfo=IST))
        self.assertEqual(content_watch.urgent_item(quiet), {})
        loud = content_watch.verdict(root, datetime(2026, 11, 25, 18, 0, tzinfo=IST))
        item = content_watch.urgent_item(loud)['auto_short_stopped']
        self.assertEqual(item['title'], 'Auto Short stopped: last one 23 Nov')
        self.assertIn('2 working days', item['detail'])
        self.assertIn('no new topic passed the checks', item['detail'])
        self.assertTrue(item['url'].endswith('/runs/45361099714'))
        self.assertLessEqual(len(item['detail']), 280)

    def test_a_published_short_clears_it(self):
        root = self.folder(last='2026-11-25')
        self.assertFalse(content_watch.verdict(root, datetime(2026, 11, 25, 18, 0, tzinfo=IST))['stopped'])

    def test_a_run_that_never_started_is_said_so(self):
        root = self.folder(last='2026-11-20')
        item = content_watch.urgent_item(content_watch.verdict(root, datetime(2026, 11, 24, 18, 0, tzinfo=IST)))
        self.assertIn('the daily run did not start', item['auto_short_stopped']['detail'])

    def test_record_run_keeps_the_plain_reason_and_skips_test_runs(self):
        root = self.folder()
        now = datetime(2026, 11, 25, 14, 40, tzinfo=IST)
        self.assertIsNone(content_watch.record_run(RuntimeError('x'), root, now, {'ANSWER_DRY': '1'}))
        entry = content_watch.record_run(RuntimeError('TopicHold: No distinct, evidence-supported topic'), root, now, {})
        self.assertEqual(entry['reason'], 'no new topic passed the checks')
        self.assertFalse(entry['published'])
        (root / 'lesson_history.json').write_text(json.dumps([{'published_on': '2026-11-25'}]))
        self.assertTrue(content_watch.record_run(None, root, now, {})['published'])
        runs = json.loads((root / 'daily_short_status.json').read_text())['runs']
        self.assertEqual([(run['date'], run['published']) for run in runs], [('2026-11-25', True)])

    def test_the_bar_takes_the_watch_own_words_and_keeps_other_owners(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'urgent.json'
            urgent_actions.update('health_watch', {'youtube': 'token expired'}, path=path)
            urgent_actions.update('content_watch', {'auto_short_stopped': {
                'title': 'Auto Short stopped: last one 13 Nov', 'detail': 'No automatic Short for 10 working days.',
                'url': 'https://github.com/x/y/actions/runs/1', 'evidence': 'last 2026-11-13'}}, path=path)
            items = {item['id']: item for item in json.loads(path.read_text())['items']}
            self.assertEqual(items['auto_short_stopped']['title'], 'Auto Short stopped: last one 13 Nov')
            self.assertEqual(items['auto_short_stopped']['action_url'], 'https://github.com/x/y/actions/runs/1')
            self.assertIn('youtube', items)
            urgent_actions.update('content_watch', {}, path=path)
            self.assertEqual([item['id'] for item in json.loads(path.read_text())['items']], ['youtube'])


if __name__ == '__main__':
    unittest.main()
