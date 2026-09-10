import ast
import json
import re
from pathlib import Path
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'daily_short.py').read_text()
TREE=ast.parse(SOURCE)
DIMENSIONS=('hook','natural_feel','value','ending','viral_potential','visual_alignment')


def load_function(name,namespace=None):
    scope={'json':json,'re':re,**(namespace or {})}
    node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(ROOT/'daily_short.py'),'exec'),scope)
    return scope[name]


def valid_review(**updates):
    value={'approved':True,'scores':dict.fromkeys(DIMENSIONS,6),'total_score':36,'weakest':'hook','feedback':'Useful complete buyer question and practical sample check.'}
    value.update(updates)
    return value


def client_for(value=None,error=None,raw=None):
    create=Mock(side_effect=error) if error else Mock(return_value=SimpleNamespace(content=[SimpleNamespace(text=raw if raw is not None else json.dumps(value))]))
    return SimpleNamespace(messages=SimpleNamespace(create=create))


class DailyReviewSchemaTest(unittest.TestCase):
    def setUp(self):
        self.review=load_function('review_script')

    def call(self,value):
        return self.review(client_for(value),'Sample ka fit kaise check karein?','How do you check the fit of a sample?','sample fit')

    def test_helpful_buyer_question_needs_no_money_or_numbers(self):
        client=client_for(valid_review())
        result=self.review(client,'Sample ka fit kaise check karein?','How do you check a sample fit?','fit')
        self.assertEqual(result[:2],(True,36))
        prompt=client.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('Numbers are not required',prompt)
        self.assertIn('AI-generated scenes are illustrations',prompt)
        self.assertEqual(client.messages.create.call_args.kwargs['model'],'claude-opus-4-6')

    def test_explicit_rejection_stays_rejected_even_with_high_score(self):
        result=self.call(valid_review(approved=False,scores=dict.fromkeys(DIMENSIONS,9),total_score=54))
        self.assertEqual(result[:2],(False,54))

    def test_published_editing_label_is_blocked_before_reviewer_call(self):
        for phrase in ('Screenshot Moment: chest and length.', 'SCREENSHOT-MOMENT', 'स्क्रीनशॉट मोमेंट: चेस्ट और लंबाई।'):
            for voice, english in ((phrase, 'Check the sample.'), ('Sample check kar lo.', phrase)):
                with self.subTest(voice=voice, english=english):
                    client=client_for(valid_review())
                    self.assertEqual(self.review(client,voice,english,'fit')[:3],(False,0,'production_marker'))
                    client.messages.create.assert_not_called()

    def test_ordinary_screenshot_reference_is_not_the_editing_label(self):
        client=client_for(valid_review())
        self.assertTrue(self.review(client,'Size chart ka screenshot dekh lo.','Check the size chart screenshot.','fit')[0])

    def test_api_error_is_unapproved(self):
        result=self.review(client_for(error=RuntimeError('temporarily unavailable')),'voice','english','topic')
        self.assertEqual(result[:3],(False,0,'review_error'))

    def test_approved_must_be_a_json_boolean(self):
        for value in ('false','true',1,0,None,[],{}):
            with self.subTest(value=value):
                self.assertEqual(self.call(valid_review(approved=value))[:3],(False,0,'review_error'))

    def test_score_bounds_zero_and_wrong_numeric_types_fail_closed(self):
        for score in (0,-1,5,61,36.0,'36',True,None):
            with self.subTest(score=score):
                self.assertEqual(self.call(valid_review(total_score=score))[:3],(False,0,'review_error'))

    def test_missing_scores_wrong_dimension_and_out_of_range_fail_closed(self):
        bad_scores=[None,{},dict.fromkeys(DIMENSIONS,0),dict.fromkeys(DIMENSIONS,11),dict.fromkeys(DIMENSIONS,True)]
        renamed=dict.fromkeys(DIMENSIONS,6);renamed['story']=renamed.pop('hook');bad_scores.append(renamed)
        for scores in bad_scores:
            with self.subTest(scores=scores):
                self.assertEqual(self.call(valid_review(scores=scores))[:3],(False,0,'review_error'))

    def test_total_must_match_dimension_scores(self):
        self.assertEqual(self.call(valid_review(total_score=37))[:3],(False,0,'review_error'))

    def test_below_threshold_or_one_weak_dimension_cannot_be_approved(self):
        self.assertEqual(self.call(valid_review(scores=dict.fromkeys(DIMENSIONS,5),total_score=30))[:2],(False,30))
        scores=dict.fromkeys(DIMENSIONS,9);scores['value']=3
        self.assertEqual(self.call(valid_review(scores=scores,total_score=48))[:2],(False,48))

    def test_invalid_json_schema_and_missing_feedback_fail_closed(self):
        for raw in ('not-json','[]','null','{}'):
            result=self.review(client_for(raw=raw),'voice','english','topic')
            self.assertEqual(result[:3],(False,0,'review_error'))
        for update in ({'weakest':''},{'feedback':''},{'feedback':False}):
            self.assertEqual(self.call(valid_review(**update))[:3],(False,0,'review_error'))

    def test_fenced_valid_review_remains_supported(self):
        client=client_for(raw='```json\n'+json.dumps(valid_review())+'\n```')
        self.assertTrue(self.review(client,'voice','english','topic')[0])


