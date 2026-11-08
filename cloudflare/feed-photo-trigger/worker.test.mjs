import assert from 'node:assert/strict';
import {readdirSync, readFileSync} from 'node:fs';
import test from 'node:test';
import worker, {COOLDOWN, isDue, jobFiles, LAST_START, MAX_ATTEMPTS, MAX_JOBS, parseAt, readJob, RUN_PAGE, START_AFTER,
  stateVerdict, tick} from './worker.mjs';

const QUEUE = new URL('../../feed_queue/', import.meta.url);
const env = {ENABLED: 'true', DRY_RUN: 'false', FEED_PHOTO_GH_TOKEN: 'token'};
const AT = '2026-11-10T13:00:00+05:30';
const JOB = {id: 'kids-photo', status: 'reviewed', publish_at: AT, caption: 'x'};
const at = (ist, minutes = 0) => Date.parse(ist) + minutes * 60000;
const listing = names => [...names.map(name => ({name, type: 'file'})), {name: 'state', type: 'dir'}];

function fake({jobs = {'kids-photo.json': JOB}, state = 404, runs = [], dispatchStatus = 204, queueStatus = 200} = {}) {
  const calls = [];
  const request = async (url, init = {}) => {
    calls.push({url, init});
    if (url.endsWith('/contents/feed_queue?ref=main')) return {status: queueStatus, json: async () => listing(Object.keys(jobs))};
    const file = url.match(/\/contents\/feed_queue\/([^?]+)\?ref=main$/);
    if (file) return {status: 200, text: async () => (typeof jobs[file[1]] === 'string' ? jobs[file[1]] : JSON.stringify(jobs[file[1]]))};
    if (url.startsWith('https://www.bulkplaintshirt.com/p/automation-state-ig-curated/')) {
      if (typeof state === 'number') return {status: state, json: async () => ({})};
      return {status: 200, json: async () => state};
    }
    if (url.includes('/runs?')) return {status: 200, json: async () => ({workflow_runs: runs})};
    if (url.endsWith('/dispatches')) return {status: dispatchStatus};
    throw new Error('unexpected url ' + url);
  };
  return {calls, request, dispatches: () => calls.filter(call => call.url.endsWith('/dispatches'))};
}

const run = (createdIst, minutes, extra = {}) => ({
  created_at: new Date(at(createdIst, minutes)).toISOString(), status: 'completed', conclusion: 'failure',
  event: 'workflow_dispatch', ...extra});

test('times must be IST (+05:30); due from publish_at + 30 s to publish_at + 3 h 30 min only', () => {
  assert.throws(() => parseAt('2026-11-10T13:00:00'), /bad_time/);
  assert.throws(() => parseAt('2026-11-10T07:30:00Z'), /bad_time/);
  assert.throws(() => parseAt(1), /bad_time/);
  assert.equal(parseAt(AT), Date.parse('2026-11-10T07:30:00Z'));
  const job = {id: 'a', at: parseAt(AT)};
  assert.equal(isDue(job, at(AT, -1)), false);
  assert.equal(isDue(job, at(AT) + START_AFTER - 1), false);
  assert.equal(isDue(job, at(AT) + START_AFTER), true);
  assert.equal(isDue(job, at(AT) + LAST_START), true);
  assert.equal(isDue(job, at(AT) + LAST_START + 1), false);
});

test('queue listing: only top-level .json files, never the state folder; a size cap', () => {
  assert.deepEqual(jobFiles(listing(['b.json', 'a.json', 'note.txt'])), ['a.json', 'b.json']);
  assert.throws(() => jobFiles({}), /bad_listing/);
  assert.throws(() => jobFiles(listing(Array.from({length: MAX_JOBS + 1}, (_, i) => `j${i}.json`))), /queue_too_large/);
  assert.throws(() => jobFiles(listing(['../x.json'])), /bad_file_name/);
});

test('a job file the publisher would refuse stops the tick (the publisher refuses the whole queue too)', () => {
  assert.deepEqual(readJob(JSON.stringify(JOB)), {id: 'kids-photo', at: parseAt(AT)});
  assert.throws(() => readJob('{'), /bad_job_json/);
  assert.throws(() => readJob('[]'), /bad_job_json/);
  assert.throws(() => readJob(JSON.stringify({...JOB, id: 'Kids'})), /bad_job_id/);
  assert.throws(() => readJob(JSON.stringify({...JOB, status: 'draft'})), /bad_job_status/);
  assert.throws(() => readJob(JSON.stringify({...JOB, publish_at: '2026-11-10T07:30:00Z'})), /bad_time/);
});

