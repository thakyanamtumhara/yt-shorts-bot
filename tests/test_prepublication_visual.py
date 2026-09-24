from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from tools import prepublication_visual as visual


SEGMENTS = [{'segment_index': 0, 'start_seconds': 0.0, 'end_seconds': 0.5},
            {'segment_index': 1, 'start_seconds': 0.5, 'end_seconds': 4.0}]


def manifest():
    return {'test_mode': False, 'source_posts': {'bot_youtube': None, 'instagram': None},
            'topic': 'Single jersey face and back',
            'script': {'voice': 'Face par V-shaped loops hain.', 'tts_input': 'Face पर V-shaped loops हैं.',
                       'english': 'The face shows V-shaped loops.'},
            'run_flags': {'caption_timing': {'caption_sentence_count': 1, 'speech_segment_count': 1,
                          'segments_reliable': True, 'plain_fallback': 'segment_timed_plain',
                          'highlight_verified': False, 'mode': 'plain'},
                          'topic_lesson': {'fact_ids': ['jersey_face_back'], 'evidence': {'jersey_face_back': {
                              'claim': 'Single jersey face has V-shaped loop legs, reverse shows crowns.',
                              'source_url': 'https://cottonworks.com/learning-hub/knitting/knit-basics/',
                              'limits': 'Construction only, not proof of an actual product test.'}}}}}


def assessment():
    return {'verdict': 'pass', **dict.fromkeys(visual.VISUAL_CHECKS, True), 'uncertain': False,
            'summary': 'Observed jersey diagrams match the cited construction and caption meaning.',
            'segment_checks': [{'segment_index': item['segment_index'], 'observed_visual': 'Plain jersey loop legs.',
                                'visible_text': 'Face vs back', 'matches_lesson': True,
                                'caption_semantics_match': True, 'uncertain': False} for item in SEGMENTS],
            'issues': []}


class VisualAssessmentTest(unittest.TestCase):
    def test_every_check_and_every_video_segment_must_pass(self):
        self.assertTrue(visual.visual_assessment_passes(assessment(), SEGMENTS, 4))
        for key in visual.VISUAL_CHECKS:
            wrong = assessment(); wrong[key] = False
            self.assertFalse(visual.visual_assessment_passes(wrong, SEGMENTS, 4))
        for key in ('matches_lesson', 'caption_semantics_match'):
            wrong = assessment(); wrong['segment_checks'][1][key] = False
            self.assertFalse(visual.visual_assessment_passes(wrong, SEGMENTS, 4))
        wrong = assessment(); wrong['segment_checks'][1]['uncertain'] = True
        self.assertFalse(visual.visual_assessment_passes(wrong, SEGMENTS, 4))
        for value in ('fail', 'uncertain'):
            wrong = assessment(); wrong['verdict'] = value
            self.assertFalse(visual.visual_assessment_passes(wrong, SEGMENTS, 4))

    def test_cable_knit_in_single_jersey_lesson_is_a_failure_even_if_top_verdict_says_pass(self):
        wrong = assessment()
        wrong['technical_visuals_match_facts'] = False
        wrong['segment_checks'][1].update(observed_visual='Cable-knit sweater with raised ribs.', matches_lesson=False)
        wrong['issues'] = [{'start_seconds': 1, 'end_seconds': 3, 'kind': 'technical_mismatch',
                            'observed': 'Cable-knit sweater', 'expected': 'Single jersey loop structure',
                            'reason': 'A different knit is being presented as the lesson demonstration.'}]
        self.assertFalse(visual.visual_assessment_passes(wrong, SEGMENTS, 4))

    def test_missing_coverage_bad_boolean_and_invalid_timestamps_cannot_pass(self):
        cases = []
        wrong = assessment(); wrong['segment_checks'].pop(); cases.append(wrong)
        wrong = assessment(); wrong['segment_checks'][1]['segment_index'] = 0; cases.append(wrong)
        wrong = assessment(); wrong['cover_matches_lesson'] = 'true'; cases.append(wrong)
        wrong = assessment(); wrong['issues'] = [{'start_seconds': 9, 'end_seconds': 10, 'kind': 'caption',
                                                'observed': 'x', 'expected': 'y', 'reason': 'z'}]; cases.append(wrong)
        for wrong in cases:
            with self.assertRaises(visual.VisualReviewError):
                visual.visual_assessment_passes(wrong, SEGMENTS, 4)

    def test_plain_captions_with_measured_matched_segments_are_allowed(self):
        data = manifest()
        self.assertTrue(visual.caption_timing_evidence(data)['verified'])
        timing = data['run_flags']['caption_timing']
        for changes in ({'plain_fallback': 'estimated_plain'}, {'speech_segment_count': 2},
                        {'segments_reliable': False}, {'caption_sentence_count': None}):
            altered = deepcopy(data); altered['run_flags']['caption_timing'].update(changes)
            self.assertFalse(visual.caption_timing_evidence(altered)['verified'])
        timing.update(plain_fallback=None, highlight_verified=True, words_reliable=True)
        self.assertTrue(visual.caption_timing_evidence(data)['verified'])

    def test_verified_word_highlights_do_not_depend_on_unused_english_fallback_counts(self):
        data = manifest()
        data['run_flags']['caption_timing'].update(
            mode='verified_word_timing', highlight_verified=True, words_reliable=True,
            caption_sentence_count=8, speech_segment_count=5, plain_fallback='estimated_plain')
        self.assertTrue(visual.caption_timing_evidence(data)['verified'])
        data['run_flags']['caption_timing']['words_reliable'] = False
        self.assertFalse(visual.caption_timing_evidence(data)['verified'])

    def test_missing_primary_facts_cannot_be_replaced_by_a_title(self):
        data = manifest(); data['run_flags']['topic_lesson']['evidence'] = {}
        with self.assertRaises(visual.VisualReviewError):
            visual.visual_context(data)


