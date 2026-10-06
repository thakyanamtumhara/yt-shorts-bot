import ast
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pytz

from tools.publish_slots import (
    IST, Taken, channel_taken, ledger_taken, pick_publish_time, table_publish_time, videos_taken,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'daily_short.py').read_text()
TREE = ast.parse(SOURCE)
TABLE = {node.targets[0].id: ast.literal_eval(node.value) for node in TREE.body
         if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
         and node.targets[0].id in ('PUBLISH_SLOTS', 'PUBLISH_SLOT_SCHEDULE', 'TIMEZONE')}


def table_slot(day):
    return TABLE['PUBLISH_SLOTS'][TABLE['PUBLISH_SLOT_SCHEDULE'].get(day.weekday(), 0)]


def at(day, hour, minute=0, second=0):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=IST)


def api_time(stamp):
    return stamp.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def scheduled(video_id, stamp):
    return {'id': video_id, 'status': {'privacyStatus': 'private', 'publishAt': api_time(stamp)},
            'snippet': {'publishedAt': api_time(stamp - timedelta(hours=4)), 'liveBroadcastContent': 'none'}}


def public(video_id, stamp):
    return {'id': video_id, 'status': {'privacyStatus': 'public'},
            'snippet': {'publishedAt': api_time(stamp), 'liveBroadcastContent': 'none'}}


class FakeRequest:
    def __init__(self, result, error):
        self.result, self.error = result, error

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class FakeResource:
    def __init__(self, client, name, result):
        self.client, self.name, self.result = client, name, result

    def list(self, **kwargs):
        self.client.calls.append((self.name, kwargs))
        return FakeRequest(self.result, self.client.errors.get(self.name))


class FakeYouTube:
    def __init__(self, videos=(), errors=None):
        self.items = list(videos)
        self.errors = errors or {}
        self.calls = []

    def channels(self):
        return FakeResource(self, 'channels', {'items': [{'contentDetails': {'relatedPlaylists': {'uploads': 'UUbot'}}}]})

    def playlistItems(self):
        return FakeResource(self, 'playlistItems', {'items': [{'contentDetails': {'videoId': v['id']}} for v in self.items]})

    def videos(self):
        return FakeResource(self, 'videos', {'items': self.items})


class FrozenDatetime(datetime):
    frozen = None

    @classmethod
    def now(cls, tz=None):
        return cls.frozen.astimezone(tz) if tz else cls.frozen


def publish_time_function():
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == 'get_publish_time')
    scope = {'pytz': pytz, 'datetime': FrozenDatetime, 'TIMEZONE': TABLE['TIMEZONE'],
             'PUBLISH_SLOTS': TABLE['PUBLISH_SLOTS'], 'PUBLISH_SLOT_SCHEDULE': TABLE['PUBLISH_SLOT_SCHEDULE'],
             'get_best_publish_slot': lambda youtube: None}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / 'daily_short.py'), 'exec'), scope)
    return scope['get_publish_time']


def write_ledger(folder, entries):
    path = Path(folder) / 'crosspost_ledger.json'
    path.write_text(json.dumps({'entries': entries}))
    return path


def run_publish_time(now, youtube, ledger_entries=()):
    FrozenDatetime.frozen = now
    out = io.StringIO()
    with tempfile.TemporaryDirectory() as folder, \
            patch('tools.publish_slots.LEDGER', write_ledger(folder, list(ledger_entries))), \
            contextlib.redirect_stdout(out):
        publish_at, publish_utc = publish_time_function()(youtube=youtube)
    return publish_at, publish_utc, out.getvalue()


MAIN_THU_18 = {'id': 'rates0015-20260917-short03-yt', 'platform': 'youtube', 'rail': 'api-scheduled',
               'due_ist': '2026-10-08 18:00', 'video_id': 'hcqhnfPewo8'}


