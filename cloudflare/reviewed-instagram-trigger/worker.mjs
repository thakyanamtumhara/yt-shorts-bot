const REPO = 'thakyanamtumhara/yt-shorts-bot';
const WORKFLOW = 'reviewed_ai_reels.yml';
const ACCOUNT = '17841407981790313';
const STATE_BASE = 'https://www.bulkplaintshirt.com/p/automation-state-reviewed-ai-reels/';
const PUBLISH_TITLE = 'Reviewed Instagram · publish_due';
const SHA = /^[a-f0-9]{64}$/;
const ID = /^[a-z0-9][a-z0-9-]{0,79}$/;
const ACTIVE = new Set(['queued', 'in_progress', 'waiting', 'requested', 'pending']);
const KINDS = new Set(['original_recording', 'real_footage_ai', 'cloned_voice_screen_recording']);
const STATE_FIELDS = new Set('format mode job_id job_sha256 account_id video_sha256 selection_sha256 phase pending parent_id media_id publish_attempt_at published_at native_ai_requested native_ai_verified prepared_at comment_id comment_verified'.split(' '));
const COOLDOWN_MS = 15 * 60 * 1000;
const RETRY_WINDOW_MS = 6 * 60 * 60 * 1000;

function check(condition, code) {
  if (!condition) throw new Error(code);
}

function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function exactKeys(value, required, optional = []) {
  check(object(value) && required.every(key => Object.hasOwn(value, key)) && Object.keys(value).every(key => [...required, ...optional].includes(key)), 'schema_mismatch');
}

function time(value) {
  check(typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value)), 'invalid_timestamp');
  return Date.parse(value);
}

function stable(value) {
  if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
  if (object(value)) return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + stable(value[key])).join(',') + '}';
  return JSON.stringify(value);
}

export async function digest(value) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(stable(value)));
  return Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, '0')).join('');
}

export function validateQueue(jobs, receipts, now) {
  const batches = new Map();
  for (const receipt of receipts) {
    exactKeys(receipt, ['batch_id', 'approval_method', 'approved_at', 'videos']);
    check(typeof receipt.batch_id === 'string' && !batches.has(receipt.batch_id) && receipt.approval_method === 'user_exact_selection' && time(receipt.approved_at) <= now, 'invalid_approval');
    check(Array.isArray(receipt.videos) && receipt.videos.length > 0, 'empty_approval');
    const ids = new Set(), hashes = new Set();
    for (const row of receipt.videos) {
      exactKeys(row, ['id', 'version', 'sha256', 'public_release_approved'], ['media_kind']);
      check(typeof row.id === 'string' && typeof row.version === 'string' && SHA.test(row.sha256) && row.public_release_approved === true && KINDS.has(row.media_kind ?? 'real_footage_ai') && !ids.has(row.id) && !hashes.has(row.sha256), 'invalid_selection');
      ids.add(row.id); hashes.add(row.sha256);
    }
    batches.set(receipt.batch_id, receipt);
  }
  const ids = new Set(), hashes = new Set();
  for (const job of jobs) {
    exactKeys(job, ['id', 'status', 'account_id', 'publish_at', 'caption', 'video_url', 'video_sha256', 'cover_url', 'cover_sha256', 'is_ai_generated', 'user_selection'], ['media_kind', 'first_comment']);
    check(ID.test(job.id) && !ids.has(job.id) && !hashes.has(job.video_sha256) && job.status === 'reviewed' && job.account_id === ACCOUNT, 'invalid_job_identity');
    ids.add(job.id); hashes.add(job.video_sha256);
    const selection = job.user_selection;
    exactKeys(selection, ['batch_id', 'id', 'version', 'sha256', 'approved']);
    const receipt = batches.get(selection.batch_id), kind = job.media_kind ?? 'real_footage_ai';
    check(receipt && selection.approved === true && selection.sha256 === job.video_sha256 && KINDS.has(kind) && job.is_ai_generated === (kind !== 'original_recording'), 'approval_mismatch');
    check(receipt.videos.some(row => row.id === selection.id && row.version === selection.version && row.sha256 === selection.sha256 && (row.media_kind ?? 'real_footage_ai') === kind), 'unapproved_version');
    check(typeof job.publish_at === 'string' && job.publish_at.endsWith('+05:30') && time(job.publish_at) > time(receipt.approved_at), 'invalid_publish_time');
    check(typeof job.caption === 'string' && [...job.caption].length >= 1 && [...job.caption].length <= 2200, 'invalid_caption');
    if (kind === 'real_footage_ai') check(job.caption.includes('AI-assisted dialogue using my own footage and voice.'), 'missing_disclosure');
    if (kind === 'cloned_voice_screen_recording') check(job.caption.includes('AI voice: my own cloned voice.') && !job.caption.includes('AI-assisted dialogue using my own footage and voice.'), 'invalid_disclosure');
    if (Object.hasOwn(job, 'first_comment')) check(typeof job.first_comment === 'string' && [...job.first_comment].length >= 1 && [...job.first_comment].length <= 2200, 'invalid_comment');
    for (const asset of ['video', 'cover']) {
      check(typeof job[asset + '_url'] === 'string' && SHA.test(job[asset + '_sha256']), 'invalid_asset');
      const url = new URL(job[asset + '_url']);
      check(url.protocol === 'https:' && !url.username && !url.password && !url.hash, 'invalid_asset_url');
    }
  }
  return jobs.filter(job => time(job.publish_at) <= now);
}

