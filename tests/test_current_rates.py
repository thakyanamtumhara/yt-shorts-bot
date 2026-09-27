import json
import unittest
from unittest.mock import MagicMock

from tools import current_rates as rates


def catalogue(**changes):
    prices = {f'Filler {index}': {'Black': {'S': 100 + index, 'M': 100 + index}} for index in range(8)}
    prices.update({
        'Non Bio Rneck': {'Black': {'36': 107, '44': 112}, 'White': {'36': 107, '44': 112}},
        'Bio Rneck': {'Black': {'36': 150, '44': 155}},
        'Hoodie 320gsm-1': {'Black': {'S': 307, 'XXL': 317}},
        'Hoodie 320gsm-2': {'Navy': {'S': 337, 'XXL': 347}, 'White': {'S': 337, 'XXL': 347}},
        'Mixed Row': {'Black': {'S': 200}, 'White': {'S': 210}},
    })
    details = {name: [name.split()[0], f'{name} description'] for name in prices}
    details.update({'Non Bio Rneck': ['NBio', 'Non Bio Round neck, 180gsm, 88% Cotton, 12% Polyester'],
                    'Bio Rneck': ['YL Bio', 'Regular Fit, Biowash Round neck, 180gsm, 100% Cotton Premium Quality Fabric'],
                    'Hoodie 320gsm-1': ['Hood320-1', 'Non-zipper Hoodie, 320gsm'],
                    'Hoodie 320gsm-2': ['Hood320-2', 'Non-zipper Hoodie, 320gsm']})
    samples = {name: 120 + index for index, name in enumerate(prices)}
    samples.update({'Non Bio Rneck': 131, 'Bio Rneck': 185, 'Hoodie 320gsm-1': 378, 'Hoodie 320gsm-2': 414})
    tbl = [prices, details, samples, {'moq': 9}, {}, {}, {'gst': 5}, {}, {}, [], {}, {}]
    for index, value in changes.items():
        tbl[int(index[1:])] = value
    return 'let tbl=' + json.dumps(tbl)


class CatalogueTest(unittest.TestCase):
    def test_live_rates_become_dated_facts_with_exact_amounts(self):
        facts = rates.rate_facts(rates.parse_catalogue(catalogue()), '28-Sep-2026')
        fact = facts['rate_non_bio_rneck']
        self.assertEqual(fact['amounts'], [107, 112, 131])
        self.assertIn('checked on 28-Sep-2026', fact['claim'])
        self.assertIn('10 or more pieces: ₹107 per piece for sizes 36; ₹112 per piece for sizes 44', fact['claim'])
        self.assertIn('Fewer than 10 pieces: ₹131 per piece', fact['claim'])
        self.assertIn('88% Cotton, 12% Polyester', fact['claim'])
        self.assertIn('exclude 5% GST and delivery', fact['claim'])
        self.assertTrue(fact['source_url'].startswith('https://'))
        self.assertIn('never mention a discount', fact['limits'])
        self.assertNotIn(' in Black', fact['claim'])

    def test_colour_band_rows_name_their_colours_and_mixed_rows_are_left_out(self):
        facts = rates.rate_facts(rates.parse_catalogue(catalogue()), '28-Sep-2026')
        self.assertIn('Hoodie 320gsm-1 in Black (', facts['rate_hoodie_320gsm_1']['claim'])
        self.assertIn('Hoodie 320gsm-2 in Navy, White (', facts['rate_hoodie_320gsm_2']['claim'])
        self.assertNotIn('rate_mixed_row', facts)

    def test_wrong_file_or_changed_shape_gives_no_facts(self):
        for text in ('<html>error</html>', 'let tbl=[1,2]', 'let tbl={bad', catalogue(t3={'moq': '9'})):
            with self.subTest(text=text[:20]), self.assertRaises(rates.RateError):
                rates.parse_catalogue(text)

    def test_catalogue_is_read_from_the_uncached_origin(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'let tbl=[]'
        opener = MagicMock(return_value=response)
        self.assertEqual(rates.fetch_catalogue(opener=opener), 'let tbl=[]')
        self.assertEqual(opener.call_args.args[0].full_url, 'https://d2fzc2z1ecgedb.cloudfront.net/pc.js')


class AmountGateTest(unittest.TestCase):
    def setUp(self):
        facts = rates.rate_facts(rates.parse_catalogue(catalogue()), '28-Sep-2026')
        self.cited = {key: facts[key] for key in ('rate_non_bio_rneck', 'rate_bio_rneck')}
        self.allowed = rates.allowed_amounts(self.cited)

    def test_every_written_form_of_money_is_found(self):
        text = '₹107 aur Rs.150, Rs 1,299, INR500, 131 rupaye, 185/- रुपये, २०० रुपये, ₹2 lakh, 180 GSM, 10 pieces'
        self.assertEqual(rates.money_amounts(text), [107, 150, 1299, 500, 131, 185, 200, 2])

    def test_cited_rates_and_their_differences_pass_everything_else_fails(self):
        self.assertEqual(rates.unsupported_amounts('Non-bio ₹107, bio ₹150 — ₹43 ka fark; sample ₹131.', self.allowed), [])
        self.assertEqual(rates.unsupported_amounts('₹45 ka fark, ₹99 wala, ₹1.5 lakh', self.allowed), [45, 99, 1.5])
        self.assertEqual(rates.unsupported_amounts('₹107', rates.allowed_amounts({})), [107])
        self.assertNotIn(307, self.allowed)

    def test_rupee_words_without_digits_cannot_be_checked(self):
        self.assertEqual(rates.loose_rupee_words('sau rupaye ka fark, kitne rupees?'), ['rupaye', 'rupees'])
        self.assertEqual(rates.loose_rupee_words('107 rupaye, 150/- rupaye, ₹131'), [])

    def test_a_drafted_price_must_still_be_the_live_price(self):
        live = rates.rate_facts(rates.parse_catalogue(catalogue()), 'posting check')
        self.assertTrue(rates.cited_rates_unchanged(self.cited, live))
        changed = {**live, 'rate_bio_rneck': {**live['rate_bio_rneck'], 'amounts': [152, 157, 185]}}
        self.assertFalse(rates.cited_rates_unchanged(self.cited, changed))
        self.assertFalse(rates.cited_rates_unchanged(self.cited, {}))
        self.assertFalse(rates.cited_rates_unchanged({}, live))


if __name__ == '__main__':
    unittest.main()
