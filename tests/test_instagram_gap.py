from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
from types import SimpleNamespace
import unittest

from tools.instagram_gap import GapReadError, PostGap, hold_reason, newest_post_time, parse_graph_time

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'test-token-should-never-print'
UTC = timezone.utc


def response(status, payload):
    return SimpleNamespace(status_code=status, json=lambda: payload)


def graph(stamp):
    return stamp.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%S+0000')


class ParseTests(unittest.TestCase):
    def test_graph_timestamps(self):
        expected = datetime(2026, 10, 6, 9, 35, 12, tzinfo=UTC)
        for text in ('2026-10-06T09:35:12+0000', '2026-10-06T09:35:12Z', '2026-10-06T15:05:12+05:30'):
            self.assertEqual(parse_graph_time(text), expected, text)
        for text in ('', None, 'yesterday', '2026-10-06T09:35:12'):
            self.assertIsNone(parse_graph_time(text), text)


class NewestPostTests(unittest.TestCase):
    def test_reads_the_newest_of_the_latest_media(self):
        calls = []
        stamps = ['2026-10-06T09:35:12+0000', '2026-10-06T13:37:00+0000', '2026-10-05T13:30:00+0000']

        def get(url, **kwargs):
            calls.append((url, kwargs))
            return response(200, {'data': [{'id': str(n), 'timestamp': s} for n, s in enumerate(stamps)]})

        self.assertEqual(newest_post_time(get, '1784', TOKEN), datetime(2026, 10, 6, 13, 37, tzinfo=UTC))
        url, kwargs = calls[0]
        self.assertEqual(url, 'https://graph.facebook.com/v21.0/1784/media')
        self.assertEqual(kwargs['params']['fields'], 'timestamp')
        self.assertEqual(kwargs['params']['access_token'], TOKEN)
        self.assertNotIn(TOKEN, url)

    def test_an_account_without_media_has_no_newest_post(self):
        self.assertIsNone(newest_post_time(lambda url, **kw: response(200, {'data': []}), '1784', TOKEN))

    def test_errors_never_carry_the_token(self):
        def refused(url, **kwargs):
            return response(400, {'error': {'message': f'Invalid OAuth access token {TOKEN}'}})

        def offline(url, **kwargs):
            raise ConnectionError(f'Max retries exceeded with url: /media?access_token={TOKEN}')

        for get, expected in ((refused, 'HTTP 400 Invalid OAuth access token ***'), (offline, 'ConnectionError')):
            with self.assertRaises(GapReadError) as caught:
                newest_post_time(get, '1784', TOKEN)
            self.assertIn(expected, str(caught.exception))
            self.assertNotIn(TOKEN, str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)

    def test_unreadable_timestamps_are_an_error(self):
        with self.assertRaises(GapReadError):
            newest_post_time(lambda url, **kw: response(200, {'data': [{'id': '1'}]}), '1784', TOKEN)


class HoldReasonTests(unittest.TestCase):
    now = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)

    def test_two_hour_gap(self):
        self.assertIsNone(hold_reason(None, self.now))
        self.assertIsNone(hold_reason(self.now - timedelta(hours=2), self.now))
        reason = hold_reason(self.now - timedelta(minutes=119), self.now)
        self.assertIn('119 min ago (06-Oct 13:31 IST)', reason)
        self.assertIn('waits for the next run', reason)
        self.assertIn('0 min ago', hold_reason(self.now + timedelta(minutes=3), self.now))


class PostGapTests(unittest.TestCase):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

    def test_reads_once_and_counts_this_runs_own_post(self):
        reads = []
        gap = PostGap(lambda: reads.append(1) or self.now - timedelta(hours=3))
        self.assertEqual(reads, [])
        self.assertIsNone(gap.hold(self.now))
        gap.posted(self.now)
        self.assertIn('0 min ago', gap.hold(self.now + timedelta(seconds=30)))
        self.assertIsNone(gap.hold(self.now + timedelta(hours=2)))
        self.assertEqual(reads, [1])

    def test_a_failed_read_keeps_the_job_queued(self):
        def broken():
            raise GapReadError('HTTP 500 temporarily unavailable')
        reason = PostGap(broken).hold(self.now)
        self.assertIn('could not read the newest Instagram post time (HTTP 500 temporarily unavailable)', reason)
        self.assertIn('ValueError', PostGap(lambda: int('x')).hold(self.now))


