import copy
import unittest
from datetime import datetime, timezone

from tools.topic_audience_signals import prompt_signals, topic_interest as _interest


def topic_interest(*args, **kwargs):
    return _interest(*args, now=datetime(2026, 9, 12, tzinfo=timezone.utc), **kwargs)


def record(media_id='1', **changes):
    return dict(media_id=media_id, checked=True, published_at='2026-09-01T00:00:00Z',
                checked_at='2026-09-03T12:00:00Z', hours_since_publish=60,
                title='A buyer question', reach=1000, shares=10, saves=5,
                views=2000, **changes)


class AudienceSignalsTests(unittest.TestCase):
    def test_sparse_sample_does_not_fall_back_to_big_view_totals(self):
        old = record('2')
        old.update(checked_at='2026-09-10T00:00:00Z', hours_since_publish=216, views=900000, shares=900)
        report = topic_interest([record(), old], n=5)
        self.assertEqual([r['media_id'] for r in report['leads']], ['1'])
        self.assertFalse(report['policy']['raw_views_fallback'])

    def test_measurement_age_not_current_age(self):
        report = topic_interest([record()])
        self.assertEqual(report['leads'][0]['measurement_age_hours'], 60)

    def test_reach_is_the_denominator_and_trial_unknown_stays_unknown(self):
        row = record()
        row.update(reach=100, views=100000)
        report = topic_interest([record('2'), row])
        self.assertEqual(report['excluded']['low_reach'], 1)
        lead = report['leads'][0]
        self.assertEqual(lead['trial_status'], 'unknown')
        self.assertEqual(lead['paid_organic_split'], 'unknown')
        self.assertEqual(lead['shares_per_1000_reached'], 10)

    def test_known_trial_excluded(self):
        report = topic_interest([record(trial=True), record('2', trial=False)])
        self.assertEqual(report['excluded']['trial'], 1)
        self.assertEqual(report['leads'][0]['trial_status'], 'not_trial')

    def test_missing_or_inconsistent_measurement_not_ranked(self):
        for key, value in [('checked_at', None), ('hours_since_publish', 400), ('reach', True), ('saves', None)]:
            with self.subTest(key=key):
                row = record()
                row[key] = value
                report = topic_interest([row])
                self.assertEqual(report['leads'], [])
                self.assertEqual(report['excluded']['invalid'], 1)

    def test_share_rate_then_save_rate_and_no_input_mutation(self):
        a, b = record(), record('2')
        b.update(reach=2000, shares=20, saves=100, views=2001)
        rows = [a, b]
        original = copy.deepcopy(rows)
        self.assertEqual(topic_interest(rows)['leads'][0]['media_id'], '2')
        self.assertEqual(rows, original)

    def test_stale_and_rejected_sources_do_not_seed_claims(self):
        old = record('2')
        old.update(published_at='2026-05-01T00:00:00Z', checked_at='2026-05-03T12:00:00Z')
        report = topic_interest([record(), old], excluded_media_ids=['1'])
        self.assertEqual(report['leads'], [])
        self.assertEqual(report['excluded']['known_rejected'], 1)
        self.assertEqual(report['excluded']['old_publication'], 1)

    def test_prompt_keeps_subject_and_counts_without_repeating_fake_loss(self):
        row = record()
        row['title'] = '₹50000 Loss — 240 GSM order rejected'
        report = topic_interest([row])
        prompt = prompt_signals(report)
        self.assertNotIn('historical_title_unverified', prompt['leads'][0])
        self.assertEqual(prompt['leads'][0]['subject_tags_from_title'], ['fabric_weight_gsm'])
        self.assertTrue(prompt['leads'][0]['incident_framing_unverified'])
        self.assertEqual(prompt['leads'][0]['shares'], 10)
        self.assertIn('historical_title_unverified', report['leads'][0])


if __name__ == '__main__':
    unittest.main()
