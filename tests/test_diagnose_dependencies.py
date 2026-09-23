from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import patch

from tools import diagnose_dependencies as probe


class DependencyProbeTest(unittest.TestCase):
    def test_invoice_summary_does_not_leak_billing_details_or_treat_string_as_bool(self):
        result = probe.subscription_summary({
            'tier': 'creator', 'status': 'active', 'character_count': 5, 'character_limit': 100,
            'has_open_invoices': 'false', 'email': 'private@example.com',
            'open_invoices': [{'amount_due_cents': 999, 'payment_intent_status': 'processing',
                               'payment_intent_statusses': ['succeeded']}],
        })
        self.assertIsNone(result['has_open_invoices'])
        self.assertEqual(result['has_open_invoices_type'], 'str')
        self.assertEqual(result['chars_left'], 95)
        self.assertEqual(result['open_invoice_count'], 1)
        self.assertNotIn('999', json.dumps(result))
        self.assertNotIn('private@example.com', json.dumps(result))

    def test_without_explicit_option_only_get_requests_occur(self):
        with patch.dict('os.environ', {'ELEVENLABS_API_KEY': 'secret'}), patch.object(
                probe, 'request', return_value=(200, 'application/json', b'{}')) as request:
            with redirect_stdout(StringIO()):
                self.assertEqual(probe.main([]), 0)
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(len(call.args) == 2 for call in request.call_args_list))

    def test_one_failed_billing_probe_does_not_retry_or_print_error_message(self):
        values = [(200, 'application/json', b'{}'), (200, 'application/json', b'{}'),
                  (402, 'application/json', b'{"detail":{"status":"payment_required","message":"secret"}}')]
        output = StringIO()
        with patch.dict('os.environ', {'ELEVENLABS_API_KEY': 'secret'}), patch.object(
                probe, 'request', side_effect=values) as request, redirect_stdout(output):
            self.assertEqual(probe.main(['--tts-probe']), 1)
        self.assertEqual(request.call_count, 3)
        self.assertIn('payment_required', output.getvalue())
        self.assertNotIn('secret', output.getvalue())
        self.assertEqual(request.call_args.args[2]['model_id'], 'eleven_v3')

    def test_success_requires_real_audio_bytes(self):
        for data, expected in [(b'ID3' + b'a' * 300, 0), (b'{"ok":true}', 1)]:
            values = [(200, 'application/json', b'{}'), (200, 'application/json', b'{}'),
                      (200, 'audio/mpeg', data)]
            with self.subTest(data=data[:3]), patch.dict('os.environ', {'ELEVENLABS_API_KEY': 'secret'}), patch.object(
                    probe, 'request', side_effect=values), redirect_stdout(StringIO()):
                self.assertEqual(probe.main(['--tts-probe']), expected)


if __name__ == '__main__':
    unittest.main()
