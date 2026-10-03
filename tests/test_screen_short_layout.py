import unittest

from PIL import Image, features

from tools.screen_short_layout import (HOOK_BOTTOM, HOOK_TOP, cta_strip_image, hook_band_image, outro_card_image,
                                       screen_top)


def ink_rows(image):
    alpha = image.getchannel('A') if image.mode == 'RGBA' else image.convert('L')
    box = alpha.getbbox()
    return box


class ScreenShortLayoutTests(unittest.TestCase):
    def test_the_band_sits_above_the_recording(self):
        self.assertEqual(screen_top(1920, 0.76, 40), 421)
        self.assertLess(HOOK_BOTTOM, screen_top())
        self.assertGreater(HOOK_TOP, 100)

    def test_hooks_fit_the_band_and_long_hooks_shrink_instead_of_being_cut(self):
        for text in ('WHITE BACKGROUND = RECTANGLE PRINT', 'DPI', 'CANVA 3.125X = 300 DPI KAISE MILEGA YAHAN',
                     'PIECES MATLAB DESIGNS NAHI SHEETS'):
            image = hook_band_image(text)
            self.assertEqual(image.width, 1080)
            self.assertLessEqual(image.height, HOOK_BOTTOM - HOOK_TOP, text)
            left, top, right, bottom = ink_rows(image)
            self.assertGreaterEqual(left, 40, text)
            self.assertLessEqual(right, 1040, text)
        with self.assertRaises(ValueError):
            hook_band_image('   ')

    def test_first_word_is_yellow(self):
        image = hook_band_image('WHITE BACKGROUND')
        pixels = image.getdata()
        self.assertIn((255, 215, 0, 255), pixels)
        self.assertIn((255, 255, 255, 255), pixels)

    @unittest.skipUnless(features.check('raqm'), 'Hindi shaping required')
    def test_devanagari_hook_renders(self):
        image = hook_band_image('सफ़ेद बैकग्राउंड = पूरा RECTANGLE')
        self.assertLessEqual(image.height, HOOK_BOTTOM - HOOK_TOP)

    def test_cta_strip_and_end_card(self):
        strip = cta_strip_image('Naya: DTF sheets - dtf.bulkplaintshirt.com')
        self.assertEqual(strip.size, (1080, 72))
        card = outro_card_image('Sale91 DTF', 'Apne PNG se DTF sheets', 'dtf.bulkplaintshirt.com')
        self.assertEqual(card.size, (1080, 1920))
        self.assertIsNotNone(card.convert('L').point(lambda v: 255 if v > 100 else 0).getbbox())
        with self.assertRaises(ValueError):
            cta_strip_image('x' * 400)


if __name__ == '__main__':
    unittest.main()