export async function classifyState(job, state) {
  if (state === null) return 'due';
  check(object(state) && Object.keys(state).every(key => STATE_FIELDS.has(key)), 'invalid_state_schema');
  const binding = {format: 'reviewed-ai-reel-v1', mode: 'publish', job_id: job.id, account_id: ACCOUNT, video_sha256: job.video_sha256, job_sha256: await digest(job), selection_sha256: await digest(job.user_selection)};
  check(Object.entries(binding).every(([key, value]) => state[key] === value) && state.native_ai_requested === job.is_ai_generated, 'state_binding_mismatch');
  if (state.pending) return 'ambiguous';
  if (state.phase === 'published_verified' && typeof state.media_id === 'string' && /^\d+$/.test(state.media_id) && state.native_ai_verified === true && (!job.first_comment || (state.comment_verified === true && typeof state.comment_id === 'string' && /^\d+$/.test(state.comment_id)))) return 'verified';
  return 'due';
}

function safeCode(error) {
  return /^[a-z0-9_]+$/.test(error?.message ?? '') ? error.message : 'request_or_parse_failed';
}

export async function tick(env, options = {}) {
  const now = options.now ?? Date.now(), request = options.fetch ?? fetch;
  if (env.ENABLED !== 'true') return {status: 'disabled', version: '1.0.1'};
  check(typeof env.REVIEWED_IG_GH_TOKEN === 'string' && env.REVIEWED_IG_GH_TOKEN.length > 0, 'github_secret_missing');
  async function raw(url, {github = false, method = 'GET', body, missing = false} = {}) {
    const headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'reviewed-instagram-trigger/1.0.1', 'Cache-Control': 'no-cache'};
    if (github) {
      check(url.startsWith(`https://api.github.com/repos/${REPO}/`), 'unexpected_github_target');
      headers.Authorization = `Bearer ${env.REVIEWED_IG_GH_TOKEN}`;
      headers['X-GitHub-Api-Version'] = '2022-11-28';
    }
    if (body) headers['Content-Type'] = 'application/json';
    const response = await request(url, {method, headers, body: body ? JSON.stringify(body) : undefined, redirect: 'manual', signal: AbortSignal.timeout(12000), cf: {cacheTtl: 0, cacheEverything: false}});
    if (missing && response.status === 404) return null;
    check(response.ok, 'http_' + response.status);
    if (response.status === 204) return null;
    check(Number(response.headers.get('content-length') ?? 0) <= 1000000, 'response_too_large');
    const text = await response.text();
    check(text.length <= 1000000, 'response_too_large');
    return text ? JSON.parse(text) : null;
  }
  const gh = (path, opts = {}) => raw(`https://api.github.com/repos/${REPO}/${path}`, {...opts, github: true});
  const head = await gh('commits/main');
  check(typeof head.sha === 'string' && /^[a-f0-9]{40}$/.test(head.sha), 'invalid_main_commit');
  async function file(path) {
    const data = await gh(`contents/${path}?ref=${head.sha}`);
    check(data?.type === 'file' && data.encoding === 'base64' && typeof data.content === 'string' && data.size <= 100000, 'invalid_repository_file');
    const bytes = Uint8Array.from(atob(data.content.replace(/\s/g, '')), char => char.charCodeAt(0));
    return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));
  }
  async function directory(path) {
    const entries = await gh(`contents/${path}?ref=${head.sha}`);
    check(Array.isArray(entries) && entries.length < 1000, 'invalid_repository_directory');
    const files = entries.filter(item => item.type === 'file' && item.name.endsWith('.json'));
    check(files.length <= 20 && files.every(item => /^[a-zA-Z0-9-]+\.json$/.test(item.name) && item.path === `${path}/${item.name}`), 'queue_limit_or_path');
    return Promise.all(files.map(item => file(item.path)));
  }
  const [jobs, baseReceipt, batchReceipts] = await Promise.all([directory('reviewed_ai_reels'), file('reviewed_ai_releases/approvals.json'), directory('reviewed_ai_releases/batches')]);
  check(2 * jobs.length + batchReceipts.length + 12 <= 48, 'queue_subrequest_limit');
  const due = validateQueue(jobs, [baseReceipt, ...batchReceipts], now);
  const ready = [], blocked = [], verified = [];
  for (const job of due) {
    try {
      const state = await raw(`${STATE_BASE}${job.video_sha256}.json?trigger_read=${now}`, {missing: true});
      const status = await classifyState(job, state);
      if (status === 'verified') verified.push(job.id);
      else if (status === 'ambiguous') blocked.push({job_id: job.id, code: 'ambiguous_saved_post'});
      else ready.push(job);
    } catch (error) { blocked.push({job_id: job.id, code: safeCode(error)}); }
  }
  const base = {version: '1.0.1', commit: head.sha, due_count: due.length, verified_count: verified.length, ready: ready.map(job => job.id), blocked};
  if (!ready.length) return {...base, status: blocked.length ? 'blocked' : 'nothing_due'};
  const runPath = `actions/workflows/${WORKFLOW}/runs?branch=main&per_page=100&exclude_pull_requests=true`;
  for (const status of ['queued', 'in_progress', 'waiting']) {
    const activeRuns = await gh(`${runPath}&status=${status}`);
    check(Array.isArray(activeRuns.workflow_runs), 'invalid_run_inventory');
    if (activeRuns.workflow_runs.length) return {...base, status: 'publisher_active'};
  }
  const runs = await gh(runPath);
  check(Array.isArray(runs.workflow_runs), 'invalid_run_inventory');
  if (runs.workflow_runs.some(run => ACTIVE.has(run.status))) return {...base, status: 'publisher_active'};
  const windowStart = Math.max(now - RETRY_WINDOW_MS, Math.min(...ready.map(job => time(job.publish_at))));
  check(runs.workflow_runs.length < 100 || time(runs.workflow_runs.at(-1).created_at) < windowStart, 'run_inventory_incomplete');
  const attempts = runs.workflow_runs.filter(run => run.display_title === PUBLISH_TITLE && ['schedule', 'workflow_dispatch'].includes(run.event) && time(run.created_at) >= windowStart);
  if (attempts.length >= 3) return {...base, status: 'retry_limit', attempts: attempts.length};
  if (attempts.some(run => now - time(run.created_at) < COOLDOWN_MS)) return {...base, status: 'cooldown'};
  if (env.DRY_RUN === 'true') return {...base, status: 'would_dispatch'};
  const receipt = await gh(`actions/workflows/${WORKFLOW}/dispatches`, {method: 'POST', body: {ref: 'main', inputs: {mode: 'publish_due'}}});
  return {...base, status: 'dispatch_accepted', run_id: receipt?.workflow_run_id ?? null};
}

export default {
  async scheduled(controller, env, context) {
    context.waitUntil((async () => {
      try { console.log(JSON.stringify({event: 'reviewed_instagram_trigger', at: new Date().toISOString(), ...await tick(env)})); }
      catch (error) { console.error(JSON.stringify({event: 'reviewed_instagram_trigger', status: 'error', code: safeCode(error)})); throw new Error(safeCode(error)); }
    })());
  },
  fetch() { return new Response('Not found', {status: 404}); }
};
