from contextlib import redirect_stderr, redirect_stdout
import datetime
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools import voice_model
from tools import voice_model_watch as watch

ROOT = Path(__file__).resolve().parents[1]
DAY = datetime.date(2026, 11, 12)
KNOWN = ['eleven_flash_v2_5', 'eleven_multilingual_v2', 'eleven_v3', 'eleven_v4', 'eleven_v4_turbo']
KEY, TOKEN, CHAT = 'xi-KEY-SECRET-1', '123456:TOKEN-SECRET-2', '-100777SECRET3'
AGENTS = ('agent_SECRET_a', 'agent_SECRET_b', 'agent_SECRET_c')


def later(days):
    return DAY + datetime.timedelta(days=days)


def state(**changes):
    base = {'flagship': 'eleven_v4', 'realtime': 'eleven_v4_turbo',
            'previous': {'flagship': 'eleven_v3', 'realtime': 'eleven_flash_v2_5'},
            'known': list(KNOWN), 'pending': {}, 'log': []}
    base.update(changes)
    return base


def clip(model, index, size=16000):
    return b'ID3' + bytes([index]) + model.encode() + b'|' + b'a' * size


class FakeApi:
    def __init__(self, models=KNOWN, trained=(), agents=None):
        self.listing = [{'model_id': model, 'can_do_text_to_speech': True, 'can_be_finetuned': True} for model in models]
        self.states = {model: 'fine_tuned' for model in trained}
        self.heard, self.sizes, self.speech_status = {}, {}, {}
        self.train_answer = (200, {'status': 'ok'}, None)
        self.shows_training = True
        self.agent_map = {agent_id: dict(tts) for agent_id, tts in (agents or {}).items()}
        self.refuse, self.unreadable_agents, self.shallow, self.calls = {}, False, False, []

    def models(self):
        return self.listing

    def voice(self):
        return {'voice_id': watch.VOICE, 'fine_tuning': {'state': dict(self.states)}}

    def speak(self, model, text):
        self.calls.append(('speak', model))
        status = self.speech_status.get(model, 200)
        if status != 200:
            return status, b'{"detail":{"status":"model_access_denied","message":"private body text"}}'
        return 200, clip(model, watch.LINES.index(text), self.sizes.get(model, 16000))

    def transcribe(self, audio):
        self.calls.append(('transcribe',))
        model = audio[4:audio.index(b'|')].decode()
        return self.heard.get(model, watch.LINES)[audio[3]]

    def train(self, model):
        self.calls.append(('train', model))
        if self.train_answer[0] == 200 and self.shows_training:
            self.states[model] = 'fine_tuning'
        return self.train_answer

    def agents(self):
        return None if self.unreadable_agents else list(self.agent_map)

    def agent(self, agent_id):
        return {'agent_id': agent_id, 'conversation_config': {'tts': dict(self.agent_map[agent_id])}}

    def patch_agent(self, agent_id, tts):
        self.calls.append(('patch', agent_id, tts['model_id']))
        if self.refuse.get(agent_id) == tts['model_id']:
            return 422
        if self.shallow:
            self.agent_map[agent_id] = dict(tts)
        else:
            self.agent_map[agent_id].update(tts)
        return 200

    def count(self, name):
        return sum(call[0] == name for call in self.calls)


def go(start, api, today=DAY, **options):
    with redirect_stdout(StringIO()) as output:
        result = watch.run(start, api, today, **options)
    return (*result, output.getvalue())


class NamingTest(unittest.TestCase):
    def test_real_time_models_are_told_from_flagship_by_name(self):
        for model in ('eleven_v4_turbo', 'eleven_flash_v2_5', 'eleven_v3_conversational', 'eleven_v5_realtime'):
            self.assertEqual(watch.kind_of(model), 'realtime', model)
        for model in ('eleven_v4', 'eleven_v5', 'eleven_multilingual_v2', 'eleven_v4_5'):
            self.assertEqual(watch.kind_of(model), 'flagship', model)

    def test_generation_is_the_numbers_after_eleven_v(self):
        expected = {'eleven_v5': (5, 0), 'eleven_v4_5': (4, 5), 'eleven_v4_turbo': (4, 0), 'eleven_v4_5_turbo': (4, 5),
                    'eleven_v10': (10, 0), 'eleven_v3_conversational': (3, 0), 'eleven_flash_v2_5': None,
                    'eleven_multilingual_v2': None, 'eleven_nova': None, 'eleven_v5-beta': None, 'Eleven_V5': None,
                    'eleven_v5x': None, 'eleven_v5\n': None, '': None, None: None}
        for model, generation in expected.items():
            self.assertEqual(watch.generation(model), generation, model)

    def test_only_a_higher_generation_counts(self):
        for model, current, expected in [('eleven_v5', 'eleven_v4', True), ('eleven_v4_5', 'eleven_v4', True),
                                         ('eleven_v5', 'eleven_v4_5', True), ('eleven_v10', 'eleven_v9', True),
                                         ('eleven_v4', 'eleven_v4', False), ('eleven_v3', 'eleven_v4', False),
                                         ('eleven_v4_turbo', 'eleven_v4_turbo', False),
                                         ('eleven_v5_turbo', 'eleven_flash_v2_5', True), ('eleven_nova', 'eleven_v4', False)]:
            self.assertEqual(watch.higher(model, current), expected, (model, current))


class ChoiceTest(unittest.TestCase):
    def entries(self, *extra):
        return {model: {} for model in (*KNOWN, *extra)}

    def test_new_higher_models_are_queued_highest_first_per_kind(self):
        queue = watch.pick(state(), self.entries('eleven_v4_5', 'eleven_v5', 'eleven_v5_turbo', 'eleven_nova',
                                                 'eleven_v3_conversational', 'eleven_v5_hd'), DAY,
                           {'flagship': 'eleven_v4', 'realtime': 'eleven_v4_turbo'})
        self.assertEqual(queue, {'flagship': ['eleven_v5', 'eleven_v5_hd', 'eleven_v4_5'], 'realtime': ['eleven_v5_turbo']})

    def test_a_failed_model_is_retested_once_a_week_four_times_and_a_left_model_never(self):
        current = {'flagship': 'eleven_v4', 'realtime': 'eleven_v4_turbo'}
        failed = {'date': DAY.isoformat(), 'kind': 'flagship', 'from': 'eleven_v4', 'to': 'eleven_v5', 'result': 'failed'}
        start = state(known=KNOWN + ['eleven_v5'], log=[failed])
        entries = self.entries('eleven_v5')
        self.assertEqual(watch.pick(start, entries, later(6), current)['flagship'], [])
        self.assertEqual(watch.pick(start, entries, later(7), current)['flagship'], ['eleven_v5'])
        start['log'] = [dict(failed, date=later(7 * index).isoformat()) for index in range(5)]
        self.assertEqual(watch.pick(start, entries, later(400), current)['flagship'], [])
        start['log'] = [failed, {'kind': 'flagship', 'from': 'eleven_v4', 'to': 'eleven_v5', 'result': 'switched'}]
        self.assertEqual(watch.pick(start, entries, later(400), current)['flagship'], [])
        start = state(log=[{'kind': 'flagship', 'from': 'eleven_v5', 'to': 'eleven_v4', 'result': 'rolled_back'}])
        self.assertEqual(watch.pick(start, entries, DAY, current)['flagship'], [])


