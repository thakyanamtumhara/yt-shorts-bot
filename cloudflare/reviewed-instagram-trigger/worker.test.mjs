import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, readdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import worker, {tick, digest, classifyState} from './worker.mjs';

const ROOT = fileURLToPath(new URL('../../', import.meta.url));
const job = JSON.parse(readFileSync(ROOT + 'reviewed_ai_reels/raw0004-20260929-full-v1.json'));
const receipt = JSON.parse(readFileSync(ROOT + 'reviewed_ai_releases/batches/raw0004-20260929.json'));
const now = Date.parse('2026-09-29T14:00:00+05:30');
const env = {ENABLED: 'true', DRY_RUN: 'false', REVIEWED_IG_GH_TOKEN: 'test-secret-only'};
const sha = 'a'.repeat(40);
const clone = value => structuredClone(value);
const json = (value, status = 200) => new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}});

async function stateFor(value = job, extra = {}) {
  return {format: 'reviewed-ai-reel-v1', mode: 'publish', job_id: value.id, job_sha256: await digest(value), account_id: value.account_id, video_sha256: value.video_sha256, selection_sha256: await digest(value.user_selection), phase: 'validated', pending: null, native_ai_requested: value.is_ai_generated, native_ai_verified: false, ...extra};
}

function mock(options = {}) {
  const jobs = options.jobs ?? [clone(job)], approval = options.receipt ?? clone(receipt), calls = [];
  const fetch = async (target, init) => {
    const url = new URL(target); calls.push({url: target, method: init.method, body: init.body, headers: init.headers});
    assert.equal(init.redirect, 'manual');
    if (url.hostname === 'www.bulkplaintshirt.com') {
      assert.equal(init.headers.Authorization, undefined);
      assert.equal(url.searchParams.get('trigger_read'), String(now));
      const key = url.pathname.split('/').at(-1).replace('.json', '');
      const state = options.states?.[key];
      if (options.stateError) return json({}, options.stateError);
      return state === undefined ? json({}, 404) : json(state);
    }
    assert.equal(url.hostname, 'api.github.com');
    assert.equal(init.headers.Authorization, 'Bearer test-secret-only');
    assert.ok(url.pathname.startsWith('/repos/thakyanamtumhara/yt-shorts-bot/'));
    const path = url.pathname.split('/yt-shorts-bot/')[1];
    if (options.githubError) return json({}, options.githubError);
    if (path === 'commits/main') return json({sha});
    if (path === 'contents/reviewed_ai_reels') return json(jobs.map(value => ({type: 'file', name: value.id + '.json', path: 'reviewed_ai_reels/' + value.id + '.json'})));
    if (path === 'contents/reviewed_ai_releases/batches') return json([]);
    if (path.startsWith('contents/')) {
      assert.equal(url.searchParams.get('ref'), sha);
      const value = path.endsWith('approvals.json') ? approval : jobs.find(item => path.endsWith(item.id + '.json'));
      assert.ok(value);
      const text = JSON.stringify(value);
      return json({type: 'file', encoding: 'base64', size: Buffer.byteLength(text), content: Buffer.from(text).toString('base64')});
    }
    if (path.endsWith('/runs')) {
      const runs = options.runs ?? [];
      return json({workflow_runs: url.searchParams.has('status') ? runs.filter(run => run.status === url.searchParams.get('status')) : runs});
    }
    if (path.endsWith('/dispatches')) {
      assert.equal(init.method, 'POST');
      assert.deepEqual(JSON.parse(init.body), {ref: 'main', inputs: {mode: 'publish_due'}});
      if (options.dispatchError) return json({}, options.dispatchError);
      return new Response(null, {status: 204});
    }
    throw new Error('unexpected_test_request');
  };
  return {fetch, calls, posts: () => calls.filter(call => call.method === 'POST')};
}

const run = (minutes, changes = {}) => ({id: minutes, event: 'workflow_dispatch', status: 'completed', conclusion: 'failure', display_title: 'Reviewed Instagram · publish_due', created_at: new Date(now - minutes * 60000).toISOString(), ...changes});

test('disabled undo does no network even without a credential', async () => {
  const result = await tick({ENABLED: 'false'}, {now, fetch() { throw new Error('network_should_not_run'); }});
  assert.equal(result.status, 'disabled');
});

