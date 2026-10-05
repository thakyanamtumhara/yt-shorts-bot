import base64
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from tools import clip_visual_check as check


PROMPTS = [f'STYLE LOCK: warm tungsten light, small Indian workshop. SCENE: scene {n}.' for n in range(1, 6)]


def ok():
    return {'state': 'pass', 'usable': True, 'verdict': 'pass', 'observed_visual': 'Hands fold a plain tee.',
            'shown_material': 'plain jersey T-shirt', 'problems': [], 'avoid': ''}


def bad(seen='Two braided cords stretched on dark fabric.'):
    return {'state': 'fail', 'usable': False, 'verdict': 'fail', 'observed_visual': seen,
            'shown_material': 'braided cord', 'avoid': 'no cords or trims; show a whole T-shirt hem at arm length',
            'problems': [{'kind': 'wrong_construction', 'detail': 'cords instead of a coverseam'}]}


def glitch():
    return {'state': 'fail', 'usable': False, 'verdict': 'fail', 'observed_visual': 'A price tag with garbled digits.',
            'shown_material': 'plain jersey', 'avoid': 'no tags or price tags',
            'problems': [{'kind': 'visible_text', 'detail': 'garbled digits on a tag'}]}


def unavailable():
    return {'state': 'review_error', 'usable': None, 'error': 'HTTP 503'}


def run(clips, verdicts, *, budget=4, minimum=3, made=True):
    """verdicts: path -> review result; regenerated clips are named <kind>-<index>."""
    calls = []

    def review(index, path):
        return verdicts.get(path, ok())

    def regenerate(prompt, index, kind):
        calls.append((index, kind, prompt))
        return f'{kind}-{index}' if made else None

    kept, prompts, report = check.repair_clips(clips, PROMPTS[:len(clips)], review=review, regenerate=regenerate,
                                               budget=budget, minimum=minimum, log=lambda *_: None)
    return kept, prompts, report, calls


