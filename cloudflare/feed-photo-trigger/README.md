# Feed photo trigger (Cloudflare Worker)

Ketu's rules: booked times are kept, cloud first, every automatic job says when it breaks. A reviewed Instagram
photo (`feed_queue/<id>.json`, `publish_at` in IST) is posted by `ig_carousel.yml` → `tools/curated_feed.py`, but
until 8-Nov-2026 only that workflow's GitHub cron started it, and GitHub ran it 6-8 h late (the 13:00 IST slot ran at
19:45-21:11 IST on 2-7 Nov-2026; the Sep photos went out 4 h 51 m and 5 h 08 m late). No other Worker dispatches it:
`reviewed-instagram-trigger` is pinned to `reviewed_ai_reels.yml` on purpose.

This Worker runs every 5 min (UTC minutes 1, 6, ..., 56). It lists the top-level `feed_queue/*.json` on `main` (never
`state/` or any subfolder) and reads each job. A job is due from `publish_at` + 30 s to `publish_at` + 3 h 30 min.
For a due job it reads the publisher's public state
(`https://www.bulkplaintshirt.com/p/automation-state-ig-curated/<id>.json`): a `media_id` = posted (done), a
`pending` POST = uncertain (never dispatched: a person must look), 404 or a record without either = open. An open
job makes it dispatch `ig_carousel.yml` on `main` with no inputs (`verify_only` stays false). Guards: no dispatch
while a run of that workflow is queued or running, none after a run created since the due time finished with
success (it posted, or the publisher's one-photo-per-IST-day guard held it), a 20-minute cooldown after a dispatch,
at most 3 dispatches per due window, and nothing when the state cannot be read. Each tick makes at most
1 + jobs + due jobs + 2 GitHub/state requests (20 jobs at most).

It never posts and never writes state. `tools/curated_feed.py` keeps the guards against a second post (S3 state
with conditional writes, a media id or an uncertain POST is never repeated, one curated post per IST day), and the
workflow's concurrency group runs one publish at a time. GitHub's own cron stays as the backup. No routes, no
workers.dev URL; the HTTP handler returns 404. Logs: one `feed_photo_trigger` JSON line per tick, state one of
`disabled`, `nothing_due`, `done`, `uncertain`, `state_unknown`, `publisher_active`, `handled_by_run`, `retry_limit`,
`cooldown`, `would_dispatch`, `dispatch_accepted`, or `error` with a code.

If the Worker ever stops, the photo still goes out with the late GitHub cron, and the workflow step "Tell Ketu when a
reviewed photo went out late" (`tools/curated_feed_alert.py`) Telegrams Ketu when a photo was published more than
30 min after its time. A failed run is reported by the workflow's own failure step (GitHub issue + Telegram).

Known edge: a `verify_only` run started by hand after a photo's time, while that photo is still open, reads as
`handled_by_run`; the photo then waits for the GitHub cron.

## Deploy

```sh
node --test worker.test.mjs
node read-only-check.mjs                            # DRY_RUN on live data; optional ISO time, e.g. 2026-11-10T13:01:00+05:30
wrangler deploy --config wrangler.disabled.jsonc    # disabled, no cron = the undo, deployed first
python3 secret-install.py                           # gh token of thakyanamtumhara -> FEED_PHOTO_GH_TOKEN
wrangler deploy --config wrangler.jsonc --var DRY_RUN:true   # one cloud tick that can never dispatch
wrangler deploy --config wrangler.jsonc             # enabled + cron
```

Deployed 8-Nov-2026 (code d1bef9a):
- `e850e0aa-c264-41f3-907e-fa15f3b3e573`: disabled, no cron (the undo), deployed first at 12:25 IST.
- `FEED_PHOTO_GH_TOKEN` stored with `secret-install.py`.
- `d8191ef5-f855-4984-a7fb-d25fbfd34bac`: enabled + cron with `DRY_RUN:true`. Cloud tick 12:36:40 IST: `nothing_due`,
  queued 4, so the token reads the queue from Cloudflare.
- `cbf88155-aba6-4b0e-b3ec-44bb3e63e88d`: enabled + cron, live from 12:37 IST. Tick 12:41:40 IST: `nothing_due`, queued 4.
- The read-only check on live data said `would_dispatch kidshood20261107-igpost` at Tue 10-Nov 13:01 IST and
  `nothing_due` at 12:59 and 16:35 IST. That photo is the first live dispatch.

Watch a tick: `wrangler tail feed-photo-trigger --format json`. If `gh auth login` is redone for thakyanamtumhara,
the old token stops working: run `python3 secret-install.py` again (same for the other three trigger Workers).

## Undo

```sh
wrangler deploy --config wrangler.disabled.jsonc    # stops the cron and every dispatch, keeps the Worker
wrangler secret delete FEED_PHOTO_GH_TOKEN --config wrangler.disabled.jsonc   # optional: remove its token copy
```

Cron changes can take up to 15 min to reach Cloudflare. A run GitHub already accepted is not cancelled; check its
log and the photo's state before cancelling it. Never delete feed state or jobs to stop this Worker.
