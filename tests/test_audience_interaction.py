import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from tools.audience_interaction import interaction_copy

ROOT = Path(__file__).resolve().parents[1]


class AudienceInteractionTests(unittest.TestCase):
    def test_each_reviewed_lesson_puts_its_exact_buyer_action_before_one_relevant_question(self):
        bank = json.loads((ROOT / 'daily_topic_lessons.json').read_text())
        questions = set()
        for seed in bank['seed_lessons']:
            brief = dict(seed, evidence={key: bank['facts'][key] for key in seed['fact_ids']})
            copy = interaction_copy(SimpleNamespace(brief=brief))
            answer, question = copy.split('\n\n')
            self.assertEqual(answer, seed['buyer_decision'])
            self.assertEqual(question.count('?'), 1)
            self.assertNotIn('sale91.com', copy.lower())
            self.assertNotIn('share', question.lower())
            questions.add(question)
        self.assertEqual(len(questions), len(bank['seed_lessons']))

    def test_missing_evidence_skips_public_question_instead_of_inventing_advice(self):
        for topic in ('unverified string', None, SimpleNamespace(brief={}),
                      SimpleNamespace(brief={'buyer_decision': 'Buy this', 'fact_ids': ['missing'], 'evidence': {}})):
            self.assertEqual(interaction_copy(topic), '')

    def test_new_evidenced_lesson_retains_exact_action_without_new_technical_claim(self):
        brief = {'buyer_decision': 'Check the supplied specification.', 'intent_key': 'new_lesson',
                 'fact_ids': ['fact'], 'evidence': {'fact': {'source_url': 'https://example.com/spec'}}}
        text = interaction_copy(SimpleNamespace(brief=brief))
        self.assertTrue(text.startswith(brief['buyer_decision'] + '\n\n'))
        self.assertTrue(text.endswith('Which part of this buying check would you like explained on a sample?'))

    def test_writer_and_reviewer_resolve_question_before_optional_interaction(self):
        source = (ROOT / 'daily_short.py').read_text()
        tree = ast.parse(source)
        functions = {node.name: ast.get_source_segment(source, node) for node in tree.body if isinstance(node, ast.FunctionDef)}
        writer = functions['get_script_prompt']
        reviewer = functions['review_script']
        self.assertIn('ANSWER THE BUYER EARLY', writer)
        self.assertNotIn('70-80%', writer)
        self.assertNotIn('#1 ranking signal', writer)
        self.assertIn('must come after the answer, never replace it', reviewer)
        self.assertIn('interaction = get_ig_cta_line(topic)', functions['cross_post_to_instagram'])
        self.assertIn('"message": comment_text', functions['_ig_post_publish_extras'])
        self.assertNotIn('Rate list + order', functions['_ig_post_publish_extras'])


if __name__ == '__main__':
    unittest.main()
