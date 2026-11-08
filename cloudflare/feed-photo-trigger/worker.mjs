// Starts ig_carousel.yml just after a reviewed Instagram photo in feed_queue/ is due (Ketu, 8-Nov-2026: booked times
// are kept; cloud first). GitHub started that workflow's own cron 6-8 h late (the 13:00 IST slot ran 19:45-21:11 IST
// on 2-7 Nov-2026; the Sep photos went out 4 h 51 m and 5 h 08 m late) and no other Worker dispatches it. This Worker
// never posts and never writes state: tools/curated_feed.py (S3 state with conditional writes, at most one curated
// post per IST day, a post with a media id or an uncertain POST is never repeated) and the workflow's concurrency
// group stay the guards against a second post.
const REPO = 'thakyanamtumhara/yt-shorts-bot';
const WORKFLOW = 'ig_carousel.yml';
const QUEUE = 'feed_queue';
const STATE_BASE = 'https://www.bulkplaintshirt.com/p/automation-state-ig-curated/';
export const VERSION = '1.0.0';
const MINUTE = 60000;
export const START_AFTER = 30000;
export const LAST_START = 210 * MINUTE;
export const COOLDOWN = 20 * MINUTE;
export const MAX_ATTEMPTS = 3;
export const MAX_JOBS = 20;
export const RUN_PAGE = 20;
const ACTIVE = new Set(['queued', 'in_progress', 'waiting', 'requested', 'pending']);
const JOB_ID = /^[a-z0-9][a-z0-9-]{0,79}$/;
const FILE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.json$/;
const UA = 'sale91-feed-photo-trigger/' + VERSION;

export function parseAt(value) {
  if (typeof value !== 'string' || !/\+05:30$/.test(value)) throw new Error('bad_time');
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) throw new Error('bad_time');
  return ms;
}

export function jobFiles(listing) {
  if (!Array.isArray(listing)) throw new Error('bad_listing');
  const names = listing.filter(entry => entry && entry.type === 'file' && typeof entry.name === 'string'
    && entry.name.endsWith('.json')).map(entry => entry.name).sort();
  if (names.length > MAX_JOBS) throw new Error('queue_too_large');
  if (names.some(name => !FILE.test(name))) throw new Error('bad_file_name');
  return names;
}

export function readJob(text) {
  let job;
  try {
    job = JSON.parse(text);
  } catch {
    throw new Error('bad_job_json');
  }
  if (!job || typeof job !== 'object' || Array.isArray(job)) throw new Error('bad_job_json');
  if (typeof job.id !== 'string' || !JOB_ID.test(job.id)) throw new Error('bad_job_id');
  if (job.status !== 'reviewed') throw new Error('bad_job_status');
  return {id: job.id, at: parseAt(job.publish_at)};
}

export const isDue = (job, now) => now >= job.at + START_AFTER && now <= job.at + LAST_START;

export function stateVerdict(status, state) {
  if (status === 404) return 'open';
  if (status !== 200) throw new Error(`state_http_${status}`);
  if (!state || typeof state !== 'object' || Array.isArray(state)) throw new Error('bad_state');
  if (state.media_id) return 'posted';
  if (state.pending) return 'uncertain';
  return 'open';
}

function safeCode(error) {
  if (error?.name === 'TimeoutError') return 'timeout';
  return /^[a-z0-9_]+$/.test(error?.message || '') ? error.message : 'exception';
}

