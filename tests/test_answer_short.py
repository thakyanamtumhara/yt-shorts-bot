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

    def test_the_rate_rule_asks_for_one_spoken_label_not_one_per_amount(self):
        from unittest import mock
        old = {'claim': 'x', 'limits': 'call it the current website rate', 'source_url': 'u', 'source_title': 't',
               'checked_on': '2026-11-27', 'amounts': [205]}
        table = [None] * 6 + [{'gst': 5}]
        with mock.patch.object(facts, 'fetch_catalogue', return_value=''), \
                mock.patch.object(facts, 'parse_catalogue', return_value=table), \
                mock.patch.object(facts, 'product_rates', return_value=[]), \
                mock.patch.object(facts, 'rate_facts', return_value={'rate_oversize_240gsm': old}), \
                mock.patch.object(facts, 'gallery', return_value={}), \
                mock.patch.object(facts, 'dtf_fact', side_effect=ValueError('no dtf')):
            sheet = facts.build('2026-11-27')
        rule = sheet['facts']['rate_oversize_240gsm']['limits']
        self.assertEqual(rule, facts.RATE_LIMITS.format(gst='5'))
        for words in ('say ONCE', 'one label covers every amount', 'Excludes 5% GST', 'Never round'):
            self.assertIn(words, rule)
        self.assertNotIn('call it the current website rate', rule)
        self.assertEqual(sheet['facts']['rate_oversize_240gsm']['amounts'], [205])

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

    def test_a_line_that_names_a_colour_shows_that_colour(self):
        from tools.answer_short.render import clip_colours, colours_named, plan_pictures
        names = {'Black', 'White', 'Navy', 'Off-white', 'Royal Blue', 'Army Green', 'Red'}
        self.assertEqual(colours_named('Black non-zipper hoodie ₹307 per piece.', names), {'Black'})
        self.assertEqual(colours_named('Off-white aur Royal Blue mein bhi hai, ordered kal.', names), {'Off-white', 'Royal Blue'})
        self.assertEqual(colours_named('Aapko hoodie kis colour mein chahiye?', names), set())
        self.assertEqual(clip_colours('https://x/ph/vid/hoodie-320gsm-black-v2.mp4', 'hoodie-320gsm', names), {'Black'})
        self.assertEqual(clip_colours('https://x/ph/vid/oversize-240gsm-royalblue-navy.mp4', 'oversize-240gsm', names),
                         {'Royal Blue', 'Navy'})
        photos = [{'u': f'https://x/ph/h/{n}.webp', 'c': colour} for n, colour in enumerate(['White', 'Navy', 'Black', 'Red'], 1)]
        sheet = {'visuals': {'rate_h': 'hoodie-320gsm'}, 'gallery': {'hoodie-320gsm': {
            'videos': ['https://x/ph/vid/hoodie-320gsm-white.mp4', 'https://x/ph/vid/hoodie-320gsm-black-v2.mp4'], 'photos': photos}}}
        says = ['Hoodie ka rate kya hai?', 'Black hoodie ₹307 per piece.', 'White aur Navy mein ₹337 hai.',
                'Red mein bhi ₹337 hai.', 'Army Green abhi nahi hai.', 'Kis colour mein chahiye?']
        plan = {'lines': [{'say': say, 'show': 'rate_h', 'card': ''} for say in says]}
        keys = [shot['key'].split('/', 1)[1] for shot in plan_pictures(plan, sheet, [(i * 4.0, i * 4.0 + 4.0) for i in range(6)])]
        self.assertEqual(keys[1], 'hoodie-320gsm-black-v2.mp4')
        self.assertIn(keys[2], ('hoodie-320gsm-white.mp4', '1.webp', '2.webp'))
        self.assertEqual(keys[3], '4.webp')
        self.assertIn(keys[4], ('hoodie-320gsm-white.mp4', 'hoodie-320gsm-black-v2.mp4', '1.webp', '2.webp', '3.webp', '4.webp'))

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

    def test_website_may_be_said_twice_the_rate_label_and_where_to_order(self):
        def twice(plan):
            plan['lines'][5]['say'] = 'GST 5% aur delivery alag hai, website se 1 piece sample mangwa lo.'
        self.assertEqual(self.problems(twice), '')

        def thrice(plan):
            twice(plan)
            plan['lines'][4]['say'] = 'Fabric 100% cotton biowash hai, website pe size XS se XXL tak.'
        self.assertIn('say "website" at most 2 times', self.problems(thrice))


