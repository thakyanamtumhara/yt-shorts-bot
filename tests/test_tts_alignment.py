import base64
import json
import unittest
from unittest.mock import Mock

from tools.tts_alignment import (
    SENTENCE_BREAK, aligned_words, alignment_evidence, sentence_spans, speech_with_timestamps, timed_sentences,
)
from tools.voice_runtime import load_normalize_for_tts

NORMALIZE = load_normalize_for_tts()

# Exact scripts from the 26-Sep run 36245255462, blocked only because Whisper gave 12 segments for 8 sentences.
VOICE = ("Ek T-shirt halki, ek bhaari — dono 200 GSM. Ab dekho, GSM sirf ek fixed area ka weight batata hai — "
         "jaise ek square meter kapda kitna bhaari hai. Ye poori tayaar T-shirt ka total weight nahi hai. Toh agar "
         "ek oversized hai aur ek regular fit, toh oversized mein zyada kapda laga hai, isliye poori garment bhaari "
         "lagegi — GSM phir bhi wahi 200 hai. Matlab, haath mein utha ke weight compare karna GSM match karne ka sahi "
         "tareeka nahi hai. Bulk order mein GSM specification kapde par lagta hai, tayaar T-shirt ke tol se uski "
         "pushthi nahi hoti. GSM same hai toh garment ka weight bhi same hoga — ye galat soch hai. Bas itna yaad rakho.")
ENGLISH = ("One t-shirt feels light, one heavy — both 200 GSM. GSM only measures weight of a fixed fabric area. "
           "It is not the total weight of a finished t-shirt. An oversized shirt uses more fabric, so it weighs more — "
           "GSM stays the same. Lifting two shirts to compare GSM is not accurate. In bulk orders, GSM applies to "
           "fabric, not finished garment weight. Same GSM means same garment weight — that thinking is wrong. "
           "Just remember this.")


def timeline(text, per_char=0.06, pause=0.4, lead=0.05):
    starts, clock = [], lead
    for char in text:
        starts.append(round(clock, 4))
        clock += per_char + (pause if char in '.?!' else 0)
    return ({'characters': list(text), 'character_start_times_seconds': starts,
             'character_end_times_seconds': [round(start + per_char, 4) for start in starts]},
            round(clock + 0.2, 3))


class AlignmentEvidenceTest(unittest.TestCase):
    def test_exact_ordered_timestamps_that_fit_the_audio_are_accepted(self):
        text = NORMALIZE(VOICE)
        alignment, seconds = timeline(text)
        evidence = alignment_evidence(text, alignment, seconds)
        self.assertTrue(evidence['exact'])
        self.assertEqual(len(evidence['alignment_sha256']), 64)

    def test_changed_incomplete_unordered_or_mismatched_audio_is_rejected(self):
        text = NORMALIZE(VOICE)
        alignment, seconds = timeline(text)
        starts = alignment['character_start_times_seconds']
        cases = {
            'characters_differ': ({**alignment, 'characters': list(text[:-1]) + ['?']}, seconds),
            'incomplete_timestamps': ({**alignment, 'character_end_times_seconds': starts[:-1]}, seconds),
            'unordered_timestamps': ({**alignment, 'character_start_times_seconds': [starts[1], starts[0]] + starts[2:]},
                                     seconds),
            'timestamps_exceed_audio': (alignment, seconds - 1),
            'unexplained_audio_after_text': (alignment, seconds + 3),
            'invalid_audio_duration': (alignment, float('nan')),
            'missing_alignment': (None, seconds),
        }
        for reason, (value, audio) in cases.items():
            with self.subTest(reason=reason):
                evidence = alignment_evidence(text, value, audio)
                self.assertFalse(evidence['exact'])
                self.assertEqual(evidence['reason'], reason)
        late = {**alignment, 'character_start_times_seconds': [start + 2 for start in starts],
                'character_end_times_seconds': [end + 2 for end in alignment['character_end_times_seconds']]}
        self.assertEqual(alignment_evidence(text, late, seconds + 2)['reason'], 'late_first_character')

    def test_slowed_voice_stretches_every_timestamp(self):
        text = NORMALIZE(VOICE)
        alignment, seconds = timeline(text)
        self.assertFalse(alignment_evidence(text, alignment, seconds / 0.95)['exact'])
        self.assertTrue(alignment_evidence(text, alignment, seconds / 0.95, scale=1 / 0.95)['exact'])