export async function tick(env, {now = Date.now(), request = fetch} = {}) {
  if (env.ENABLED !== 'true') return {state: 'disabled'};
  if (!env.FEED_PHOTO_GH_TOKEN) return {state: 'error', code: 'missing_token'};
  const gh = (path, {method = 'GET', body, accept = 'application/vnd.github+json'} = {}) => request(
    `https://api.github.com/repos/${REPO}/${path}`, {
      method, redirect: 'manual', signal: AbortSignal.timeout(12000),
      headers: {Authorization: `Bearer ${env.FEED_PHOTO_GH_TOKEN}`, Accept: accept, 'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': UA, ...(body ? {'Content-Type': 'application/json'} : {})},
      body: body ? JSON.stringify(body) : undefined});

  const listing = await gh(`contents/${QUEUE}?ref=main`);
  if (listing.status !== 200) throw new Error(`queue_http_${listing.status}`);
  const jobs = [];
  for (const name of jobFiles(await listing.json())) {
    const file = await gh(`contents/${QUEUE}/${name}?ref=main`, {accept: 'application/vnd.github.raw+json'});
    if (file.status !== 200) throw new Error(`job_http_${file.status}`);
    const text = await file.text();
    if (text.length > 50000) throw new Error('job_too_large');
    jobs.push(readJob(text));
  }
  if (new Set(jobs.map(job => job.id)).size !== jobs.length) throw new Error('duplicate_job_id');
  const due = jobs.filter(job => isDue(job, now));
  if (!due.length) return {state: 'nothing_due', queued: jobs.length};

  const todo = [], done = [], uncertain = [], unknown = [];
  for (const job of due) {
    try {
      const response = await request(`${STATE_BASE}${job.id}.json?trigger_read=${now}`, {
        redirect: 'manual', signal: AbortSignal.timeout(10000), headers: {'Cache-Control': 'no-cache', 'User-Agent': UA}});
      const verdict = stateVerdict(response.status, response.status === 200 ? await response.json() : null);
      if (verdict === 'posted') done.push(job.id);
      else if (verdict === 'uncertain') uncertain.push(job.id);
      else todo.push(job);
    } catch (error) {
      unknown.push({id: job.id, code: safeCode(error)});
    }
  }
  const base = {due: due.map(job => job.id), done, uncertain, unknown};
  if (uncertain.length) return {state: 'uncertain', ...base};
  if (!todo.length) return {state: unknown.length ? 'state_unknown' : 'done', ...base};

  const response = await gh(`actions/workflows/${WORKFLOW}/runs?per_page=${RUN_PAGE}&exclude_pull_requests=true`);
  if (response.status !== 200) throw new Error(`runs_http_${response.status}`);
  const runs = (await response.json()).workflow_runs;
  if (!Array.isArray(runs)) throw new Error('bad_run_list');
  if (runs.some(run => ACTIVE.has(run.status))) return {state: 'publisher_active', ...base};
  const windowStart = Math.min(...todo.map(job => job.at));
  if (runs.length >= RUN_PAGE && Date.parse(runs.at(-1).created_at) >= windowStart) throw new Error('run_inventory_incomplete');
  const since = runs.filter(run => Date.parse(run.created_at) >= windowStart);
  if (since.some(run => run.status === 'completed' && run.conclusion === 'success')) return {state: 'handled_by_run', ...base};
  const attempts = since.filter(run => run.event === 'workflow_dispatch');
  if (attempts.length >= MAX_ATTEMPTS) return {state: 'retry_limit', attempts: attempts.length, ...base};
  if (attempts.some(run => now - Date.parse(run.created_at) < COOLDOWN)) return {state: 'cooldown', ...base};
  if (env.DRY_RUN === 'true') return {state: 'would_dispatch', ...base};
  const sent = await gh(`actions/workflows/${WORKFLOW}/dispatches`, {method: 'POST', body: {ref: 'main'}});
  if (sent.status !== 204 && sent.status !== 200) throw new Error(`dispatch_http_${sent.status}`);
  return {state: 'dispatch_accepted', ...base};
}

export default {
  async scheduled(controller, env) {
    let result;
    try {
      result = await tick(env);
    } catch (error) {
      result = {state: 'error', code: safeCode(error)};
    }
    console.log(JSON.stringify({event: 'feed_photo_trigger', version: VERSION, at: new Date().toISOString(), ...result}));
  },
  async fetch() {
    return new Response('Not found', {status: 404});
  },
};