class FakeClient:
    """Stands in for the model: the writer answers the first question it is offered; the fact checker refuses
    every plan for the questions in `hard`."""

    def __init__(self, hard):
        self.hard, self.asked, self.messages = set(hard), [], self

    def create(self, model, max_tokens, messages):
        prompt = messages[0]['content']
        if prompt.startswith('You are the fact checker'):
            refused = any(f'"search_phrase": "{phrase}"' in prompt or phrase in self.asked[-1:] for phrase in self.hard)
            answer = {'breaches': [{'where': '2', 'problem': 'not in the facts'}] if refused else []}
        else:
            offered = json.loads(prompt.split('yt / google = where it appeared):\n', 1)[1].split('\n\nFACTS YOU MAY USE', 1)[0])
            phrase = offered[0]['q']
            self.asked.append(phrase)
            answer = {**good_plan(), 'search_phrase': phrase, 'intent_key': 'intent_' + phrase.replace(' ', '_')}
        text = json.dumps(answer, ensure_ascii=False)
        return type('Reply', (), {'content': [type('Block', (), {'text': text})()], 'stop_reason': 'end_turn'})()


class OneADay(unittest.TestCase):
    def test_a_second_start_the_same_day_posts_nothing(self):
        from tools.answer_short import run
        history = {'published': [{'date': '2026-11-26', 'youtube': 'abc', 'instagram': '1'},
                                 {'date': '2026-11-27', 'youtube': None, 'instagram': None, 'telegram': True}]}
        self.assertTrue(run.out_today(history, date(2026, 11, 26)))
        self.assertFalse(run.out_today(history, date(2026, 11, 27)))
        self.assertFalse(run.out_today(history, date(2026, 11, 28)))


