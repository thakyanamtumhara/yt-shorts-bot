import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import health_watch


class CaptionHealthTests(unittest.TestCase):
    def grade(self, flags):
        with TemporaryDirectory() as directory:
            file = Path(directory) / 'flags.json'
            file.write_text(json.dumps({'tts': 'elevenlabs', 'karaoke': False, **flags}))
            with patch.object(health_watch, 'RUN_FLAGS_FILE', str(file)):
                return health_watch.grade_render()

    def test_verified_plain_captions_are_not_reported_as_a_broken_render(self):
        bad, warnings = self.grade({'caption_timing': {'plain_fallback': 'segment_timed_plain',
                                                      'segments_reliable': True},
                                    'native_visual_review': {'passed': True}})
        self.assertEqual(bad, [])
        self.assertTrue(any('word highlighting is off' in item for item in warnings))

    def test_unreviewed_or_estimated_captions_remain_a_quality_issue(self):
        for flags in ({}, {'caption_timing': {'plain_fallback': 'estimated_plain'},
                           'native_visual_review': {'passed': True}},
                      {'caption_timing': {'plain_fallback': 'segment_timed_plain', 'segments_reliable': True}}):
            bad, _ = self.grade(flags)
            self.assertTrue(any('Caption timing' in item for item in bad))


if __name__ == '__main__':
    unittest.main()
