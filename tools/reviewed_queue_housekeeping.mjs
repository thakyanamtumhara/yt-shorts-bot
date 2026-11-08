import {existsSync, mkdirSync, readdirSync, readFileSync, renameSync} from 'node:fs';
import {join} from 'node:path';
import {classifyState, tick} from '../cloudflare/reviewed-instagram-trigger/worker.mjs';

export const QUEUE = 'reviewed_ai_reels';
export const BATCHES = 'reviewed_ai_releases/batches';
export const FILE_CAP = 20;
export const BUDGET_CAP = 48;
const STATE_BASE = 'https://www.bulkplaintshirt.com/p/automation-state-reviewed-ai-reels/';
const SETTLE_MS = 6 * 60 * 60 * 1000;
const LATE_MS = 30 * 60 * 1000;
const WARN_FILES = 16;
const WARN_BUDGET = 42;
const KEEP_RECEIPTS = new Set(['raw0004-20260929.json']);
const IST = {timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false};

function jsonFiles(dir) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir, {withFileTypes: true}).filter(entry => entry.isFile() && entry.name.endsWith('.json')).map(entry => entry.name).sort();
}

function read(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

function safe(error) {
  return /^[a-z0-9_]+$/.test(error?.message ?? '') ? error.message : 'request_or_parse_failed';
}

function move(fromDir, toDir, name) {
  if (existsSync(join(toDir, name))) return false;
  mkdirSync(toDir, {recursive: true});
  renameSync(join(fromDir, name), join(toDir, name));
  return true;
}

export function counts(root = '.') {
  const jobs = jsonFiles(join(root, QUEUE)).length;
  const receipts = jsonFiles(join(root, BATCHES)).length;
  return {jobs, receipts, budget: 2 * jobs + receipts + 12};
}

export async function archive({root = '.', now = Date.now(), request = fetch} = {}) {
  const queueDir = join(root, QUEUE), queueArchive = join(queueDir, 'archive');
  const batchDir = join(root, BATCHES), batchArchive = join(batchDir, 'archive');
  const movedJobs = [], movedReceipts = [], restoredReceipts = [], kept = [];
  for (const name of jsonFiles(queueDir)) {
    try {
      const job = read(join(queueDir, name));
      const due = Date.parse(job.publish_at);
      if (!Number.isFinite(due) || now - due < SETTLE_MS) { kept.push({file: name, why: 'not_settled'}); continue; }
      const response = await request(`${STATE_BASE}${job.video_sha256}.json?housekeeping=${now}`, {headers: {'Cache-Control': 'no-cache'}, redirect: 'manual', signal: AbortSignal.timeout(12000)});
      if (!response.ok) { kept.push({file: name, why: 'state_http_' + response.status}); continue; }
      const state = JSON.parse(await response.text());
      if (await classifyState(job, state) !== 'verified') { kept.push({file: name, why: 'not_verified'}); continue; }
      if (move(queueDir, queueArchive, name)) movedJobs.push(name);
      else kept.push({file: name, why: 'archive_name_taken'});
    } catch (error) {
      kept.push({file: name, why: safe(error)});
    }
  }
  const queued = new Set();
  let queueReadable = true;
  for (const name of jsonFiles(queueDir)) {
    try { queued.add(read(join(queueDir, name)).user_selection.batch_id); } catch { queueReadable = false; }
  }
  if (!queueReadable) return {moved_jobs: movedJobs, moved_receipts: movedReceipts, restored_receipts: restoredReceipts, kept, receipts_skipped: 'unreadable_queue_job', ...counts(root)};
  const topBatches = new Set();
  for (const name of jsonFiles(batchDir)) {
    try { topBatches.add(read(join(batchDir, name)).batch_id); } catch { topBatches.add(null); }
  }
  for (const name of jsonFiles(batchArchive)) {
    try {
      const batch = read(join(batchArchive, name)).batch_id;
      if (queued.has(batch) && !topBatches.has(batch) && move(batchArchive, batchDir, name)) { restoredReceipts.push(name); topBatches.add(batch); }
    } catch {}
  }
  const released = new Set();
  for (const name of jsonFiles(queueArchive)) {
    try { const selection = read(join(queueArchive, name)).user_selection; released.add(selection.batch_id + '\u0000' + selection.id); } catch {}
  }
  for (const name of jsonFiles(batchDir)) {
    if (KEEP_RECEIPTS.has(name)) continue;
    try {
      const receipt = read(join(batchDir, name));
      if (queued.has(receipt.batch_id)) continue;
      if (!Array.isArray(receipt.videos) || !receipt.videos.length || !receipt.videos.every(row => released.has(receipt.batch_id + '\u0000' + row.id))) continue;
      if (move(batchDir, batchArchive, name)) movedReceipts.push(name);
    } catch {}
  }
  return {moved_jobs: movedJobs, moved_receipts: movedReceipts, restored_receipts: restoredReceipts, kept, ...counts(root)};
}

function lateReady(result, root, now) {
  const lines = [];
  for (const id of result?.ready ?? []) {
    try {
      const due = Date.parse(read(join(root, QUEUE, id + '.json')).publish_at);
      if (Number.isFinite(due) && now - due > LATE_MS) lines.push(`${id} was due ${new Date(due).toLocaleString('en-GB', IST)} IST and nothing has started it yet`);
    } catch {}
  }
  return lines;
}

export async function check({root = '.', now = Date.now(), token, request = fetch, wait = ms => new Promise(done => setTimeout(done, ms)), attempts = 3} = {}) {
  const lines = [];
  let result = null, failure = null;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try { result = await tick({ENABLED: 'true', DRY_RUN: 'true', REVIEWED_IG_GH_TOKEN: token}, {now, fetch: request}); failure = null; break; }
    catch (error) { failure = safe(error); if (attempt < attempts) await wait(10000); }
  }
  const size = counts(root);
  const sizeLine = `Now: ${size.jobs} Reels queued, ${size.receipts} approval files (limit ${FILE_CAP} each), budget ${size.budget}/${BUDGET_CAP}.`;
  if (failure) lines.push(`The on-time trigger cannot read the Reel queue (${failure}). Approved Reels will go out late, only when GitHub's slow timer runs, until an AI session fixes it.`);
  for (const item of result?.blocked ?? []) lines.push(`${item.job_id} is blocked (${item.code}).`);
  if (result?.status === 'retry_limit') lines.push(`A due Reel was started ${result.attempts} times in 6 hours and is still not verified.`);
  if (result?.status === 'would_dispatch') lines.push(...lateReady(result, root, now));
  if (size.jobs > WARN_FILES || size.receipts > WARN_FILES || size.budget > WARN_BUDGET) lines.push(`The Reel queue is nearly full. Archive released Reels before booking more, or the on-time trigger stops.`);
  return {result, failure, lines, sizeLine, ...size};
}

