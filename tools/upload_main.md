# MAIN YouTube uploader

`upload_main.py` uploads one reviewed asset to MAIN `UCdgOMA7WO48MYimj6q6mvNQ`. It never uses the BOT token or Vizard. Default behavior is a dry run, including a read-only channel check. Add `--execute` only when performing the intended upload/status change.

Requires `ffprobe`, `google-auth` and `google-api-python-client`. On this Mac, `/usr/local/bin/python3` has the Google libraries; the default Homebrew Python currently does not. Credentials are read only from `~/.yt_main_token.json` and refreshed in memory. There is no token argument and no credential output.

Create a separate JSON publishing manifest per finished video; paths resolve beside that manifest:

```json
{
  "video_path": "warehouse-final.mp4",
  "thumbnail_path": "warehouse-youtube.jpg",
  "title": "Plain T-Shirt Warehouse Walkthrough | Delhi Wholesale",
  "description_path": "youtube-description.txt",
  "tags": ["plain t shirt wholesale", "Delhi warehouse"],
  "language": "hi",
  "category_id": "22",
  "contains_synthetic_media": false,
  "ai_music": false,
  "ai_face": false,
  "ai_voice": false
}
```

An inline `description` can replace `description_path`. Add an explicit future ISO `publish_at`, such as `2026-09-19T19:00:00+05:30`, to schedule after upload/processing/thumbnail completion. Omitting it leaves the upload private. Past or timezone-free dates are rejected. Dates in this example are illustrative; recheck the current queue.