class HardQuestion(unittest.TestCase):
    """27-Nov-2026: no plan passed for one question in 4 attempts and the day's Short was lost."""

    def setUp(self):
        import os
        from unittest import mock
        self.env = mock.patch.dict(os.environ, {'ANSWER_MODEL': 'claude-test-1-0'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.bank = {'queries': [{'q': f'question {number}', 'yt': 1, 'google': 1, 'rank': number} for number in range(1, 8)]}

    def test_a_question_no_plan_passes_for_gives_way_to_another_and_is_rested(self):
        client, said = FakeClient(hard={'question 1'}), []
        plan = write.make_plan(client, SHEET, self.bank, [], log=said.append)
        self.assertEqual(plan['search_phrase'], 'question 2')
        self.assertEqual(client.asked, ['question 1'] * write.ROUNDS + ['question 2'])
        self.assertEqual([(item['search_phrase'], item['intent_key']) for item in plan['gave_up_on']],
                         [('question 1', 'intent_question_1')])
        self.assertTrue(any('trying one other question' in line for line in said))

    def test_two_hard_questions_stop_the_run_and_both_are_handed_back_to_be_rested(self):
        client = FakeClient(hard={'question 1', 'question 2'})
        with self.assertRaises(write.WriteHold) as stopped:
            write.make_plan(client, SHEET, self.bank, [], log=lambda line: None)
        self.assertEqual([item['search_phrase'] for item in stopped.exception.failed], ['question 1', 'question 2'])
        self.assertEqual(len(client.asked), 2 * write.ROUNDS)

    def test_a_pinned_test_question_never_moves_to_another_one(self):
        client = FakeClient(hard={'question 3'})
        with self.assertRaises(write.WriteHold) as stopped:
            write.make_plan(client, SHEET, self.bank, [], log=lambda line: None, only='question 3')
        self.assertEqual(client.asked, ['question 3'] * write.ROUNDS)
        self.assertEqual([item['search_phrase'] for item in stopped.exception.failed], ['question 3'])

    def test_the_first_plan_that_passes_gives_up_on_nothing(self):
        plan = write.make_plan(FakeClient(hard=set()), SHEET, self.bank, [], log=lambda line: None)
        self.assertEqual((plan['search_phrase'], plan['gave_up_on']), ('question 1', []))


class Screens(unittest.TestCase):
    """A real page of our own website on a line or two. A screen that cannot be shown never stops a Short."""

    def sheet(self):
        return {'facts': {'size_chart_on_site': {}, 'order_from_one_piece': {}, 'dtf_rate': {'amounts': [130]}, 'rate_os': {}},
                'visuals': {'rate_os': 'oversize-240gsm', 'rate_sale': 'cotton-polo-sale'},
                'gallery': {'oversize-240gsm': {'videos': ['https://x/ph/vid/a.mp4'], 'photos': []},
                            'cotton-polo-sale': {'videos': ['https://x/ph/vid/s.mp4'], 'photos': []}}}

    def plan(self, screens, cards=None, shows=None):
        cards, shows = cards or {}, shows or {}
        return {'fact_ids': ['size_chart_on_site', 'order_from_one_piece', 'dtf_rate', 'rate_os'],
                'lines': [{'say': 'x', 'show': shows.get(index, 'rate_os'), 'card': cards.get(index, ''), 'screen': name}
                          for index, name in enumerate(screens)]}

    def test_a_screen_is_kept_only_where_it_may_be_shown(self):
        from tools.answer_short import screens
        plan = self.plan(['dtf_rate', 'size_chart_on_site', 'made_up', 'order_from_one_piece', 'dtf_rate', 'dtf_rate'],
                         cards={1: 'rate_os'})
        self.assertEqual(screens.tidy(plan, self.sheet()), ['order_from_one_piece', 'dtf_rate'])
        self.assertEqual([line['screen'] for line in plan['lines']], ['', '', '', 'order_from_one_piece', 'dtf_rate', ''])
        three = self.plan(['', 'dtf_rate', 'order_from_one_piece', 'size_chart_on_site', ''])
        self.assertEqual(len(screens.tidy(three, self.sheet())), 2)
        uncited = self.plan(['', 'dtf_rate', ''])
        uncited['fact_ids'] = ['rate_os']
        self.assertEqual(screens.tidy(uncited, self.sheet()), [])
        unknown_today = self.plan(['', 'dtf_rate', ''])
        sheet = self.sheet()
        del sheet['facts']['dtf_rate']
        self.assertEqual(screens.tidy(unknown_today, sheet), [])

    def test_the_off_switch_leaves_every_line_its_product_picture(self):
        import os
        from unittest import mock
        from tools.answer_short import screens
        plan = self.plan(['', 'dtf_rate', 'order_from_one_piece', ''])
        with mock.patch.dict(os.environ, {'ANSWER_SCREENS': 'off'}):
            self.assertEqual(screens.offered(self.sheet()), {})
            self.assertEqual(screens.tidy(plan, self.sheet()), [])
        self.assertEqual([line['screen'] for line in plan['lines']], ['', '', '', ''])

    def test_a_size_chart_belongs_to_the_product_shown_and_never_to_a_sale_lot(self):
        from tools.answer_short import screens
        plan = self.plan(['', 'size_chart_on_site', 'size_chart_on_site', ''], shows={2: 'rate_sale'})
        self.assertEqual(screens.tidy(plan, self.sheet()), ['size_chart_on_site'])
        self.assertEqual(screens.key_for(plan['lines'][1], self.sheet()), 'size_chart_on_site@oversize-240gsm')
        self.assertEqual(screens.key_for({'screen': 'dtf_rate', 'show': 'rate_os'}, self.sheet()), 'dtf_page')
        self.assertEqual(screens.key_for({'screen': 'order_from_one_piece', 'show': 'rate_os'}, self.sheet()), 'order_from_one_piece')

    def test_two_facts_on_one_page_share_its_picture_and_the_second_showing_scrolls_on(self):
        from tools.answer_short import render, screens
        sheet = {**self.sheet(), 'facts': {**self.sheet()['facts'], 'dtf_how_it_works': {}}}
        plan = self.plan(['', 'dtf_rate', '', 'dtf_how_it_works', ''])
        plan['fact_ids'].append('dtf_how_it_works')
        self.assertEqual(screens.tidy(plan, sheet), ['dtf_rate', 'dtf_how_it_works'])
        sheet['screen_files'] = {'dtf_page': '/tmp/dtf.png'}
        shots = render.plan_pictures(plan, sheet, [(index * 5.0, index * 5.0 + 5.0) for index in range(5)])
        self.assertEqual([shot['key'] for shot in shots if shot['kind'] == 'screen'], ['screen/dtf_page', 'screen/dtf_page'])
        self.assertEqual((shots[1]['from'], shots[3]['from']), (0.0, 5.0 - render.SCREEN_WAIT))

    def test_a_taken_screen_is_shown_and_a_missing_one_keeps_the_product_picture(self):
        from tools.answer_short.render import plan_pictures
        plan = self.plan(['', 'size_chart_on_site', 'dtf_rate', ''])
        sheet = {**self.sheet(), 'screen_files': {'size_chart_on_site@oversize-240gsm': '/tmp/chart.png'}}
        shots = plan_pictures(plan, sheet, [(index * 4.0, index * 4.0 + 4.0) for index in range(4)])
        self.assertEqual([shot['kind'] for shot in shots], ['video', 'screen', 'video', 'video'])
        self.assertEqual((shots[1]['key'], shots[1]['bar'], shots[1]['card']),
                         ('screen/size_chart_on_site@oversize-240gsm', 'bulkplaintshirt.com', ''))

    def test_no_browser_means_product_pictures_not_a_stopped_short(self):
        import sys
        from unittest import mock
        from tools.answer_short import screens
        plan = self.plan(['', 'dtf_rate', 'order_from_one_piece', ''])
        said = []
        with mock.patch.dict(sys.modules, {'playwright': None, 'playwright.sync_api': None}), tempfile.TemporaryDirectory() as folder:
            self.assertEqual(screens.capture(plan, self.sheet(), folder, log=said.append), {})
        self.assertEqual([line['screen'] for line in plan['lines']], ['', '', '', ''])
        self.assertEqual(len(said), 1)
        quiet = []
        self.assertEqual(screens.capture(self.plan(['', '', '']), self.sheet(), '/nowhere', log=quiet.append), {})
        self.assertEqual(quiet, [])

    def test_a_blank_or_tiny_picture_is_not_a_screen(self):
        from PIL import Image, ImageDraw
        from tools.answer_short import screens
        with tempfile.TemporaryDirectory() as folder:
            blank, small, good = (Path(folder) / name for name in ('blank.png', 'small.png', 'good.png'))
            Image.new('RGB', (1140, 681), 'white').save(blank)
            Image.new('RGB', (300, 200), 'white').save(small)
            page = Image.new('RGB', (1140, 681), 'white')
            ImageDraw.Draw(page).rectangle((0, 0, 1140, 200), fill=(106, 27, 154))
            page.save(good)
            self.assertEqual([screens.looks_right(path) for path in (blank, small, good, Path(folder) / 'none.png')],
                             [False, False, True, False])

    def test_a_screen_is_drawn_above_the_captions_and_a_long_page_scrolls(self):
        from PIL import Image, ImageDraw
        from tools.answer_short import render
        with tempfile.TemporaryDirectory() as folder:
            short, long = Path(folder) / 'short.png', Path(folder) / 'long.png'
            Image.new('RGB', (1140, 681), 'white').save(short)
            page = Image.new('RGB', (1170, 2400), 'white')
            ImageDraw.Draw(page).rectangle((0, 1150, 1170, 1250), fill=(200, 0, 0))
            page.save(long)
            base = {'kind': 'screen', 'url': '', 'bar': 'bulkplaintshirt.com', 'key': 'screen/x', 'start': 2.0, 'end': 8.0}
            for path in (short, long):
                shot = render.Shot({**base, 'path': str(path)}, Path(folder))
                first, last = shot.frame(2.1), shot.frame(7.9)
                self.assertEqual(first.size, (render.W, render.H))
                self.assertGreater(min(first.getpixel((render.W // 2, 700))), 200)
                for y in (render.SCREEN_TOP - 30, render.SCREEN_BOTTOM + 30, render.CAPTION_Y):
                    self.assertLess(max(first.getpixel((render.W // 2, y))), 140)
                    self.assertLess(max(last.getpixel((render.W // 2, y))), 140)
            self.assertNotEqual(first.tobytes(), last.tobytes())
            red = [y for y in range(render.SCREEN_TOP, render.SCREEN_BOTTOM) if last.getpixel((render.W // 2, y))[0] > 150
                   and last.getpixel((render.W // 2, y))[1] < 80]
            self.assertTrue(red)


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