async function telegram(text) {
  const token = (process.env.TELEGRAM_BOT_TOKEN || '').trim(), chat = (process.env.TELEGRAM_ALERT_CHAT_ID || '').trim();
  if (!token || !chat) { console.log('Telegram secrets missing; alert not sent.'); return false; }
  try {
    const response = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({chat_id: chat, text, disable_web_page_preview: true}), signal: AbortSignal.timeout(20000)});
    console.log(response.ok ? 'Telegram sent.' : 'Telegram refused: HTTP ' + response.status);
    return response.ok;
  } catch {
    console.log('Telegram request failed.');
    return false;
  }
}

async function main(mode) {
  if (mode === 'archive') {
    console.log(JSON.stringify(await archive(), null, 2));
    return 0;
  }
  if (mode === 'check') {
    const outcome = await check({token: (process.env.GH_TOKEN || '').trim()});
    console.log(JSON.stringify({status: outcome.result?.status ?? 'error', failure: outcome.failure, due: outcome.result?.due_count, verified: outcome.result?.verified_count, ready: outcome.result?.ready, blocked: outcome.result?.blocked, jobs: outcome.jobs, receipts: outcome.receipts, budget: outcome.budget}, null, 2));
    if (!outcome.lines.length) { console.log('Reviewed Reels trigger: healthy, nothing to report.'); return 0; }
    let text = 'Instagram Reels on-time trigger needs a look\n- ' + outcome.lines.join('\n- ') + '\n' + outcome.sizeLine;
    if (process.env.RUN_URL) text += '\n' + process.env.RUN_URL;
    console.log(text);
    return await telegram(text) ? 0 : 1;
  }
  console.log('usage: node tools/reviewed_queue_housekeeping.mjs archive|check');
  return 2;
}

if (import.meta.url === `file://${process.argv[1]}`) process.exitCode = await main(process.argv[2]);
