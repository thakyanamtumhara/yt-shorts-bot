import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import worker, {COOLDOWN, dueJobs, LAST_START, MAX_ATTEMPTS, missingPlatforms, parseAt, START_AFTER, tick} from './worker.mjs';

const REAL = JSON.parse(readFileSync(new URL('../../held_shorts/jobs.json', import.meta.url), 'utf8'));
const env = {ENABLED: 'true', DRY_RUN: 'false', HELD_SHORT_GH_TOKEN: 'token'};
const AT = '2026-10-11T19:00:00+05:30';
const JOBS = {jobs: [{id: 'dtf-real-a', meta_at: AT}]};
const at = (ist, minutes = 0) => Date.parse(ist) + minutes * 60000;

function fake({jobs = JOBS, state = 404, runs = [], dispatchStatus = 204, jobsStatus = 200} = {}) {
  const calls = [];
  const request = async (url, init = {}) => {
    calls.push({url, init});
    if (url.includes('/contents/held_shorts/jobs.json')) {
      return {status: jobsStatus, text: async () => JSON.stringify(jobs)};
    }
    if (url.startsWith('https://www.bulkplaintshirt.com/p/automation-state-held-shorts/')) {
      if (typeof state === 'number') return {status: state, json: async () => ({})};
      return {status: 200, json: async () => state};
    }
    if (url.includes('/runs?')) return {status: 200, json: async () => ({workflow_runs: runs})};
    if (url.endsWith('/dispatches')) return {status: dispatchStatus};
    throw new Error('unexpected url ' + url);
  };
  return {calls, request, dispatches: () => calls.filter(call => call.url.endsWith('/dispatches'))};
}

const run = (title, createdIst, minutes, extra = {}) => ({
  display_title: title, created_at: new Date(at(createdIst, minutes)).toISOString(), status: 'completed', ...extra});

test('times need an offset; a job is due from meta_at + 2 min to meta_at + 3 h 30 min only', () => {
  assert.throws(() => parseAt('2026-10-11T19:00:00'), /bad_time/);
  assert.throws(() => parseAt(1), /bad_time/);
  assert.equal(parseAt(AT), Date.parse('2026-10-11T13:30:00Z'));
  assert.deepEqual(dueJobs(JOBS, at(AT, -60)), []);
  assert.deepEqual(dueJobs(JOBS, at(AT, 1)), []);
  assert.equal(dueJobs(JOBS, at(AT) + START_AFTER).length, 1);
  assert.equal(dueJobs(JOBS, at(AT) + LAST_START).length, 1);
  assert.deepEqual(dueJobs(JOBS, at(AT) + LAST_START + 1), []);
  assert.throws(() => dueJobs({jobs: [{id: 'A', meta_at: AT}]}, 0), /bad_job_id/);
  assert.throws(() => dueJobs({jobs: [{id: 'a', meta_at: AT}, {id: 'a', meta_at: AT}]}, 0), /bad_job_id/);
  assert.throws(() => dueJobs({}, 0), /bad_jobs_file/);
});

test('the real jobs file: A today, B Monday, C Tuesday, nothing at other times', () => {
  const ids = ist => dueJobs(REAL, Date.parse(ist)).map(job => job.id);
  assert.deepEqual(ids('2026-10-11T12:40:00+05:30'), []);
  assert.deepEqual(ids('2026-10-10T19:04:00+05:30'), []);
  assert.deepEqual(ids('2026-10-11T19:04:00+05:30'), ['dtf-real-a']);
  assert.deepEqual(ids('2026-10-11T22:30:00+05:30'), ['dtf-real-a']);
  assert.deepEqual(ids('2026-10-11T22:31:00+05:30'), []);
  assert.deepEqual(ids('2026-10-12T19:04:00+05:30'), ['dtf-real-b']);
  assert.deepEqual(ids('2026-10-12T15:00:00+05:30'), []);
  assert.deepEqual(ids('2026-10-13T19:04:00+05:30'), ['dtf-real-c']);
});