class MeasuredCaptionTest(unittest.TestCase):
    def test_blocked_gsm_script_gets_measured_sentences_and_every_word(self):
        text = NORMALIZE(VOICE)
        alignment, seconds = timeline(text)
        spans = sentence_spans(text, alignment)
        english = [line for line in SENTENCE_BREAK.split(ENGLISH) if line.strip()]
        self.assertEqual(len(spans), 8)
        captions = timed_sentences(english, spans, seconds)
        self.assertEqual([caption['text'] for caption in captions], english)
        for current, following in zip(captions, captions[1:]):
            self.assertAlmostEqual(current['end'], following['start'])
        self.assertLessEqual(captions[-1]['end'], seconds)
        tokens = VOICE.split()
        words, evidence = aligned_words(tokens, tokens, text, alignment, NORMALIZE)
        self.assertTrue(evidence['highlight_verified'])
        self.assertEqual(len(words), len([token for token in tokens if token != '—']))
        self.assertEqual(words[0]['text'], 'Ek')
        self.assertEqual(words[-1]['text'], 'rakho.')
        starts = [word['start'] for word in words]
        self.assertEqual(starts, sorted(starts))
        dono = next(word for word in words if word['text'] == 'dono')
        first_200 = next(word for word in words if word['text'] == '200')
        self.assertLess(dono['end'], first_200['start'] + 1e-9)

    def test_phrase_that_normalizes_together_is_highlighted_as_one_measured_unit(self):
        voice = 'Rs 99 se ₹2 lakh tak order. 40s combed cotton hai.'
        text = NORMALIZE(voice)
        alignment, _ = timeline(text)
        tokens = voice.split()
        words, evidence = aligned_words(tokens, tokens, text, alignment, NORMALIZE)
        self.assertTrue(evidence['highlight_verified'])
        self.assertIn('Rs 99', [word['text'] for word in words])
        self.assertIn('₹2 lakh', [word['text'] for word in words])
        self.assertEqual(evidence['grouped_tokens'], 4)

    def test_audio_that_skipped_a_word_turns_highlighting_off(self):
        text = NORMALIZE(VOICE).replace('कितना ', '', 1)
        alignment, _ = timeline(text)
        tokens = VOICE.split()
        words, evidence = aligned_words(tokens, tokens, text, alignment, NORMALIZE)
        self.assertEqual(words, [])
        self.assertFalse(evidence['highlight_verified'])

    def test_sentence_count_mismatch_gives_no_measured_plain_captions(self):
        text = NORMALIZE(VOICE)
        alignment, seconds = timeline(text)
        spans = sentence_spans(text, alignment)
        self.assertIsNone(timed_sentences(['Only one line.'], spans, seconds))


class TimestampedRequestTest(unittest.TestCase):
    def response(self, status, body=None):
        value = Mock(status_code=status, headers={'request-id': 'req-1'})
        value.json.return_value = body if body is not None else {}
        return value

    def test_same_voice_request_returns_audio_and_alignment(self):
        audio = b'ID3' + b'x' * 2000
        body = {'audio_base64': base64.b64encode(audio).decode(), 'alignment': {'characters': ['a']}}
        post = Mock(return_value=self.response(200, body))
        settings = {'stability': 0.5}
        result = speech_with_timestamps('key', 'voice', 'eleven_v3', 'text', settings, post=post, sleep=Mock())
        self.assertEqual(result, (audio, {'characters': ['a']}, 'req-1'))
        url = post.call_args.args[0]
        self.assertTrue(url.endswith('/v1/text-to-speech/voice/with-timestamps'))
        self.assertEqual(post.call_args.kwargs['json'], {'text': 'text', 'model_id': 'eleven_v3',
                                                          'voice_settings': settings})
        self.assertEqual(post.call_args.kwargs['params'], {'output_format': 'mp3_44100_128'})

    def test_one_retry_for_temporary_errors_but_never_for_billing_errors(self):
        audio = base64.b64encode(b'ID3' + b'x' * 2000).decode()
        post = Mock(side_effect=[self.response(503), self.response(200, {'audio_base64': audio, 'alignment': None})])
        self.assertIsNone(speech_with_timestamps('k', 'v', 'm', 't', {}, post=post, sleep=Mock())[1])
        self.assertEqual(post.call_count, 2)
        post = Mock(return_value=self.response(402, {'detail': {'status': 'payment_required', 'message': 'secret'}}))
        with self.assertRaises(RuntimeError) as raised:
            speech_with_timestamps('k', 'v', 'm', 't', {}, post=post, sleep=Mock())
        self.assertEqual(post.call_count, 1)
        self.assertIn('payment_required', str(raised.exception))
        self.assertNotIn('secret', str(raised.exception))

    def test_response_without_real_audio_is_an_error(self):
        post = Mock(return_value=self.response(200, {'audio_base64': base64.b64encode(json.dumps({}).encode()).decode()}))
        with self.assertRaises(RuntimeError):
            speech_with_timestamps('k', 'v', 'm', 't', {}, post=post, sleep=Mock())


if __name__ == '__main__':
    unittest.main()