class ChecksTest(unittest.TestCase):
    def test_words_are_compared_without_case_punctuation_or_nukta(self):
        self.assertEqual(watch.normalise('बाक़ी Cities, में! पाँच।'), ['बाकी', 'cities', 'में', 'पांच'])
        self.assertEqual(watch.similarity('Sample order, करके पहले!', 'sample ORDER करके पहले।'), 1.0)
        self.assertAlmostEqual(watch.similarity('एक दो तीन चार', 'एक दो तीन पाँच'), 0.75)
        self.assertEqual(watch.similarity('', ''), 0.0)
        self.assertEqual(watch.similarity('एक दो', ''), 0.0)

    def test_words_and_speed_limits(self):
        good = [0.9, 0.9, 0.9, 0.9, 0.9]
        for kind, scores, speed, expected in [
                ('flagship', good, 1.0, ''), ('flagship', good, 0.90, ''), ('flagship', good, 1.10, ''),
                ('flagship', good, 0.899, 'speed'), ('flagship', good, 1.101, 'speed'), ('flagship', good, 1.27, 'speed'),
                ('realtime', good, 1.27, ''), ('realtime', good, 0.6, ''),
                ('flagship', [0.85] * 5, 1.0, ''), ('flagship', [0.84] * 5, 1.0, 'words'),
                ('flagship', [1, 1, 1, 1, 0.70], 1.0, ''), ('flagship', [1, 1, 1, 1, 0.69], 1.0, 'words'),
                ('realtime', [1, 1, 1, 1, 0.69], 1.0, 'words'), ('flagship', [0.5] * 5, 1.5, 'words')]:
            self.assertEqual(watch.judge(kind, scores, speed), expected, (kind, scores, speed))

    def test_scores_are_never_rounded_up_past_a_limit(self):
        self.assertEqual([watch.down(score) for score in (0.8496, 0.85, 0.6999, 0.7, 1.0, 0.0)], [0.849, 0.85, 0.699, 0.7, 1.0, 0.0])

    def test_clip_length_comes_from_its_size_at_128_kbps(self):
        self.assertEqual(watch.seconds(b'a' * 16000), 1.0)
        self.assertTrue(watch.is_mp3(200, b'ID3' + b'a' * 2000))
        self.assertTrue(watch.is_mp3(200, b'\xff\xfb' + b'a' * 2000))
        for status, audio in [(200, b''), (200, b'{"detail":"x"}' * 200), (403, b'ID3' + b'a' * 2000)]:
            self.assertFalse(watch.is_mp3(status, audio))

    def test_clone_state_reads_the_real_answer_shapes(self):
        def voice(value):
            return {'fine_tuning': {'state': {'eleven_v5': value} if value else {}}}
        for value, entry, expected in [
                ('fine_tuned', {}, 'trained'), ('queued', {}, 'training'), ('fine_tuning', {}, 'training'),
                ('delayed', {}, 'training'), ('failed', {}, 'failed'), ('not_started', {}, 'untrained'),
                (None, {'can_be_finetuned': True}, 'untrained'), (None, {'can_be_finetuned': False}, 'trained'),
                ('not_started', {'can_be_finetuned': False}, 'trained'), ('something_new', {}, 'unknown')]:
            self.assertEqual(watch.clone_state(voice(value), 'eleven_v5', entry), expected, (value, entry))
        self.assertEqual(watch.clone_state({}, 'eleven_v5', {}), 'untrained')