test('state: only a platform with no record at all is missing', () => {
  assert.deepEqual(missingPlatforms({}), ['facebook', 'telegram']);
  assert.deepEqual(missingPlatforms({youtube: {video_id: 'x'}, facebook: {pending: true}}), ['telegram']);
  assert.deepEqual(missingPlatforms({facebook: {failed: 'x'}, telegram: {id: '1'}}), []);
});

test('disabled or without a token: no network at all', async () => {
  const f = fake();
  assert.deepEqual(await tick({...env, ENABLED: 'false'}, {now: at(AT, 4), request: f.request}), {state: 'disabled'});
  assert.deepEqual(await tick({...env, HELD_SHORT_GH_TOKEN: ''}, {now: at(AT, 4), request: f.request}), {state: 'error', code: 'missing_token'});
  assert.equal(f.calls.length, 0);
});

test('nothing due: one read of the jobs file, no dispatch', async () => {
  const f = fake();
  assert.deepEqual(await tick(env, {now: at(AT, -300), request: f.request}), {state: 'nothing_due'});
  assert.equal(f.calls.length, 1);
  assert.match(f.calls[0].url, /^https:\/\/api\.github\.com\/repos\/thakyanamtumhara\/yt-shorts-bot\/contents\/held_shorts\/jobs\.json\?ref=main$/);
  assert.equal(f.calls[0].init.headers.Accept, 'application/vnd.github.raw+json');
  assert.equal(f.calls[0].init.redirect, 'manual');
});

test('due with no state: dispatches meta_due once, on main, never following redirects', async () => {
  const f = fake({runs: [run('Held Short · meta_due', '2026-10-10T23:24:00+05:30', 0)]});
  const result = await tick(env, {now: at(AT, 4), request: f.request});
  assert.equal(result.state, 'dispatch_accepted');
  assert.deepEqual(result.due, ['dtf-real-a']);
  assert.equal(f.dispatches().length, 1);
  const call = f.dispatches()[0];
  assert.equal(call.url, 'https://api.github.com/repos/thakyanamtumhara/yt-shorts-bot/actions/workflows/held_short_release.yml/dispatches');
  assert.equal(call.init.method, 'POST');
  assert.equal(call.init.redirect, 'manual');
  assert.deepEqual(JSON.parse(call.init.body), {ref: 'main', inputs: {mode: 'meta_due'}});
  assert.match(f.calls[1].url, /automation-state-held-shorts\/dtf-real-a\.json\?trigger_read=\d+$/);
});

test('both platforms recorded (posted, pending or failed): done, no run list, no dispatch', async () => {
  for (const state of [{facebook: {id: '1'}, telegram: {id: '2'}}, {facebook: {pending: true}, telegram: {failed: 'x'}}]) {
    const f = fake({state});
    assert.equal((await tick(env, {now: at(AT, 4), request: f.request})).state, 'done');
    assert.equal(f.calls.some(call => call.url.includes('/runs?')), false);
    assert.equal(f.dispatches().length, 0);
  }
});

test('one platform left (e.g. Telegram after Facebook) still dispatches; the tool skips the posted one', async () => {
  const f = fake({state: {youtube: {}, facebook: {id: '1'}}});
  assert.equal((await tick(env, {now: at(AT, 4), request: f.request})).state, 'dispatch_accepted');
});

test('unreadable state is not proof of anything: the tool (S3 + credentials) decides', async () => {
  const f = fake({state: 503});
  const result = await tick(env, {now: at(AT, 4), request: f.request});
  assert.equal(result.state, 'dispatch_accepted');
  assert.deepEqual(result.unknown, [{id: 'dtf-real-a', code: 'state_http_503'}]);
});

test('a running or queued release run blocks a dispatch', async () => {
  for (const status of ['queued', 'in_progress', 'waiting', 'requested', 'pending']) {
    const f = fake({runs: [run('Held Short · check', AT, -30, {status})]});
    assert.equal((await tick(env, {now: at(AT, 4), request: f.request})).state, 'publisher_active');
    assert.equal(f.dispatches().length, 0);
  }
});

