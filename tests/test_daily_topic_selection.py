import ast
import copy
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import re
import textwrap
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from tools.daily_topic_selection import (
    DIMENSIONS, SelectedTopic, TopicHold, brainstorming_context, choose_topic,
    consume_uploaded_topic, evidence_prompt,
    load_bank, load_topic_history, response_json, review_result, safe_failure_details,
    unsupported_shortcut, validate_brief,
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


class TopicConsumptionTest(unittest.TestCase):
    VIDEO_ID = 'aB3dE5gH7_-'

    def test_normal_ack_preserves_existing_strings_and_is_idempotent(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'topic_history.json'
            path.write_text('["Earlier lesson", "Other session lesson"]')
            topic = SelectedTopic(validate_brief(brief(), BANK))
            self.assertTrue(consume_uploaded_topic(path, topic, self.VIDEO_ID))
            self.assertEqual(load_topic_history(path), ['Earlier lesson', 'Other session lesson', str(topic)])
            self.assertFalse(consume_uploaded_topic(path, topic, self.VIDEO_ID))
            self.assertEqual(len(load_topic_history(path)), 3)

    def test_test_modes_and_invalid_ack_never_touch_history(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'topic_history.json'
            for mode in ('test_mode', 'new_test_mode', 'single_veo_test'):
                self.assertFalse(consume_uploaded_topic(path, 'Lesson', self.VIDEO_ID, **{mode: True}))
            for video_id in (None, '', '?', 'test:12345', 'too-short', 12345678901, 'https://yt/12345'):
                self.assertFalse(consume_uploaded_topic(path, 'Lesson', video_id))
            self.assertFalse(path.exists())

    def test_corrupt_history_and_failed_atomic_write_preserve_original_bytes(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'topic_history.json'
            path.write_text('["Earlier lesson"]')
            original = path.read_bytes()
            with patch('tools.daily_topic_selection.os.fsync', side_effect=OSError('Disk write failed')):
                with self.assertRaises(OSError):
                    consume_uploaded_topic(path, 'New lesson', self.VIDEO_ID)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])
            path.write_text('broken')
            with self.assertRaises(TopicHold):
                consume_uploaded_topic(path, 'New lesson', self.VIDEO_ID)
            self.assertEqual(path.read_text(), 'broken')

    def upload_stage(self, path, *, mode=None, upload_error=None, instagram_error=None,
                     video_id=VIDEO_ID, youtube_auth=True):
        source = (ROOT / 'daily_short.py').read_text()
        start = source.index('    # ── 10. Upload to YouTube')
        end = source.index('    # ── 10e. Generate & Publish SEO Blog Post', start)
        scope = {'TEST_MODE': False, 'NEW_TEST_MODE': False, 'SINGLE_VEO_TEST': False,
                 'TOPIC_HISTORY_FILE': path, 'consume_uploaded_topic': consume_uploaded_topic,
                 'fresh_topic': 'New supported lesson', 'output_path': 'video.mp4',
                 'thumbnail_path': None, 'yt_title': 'Title', 'ig_title': 'IG title',
                 'yt_description': 'Description', 'yt_tags': [], 'music_mood': 'calm',
                 'get_topic_tags': lambda topic: [], 'get_youtube_service': lambda: object() if youtube_auth else None,
                 'check_past_engagement': Mock(), 'check_instagram_engagement': Mock(),
                 'upload_to_youtube': Mock(side_effect=upload_error, return_value=(video_id, 'https://youtube.com/shorts/' + str(video_id))),
                 'SCHEDULE_PUBLISH': False, 'time': SimpleNamespace(sleep=Mock()),
                 'get_pin_tail': lambda topic: None, 'add_to_playlist': Mock(), 'flag': Mock(),
                 'CROSS_POST_INSTAGRAM': False, 'os': SimpleNamespace(environ={}),
                 'cross_post_to_instagram': Mock(side_effect=instagram_error, return_value=None),
                 'publish_fb_reel': Mock(return_value=None), 'post_telegram_channel': Mock()}
        if mode:
            scope[mode] = True
        with redirect_stdout(StringIO()):
            exec(compile(textwrap.dedent(source[start:end]), 'upload_stage', 'exec'), scope)
        return scope

    def test_actual_upload_stage_consumes_after_ack_before_instagram_failure(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'topic_history.json'
            path.write_text('["Earlier lesson"]')
            with self.assertRaisesRegex(RuntimeError, 'Instagram failed'):
                self.upload_stage(path, instagram_error=RuntimeError('Instagram failed'))
            self.assertEqual(load_topic_history(path), ['Earlier lesson', 'New supported lesson'])

    def test_actual_upload_stage_errors_auth_failures_and_invalid_ids_do_not_consume(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'topic_history.json'
            path.write_text('["Earlier lesson"]')
            for options in ({'upload_error': RuntimeError('Upload failed')}, {'youtube_auth': False},
                            {'video_id': '?'}, {'video_id': 'invalid'}):
                with self.subTest(options=options):
                    self.upload_stage(path, **options)
                    self.assertEqual(load_topic_history(path), ['Earlier lesson'])

    def test_actual_upload_stage_test_modes_keep_their_existing_upload_semantics(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'topic_history.json'
            path.write_text('["Earlier lesson"]')
            for mode in ('TEST_MODE', 'NEW_TEST_MODE', 'SINGLE_VEO_TEST'):
                with self.subTest(mode=mode):
                    scope = self.upload_stage(path, mode=mode)
                    self.assertEqual(load_topic_history(path), ['Earlier lesson'])
                    self.assertEqual(scope['upload_to_youtube'].call_count, 0 if mode == 'TEST_MODE' else 1)

    def test_actual_topic_selection_is_read_only_until_successful_upload(self):
        source = (ROOT / 'daily_short.py').read_text()
        start = source.index('    # ── 2. Pick a distinct lesson with reviewed facts')
        end = source.index('    # ── 3. Generate Script (with quality gate)', start)
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'topic_history.json'
            path.write_text('["Earlier lesson"]')
            original = path.read_bytes()
            scope = {'TOPIC_HISTORY_FILE': path, 'claude': None, 'TOPIC_BANK': [],
                     'smart_pick_topic': Mock(return_value='New supported lesson')}
            with redirect_stdout(StringIO()):
                exec(compile(textwrap.dedent(source[start:end]), 'selection_stage', 'exec'), scope)
            self.assertEqual(scope['fresh_topic'], 'New supported lesson')
            self.assertEqual(path.read_bytes(), original)


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


class TopicResponseReliabilityTest(unittest.TestCase):
    def brainstorm(self):
        from tools.topic_audience_signals import topic_interest
        return load_function('search_trending_topics', {
            'get_audience_questions': lambda _: 'No current questions',
            'get_ig_topic_interest_signals': lambda _: topic_interest([])})

    def response(self, text, stop='end_turn', tokens=500):
        return SimpleNamespace(content=[SimpleNamespace(text=text)], stop_reason=stop,
                               usage=SimpleNamespace(output_tokens=tokens))

    def test_truncated_but_parseable_json_is_never_accepted(self):
        with self.assertRaisesRegex(TopicHold, 'output-token limit'):
            response_json(self.response('[]', 'max_tokens', 2400))

    def test_refusal_or_tool_response_never_becomes_approval(self):
        for stop in ('refusal', 'tool_use', 'pause_turn'):
            with self.subTest(stop=stop), self.assertRaises(TopicHold):
                response_json(self.response('[]', stop))

    def test_multiple_text_blocks_and_json_fence_are_supported(self):
        response = self.response('```json\n[')
        response.content.extend([SimpleNamespace(type='thinking'), SimpleNamespace(text=']\n```')])
        self.assertEqual(response_json(response), [])

    def test_truncation_retries_smaller_request_and_reports_safe_cause(self):
        api = client([])
        api.messages.create.side_effect = [self.response('[{"topic":', 'max_tokens', 3200),
                                           self.response(json.dumps([brief()]))]
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.brainstorm()(api, []), [brief()])
        self.assertIn('stop_reason=max_tokens', output.getvalue())
        self.assertIn('output_tokens=3200', output.getvalue())
        self.assertEqual(api.messages.create.call_count, 2)
        for call in api.messages.create.call_args_list:
            self.assertEqual(call.kwargs['model'], 'claude-opus-4-6')
            self.assertEqual(call.kwargs['max_tokens'], 3200)
        self.assertIn('at most TWO', api.messages.create.call_args.kwargs['messages'][0]['content'])

    def test_invalid_array_or_repeated_malformed_json_still_falls_back_to_seeds(self):
        for payload in ({'topics': [brief()]}, ['bare topic'], None):
            api = client(payload)
            self.assertEqual(self.brainstorm()(api, []), [])
            self.assertEqual(api.messages.create.call_count, 2)

    def test_valid_empty_result_is_not_retried_or_treated_as_api_failure(self):
        api = client([])
        self.assertEqual(self.brainstorm()(api, []), [])
        self.assertEqual(api.messages.create.call_count, 1)

    def test_auth_failure_is_not_retried_and_secrets_are_not_logged(self):
        error = RuntimeError('api-key=secret-sentinel https://private.invalid/?token=secret-sentinel')
        error.status_code = 401
        api = client(error=error)
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.brainstorm()(api, []), [])
        self.assertEqual(api.messages.create.call_count, 1)
        self.assertIn('http_status=401', output.getvalue())
        self.assertNotIn('secret-sentinel', output.getvalue())
        self.assertNotIn('secret-sentinel', safe_failure_details(error))

    def test_truncated_review_retries_without_relaxing_duplicate_gate(self):
        api = client([])
        api.messages.create.side_effect = [self.response('{', 'max_tokens', 900),
            self.response(json.dumps(review_payload(duplicate_of='Prior buyer lesson')))]
        self.assertEqual(load_function('review_topic')(api, brief(), [])[0], 0)
        self.assertEqual(api.messages.create.call_count, 2)
        self.assertEqual(api.messages.create.call_args.kwargs['max_tokens'], 900)

    def test_seed_buffer_covers_previously_unused_fact_ids_after_exact_exhaustion(self):
        history = [item['topic'] for item in BANK['seed_lessons'][:6]]
        available = [validate_brief(item, BANK, history) for item in BANK['seed_lessons'][6:]]
        self.assertGreaterEqual(len(available), 4)
        self.assertEqual(len({item['intent_key'] for item in available}), len(available))

    def test_prompt_prioritizes_unused_facts_and_excludes_completed_decisions(self):
        history = [brief(0)['topic'], brief(1)['topic'], brief(2)['topic']]
        context = brainstorming_context(BANK, history)
        self.assertNotIn('knit_loop_stretch', context['preferred_fact_ids'])
        self.assertIn('jersey_face_back', context['preferred_fact_ids'])
        self.assertIn('knit_loop_stretch', context['facts'])
        self.assertEqual(len(context['completed_lessons']), 3)
        api = client([])
        self.brainstorm()(api, history)
        prompt = api.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertIn(brief(0)['buyer_decision'], prompt)
        self.assertIn(brief(0)['intent_key'], prompt)
        self.assertIn('genuinely different buyer decision', prompt)

    def test_completed_known_intent_is_rejected_after_title_change(self):
        candidate = brief(0)
        candidate['topic'] = 'A renamed hook for the identical lesson'
        with self.assertRaisesRegex(TopicHold, 'completed reviewed lesson intent'):
            validate_brief(candidate, BANK, [brief(0)['topic']])

    def test_used_fact_is_still_available_for_a_genuinely_new_reviewed_decision(self):
        candidate = brief(0)
        candidate.update(topic='A distinct proposed buyer decision', intent_key='different_decision')
        validated = validate_brief(candidate, BANK, [brief(0)['topic']])
        self.assertIn('knit_loop_stretch', validated['evidence'])


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
