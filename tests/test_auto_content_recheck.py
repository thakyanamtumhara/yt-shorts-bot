import copy
from contextlib import redirect_stdout
from io import StringIO
from datetime import datetime
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools import auto_content_recheck as check
from tools import audit_short_output as audit
from tests.test_audit_short_output import archive_data, manifest_data, run_data

NOW = datetime(2026, 9, 24, 20, 37, tzinfo=check.IST)


def run(day=24, **changes):
    return {**run_data(), 'created_at': f'2026-09-{day}T09:00:00Z', **changes}


class RecheckTests(unittest.TestCase):
    def test_prior_day_and_explicit_tests_do_not_satisfy_today(self):
        self.assertIsNone(check.latest_today([run(23), run(inputs={'test_mode': True})], NOW))
        self.assertEqual(check.latest_today([run(23), run()], NOW)['created_at'], run()['created_at'])

    @patch.object(audit, 'github_json', return_value={'workflow_runs': []})
    def test_missing_after_deadline_is_issue_but_morning_is_not_due(self, api):
        self.assertEqual(check.daily_check(NOW)['state'], 'issue')
        self.assertEqual(check.daily_check(NOW.replace(hour=8))['state'], 'not_due')

    @patch.object(audit, 'github_json')
    def test_sunday_is_not_due_without_network(self, api):
        self.assertEqual(check.daily_check(NOW.replace(day=27))['state'], 'not_due')
        api.assert_not_called()

    @patch.object(audit, 'github_json')
    def test_latest_failure_and_running_are_not_green(self, api):
        for change, expected in [({'conclusion': 'failure'}, 'issue'), ({'status': 'in_progress'}, 'pending')]:
            api.side_effect = [{'workflow_runs': [run(**change)]}, run(**change)]
            with patch.object(audit, 'download_artifact', side_effect=RuntimeError('No archive')):
                self.assertEqual(check.daily_check(NOW)['state'], expected)

    def test_overnight_schedule_and_completion_check_source_day(self):
        morning = NOW.replace(day=25, hour=1)
        with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'schedule', 'RECHECK_TRIGGER_CREATED_AT': ''}):
            self.assertEqual(check.check_clock(morning).date().isoformat(), '2026-09-24')
        with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'RECHECK_TRIGGER_CREATED_AT': ''}):
            self.assertEqual(check.check_clock(morning), morning)
        with patch.dict(os.environ, {'RECHECK_TRIGGER_CREATED_AT': '2026-09-24T13:00:00Z'}):
            self.assertEqual(check.check_clock(morning).date().isoformat(), '2026-09-24')

    def test_failed_saved_audio_cannot_pass_even_when_all_platforms_pass(self):
        manifest = manifest_data()
        manifest['run_flags'] = {'native_audio_review': {'passed': False}}
        with patch.object(audit, 'github_json', side_effect=[{'workflow_runs': [run()]}, run()]), \
                patch.object(audit, 'download_artifact', return_value=(archive_data(manifest), 7)), \
                patch.object(check, 'youtube_check', return_value=check.result('ok', 'ready')) as yt, \
                patch.object(check, 'instagram_check', return_value=check.result('ok', 'ready')), \
                patch.object(audit, 'facebook_readback', return_value={'publication_confirmed_by_api': True, 'public_by_api': True}):
            observed = NOW.replace(day=25, hour=1)
            output = check.daily_check(NOW, observed)
            self.assertEqual(yt.call_args.args[1], observed)
        self.assertEqual(output['state'], 'issue')
        self.assertEqual(output['checks']['audio']['state'], 'issue')
        self.assertEqual(output['artifact_id'], 7)

    def test_feed_stale_receipt_and_unposted_eligible_draft_not_green(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'ig_drafts').mkdir()
            (root / 'ig_drafts/2026-09-22.json').write_text(json.dumps(
                {'posted': True, 'posted_at': '2026-09-23T13:00:00+05:30', 'media_id': '123'}))
            (root / 'ig_drafts/2026-09-23.json').write_text(json.dumps({'posted': False, 'image_urls': ['image']}))
            self.assertEqual(check.feed_receipts(root, NOW), ([], 1))
            with patch.object(check, 'workflow_state', return_value=(check.result('ok', 'run'), run())):
                self.assertEqual(check.feed_check(NOW, root)['state'], 'issue')

    @patch.object(audit, 'instagram_readback')
    def test_real_photo_feed_needs_owner_but_not_ai_flag_or_reels(self, read):
        read.return_value = {'owner_verified': True, 'permalink': 'https://www.instagram.com/p/abcde/',
                             'media_type': 'IMAGE', 'media_product_type': 'FEED', 'native_ai_disclosure': 'unverified'}
        self.assertEqual(check.instagram_check({}, feed=True)['state'], 'ok')
        self.assertEqual(check.instagram_check({})['state'], 'issue')
        read.return_value['owner_verified'] = False
        self.assertEqual(check.instagram_check({}, feed=True)['state'], 'issue')

    @patch.object(audit, 'youtube_readback')
    def test_native_ack_survives_only_expected_elapsed_schedule(self, read):
        vid = 'abcdefghijk'
        expected = {'privacyStatus': 'private', 'publishAt': '2026-09-24T13:30:00Z',
                    'containsSyntheticMedia': True, 'embeddable': True}
        saved = audit.status_verification(vid, expected, expected, {'id': vid, 'status': expected})
        manifest = {'source_posts': {'bot_youtube': vid}, 'run_flags': {'youtube_status_evidence': saved}}
        before = copy.deepcopy(manifest)
        read.return_value = {'video_id': vid, 'channel_id': audit.BOT_CHANNEL, 'privacyStatus': 'public',
                             'uploadStatus': 'processed', 'title_matches_manifest': True, 'mutable_status': {'privacyStatus': 'public', 'embeddable': True}}
        self.assertEqual(check.youtube_check(manifest, NOW)['state'], 'ok')
        self.assertEqual(check.youtube_check(manifest, NOW.replace(hour=18))['state'], 'issue')
        read.return_value['mutable_status']['containsSyntheticMedia'] = False
        self.assertEqual(check.youtube_check(manifest, NOW)['state'], 'issue')
        self.assertEqual(manifest, before)

    def test_dry_run_writes_report_and_real_summary_has_one_verified_delivery(self):
        with TemporaryDirectory() as tmp, patch.object(check, 'ROOT', Path(tmp)):
            old = Path.cwd()
            os.chdir(tmp)
            try:
                for dry in (True, False):
                    with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'fake', 'TELEGRAM_ALERT_CHAT_ID': '123'}), \
                            patch.object(check, 'daily_check', return_value=check.result('not_due', 'Later')), \
                            patch.object(check, 'feed_check', return_value=check.result('not_due', 'Later')), \
                            patch.object(check, 'blog_check', return_value=check.result('not_due', 'Later')), \
                            patch('social_watch._tg_direct', return_value=(True, 'sent')) as send, redirect_stdout(StringIO()):
                        self.assertEqual(check.main(['--dry-run'] if dry else []), 0)
                        report = json.loads(Path('auto-content-recheck/report.json').read_text())
                        self.assertEqual(report['notification_delivered'], not dry)
                        self.assertEqual(send.call_count, 0 if dry else 1)
            finally:
                os.chdir(old)

    def test_safe_exception_does_not_print_payload(self):
        def fail():
            raise RuntimeError('secret-token/full-request')
        self.assertNotIn('secret', json.dumps(check.guarded(fail)))

    def test_blog_old_article_is_not_today_and_availability_is_not_quality(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'blog_history.json').write_text(json.dumps([{'date': '2026-09-23T20:00:00+05:30',
                                                               'modified': '2026-09-24'}]))
            self.assertEqual(check.blog_check(NOW.replace(day=25), root)['state'], 'issue')

    def test_blog_cadence_matches_mon_wed_fri_and_can_be_configured(self):
        for day in (22, 24, 26, 27):
            self.assertEqual(check.blog_check(NOW.replace(day=day))['state'], 'not_due')
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {'BLOG_WEEKDAYS': '3'}):
            root = Path(tmp)
            (root / 'blog_history.json').write_text('[]')
            self.assertEqual(check.blog_check(NOW, root)['state'], 'issue')

    def test_failed_run_still_checks_successful_instagram_receipt(self):
        manifest = manifest_data()
        manifest['run_flags'] = {'native_audio_review': {'passed': True}}
        failed = run(conclusion='failure')
        with patch.object(audit, 'github_json', side_effect=[{'workflow_runs': [failed]}, failed]), \
                patch.object(audit, 'download_artifact', return_value=(archive_data(manifest), 7)), \
                patch.object(check, 'youtube_check', return_value=check.result('issue', 'Held')), \
                patch.object(check, 'instagram_check', return_value=check.result('ok', 'Published')) as ig, \
                patch.object(audit, 'facebook_readback', return_value={'publication_confirmed_by_api': True, 'public_by_api': True}):
            output = check.daily_check(NOW)
        self.assertEqual(output['state'], 'issue')
        self.assertEqual(output['conclusion'], 'failure')
        self.assertEqual(output['checks']['instagram']['state'], 'ok')
        ig.assert_called_once()


if __name__ == '__main__':
    unittest.main()