test('cooldown after an attempt, at most three attempts per due window', async () => {
  const recent = fake({runs: [run('Held Short · meta_due', AT, 4)]});
  assert.equal((await tick(env, {now: at(AT, 4) + COOLDOWN - 1, request: recent.request})).state, 'cooldown');
  const old = fake({runs: [run('Held Short · meta_due', AT, 4)]});
  assert.equal((await tick(env, {now: at(AT, 4) + COOLDOWN + 1, request: old.request})).state, 'dispatch_accepted');
  const many = fake({runs: Array.from({length: MAX_ATTEMPTS}, (_, i) => run('Held Short · meta_due ', AT, 4 + 25 * i))});
  const limited = await tick(env, {now: at(AT, 100), request: many.request});
  assert.equal(limited.state, 'retry_limit');
  assert.equal(many.dispatches().length, 0);
  const otherModes = fake({runs: [run('Held Short · youtube dtf-real-a', AT, 3), run('Held Short · check', AT, 3)]});
  assert.equal((await tick(env, {now: at(AT, 4), request: otherModes.request})).state, 'dispatch_accepted');
});

test('a full run page that does not reach back to the due time stops the tick', async () => {
  const runs = Array.from({length: 50}, () => run('Held Short · youtube x', AT, 3));
  const f = fake({runs});
  await assert.rejects(tick(env, {now: at(AT, 4), request: f.request}), /run_inventory_incomplete/);
  assert.equal(f.dispatches().length, 0);
});

test('dry run never posts a dispatch; GitHub errors surface as codes', async () => {
  const f = fake();
  assert.equal((await tick({...env, DRY_RUN: 'true'}, {now: at(AT, 4), request: f.request})).state, 'would_dispatch');
  assert.equal(f.dispatches().length, 0);
  await assert.rejects(tick(env, {now: at(AT, 4), request: fake({dispatchStatus: 403}).request}), /dispatch_http_403/);
  await assert.rejects(tick(env, {now: at(AT, 4), request: fake({jobsStatus: 401}).request}), /jobs_http_401/);
});

test('probe (PROBE=check): dispatches the posting-free check mode once an hour, only when nothing is due', async () => {
  const probeEnv = {...env, PROBE: 'check'};
  const f = fake();
  assert.deepEqual(await tick(probeEnv, {now: at(AT, -300), request: f.request}), {state: 'probe_dispatched', mode: 'check'});
  assert.deepEqual(JSON.parse(f.dispatches()[0].init.body), {ref: 'main', inputs: {mode: 'check'}});
  const again = fake({runs: [run('Held Short · check', AT, -310)]});
  assert.equal((await tick(probeEnv, {now: at(AT, -300), request: again.request})).state, 'probe_done');
  const dry = fake();
  assert.equal((await tick({...probeEnv, DRY_RUN: 'true'}, {now: at(AT, -300), request: dry.request})).state, 'probe_would_dispatch');
  assert.equal(dry.dispatches().length, 0);
  const dueNow = fake();
  assert.equal((await tick(probeEnv, {now: at(AT, 4), request: dueNow.request})).state, 'dispatch_accepted');
  assert.deepEqual(JSON.parse(dueNow.dispatches()[0].init.body), {ref: 'main', inputs: {mode: 'meta_due'}});
});

test('scheduled handler logs one identifier-only line and never throws; fetch is 404', async () => {
  const lines = [];
  const original = console.log;
  console.log = line => lines.push(line);
  try {
    await worker.scheduled({}, {ENABLED: 'false'});
  } finally {
    console.log = original;
  }
  const entry = JSON.parse(lines[0]);
  assert.equal(entry.event, 'held_short_trigger');
  assert.equal(entry.state, 'disabled');
  assert.equal((await worker.fetch()).status, 404);
});