FAKE_REQUESTS = '''
import json, os

class Response:
    def __init__(self, status, payload):
        self.status_code, self._payload, self.text = status, payload, json.dumps(payload)
    def json(self):
        return self._payload
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %s" % self.status_code)

def _log(method, url, values):
    with open(os.environ["FAKE_CALLS"], "a") as handle:
        handle.write(json.dumps({"method": method, "url": url, "fields": (values or {}).get("fields")}) + "\\n")

def get(url, params=None, timeout=None):
    _log("GET", url, params)
    params = params or {}
    if url.endswith("/media") and params.get("fields") == "timestamp":
        newest = os.environ["FAKE_IG_NEWEST"]
        if newest == "error":
            return Response(500, {"error": {"message": "temporarily unavailable"}})
        return Response(200, {"data": [] if newest == "none" else [{"id": "1", "timestamp": newest}]})
    if params.get("fields") == "status_code,status":
        return Response(200, {"status_code": "FINISHED"})
    if params.get("fields") == "permalink":
        return Response(200, {"permalink": "https://www.instagram.com/reel/TEST/"})
    return Response(404, {"error": {"message": "unexpected GET"}})

def post(url, data=None, timeout=None):
    _log("POST", url, data)
    if url.endswith("/media_publish"):
        return Response(200, {"id": "media-1"})
    if url.endswith("/media"):
        return Response(200, {"id": "container-1"})
    return Response(200, {"id": "comment-1"})
'''


def queue_script():
    lines = (ROOT / '.github' / 'workflows' / 'publish_queue.yml').read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "python3 - << 'PY'")
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == 'PY')
    return textwrap.dedent('\n'.join(lines[start + 1:end])) + '\n'


class PublishQueueWorkflowTests(unittest.TestCase):
    def run_queue(self, newest, jobs):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            (work / 'fakes').mkdir()
            (work / 'fakes' / 'requests.py').write_text(FAKE_REQUESTS)
            (work / 'publish_queue').mkdir()
            for name, job in jobs.items():
                (work / 'publish_queue' / name).write_text(json.dumps(job))
            calls = work / 'calls.jsonl'
            calls.touch()
            env = {**os.environ, 'PYTHONPATH': f"{work / 'fakes'}{os.pathsep}{ROOT}", 'IG_TOKEN': TOKEN,
                   'IG_BIZ': '1784', 'FAKE_IG_NEWEST': newest, 'FAKE_CALLS': str(calls)}
            result = subprocess.run([sys.executable, '-'], input=queue_script(), cwd=work, env=env,
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(TOKEN, result.stdout + result.stderr)
            log = [json.loads(line) for line in calls.read_text().splitlines()]
            queued = sorted(path.name for path in (work / 'publish_queue').glob('*.json'))
            done = sorted(path.name for path in (work / 'publish_queue' / 'done').glob('*.json'))
            return result.stdout, log, queued, done

    def due_jobs(self):
        job = {'video_url': 'https://www.bulkplaintshirt.com/p/x.mp4', 'caption': 'c', 'first_comment': 'q'}
        return {'a.json': {**job, 'publish_at': '2026-01-01 00:00'}, 'b.json': job}

    def test_a_post_under_two_hours_old_keeps_every_job_queued(self):
        newest = graph(datetime.now(UTC) - timedelta(minutes=30))
        out, log, queued, done = self.run_queue(newest, self.due_jobs())
        self.assertEqual((queued, done), (['a.json', 'b.json'], []))
        self.assertEqual(out.count('holding:'), 2)
        self.assertIn('min ago', out)
        self.assertEqual([c for c in log if c['method'] == 'POST'], [])
        self.assertEqual(len([c for c in log if c['fields'] == 'timestamp']), 1)

    def test_an_old_post_lets_one_job_out_and_the_next_waits(self):
        for newest in (graph(datetime.now(UTC) - timedelta(hours=3)), 'none'):
            with self.subTest(newest=newest):
                out, log, queued, done = self.run_queue(newest, self.due_jobs())
                self.assertEqual((queued, done), (['b.json'], ['a.json']))
                self.assertEqual([c['url'].rsplit('/', 1)[1] for c in log if c['method'] == 'POST'],
                                 ['media', 'media_publish', 'comments'])
                self.assertIn('holding: publish_queue/b.json - newest Instagram post went out 0 min ago', out)

    def test_an_unreadable_account_keeps_the_job_queued(self):
        out, log, queued, done = self.run_queue('error', self.due_jobs())
        self.assertEqual((queued, done), (['a.json', 'b.json'], []))
        self.assertIn('could not read the newest Instagram post time (HTTP 500 temporarily unavailable)', out)
        self.assertEqual([c for c in log if c['method'] == 'POST'], [])

    def test_a_job_not_yet_due_never_reads_the_account(self):
        job = {'video_url': 'https://www.bulkplaintshirt.com/p/x.mp4', 'publish_at': '2099-01-01 00:00'}
        out, log, queued, done = self.run_queue('error', {'later.json': job})
        self.assertEqual((queued, done, log), (['later.json'], [], []))
        self.assertIn('until 2099-01-01 00:00 IST', out)


if __name__ == '__main__':
    unittest.main()
