import ast
import copy
import json
from pathlib import Path
import re
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from tools.daily_topic_selection import (
    DIMENSIONS, SelectedTopic, TopicHold, choose_topic, evidence_prompt,
    load_bank, load_topic_history, review_result, unsupported_shortcut, validate_brief,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'daily_short.py').read_text()
TREE = ast.parse(SOURCE)
BANK = load_bank()


def load_function(name, namespace=None):
    scope = {'json': json, 're': re, **(namespace or {})}
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / 'daily_short.py'), 'exec'), scope)
    return scope[name]


def brief(index=0):
    return copy.deepcopy(BANK['seed_lessons'][index])


def review_payload(**updates):
    value = {'scores': dict.fromkeys(DIMENSIONS, 7), 'score': 28,
             'facts_supported': True, 'teaches_specific_lesson': True,
             'duplicate_of': None, 'feedback': 'Explains loop movement and the separate fibre specification.'}
    value.update(updates)
    return value


def client(payload=None, error=None):
    create = Mock(side_effect=error) if error else Mock(return_value=SimpleNamespace(
        content=[SimpleNamespace(text=json.dumps(payload))]))
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def choose(candidates, reviewer, **kwargs):
    return choose_topic(candidates, bank=BANK, history=kwargs.pop('history', []),
                        review=reviewer, viable=kwargs.pop('viable', lambda title: True), **kwargs)


