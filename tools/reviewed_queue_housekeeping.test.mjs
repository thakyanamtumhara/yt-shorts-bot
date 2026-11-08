import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync, mkdirSync, mkdtempSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {digest} from '../cloudflare/reviewed-instagram-trigger/worker.mjs';
import {archive, check, counts, QUEUE, BATCHES} from './reviewed_queue_housekeeping.mjs';

const ACCOUNT = '17841407981790313';
const NOW = Date.parse('2026-11-08T12:00:00+05:30');
const sha = n => n.toString(16).padStart(64, '0');

function job(id, batch, video, publishAt, n) {
  return {id, status: 'reviewed', account_id: ACCOUNT, publish_at: publishAt, caption: 'AI voice: my own cloned voice.', video_url: 'https://example.com/v.mp4', video_sha256: sha(n), cover_url: 'https://example.com/c.jpg', cover_sha256: sha(n + 1000), is_ai_generated: true, media_kind: 'cloned_voice_screen_recording', first_comment: 'hi', user_selection: {batch_id: batch, id: video, version: 'v1', sha256: sha(n), approved: true}};
}

async function verifiedState(item) {
  return {format: 'reviewed-ai-reel-v1', mode: 'publish', job_id: item.id, account_id: ACCOUNT, video_sha256: item.video_sha256, job_sha256: await digest(item), selection_sha256: await digest(item.user_selection), native_ai_requested: true, native_ai_verified: true, phase: 'published_verified', media_id: '111', comment_id: '222', comment_verified: true};
}

function receipt(batch, videos) {
  return {batch_id: batch, approval_method: 'user_exact_selection', approved_at: '2026-11-01T10:00:00+05:30', videos: videos.map(([id, n]) => ({id, version: 'v1', sha256: sha(n), public_release_approved: true}))};
}

function tree() {
  const root = mkdtempSync(join(tmpdir(), 'rq-'));
  for (const dir of [QUEUE, QUEUE + '/archive', BATCHES, BATCHES + '/archive']) mkdirSync(join(root, dir), {recursive: true});
  return root;
}

const put = (root, path, value) => writeFileSync(join(root, path), JSON.stringify(value));

function stateServer(states) {
  return async url => {
    const key = url.split('/').pop().split('.json')[0];
    if (!Object.hasOwn(states, key)) return new Response('', {status: 404});
    return new Response(JSON.stringify(states[key]), {status: 200});
  };
}

test('archives only settled, verified jobs and receipts whose every video is released', async () => {
  const root = tree();
  const done = job('a-done', 'old', 'x', '2026-11-07T11:00:00+05:30', 1);
  const fresh = job('a-fresh', 'old2', 'y', '2026-11-08T11:00:00+05:30', 2);
  const missing = job('a-missing', 'old3', 'z', '2026-11-06T11:00:00+05:30', 3);
  const wrong = job('a-wrong', 'old4', 'w', '2026-11-06T11:00:00+05:30', 4);
  for (const item of [done, fresh, missing, wrong]) put(root, `${QUEUE}/${item.id}.json`, item);
  put(root, `${BATCHES}/old.json`, receipt('old', [['x', 1]]));
  put(root, `${BATCHES}/old2.json`, receipt('old2', [['y', 2]]));
  put(root, `${BATCHES}/partial.json`, receipt('partial', [['p', 5], ['q', 6]]));
  put(root, `${QUEUE}/archive/p.json`, job('p', 'partial', 'p', '2026-11-01T11:00:00+05:30', 5));
  put(root, `${BATCHES}/raw0004-20260929.json`, receipt('raw0004-20260929', [['f', 7]]));
  put(root, `${QUEUE}/archive/f.json`, job('f', 'raw0004-20260929', 'f', '2026-09-30T11:00:00+05:30', 7));
  const wrongState = {...await verifiedState(wrong), job_sha256: sha(99)};
  const states = {[sha(1)]: await verifiedState(done), [sha(2)]: await verifiedState(fresh), [sha(4)]: wrongState};
  const out = await archive({root, now: NOW, request: stateServer(states)});
  assert.deepEqual(out.moved_jobs, ['a-done.json']);
  assert.deepEqual(out.moved_receipts, ['old.json']);
  assert.ok(existsSync(join(root, QUEUE, 'archive/a-done.json')) && existsSync(join(root, BATCHES, 'archive/old.json')));
  const why = Object.fromEntries(out.kept.map(item => [item.file, item.why]));
  assert.equal(why['a-fresh.json'], 'not_settled');
  assert.equal(why['a-missing.json'], 'state_http_404');
  assert.equal(why['a-wrong.json'], 'state_binding_mismatch');
  for (const name of ['old2.json', 'partial.json', 'raw0004-20260929.json']) assert.ok(existsSync(join(root, BATCHES, name)), name);
  assert.deepEqual(counts(root), {jobs: 3, receipts: 3, budget: 21});
});

test('a failed state read keeps the job', async () => {
  const root = tree();
  const item = job('b-err', 'b', 'x', '2026-11-01T11:00:00+05:30', 8);
  put(root, `${QUEUE}/b-err.json`, item);
  const out = await archive({root, now: NOW, request: async () => { throw new TypeError('fetch failed'); }});
  assert.deepEqual(out.moved_jobs, []);
  assert.equal(out.kept[0].why, 'request_or_parse_failed');
  assert.ok(existsSync(join(root, QUEUE, 'b-err.json')));
});

test('a queued job brings its archived receipt back', async () => {
  const root = tree();
  put(root, `${QUEUE}/c.json`, job('c', 'back', 'x', '2026-11-09T11:00:00+05:30', 9));
  put(root, `${BATCHES}/archive/back.json`, receipt('back', [['x', 9]]));
  const out = await archive({root, now: NOW, request: stateServer({})});
  assert.deepEqual(out.restored_receipts, ['back.json']);
  assert.ok(existsSync(join(root, BATCHES, 'back.json')) && !existsSync(join(root, BATCHES, 'archive/back.json')));
});

test('an unreadable queue job stops receipt archiving', async () => {
  const root = tree();
  writeFileSync(join(root, QUEUE, 'broken.json'), '{');
  put(root, `${BATCHES}/old.json`, receipt('old', [['x', 1]]));
  put(root, `${QUEUE}/archive/x.json`, job('x', 'old', 'x', '2026-11-01T11:00:00+05:30', 1));
  const out = await archive({root, now: NOW, request: stateServer({})});
  assert.equal(out.receipts_skipped, 'unreadable_queue_job');
  assert.ok(existsSync(join(root, BATCHES, 'old.json')));
});

test('check reports a trigger that cannot read the queue and a nearly full folder, after retries', async () => {
  const root = tree();
  for (let i = 0; i < 17; i++) writeFileSync(join(root, BATCHES, `r${i}.json`), '{}');
  let waits = 0;
  const out = await check({root, now: NOW, token: 't', request: async () => new Response('', {status: 500}), wait: async () => { waits++; }});
  assert.equal(out.failure, 'http_500');
  assert.equal(waits, 2);
  assert.match(out.lines[0], /cannot read the Reel queue \(http_500\)/);
  assert.match(out.lines.at(-1), /nearly full/);
  assert.match(out.sizeLine, /17 approval files/);
});

test('check never dispatches', async () => {
  const root = tree();
  const posts = [];
  const request = async (url, init = {}) => { if ((init.method ?? 'GET') !== 'GET') posts.push(url); return new Response('', {status: 500}); };
  await check({root, now: NOW, token: 't', request, wait: async () => {}});
  assert.deepEqual(posts, []);
});
