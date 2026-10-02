import unittest

from tools.script_length import LIMITS, TARGET_WORDS, length_feedback, spoken_words, voice_text_feedback
from tools.voice_runtime import load_normalize_for_tts

NORMALIZE = load_normalize_for_tts()

# Published 29-Sep-2026 Short (run 36588485211): 127 spoken words, 36.6 s on eleven_v3, 47.2 s on eleven_v4.
PUBLISHED = ('Ek oversize ₹186 aur doosra ₹205, kyun? Dekho, dono oversize hain par ek 180 GSM hai aur doosra '
             '240 GSM, aur construction aur finish bhi alag listed hai. Ab GSM ka matlab ye nahi ki poori T-shirt '
             'ka weight kitna hai. GSM basically fabric ka ek defined area kitna heavy hai, wo measure karta hai. '
             'Matlab same 240 GSM fabric se agar size S kaatoge toh garment halka banega, size L kaatoge toh bhaari, '
             'par fabric ka GSM wahi rahega. Toh jab aap do blanks compare karo, sirf GSM number mat dekho, uske '
             'saath jo construction aur finish likhi hai wo bhi padho. Bas itna samajh lo toh sahi comparison kar '
             'paoge. Theek hai.')
# The same script without its third and fourth sentences: 99 spoken words, 37.4 s on eleven_v4.
SIX_SENTENCES = ('Ek oversize ₹186 aur doosra ₹205, kyun? Dekho, dono oversize hain par ek 180 GSM hai aur doosra '
                 '240 GSM, aur construction aur finish bhi alag listed hai. Matlab same 240 GSM fabric se agar size S '
                 'kaatoge toh garment halka banega, size L kaatoge toh bhaari, par fabric ka GSM wahi rahega. Toh jab '
                 'aap do blanks compare karo, sirf GSM number mat dekho, uske saath jo construction aur finish likhi '
                 'hai wo bhi padho. Bas itna samajh lo toh sahi comparison kar paoge. Theek hai.')
# 1-Oct-2026 hem script cut to six sentences: 92 spoken words, 30.5 s on eleven_v4.
HEM = ('Hem pe do parallel lines kyun hoti hain? Matlab, andar se ek looper thread raw edge ko cover kar raha hota '
       'hai, aur bahar do needle rows dikhti hain. Ye isliye use hoti hai kyunki knit fabric stretch karti hai, aur '
       'coverseam uske saath stretch kar sakti hai bina tootey. Ab agar koi 503 serge stitch lagaye, toh wo loaded '
       'seam mein kaam nahi karegi — wo sirf edge finish ke liye hoti hai. Toh jab hem check karo, do lines ka '
       'matlab samjho — coverseam hai ya nahi. Bas itna hi.')


def feedback(script):
    return voice_text_feedback(script, NORMALIZE(script))


class ScriptLengthTest(unittest.TestCase):
    def test_v3_length_script_is_rewritten_before_any_voice_spend(self):
        self.assertEqual(spoken_words(NORMALIZE(PUBLISHED)), 127)
        message = feedback(PUBLISHED)
        self.assertIn('too long', message)
        self.assertIn('127 spoken words, limit 100', message)
        self.assertIn('Sentence lengths now: 7, 21, 13, 14, 25, 23, 9, 2 words.', message)
        self.assertIn(f'{TARGET_WORDS[0]}-{TARGET_WORDS[1]} words', message)
        self.assertIn('every other sentence up to 14 words', message)

    def test_six_sentence_scripts_that_measured_30_to_37_seconds_pass(self):
        self.assertEqual(spoken_words(NORMALIZE(SIX_SENTENCES)), 99)
        self.assertEqual(spoken_words(NORMALIZE(HEM)), 92)
        self.assertIsNone(feedback(SIX_SENTENCES))
        self.assertIsNone(feedback(HEM))

    def test_numbers_and_rupees_count_as_the_words_said_aloud(self):
        self.assertEqual(spoken_words(NORMALIZE('₹186')), 4)
        self.assertEqual(spoken_words(NORMALIZE('240 GSM')), 4)

    def test_too_short_script_is_rewritten(self):
        message = feedback('Sample par apna print check kar lo. Theek hai.')
        self.assertIn('too short', message)

    def test_markup_and_bracket_directions_never_reach_the_voice(self):
        for script in ('[pause] ' + HEM, HEM.replace('Bas itna hi.', '<break time="1s"/> Bas itna hi.'),
                       HEM + ' [laughs]'):
            with self.subTest(script=script[-40:]):
                self.assertIn('markup', feedback(script))

    def test_target_sits_inside_the_hard_limits(self):
        self.assertLess(LIMITS[0], TARGET_WORDS[0])
        self.assertLess(TARGET_WORDS[1], LIMITS[1])
        self.assertIsNone(length_feedback(' '.join(['shabd'] * LIMITS[1])))
        self.assertIn('too long', length_feedback(' '.join(['shabd'] * (LIMITS[1] + 1))))
        self.assertIsNone(length_feedback(' '.join(['shabd'] * LIMITS[0])))
        self.assertIn('too short', length_feedback(' '.join(['shabd'] * (LIMITS[0] - 1))))


if __name__ == '__main__':
    unittest.main()