class DailyAcceptanceFlowTest(unittest.TestCase):
    def run_acceptance(self,reviews,payloads=None):
        start=SOURCE.index('    # ── 3. Generate Script (with quality gate)')
        end=SOURCE.index('    script_voice = data["script_voice"]',start)
        block=textwrap.dedent(SOURCE[start:end])
        self.assertLess(end,SOURCE.index('    load_bg_music(music_mood)',end))
        candidate={'script_voice':'Pehle sample ka fit check kar lo.','script_english':'Check the fit of a sample first.'}
        payloads=payloads or [candidate]*len(reviews)
        responses=[SimpleNamespace(content=[SimpleNamespace(text=json.dumps(p))],usage=SimpleNamespace(input_tokens=1,output_tokens=1)) for p in payloads]
        paid=Mock();flags=Mock();review=Mock(side_effect=reviews)
        scope={'json':json,'re':__import__('re'),'time':SimpleNamespace(sleep=Mock()),'print':Mock(),'SCRIPT_MAX_ATTEMPTS':len(payloads),'VEO_CLIPS_PER_VIDEO':5,'fresh_topic':'Fit before the size letter','get_script_prompt':lambda topic:'Write useful buyer advice.','claude':SimpleNamespace(messages=SimpleNamespace(create=Mock(side_effect=responses))),'cost':SimpleNamespace(track_claude_call=Mock()),'review_script':review,'flag':flags,'paid_stage':paid}
        try:
            exec(compile(block+'\npaid_stage(data)\n','actual-main-script-acceptance','exec'),scope)
            return paid,flags,review,None
        except RuntimeError as exc:
            return paid,flags,review,exc

    def test_all_rejected_candidates_never_reach_paid_stage(self):
        paid,flags,review,error=self.run_acceptance([(False,42,'value','Unsupported prescription')]*3)
        self.assertIsInstance(error,RuntimeError)
        self.assertIn('No approved script',str(error))
        paid.assert_not_called();flags.assert_called_once_with('script_approved',False)
        self.assertEqual(review.call_count,3)

    def test_review_errors_and_zero_score_do_not_use_last_candidate(self):
        for result in ((False,0,'review_error','Review error'),(True,0,'',''),('false',54,'value','Rejected')):
            with self.subTest(result=result):
                paid,_,_,error=self.run_acceptance([result,result])
                self.assertIsInstance(error,RuntimeError);paid.assert_not_called()

    def test_rejected_then_approved_uses_only_the_approved_script(self):
        first={'script_voice':'Rejected claim.','script_english':'Rejected claim.'}
        second={'script_voice':'Helpful sample question.','script_english':'Helpful sample question.'}
        paid,flags,review,error=self.run_acceptance([(False,30,'value','Fix it'),(True,42,'hook','Useful')],[first,second])
        self.assertIsNone(error);paid.assert_called_once_with(second);flags.assert_not_called();self.assertEqual(review.call_count,2)

    def test_bad_writer_payloads_never_reach_reviewer_or_paid_stage(self):
        for payload in ([],None,{}, {'script_voice':False,'script_english':'text'}, {'script_voice':'','script_english':'text'}):
            with self.subTest(payload=payload):
                paid,_,review,error=self.run_acceptance([(True,42,'hook','Useful')],[payload])
                self.assertIsInstance(error,RuntimeError);review.assert_not_called();paid.assert_not_called()


class WriterContradictionRegressionTest(unittest.TestCase):
    def test_writer_uses_illustrations_and_complete_helpful_questions(self):
        prompt=load_function('get_script_prompt',{'BUSINESS_CONTEXT':'Plain T shirts.','get_source_channel_top_topics':lambda n:[],'_own_channel_performance_signal':lambda:'','extract_voice_corpus_style_hints':lambda:'','_get_recent_clip_prompts':lambda:''})('Printing samples')
        for poisoned in ('Study these REAL examples from the actual business owner','200 GSM minimum rakho','₹40,000 KI GALTI','500 PIECE BARBAAD','The damage/drama is visible in the very first frame','A ₹1.2 lakh loss → make it','return zero ho jayega','opens MID-ACTION, damage/drama already happening'):
            with self.subTest(poisoned=poisoned):self.assertNotIn(poisoned,prompt)
        self.assertIn('newly written TONE EXAMPLES',prompt)
        self.assertIn('not evidence of an actual test result',prompt)
        self.assertIn('A useful buyer question can be strong without money',prompt)
        self.assertIn('Never prescribe a universal GSM minimum',prompt)


if __name__=='__main__':unittest.main()
