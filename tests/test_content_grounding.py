import unittest

from tools.content_grounding import unsupported_derived_claim


class DerivedClaimTest(unittest.TestCase):
    def test_published_incomplete_loop_explanation_is_held(self):
        bad = [
            "Pique: some loops don't get fully pulled through to the surface.",
            "Pique uses tuck stitches. Some loops aren't fully pulled through.",
            'Pique construction works differently: some loops are <strong>not fully pulled through</strong>.',
            'This is the tuck-stitch mechanism. These incomplete, held loops push the fabric outward.',
            '<th>Pique (Tuck-Stitch)</th><td>Some loops held (tucked), not fully formed</td>',
        ]
        for value in bad:
            with self.subTest(value=value):
                self.assertIn('Tuck formation', unsupported_derived_claim(value))

    def test_published_fibre_only_printing_claim_is_held(self):
        for value in [
            'Surface printing compatibility is a fibre-level question, not a construction-level one.',
            'They differ in how fabric responds to printing processes. Those are fibre-level differences, not construction differences.',
            'Print compatibility depends solely on fibre composition.',
        ]:
            with self.subTest(value=value):
                self.assertIn('print compatibility', unsupported_derived_claim(value))

    def test_supported_explanation_and_practical_limit_are_allowed(self):
        for value in [
            'In tuck knitting, the old loop stays on the needle while another yarn is collected. A later yarn is drawn through the held and tuck loops.',
            'Pique describes construction. State fibre composition separately in the order specification.',
            'For printing, neither a fibre label nor a construction label establishes the result. Evaluate the actual blank and materials.',
            'Print compatibility is not only a fibre question. Test the actual combination.',
            'Pique does not mean incomplete loops. Ask for the construction specification.',
            '<p>Pique: never describe the loops as incomplete.</p>',
        ]:
            with self.subTest(value=value):
                self.assertIsNone(unsupported_derived_claim(value))

    def test_unrelated_fibre_description_is_not_a_printing_claim(self):
        self.assertIsNone(unsupported_derived_claim(
            '<p>Cotton is a fibre-level description, not a construction-level description.</p>'
            '<p>For printing, test your actual sample.</p>'))

    def test_visible_instruction_leak_is_held(self):
        self.assertIn('Editorial instructions', unsupported_derived_claim(
            '<p>See the buyer FAQ — one reference, then move on.</p>'))


if __name__ == '__main__':
    unittest.main()
