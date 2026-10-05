import ast
import os
from pathlib import Path
import random
import sys
import tempfile
import types
import unittest
from unittest import mock

from PIL import Image, features
from tools.daily_cover_layout import render_buyer_cover, supported_comparison
from tools.daily_topic_selection import SelectedTopic, load_bank


def topic(text, facts):
    bank=load_bank()
    return SelectedTopic({'topic':text,'fact_ids':facts,'evidence':{key:bank['facts'][key] for key in facts}})


class DailyCoverLayoutTest(unittest.TestCase):
    def entry_points(self,tmp):
        path=Path(__file__).resolve().parents[1]/'daily_short.py'
        tree=ast.parse(path.read_text())
        nodes=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in {'_cover_lines_from_text','generate_thumbnail','generate_ai_thumbnail'}]
        state={'os':os,'random':random,'GENERATE_THUMBNAIL':True,'AI_THUMBNAIL':True,'COVER_RAQM':True,'WORK_DIR':str(tmp),'COVER_META':{},'_has_deva':lambda s:any('\u0900'<=c<='\u097f' for c in s),'_thumbnail_background':lambda *a,**kw:Image.new('RGB',(1080,1920),'#4684a3'),'refresh_thumbnail_research':lambda *_:{},'get_source_channel_top_topics':lambda *_:[],'get_audience_questions':lambda *_:[],'get_ig_engagement_summary':lambda:''}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),state)
        return state

    @unittest.skipUnless(features.check('raqm'),'Hindi shaping required')
    def test_actual_fallback_entry_point_passes_lesson_and_writes_both_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=self.entry_points(tmp)
            path=state['generate_thumbnail']('',topic('GSM',['fabric_mass_per_area']),cover_text='ज़्यादा GSM | बेहतर कपड़ा?',script_text='GSM lighter heavier quality')
            self.assertIsNotNone(path)
            self.assertEqual(state['COVER_META']['layout']['comparison_key'],'weight_question')
            self.assertTrue(Path(path).with_name(Path(path).stem+'_youtube.png').exists())

    @unittest.skipUnless(features.check('raqm'),'Hindi shaping required')
    def test_actual_ai_entry_point_uses_b_renderer_after_the_same_brief_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=self.entry_points(tmp)
            state['generate_thumbnail_brief']=lambda *a,**kw:{'text':'टेरी या फ्लीस | अंदर क्या फर्क?','text_latin':'TERRY YA FLEECE | ANDAR KYA FARAK?','quality_check':'validated','face':False}
            google=types.ModuleType('google');genai=types.ModuleType('google.genai');genai.types=types.SimpleNamespace();google.genai=genai
            with mock.patch.dict(sys.modules,{'google':google,'google.genai':genai}):
                path=state['generate_ai_thumbnail']('',topic('terry fleece',['french_terry_fleece']),'French terry loops and fleece brushed inside.',claude_client=object())
            self.assertIsNotNone(path)
            self.assertEqual(state['COVER_META']['layout']['comparison_key'],'terry_vs_fleece')
            self.assertTrue(Path(path).with_name(Path(path).stem+'_youtube.png').exists())

    def test_bare_topic_does_not_invent_a_comparison(self):
        self.assertIsNone(supported_comparison('GSM हल्का भारी quality','GSM हल्का भारी quality'))

    def test_labels_need_the_actual_spoken_comparison(self):
        selected=topic('GSM',['fabric_mass_per_area'])
        self.assertIsNone(supported_comparison(selected,'The fabric is nice.'))
        self.assertEqual(supported_comparison(selected,'GSM can be lighter or heavier; quality needs other checks.')['labels'],('हल्का','भारी'))

    def test_biowash_alone_does_not_claim_shrinkage(self):
        text='Biowash softens the surface; compaction affects shrinkage.'
        self.assertIsNone(supported_comparison(topic('biowash',['biopolish_surface']),text))
        self.assertEqual(supported_comparison(topic('surface shrinkage',['biopolish_surface','compaction_shrinkage']),text)['key'],'surface_vs_shrinkage')

    def test_distinct_topics_receive_distinct_labels(self):
        rows=[('pique',['pique_tuck_structure'],'Pique is knit construction, not a fibre label.','pique_vs_fibre'),('terry',['french_terry_fleece'],'French terry has inside loops; fleece has a brushed inside.','terry_vs_fleece'),('jersey',['jersey_face_back'],'Single jersey front and back look different.','jersey_sides'),('count',['cotton_count_direction'],'Ne cotton count and denier use different directions.','yarn_numbering'),('knit',['knit_loop_stretch'],'Knit loops stretch; that does not establish fibre content or elastane.','knit_vs_fibre')]
        for title,facts,script,expected in rows:
            selected=supported_comparison(topic(title,facts),script)
            self.assertEqual(selected['key'],expected)
            self.assertNotIn('हल्का',selected['labels'])

    def test_dtf_lessons_get_their_own_comparison_only_when_the_script_speaks_it(self):
        rows=[(['dtf_transparent_background'],'White background rakha toh rectangle chhapa; transparent rakho.','dtf_white_vs_transparent'),
              (['dtf_canva_png_size','dtf_min_resolution'],'Canva mein size 3.125x chuno, tab 300 DPI pixel milenge.','dtf_canva_size'),
              (['dtf_min_resolution'],'DPI number badalne se pixel nahi badhte.','dtf_dpi_vs_pixels'),
              (['dtf_pieces_whole_sheet','dtf_sheet_size'],'Pieces matlab poori sheet; 28 design ek sheet par.','dtf_pieces_vs_designs'),
              (['dtf_sheet_size','dtf_pieces_whole_sheet'],'Gang sheet: ek sheet par kai design.','dtf_separate_vs_gang'),
              (['dtf_press_settings'],'T-shirt 165 aur hoodie 180 degree par press karo.','dtf_tee_vs_hoodie')]
        for facts,script,expected in rows:
            self.assertEqual(supported_comparison(topic('dtf',facts),script)['key'],expected)
        self.assertIsNone(supported_comparison(topic('dtf',['dtf_transparent_background']),'Glow aur shadow patchy chhapte hain.'))
        self.assertIsNone(supported_comparison(topic('dtf',['dtf_press_settings']),'Press karo, phir thanda hone do.'))
        self.assertIsNone(supported_comparison(topic('dtf',['dtf_service_launch']),'DTF sheet online order karo.'))

    @unittest.skipUnless(features.check('raqm'),'Hindi shaping required')
    def test_actual_dtf_renders_fit(self):
        rows=[(['dtf_transparent_background'],'White background rakha toh rectangle chhapa; transparent rakho.',['WHITE BG रखा','RECTANGLE क्यों छपा?']),
              (['dtf_canva_png_size'],'Canva mein size 3.125x chuno, tab 300 DPI milega.',['CANVA से PNG','300 DPI कैसे?']),
              (['dtf_min_resolution'],'DPI number badalne se pixel nahi badhte.',['DPI बदला फिर भी','फ़ाइल रिजेक्ट क्यों?']),
              (['dtf_pieces_whole_sheet'],'Pieces matlab poori sheet, design nahi.',['पीस मतलब','डिज़ाइन या शीट?']),
              (['dtf_sheet_size'],'Gang sheet: ek sheet par kai design.',['हर डिज़ाइन','अलग शीट पर?']),
              (['dtf_press_settings'],'T-shirt 165 aur hoodie 180 degree par press karo.',['DTF प्रेस','कितने डिग्री पर?'])]
        with tempfile.TemporaryDirectory() as tmp:
            for i,(facts,script,lines) in enumerate(rows):
                result=render_buyer_cover(Image.new('RGB',(1080,1920),'#456678'),lines,Path(tmp)/f'{i}.png',topic=topic('dtf',facts),script=script)
                self.assertTrue(result['comparison_illustrated'],facts)
                self.assertEqual(Image.open(Path(tmp)/f'{i}.png').size,(1080,1920))
                self.assertEqual(Image.open(Path(tmp)/f'{i}_youtube.png').size,(1280,720))

    def test_missing_evidence_does_not_enable_profile(self):
        selected=SelectedTopic({'topic':'GSM','fact_ids':['fabric_mass_per_area']})
        self.assertIsNone(supported_comparison(selected,'GSM lighter heavier quality'))

    @unittest.skipUnless(features.check('raqm'),'Hindi shaping required')
    def test_actual_varied_renders_fit_portrait_and_youtube(self):
        samples=[('GSM',['fabric_mass_per_area'],'GSM lighter heavier quality',['ज़्यादा GSM','बेहतर कपड़ा?']),('terry',['french_terry_fleece'],'French terry loops and brushed fleece',['टेरी या फ्लीस','अंदर क्या फर्क?']),('bio',['biopolish_surface','compaction_shrinkage'],'Biowash surface smoothness and compaction shrinkage differ.',['बायोवॉश के बाद','सिकुड़न भी रुकेगी?']),('jersey',['jersey_face_back'],'Single jersey front and back differ.',['सिंगल जर्सी','दो तरफ फर्क?'])]
        with tempfile.TemporaryDirectory() as tmp:
            for i,(title,facts,script,lines) in enumerate(samples):
                result=render_buyer_cover(Image.new('RGB',(1080,1920),'#456678'),lines,Path(tmp)/f'{i}.png',topic=topic(title,facts),script=script)
                self.assertTrue(result['comparison_illustrated'])
                for mode,output in result['outputs'].items():
                    image=Image.open(output['file']);self.assertEqual(list(image.size),output['size']);image.close()
                    self.assertLess(output['bytes'],2_000_000)
                    for item in output['text']:
                        x1,y1,x2,y2=item['bounds']
                        self.assertTrue(0<=x1<x2<=output['size'][0] and 0<=y1<y2<=output['size'][1])
                        if mode=='youtube':self.assertTrue(352<=x1<x2<=928)

    def test_unsupported_pricing_or_cut_phrase_stops_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            for lines in (['₹30 फर्क','GSM खरीदो'],['GSM तुलना','इसके बिना']):
                with self.assertRaises(ValueError):
                    render_buyer_cover(Image.new('RGB',(1080,1920),'white'),lines,Path(tmp)/'cover.png',script='')

    def test_unknown_lesson_keeps_relevant_scene_without_false_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=render_buyer_cover(Image.new('RGB',(1080,1920),'#4684a3'),['PRINT SAMPLE','WHAT TO CHECK?'],Path(tmp)/'cover.png',topic='print sample',script='Test a sample with your printer.')
            self.assertFalse(result['comparison_illustrated']);self.assertEqual(result['labels'],[])


