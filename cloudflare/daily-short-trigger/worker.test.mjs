import assert from 'node:assert/strict';
import test from 'node:test';
import {istDay, tick, todaysRun} from './worker.mjs';

const MON_1430_IST = Date.parse('2026-10-05T09:00:00Z');
const SUN_1430_IST = Date.parse('2026-10-04T09:00:00Z');
const env = {ENABLED: 'true', DRY_RUN: 'false', DAILY_SHORT_GH_TOKEN: 'token'};

function github(runs, dispatchStatus = 204) {
  const calls = [];
  const request = async (url, init = {}) => {
    calls.push({url, init});
    if (url.endsWith('/dispatches')) return {status: dispatchStatus};
    return {status: 200, json: async () => ({workflow_runs: runs})};
  };
  return {calls, request};
}

test('IST day and weekday', () => {
  assert.deepEqual(istDay(MON_1430_IST), {date: '2026-10-05', weekday: 1});
  assert.equal(istDay(Date.parse('2026-10-04T19:00:00Z')).date, '2026-10-05');
});

test('starts the run with the review gate when nothing ran today', async () => {
  const {calls, request} = github([{id: 1, created_at: '2026-10-04T10:00:00Z', conclusion: 'success', display_title: 'Daily YouTube Short'}]);
  assert.deepEqual(await tick(env, {now: MON_1430_IST, request}), {state: 'dispatch_accepted', date: '2026-10-05'});
  const body = JSON.parse(calls[1].init.body);
  assert.deepEqual(body, {ref: 'main', inputs: {owner_review: 'true', enable_subtitles: 'true'}});
  assert.equal(calls[1].init.redirect, 'manual');
});

test('never a second run; test and cancelled runs do not count', async () => {
  const today = [{id: 7, created_at: '2026-10-05T03:00:00Z', conclusion: 'failure', display_title: 'Daily YouTube Short'}];
  const {calls, request} = github(today);
  assert.deepEqual(await tick(env, {now: MON_1430_IST, request}), {state: 'already_started', date: '2026-10-05', run_id: 7});
  assert.equal(calls.length, 1);
  const ignored = [{id: 8, created_at: '2026-10-05T03:00:00Z', conclusion: 'success', display_title: 'Daily YouTube Short (test)'},
    {id: 9, created_at: '2026-10-05T04:00:00Z', conclusion: 'cancelled', display_title: 'Daily YouTube Short'}];
  assert.equal(todaysRun(ignored, '2026-10-05'), undefined);
});

test('Sunday, disabled, dry run, missing token and GitHub errors never dispatch', async () => {
  const {calls, request} = github([]);
  assert.equal((await tick(env, {now: SUN_1430_IST, request})).state, 'sunday');
  assert.equal((await tick({...env, ENABLED: 'false'}, {now: MON_1430_IST, request})).state, 'disabled');
  assert.equal((await tick({...env, DAILY_SHORT_GH_TOKEN: ''}, {now: MON_1430_IST, request})).code, 'missing_token');
  assert.equal((await tick({...env, DRY_RUN: 'true'}, {now: MON_1430_IST, request})).state, 'would_dispatch');
  assert.ok(calls.every(call => !call.url.endsWith('/dispatches')));
  const failing = github([], 403);
  assert.equal((await tick(env, {now: MON_1430_IST, request: failing.request})).code, 'dispatch_http_403');
  const down = async () => ({status: 502});
  assert.equal((await tick(env, {now: MON_1430_IST, request: down})).code, 'list_http_502');
});