class RunTest(unittest.TestCase):
    def test_first_run_seeds_the_list_and_says_nothing(self):
        api = FakeApi()
        after, notes, blind, _ = go(state(known=[]), api)
        self.assertEqual((after['known'], notes, blind, api.calls), (sorted(KNOWN), [], False, []))
        self.assertEqual((after['flagship'], after['realtime']), ('eleven_v4', 'eleven_v4_turbo'))

    def test_a_normal_day_changes_nothing_and_says_nothing(self):
        start, api = state(), FakeApi()
        after, notes, blind, _ = go(start, api)
        self.assertEqual((after, notes, blind, api.calls), (start, [], False, []))

    def test_a_model_that_is_not_a_newer_generation_is_named_once_and_never_tested(self):
        api = FakeApi(KNOWN + ['eleven_nova', 'eleven_v4_hd'])
        after, notes, blind, _ = go(state(), api)
        self.assertEqual(len(notes), 1)
        self.assertIn('eleven_nova, eleven_v4_hd', notes[0]['text'])
        self.assertIn('stays on eleven_v4', notes[0]['text'])
        self.assertEqual((api.calls, blind, after['flagship']), ([], False, 'eleven_v4'))
        self.assertEqual(go(after, api)[1], [])

    def test_unreadable_model_list_is_blind_and_changes_nothing(self):
        for listing in (None, [], [{'model_id': 'eleven_v5', 'can_do_text_to_speech': False}], {'detail': 'x'}):
            api = FakeApi()
            api.listing = listing
            after, notes, blind, _ = go(state(), api)
            self.assertEqual((after, blind, notes[0]['text']), (state(), True, watch.BLIND))

    def test_a_newer_flagship_that_passes_takes_over_and_the_old_one_is_kept_for_the_way_back(self):
        api = FakeApi(KNOWN + ['eleven_v5'], trained=['eleven_v5'])
        after, notes, blind, _ = go(state(), api)
        self.assertEqual((after['flagship'], after['previous']['flagship'], after['realtime']),
                         ('eleven_v5', 'eleven_v4', 'eleven_v4_turbo'))
        self.assertEqual(after['log'], [{'date': '2026-11-12', 'kind': 'flagship', 'from': 'eleven_v4', 'to': 'eleven_v5',
                                         'words': 1.0, 'words_min': 1.0, 'speed': 1.0, 'result': 'switched'}])
        self.assertIn('eleven_v5', after['known'])
        self.assertEqual(notes[0]['text'], 'Voice moved to the new ElevenLabs model eleven_v5 (was eleven_v4). Words check '
                                           '100%. Listen: clip A = old, clip B = new.\n'
                                           "To go back: tell any AI session 'voice back to eleven_v4'.")
        self.assertEqual([title for title, _ in notes[0]['clips']],
                         ['A - old voice (eleven_v4)', 'B - new voice (eleven_v5)'])
        self.assertEqual([audio[4:13] for _, audio in notes[0]['clips']], [b'eleven_v4', b'eleven_v5'])
        self.assertEqual((api.count('speak'), api.count('transcribe'), api.count('train'), blind), (10, 10, 0, False))
        self.assertEqual(go(after, api)[1], [])

    def test_changed_words_keep_the_voice_and_the_model_is_not_tested_again_for_a_week(self):
        api = FakeApi(KNOWN + ['eleven_v5'], trained=['eleven_v5'])
        api.heard['eleven_v5'] = ['alpha beta gamma'] * 5
        after, notes, blind, _ = go(state(), api)
        self.assertEqual((after['flagship'], after['previous']['flagship'], blind), ('eleven_v4', 'eleven_v3', False))
        self.assertEqual((after['log'][0]['result'], after['log'][0]['reason']), ('failed', 'words'))
        self.assertIn('It did not pass: it does not say our words the same way (words check 0%, needs 85%)', notes[0]['text'])
        self.assertIn('Voice stays on eleven_v4. I will test it again in 7 days.', notes[0]['text'])
        self.assertEqual(notes[0]['clips'], [])
        before = len(api.calls)
        self.assertEqual(go(after, api, later(6))[1], [])
        self.assertEqual(len(api.calls), before)
        for attempt in range(1, 5):
            after, notes, _, _ = go(after, api, later(7 * attempt))
            self.assertEqual(len(watch.fails(after, 'eleven_v5')), attempt + 1)
        self.assertIn('I will not test it again by myself.', notes[0]['text'])
        before = len(api.calls)
        self.assertEqual(go(after, api, later(400))[1], [])
        self.assertEqual(len(api.calls), before)

    def test_one_badly_said_line_is_enough_to_fail(self):
        api = FakeApi(KNOWN + ['eleven_v5'], trained=['eleven_v5'])
        words = watch.LINES[4].split()
        api.heard['eleven_v5'] = list(watch.LINES[:4]) + [' '.join(words[:8] + ['alpha'] * 8)]
        after, notes, _, _ = go(state(), api)
        self.assertEqual((after['flagship'], after['log'][0]['reason']), ('eleven_v4', 'words'))
        self.assertEqual((after['log'][0]['words'], after['log'][0]['words_min']), (0.896, 0.484))
        self.assertIn('it said one test line differently (that line 48%, needs 70%)', notes[0]['text'])

    def test_a_slower_flagship_is_not_switched_but_a_real_time_model_has_no_speed_gate(self):
        api = FakeApi(KNOWN + ['eleven_v5'], trained=['eleven_v5'])
        api.sizes['eleven_v5'] = 20320
        after, notes, _, _ = go(state(), api)
        self.assertEqual((after['flagship'], after['log'][0]['reason'], after['log'][0]['speed']), ('eleven_v4', 'speed', 1.27))
        self.assertIn('it speaks 27% slower than the voice now, so an AI session must first change the Shorts script length',
                      notes[0]['text'])
        api = FakeApi(KNOWN + ['eleven_v5_turbo'], trained=['eleven_v5_turbo'])
        api.sizes['eleven_v5_turbo'] = 20320
        after, notes, _, _ = go(state(), api)
        self.assertEqual((after['realtime'], after['previous']['realtime'], after['flagship']),
                         ('eleven_v5_turbo', 'eleven_v4_turbo', 'eleven_v4'))
        self.assertIn('Live call voice moved to the new ElevenLabs model eleven_v5_turbo (was eleven_v4_turbo).', notes[0]['text'])

    def test_the_highest_generation_is_tried_first_and_a_pass_ends_the_run_for_that_kind(self):
        api = FakeApi(KNOWN + ['eleven_v4_5', 'eleven_v5'], trained=['eleven_v4_5', 'eleven_v5'])
        after, notes, _, _ = go(state(), api)
        self.assertEqual(after['flagship'], 'eleven_v5')
        self.assertNotIn(('speak', 'eleven_v4_5'), api.calls)
        self.assertIn('ElevenLabs added: eleven_v4_5.', notes[1]['text'])
        api = FakeApi(KNOWN + ['eleven_v4_5', 'eleven_v5'], trained=['eleven_v4_5', 'eleven_v5'])
        api.heard['eleven_v5'] = ['गलत'] * 5
        after, notes, _, _ = go(state(), api)
        self.assertEqual((after['flagship'], [row['result'] for row in after['log']]), ('eleven_v4_5', ['failed', 'switched']))

    def test_untrained_clone_is_trained_once_then_checked_when_ready(self):
        api = FakeApi(KNOWN + ['eleven_v5'])
        after, notes, blind, _ = go(state(), api)
        self.assertEqual((after['flagship'], blind, api.count('train'), api.count('speak')), ('eleven_v4', False, 1, 0))
        self.assertEqual(after['pending'], {'eleven_v5': {'kind': 'flagship', 'since': '2026-11-12', 'told': '2026-11-12',
                                                          'state': 'asked', 'train_started': True}})
        self.assertIn('I asked ElevenLabs to train your voice on it and will test it tomorrow. Voice stays on eleven_v4',
                      notes[0]['text'])
        after, notes, _, _ = go(after, api, later(1))
        self.assertEqual((notes, api.count('train'), api.count('speak')), ([], 1, 0))
        after, notes, _, _ = go(after, api, later(7))
        self.assertIn('Still waiting: your voice is not ready on the new ElevenLabs model eleven_v5 (7 days)', notes[0]['text'])
        self.assertEqual(go(after, api, later(8))[1], [])
        api.states['eleven_v5'] = 'fine_tuned'
        after, notes, _, _ = go(after, api, later(9))
        self.assertEqual((after['flagship'], after['pending'], api.count('train')), ('eleven_v5', {}, 1))
        self.assertIn('Voice moved to the new ElevenLabs model eleven_v5 (was eleven_v4)', notes[0]['text'])

    def test_failed_training_is_a_fail_and_restarts_only_at_the_weekly_retest(self):
        api = FakeApi(KNOWN + ['eleven_v5'])
        after = go(state(), api)[0]
        api.states['eleven_v5'] = 'failed'
        after, notes, _, _ = go(after, api, later(1))
        self.assertEqual((after['pending'], after['log'][0]['reason'], after['flagship']), ({}, 'training', 'eleven_v4'))
        self.assertIn('training your voice on it failed at ElevenLabs', notes[0]['text'])
        self.assertEqual(go(after, api, later(2))[1], [])
        self.assertEqual(api.count('train'), 1)
        after = go(after, api, later(8))[0]
        self.assertEqual((api.count('train'), sorted(after['pending'])), (2, ['eleven_v5']))

    def test_a_model_elevenlabs_shows_no_training_for_is_checked_the_next_day(self):
        api = FakeApi(KNOWN + ['eleven_v5'])
        api.shows_training = False
        after, notes, _, _ = go(state(), api)
        self.assertEqual((after['flagship'], after['pending']['eleven_v5']['state'], api.count('speak')), ('eleven_v4', 'asked', 0))
        after, notes, blind, _ = go(after, api, later(1))
        self.assertEqual((after['flagship'], after['pending'], blind, api.count('train')), ('eleven_v5', {}, False, 1))
        self.assertIn('Voice moved to the new ElevenLabs model eleven_v5 (was eleven_v4)', notes[0]['text'])

    def test_training_that_hangs_two_weeks_is_checked_as_it_is(self):
        api = FakeApi(KNOWN + ['eleven_v5'])
        after = go(state(), api)[0]
        after = go(after, api, later(13))[0]
        self.assertEqual((after['flagship'], sorted(after['pending'])), ('eleven_v4', ['eleven_v5']))
        self.assertEqual(go(after, api, later(14))[0]['flagship'], 'eleven_v5')

    def test_a_refused_training_request_goes_straight_to_the_voice_check(self):
        api = FakeApi(KNOWN + ['eleven_v5'])
        api.train_answer = (400, {'detail': {'status': 'some_new_code', 'message': 'private body text'}}, 'some_new_code')
        after, notes, blind, output = go(state(), api)
        self.assertEqual((after['flagship'], blind, after['pending'], api.count('train')), ('eleven_v5', False, {}, 1))
        self.assertIn('some_new_code', output)
        self.assertNotIn('private body text', output)
        api = FakeApi(KNOWN + ['eleven_v5'])
        api.states['eleven_v5'] = 'something_new'
        after, notes, blind, _ = go(state(), api)
        self.assertEqual((after['flagship'], blind, after['pending']['eleven_v5']['train_started']), ('eleven_v4', False, False))
        self.assertIn('a training state this check does not know', notes[0]['text'])
        api = FakeApi(KNOWN + ['eleven_v5'])
        api.train_answer = (503, {}, None)
        after, notes, blind, _ = go(state(), api)
        self.assertEqual((blind, after['pending'], 'eleven_v5' in after['known']), (True, {}, False))

    def test_a_model_that_takes_no_fine_tune_is_checked_at_once(self):
        api = FakeApi(KNOWN + ['eleven_v5'])
        api.listing[-1]['can_be_finetuned'] = False
        after, _, _, _ = go(state(), api)
        self.assertEqual((after['flagship'], api.count('train')), ('eleven_v5', 0))

    def test_candidate_refused_is_a_fail_but_a_dead_check_is_blind_and_retried_next_day(self):
        api = FakeApi(KNOWN + ['eleven_v5'], trained=['eleven_v5'])
        api.speech_status['eleven_v5'] = 403
        after, notes, blind, output = go(state(), api)
        self.assertEqual((after['log'][0]['reason'], blind, api.count('speak')), ('refused', False, 1))
        self.assertIn('ElevenLabs does not let us use it yet', notes[0]['text'])
        self.assertNotIn('private body text', output)
        for broken, status in (('eleven_v4', 403), ('eleven_v5', 503), ('eleven_v4', 0)):
            api = FakeApi(KNOWN + ['eleven_v5'], trained=['eleven_v5'])
            api.speech_status[broken] = status
            after, notes, blind, _ = go(state(), api)
            self.assertEqual((after['flagship'], after['log'], blind, 'eleven_v5' in after['known']),
                             ('eleven_v4', [], True, False))
            self.assertIn('the test could not finish today because ElevenLabs did not answer. Voice stays on eleven_v4',
                          notes[0]['text'])
        api = FakeApi(KNOWN + ['eleven_v5'], trained=['eleven_v5'])
        api.heard['eleven_v5'] = [None] * 5
        self.assertEqual(go(state(), api)[2], True)

    def test_dry_run_starts_no_training_and_changes_no_agent(self):
        api = FakeApi(KNOWN + ['eleven_v5'])
        after, notes, _, output = go(state(), api, dry=True)
        self.assertEqual(api.count('train'), 0)
        self.assertIn('a real run would first ask ElevenLabs to train the clone on it', output)
        self.assertIn('Voice moved to the new ElevenLabs model eleven_v5', notes[0]['text'])
        agents = {AGENTS[0]: {'voice_id': watch.VOICE, 'model_id': 'eleven_v4_turbo'}}
        api = FakeApi(KNOWN + ['eleven_v5_turbo'], trained=['eleven_v5_turbo'], agents=agents)
        _, notes, _, _ = go(state(), api, dry=True)
        self.assertEqual((api.count('patch'), api.agent_map), (0, agents))
        self.assertIn('1 voice agent moved.', notes[0]['text'])

    def test_proof_run_pretends_the_old_model_and_forces_todays_model_through_the_whole_path(self):
        api = FakeApi(trained=['eleven_v3', 'eleven_v4'])
        after, notes, blind, _ = go(state(known=[]), api, dry=True, pretend={'flagship': 'eleven_v3'}, forced='eleven_v4')
        self.assertEqual((api.count('speak'), api.count('transcribe'), blind), (10, 10, False))
        self.assertIn('Voice moved to the new ElevenLabs model eleven_v4 (was eleven_v3). Words check 100%.', notes[0]['text'])
        self.assertEqual(len(notes), 1)
        api = FakeApi(trained=['eleven_v3', 'eleven_v4'])
        self.assertEqual(go(state(), api, dry=True, pretend={'flagship': 'eleven_v3'})[0]['flagship'], 'eleven_v4')

    def test_forced_model_must_be_listed_and_must_not_be_the_one_in_use(self):
        api = FakeApi()
        after, notes, blind, _ = go(state(), api, forced='eleven_v9')
        self.assertEqual((after, blind, api.calls), (state(), False, []))
        self.assertIn("eleven_v9 is not in ElevenLabs' list", notes[0]['text'])
        after, notes, blind, _ = go(state(), api, forced='eleven_v4')
        self.assertEqual((after, blind, api.calls), (state(), False, []))
        self.assertIn('Voice is already on eleven_v4', notes[0]['text'])

    def test_forced_test_leaves_a_real_newer_model_for_the_next_normal_run(self):
        api = FakeApi(KNOWN + ['eleven_v5'], trained=['eleven_v3', 'eleven_v5'])
        after, _, _, _ = go(state(), api, forced='eleven_v3')
        self.assertNotIn('eleven_v5', after['known'])
        self.assertEqual(go(after, api, later(1))[0]['flagship'], 'eleven_v5')


