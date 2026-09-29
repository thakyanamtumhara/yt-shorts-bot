# Reviewed Instagram fallback · v1.0.0

Dedicated Cloudflare Cron Worker; fixed target is the existing `reviewed_ai_reels.yml` workflow in `thakyanamtumhara/yt-shorts-bot`, `main`, `mode=publish_due`. It never publishes to Meta or writes release state. No routes, workers.dev URL or preview URL are enabled, and its HTTP handler always returns404.

Every five minutes, at UTC minutes2/7/12/…/57, it reads the reviewed queue and independent approvals at one exact main commit. Only due IST jobs are considered. Public, cache-busted release receipts must match the exact job and selection hashes, account, native-AI request and publication mode. Verified media plus required verified comment are complete. Missing state is eligible; denied, malformed, mismatched or ambiguous state is blocked. A blocked job is logged; another valid due job can still invoke the existing publisher, which preserves per-job isolation and all its durable guards.

Before dispatch, any queued/in-progress/waiting run of this workflow postpones the fallback. The explicit `Reviewed Instagram · publish_due` run title supports a15-minute cooldown and at most three attempts per six-hour due window. Preflight success never proves delivery. GitHub success never overrides incomplete per-job receipts. If a workflow run list is too dense to inspect the current retry window safely, the trigger stops. Queue size is bounded to fit the Workers free-plan subrequest budget; reaching the limit requires review/archival, not silent skipping.

Concurrent Cron events or delayed GitHub run visibility may dispatch more than one workflow. This trigger does not claim an atomic lock. Existing GitHub concurrency and conditional S3 media/comment claims remain the publication safeguards. It preserves every original GitHub cron as an additional trigger.

## Verification before deployment

From this directory:

```sh
node --test worker.test.mjs
node read-only-check.mjs
wrangler deploy --dry-run --config wrangler.jsonc --outdir /private/tmp/reviewed-ig-trigger-disabled-build
wrangler deploy --dry-run --config wrangler.enabled.jsonc --outdir /private/tmp/reviewed-ig-trigger-enabled-build
```

The read-only check captures the existing `gh` credential in process memory and sets `DRY_RUN=true`; it cannot POST a dispatch. It prints identifier-only results. Never enable HTTP testing routes or print environment/credential values.

29-Sep-2026 evidence:22 tests passed, including exact canonical hash comparison against Python for all seven real queued jobs; both deploy dry-runs passed. Live read-only check at main `deac73545a6b7d16be10dbd31599f886e0e7ce82` returned four due/four verified, zero ready/blocked, `nothing_due`. No deployment or trigger submission was part of those checks.

## Deployment after review

1. Commit/push the Worker and one workflow run-name line together; preserve other agents' changes. Re-run the read-only check against current main.
2. Deploy the disabled Worker first with `wrangler deploy --config wrangler.jsonc`. Record the resulting deployment/version ID as the tested disabled rollback point. This config has `ENABLED=false`, `DRY_RUN=true`, `crons=[]`, no routes and no public endpoint.
3. Run `python3 secret-install.py`. It reads the existing `gh auth token` through captured stdout and pipes it to `wrangler secret put REVIEWED_IG_GH_TOKEN` through stdin. It suppresses both processes' raw output and prints only success/failure. Nothing is stored in repo, arguments, logs or a plaintext file. On29-Sep the existing GitHub CLI OAuth credential's reported scopes were `gist`, `read:org`, `repo`, `workflow`; this is broader than an ideal dedicated Actions-write/Contents-read token, but is already authorized for this owner's repository and is restricted here by fixed host/repo/mode and no public handler. A later scoped credential can replace the encrypted secret without code changes.
4. Check the secret name exists, without displaying its value. Enable via `wrangler deploy --config wrangler.enabled.jsonc` and record its version. Do not run `secret-install.py` against some other worker or account.
5. Use `wrangler tail --format json --config wrangler.enabled.jsonc` to observe a natural Cron event and identifier-only `reviewed_instagram_trigger` result. Empty/verified queue must log `nothing_due`; it must not generate a GitHub run. Recheck worker routes and cron configuration remotely. A natural tick verifies cloud execution, not a future Instagram publication.
6. Verify the next due approved job from its exact durable state plus actual Instagram media/first-comment/native-label readback. Do not claim scheduled posting worked until that evidence exists.

## Tested undo

```sh
wrangler deploy --config wrangler.jsonc
wrangler secret delete REVIEWED_IG_GH_TOKEN --config wrangler.jsonc
```

The first command restores the tested no-dispatch code path and removes only this Worker's Cron. The second removes only this Worker's copy of the credential; it does not revoke the owner's GitHub CLI login. Record/inspect deployment IDs and remote trigger state. Cron propagation can take up to15minutes; if urgent, remove the secret first and then restore disabled config. Already-running invocations may retain an earlier secret, and an already accepted GitHub run is not cancelled by this undo. Inspect that run and durable publication state before deciding on cancellation. Never delete or alter queue jobs, approvals, media/comment state, existing workflow schedules or any other Worker to disable this fallback.

Tests prove `ENABLED=false` does zero network even without a token, both configs expose zero routes, and undo config has no cron. Wrangler validates both bundles without deployment. Remote rollback/deployment verification must be recorded when actually performed.

## Observability and limits

Cloudflare retained logs report `disabled`, `nothing_due`, `blocked`, `publisher_active`, `cooldown`, `retry_limit`, `would_dispatch`, `dispatch_accepted`, or a safe error code. `dispatch_accepted` is not publication. Blocked/retry-limit records require investigation; this Worker sends no public or private messages and does not silently retry ambiguous posts. No external uptime alarm is included, so a completely absent Cron tick cannot report its own absence.

GitHub documents delayed or dropped scheduled events; Cloudflare Cron uses UTC and may take15minutes to propagate trigger changes. Neither provider guarantees exact-minute social publication. Sources: [GitHub schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [GitHub workflow dispatch](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event), [Cloudflare Cron](https://developers.cloudflare.com/workers/configuration/cron-triggers/), [Cloudflare secrets](https://developers.cloudflare.com/workers/configuration/secrets/).