test('missing credential fails before any network', async () => {
  await assert.rejects(tick({ENABLED: 'true'}, {now}), /github_secret_missing/);
});

test('due approved job dispatches exactly the existing publisher', async () => {
  const m = mock(); const result = await tick(env, {now, fetch: m.fetch});
  assert.equal(result.status, 'dispatch_accepted'); assert.equal(result.run_id, null); assert.equal(m.posts().length, 1);
});

test('read-only smoke cannot dispatch', async () => {
  const m = mock(); const result = await tick({...env, DRY_RUN: 'true'}, {now, fetch: m.fetch});
  assert.equal(result.status, 'would_dispatch'); assert.equal(m.posts().length, 0);
});

test('future queue does not read release states or dispatch', async () => {
  const future = {...clone(job), publish_at: '2026-10-02T16:30:00+05:30'};
  const m = mock({jobs: [future]}); const result = await tick(env, {now, fetch: m.fetch});
  assert.equal(result.status, 'nothing_due'); assert.equal(m.posts().length, 0);
  assert.equal(m.calls.some(call => call.url.includes('automation-state')), false);
});

test('verified source, native disclosure and comment skip all workflow calls', async () => {
  const state = await stateFor(job, {phase: 'published_verified', media_id: '1784', native_ai_verified: true, comment_id: '1785', comment_verified: true});
  const m = mock({states: {[job.video_sha256]: state}}); const result = await tick(env, {now, fetch: m.fetch});
  assert.equal(result.status, 'nothing_due'); assert.equal(result.verified_count, 1); assert.equal(m.calls.some(call => call.url.includes('/actions/')), false);
});

test('verified media with unfinished comment requests guarded recovery, never a Meta call', async () => {
  const state = await stateFor(job, {phase: 'published_verified', media_id: '1784', native_ai_verified: true});
  const m = mock({states: {[job.video_sha256]: state}}); const result = await tick(env, {now, fetch: m.fetch});
  assert.equal(result.status, 'dispatch_accepted'); assert.equal(m.posts().length, 1);
});

test('ambiguous media or comment POST never triggers a retry for that job', async () => {
  for (const pending of ['media', 'comment']) {
    const state = await stateFor(job, {pending});
    const m = mock({states: {[job.video_sha256]: state}}); const result = await tick(env, {now, fetch: m.fetch});
    assert.equal(result.status, 'blocked'); assert.equal(result.blocked[0].code, 'ambiguous_saved_post'); assert.equal(m.posts().length, 0);
  }
});

test('changed caption, native flag, account and unknown state fields fail closed', async () => {
  for (const patch of [{job_sha256: '0'.repeat(64)}, {native_ai_requested: true}, {account_id: 'wrong'}, {injected: true}]) {
    const m = mock({states: {[job.video_sha256]: await stateFor(job, patch)}}); const result = await tick(env, {now, fetch: m.fetch});
    assert.equal(result.status, 'blocked'); assert.equal(m.posts().length, 0);
  }
});

test('state outage is blocked, not mistaken for an absent record', async () => {
  for (const stateError of [403, 429, 500]) {
    const m = mock({stateError}); const result = await tick(env, {now, fetch: m.fetch});
    assert.equal(result.status, 'blocked'); assert.equal(m.posts().length, 0);
  }
});

test('missing or changed approval never dispatches', async () => {
  for (const patch of [{approved: false}, {version: 'unapproved'}, {batch_id: 'unknown'}]) {
    const changed = clone(job); Object.assign(changed.user_selection, patch);
    const m = mock({jobs: [changed]}); await assert.rejects(tick(env, {now, fetch: m.fetch})); assert.equal(m.posts().length, 0);
  }
});

test('duplicate exact content is rejected', async () => {
  const changed = {...clone(job), id: 'another-id'};
  const m = mock({jobs: [clone(job), changed]}); await assert.rejects(tick(env, {now, fetch: m.fetch}), /invalid_job_identity/); assert.equal(m.posts().length, 0);
});

test('active publisher or preflight waits without another workflow', async () => {
  for (const status of ['queued', 'in_progress', 'waiting', 'requested', 'pending']) {
    const m = mock({runs: [run(300, {status, display_title: 'old title'})]});
    assert.equal((await tick(env, {now, fetch: m.fetch})).status, 'publisher_active'); assert.equal(m.posts().length, 0);
  }
});

