import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

import health_watch
import social_watch


class HealthAlertsTest(unittest.TestCase):
    def test_old_invoice_flag_does_not_mean_active_clone_is_down(self):
        subscription = {'tier': 'creator', 'status': 'active', 'has_open_invoices': True,
                        'can_use_professional_voice_cloning': True,
                        'character_count': 1038, 'character_limit': 258644}
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'test'}), \
             patch.object(health_watch, '_json_req', side_effect=[
                 (200, subscription), (200, {'category': 'professional', 'name': 'Ketu Original'})]):
            result = health_watch.check_elevenlabs()
        self.assertFalse(result.ok)
        self.assertEqual(result.severity, health_watch.WARN)
        self.assertEqual(result.extra['chars_left'], 257606)

    def test_real_payment_or_clone_failure_still_blocks(self):
        subscription = {'tier': 'creator', 'status': 'past_due', 'has_open_invoices': True,
                        'can_use_professional_voice_cloning': True,
                        'character_count': 1038, 'character_limit': 258644}
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'test'}), \
             patch.object(health_watch, '_json_req', return_value=(200, subscription)):
            result = health_watch.check_elevenlabs()
        self.assertFalse(result.ok)
        self.assertEqual(result.severity, health_watch.CRITICAL)
        subscription['status'] = 'active'
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'test'}), \
             patch.object(health_watch, '_json_req', side_effect=[(200, subscription), (403, {})]):
            result = health_watch.check_elevenlabs()
        self.assertFalse(result.ok)
        self.assertEqual(result.severity, health_watch.CRITICAL)

    def run_watch(self, state, delivered, result):
        with patch.object(health_watch, 'load_state', return_value=state), \
             patch.object(health_watch, 'save_state'), \
             patch.object(health_watch, 'run_all', return_value=[result]), \
             patch.object(health_watch, 'send_telegram', return_value=delivered), \
             patch('sys.argv', ['health_watch.py']), \
             patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
            return health_watch.main()

    def test_failed_notice_does_not_suppress_later_retry(self):
        result = health_watch.Result('elevenlabs', 'Voice', health_watch.CRITICAL, False, 'blocked')
        state = {'checks': {'elevenlabs': {'ok': False}}}
        self.assertEqual(self.run_watch(state, False, result), 1)
        self.assertNotIn('last_still_down_date', state)
        self.assertEqual(self.run_watch(state, True, result), 1)
        self.assertIn('last_still_down_date', state)

    def test_green_only_after_delivery(self):
        result = health_watch.Result('elevenlabs', 'Voice', health_watch.CRITICAL, True, 'ready')
        state = {'checks': {'elevenlabs': {'ok': True}}}
        self.assertEqual(self.run_watch(state, False, result), 1)
        self.assertNotIn('last_green_date', state)
        self.assertEqual(self.run_watch(state, True, result), 0)
        self.assertIn('last_green_date', state)

    def test_critical_probe_exception_never_passes(self):
        def check_elevenlabs():
            raise ValueError('bad provider schema')
        with patch.object(health_watch, 'CHECKS', (check_elevenlabs,)):
            result = health_watch.run_all()[0]
        self.assertFalse(result.ok)
        self.assertEqual(result.severity, health_watch.CRITICAL)

    def test_private_recipient_verification_blocks_public_send(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'test', 'TELEGRAM_ALERT_CHAT_ID': '123'}), \
             patch.object(social_watch, 'get_json', return_value={'ok': True, 'result': {'id': 123, 'type': 'channel'}}), \
             patch.object(social_watch.urllib.request, 'urlopen') as send:
            ok, _ = social_watch._tg_direct('test', 'body')
        self.assertFalse(ok)
        send.assert_not_called()

    def test_receipt_must_match_recipient(self):
        payload = {'ok': True, 'result': {'message_id': 88, 'chat': {'id': 999}}}
        response = io.BytesIO(json.dumps(payload).encode())
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'test', 'TELEGRAM_ALERT_CHAT_ID': '123'}), \
             patch.object(social_watch, 'get_json', return_value={'ok': True, 'result': {'id': 123, 'type': 'private', 'username': 'BulkPlainTshirt_com'}}), \
             patch.object(social_watch.urllib.request, 'urlopen', return_value=response):
            ok, _ = social_watch._tg_direct('test', 'body')
        self.assertFalse(ok)


if __name__ == '__main__':
    unittest.main()
