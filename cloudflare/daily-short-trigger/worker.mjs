// Starts the day's Short at 14:30 IST (Ketu 5-Oct-2026: cloud first). GitHub's own cron starts it 3-11 h late.
// Never starts a second run: any non-cancelled, non-test daily_short.yml run created today (IST) means done.
const REPO = 'thakyanamtumhara/yt-shorts-bot';
const WORKFLOW = 'daily_short.yml';
const IST_MS = 19800000;

export function istDay(ms) {
  const date = new Date(ms + IST_MS);
  return {date: date.toISOString().slice(0, 10), weekday: date.getUTCDay()};
}

export function todaysRun(runs, date) {
  return runs.find(run => run.conclusion !== 'cancelled' && !String(run.display_title || '').endsWith('(test)')
    && istDay(Date.parse(run.created_at)).date === date);
}

export async function tick(env, {now = Date.now(), request = fetch} = {}) {
  if (env.ENABLED !== 'true') return {state: 'disabled'};
  if (!env.DAILY_SHORT_GH_TOKEN) return {state: 'error', code: 'missing_token'};
  const today = istDay(now);
  if (today.weekday === 0) return {state: 'sunday', date: today.date};
  const headers = {Authorization: `Bearer ${env.DAILY_SHORT_GH_TOKEN}`, Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'sale91-daily-short-trigger'};
  const base = `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}`;
  const listing = await request(`${base}/runs?per_page=30`, {headers, redirect: 'manual'});
  if (listing.status !== 200) return {state: 'error', code: `list_http_${listing.status}`};
  const started = todaysRun((await listing.json()).workflow_runs || [], today.date);
  if (started) return {state: 'already_started', date: today.date, run_id: started.id};
  if (env.DRY_RUN === 'true') return {state: 'would_dispatch', date: today.date};
  const dispatch = await request(`${base}/dispatches`, {method: 'POST', redirect: 'manual',
    headers: {...headers, 'Content-Type': 'application/json'},
    body: JSON.stringify({ref: 'main', inputs: {owner_review: 'true', enable_subtitles: 'true'}})});
  if (dispatch.status !== 204) return {state: 'error', code: `dispatch_http_${dispatch.status}`};
  return {state: 'dispatch_accepted', date: today.date};
}

export default {
  async scheduled(controller, env) {
    let result;
    try {
      result = await tick(env);
    } catch (error) {
      result = {state: 'error', code: error?.name || 'exception'};
    }
    console.log(JSON.stringify({daily_short_trigger: result}));
  },
  async fetch() {
    return new Response('Not found', {status: 404});
  },
};