if __name__=='__main__':unittest.main()


def rate_fact(name, amounts):
    return {'claim': f'BulkPlainTshirt.com live rate list checked on 05-Oct-2026: {name} (details). 10 or more pieces: '
                     f'₹{amounts[0]} per piece. Rates exclude 5% GST and delivery.',
            'source_url': 'https://www.bulkplaintshirt.com/', 'source_title': 'BulkPlainTshirt.com live rate list',
            'checked_on': '05-Oct-2026', 'limits': 'Website rate on the checked date only.', 'amounts': list(amounts)}


def rate_topic(facts):
    return SelectedTopic({'topic': 'Price', 'fact_ids': list(facts), 'evidence': facts})


class RatePairCoverTest(unittest.TestCase):
    # 5-Oct-2026: the AI final review refused a ₹107 vs ₹150 lesson's single-picture cover (Option B wants the
    # comparison), so a two-product price lesson gets one card per product with the rate the script says.
    FACTS = {'rate_non_bio_rneck': rate_fact('Non Bio Rneck', (107, 112, 131)),
             'rate_hoodie_320gsm_2': rate_fact('Hoodie 320gsm-2 in White, Navy', (337, 347, 414))}

    def test_two_spoken_rates_make_a_product_comparison(self):
        profile = supported_comparison(rate_topic(self.FACTS), 'Non Bio Rneck ₹107 hai, hoodie Rs 337 hai.')
        self.assertEqual(profile['key'], 'rate_pair')
        self.assertEqual(profile['labels'], ('₹107', '₹337'))
        self.assertEqual(profile['icons'], ('text:Non Bio Rneck', 'text:Hoodie 320gsm'))

    def test_no_comparison_unless_both_rates_are_spoken_or_with_one_rate(self):
        self.assertIsNone(supported_comparison(rate_topic(self.FACTS), 'Non Bio Rneck ₹107 hai, hoodie mehenga.'))
        self.assertIsNone(supported_comparison(rate_topic(self.FACTS), 'Non Bio Rneck ₹1070 hai, hoodie ₹3370.'))
        one = {'rate_non_bio_rneck': self.FACTS['rate_non_bio_rneck']}
        self.assertIsNone(supported_comparison(rate_topic(one), 'Non Bio Rneck ₹107 hai.'))

    @unittest.skipUnless(features.check('raqm'), 'Hindi shaping required')
    def test_the_comparison_cover_renders_names_and_rates_in_both_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = render_buyer_cover(None, ['HOODIE PRICE', 'फ़र्क कहाँ है?'], Path(tmp) / 'c.png',
                                      topic=rate_topic(self.FACTS), script='Non Bio Rneck ₹107, hoodie ₹337.')
            for size in ('portrait', 'youtube'):
                texts = [box['text'] for box in meta['outputs'][size]['text']]
                for expected in ('Non Bio Rneck', '₹107', '₹337'):
                    self.assertIn(expected, texts)
                self.assertTrue({'Hoodie 320gsm'} <= set(texts) or {'Hoodie', '320gsm'} <= set(texts))
            self.assertEqual(meta['comparison_key'], 'rate_pair')
