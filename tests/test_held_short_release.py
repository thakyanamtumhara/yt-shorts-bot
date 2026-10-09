import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import held_short_release as h
from tools.daily_topic_selection import announcement_topic, load_bank
from tools.publish_slots import Taken

VIDEO, COVER = b'held video bytes', b'held cover bytes'
SHA_V, SHA_C = hashlib.sha256(VIDEO).hexdigest(), hashlib.sha256(COVER).hexdigest()
KEY = 'dtf_black_tee_real_2026_10_07'
TOPIC = str(announcement_topic(load_bank(), KEY))
JOB = {'id': 'dtf-real-b', 'run_id': '37611306924', 'announcement': KEY, 'video_sha256': SHA_V, 'cover_sha256': SHA_C,
       'instagram_title': 'DTF Print on Black T-Shirt — Why the White Layer Matters',
       'youtube_at': '2026-10-10T17:00:00+05:30', 'meta_at': '2026-10-10T19:00:00+05:30'}
REVIEW = {'run_id': '37611306924', 'video_sha256': SHA_V, 'topic': TOPIC,
          'titles': {'youtube': 'काली T-Shirt पर DTF Print फीका? White Layer का असली सच', 'instagram': 'old title'},
          'youtube_description': 'Will a DTF print look faded?\n\nDTF sheets: dtf.bulkplaintshirt.com', 'youtube_tags': ['dtf printing']}
NOW = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)


def fake_get(review=REVIEW, video=VIDEO, status=200):
    def get(url, timeout=None):
        name = url.rsplit('/', 1)[1]
        body = {'video.mp4': video, 'cover.png': COVER, 'review.json': json.dumps(review).encode()}[name]
        return SimpleNamespace(status_code=status, content=body)
    return get


class FakeStore:
    def __init__(self, state=None):
        self.state, self.saves = dict(state or {}), []

    def read(self):
        return json.loads(json.dumps(self.state))

    def save(self, state):
        self.state = json.loads(json.dumps(state))
        self.saves.append(self.state)


class FakeYouTube:
    def __init__(self, status):
        self.status = status

    def videos(self):
        return self

    def list(self, **kwargs):
        return SimpleNamespace(execute=lambda: {'items': [{'status': self.status}]})


class FakeDs:
    AUTO_PIN_COMMENT = True

    def __init__(self, status=None):
        self.calls = []
        self.youtube = FakeYouTube(status or {'privacyStatus': 'private', 'publishAt': '2026-10-10T11:30:00Z',
                                              'containsSyntheticMedia': True})
        self.fb_result, self.tg_result = 'fb123', 77

    def get_youtube_service(self):
        return self.youtube

    def get_publish_time(self, youtube=None):
        raise AssertionError('the real slot picker must be replaced by the job time')

    def upload_to_youtube(self, youtube, path, title, description, tags, topic=''):
        at = self.get_publish_time(youtube=youtube)
        self.calls.append(('upload', title, description, tags, str(topic), at[1].isoformat(), Path(path).read_bytes()))
        return 'AbCdEfGhIjK', 'https://youtube.com/shorts/AbCdEfGhIjK'

    def get_pin_tail(self, topic):
        return 'Kaunsa design best laga?'

    def pin_comment(self, youtube, video_id, comment_text=None):
        self.calls.append(('pin', video_id, comment_text))

    def upload_thumbnail(self, youtube, video_id, path):
        self.calls.append(('thumb', video_id, Path(path).read_bytes()))

    def add_to_playlist(self, youtube, video_id, topic):
        self.calls.append(('playlist', video_id))

    def campaign_line(self, topic, key):
        return 'Naya: DTF - dtf.bulkplaintshirt.com' if key == 'ig_line' else ''

    def publish_fb_reel(self, path, caption, cover_path=None):
        self.calls.append(('fb', caption, Path(cover_path).read_bytes()))
        return self.fb_result

    def post_telegram_channel(self, path, caption):
        self.calls.append(('tg', caption))
        return self.tg_result