class VisualProviderAndGateTest(unittest.TestCase):
    def review(self, *, value=None, source=None, provider_status='completed', http=200):
        value = assessment() if value is None else value
        source = manifest() if source is None else source
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            proxy = folder / 'proxy.mp4'; proxy.write_bytes(b'complete-video-with-audio')
            cover = folder / 'cover.jpg'; cover.write_bytes(b'actual-rendered-cover')
            ending = folder / 'end.jpg'; ending.write_bytes(b'actual-rendered-ending')
            media = {'duration_seconds': 4, 'video_sha256': 'a' * 64, 'segments_requested': SEGMENTS}
            prepared = (proxy, [{'path': cover, 'at_seconds': 0.25}, {'path': ending, 'at_seconds': 3.85}], media)
            response = Mock(status_code=http)
            response.json.return_value = {'status': provider_status, 'private_provider_field': 'do-not-copy',
                'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': json.dumps(value)}]}]}
            with patch.object(visual, 'prepare_visual_input', return_value=prepared), \
                    patch.dict('os.environ', {'GOOGLE_API_KEY': 'do-not-copy'}), \
                    patch.object(visual.requests, 'post', return_value=response) as post:
                if http != 200 or provider_status != 'completed':
                    with self.assertRaises(visual.VisualReviewError):
                        visual.assess_final_visuals('final.mp4', source, report_dir=folder)
                    result = json.loads((folder / 'visual-assessment.json').read_text())
                else:
                    result = visual.assess_final_visuals('final.mp4', source, report_dir=folder)
                payload = post.call_args.kwargs['json']
                self.assertEqual(post.call_count, 1)
            self.assertNotIn('do-not-copy', (folder / 'visual-assessment.json').read_text())
        return result, payload

    def test_real_video_cover_ending_script_and_facts_go_to_same_approved_model_once(self):
        result, payload = self.review()
        self.assertTrue(result['passed'])
        self.assertEqual(payload['model'], 'gemini-3.8-flash')
        self.assertFalse(payload['store'])
        video = [part for part in payload['input'] if part['type'] == 'video'][0]
        self.assertEqual(video['processing'], {'type': 'static', 'fps': 2})
        self.assertNotIn('start_offset', video['processing'])
        self.assertNotIn('end_offset', video['processing'])
        self.assertEqual(len([part for part in payload['input'] if part['type'] == 'image']), 2)
        self.assertIn('cottonworks.com', payload['input'][0]['text'])
        self.assertIn('Single jersey', payload['input'][0]['text'])

    def test_old_estimated_timing_still_gets_visual_replay_but_never_passes_the_gate(self):
        data = manifest(); data['run_flags']['caption_timing']['plain_fallback'] = 'estimated_plain'
        result, _ = self.review(source=data)
        self.assertTrue(result['model_visual_passed'])
        self.assertFalse(result['passed'])
        self.assertEqual(result['state'], 'caption_timing_unverified')

    def test_provider_failure_or_uncertain_result_fail_closed_with_artifact(self):
        for kwargs in ({'http': 429}, {'provider_status': 'in_progress'},
                       {'value': {**assessment(), 'verdict': 'uncertain', 'uncertain': True}}):
            result, _ = self.review(**kwargs)
            self.assertFalse(result['passed'])

    def test_gate_records_pass_failure_and_provider_error_flags(self):
        for passed in (True, False, 'error'):
            with TemporaryDirectory() as directory:
                path = Path(directory) / 'review_manifest.json'; path.write_text(json.dumps(manifest()))
                result = {'passed': passed is True, 'state': 'pass' if passed is True else 'fail',
                          'media': {'video_sha256': 'a' * 64}}
                with patch.object(visual, 'assess_final_visuals', return_value=result,
                                  side_effect=visual.VisualReviewError('unavailable') if passed == 'error' else None):
                    if passed is True:
                        visual.require_native_visual_review('final.mp4', path)
                    else:
                        with self.assertRaises(visual.VisualReviewError):
                            visual.require_native_visual_review('final.mp4', path)
                flag = json.loads(path.read_text())['run_flags']['native_visual_review']
                self.assertEqual(flag['passed'], passed is True)
                self.assertEqual(flag['artifact'], 'visual-assessment.json')

    def test_already_published_cannot_use_prepublication_gate(self):
        data = manifest(); data['source_posts']['bot_youtube'] = 'abcdefghijk'
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'review_manifest.json'; path.write_text(json.dumps(data))
            with patch.object(visual, 'assess_final_visuals') as review, self.assertRaises(visual.VisualReviewError):
                visual.require_native_visual_review('final.mp4', path)
            review.assert_not_called()

    def test_mismatched_final_video_hash_stops_before_transcode(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'final.mp4'; path.write_bytes(b'new-video')
            data = manifest(); data['assets'] = {'video': {'bytes': len(b'new-video'), 'sha256': hashlib.sha256(b'old-video').hexdigest()}}
            with patch.object(visual.subprocess, 'run') as run, self.assertRaises(visual.VisualReviewError):
                visual.prepare_visual_input(path, data, Path(directory))
            run.assert_not_called()

    def test_both_gates_precede_every_public_upload(self):
        source = (Path(__file__).resolve().parents[1] / 'daily_short.py').read_text()
        main = source[source.index('def main():'):]
        for gate in ('require_native_audio_review(output_path, review_path)', 'require_native_visual_review(output_path, review_path)'):
            position = main.index(gate)
            for upload in ('upload_to_youtube(', 'cross_post_to_instagram(', 'publish_fb_reel(', 'post_telegram_channel('):
                self.assertLess(position, main.index(upload))


if __name__ == '__main__':
    unittest.main()