class EvidenceAndSelectionTest(unittest.TestCase):
    def test_corrupt_or_wrong_history_cannot_reset_freshness_silently(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'history.json'
            self.assertEqual(load_topic_history(path), [])
            for content in ('broken', '{}', '[null]', '[42]'):
                path.write_text(content)
                with self.subTest(content=content), self.assertRaises(TopicHold):
                    load_topic_history(path)
            path.write_text('["Prior fabric lesson"]')
            self.assertEqual(load_topic_history(path), ['Prior fabric lesson'])

    def test_validated_seed_bank_contains_specific_teaching_and_primary_sources(self):
        for seed in BANK['seed_lessons']:
            result = validate_brief(seed, BANK)
            self.assertTrue(result['evidence'])
            self.assertNotIn('₹', result['topic'])

    def test_observed_10_september_all_below_threshold_never_selects_best_bad_topic(self):
        reviewer = Mock(side_effect=[(score, 'Same lesson as recent output') for score in [18, 14, 14, 14, 14]])
        with self.assertRaisesRegex(TopicHold, 'No distinct'):
            choose(BANK['seed_lessons'], reviewer)
        self.assertEqual(reviewer.call_count, 5)

    def test_twenty_four_cannot_pass_in_any_bank_position(self):
        with self.assertRaises(TopicHold):
            choose(BANK['seed_lessons'], lambda _: (24, 'Below threshold'))

    def test_selects_strongest_approved_lesson_and_retains_evidence_for_writer_archive(self):
        reviewer = Mock(side_effect=[(25, 'Supported'), (32, 'Clearer learning')])
        result = choose([brief(0), brief(1)], reviewer)
        self.assertIsInstance(result, str)
        self.assertEqual(result, brief(1)['topic'])
        self.assertEqual(json.loads(json.dumps([result])), [brief(1)['topic']])
        self.assertEqual(result.brief['selection_review']['score'], 32)
        self.assertIn('PRIMARY-SOURCE', evidence_prompt(result))
        self.assertIn('french_terry_fleece', evidence_prompt(result))

    def test_review_exception_never_becomes_thirty_point_approval(self):
        reviewer = Mock(side_effect=RuntimeError('Unavailable'))
        with self.assertRaises(TopicHold):
            choose([brief()], reviewer)

    def test_bad_callback_score_types_cannot_pass(self):
        for score in (True, '30', 30.0, 41, None):
            with self.subTest(score=score), self.assertRaises(TopicHold):
                choose([brief()], lambda _: (score, 'Not a valid integer review'))

    def test_unknown_fact_bare_title_generic_label_and_empty_bank_do_not_reach_reviewer(self):
        unknown = brief(); unknown['fact_ids'] = ['invented_fact']
        generic = brief(); generic['lesson'] = 'Check quality and ask your supplier.'
        for candidate in (unknown, generic, 'Plain T-Shirt Quality Check — GSM aur Fabric Basics'):
            reviewer = Mock(return_value=(40, 'Looks good'))
            with self.subTest(candidate=candidate), self.assertRaises(TopicHold):
                choose([candidate], reviewer)
            reviewer.assert_not_called()
        with self.assertRaises(TopicHold):
            choose([], Mock())

    def test_normalized_title_duplicate_cannot_be_reintroduced(self):
        reviewer = Mock()
        with self.assertRaises(TopicHold):
            choose([brief()], reviewer, history=[brief()['topic'].replace('?', ' ?  ')])
        reviewer.assert_not_called()

    def test_same_lesson_intent_cannot_fill_multiple_review_slots(self):
        original = brief(); renamed = brief(); renamed['topic'] = 'Another hook for the same buyer lesson'
        reviewer = Mock(return_value=(28, 'Useful'))
        choose([original, renamed], reviewer)
        self.assertEqual(reviewer.call_count, 1)

    def test_blog_viability_never_waives_quality_floor(self):
        with self.assertRaises(TopicHold):
            choose(BANK['seed_lessons'], lambda _: (18, 'Repeated'), viable=lambda _: False)

    def test_at_most_five_reviews_and_ten_blog_checks(self):
        many = []
        for index in range(15):
            item = brief(); item.update(topic=f'Proposed lesson {index}', intent_key=f'angle_{index}'); many.append(item)
        reviewer = Mock(return_value=(28, 'Supported')); viable = Mock(return_value=True)
        choose(many, reviewer, viable=viable)
        self.assertEqual(reviewer.call_count, 5)
        self.assertEqual(viable.call_count, 10)


class StrictReviewTest(unittest.TestCase):
    def test_topic_api_and_invalid_json_fail_closed(self):
        review = load_function('review_topic')
        for api in (client(error=RuntimeError('Down')), client([]), client({'score': 30, 'feedback': 'Approved by default'})):
            self.assertEqual(review(api, brief(), [])[:1], (0,))

    def test_valid_review_keeps_existing_provider_and_score(self):
        api = client(review_payload())
        self.assertEqual(load_function('review_topic')(api, brief(), ["Recent title"])[0], 28)
        args = api.messages.create.call_args.kwargs
        self.assertEqual(args['model'], 'claude-opus-4-6')
        self.assertIn('Recent title', args['messages'][0]['content'])
        self.assertIn('knit_loop_stretch', args['messages'][0]['content'])

    def test_high_scoring_duplicate_or_empty_checklist_stays_rejected(self):
        for updates in ({'duplicate_of': '180 GSM vs 200 GSM — Inside Out Rub Test'},
                        {'teaches_specific_lesson': False}, {'facts_supported': False}):
            self.assertEqual(review_result(review_payload(**updates))[0], 0)

    def test_schema_cannot_approve_strings_missing_flags_or_mismatched_total(self):
        bad = [{'score': True}, {'score': 29}, {'score': '28'}, {'facts_supported': 'true'},
               {'teaches_specific_lesson': None}, {'duplicate_of': ''}, {'feedback': ''}, {'scores': {}}]
        for update in bad:
            with self.subTest(update=update), self.assertRaises(TopicHold):
                review_result(review_payload(**update))
        value = review_payload(); del value['duplicate_of']
        with self.assertRaises(TopicHold):
            review_result(value)


class ActualPublishedMythRegressionTest(unittest.TestCase):
    CLAIMS = (
        'Rub fabric near your ear — rough sound means carded yarn, smooth means combed ring-spun.',
        'Surface pe chote chote fuzz dikh rahe hain toh bio-wash nahi hua hai.',
        "Stretch and release — if shape doesn't bounce back, it's not pre-shrunk.",
        'कपड़ा कान के पास रगड़ो, आवाज़ से कोम्ड yarn पहचान लो।',
    )

    def test_each_actual_certification_shortcut_is_blocked_before_topic_review(self):
        for claim in self.CLAIMS:
            item = brief(); item['lesson'] = claim + ' This is the specific fabric explanation the buyer should follow when choosing a shirt.'
            reviewer = Mock(return_value=(40, 'Novel physical test'))
            with self.subTest(claim=claim), self.assertRaises(TopicHold):
                choose([item], reviewer)
            reviewer.assert_not_called()

    def test_each_actual_claim_is_blocked_before_script_review_api(self):
        review = load_function('review_script')
        for claim in self.CLAIMS:
            api = client({'approved': True})
            result = review(api, claim, claim, SelectedTopic(validate_brief(brief(), BANK)))
            self.assertEqual(result[:3], (False, 0, 'unsupported_shortcut'))
            api.messages.create.assert_not_called()

    def test_ordinary_sourced_process_explanation_is_not_a_certification_trick(self):
        for text in ('Biopolishing removes protruding microfibrils and reduces surface fuzz.',
                     'Combing removes short fibres before yarn is spun.',
                     'Compaction reduces later shrinkage by compressing the fabric structure.'):
            self.assertIsNone(unsupported_shortcut(text))


class ActualIntegrationTest(unittest.TestCase):
    def smart(self, generated, reviewer):
        flags = Mock()
        function = load_function('smart_pick_topic', {
            'search_trending_topics': lambda *args: generated,
            'review_topic': reviewer, '_topic_blog_viable': lambda *args: True,
            'TOPIC_MIN_SCORE': 25, 'TOPIC_MAX_CANDIDATES': 5, 'flag': flags})
        return function, flags

    def test_empty_brainstorm_seed_fallback_still_requires_review(self):
        function, flags = self.smart([], lambda *args: (0, 'Unapproved'))
        with self.assertRaises(TopicHold):
            function(None, ['Legacy unused fabricated incident'], [])
        flags.assert_not_called()

    def test_malformed_brainstorm_bare_topic_cannot_bypass_seed_gate(self):
        function, flags = self.smart(['Unreviewed generated title'], lambda *args: (18, 'Below threshold'))
        with self.assertRaises(TopicHold):
            function(None, [], [])
        flags.assert_not_called()

    def test_legacy_bank_exhausted_or_unused_have_the_same_quality_floor(self):
        for legacy in ([], ['A legacy bank topic']):
            function, flags = self.smart(BANK['seed_lessons'], lambda *args: (24, 'Weak'))
            with self.subTest(legacy=legacy), self.assertRaises(TopicHold):
                function(None, legacy, [])
            flags.assert_not_called()

    def test_seed_fallback_can_select_an_actually_approved_explanation(self):
        function, flags = self.smart([], lambda *args: (28, 'Supported specific explanation'))
        result = function(None, [], [])
        self.assertIsInstance(result, SelectedTopic)
        flags.assert_any_call('topic_approved', True)
        flags.assert_any_call('topic_lesson', result.brief)

    def test_writer_receives_source_limits_and_does_not_ban_process_explanations(self):
        function = load_function('get_script_prompt', {'BUSINESS_CONTEXT': '',
            'get_source_channel_top_topics': lambda _: [], '_own_channel_performance_signal': lambda: '',
            'extract_voice_corpus_style_hints': lambda: '', '_get_recent_clip_prompts': lambda: ''})
        prompt = function(SelectedTopic(validate_brief(brief(), BANK)))
        self.assertIn('knit_loop_stretch', prompt)
        self.assertIn('TEACH THE REASON', prompt)
        self.assertNotIn('THEORY AVOID', prompt)

    def test_script_cannot_pass_when_source_or_explanation_flags_fail(self):
        review = load_function('review_script')
        topic = SelectedTopic(validate_brief(brief(), BANK))
        payload = {'approved': True, 'scores': dict.fromkeys(
            ('hook', 'natural_feel', 'value', 'ending', 'viral_potential', 'visual_alignment'), 7),
            'total_score': 42, 'weakest': 'hook', 'feedback': 'Review done.'}
        for flags in ({}, {'lesson_supported': True, 'specific_lesson_delivered': False},
                      {'lesson_supported': 'true', 'specific_lesson_delivered': True}):
            result = review(client({**payload, **flags}), 'Loops move to give stretch.', 'Loops move to give stretch.', topic)
            self.assertFalse(result[0])
        good = {**payload, 'lesson_supported': True, 'specific_lesson_delivered': True}
        self.assertTrue(review(client(good), 'Loops move to give stretch.', 'Loops move to give stretch.', topic)[0])

    def test_question_mining_drops_owner_sales_reply_and_best_compliment(self):
        comments = [{'text': text, 'likes': 1, 'video_title': 'T-shirts'} for text in (
            'This man is the best businessman I have worked with.',
            'Search sale91.com and WhatsApp to chat: https://whatsapp.sale91.com',
            'Best quality kitane gsm ka hota hai?', 'Sample kaise mangaye?')]
        comments.append({'text': 'What do you want to buy?', 'is_owner': True, 'video_title': 'Own reply'})
        output = load_function('get_audience_questions', {'fetch_source_channel_comments': lambda: comments})()
        self.assertIn('gsm', output); self.assertIn('Sample', output)
        self.assertNotIn('businessman', output); self.assertNotIn('sale91', output); self.assertNotIn('Own reply', output)

    def test_brainstorm_api_error_returns_no_unreviewed_topic(self):
        from tools.topic_audience_signals import topic_interest
        function = load_function('search_trending_topics', {'get_audience_questions': lambda _: 'No questions',
            'get_ig_topic_interest_signals': lambda _: topic_interest([])})
        self.assertEqual(function(client(error=RuntimeError('Unavailable')), []), [])


if __name__ == '__main__':
    unittest.main()
