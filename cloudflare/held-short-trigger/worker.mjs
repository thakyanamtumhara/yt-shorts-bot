// Starts held_short_release.yml (mode meta_due) a few minutes after a held Short's meta_at (Ketu, 11-Oct-2026: cloud
// first). GitHub started that workflow's own cron 4-5 h late on 9 and 10-Oct-2026, after the tool's 4 h window, so
// DTF B's Facebook + Telegram never went out. This Worker never posts and never writes release state: the tool's S3
// state (conditional writes; a platform with an id, a pending attempt or a failure is never posted again) and the
// workflow's concurrency group stay the guards against a second post.
const REPO = 'thakyanamtumhara/yt-shorts-bot';
const WORKFLOW = 'held_short_release.yml';
const JOBS_PATH = 'held_shorts/jobs.json';
const STATE_BASE = 'https://www.bulkplaintshirt.com/p/automation-state-held-shorts/';
export const VERSION = '1.0.0';
const PLATFORMS = ['facebook', 'telegram'];
const MINUTE = 60000;
export const START_AFTER = 2 * MINUTE;
export const LAST_START = 210 * MINUTE;
export const COOLDOWN = 20 * MINUTE;
export const MAX_ATTEMPTS = 3;
const PROBE_GAP = 60 * MINUTE;
const ACTIVE = new Set(['queued', 'in_progress', 'waiting', 'requested', 'pending']);
const JOB_ID = /^[a-z0-9][a-z0-9-]{0,63}$/;
const UA = 'sale91-held-short-trigger/' + VERSION;

export function parseAt(value) {
  if (typeof value !== 'string' || !/(?:Z|[+-]\d\d:\d\d)$/.test(value)) throw new Error('bad_time');
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) throw new Error('bad_time');
  return ms;
}

export function dueJobs(data, now) {
  if (!data || typeof data !== 'object' || !Array.isArray(data.jobs)) throw new Error('bad_jobs_file');
  const seen = new Set(), due = [];
  for (const job of data.jobs) {
    if (!job || typeof job !== 'object' || typeof job.id !== 'string' || !JOB_ID.test(job.id) || seen.has(job.id)) {
      throw new Error('bad_job_id');
    }
    seen.add(job.id);
    const at = parseAt(job.meta_at);
    if (now >= at + START_AFTER && now <= at + LAST_START) due.push({id: job.id, at});
  }
  return due;
}

export function missingPlatforms(state) {
  return PLATFORMS.filter(platform => !Object.hasOwn(state, platform));
}

const title = run => String(run.display_title || '').trim();

function safeCode(error) {
  if (error?.name === 'TimeoutError') return 'timeout';
  return /^[a-z0-9_]+$/.test(error?.message || '') ? error.message : 'exception';
}

export async function tick(env, {now = Date.now(), request = fetch} = {}) {
  if (env.ENABLED !== 'true') return {state: 'disabled'};
  if (!env.HELD_SHORT_GH_TOKEN) return {state: 'error', code: 'missing_token'};
  const gh = (path, {method = 'GET', body, accept = 'application/vnd.github+json'} = {}) => request(
    `https://api.github.com/repos/${REPO}/${path}`, {
      method, redirect: 'manual', signal: AbortSignal.timeout(12000),
      headers: {Authorization: `Bearer ${env.HELD_SHORT_GH_TOKEN}`, Accept: accept, 'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': UA, ...(body ? {'Content-Type': 'application/json'} : {})},
      body: body ? JSON.stringify(body) : undefined});
  const dispatch = async mode => {
    const response = await gh(`actions/workflows/${WORKFLOW}/dispatches`, {method: 'POST', body: {ref: 'main', inputs: {mode}}});
    if (response.status !== 204 && response.status !== 200) throw new Error(`dispatch_http_${response.status}`);
  };
  const listRuns = async () => {
    const response = await gh(`actions/workflows/${WORKFLOW}/runs?per_page=50&exclude_pull_requests=true`);
    if (response.status !== 200) throw new Error(`runs_http_${response.status}`);
    const runs = (await response.json()).workflow_runs;
    if (!Array.isArray(runs)) throw new Error('bad_run_list');
    return runs;
  };

  const file = await gh(`contents/${JOBS_PATH}?ref=main`, {accept: 'application/vnd.github.raw+json'});
  if (file.status !== 200) throw new Error(`jobs_http_${file.status}`);
  const text = await file.text();
  if (text.length > 200000) throw new Error('jobs_too_large');
  const due = dueJobs(JSON.parse(text), now);

  if (!due.length) {
    if (env.PROBE !== 'check') return {state: 'nothing_due'};
    const runs = await listRuns();
    if (runs.some(run => ACTIVE.has(run.status))) return {state: 'probe_wait_active'};
    if (runs.some(run => title(run).startsWith('Held Short · check') && now - Date.parse(run.created_at) < PROBE_GAP)) {
      return {state: 'probe_done'};
    }
    if (env.DRY_RUN === 'true') return {state: 'probe_would_dispatch'};
    await dispatch('check');
    return {state: 'probe_dispatched', mode: 'check'};
  }

  const todo = [], done = [], unknown = [];
  for (const job of due) {
    let missing;
    try {
      const response = await request(`${STATE_BASE}${job.id}.json?trigger_read=${now}`, {
        redirect: 'manual', signal: AbortSignal.timeout(10000), headers: {'Cache-Control': 'no-cache', 'User-Agent': UA}});
      if (response.status === 404) missing = PLATFORMS;
      else if (response.status === 200) {
        const state = await response.json();
        if (!state || typeof state !== 'object' || Array.isArray(state)) throw new Error('bad_state');
        missing = missingPlatforms(state);
      } else throw new Error(`state_http_${response.status}`);
    } catch (error) {
      unknown.push({id: job.id, code: safeCode(error)});
      todo.push(job);
      continue;
    }
    if (missing.length) todo.push(job);
    else done.push(job.id);
  }
  const base = {due: due.map(job => job.id), done, unknown};
  if (!todo.length) return {state: 'done', ...base};

  const runs = await listRuns();
  if (runs.some(run => ACTIVE.has(run.status))) return {state: 'publisher_active', ...base};
  const windowStart = Math.min(...todo.map(job => job.at));
  if (runs.length >= 50 && Date.parse(runs.at(-1).created_at) >= windowStart) throw new Error('run_inventory_incomplete');
  const attempts = runs.filter(run => title(run).startsWith('Held Short · meta_due') && Date.parse(run.created_at) >= windowStart);
  if (attempts.length >= MAX_ATTEMPTS) return {state: 'retry_limit', attempts: attempts.length, ...base};
  if (attempts.some(run => now - Date.parse(run.created_at) < COOLDOWN)) return {state: 'cooldown', ...base};
  if (env.DRY_RUN === 'true') return {state: 'would_dispatch', ...base};
  await dispatch('meta_due');
  return {state: 'dispatch_accepted', mode: 'meta_due', ...base};
}

export default {
  async scheduled(controller, env) {
    let result;
    try {
      result = await tick(env);
    } catch (error) {
      result = {state: 'error', code: safeCode(error)};
    }
    console.log(JSON.stringify({event: 'held_short_trigger', version: VERSION, at: new Date().toISOString(), ...result}));
  },
  async fetch() {
    return new Response('Not found', {status: 404});
  },
};