class AgentsTest(unittest.TestCase):
    def agents(self):
        return {AGENTS[0]: {'voice_id': watch.VOICE, 'model_id': 'eleven_v4_turbo', 'stability': 0.4, 'speed': 1.05},
                AGENTS[1]: {'voice_id': watch.VOICE, 'model_id': 'eleven_v4_turbo'},
                AGENTS[2]: {'voice_id': watch.VOICE, 'model_id': 'eleven_v4_turbo'},
                'other_voice': {'voice_id': 'someone-else', 'model_id': 'eleven_v4_turbo'},
                'other_model': {'voice_id': watch.VOICE, 'model_id': 'eleven_flash_v2_5'}}

    def test_real_time_switch_moves_only_his_agents_on_the_old_model(self):
        api = FakeApi(KNOWN + ['eleven_v5_turbo'], trained=['eleven_v5_turbo'], agents=self.agents())
        after, notes, blind, output = go(state(), api)
        self.assertEqual((after['realtime'], after['previous']['realtime'], blind), ('eleven_v5_turbo', 'eleven_v4_turbo', False))
        self.assertEqual([api.agent_map[agent_id]['model_id'] for agent_id in AGENTS], ['eleven_v5_turbo'] * 3)
        self.assertEqual(api.agent_map[AGENTS[0]], {'voice_id': watch.VOICE, 'model_id': 'eleven_v5_turbo', 'stability': 0.4, 'speed': 1.05})
        self.assertEqual(api.agent_map['other_voice']['model_id'], 'eleven_v4_turbo')
        self.assertEqual(api.agent_map['other_model']['model_id'], 'eleven_flash_v2_5')
        self.assertIn('Words check 100%. 3 voice agents moved. Listen: clip A = old, clip B = new.', notes[0]['text'])
        for agent_id in AGENTS:
            self.assertNotIn(agent_id, output + json.dumps(notes[0]['text']) + json.dumps(after))

    def test_a_refused_agent_puts_every_agent_back_and_keeps_the_model(self):
        api = FakeApi(KNOWN + ['eleven_v5_turbo'], trained=['eleven_v5_turbo'], agents=self.agents())
        api.refuse[AGENTS[1]] = 'eleven_v5_turbo'
        after, notes, blind, output = go(state(), api)
        self.assertEqual((after['realtime'], after['previous']['realtime'], blind), ('eleven_v4_turbo', 'eleven_flash_v2_5', False))
        self.assertEqual(api.agent_map, self.agents())
        self.assertEqual([call for call in api.calls if call[0] == 'patch'],
                         [('patch', AGENTS[0], 'eleven_v5_turbo'), ('patch', AGENTS[1], 'eleven_v5_turbo'),
                          ('patch', AGENTS[0], 'eleven_v4_turbo'), ('patch', AGENTS[1], 'eleven_v4_turbo')])
        self.assertEqual((after['log'][0]['result'], after['log'][0]['reason'], after['log'][0]['words']), ('failed', 'agents', 1.0))
        self.assertIn('It did not pass: the voice agents did not accept it. Live call voice stays on eleven_v4_turbo.',
                      notes[0]['text'])
        self.assertNotIn('could not be put back', notes[0]['text'])
        for agent_id in AGENTS:
            self.assertNotIn(agent_id, output + notes[0]['text'])

    def test_an_agent_that_cannot_be_put_back_is_said_loudly(self):
        api = FakeApi(KNOWN + ['eleven_v5_turbo'], trained=['eleven_v5_turbo'], agents=self.agents())
        api.refuse[AGENTS[1]] = 'eleven_v5_turbo'
        api.refuse[AGENTS[0]] = 'eleven_v4_turbo'
        after, notes, blind, _ = go(state(), api)
        self.assertEqual((after['realtime'], blind), ('eleven_v4_turbo', True))
        self.assertIn('1 voice agent could not be put back. Please tell an AI session now.', notes[0]['text'])

    def test_a_change_that_wipes_the_agent_voice_is_caught_and_the_whole_voice_setting_restored(self):
        api = FakeApi(KNOWN + ['eleven_v5_turbo'], trained=['eleven_v5_turbo'], agents=self.agents())
        api.shallow = True
        after, notes, blind, output = go(state(), api)
        self.assertEqual((after['realtime'], after['log'][0]['reason'], blind), ('eleven_v4_turbo', 'agents', False))
        self.assertEqual(api.agent_map, self.agents())
        self.assertEqual(api.count('patch'), 2)

    def test_unreadable_agents_change_nothing_and_are_retried(self):
        api = FakeApi(KNOWN + ['eleven_v5_turbo'], trained=['eleven_v5_turbo'], agents=self.agents())
        api.unreadable_agents = True
        after, notes, blind, _ = go(state(), api)
        self.assertEqual((after['realtime'], after['log'], blind, api.count('patch')), ('eleven_v4_turbo', [], True, 0))
        self.assertNotIn('eleven_v5_turbo', after['known'])