class WeekdayTableTests(unittest.TestCase):
    def test_every_weekday_publishes_at_19(self):
        for weekday in range(7):
            self.assertEqual(TABLE['PUBLISH_SLOTS'][TABLE['PUBLISH_SLOT_SCHEDULE'][weekday]][:2], (19, 0), weekday)

    def test_retired_slots_stay_listed_for_old_analytics_buckets(self):
        self.assertEqual([slot[:2] for slot in TABLE['PUBLISH_SLOTS']], [(21, 30), (11, 0), (19, 0)])


class PickPublishTimeTests(unittest.TestCase):
    def test_today_two_shorts_already_at_19_send_the_next_one_to_21(self):
        taken = [Taken(at(6, 19), 'lnYzuwjXthE (scheduled)'), Taken(at(6, 19), 'HaOgzWlJGDM (scheduled)')]
        chosen, skipped = pick_publish_time(at(6, 18, 8), table_slot, taken)
        self.assertEqual(chosen, at(6, 21))
        self.assertEqual(len(skipped), 1)
        self.assertIn('Tue 06-Oct 19:00 taken by', skipped[0])

    def test_both_evening_slots_taken_moves_to_the_next_day(self):
        taken = [Taken(at(6, 19), 'a'), Taken(at(6, 19), 'b'), Taken(at(6, 21), 'c')]
        chosen, skipped = pick_publish_time(at(6, 18, 8), table_slot, taken)
        self.assertEqual(chosen, at(7, 19))
        self.assertEqual([note.split(' taken')[0] for note in skipped], ['Tue 06-Oct 19:00', 'Tue 06-Oct 21:00'])

    def test_exactly_two_hours_apart_is_free_and_one_minute_less_is_not(self):
        self.assertEqual(pick_publish_time(at(7, 15), table_slot, [Taken(at(7, 17), 'x')])[0], at(7, 19))
        self.assertEqual(pick_publish_time(at(7, 15), table_slot, [Taken(at(7, 17, 1), 'x')])[0], at(7, 21))

    def test_a_video_after_the_candidate_also_blocks_it(self):
        chosen, skipped = pick_publish_time(at(7, 15), table_slot, [Taken(at(7, 20, 30), 'later')])
        self.assertEqual(chosen, at(8, 19))
        self.assertEqual(len(skipped), 2)

    def test_wednesday_run_keeps_wednesday_19_after_the_stickers_short_at_11(self):
        videos = [public('ZVdl3mssPWw', at(7, 11, 0, 20)), public('lnYzuwjXthE', at(6, 19, 0, 30)),
                  public('HaOgzWlJGDM', at(6, 19, 0, 36))]
        now = at(7, 15, 5)
        chosen, skipped = pick_publish_time(now, table_slot, videos_taken(videos, now))
        self.assertEqual(chosen, at(7, 19))
        self.assertEqual(skipped, [])

    def test_thursday_run_gets_thursday_19_after_wednesdays_short(self):
        now = at(8, 15, 5)
        chosen, _ = pick_publish_time(now, table_slot, videos_taken([public('wed', at(7, 19, 0, 12))], now))
        self.assertEqual(chosen, at(8, 19))

    def test_thursday_run_moves_to_21_when_19_is_needed_by_another_video(self):
        now = at(8, 15, 5)
        for taken in ([Taken(at(8, 19), 'wed (scheduled Thu 08-Oct 19:00)')], ledger_entries_taken(now)):
            chosen, skipped = pick_publish_time(now, table_slot, taken)
            self.assertEqual(chosen, at(8, 21))
            self.assertIn('Thu 08-Oct 19:00 taken by', skipped[0])

    def test_a_short_public_seconds_after_19_still_leaves_21_free(self):
        now = at(6, 19, 7)
        taken = videos_taken([public('lnYzuwjXthE', at(6, 19, 0, 30)), public('HaOgzWlJGDM', at(6, 19, 0, 36))], now)
        chosen, skipped = pick_publish_time(now, table_slot, taken)
        self.assertEqual(chosen, at(6, 21))
        self.assertEqual(skipped, ['Tue 06-Oct 19:00 passed'])

    def test_twenty_minute_lead(self):
        chosen, skipped = pick_publish_time(at(7, 18, 41), table_slot, [])
        self.assertEqual(chosen, at(7, 21))
        self.assertEqual(skipped, ['Wed 07-Oct 19:00 under 20 min away'])
        self.assertEqual(pick_publish_time(at(7, 18, 40), table_slot, [])[0], at(7, 19))
        self.assertEqual(pick_publish_time(at(7, 20, 45), table_slot, [])[0], at(8, 19))

    def test_seven_day_limit(self):
        now = at(7, 15)
        blocked = [Taken(at(7, 20) + timedelta(days=offset), f'busy{offset}') for offset in range(8)]
        chosen, skipped = pick_publish_time(now, table_slot, blocked)
        self.assertIsNone(chosen)
        self.assertEqual(len(skipped), 16)
        chosen, _ = pick_publish_time(now, table_slot, blocked[:7])
        self.assertEqual(chosen, at(14, 19))

    def test_fallback_table_slot_is_todays_until_it_passes(self):
        self.assertEqual(table_publish_time(at(7, 15, 5), table_slot), at(7, 19))
        self.assertEqual(table_publish_time(at(7, 19), table_slot), at(8, 19))


