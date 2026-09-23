import unittest
from unittest.mock import Mock

from tools.caption_timing import (
    choose_timing_source, plain_caption_segments, timing_evidence, timing_segments, verified_karaoke,
)


def result(end=4.0):
    return {"segments": [{"start": 0.0, "end": end, "words": [
        {"word": text, "start": index * end / 4, "end": (index + 1) * end / 4}
        for index, text in enumerate(("कपड़ा", "और", "फाइबर", "अलग"))]}]}


class CaptionTimingTest(unittest.TestCase):
    def test_observed_fifteen_second_alignment_cannot_be_spread_across_full_audio(self):
        source = result(15.0)
        retry = Mock(return_value=source)
        chosen, evidence = choose_timing_source(source, 35.9, retry)
        self.assertFalse(evidence["words_reliable"])
        self.assertTrue(evidence["retry_attempted"])
        retry.assert_called_once()
        align = Mock()
        words, review = verified_karaoke(["fabric"] * 133, ["fabric"] * 133,
                                        timing_segments(chosen), 35.9, str, align)
        self.assertEqual(words, [])
        self.assertFalse(review["highlight_verified"])
        align.assert_not_called()

    def test_one_same_model_retry_can_recover_complete_real_timestamps(self):
        retry = Mock(return_value=result(35.6))
        chosen, evidence = choose_timing_source(result(15), 35.9, retry)
        self.assertEqual(chosen, result(35.6))
        self.assertTrue(evidence["words_reliable"])
        self.assertEqual(evidence["source"], "retry_whisper")
        retry.assert_called_once()

    def test_good_initial_word_timestamps_need_no_retry(self):
        retry = Mock()
        _, evidence = choose_timing_source(result(), 4.2, retry)
        self.assertTrue(evidence["words_reliable"])
        retry.assert_not_called()

    def test_retry_exception_keeps_safe_fallback_and_logs_only_exception_type(self):
        _, evidence = choose_timing_source(result(15), 35.9, Mock(side_effect=ValueError("token=secret")))
        self.assertEqual(evidence["retry_error"], "ValueError")
        self.assertNotIn("secret", str(evidence))

    def test_segment_duration_cannot_hide_missing_or_degenerate_words(self):
        for change in ("missing", "collapsed", "nan", "overlap"):
            source = result(35.9)
            words = source["segments"][0]["words"]
            if change == "missing": source["segments"][0]["words"] = []
            if change == "collapsed": words[-1]["end"] = words[-1]["start"]
            if change == "nan": words[-1]["end"] = float("nan")
            if change == "overlap": words[-1]["start"] = 0
            with self.subTest(change=change):
                self.assertFalse(timing_evidence(timing_segments(source), 35.9)["words_reliable"])

    def test_reliable_direct_word_anchors_preserve_devanagari_vowel_signs(self):
        raw = ["kapda", "aur", "fibre", "alag"]
        normal = dict(zip(raw, ("कपड़ा", "और", "फाइबर", "अलग")))
        words, evidence = verified_karaoke(raw, raw, timing_segments(result()), 4.2, normal.get,
                                          lambda *args: [(0, 1), (1, 2), (2, 3), (3, 4)])
        self.assertTrue(evidence["highlight_verified"])
        self.assertEqual([word["start"] for word in words], [0, 1, 2, 3])

    def test_full_span_wrong_text_and_collapsed_tail_cannot_claim_alignment(self):
        for aligned in ([(0, 1), (1, 2), (2, 3), (3, 4)], [(0, 1), (1, 2), (2, 2), (2, 2)]):
            words, evidence = verified_karaoke(["unrelated"] * 4, ["wrong"] * 4,
                                              timing_segments(result()), 4.2, str, lambda *args: aligned)
            self.assertEqual(words, [])
            self.assertFalse(evidence["highlight_verified"])

    def test_number_expansion_uses_real_multiple_word_span(self):
        source = {"segments": [{"start": 0, "end": 2, "words": [
            {"word": "दस", "start": 0, "end": 1}, {"word": "रुपये", "start": 1, "end": 2}]}]}
        words, evidence = verified_karaoke(["₹10"], ["Rs10"], timing_segments(source), 2.1,
                                          lambda raw: "दस रुपये", lambda *args: [(0, 2)])
        self.assertTrue(evidence["highlight_verified"])
        self.assertEqual(words, [{"text": "Rs10", "start": 0, "end": 2}])

    def test_plain_mismatched_sentence_count_is_honest_and_has_no_overlaps(self):
        sentences = [f"Sentence {index}" for index in range(8)]
        source = [{"start": index * 6, "end": (index + 1) * 6, "words": []} for index in range(6)]
        captions, mode = plain_caption_segments(sentences, source, 36)
        self.assertEqual(mode, "estimated_plain")
        self.assertEqual(len(captions), 8)
        self.assertEqual(captions[-1]["end"], 36)
        for previous, current in zip(captions, captions[1:]):
            self.assertLessEqual(previous["end"], current["start"])

    def test_plain_exact_count_uses_actual_sentence_spans(self):
        captions, mode = plain_caption_segments(["Fabric and fibre differ."], timing_segments(result()), 4.2)
        self.assertEqual(mode, "segment_timed_plain")
        self.assertEqual(captions[0]["end"], 4)


if __name__ == "__main__":
    unittest.main()