test('successful preflight and old successful title do not establish publication', async () => {
  for (const display_title of ['Reviewed Instagram · preflight', 'Reviewed Instagram Reels']) {
    const m = mock({runs: [run(1, {display_title, conclusion: 'success'})]});
    assert.equal((await tick(env, {now, fetch: m.fetch})).status, 'dispatch_accepted'); assert.equal(m.posts().length, 1);
  }
});

test('recent publisher waits for receipt propagation before retry', async () => {
  const m = mock({runs: [run(2, {conclusion: 'success'})]});
  assert.equal((await tick(env, {now, fetch: m.fetch})).status, 'cooldown'); assert.equal(m.posts().length, 0);
});

test('three unsuccessful delivery attempts stop automatic run creation', async () => {
  const m = mock({runs: [run(18), run(25), run(35)]});
  assert.equal((await tick(env, {now, fetch: m.fetch})).status, 'retry_limit'); assert.equal(m.posts().length, 0);
});

test('GitHub read or dispatch failure is never reported accepted', async () => {
  for (const options of [{githubError: 403}, {dispatchError: 500}]) {
    const m = mock(options); await assert.rejects(tick(env, {now, fetch: m.fetch}), /http_/);
  }
});

test('Workers-compatible manual redirect mode refuses all redirect responses', async () => {
  for (const status of [301, 302, 307, 308]) {
    const m = mock({githubError: status});
    await assert.rejects(tick(env, {now, fetch: m.fetch}), new RegExp('http_' + status));
    assert.equal(m.calls.length, 1); assert.equal(m.posts().length, 0);
  }
});

test('new due job can proceed when a different job has an ambiguous saved state', async () => {
  const other = {...clone(job), id: 'other-approved-job', video_sha256: '1'.repeat(64), user_selection: {...clone(job.user_selection), id: 'other-video', sha256: '1'.repeat(64)}};
  const approval = clone(receipt); approval.videos.push({...clone(receipt.videos[0]), id: 'other-video', sha256: '1'.repeat(64)});
  const m = mock({jobs: [clone(job), other], receipt: approval, states: {[job.video_sha256]: await stateFor(job, {pending: 'comment'})}});
  const result = await tick(env, {now, fetch: m.fetch});
  assert.equal(result.status, 'dispatch_accepted'); assert.equal(result.blocked.length, 1); assert.deepEqual(result.ready, ['other-approved-job']);
});

test('published flag alone is not sufficient completion evidence', async () => {
  assert.equal(await classifyState(job, await stateFor(job, {phase: 'published_verified'})), 'due');
});

test('hash canonicalization matches publisher Python for every real queued job', async () => {
  for (const name of readdirSync(ROOT + 'reviewed_ai_reels').filter(name => name.endsWith('.json'))) {
    const text = readFileSync(ROOT + 'reviewed_ai_reels/' + name, 'utf8');
    const pythonHash = execFileSync('python3', ['-c', 'import json,hashlib,sys;print(hashlib.sha256(json.dumps(json.load(sys.stdin),ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest())'], {input: text, encoding: 'utf8'}).trim();
    assert.equal(await digest(JSON.parse(text)), pythonHash, name);
  }
});

test('both deployment configs expose no HTTP route and undo disables cron', () => {
  const disabled = JSON.parse(readFileSync(new URL('./wrangler.jsonc', import.meta.url)));
  const enabled = JSON.parse(readFileSync(new URL('./wrangler.enabled.jsonc', import.meta.url)));
  for (const config of [disabled, enabled]) {
    assert.equal(config.workers_dev, false); assert.equal(config.preview_urls, false); assert.deepEqual(config.routes, []);
  }
  assert.equal(disabled.vars.ENABLED, 'false'); assert.deepEqual(disabled.triggers.crons, []);
  assert.equal(enabled.vars.ENABLED, 'true'); assert.equal(enabled.triggers.crons.length, 1);
  delete disabled.vars; delete disabled.triggers; delete enabled.vars; delete enabled.triggers;
  assert.deepEqual(disabled, enabled);
});

test('HTTP handler cannot trigger publication', async () => {
  assert.equal((await worker.fetch(new Request('https://irrelevant/trigger'), env)).status, 404);
});