def ledger_entries_taken(now):
    with tempfile.TemporaryDirectory() as folder:
        return ledger_taken(now, write_ledger(folder, [MAIN_THU_18]))


class ChannelReadTests(unittest.TestCase):
    def test_scheduled_recent_public_and_upcoming_premieres_are_taken(self):
        now = at(7, 15, 5)
        videos = [
            scheduled('sched', at(7, 19)),
            public('recent', at(6, 19, 0, 30)),
            public('old', at(4, 19)),
            {'id': 'held', 'status': {'privacyStatus': 'private'}, 'snippet': {'publishedAt': api_time(at(7, 9))}},
            {'id': 'unl', 'status': {'privacyStatus': 'unlisted'}, 'snippet': {'publishedAt': api_time(at(7, 9))}},
            {'id': 'prem', 'status': {'privacyStatus': 'public'},
             'snippet': {'publishedAt': api_time(at(1, 9)), 'liveBroadcastContent': 'upcoming'},
             'liveStreamingDetails': {'scheduledStartTime': api_time(at(9, 18, 30))}},
        ]
        youtube = FakeYouTube(videos)
        taken = channel_taken(youtube, now)
        self.assertEqual(sorted((item.at, item.source.split()[0]) for item in taken),
                         [(at(6, 19), 'recent'), (at(7, 19), 'sched'), (at(9, 18, 30), 'prem')])
        names = [name for name, _ in youtube.calls]
        self.assertEqual(names, ['channels', 'playlistItems', 'videos'])
        self.assertEqual(youtube.calls[0][1], {'part': 'contentDetails', 'mine': True})
        self.assertEqual(youtube.calls[1][1]['maxResults'], 25)
        self.assertEqual(set(youtube.calls[2][1]['part'].split(',')), {'status', 'snippet', 'liveStreamingDetails'})

    def test_a_token_without_a_channel_is_an_error(self):
        youtube = FakeYouTube()
        youtube.channels = lambda: FakeResource(youtube, 'channels', {'items': []})
        with self.assertRaises(LookupError):
            channel_taken(youtube, at(7, 15))


class LedgerTests(unittest.TestCase):
    def test_youtube_bookings_that_can_still_clash(self):
        now = at(7, 15)
        entries = [
            MAIN_THU_18,
            {'id': 'just-before', 'platform': 'youtube', 'due_ist': '2026-10-07 14:30'},
            {'id': 'long-gone', 'platform': 'youtube', 'due_ist': '2026-10-07 12:30'},
            {'id': 'reel', 'platform': 'instagram', 'due_ist': '2026-10-08 18:00'},
            {'id': 'main-short', 'platform': 'YouTube-main', 'due_ist': '2026-10-09 18:00'},
            {'id': 'bad-date', 'platform': 'youtube', 'due_ist': 'tomorrow'},
        ]
        with tempfile.TemporaryDirectory() as folder:
            taken = ledger_taken(now, write_ledger(folder, entries))
        self.assertEqual([(item.at, item.source) for item in taken], [
            (at(8, 18), 'hcqhnfPewo8 (ledger rates0015-20260917-short03-yt, Thu 08-Oct 18:00)'),
            (at(7, 14, 30), 'ledger just-before (Wed 07-Oct 14:30)'),
            (at(9, 18), 'ledger main-short (Fri 09-Oct 18:00)'),
        ])

    def test_missing_ledger_is_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(ledger_taken(at(7, 15), Path(folder) / 'none.json'), [])


