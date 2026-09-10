import ast
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image
from tools.cover_quality import choose_cover, neutral_cover, prepend_cover, render_youtube_cover, validate_cover_text

ROOT = Path(__file__).resolve().parents[1]


class CoverQualityTest(unittest.TestCase):
    def test_published_broken_money_hooks_are_replaced_as_whole_phrases(self):
        for old in ('₹7 DYE | 500 Pc BLEED', '₹3 FUSING | COLLAR FLAT 2', '₹2 LESS | 500 Pc बिना'):
            hi, latin, reason = choose_cover(old, old, '500 pieces', 'MOQ bulk order')
            self.assertEqual(hi, 'BULK खरीदने से पहले | सैंपल जाँचो')
            self.assertEqual(latin, 'BEFORE BULK | CHECK A SAMPLE')
            self.assertTrue(reason.startswith('neutral-fallback:'))

    def test_plural_returns_and_unrelated_number_context_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_cover_text('PRINT CRACK | 500 RETURNS', 'आप500पीस लेने से पहले सैंपल देखो।')
        with self.assertRaises(ValueError):
            validate_cover_text('DTF PRINT | 160 DEGREES', 'Transfer temperature160degrees')
        hi, latin, _ = choose_cover('', '', 'Sublimation polyester compatibility', 'sublimation printing on polyester')
        self.assertNotIn('GSM', hi)
        self.assertNotIn('GSM', latin)

    def test_complete_new_buyer_question_is_preserved(self):
        pair = ('DTF PRINT | नमी का असर', 'DTF PRINT | NAMI KA ASAR')
        self.assertEqual(choose_cover(*pair, 'DTF humidity', 'DTF humidity'), (*pair, 'validated'))

    def test_long_or_incomplete_wording_is_rewritten_never_cut(self):
        for text in ('MY T SHIRT PRINT HAS | A VERY BIG PROBLEM TODAY', 'DTF PRINT | बिना', 'DTF | 50 PIECES AND'):
            with self.assertRaises(ValueError):
                validate_cover_text(text, '50')

    def test_numeric_substrings_do_not_prove_a_claim(self):
        with self.assertRaises(ValueError):
            validate_cover_text('DTF PRINT | 7 DEGREE CHANGE', '70 degrees')
        validate_cover_text('180 GSM | क्या जाँचें?', '180 GSM')

    def test_neutral_topic_covers_pass_without_prices_or_numbers(self):
        for topic in ('DTF humidity','DTF print','regular fit','polo collar','wet rub dye','polyester','MOQ','biowash','cotton','screen print','wholesale tips'):
            for text in neutral_cover(topic):
                validate_cover_text(text, '')

    def test_youtube_has_native_dimensions_and_all_text_inside_four_five_crop(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cover.png'
            result=render_youtube_cover(Image.new('RGB',(1080,1920),'#314962'),['SAME GSM','कपड़ा अलग क्यों?'],path)
            with Image.open(path) as check_image:
                self.assertEqual(check_image.size,(1280,720))
            self.assertLess(path.stat().st_size,2_000_000)
            for x1,y1,x2,y2 in result['text_boxes']:
                self.assertTrue(352 <= x1 < x2 <= 928)
                self.assertTrue(0 <= y1 < y2 <= 720)

    def test_youtube_upload_chooses_its_sibling_while_instagram_keeps_vertical(self):
        source=(ROOT/'daily_short.py').read_text()
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='upload_thumbnail')
        code=ast.get_source_segment(source,node)
        self.assertIn('"_youtube.png"',code)
        self.assertIn('cross_post_to_instagram(output_path, ig_title, yt_description, fresh_topic, thumbnail_path=thumbnail_path)',source)
        self.assertNotIn('thumb_text = " ".join(thumb_text.split()[:6])',source)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'ffmpeg required')
    def test_opening_cover_keeps_complete_body_and_delays_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp); video=tmp/'body.mp4'; cover=tmp/'cover.png'
            Image.new('RGB',(120,200),'yellow').save(cover)
            subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=120x200:r=30:d=1','-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(video)],check=True)
            qc=prepend_cover(video,cover)
            self.assertAlmostEqual(qc['after_seconds'],1.5,delta=.07)
            for time,expected in (('0.1','yellow'),('1.3','blue')):
                image=tmp/(time+'.png')
                subprocess.run(['ffmpeg','-v','error','-y','-ss',time,'-i',str(video),'-frames:v','1',str(image)],check=True)
                with Image.open(image) as check_image:
                    r,g,b=check_image.getpixel((60,100))
                self.assertTrue((r>200 and g>200 and b<30) if expected=='yellow' else (b>200 and r<30 and g<30))
            import array,math
            data=subprocess.run(['ffmpeg','-v','error','-i',str(video),'-f','s16le','-ac','1','-ar','16000','-'],check=True,capture_output=True).stdout
            samples=array.array('h',data)
            def rms(a,b):
                x=samples[int(a*16000):int(b*16000)];return math.sqrt(sum(v*v for v in x)/len(x))
            self.assertLess(rms(.05,.4),10)
            self.assertGreater(rms(.7,1.4),1000)


if __name__=='__main__':
    unittest.main()