class RepairClipsTest(unittest.TestCase):
    def test_good_clips_are_kept_untouched_and_nothing_is_regenerated(self):
        clips = ['c1', 'c2', 'c3', 'c4', 'c5']
        kept, prompts, report, calls = run(clips, {})
        self.assertEqual(kept, clips)
        self.assertEqual(prompts, PROMPTS)
        self.assertEqual(calls, [])
        self.assertEqual({entry['result'] for entry in report}, {'kept'})

    def test_a_render_glitch_is_rendered_again_with_what_to_avoid_and_keeps_its_place(self):
        kept, prompts, report, calls = run(['c1', 'c2', 'c3', 'c4', 'c5'], {'c3': glitch()})
        self.assertEqual(kept, ['c1', 'c2', 'repair-2', 'c4', 'c5'])
        self.assertEqual(len(calls), 1)
        index, kind, prompt = calls[0]
        self.assertEqual((index, kind), (2, 'repair'))
        self.assertTrue(prompt.startswith(PROMPTS[2]))
        self.assertIn('garbled digits', prompt)
        self.assertIn('Avoid: no tags or price tags', prompt)
        self.assertIn("arm's length", prompt)
        self.assertEqual(prompts[2], prompt)
        self.assertEqual(report[2]['result'], 'replaced')

    def test_a_glitch_that_returns_becomes_a_safe_whole_garment_scene_in_the_same_style(self):
        kept, _, report, calls = run(['c1', 'c2', 'c3', 'c4', 'c5'], {'c2': glitch(), 'repair-1': glitch()})
        self.assertEqual(kept[1], 'safe-1')
        self.assertEqual([kind for _, kind, _ in calls], ['repair', 'safe'])
        safe = calls[1][2]
        self.assertTrue(safe.startswith('STYLE LOCK: warm tungsten light, small Indian workshop.'))
        self.assertIn('SCENE: ' + check.SAFE_SCENES[1], safe)
        self.assertIn('No close-up of stitches', safe)
        self.assertEqual([a['kind'] for a in report[1]['attempts']], ['repair', 'safe'])

    def test_a_wrongly_drawn_construction_goes_straight_to_safe_scenes(self):
        # 1-Oct-2026: asking Veo again for the coverseam close-up mostly draws the cords again.
        kept, _, report, calls = run(['c1', 'c2', 'c3', 'c4', 'c5'], {'c2': bad(), 'safe-1': bad('Rib knit swatch.')})
        self.assertEqual(kept[1], 'safe2-1')
        self.assertEqual([kind for _, kind, _ in calls], ['safe', 'safe2'])
        self.assertIn('SCENE: ' + check.SAFE_SCENES[1], calls[0][2])
        self.assertIn('SCENE: ' + check.SAFE_SCENES[3], calls[1][2])
        mixed = {**bad(), 'problems': bad()['problems'] + glitch()['problems']}
        self.assertEqual([kind for kind, _ in check.attempt_plan(PROMPTS[0], mixed, PROMPTS, 0)], ['safe', 'safe2'])
        unsure = {**bad(), 'verdict': 'uncertain', 'problems': []}
        self.assertEqual([kind for kind, _ in check.attempt_plan(PROMPTS[0], unsure, PROMPTS, 0)], ['safe', 'safe2'])

    def test_a_clip_with_no_truthful_version_is_dropped_but_the_short_goes_on(self):
        kept, _, report, _ = run(['c1', 'c2', 'c3', 'c4', 'c5'],
                                 {'c4': bad(), 'safe-3': bad(), 'safe2-3': bad()})
        self.assertEqual(kept, ['c1', 'c2', 'c3', 'c5'])
        self.assertEqual(report[3]['result'], 'dropped')

    def test_the_budget_caps_new_clips_per_short(self):
        verdicts = {name: glitch() for name in ('c1', 'c2', 'c3')}
        kept, _, report, calls = run(['c1', 'c2', 'c3', 'c4', 'c5'], verdicts, budget=2, minimum=2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(kept, ['repair-0', 'repair-1', 'c4', 'c5'])
        self.assertEqual(report[2]['attempts'], [{'kind': 'repair', 'skipped': 'budget'}])

    def test_too_few_truthful_clips_stops_the_short_with_the_report(self):
        verdicts = {name: bad() for name in ('c1', 'c2', 'c3', 'safe-0', 'safe2-0', 'safe-1', 'safe2-1',
                                             'safe-2', 'safe2-2')}
        with self.assertRaises(check.ClipRepairError) as stop:
            run(['c1', 'c2', 'c3', 'c4', 'c5'], verdicts, budget=10)
        self.assertIn('only 2 of 5', str(stop.exception))
        self.assertEqual(len(stop.exception.report), 5)

    def test_an_unavailable_check_keeps_the_clip_for_the_final_review_to_decide(self):
        kept, _, report, calls = run(['c1', 'c2', 'c3'], {'c2': unavailable()})
        self.assertEqual(kept, ['c1', 'c2', 'c3'])
        self.assertEqual(calls, [])
        self.assertEqual(report[1]['result'], 'unchecked')

    def test_a_failed_generation_tries_the_next_version_then_drops(self):
        kept, _, report, calls = run(['c1', 'c2', 'c3', 'c4'], {'c1': bad()}, made=False)
        self.assertEqual(kept, ['c2', 'c3', 'c4'])
        self.assertEqual([a['state'] for a in report[0]['attempts']], ['not_generated', 'not_generated'])

    def test_every_clip_needs_its_prompt(self):
        with self.assertRaises(ValueError):
            check.repair_clips(['c1'], [], review=Mock(), regenerate=Mock(), log=lambda *_: None)


class NarrationTest(unittest.TestCase):
    def test_each_clip_hears_the_sentences_spoken_while_it_is_on_screen(self):
        captions = check.caption_spans(['One.', 'Two.', 'Three.', 'Four.'], 32.0,
                                       [{'start': 0, 'end': 8}, {'start': 8, 'end': 16},
                                        {'start': 16, 'end': 24}, {'start': 24, 'end': 32}])
        narration = check.clip_narration([8.0] * 5, 32.8, 0.3, captions)
        # 5 clips share 32.8 s with 0.3 s cross-fades: 6.8 s each, starting 0, 6.5, 13, 19.5, 26.
        self.assertEqual(narration, ['One.', 'One. Two.', 'Two. Three.', 'Three. Four.', 'Four.'])

    def test_a_looped_clip_hears_both_places_it_plays(self):
        captions = check.caption_spans(['A.', 'B.', 'C.'], 30.0)
        narration = check.clip_narration([8.0, 8.0], 30.8, 0.3, captions)
        self.assertEqual(narration[0], 'A. B. C.')

    def test_without_measured_timing_sentences_are_spread_evenly(self):
        self.assertEqual(check.caption_spans(['A.', 'B.'], 10.0),
                         [{'text': 'A.', 'start': 0.0, 'end': 5.0}, {'text': 'B.', 'start': 5.0, 'end': 10.0}])
        self.assertEqual(check.caption_spans(['A.', 'B.'], 10.0, [{'start': 1, 'end': 3}])[1]['start'], 5.0)

    def test_style_lock_comes_from_the_prompt_set(self):
        self.assertEqual(check.style_lock(['', PROMPTS[0]]), 'STYLE LOCK: warm tungsten light, small Indian workshop.')
        self.assertEqual(check.style_lock(['A plain scene.']), '')
        self.assertTrue(check.safe_prompt(['A plain scene.'], 0).startswith('SCENE: '))


class LessonContextTest(unittest.TestCase):
    def test_context_carries_the_cited_claims_and_limits_only(self):
        brief = {'fact_ids': ['stitch_construction_jobs', 'missing'], 'buyer_question': 'Hem lines?',
                 'buyer_decision': 'Ask the stitch type.',
                 'evidence': {'stitch_construction_jobs': {'claim': '406 coverseam on knit hems.',
                                                           'source_url': 'https://example.org/x',
                                                           'limits': 'Construction only.'}}}
        context = check.lesson_context('Hem stitch', brief)
        self.assertEqual(context['primary_facts'], [{'fact_id': 'stitch_construction_jobs',
                                                     'claim': '406 coverseam on knit hems.',
                                                     'limits': 'Construction only.'}])
        self.assertEqual(check.lesson_context('x', None)['primary_facts'], [])


class AssessmentTest(unittest.TestCase):
    def value(self, **change):
        return {'verdict': 'pass', 'observed_visual': 'Plain tee folded.', 'shown_material': 'jersey',
                'problems': [], 'avoid': '', **change}

    def test_only_a_clean_pass_is_usable(self):
        self.assertTrue(check.assessment_usable(self.value()))
        self.assertFalse(check.assessment_usable(self.value(verdict='fail')))
        self.assertFalse(check.assessment_usable(self.value(verdict='uncertain')))
        self.assertFalse(check.assessment_usable(self.value(
            problems=[{'kind': 'visible_text', 'detail': 'garbled label'}])))

    def test_malformed_answers_are_errors_not_passes(self):
        for broken in (self.value(verdict='ok'), self.value(observed_visual=' '),
                       self.value(problems=[{'kind': 'other', 'detail': 'x'}]), self.value(problems='none')):
            with self.assertRaises(ValueError):
                check.assessment_usable(broken)


def clip_file(folder):
    path = Path(folder) / 'clip.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=gray:s=320x568:d=1',
                    '-pix_fmt', 'yuv420p', str(path)], check=True, capture_output=True, timeout=60)
    return path