class RollbackTest(unittest.TestCase):
    def back(self, start, kind, api, **options):
        with redirect_stdout(StringIO()):
            return watch.rollback(start, kind, api, later(3), **options)

    def test_flagship_goes_back_and_is_never_auto_switched_to_that_model_again(self):
        api = FakeApi(KNOWN + ['eleven_v5'], trained=['eleven_v5'])
        moved = go(state(), api)[0]
        after, notes, failed = self.back(moved, 'flagship', api)
        self.assertEqual((after['flagship'], after['previous']['flagship'], failed), ('eleven_v4', 'eleven_v5', False))
        self.assertEqual(after['log'][-1], {'date': '2026-11-15', 'kind': 'flagship', 'from': 'eleven_v5', 'to': 'eleven_v4',
                                            'result': 'rolled_back'})
        self.assertEqual(notes[0]['text'], 'Voice is back on eleven_v4 (was eleven_v5). '
                                           'The daily check will not move to eleven_v5 again by itself.')
        before = len(api.calls)
        again, notes, _, _ = go(after, api, later(30))
        self.assertEqual((again['flagship'], notes, len(api.calls)), ('eleven_v4', [], before))

    def test_real_time_goes_back_with_its_agents_or_not_at_all(self):
        agents = {AGENTS[0]: {'voice_id': watch.VOICE, 'model_id': 'eleven_v4_turbo'},
                  AGENTS[1]: {'voice_id': watch.VOICE, 'model_id': 'eleven_v4_turbo'}}
        api = FakeApi(agents=agents)
        after, notes, failed = self.back(state(), 'realtime', api)
        self.assertEqual((after['realtime'], after['previous']['realtime'], failed), ('eleven_flash_v2_5', 'eleven_v4_turbo', False))
        self.assertEqual({tts['model_id'] for tts in api.agent_map.values()}, {'eleven_flash_v2_5'})
        self.assertIn('Live call voice is back on eleven_flash_v2_5 (was eleven_v4_turbo). 2 voice agents moved.', notes[0]['text'])
        api = FakeApi(agents=agents)
        api.refuse[AGENTS[1]] = 'eleven_flash_v2_5'
        after, notes, failed = self.back(state(), 'realtime', api)
        self.assertEqual((after, failed, api.agent_map), (state(), True, agents))
        self.assertIn('could not go back to eleven_flash_v2_5', notes[0]['text'])
        api = FakeApi(agents=agents)
        after, _, _ = self.back(state(), 'realtime', api, dry=True)
        self.assertEqual((api.count('patch'), api.agent_map), (0, agents))

    def test_nothing_saved_to_go_back_to(self):
        after, notes, failed = self.back(state(previous={}), 'flagship', FakeApi())
        self.assertEqual((after['flagship'], failed), ('eleven_v4', True))
        self.assertIn('No earlier model is saved', notes[0]['text'])


