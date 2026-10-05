# Daily Short trigger (Cloudflare Worker)

Ketu, 5-Oct-2026: cloud first, the Mac only as a backup. GitHub's own cron starts `daily_short.yml` 3-11 hours late,
so this Worker starts it at 14:30 IST (cron `0 9 * * *` UTC, re-check `20 9 * * *`) with `owner_review=true`.
It skips Sundays and any day that already has a non-cancelled, non-test run (never a second run). The held Short
then dispatches `ai_final_review.yml`, where Claude Opus 5.5 decides; the Mac job (`com.ketu.dailyshortreview`,
15:15 IST) only starts a missing run or reviews when no cloud decision appears within 25 minutes.

No routes, no workers.dev URL; the HTTP handler returns 404. Logs: one `daily_short_trigger` JSON line per tick
(`disabled`, `sunday`, `already_started`, `would_dispatch`, `dispatch_accepted` or `error` with a code).

## Deploy (as done 5-Oct-2026)

```sh
node --test worker.test.mjs
npx wrangler deploy --config wrangler.disabled.jsonc   # disabled, no cron  (version 95d362f7)
python3 secret-install.py                             # gh token of thakyanamtumhara -> DAILY_SHORT_GH_TOKEN
npx wrangler deploy --config wrangler.jsonc            # enabled + crons    (version fb1ed7ca)
```

If `gh auth login` is redone for thakyanamtumhara, the old token stops working: run `python3 secret-install.py` again.
Watch a tick: `npx wrangler tail --config wrangler.jsonc --format json`.

## Undo

```sh
npx wrangler deploy --config wrangler.disabled.jsonc   # stops the cron, keeps the Worker
npx wrangler secret delete DAILY_SHORT_GH_TOKEN --config wrangler.jsonc
```

Then move the Mac job back to 14:30 (`tools/daily_review_agent/com.ketu.dailyshortreview.plist`, reload with
`launchctl bootout gui/$(id -u)/com.ketu.dailyshortreview; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ketu.dailyshortreview.plist`).