test('the real queue: the Kids Hoodie photo is due Tue 10-Nov 13:00:30-16:30 IST, nothing else now', () => {
  const jobs = readdirSync(QUEUE).filter(name => name.endsWith('.json')).map(name => readJob(readFileSync(new URL(name, QUEUE), 'utf8')));
  const ids = ist => jobs.filter(job => isDue(job, Date.parse(ist))).map(job => job.id);
  assert.deepEqual(ids('2026-11-10T12:59:00+05:30'), []);
  assert.deepEqual(ids('2026-11-10T13:01:00+05:30'), ['kidshood20261107-igpost']);
  assert.deepEqual(ids('2026-11-10T16:30:00+05:30'), ['kidshood20261107-igpost']);
  assert.deepEqual(ids('2026-11-10T16:31:00+05:30'), []);
  assert.deepEqual(ids('2026-11-08T12:40:00+05:30'), []);
});

test('state: 404 = not started, media_id = posted, pending = uncertain, a partial record = open', () => {
  assert.equal(stateVerdict(404, null), 'open');
  assert.equal(stateVerdict(200, {media_id: '1', pending: null}), 'posted');
  assert.equal(stateVerdict(200, {pending: 'media'}), 'uncertain');
  assert.equal(stateVerdict(200, {child_ids: [], pending: null, phase: 'media_saved'}), 'open');
  assert.throws(() => stateVerdict(503, null), /state_http_503/);
  assert.throws(() => stateVerdict(200, []), /bad_state/);
});

test('disabled or without a token: no network at all', async () => {
  const f = fake();
  assert.deepEqual(await tick({...env, ENABLED: 'false'}, {now: at(AT, 1), request: f.request}), {state: 'disabled'});
  assert.deepEqual(await tick({...env, FEED_PHOTO_GH_TOKEN: ''}, {now: at(AT, 1), request: f.request}), {state: 'error', code: 'missing_token'});
  assert.equal(f.calls.length, 0);
});

test('nothing due: reads the queue only, no state, run list or dispatch', async () => {
  const f = fake();
  assert.deepEqual(await tick(env, {now: at(AT, -30), request: f.request}), {state: 'nothing_due', queued: 1});
  assert.equal(f.calls.length, 2);
  assert.equal(f.calls[0].url, 'https://api.github.com/repos/thakyanamtumhara/yt-shorts-bot/contents/feed_queue?ref=main');
  assert.equal(f.calls[1].init.headers.Accept, 'application/vnd.github.raw+json');
  assert.ok(f.calls.every(call => call.init.redirect === 'manual'));
});

test('due and never started: dispatches ig_carousel.yml once on main with no inputs (verify_only stays false)', async () => {
  const f = fake({runs: [run('2026-11-09T19:45:00+05:30', 0, {event: 'schedule', conclusion: 'success'})]});
  const result = await tick(env, {now: at(AT, 1), request: f.request});
  assert.equal(result.state, 'dispatch_accepted');
  assert.deepEqual(result.due, ['kids-photo']);
  assert.equal(f.dispatches().length, 1);
  const call = f.dispatches()[0];
  assert.equal(call.url, 'https://api.github.com/repos/thakyanamtumhara/yt-shorts-bot/actions/workflows/ig_carousel.yml/dispatches');
  assert.equal(call.init.method, 'POST');
  assert.equal(call.init.redirect, 'manual');
  assert.deepEqual(JSON.parse(call.init.body), {ref: 'main'});
  assert.match(f.calls.find(c => c.url.includes('automation-state-ig-curated')).url, /kids-photo\.json\?trigger_read=\d+$/);
  assert.match(f.calls.find(c => c.url.includes('/runs?')).url, new RegExp(`per_page=${RUN_PAGE}&`));
});

test('a partial record (containers made, nothing published) still dispatches: the publisher resumes it', async () => {
  const f = fake({state: {child_ids: ['1'], pending: null, phase: 'child_saved'}});
  assert.equal((await tick(env, {now: at(AT, 1), request: f.request})).state, 'dispatch_accepted');
});

test('posted: done, no run list, no dispatch', async () => {
  const f = fake({state: {media_id: '179', pending: null}});
  const result = await tick(env, {now: at(AT, 6), request: f.request});
  assert.equal(result.state, 'done');
  assert.deepEqual(result.done, ['kids-photo']);
  assert.equal(f.calls.some(call => call.url.includes('/runs?')), false);
  assert.equal(f.dispatches().length, 0);
});