class JobsFileTest(unittest.TestCase):
    def test_the_real_jobs_file_holds_the_three_approved_shorts(self):
        jobs = h.load_jobs()
        self.assertEqual([j['id'] for j in jobs], ['dtf-real-b', 'dtf-real-a', 'dtf-real-c'])
        bank = load_bank()
        for job in jobs:
            self.assertTrue(str(announcement_topic(bank, job['announcement'])))

    def test_refuses_a_time_before_the_approval_and_a_short_hash(self):
        data = json.loads(h.JOBS.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'jobs.json'
            early = json.loads(json.dumps(data))
            early['jobs'][0]['meta_at'] = '2026-10-09T10:00:00+05:30'
            path.write_text(json.dumps(early))
            with self.assertRaisesRegex(h.ReleaseError, 'after the owner'):
                h.load_jobs(path)
            short = json.loads(json.dumps(data))
            short['jobs'][1]['video_sha256'] = 'abc'
            path.write_text(json.dumps(short))
            with self.assertRaisesRegex(h.ReleaseError, 'full sha256'):
                h.load_jobs(path)


class FetchTest(unittest.TestCase):
    def test_a_changed_file_is_never_released(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(h.ReleaseError, 'not the approved file'):
                h.fetch_held(JOB, folder, get=fake_get(video=b'another render'))
            other = dict(REVIEW, run_id='1')
            with self.assertRaisesRegex(h.ReleaseError, 'another file'):
                h.fetch_held(JOB, folder, get=fake_get(review=other))
            with self.assertRaisesRegex(h.ReleaseError, 'HTTP 404'):
                h.fetch_held(JOB, folder, get=fake_get(status=404))


@patch('tools.youtube_status.post_daily_ai_comment', lambda youtube, video_id, text, post, scheduled: post())
class YouTubeTest(unittest.TestCase):
    def test_books_the_exact_file_at_the_job_time_once(self):
        ds, store = FakeDs(), FakeStore()
        with tempfile.TemporaryDirectory() as folder, patch('tools.publish_slots.channel_taken', return_value=[]):
            self.assertEqual(h.release_youtube(JOB, ds, store, folder, now=NOW, get=fake_get()), 'AbCdEfGhIjK')
            upload = ds.calls[0]
            self.assertEqual(upload[:5], ('upload', REVIEW['titles']['youtube'], REVIEW['youtube_description'],
                                          ['dtf printing'], TOPIC))
            self.assertEqual(upload[5], '2026-10-10T11:30:00+00:00')
            self.assertEqual(upload[6], VIDEO)
            self.assertEqual([c[0] for c in ds.calls], ['upload', 'pin', 'thumb', 'playlist'])
            self.assertEqual(ds.calls[2][2], COVER)
            self.assertTrue(store.saves[0]['youtube']['pending'])
            self.assertEqual(store.state['youtube']['video_id'], 'AbCdEfGhIjK')
            self.assertTrue(all(store.state['youtube']['readback'].values()))
            ds.calls.clear()
            self.assertEqual(h.release_youtube(JOB, ds, store, folder, now=NOW, get=fake_get()), 'AbCdEfGhIjK')
            self.assertEqual(ds.calls, [])

    def test_another_video_within_2_h_stops_before_any_upload(self):
        ds, store = FakeDs(), FakeStore()
        taken = [Taken(datetime(2026, 10, 10, 12, 30, tzinfo=timezone.utc), 'xyz (scheduled)')]
        with tempfile.TemporaryDirectory() as folder, patch('tools.publish_slots.channel_taken', return_value=taken):
            with self.assertRaisesRegex(h.ReleaseError, 'within 2 h'):
                h.release_youtube(JOB, ds, store, folder, now=NOW, get=fake_get())
        self.assertEqual((ds.calls, store.saves), ([], []))

    def test_a_left_over_attempt_is_never_uploaded_again(self):
        ds, store = FakeDs(), FakeStore({'youtube': {'pending': True}})
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(h.ReleaseError, 'reconcile'):
                h.release_youtube(JOB, ds, store, folder, now=NOW, get=fake_get())
        self.assertEqual(ds.calls, [])

    def test_an_omitted_ai_label_readback_is_not_a_failure_but_an_explicit_false_is(self):
        omitted = FakeDs({'privacyStatus': 'private', 'publishAt': '2026-10-10T11:30:00Z'})
        with tempfile.TemporaryDirectory() as folder, patch('tools.publish_slots.channel_taken', return_value=[]):
            self.assertEqual(h.release_youtube(JOB, omitted, FakeStore(), folder, now=NOW, get=fake_get()), 'AbCdEfGhIjK')
            refused = FakeDs({'privacyStatus': 'private', 'publishAt': '2026-10-10T11:30:00Z', 'containsSyntheticMedia': False})
            with self.assertRaisesRegex(h.ReleaseError, 'readback'):
                h.release_youtube(JOB, refused, FakeStore(), folder, now=NOW, get=fake_get())

    def test_a_wrong_readback_is_reported(self):
        ds, store = FakeDs({'privacyStatus': 'private', 'publishAt': '2026-10-10T13:30:00Z', 'containsSyntheticMedia': True}), FakeStore()
        with tempfile.TemporaryDirectory() as folder, patch('tools.publish_slots.channel_taken', return_value=[]):
            with self.assertRaisesRegex(h.ReleaseError, 'readback'):
                h.release_youtube(JOB, ds, store, folder, now=NOW, get=fake_get())
        self.assertEqual(store.state['youtube']['video_id'], 'AbCdEfGhIjK')


class MetaTest(unittest.TestCase):
    def run_meta(self, ds, stores, now):
        with tempfile.TemporaryDirectory() as folder:
            return h.release_meta_due([JOB], ds, lambda job_id: stores.setdefault(job_id, FakeStore()), folder,
                                      now=now, get=fake_get())

    def test_facebook_and_telegram_once_with_the_daily_caption(self):
        ds, stores = FakeDs(), {}
        due = datetime(2026, 10, 10, 13, 40, tzinfo=timezone.utc)
        report = self.run_meta(ds, stores, due)
        self.assertEqual(report, ['dtf-real-b: facebook posted (fb123)', 'dtf-real-b: telegram posted (77)'])
        caption = ('DTF Print on Black T-Shirt — Why the White Layer Matters\n\nWill a DTF print look faded?\n\n'
                   'Naya: DTF - dtf.bulkplaintshirt.com')
        self.assertEqual(ds.calls, [('fb', caption, COVER), ('tg', caption)])
        ds.calls.clear()
        self.assertEqual(self.run_meta(ds, stores, due + timedelta(minutes=30)), [])
        self.assertEqual(ds.calls, [])

    def test_not_due_yet_and_too_late(self):
        ds, stores = FakeDs(), {}
        self.assertEqual(self.run_meta(ds, stores, datetime(2026, 10, 10, 13, 0, tzinfo=timezone.utc)), [])
        late = self.run_meta(ds, stores, datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(len(late), 1)
        self.assertIn('missed', late[0])
        self.assertEqual(ds.calls, [])

    def test_a_failure_is_recorded_and_not_retried(self):
        ds, stores = FakeDs(), {}
        ds.fb_result = None
        due = datetime(2026, 10, 10, 13, 40, tzinfo=timezone.utc)
        report = self.run_meta(ds, stores, due)
        self.assertIn('facebook FAILED', report[0])
        self.assertEqual(stores['dtf-real-b'].state['facebook']['failed'], 'no id returned')
        ds.calls.clear()
        ds.fb_result = 'fb999'
        self.assertEqual(self.run_meta(ds, stores, due + timedelta(minutes=20)), [])
        self.assertEqual(ds.calls, [])


if __name__ == '__main__':
    unittest.main()