class ReaderTest(unittest.TestCase):
    def test_reader_returns_the_file_value_and_falls_back_on_anything_odd(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'voice_models.json'
            self.assertEqual((voice_model.flagship(root=folder), voice_model.realtime(root=folder)), ('eleven_v4', 'eleven_v4_turbo'))
            for content in ('', 'not json', '[]', '"eleven_v5"', '{}', '{"flagship": 5, "realtime": null}',
                            '{"flagship": "Eleven V5", "realtime": "eleven_v5_turbo\\n"}',
                            '{"flagship": "", "realtime": "gpt-voice"}', '{"flagship": ["eleven_v5"], "realtime": {}}'):
                path.write_text(content)
                self.assertEqual((voice_model.flagship(root=folder), voice_model.realtime(root=folder)),
                                 ('eleven_v4', 'eleven_v4_turbo'), content)
            path.write_text('{"flagship": "eleven_v5", "realtime": "eleven_v5_turbo"}')
            self.assertEqual((voice_model.flagship(root=folder), voice_model.realtime(root=folder)), ('eleven_v5', 'eleven_v5_turbo'))
            path.write_text('{"flagship": "broken value"}')
            self.assertEqual((voice_model.flagship('eleven_v3', root=folder), voice_model.realtime(default='eleven_x', root=folder)),
                             ('eleven_v3', 'eleven_x'))
        self.assertEqual(voice_model.flagship(root='/nonexistent/folder'), 'eleven_v4')

    def test_repo_file_is_valid_and_the_reader_agrees_with_it(self):
        saved = json.loads((ROOT/'voice_models.json').read_text())
        self.assertEqual(set(saved), {'flagship', 'realtime', 'previous', 'known', 'pending', 'log'})
        self.assertEqual((voice_model.flagship(), voice_model.realtime()), (saved['flagship'], saved['realtime']))
        self.assertEqual(watch.load_state(), {**saved, 'known': sorted(saved['known'])})

    def test_state_loader_repairs_odd_values_and_refuses_a_non_object(self):
        with TemporaryDirectory() as folder:
            path = str(Path(folder)/'voice_models.json')
            Path(path).write_text(json.dumps({'flagship': 'bad value', 'previous': {'flagship': 7, 'realtime': 'eleven_flash_v2_5'},
                                              'known': ['b', 'a', 3], 'pending': {'x': 1}, 'log': ['x', {'to': 'y'}], 'extra': 1}))
            self.assertEqual(watch.load_state(path), {
                'flagship': 'eleven_v4', 'realtime': 'eleven_v4_turbo', 'previous': {'realtime': 'eleven_flash_v2_5'},
                'known': ['a', 'b'], 'pending': {}, 'log': [{'to': 'y'}], 'extra': 1})
            Path(path).write_text('[]')
            with self.assertRaises(ValueError):
                watch.load_state(path)


class Wire:
    def __init__(self, models, trained=(), agents=None, telegram_ok=True, refuse_stt=()):
        self.models, self.trained, self.telegram_ok, self.refuse_stt = models, trained, telegram_ok, refuse_stt
        self.agent_map = {agent_id: dict(tts) for agent_id, tts in (agents or {}).items()}
        self.calls = []

    def __call__(self, method, url, headers=None, data=None, timeout=120):
        self.calls.append((method, url, dict(headers or {}), data))
        if url.startswith('https://api.telegram.org/'):
            return (200, b'{"ok":true}') if self.telegram_ok else (401, b'{"ok":false,"description":"private body text"}')
        path = url[len(watch.API):]
        if path == 'models':
            return 200, json.dumps([{'model_id': model, 'can_do_text_to_speech': True} for model in self.models]).encode()
        if path == 'voices/' + watch.VOICE:
            return 200, json.dumps({'fine_tuning': {'state': {model: 'fine_tuned' for model in self.trained}}}).encode()
        if path.startswith('text-to-speech/'):
            body = json.loads(data)
            return 200, clip(body['model_id'], watch.LINES.index(body['text']))
        if path == 'speech-to-text':
            model = data.split(b'name="model_id"\r\n\r\n', 1)[1].split(b'\r\n', 1)[0].decode()
            if model in self.refuse_stt:
                return 400, b'{"detail":{"status":"invalid_model_id","message":"private body text"}}'
            audio = data[data.index(b'ID3'):]
            return 200, json.dumps({'text': watch.LINES[audio[3]], 'language_code': 'hi'}).encode()
        if path.startswith('convai/agents?'):
            return 200, json.dumps({'agents': [{'agent_id': agent_id, 'name': 'x'} for agent_id in self.agent_map],
                                    'has_more': False, 'next_cursor': None}).encode()
        if path.startswith('convai/agents/'):
            agent_id = path.rsplit('/', 1)[1]
            if method == 'PATCH':
                self.agent_map[agent_id].update(json.loads(data)['conversation_config']['tts'])
            return 200, json.dumps({'agent_id': agent_id, 'conversation_config': {'tts': self.agent_map[agent_id]}}).encode()
        return 404, b'{}'


class MainTest(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.file = str(Path(self.folder.name)/'voice_models.json')
        Path(self.file).write_text(json.dumps(state()))

    def main(self, wire, argv, actions=False, environment=None):
        env = {'ELEVENLABS_API_KEY': KEY, 'TELEGRAM_BOT_TOKEN': TOKEN, 'TELEGRAM_ALERT_CHAT_ID': CHAT,
               'GITHUB_ACTIONS': 'true' if actions else '', 'GITHUB_OUTPUT': '', **(environment or {})}
        output = StringIO()
        with patch.dict('os.environ', env), patch.object(watch, 'http', wire), patch.object(watch, 'STATE_FILE', self.file), \
                patch.object(watch.time, 'sleep'), redirect_stdout(output), redirect_stderr(output):
            code = watch.main(argv)
        return code, output.getvalue()

    def saved(self):
        return json.loads(Path(self.file).read_text())

    def agents(self):
        return {agent_id: {'voice_id': watch.VOICE, 'model_id': 'eleven_v4_turbo'} for agent_id in AGENTS}

    def test_real_time_switch_end_to_end_uses_the_documented_calls_and_prints_no_secret(self):
        wire = Wire(KNOWN + ['eleven_v5_turbo'], trained=['eleven_v5_turbo'], agents=self.agents())
        code, output = self.main(wire, [])
        self.assertEqual((code, self.saved()['realtime'], self.saved()['previous']['realtime']), (0, 'eleven_v5_turbo', 'eleven_v4_turbo'))
        for secret in (KEY, TOKEN, CHAT, *AGENTS, 'api.telegram.org', 'private body text'):
            self.assertNotIn(secret, output)
        eleven = [call for call in wire.calls if call[1].startswith(watch.API)]
        self.assertTrue(all(call[2]['xi-api-key'] == KEY for call in eleven))
        speech = [call for call in eleven if '/text-to-speech/' in call[1]]
        self.assertEqual(len(speech), 10)
        self.assertTrue(all(call[1] == f'{watch.API}text-to-speech/{watch.VOICE}?output_format=mp3_44100_128' for call in speech))
        self.assertEqual(json.loads(speech[0][3]), {'text': watch.LINES[0], 'model_id': 'eleven_v5_turbo',
                                                    'voice_settings': {'stability': 0.5, 'similarity_boost': 0.9}})
        heard = [call for call in eleven if call[1] == watch.API + 'speech-to-text']
        self.assertEqual(len(heard), 10)
        self.assertTrue(heard[0][2]['Content-Type'].startswith('multipart/form-data; boundary='))
        for field in (b'name="model_id"\r\n\r\nscribe_v2\r\n', b'name="language_code"\r\n\r\nhi\r\n',
                      b'name="file"; filename="clip.mp3"\r\nContent-Type: audio/mpeg\r\n\r\nID3'):
            self.assertIn(field, heard[0][3])
        patches = [call for call in eleven if call[0] == 'PATCH']
        self.assertEqual([call[1] for call in patches], [f'{watch.API}convai/agents/{agent_id}' for agent_id in AGENTS])
        self.assertEqual(json.loads(patches[0][3]), {'conversation_config': {'tts': {'model_id': 'eleven_v5_turbo'}}})
        told = [call for call in wire.calls if call[1].startswith('https://api.telegram.org/')]
        self.assertEqual([call[1].rsplit('/', 1)[1] for call in told], ['sendMessage', 'sendAudio', 'sendAudio'])
        self.assertIn(b'Live+call+voice+moved+to+the+new+ElevenLabs+model+eleven_v5_turbo', told[0][3])
        self.assertIn(b'name="audio"; filename="A.mp3"', told[1][3])

    def test_on_github_agent_ids_appear_only_as_mask_commands(self):
        wire = Wire(KNOWN + ['eleven_v5_turbo'], trained=['eleven_v5_turbo'], agents=self.agents())
        code, output = self.main(wire, [], actions=True)
        self.assertEqual(code, 0)
        lines = [line for line in output.splitlines() if any(agent_id in line for agent_id in AGENTS)]
        self.assertEqual(sorted(lines), sorted('::add-mask::' + agent_id for agent_id in AGENTS))
        for secret in (KEY, TOKEN, CHAT):
            self.assertNotIn(secret, output)

    def test_normal_day_is_silent_exit_zero_and_first_run_only_seeds(self):
        wire = Wire(KNOWN)
        code, _ = self.main(wire, [])
        self.assertEqual((code, self.saved(), [call for call in wire.calls if 'telegram' in call[1]]), (0, state(), []))
        Path(self.file).write_text(json.dumps(state(known=[])))
        code, _ = self.main(wire, [])
        self.assertEqual((code, self.saved()['known'], [call for call in wire.calls if 'telegram' in call[1]]), (0, sorted(KNOWN), []))

    def test_blind_days_exit_non_zero_and_tell_the_owner(self):
        wire = Wire(KNOWN)
        wire.models = None
        with patch.object(Wire, '__call__', lambda self, method, url, headers=None, data=None, timeout=120:
                          (self.calls.append((method, url, headers, data)), (200, b'{"ok":true}') if 'telegram' in url else (0, b''))[1]):
            code, output = self.main(wire, [])
        self.assertEqual((code, self.saved()), (1, state()))
        self.assertIn(b'could+not+read+ElevenLabs+today', wire.calls[-1][3])
        code, output = self.main(Wire(KNOWN), [], environment={'ELEVENLABS_API_KEY': ''})
        self.assertEqual(code, 1)
        Path(self.file).write_text('{broken')
        wire = Wire(KNOWN)
        code, output = self.main(wire, [])
        self.assertEqual(code, 1)
        self.assertIn(b'could+not+read+its+own+settings+file', wire.calls[-1][3])

    def test_dry_run_proof_prints_the_message_and_writes_and_sends_nothing(self):
        wire = Wire(KNOWN, trained=['eleven_v3', 'eleven_v4'])
        Path(self.file).write_text(json.dumps(state(known=[])))
        code, output = self.main(wire, ['--pretend-current', 'flagship=eleven_v3', '--force-candidate', 'eleven_v4',
                                        '--outbox', str(Path(self.folder.name)/'outbox')])
        self.assertEqual((code, self.saved()), (0, state(known=[])))
        self.assertIn('--- TELEGRAM (dry run, not sent) ---\nVoice moved to the new ElevenLabs model eleven_v4 (was eleven_v3). '
                      'Words check 100%.', output)
        self.assertIn('+ audio "A - old voice (eleven_v3)"', output)
        self.assertEqual([call for call in wire.calls if 'telegram' in call[1] or call[0] == 'PATCH' or '/train' in call[1]], [])
        self.assertFalse((Path(self.folder.name)/'outbox').exists())
        self.assertEqual(sum('/text-to-speech/' in call[1] for call in wire.calls), 10)

    def test_messages_wait_in_the_outbox_until_the_file_is_pushed(self):
        wire = Wire(KNOWN + ['eleven_v5'], trained=['eleven_v5'])
        outbox = str(Path(self.folder.name)/'outbox')
        code, _ = self.main(wire, ['--outbox', outbox])
        self.assertEqual((code, self.saved()['flagship'], [call for call in wire.calls if 'telegram' in call[1]]), (0, 'eleven_v5', []))
        result = Path(self.folder.name)/'github_output'
        code, output = self.main(wire, ['--send-outbox', outbox], environment={'GITHUB_OUTPUT': str(result)})
        self.assertEqual((code, result.read_text()), (0, 'told=1\n'))
        self.assertEqual([call[1].rsplit('/', 1)[1] for call in wire.calls if 'telegram' in call[1]],
                         ['sendMessage', 'sendAudio', 'sendAudio'])
        self.assertNotIn(TOKEN, output)
        result.write_text('')
        code, output = self.main(Wire(KNOWN, telegram_ok=False), ['--send-outbox', outbox], environment={'GITHUB_OUTPUT': str(result)})
        self.assertEqual((code, result.read_text()), (1, ''))
        self.assertNotIn('private body text', output)
        code, _ = self.main(wire, ['--send-outbox', str(Path(self.folder.name)/'none')], environment={'GITHUB_OUTPUT': str(result)})
        self.assertEqual((code, result.read_text()), (0, ''))

    def test_speech_to_text_falls_back_to_the_older_model_id(self):
        wire = Wire(KNOWN + ['eleven_v5'], trained=['eleven_v5'], refuse_stt=('scribe_v2',))
        code, output = self.main(wire, [])
        self.assertEqual((code, self.saved()['flagship']), (0, 'eleven_v5'))
        used = [call[3].split(b'name="model_id"\r\n\r\n', 1)[1].split(b'\r\n', 1)[0] for call in wire.calls
                if call[1] == watch.API + 'speech-to-text']
        self.assertEqual(used, [b'scribe_v2'] + [b'scribe_v1'] * 10)
        self.assertNotIn('private body text', output)

    def test_rollback_from_the_command_line(self):
        wire = Wire(KNOWN)
        code, _ = self.main(wire, ['--rollback', 'flagship'])
        self.assertEqual((code, self.saved()['flagship'], self.saved()['previous']['flagship']), (0, 'eleven_v3', 'eleven_v4'))
        self.assertIn(b'Voice+is+back+on+eleven_v3', wire.calls[-1][3])

    def test_bad_inputs_are_refused_before_anything_runs(self):
        for argv in (['--pretend-current', 'flagship=$(id)'], ['--pretend-current', 'other=eleven_v3'],
                     ['--force-candidate', 'eleven_v4; rm'], ['--rollback', 'everything']):
            wire = Wire(KNOWN)
            with self.assertRaises(SystemExit):
                self.main(wire, argv)
            self.assertEqual(wire.calls, [])


class RoutineTest(unittest.TestCase):
    def test_one_line_a_month_and_a_missed_days_alert(self):
        current = state()
        notes = watch.routine(current, DAY, 0)
        self.assertEqual((len(notes), current['heartbeat']), (1, '2026-11'))
        self.assertIn('Videos speak with eleven_v4, live calls with eleven_v4_turbo. You get this line once a month',
                      notes[0]['text'])
        self.assertEqual(watch.routine(current, later(1), 1), [])
        self.assertEqual(watch.routine(current, later(1), None), [])
        notes = watch.routine(current, later(5), 4)
        self.assertEqual(len(notes), 1)
        self.assertIn('had not run for 4 days', notes[0]['text'])
        notes = watch.routine(state(pending={'eleven_v5': {'kind': 'flagship'}}), later(30), 0)
        self.assertIn('A newer model is waiting for its check: eleven_v5.', notes[0]['text'])

    def test_last_run_age_needs_a_token_and_survives_any_answer(self):
        now = datetime.datetime(2026, 11, 12, 4, 0, tzinfo=datetime.timezone.utc)
        with patch.dict('os.environ', {'GITHUB_REPOSITORY': '', 'GH_TOKEN': ''}):
            self.assertIsNone(watch.last_run_days(now))
        env = {'GITHUB_REPOSITORY': 'o/r', 'GH_TOKEN': 'ghs_SECRET'}
        for body, expected in ((b'{"workflow_runs":[{"created_at":"2026-11-08T03:40:00Z"}]}', 4),
                               (b'{"workflow_runs":[]}', None), (b'not json', None), (b'[]', None)):
            with patch.dict('os.environ', env), patch.object(watch, 'http', lambda *a, **k: (200, body)), \
                    redirect_stdout(StringIO()) as output:
                self.assertEqual(watch.last_run_days(now), expected)
            self.assertNotIn('ghs_SECRET', output.getvalue())


class WorkflowTest(unittest.TestCase):
    def test_workflow_runs_daily_can_commit_and_never_fails_quietly(self):
        workflow = (ROOT/'.github/workflows/voice_model_watch.yml').read_text()
        self.assertIn("- cron: '35 3 * * *'", workflow)
        self.assertIn("- cron: '35 9 * * *'", workflow)
        self.assertIn('permissions:\n  contents: write\n  actions: read\n', workflow)
        self.assertIn('if [ "$EVENT" = "schedule" ]; then args+=(--scheduled); fi', workflow)
        for name in ('dry_run', 'pretend_current', 'force_candidate', 'rollback'):
            self.assertRegex(workflow, r'(?m)^      ' + name + ':$')
        self.assertIn("if: ${{ failure() && steps.tell.outputs.told != '1' }}", workflow)
        self.assertIn('git pull --rebase -q origin "$REF" && git push -q origin "HEAD:$REF"', workflow)
        scripts = workflow.split('run: |')[1:]
        self.assertTrue(all('${{ inputs.' not in script.split('\n      - ')[0] for script in scripts))
        self.assertLess(workflow.index('name: Save voice_models.json'), workflow.index('name: Tell the owner'))


if __name__ == '__main__':
    unittest.main()
