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

`ai_face: true` is persisted in the state and makes that asset **private-only**. Both a manifest publication date and a later `--schedule-at` action are refused. Public AI-face adoption requires separate review/work after you inspect the private sample; this tool contains no approval bypass. Ordinary filmed footage uses `ai_face: false`.

```sh
/usr/local/bin/python3 tools/upload_main.py --manifest /private/path/publish.json --state /private/path/upload-state.json --dry-run
/usr/local/bin/python3 tools/upload_main.py --manifest /private/path/publish.json --state /private/path/upload-state.json --execute
```

The dry run validates local video duration/dimensions, JPEG/PNG thumbnail, copy, schedule, source fingerprint, metadata hash and authenticated MAIN identity. `--offline` makes a dry run local-only; it cannot be combined with execution. Thumbnail presentation and rendered audiovisual quality still need separate viewing.

Keep state outside the public repository. State is atomically saved with mode 0600 and protected by a local process lock. Each upload begins **private with no schedule**. Its returned ID is saved before thumbnail or scheduling calls. Processing delays or follow-up failures can be retried with the same manifest/state, reusing that ID. A changed source, copy, thumbnail or date cannot silently reuse the state. If an upload request ends ambiguously before an ID is recorded, a second insert is blocked; inspect MAIN uploads before recovering the state. Do not delete the state merely to retry.

The uploader prepares a private-hold body in state before applying a schedule, preserves mutable status fields, and reads back the result. The hold operates only on this tool's recorded ID after checking MAIN ownership:

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
