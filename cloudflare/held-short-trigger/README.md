# Held Short trigger (Cloudflare Worker)

Ketu's rule: cloud first, laptop only as a fallback. `held_short_release.yml` posts the Facebook Reel + Telegram leg
of an owner-approved held Short (`held_shorts/jobs.json`, `meta_at`), but `tools/held_short_release.py` posts only
within 4 h of `meta_at`. GitHub started that workflow's cron 4 h 24 m and 5 h 22 m late on 9 and 10-Oct-2026, so DTF B
(Sat 10-Oct 19:00 IST) never went out on Facebook or Telegram.

This Worker runs every 5 min (UTC minutes 4, 9, ..., 59). It reads `held_shorts/jobs.json` on `main`. A job is due
from `meta_at` + 2 min to `meta_at` + 3 h 30 min. It is skipped when its public release state
(`https://www.bulkplaintshirt.com/p/automation-state-held-shorts/<id>.json`) already records both `facebook` and
`telegram` (posted, pending or failed). If a due job is still open, the Worker dispatches `held_short_release.yml`
with `mode=meta_due` on `main`. Three guards apply: no dispatch while a run of that workflow is queued or running, a
20-minute cooldown after any meta_due run, and at most 3 meta_due runs per due window.

It never posts and never writes state. The guard against a second post stays in the tool. Its S3 state uses
conditional writes, and a platform with an id, a pending attempt or a failure is never posted again. The workflow's
concurrency group runs one release at a time. GitHub's own cron stays as a backup. No routes, no workers.dev URL; the
HTTP handler returns 404. Logs: one `held_short_trigger` JSON line per tick. Its state is one of `disabled`,
`nothing_due`, `done`, `publisher_active`, `cooldown`, `retry_limit`, `would_dispatch`, `dispatch_accepted`, the
probe states, or `error` with a code.

`PROBE=check` (a deploy-time `--var` only, never in a config) makes a tick with nothing due dispatch the posting-free
`mode=check` once an hour. It verifies the held files and prints the captions, which proves the cloud dispatch path.
A due job still gets `meta_due` in probe mode.

## Deploy (as done 11-Oct-2026)

```sh
node --test worker.test.mjs
node read-only-check.mjs                       # DRY_RUN; optional ISO time argument, e.g. 2026-10-11T19:04:00+05:30
wrangler deploy --config wrangler.disabled.jsonc   # disabled, no cron = the undo, deployed first
python3 secret-install.py                          # gh token of thakyanamtumhara -> HELD_SHORT_GH_TOKEN
wrangler deploy --config wrangler.jsonc --var PROBE:check   # one harmless check-mode dispatch
wrangler deploy --config wrangler.jsonc            # enabled + cron, no probe
```

Watch a tick: `wrangler tail held-short-trigger --format json`. If `gh auth login` is redone for thakyanamtumhara, the
old token stops working: run `python3 secret-install.py` again (same for daily-short-trigger and
reviewed-instagram-trigger).

## Undo

```sh
wrangler deploy --config wrangler.disabled.jsonc   # stops the cron and every dispatch, keeps the Worker
wrangler secret delete HELD_SHORT_GH_TOKEN --config wrangler.disabled.jsonc   # optional: remove its token copy
```

Cron changes can take up to 15 min to reach Cloudflare. A run that GitHub already accepted is not cancelled. Check
its log and the job's state before cancelling it. Never delete release state or jobs to stop this Worker. With the
Worker off, the GitHub cron (19:00 / 20:00 IST nominal) is the only trigger again.