class GetPublishTimeTests(unittest.TestCase):
    def test_clash_aware_time_and_reason_are_printed(self):
        youtube = FakeYouTube([scheduled('lnYzuwjXthE', at(6, 19)), scheduled('HaOgzWlJGDM', at(6, 19))])
        publish_at, publish_utc, log = run_publish_time(at(6, 18, 8), youtube)
        self.assertEqual(publish_at, at(6, 21))
        self.assertEqual(publish_utc, datetime(2026, 10, 6, 15, 30, tzinfo=timezone.utc))
        self.assertIn('Publish slot: Tue 06-Oct 21:00 IST — Tue 06-Oct 19:00 taken by HaOgzWlJGDM', log)

    def test_wednesday_daily_run_no_longer_lands_on_thursday(self):
        youtube = FakeYouTube([public('ZVdl3mssPWw', at(7, 11, 0, 20))])
        publish_at, _, log = run_publish_time(at(7, 15, 5), youtube, [MAIN_THU_18])
        self.assertEqual(publish_at, at(7, 19))
        self.assertIn('Publish slot: Wed 07-Oct 19:00 IST — free', log)

    def test_thursday_run_keeps_19_a_main_channel_booking_is_another_account(self):
        youtube = FakeYouTube([public('wed', at(7, 19, 0, 12))])
        publish_at, _, log = run_publish_time(at(8, 15, 5), youtube, [MAIN_THU_18])
        self.assertEqual(publish_at, at(8, 19))
        self.assertNotIn('hcqhnfPewo8', log)

    def test_thursday_run_steps_to_21_when_this_channel_has_a_short_at_18(self):
        youtube = FakeYouTube([public('wed', at(7, 19, 0, 12)), scheduled('extra', at(8, 18))])
        publish_at, _, log = run_publish_time(at(8, 15, 5), youtube)
        self.assertEqual(publish_at, at(8, 21))
        self.assertIn('19:00 taken by extra', log)

    def test_api_failure_keeps_the_table_slot_and_logs_one_line(self):
        for errors in ({'channels': OSError('network down')}, {'videos': RuntimeError('quotaExceeded')}):
            with self.subTest(errors=errors):
                youtube = FakeYouTube([scheduled('x', at(7, 19))], errors=errors)
                publish_at, _, log = run_publish_time(at(7, 15, 5), youtube, [MAIN_THU_18])
                self.assertEqual(publish_at, at(7, 19))
                warnings = [line for line in log.splitlines() if '⚠️' in line]
                self.assertEqual(len(warnings), 1)
                self.assertIn('Publish clash check skipped', warnings[0])
        publish_at, _, log = run_publish_time(at(7, 19, 30), None)
        self.assertEqual(publish_at, at(8, 19))
        self.assertIn('no YouTube client', log)

    def test_no_free_slot_in_seven_days_uses_the_table_slot(self):
        busy = [scheduled(f'busy{offset}', at(7, 20) + timedelta(days=offset)) for offset in range(8)]
        publish_at, _, log = run_publish_time(at(7, 15, 5), FakeYouTube(busy))
        self.assertEqual(publish_at, at(7, 19))
        self.assertIn('no free slot in the next 7 days', log)

    def test_upload_schedules_with_the_clash_aware_time(self):
        upload = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == 'upload_to_youtube')
        text = ast.get_source_segment(SOURCE, upload)
        self.assertIn('publish_ist, publish_utc = get_publish_time(youtube=youtube)', text)
        self.assertIn('body["status"]["publishAt"] = publish_utc.strftime(', text)


if __name__ == '__main__':
    unittest.main()
