import copy
import hashlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tools import reviewed_ai_reels as r

NOW = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
HASH = 'a' * 64
APPROVALS = {'batch_id': 'warehouse-20260911', 'approval_method': 'user_exact_selection', 'approved_at': '2026-09-11T04:08:05+00:00', 'videos': [{'id': 'WH09', 'version': '1.0', 'sha256': HASH, 'public_release_approved': True}]}
JOB = {'id': 'warehouse-wh09-v1-0', 'status': 'reviewed', 'account_id': r.ACCOUNT_ID, 'publish_at': '2026-09-23T15:00:00+05:30', 'caption': 'A useful complete lesson.\n' + r.DISCLOSURE, 'video_url': 'https://example.com/a.mp4', 'video_sha256': HASH, 'cover_url': 'https://example.com/a.jpg', 'cover_sha256': 'b' * 64, 'is_ai_generated': True, 'user_selection': {'batch_id': 'warehouse-20260911', 'id': 'WH09', 'version': '1.0', 'sha256': HASH, 'approved': True}}


class FakeBackend:
    def __init__(self):
        self.records = {}
        self.revision = 0
        self.fail_next = False

    def read(self, filename):
        row = self.records.get(filename)
        return (copy.deepcopy(row[0]), row[1]) if row else (None, None)

    def save(self, filename, state, etag):
        current = self.records.get(filename)
        if self.fail_next or (current[1] if current else None) != etag:
            self.fail_next = False
            raise r.Error('conditional failure')
        self.revision += 1
        tag = str(self.revision)
        self.records[filename] = (copy.deepcopy(state), tag)
        return tag


class FakeAPI:
    def __init__(self):
        self.posts = []
        self.label = True
        self.probe_label = True
        self.create_failure = False
        self.publish_failure = False
        self.bad_identity = False
        self.finished = 'FINISHED'
        self.caption = JOB['caption']
        self.comment_failure = False
        self.comment_text = None

    def identity(self):
        return {'id': 'wrong' if self.bad_identity else r.ACCOUNT_ID, 'username': r.ACCOUNT_USERNAME}

    def create(self, data):
        self.posts.append(('create', copy.deepcopy(data)))
        if self.create_failure:
            raise r.Error('timeout after create accepted')
        return '123'

    def status(self, container):
        return self.finished

    def publish(self, container):
        self.posts.append(('publish', container))
        if self.publish_failure:
            raise r.Error('timeout after publish accepted')
        return '456'

    def media(self, media):
        return {'id': media, 'username': r.ACCOUNT_USERNAME, 'owner': {'id': r.ACCOUNT_ID}, 'caption': self.caption, 'is_ai_generated': self.probe_label if media == r.DISCLOSURE_PROBE_ID else self.label}

    def comment(self, media_id, text):
        self.posts.append(('comment', media_id, text))
        self.comment_text = text
        if self.comment_failure:
            raise r.Error('timeout after comment accepted')
        return '789'

    def read_comment(self, comment_id):
        return {'id': comment_id, 'text': self.comment_text}


class ReviewedReelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.backend = FakeBackend()
        self.api = FakeAPI()
        self.store = r.StateStore(self.dir / (HASH + '.json'), self.backend)

    def tearDown(self):
        self.tmp.cleanup()

    def run_job(self, **kwargs):
        params = dict(api=self.api, job=copy.deepcopy(JOB), store=self.store, execute=True, clock=lambda: NOW, assets_check=lambda job: None)
        params.update(kwargs)
        return r.run_job(**params)

    def load(self, job):
        path = self.dir / 'job.json'
        path.write_text(json.dumps(job))
        return r.load_job(path, APPROVALS)

    def test_exact_job_loads(self):
        self.assertEqual(self.load(JOB), JOB)

    def test_selection_cannot_self_approve_different_version_or_video(self):
        for changes in [{'version': '1.1'}, {'id': 'WH10'}, {'sha256': 'c' * 64}, {'approved': 'true'}, {'batch_id': 'other'}]:
            with self.subTest(changes=changes):
                job = copy.deepcopy(JOB)
                job['user_selection'].update(changes)
                with self.assertRaises(r.Error): self.load(job)

    def test_native_flag_must_be_true_boolean(self):
        for flag in [False, 'true', 'false', 1, None]:
            job = copy.deepcopy(JOB); job['is_ai_generated'] = flag
            with self.assertRaises(r.Error): self.load(job)

    def test_changed_video_or_account_or_missing_disclosure_blocked(self):
        for key, value in [('video_sha256', 'c' * 64), ('account_id', '123'), ('caption', 'No disclosure'), ('publish_at', '2026-09-23T15:00:00'), ('publish_at', '2026-09-10T15:00:00+05:30')]:
            job = copy.deepcopy(JOB); job[key] = value
            with self.assertRaises(r.Error): self.load(job)

    def test_preflight_no_posts_or_claims(self):
        result = self.run_job(execute=False)
        self.assertTrue(result['dry_run'])
        self.assertEqual(self.api.posts, [])
        self.assertEqual(self.backend.records, {})

    def test_wrong_live_identity_blocks_before_assets_and_mutations(self):
        self.api.bad_identity = True
        with self.assertRaises(r.Error): self.run_job(assets_check=lambda job: self.fail('assets should not run'))
        self.assertEqual(self.api.posts, [])

    def test_changed_download_blocks_all_mutations(self):
        def fail(job): raise r.Error('hash mismatch')
        with self.assertRaises(r.Error): self.run_job(assets_check=fail)
        self.assertEqual(self.api.posts, [])
        self.assertEqual(self.backend.records, {})

    def test_future_publication_blocked(self):
        with self.assertRaises(r.Error): self.run_job(clock=lambda: datetime(2026, 9, 20, tzinfo=timezone.utc))
        self.assertEqual(self.api.posts, [])

    def test_prepare_only_flagged_container_no_publish_or_remote_state(self):
        local = r.StateStore(self.dir / 'prepare.json')
        result = self.run_job(store=local, prepare_only=True)
        self.assertEqual([p[0] for p in self.api.posts], ['create'])
        self.assertEqual(self.api.posts[0][1]['is_ai_generated'], 'true')
        self.assertEqual(result['phase'], 'prepared_native_requested')
        self.assertFalse(result['native_ai_verified'])
        self.assertEqual(self.backend.records, {})
        self.run_job(store=local, prepare_only=True)
        self.assertEqual(len(self.api.posts), 1)

    def test_durable_prepare_reuses_container_across_fresh_runner_directories(self):
        self.backend.mode = 'prepare_only'
        first = r.StateStore(self.dir / 'runner-one' / (HASH + '.json'), self.backend)
        second = r.StateStore(self.dir / 'runner-two' / (HASH + '.json'), self.backend)
        self.run_job(store=first, prepare_only=True)
        result = self.run_job(store=second, prepare_only=True)
        self.assertEqual(result['parent_id'], '123')
        self.assertEqual([p[0] for p in self.api.posts], ['create'])
        with self.assertRaises(r.Error): self.run_job(store=second, prepare_only=False)

    def test_prepare_state_cannot_be_promoted(self):
        local = r.StateStore(self.dir / 'prepare.json')
        result = self.run_job(store=local, prepare_only=True)
        with self.assertRaises(r.Error): r.bind_state(result, JOB, 'publish')
        with self.assertRaises(r.Error): self.run_job(prepare_only=True)

    def test_publication_requires_durable_backend(self):
        with self.assertRaises(r.Error): self.run_job(store=r.StateStore(self.dir / 'local.json'))
        self.assertEqual(self.api.posts, [])

    def test_preflight_checks_actual_native_label_route(self):
        self.api.probe_label = None
        with self.assertRaises(r.Error): self.run_job(execute=False)
        self.assertEqual(self.api.posts, [])
        self.assertEqual(self.backend.records, {})

    def test_disappearing_live_native_disclosure_route_blocks_before_post(self):
        self.api.probe_label = None
        with self.assertRaises(r.Error): self.run_job()
        self.assertEqual(self.api.posts, [])
        self.assertEqual(self.backend.records, {})

    def test_success_exact_native_label_and_rerun_no_duplicate(self):
        result = self.run_job()
        self.assertTrue(result['native_ai_verified'])
        self.assertEqual([p[0] for p in self.api.posts], ['create', 'publish'])
        self.run_job()
        self.assertEqual(len(self.api.posts), 2)

    def test_create_timeout_is_not_retried_or_stripped(self):
        self.api.create_failure = True
        with self.assertRaises(r.Error): self.run_job()
        with self.assertRaises(r.Error): self.run_job()
        self.assertEqual(len(self.api.posts), 1)
        self.assertEqual(self.api.posts[0][1]['is_ai_generated'], 'true')

    def test_publish_timeout_is_not_retried(self):
        self.api.publish_failure = True
        with self.assertRaises(r.Error): self.run_job()
        with self.assertRaises(r.Error): self.run_job()
        self.assertEqual(len(self.api.posts), 2)
        self.assertEqual(self.store.read()['pending'], 'media')

    def test_false_or_missing_native_readback_retains_id_and_only_rechecks(self):
        for value in [False, None, 'true']:
            self.api.label = value
            with self.assertRaises(r.Error): self.run_job()
            self.assertEqual(self.store.read()['media_id'], '456')
        self.assertEqual(len(self.api.posts), 2)
        self.api.label = True
        self.assertTrue(self.run_job()['native_ai_verified'])
        self.assertEqual(len(self.api.posts), 2)

    def test_saved_state_rejects_copy_schedule_and_version_changes(self):
        self.run_job()
        for key, value in [('caption', JOB['caption'] + '!'), ('publish_at', '2026-09-24T15:00:00+05:30'), ('id', 'renamed-job')]:
            changed = dict(JOB, **{key: value})
            with self.assertRaises(r.Error): self.run_job(job=changed)
        self.assertEqual(len(self.api.posts), 2)

    def test_state_failure_before_post_blocks_post(self):
        self.backend.fail_next = True
        with self.assertRaises(r.Error): self.run_job()
        self.assertEqual(self.api.posts, [])

    def test_save_failure_after_create_keeps_remote_ambiguous_claim(self):
        original = self.api.create
        def create(data):
            result = original(data)
            self.backend.fail_next = True
            return result
        self.api.create = create
        with self.assertRaises(r.Error): self.run_job()
        with self.assertRaises(r.Error): self.run_job()
        self.assertEqual(len(self.api.posts), 1)

    def test_two_stores_cannot_win_same_claim(self):
        other = r.StateStore(self.dir / 'other' / (HASH + '.json'), self.backend)
        a, b = self.store.read(), other.read()
        self.store.save(r.bind_state(a, JOB, 'publish'))
        with self.assertRaises(r.Error): other.save(r.bind_state(b, JOB, 'publish'))

    def test_expired_container_never_recreated(self):
        self.api.finished = 'EXPIRED'
        with self.assertRaises(r.Error): self.run_job()
        with self.assertRaises(r.Error): self.run_job()
        self.assertEqual(len(self.api.posts), 1)

    def test_queue_rejects_same_content_under_different_job_id(self):
        (self.dir / 'a.json').write_text(json.dumps(JOB))
        (self.dir / 'b.json').write_text(json.dumps(dict(JOB, id='another')))
        with self.assertRaises(r.Error): r.run_queue(self.dir, APPROVALS, self.backend, execute=True)

    def test_full_hash_download_verifies_and_rejects_mutation(self):
        class Response:
            status_code = 200
            url = 'https://example.com/file'
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def iter_content(self, size): return iter([b'abc', b'def'])
        with patch.object(r.requests, 'get', return_value=Response()):
            r.download_checked('https://example.com/file', self.dir/'file', hashlib.sha256(b'abcdef').hexdigest(), 10)
            with self.assertRaises(r.Error): r.download_checked('https://example.com/file', self.dir/'file', '0'*64, 10)
            with self.assertRaises(r.Error): r.download_checked('https://example.com/file', self.dir/'file', hashlib.sha256(b'abcdef').hexdigest(), 5)

    def kind_job(self, kind):
        job, approval = copy.deepcopy(JOB), copy.deepcopy(APPROVALS)
        job['media_kind'] = kind
        job['is_ai_generated'] = kind != 'original_recording'
        job['caption'] = 'Buyer lesson.' + ('\n' + r.CLONED_VOICE_DISCLOSURE if kind == 'cloned_voice_screen_recording' else '')
        approval['videos'][0]['media_kind'] = kind
        return job, approval

    def load_with_approval(self, job, approval):
        path = self.dir / 'job.json'
        path.write_text(json.dumps(job))
        return r.load_job(path, {approval['batch_id']: approval})

    def test_original_requires_independently_approved_kind(self):
        job, approval = self.kind_job('original_recording')
        self.assertEqual(self.load_with_approval(job, approval), job)
        with self.assertRaises(r.Error): self.load_with_approval(job, APPROVALS)
        job['is_ai_generated'] = True
        with self.assertRaises(r.Error): self.load_with_approval(job, approval)

    def test_screen_recording_requires_native_ai_and_truthful_disclosure(self):
        job, approval = self.kind_job('cloned_voice_screen_recording')
        self.assertEqual(self.load_with_approval(job, approval), job)
        for caption in ['No disclosure', r.DISCLOSURE, r.CLONED_VOICE_DISCLOSURE + r.DISCLOSURE]:
            with self.assertRaises(r.Error): self.load_with_approval(dict(job, caption=caption), approval)
        with self.assertRaises(r.Error): self.load_with_approval(dict(job, is_ai_generated=False), approval)

    def test_approval_batches_leave_legacy_receipt_unchanged(self):
        original = self.dir / 'approval.json'
        original.write_text(json.dumps(APPROVALS))
        batches = self.dir / 'batches'; batches.mkdir()
        _, approval = self.kind_job('original_recording')
        approval['batch_id'] = 'new-batch'
        (batches / 'new.json').write_text(json.dumps(approval))
        loaded = r.load_approval_batches(original, batches)
        self.assertEqual(loaded[APPROVALS['batch_id']], APPROVALS)
        self.assertEqual(len(loaded), 2)
        (batches / 'duplicate.json').write_text(json.dumps(approval))
        with self.assertRaises(r.Error): r.load_approval_batches(original, batches)

    def test_original_publish_sends_false_and_requires_false_readback(self):
        job, _ = self.kind_job('original_recording')
        self.api.caption, self.api.label, self.api.probe_label = job['caption'], False, None
        result = self.run_job(job=job)
        self.assertEqual(self.api.posts[0][1]['is_ai_generated'], 'false')
        self.assertTrue(result['native_ai_verified'])
        self.api.label = True
        with self.assertRaises(r.Error): self.run_job(job=job)
        self.assertEqual(len(self.api.posts), 2)

    def test_original_first_comment_verified_once(self):
        job, _ = self.kind_job('original_recording')
        job['first_comment'] = 'Arrival date or quantity?'
        self.api.caption, self.api.label = job['caption'], False
        self.assertTrue(self.run_job(job=job)['comment_verified'])
        self.assertTrue(self.run_job(job=job)['comment_verified'])
        self.assertEqual([p[0] for p in self.api.posts], ['create', 'publish', 'comment'])

    def test_ambiguous_first_comment_blocks_duplicate_media_and_comment(self):
        job, _ = self.kind_job('original_recording')
        job['first_comment'] = 'Arrival date or quantity?'
        self.api.caption, self.api.label, self.api.comment_failure = job['caption'], False, True
        with self.assertRaises(r.Error): self.run_job(job=job)
        with self.assertRaises(r.Error): self.run_job(job=job)
        self.assertEqual(self.store.read()['pending'], 'comment')
        self.assertEqual([p[0] for p in self.api.posts], ['create', 'publish', 'comment'])

    def test_queue_due_time_lateness_and_retry_do_not_duplicate(self):
        job, approval = self.kind_job('original_recording')
        (self.dir / 'job.json').write_text(json.dumps(job))
        self.api.caption, self.api.label = job['caption'], False
        early = lambda: datetime(2026, 9, 23, 9, 29, tzinfo=timezone.utc)
        result = r.run_queue(self.dir, approval, self.backend, api_factory=lambda: self.api, execute=True, clock=early, assets_check=lambda j: None)
        self.assertEqual(result['results'], [])
        self.assertEqual(self.api.posts, [])
        kwargs = dict(api_factory=lambda: self.api, execute=True, clock=lambda: NOW, assets_check=lambda j: None)
        result = r.run_queue(self.dir, approval, self.backend, **kwargs)
        self.assertEqual(result['results'][0]['due_lateness_seconds'], 1800)
        self.assertTrue(r.run_queue(self.dir, approval, self.backend, **kwargs)['results'][0]['already_verified'])
        self.assertEqual([p[0] for p in self.api.posts], ['create', 'publish'])

    def test_blocked_original_comment_does_not_stop_due_legacy_job(self):
        first, approval = self.kind_job('original_recording')
        first['first_comment'] = 'Arrival date or quantity?'
        self.api.caption, self.api.label, self.api.comment_failure = first['caption'], False, True
        with self.assertRaises(r.Error): self.run_job(job=first)
        blocked_state = copy.deepcopy(self.store.read())
        legacy = copy.deepcopy(JOB)
        legacy.update(id='warehouse-wh08-v1-0', video_sha256='c' * 64, publish_at='2026-09-23T15:01:00+05:30')
        legacy['user_selection'].update(id='WH08', sha256='c' * 64)
        approval['videos'].append({'id':'WH08','version':'1.0','sha256':'c' * 64,'public_release_approved':True})
        queue = self.dir / 'queue'; queue.mkdir()
        (queue / 'a.json').write_text(json.dumps(first))
        (queue / 'b.json').write_text(json.dumps(legacy))
        legacy_api = FakeAPI()
        params = dict(api_factory=lambda: legacy_api, execute=True, clock=lambda: NOW, assets_check=lambda j: None)
        result = r.run_queue(queue, approval, self.backend, **params)
        self.assertEqual(result['errors'][0]['job_id'], first['id'])
        self.assertIn('comment POST is ambiguous', result['errors'][0]['error'])
        self.assertEqual(result['results'][0]['job_id'], legacy['id'])
        self.assertTrue(result['results'][0]['native_ai_verified'])
        self.assertEqual([p[0] for p in legacy_api.posts], ['create', 'publish'])
        self.assertEqual(self.store.read(), blocked_state)
        again = r.run_queue(queue, approval, self.backend, **params)
        self.assertEqual(len(again['errors']), 1)
        self.assertTrue(again['results'][0]['already_verified'])
        self.assertEqual(len(legacy_api.posts), 2)
        self.assertEqual(self.store.read(), blocked_state)

    def test_queue_errors_make_cli_fail_visibly(self):
        with patch.object(r, 'load_approval_batches', return_value={}), patch.object(r, 'backend_from_environment', return_value=self.backend), patch.object(r, 'run_queue', return_value={'results':[], 'errors':[{'job_id':'blocked','error':'ambiguous comment'}]}), patch('builtins.print'):
            self.assertEqual(r.main(['--queue-dir', str(self.dir)]), 1)


