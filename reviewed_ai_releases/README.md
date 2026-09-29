# Exact reviewed Reel releases

`tools/reviewed_ai_reels.py` is separate from the daily bot. It does not rewrite copy or remove native AI disclosure. The scheduled runner checks due jobs at 12:45, 13:15, 13:45, 14:15, 15:00, 16:30, 17:00 and 18:00 IST; GitHub scheduling can be delayed. These are eligibility/catch-up checks, not guaranteed publication times. Each result reports due-time lateness. Every Graph POST has a durable conditional claim first. An uncertain request stays blocked for manual reconciliation, including when a local or network write fails after acceptance. Per-job failures are isolated: other independently approved jobs still run, while the final nonzero result reports every blocked job.

The release owner supplies `reviewed_ai_releases/approvals.json`, stripped from the user's exact selection receipt:

```json
{"batch_id":"warehouse-20260911","approval_method":"user_exact_selection","approved_at":"2026-09-11T04:08:05.404382+00:00","videos":[{"id":"WH09","version":"1.0","sha256":"<64 lowercase hex>","public_release_approved":true}]}
```

Additional batches use independent `reviewed_ai_releases/batches/<batch>.json` receipts. A video may optionally declare `media_kind`: `original_recording`, `cloned_voice_screen_recording`, or `real_footage_ai` (the unchanged legacy default). The selected receipt must approve the same kind as well as the exact ID/version/hash. Originals require `is_ai_generated=false`; both AI kinds require true. The screen-recording kind requires `AI voice: my own cloned voice.` and rejects the filmed-footage disclosure.

Each `reviewed_ai_reels/<job-id>.json` uses the required fields below, plus optional `media_kind` and `first_comment`:

```json
{
  "id": "warehouse-wh09-v1-0",
  "status": "reviewed",
  "account_id": "17841407981790313",
  "publish_at": "2026-09-23T15:00:00+05:30",
  "caption": "Reviewed post copy.\n\nAI-assisted dialogue using my own footage and voice. Background music created with AI.",
  "video_url": "https://YOUR-PUBLIC-HOST/exact-final.mp4",
  "video_sha256": "<64 lowercase hex>",
  "cover_url": "https://YOUR-PUBLIC-HOST/approved-cover.jpg",
  "cover_sha256": "<64 lowercase hex>",
  "is_ai_generated": true,
  "user_selection": {"batch_id":"warehouse-20260911","id":"WH09","version":"1.0","sha256":"<same exact video hash>","approved":true}
}
```

No sample JSON is executable or preapproved in this directory. Keep local paths, private provider data and credentials out of public receipts. Public video URLs should use immutable content-addressed object names. Hashes are downloaded and checked again immediately before creating a container. The platform subsequently fetches those URLs, so object immutability must be maintained by the release owner.

Run a single read-only preflight with `python3 tools/reviewed_ai_reels.py --job reviewed_ai_reels/JOB.json`. Full-queue preflight (the default) also validates remote state, account and all assets. `REVIEWED_AI_STATE_BUCKET=bulkplaintshirt.com` is required for the whole queue. The hardcoded S3 prefix is `p/automation-state-reviewed-ai-reels`, fitting the existing GitHub IAM p/* access. Its key is the full video SHA256, preventing the same bytes from being re-released under a renamed job. Only identifiers, hashes, boolean disclosure evidence and times are stored there.

Unpublished prepare test:

```sh
python3 tools/reviewed_ai_reels.py --job reviewed_ai_reels/JOB.json --prepare-only --execute --state /private/tmp/reviewed-ai-prepare/JOB.json
```

This creates and polls one unpublished Reel container with the exact approved native AI boolean, preserves its accepted ID, and never calls media_publish. Reuse the same local state to avoid duplicate preparation. Cloud preparation adds `--durable-prepare`, retaining its content-hash claim under the separate `p/automation-state-reviewed-ai-reels/prepare/` namespace so a new runner directory cannot duplicate a container. That namespace only accepts prepare-only state and can never authorize publication. An expired prepared container requires explicit reconciliation, not automatic recreation. A FINISHED container plus an accepted flagged request proves API preparation, not a visible label. Instagram containers expire; prepare-only state is intentionally never promoted into the future publishing queue.

`--execute` on the whole queue requires durable S3 state and refuses future jobs. For AI jobs, before any public POST it rechecks the known owned AI-labeled media 18091355006159379, requiring true native AI disclosure and the correct owner. A missing field or changed route blocks before publication. It publishes only due selected versions, then reads back the exact approved native AI boolean, identity and exact caption from the media ID. If the readback fails, its ID remains saved and a rerun only rechecks it; it never republishes. Unknown/omitted disclosure fails verification; false also fails AI jobs. Native flag rejection never triggers a different flag. Approved first comments use the same conditional intent/ID recovery and exact text readback; an ambiguous comment POST blocks retry without duplicating media or comments.

Undo before any release: remove the specific queued job (or move its due time before it has a saved claim) and commit that change. Do not erase S3 claims or edit media/hash/copy after an attempted release; reconcile any pending POST using the retained IDs first. A code rollback cannot recall already published media. Public release rollback/removal must target the saved media ID explicitly.

29-Sep-2026: `raw0004-20260929` selects only full-v1 and short02-v1; repetitive short01-v1 is excluded. `prebook-20260928` is the independently approved 19.1s website-screen-recording Reel, preserved without a new baked cover. Evidence and tested undo: `release-audits/raw0004-20260929.md`.
