import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from tools import urgent_actions as urgent


class UrgentActionsTest(unittest.TestCase):
    def test_owner_sets_and_clears_only_its_own_items(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'urgent_actions.json'
            urgent.update('daily_short', {'replicate_credit': 'Insufficient credit'}, path, now='2026-09-11T18:48:12+05:30')
            urgent.update('health_watch', {'anthropic': 'credit empty'}, path, now='2026-09-12T09:00:00+05:30')
            urgent.update('daily_short', {'replicate_credit': 'Insufficient credit'}, path, now='2026-09-27T21:25:35+05:30')
            data = json.loads(path.read_text())
            self.assertEqual([item['id'] for item in data['items']], ['replicate_credit', 'anthropic'])
            replicate = data['items'][0]
            self.assertEqual(replicate['since'], '2026-09-11T18:48:12+05:30')
            self.assertEqual(replicate['last_seen'], '2026-09-27T21:25:35+05:30')
            self.assertEqual(replicate['action_url'], 'https://replicate.com/account/billing')
            urgent.update('health_watch', {}, path)
            self.assertEqual([item['id'] for item in urgent.load(path)['items']], ['replicate_credit'])
            urgent.update('daily_short', {}, path)
            self.assertEqual(urgent.load(path)['items'], [])

    def test_unknown_items_and_broken_files_never_publish_guesses(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'urgent_actions.json'
            path.write_text('{not json')
            self.assertEqual(urgent.load(path)['items'], [])
            urgent.update('health_watch', {'sarvam': '402', 'openai': 'down'}, path)
            self.assertEqual(urgent.load(path)['items'], [])

    def test_every_action_links_to_a_secure_page(self):
        for title, detail, url in urgent.ACTIONS.values():
            self.assertTrue(url.startswith('https://'))
            self.assertLess(len(title), 40)
            self.assertNotRegex(detail, r'\$|₹|\d{4,}')

    def test_only_billing_refusals_count_as_replicate_credit_problems(self):
        self.assertTrue(urgent.replicate_refused_credit(Exception('title: Insufficient credit\nstatus: 402')))
        self.assertTrue(urgent.replicate_refused_credit(SimpleNamespace(status=402)))
        self.assertFalse(urgent.replicate_refused_credit(Exception('429 rate limit')))
        self.assertFalse(urgent.replicate_refused_credit(Exception('Replicate returned empty audio')))


if __name__ == '__main__':
    unittest.main()