test('an uncertain POST or an unreadable state never dispatches', async () => {
  const pending = fake({state: {pending: 'media'}});
  assert.equal((await tick(env, {now: at(AT, 1), request: pending.request})).state, 'uncertain');
  const down = fake({state: 503});
  const result = await tick(env, {now: at(AT, 1), request: down.request});
  assert.equal(result.state, 'state_unknown');
  assert.deepEqual(result.unknown, [{id: 'kids-photo', code: 'state_http_503'}]);
  assert.equal(pending.dispatches().length + down.dispatches().length, 0);
});

test('a queued or running publish run blocks a dispatch', async () => {
  for (const status of ['queued', 'in_progress', 'waiting', 'requested', 'pending']) {
    const f = fake({runs: [run(AT, -300, {status, event: 'schedule', conclusion: null})]});
    assert.equal((await tick(env, {now: at(AT, 1), request: f.request})).state, 'publisher_active');
    assert.equal(f.dispatches().length, 0);
  }
});

test('a run that finished cleanly after the due time already handled it (posted, or the one-per-day guard held it)', async () => {
  for (const event of ['workflow_dispatch', 'schedule']) {
    const f = fake({runs: [run(AT, 1, {event, conclusion: 'success'})]});
    assert.equal((await tick(env, {now: at(AT, 30), request: f.request})).state, 'handled_by_run');
    assert.equal(f.dispatches().length, 0);
  }
});

test('cooldown after a failed attempt, at most three attempts; failed schedule runs are not attempts', async () => {
  const recent = fake({runs: [run(AT, 1)]});
  assert.equal((await tick(env, {now: at(AT, 1) + COOLDOWN - 1, request: recent.request})).state, 'cooldown');
  const old = fake({runs: [run(AT, 1)]});
  assert.equal((await tick(env, {now: at(AT, 1) + COOLDOWN + 1, request: old.request})).state, 'dispatch_accepted');
  const many = fake({runs: Array.from({length: MAX_ATTEMPTS}, (_, i) => run(AT, 1 + 25 * i))});
  const limited = await tick(env, {now: at(AT, 100), request: many.request});
  assert.equal(limited.state, 'retry_limit');
  assert.equal(limited.attempts, MAX_ATTEMPTS);
  assert.equal(many.dispatches().length, 0);
  const scheduled = fake({runs: [run(AT, 2, {event: 'schedule'}), run(AT, -60, {event: 'workflow_dispatch'})]});
  assert.equal((await tick(env, {now: at(AT, 3), request: scheduled.request})).state, 'dispatch_accepted');
});

test('a full run page that does not reach back to the due time stops the tick', async () => {
  const f = fake({runs: Array.from({length: RUN_PAGE}, () => run(AT, 1, {event: 'schedule'}))});
  await assert.rejects(tick(env, {now: at(AT, 2), request: f.request}), /run_inventory_incomplete/);
  assert.equal(f.dispatches().length, 0);
});

test('dry run never dispatches; duplicate ids and GitHub errors surface as codes', async () => {
  const f = fake();
  assert.equal((await tick({...env, DRY_RUN: 'true'}, {now: at(AT, 1), request: f.request})).state, 'would_dispatch');
  assert.equal(f.dispatches().length, 0);
  await assert.rejects(tick(env, {now: at(AT, 1), request: fake({dispatchStatus: 403}).request}), /dispatch_http_403/);
  await assert.rejects(tick(env, {now: at(AT, 1), request: fake({queueStatus: 401}).request}), /queue_http_401/);
  await assert.rejects(tick(env, {now: at(AT, 1), request: fake({jobs: {'a.json': JOB, 'b.json': JOB}}).request}), /duplicate_job_id/);
});

test('scheduled handler logs one line and never throws; fetch is 404', async () => {
  const lines = [];
  const original = console.log;
  console.log = line => lines.push(line);
  try {
    await worker.scheduled({}, {ENABLED: 'false'});
  } finally {
    console.log = original;
  }
  assert.equal(lines.length, 1);
  assert.equal(JSON.parse(lines[0]).event, 'feed_photo_trigger');
  assert.equal(JSON.parse(lines[0]).state, 'disabled');
  assert.equal((await worker.fetch()).status, 404);
});
