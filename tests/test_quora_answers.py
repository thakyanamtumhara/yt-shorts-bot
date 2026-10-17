import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import quora_answers as qa

ADVICE = ("Start with the fabric weight. A 180 GSM cotton tee is the usual everyday choice, while 210 to 240 GSM "
          "suits oversized fits. Order one sample first, wash it twice, and check shrinkage, neck shape and colour. "
          "Ask for one dye lot per colour so two cartons match. Plan sizes from your past orders instead of equal "
          "splits, because M and L usually move fastest. Compare the landed cost with GST and shipping, not just the "
          "piece rate, and keep the invoice for every order. If you print, match the blank to the method: cotton for "
          "screen print and DTF, polyester for sublimation. ")
GOOD = ADVICE + ("\n\nDisclosure: I run Own Knitted Blank Wears (sale91.com), a plain T-shirt maker in Delhi. "
                 "The minimum order is 10 pieces in total and single samples are available.")


class Rules(unittest.TestCase):
    def test_good_answer_passes(self):
        self.assertEqual(qa.problems(GOOD), [])

    def test_price_fails(self):
        self.assertIn("a price", qa.problems(GOOD.replace("everyday choice", "everyday choice at ₹150")))
        self.assertIn("a price", qa.problems(GOOD.replace("everyday choice", "everyday choice at Rs 150")))

    def test_phone_and_name_fail(self):
        self.assertIn("a phone number", qa.problems(GOOD + " Call 93366 95049."))
        self.assertIn("a person's name", qa.problems(GOOD.replace("I run", "I am Ankit and I run")))

    def test_banned_words_fail(self):
        self.assertIn("a word that must never appear", qa.problems(GOOD + " We ship from Surat."))
        self.assertIn("a 'best' claim", qa.problems(GOOD + " We give the best quality."))
        self.assertIn("a superlative or guarantee", qa.problems(GOOD + " We are the cheapest."))

    def test_site_once_and_disclosure(self):
        self.assertIn("sale91.com must appear exactly once", qa.problems(GOOD + " See sale91.com."))
        self.assertIn("no disclosure line", qa.problems(ADVICE + " sale91.com"))
        self.assertIn("a link", qa.problems(GOOD + " https://sale91.com/catalog"))

    def test_length(self):
        self.assertTrue(any("words" in p for p in qa.problems("Disclosure: I run Own Knitted Blank Wears (sale91.com).")))

    def test_question_urls(self):
        self.assertEqual(qa.norm_url("https://quora.com/Where-can-I-buy-T-shirts?share=1"),
                         "https://www.quora.com/Where-can-I-buy-T-shirts")
        self.assertEqual(qa.norm_url("https://www.quora.com/Where-can-I-buy-T-shirts/answer/Someone"),
                         "https://www.quora.com/Where-can-I-buy-T-shirts")
        self.assertIsNone(qa.norm_url("https://www.quora.com/profile/Someone"))
        self.assertIsNone(qa.norm_url("https://hi.quora.com/question"))
        self.assertIsNone(qa.norm_url("https://example.com/x"))

    def test_pick_keeps_only_real_new_distinct(self):
        u1, u2, u3 = ("https://www.quora.com/Q-one", "https://www.quora.com/Q-two", "https://www.quora.com/Q-three")
        other = ("Think about who will wear it. Uniform buyers want shape that holds after many washes, so a blend can "
                 "be smarter there, while a streetwear label needs a heavier cotton with a dropped shoulder. Ask the "
                 "supplier for the fabric weight on the invoice, compare two samples side by side after washing, and "
                 "check the stitching at the shoulder and hem. Keep your first order small, mix sizes by what you "
                 "sold before, and reorder the colours that move. Write down every defect you find so the next order "
                 "is cleaner, and share that list with the supplier before you pay for the next lot. ") + GOOD.split(ADVICE)[1]
        cands = [
            {"question": "one", "url": u1, "answer": GOOD},
            {"question": "dup text", "url": u2, "answer": GOOD},
            {"question": "not searched", "url": "https://www.quora.com/Q-never", "answer": other},
            {"question": "seen", "url": u3, "answer": other},
            {"question": "two", "url": u2, "answer": other},
        ]
        kept = qa.pick(cands, found={u1, u2, u3}, seen={u3})
        self.assertEqual([k["url"] for k in kept], [u1, u2])


if __name__ == "__main__":
    unittest.main()
