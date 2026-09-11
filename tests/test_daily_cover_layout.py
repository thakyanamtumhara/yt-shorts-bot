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
        rows=[('terry',['french_terry_fleece'],'French terry has inside loops; fleece has a brushed inside.','terry_vs_fleece'),('jersey',['jersey_face_back'],'Single jersey front and back look different.','jersey_sides'),('count',['cotton_count_direction'],'Ne cotton count and denier use different directions.','yarn_numbering'),('knit',['knit_loop_stretch'],'Knit loops stretch; that does not establish fibre content or elastane.','knit_vs_fibre')]
        for title,facts,script,expected in rows:
            selected=supported_comparison(topic(title,facts),script)
            self.assertEqual(selected['key'],expected)
            self.assertNotIn('हल्का',selected['labels'])

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
