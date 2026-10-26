import test from 'node:test';
import assert from 'node:assert/strict';
import {dispatch, tick, SLOTS} from './worker.mjs';

const env = {ENABLED: 'true', QA_GH_TOKEN: 'gh-test', TG_TOKEN: 'tg-test', TG_CHAT: '42'};
const noWait = async () => {};

function fake(statuses) {
  const calls = [];
  const request = async (url, init) => {
    calls.push({url, init});
    if (url.startsWith('https://api.telegram.org/')) return {json: async () => ({ok: true})};
    return {status: statuses.shift() ?? 204};
  };
  return {calls, request};
}

test('the two crons are 08:17 send and 10:47 catchup, Monday to Saturday', () => {
  assert.deepEqual(SLOTS['47 2 * * MON-SAT'], ['send', '8:17']);
  assert.deepEqual(SLOTS['17 5 * * MON-SAT'], ['catchup', '10:47']);
});

test('a start GitHub accepts sends the right action to main and no Telegram', async () => {
  const {calls, request} = fake([204]);
  const result = await tick(env, '17 5 * * MON-SAT', {request, wait: noWait});
  assert.equal(result.state, 'dispatch_accepted');
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /repos\/thakyanamtumhara\/yt-shorts-bot\/actions\/workflows\/quora_answers\.yml\/dispatches$/);
  assert.deepEqual(JSON.parse(calls[0].init.body), {ref: 'main', inputs: {action: 'catchup'}});
  assert.equal(calls[0].init.headers.Authorization, 'Bearer gh-test');
});

test('a GitHub outage is tried 3 times, then Telegram names the slot', async () => {
  const {calls, request} = fake([502, 503, 500]);
  const result = await tick(env, '47 2 * * MON-SAT', {request, wait: noWait});
  assert.equal(result.state, 'error');
  assert.equal(result.code, 'dispatch_http_500');
  assert.equal(result.told, true);
  const gh = calls.filter(c => c.url.startsWith('https://api.github.com/'));
  assert.equal(gh.length, 3);
  const tg = calls.find(c => c.url.startsWith('https://api.telegram.org/'));
  assert.match(JSON.parse(tg.init.body).text, /8:17 IST start/);
});

test('a refused token is not retried', async () => {
  const {calls, request} = fake([401, 204]);
  const result = await dispatch(env, 'send', {request, wait: noWait});
  assert.equal(result.code, 'dispatch_http_401');
  assert.equal(calls.length, 1);
});

test('a missing GitHub token still tells Ketu', async () => {
  const {calls, request} = fake([]);
  const result = await tick({...env, QA_GH_TOKEN: ''}, '47 2 * * MON-SAT', {request, wait: noWait});
  assert.equal(result.code, 'missing_token');
  assert.equal(result.told, true);
  assert.equal(calls.length, 1);
});

test('an unlisted cron (a one-off test) starts a normal send', async () => {
  const {calls, request} = fake([204]);
  const result = await tick(env, '5 7 26 10 *', {request, wait: noWait});
  assert.equal(result.action, 'send');
  assert.deepEqual(JSON.parse(calls[0].init.body).inputs, {action: 'send'});
});

test('switched off = nothing is called', async () => {
  const {calls, request} = fake([204]);
  assert.deepEqual(await tick({...env, ENABLED: 'false'}, '47 2 * * MON-SAT', {request}), {state: 'disabled'});
  assert.equal(calls.length, 0);
});