class ReviewClipTest(unittest.TestCase):
    def response(self, value, status=200):
        reply = Mock(status_code=status)
        reply.json.return_value = {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [
            {'type': 'text', 'text': json.dumps(value)}]}]}
        return reply

    def test_the_clip_and_its_narration_go_to_the_same_review_model(self):
        with TemporaryDirectory() as folder:
            post = Mock(return_value=self.response({**bad(), 'state': None}))
            result = check.review_clip(clip_file(folder), context={'topic': 'Hem'}, narration='Coverseam stretches.',
                                       api_key='k', post=post)
        self.assertEqual((result['state'], result['usable']), ('fail', False))
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['model'], check.QA_MODEL)
        self.assertIn('Coverseam stretches.', payload['input'][0]['text'])
        self.assertIn('braided cords', payload['input'][0]['text'])
        self.assertEqual(payload['input'][1]['mime_type'], 'video/mp4')
        self.assertTrue(base64.b64decode(payload['input'][1]['data']))
        self.assertEqual(payload['response_format']['schema'], check.CLIP_SCHEMA)

    def test_provider_trouble_is_reported_as_unchecked_never_as_a_pass(self):
        with TemporaryDirectory() as folder:
            path = clip_file(folder)
            for post in (Mock(return_value=self.response({}, status=500)),
                         Mock(return_value=self.response({'verdict': 'pass'}))):
                result = check.review_clip(path, context={}, narration='', api_key='k', post=post)
                self.assertEqual((result['state'], result['usable']), ('review_error', None))
            self.assertEqual(post.call_count, 1)


if __name__ == '__main__':
    unittest.main()
