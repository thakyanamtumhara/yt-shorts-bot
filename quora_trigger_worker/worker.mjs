// Starts yt-shorts-bot's quora_answers.yml on time (Ketu 18-Oct-2026: "keep checking so we are first to respond").
// GitHub's own cron started it ~5 h late every day (19-24 Oct-2026: the 08:17 IST run began 13:06-13:36, the 10:47 one
// 15:05-16:20), after the Post Time alarm (11-12 IST), so the page still showed the day before's answer.
// 02:47 UTC = 08:17 IST -> action send; 05:17 UTC = 10:47 IST -> action catchup (writes it only if the 08:17 one did not,
// and tells Ketu). GitHub's schedules stay as late backups; quora_answers.py skips a day that is already written.
// GitHub refusing the start -> Telegram (payerr-watch's bot + chat, from WWbun's Railway variables). Stores nothing.
const REPO = 'thakyanamtumhara/yt-shorts-bot';
const WORKFLOW = 'quora_answers.yml';
export const SLOTS = {'47 2 * * MON-SAT': ['send', '8:17'], '17 5 * * MON-SAT': ['catchup', '10:47']};
const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

export async function dispatch(env, action, {request = fetch, wait = sleep, tries = 3} = {}) {
  if (!env.QA_GH_TOKEN) return {state: 'error', code: 'missing_token', action};
  let code = '';
  for (let i = 0; i < tries; i++) {
    if (i) await wait(20000);
    try {
      const response = await request(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
        method: 'POST', redirect: 'manual',
        headers: {Authorization: `Bearer ${env.QA_GH_TOKEN}`, Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'sale91-quora-trigger', 'Content-Type': 'application/json'},
        body: JSON.stringify({ref: 'main', inputs: {action}})});
      if (response.status === 204) return {state: 'dispatch_accepted', action, tries: i + 1};
      code = `dispatch_http_${response.status}`;
      if ([401, 403, 404, 422].includes(response.status)) break;
    } catch (error) {
      code = error?.name || 'fetch_failed';
    }
  }
  return {state: 'error', code, action};
}

export async function telegram(env, text, request = fetch) {
  if (!env.TG_TOKEN || !env.TG_CHAT) return false;
  try {
    const response = await request(`https://api.telegram.org/bot${env.TG_TOKEN}/sendMessage`, {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({chat_id: env.TG_CHAT, text, disable_web_page_preview: true})});
    return (await response.json()).ok === true;
  } catch {
    return false;
  }
}

export async function tick(env, cron, deps = {}) {
  if (env.ENABLED !== 'true') return {state: 'disabled'};
  const [action, time] = SLOTS[cron] || ['send', 'test'];
  const result = await dispatch(env, action, deps);
  if (result.state !== 'dispatch_accepted') {
    result.told = await telegram(env, `Quora timer: GitHub did not accept the ${time} IST start of today's answer ` +
      `(${result.code}). GitHub's own late run will still try; if that fails too, you get "Today's Quora answer did ` +
      `not go out".`, deps.request);
  }
  return result;
}

export default {
  async scheduled(controller, env) {
    let result;
    try {
      result = await tick(env, controller.cron);
    } catch (error) {
      result = {state: 'error', code: error?.name || 'exception'};
      result.told = await telegram(env, `Quora timer crashed (${result.code}). GitHub's own late run will still try.`);
    }
    console.log(JSON.stringify({quora_trigger: result, cron: controller.cron}));
  },
  async fetch() {
    return new Response('Not found', {status: 404});
  },
};