Set `contains_synthetic_media: true` for AI material requiring disclosure. This uploader conservatively defaults the native `containsSyntheticMedia` flag to true whenever `ai_music`, `ai_face` or `ai_voice` is set; explicitly contradicting that with false is rejected. This is a tool choice, not a claim that every synthetic voice requires a label: YouTube exempts cloning your own voice for voiceovers/dubs, while other changes such as realistic face animation or AI music can separately require disclosure. Normal real voice without AI additions defaults to false. These declarations come from the publishing package, not a guess from the filename. [YouTube disclosure guidance](https://support.google.com/youtube/answer/14328491?hl=en)

`ai_face: true` is persisted in the state and keeps the asset **private-only until an exact user selection is recorded**. Scheduling requires `user_selection` containing `batch_id`, `video_id`, `version`, `sha256`, `approval_method: "user_exact_selection"`, and `approved_at`. Its source hash must match this exact rendered file; machine review cannot authorize release. Ordinary filmed footage uses `ai_face: false`.

```sh
/usr/local/bin/python3 tools/upload_main.py --manifest /private/path/publish.json --state /private/path/upload-state.json --dry-run
/usr/local/bin/python3 tools/upload_main.py --manifest /private/path/publish.json --state /private/path/upload-state.json --execute
```

The dry run validates local video duration/dimensions, JPEG/PNG thumbnail, copy, schedule, source fingerprint, metadata hash and authenticated MAIN identity. `--offline` makes a dry run local-only; it cannot be combined with execution. Thumbnail presentation and rendered audiovisual quality still need separate viewing.

Keep state outside the public repository. State is atomically saved with mode 0600 and protected by a local process lock. Each upload begins **private with no schedule**. Its returned ID is saved before thumbnail or scheduling calls. Processing delays or follow-up failures can be retried with the same manifest/state, reusing that ID. A changed source, copy, thumbnail or date cannot silently reuse the state. If an upload request ends ambiguously before an ID is recorded, a second insert is blocked; inspect MAIN uploads before recovering the state. Do not delete the state merely to retry.

Use `--resume-only` whenever finishing an existing upload. It requires `--manifest`, an existing state with a confirmed uploaded ID, and matching manifest/source fingerprints before constructing the API client or making any mutation. A missing or mistyped state path, missing ID or uncertain upload phase stops; it cannot start a new upload. The flag applies to dry runs and execution. Normal commands without this flag retain the new-upload path.

```sh
/usr/local/bin/python3 tools/upload_main.py --manifest /private/path/publish.json --state /private/path/upload-state.json --resume-only --execute
```

The uploader prepares a private-hold body in state before applying a schedule, preserves mutable status fields, and reads back the result. YouTube can briefly return stale metadata/status after a successful write. Copy verification after processing and schedule/hold readback allow up to 30 seconds of read-only convergence, checking every 2 seconds. These polls never repeat an upload, thumbnail or status write; a persistent mismatch still stops with the uploaded ID and undo state saved. Ownership failures stop immediately. The hold operates only on this tool's recorded ID after checking MAIN ownership:

For native AI disclosure, an explicit `containsSyntheticMedia: true` from the owner GET is accepted and explicit false blocks an AI release. If GET omits the field, the uploader may use a saved **official `videos.update` response** that identifies the exact owned video and explicitly returns `status.containsSyntheticMedia: true`. The response is bound to the source and metadata hashes in `native_ai_writes`; a caption or the outgoing request alone never counts. This records API acknowledgment, not a claim about a visible Studio label. The official API documents the field and the status update route. [Video status field](https://developers.google.com/youtube/v3/docs/videos#status.containsSyntheticMedia), [videos.update](https://developers.google.com/youtube/v3/docs/videos/update).

For an already uploaded private video whose GET omits the field, the same manifest/state resumes its saved ID. Before thumbnail/scheduling, the tool saves intent and private-hold undo, makes at most one status-only disclosure confirmation for that exact metadata, and saves the response before any next action. It preserves other supported mutable status fields and an existing future schedule. Scheduling explicitly sends the native flag again and, when GET still omits it, requires that schedule write's own true acknowledgment. An earlier confirmation cannot mask a later missing acknowledgment. Follow-up GET still verifies MAIN ownership, title, description, tags, privacy and the planned date.

If a native status request times out or returns no usable acknowledgment while GET still omits the field, automatic retries stop; the existing ID and intent remain saved. Do not delete the state or fabricate a receipt to retry. A private hold remains available to remove a schedule. An explicit later scheduling action after a hold starts a new status action on the same ID; ordinary retries do not recreate canceled schedules or repeat acknowledged writes.

```sh
/usr/local/bin/python3 tools/upload_main.py --hold-private --state /private/path/upload-state.json --dry-run
/usr/local/bin/python3 tools/upload_main.py --hold-private --state /private/path/upload-state.json --execute
```

A hold removes `publishAt`, leaves the video private and blocks automatic scheduling from an old manifest. Removing a previously confirmed schedule in Studio is also respected: an ordinary retry refuses to recreate it. An interrupted first scheduling request can still be retried using its existing ID. To deliberately schedule that same uploaded asset after a hold/private preview or Studio cancellation:

```sh
/usr/local/bin/python3 tools/upload_main.py --schedule-at '2026-09-19T19:00:00+05:30' --state /private/path/upload-state.json --dry-run
/usr/local/bin/python3 tools/upload_main.py --schedule-at '2026-09-19T19:00:00+05:30' --state /private/path/upload-state.json --execute
```

That explicit action updates the date and metadata hash in state. If later resuming via a manifest, its date must match. It requires completed processing and thumbnail upload. It does not change the source or copy. No delete operation or arbitrary `--video-id` operation exists.

Safeguards, including schedule → hold → readback, are tested with a fake API; the tests do not mutate any real video:

```sh
python3 -m unittest discover -s tests -p 'test_upload_main.py' -v
```

Scheduling and holding use the official [`videos.update` status semantics](https://developers.google.com/youtube/v3/docs/videos/update). Native disclosure uses `status.containsSyntheticMedia`. Live rollback testing on a newly uploaded private asset is separate from these local safeguards; no old women's/catalogue ID is part of this tool's test.
