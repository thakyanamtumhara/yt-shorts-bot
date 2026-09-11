import importlib.util
import math
import random
import unittest

from tools.daily_visual_timeline import plan_visual_segments, verify_visual_coverage


class VisualTimelineTests(unittest.TestCase):
    def assert_complete(self, durations, target, overlap):
        plan = plan_visual_segments(durations, target, overlap)
        self.assertAlmostEqual(plan[-1]['start'] + plan[-1]['duration'], target)
        for item in plan:
            self.assertGreater(item['duration'], 0)
            self.assertLessEqual(item['duration'], durations[item['source_index']] + 1e-8)
        return plan

    def test_five_scene_transition_gap_regression(self):
        target = 34.6
        previous_duration = target - 4 * .3
        with self.assertRaises(ValueError):
            verify_visual_coverage(previous_duration, target)
        plan = self.assert_complete([8] * 5, target, .3)
        self.assertEqual(len(plan), 5)
        self.assertAlmostEqual(plan[0]['duration'], 7.16)

    def test_short_sources_are_not_frozen_or_overread(self):
        self.assert_complete([2, 8, 7, 5, 8], 28, .3)

    def test_long_speech_uses_moving_source_segments(self):
        plan = self.assert_complete([8] * 5, 52.3, .3)
        self.assertGreater(len(plan), 5)
        self.assertEqual([item['source_index'] for item in plan], [0, 1, 2, 3, 4, 0, 1])

    def test_no_transitions_and_single_source(self):
        self.assert_complete([3, 7, 8], 12, 0)
        self.assert_complete([8], 6, .3)
        self.assert_complete([8, 8], .1, .3)

    def test_many_duration_combinations(self):
        rng = random.Random(20260911)
        for _ in range(200):
            durations = [rng.uniform(.5, 8) for _ in range(rng.randint(1, 7))]
            self.assert_complete(durations, rng.uniform(3, 100), .3)

    def test_invalid_inputs_stop(self):
        for durations, target, overlap in [([], 10, .3), ([0], 10, .3), ([.3], 10, .3), ([math.inf], 10, .3), ([8], 0, .3), ([8], math.nan, .3), ([8], 10, -1)]:
            with self.assertRaises(ValueError):
                plan_visual_segments(durations, target, overlap)

    def test_coverage_stops_on_real_gap(self):
        verify_visual_coverage(30, 30)
        verify_visual_coverage(30.02, 30)
        with self.assertRaises(ValueError):
            verify_visual_coverage(29.96, 30)
        with self.assertRaises(ValueError):
            verify_visual_coverage(math.nan, 30)

    @unittest.skipUnless(importlib.util.find_spec('moviepy'), 'MoviePy render dependency unavailable')
    def test_actual_composition_covers_last_word_with_moving_picture(self):
        import numpy as np
        from moviepy.video.VideoClip import VideoClip
        from tools.daily_visual_timeline import assemble_visual_timeline
        def frame(t):
            result = np.full((32, 32, 3), 80, dtype=np.uint8)
            result[:, int(t * 20) % 24:int(t * 20) % 24 + 8] = 230
            return result
        sources = [VideoClip(frame, duration=2) for _ in range(5)]
        result = assemble_visual_timeline(sources, 6, .3)
        self.assertAlmostEqual(result.duration, 6)
        self.assertGreater(result.get_frame(5.96).mean(), 50)
        self.assertFalse(np.array_equal(result.get_frame(5.6), result.get_frame(5.96)))
        result.close()


if __name__ == '__main__':
    unittest.main()