class S3Tests(unittest.TestCase):
    def test_absent_exact_key_does_not_call_denied_get(self):
        class S3:
            def list_objects_v2(self, **kw):
                self.kw = kw
                return {'Contents': [{'Key': kw['Prefix'] + '.other'}]}
            def get_object(self, **kw): raise AssertionError('Get must never happen for absent exact key')
        client = S3()
        self.assertEqual(r.S3Backend(client).read(HASH + '.json'), (None, None))
        self.assertEqual(client.kw['MaxKeys'], 1)
        self.assertTrue(client.kw['Prefix'].startswith('p/'))

    def test_existing_get_denied_is_not_missing(self):
        class S3:
            def list_objects_v2(self, **kw): return {'Contents': [{'Key': kw['Prefix']}]}
            def get_object(self, **kw): raise PermissionError('403')
        with self.assertRaises(r.Error): r.S3Backend(S3()).read(HASH + '.json')

    def test_remote_prepare_namespace_cannot_write_publish_state(self):
        class S3:
            def put_object(self, **kw): self.kw = kw; return {'ETag': 'new'}
        client = S3(); backend = r.S3Backend(client, mode='prepare_only')
        state = r.bind_state(None, JOB, 'prepare_only')
        backend.save(HASH + '.json', state, None)
        self.assertEqual(client.kw['Key'], r.PREFIX + '/prepare/' + HASH + '.json')
        with self.assertRaises(r.Error): backend.save(HASH + '.json', r.bind_state(None, JOB, 'publish'), None)
        with self.assertRaises(r.Error): r.S3Backend(client).save(HASH + '.json', state, None)

    def test_only_sanitized_state_is_saved_conditionally(self):
        class S3:
            def put_object(self, **kw): self.kw = kw; return {'ETag': 'new'}
        client = S3(); backend = r.S3Backend(client)
        state = r.bind_state(None, JOB, 'publish')
        backend.save(HASH + '.json', state, None)
        self.assertEqual(client.kw['IfNoneMatch'], '*')
        self.assertNotIn('caption', json.loads(client.kw['Body']))
        backend.save(HASH + '.json', state, 'old')
        self.assertEqual(client.kw['IfMatch'], 'old')
        with self.assertRaises(r.Error): backend.save(HASH + '.json', dict(state, caption='private copy'), 'old')


if __name__ == '__main__':
    unittest.main()
