# Finished-clip Vizard batch

One manifest schedules one finished real clip to the existing Facebook Page, X account and personal LinkedIn account from `vizard_publish.py`. It does not publish to YouTube or Instagram.

Keep the manifest and state outside this public repository:

```json
{
  "name": "Warehouse sample-first clip",
  "local_video_path": "/absolute/path/clip03.mp4",
  "video_url": "https://www.bulkplaintshirt.com/p/clip03.mp4",
  "legs": {
    "fb": {"post": "Facebook copy", "when_ist": "2026-09-20 20:00"},
    "x": {"post": "X copy", "when_ist": "2026-09-20 20:30"},
    "li": {"post": "LinkedIn copy", "when_ist": "2026-09-22 08:15"}
  }
}
```

```sh
python3 tools/vizard_batch.py --manifest /private/path/clip03.json --state /private/path/clip03-state.json
python3 tools/vizard_batch.py --manifest /private/path/clip03.json --state /private/path/clip03-state.json --execute
```

Dry-run checks all three captions and IST dates, the local video, and a bounded HTTP GET with `Range: bytes=0-31` on the owned HTTPS MP4 under `bulkplaintshirt.com/p/`. The returned total byte length must match the local file; MIME and the MP4 `ftyp` signature must also match. If the server ignores Range, only 32 bytes are read before closing. A redirect must still end at an owned asset URL. Local SHA-256, URL, copy, dates and fixed social account IDs bind the state to this manifest. Duration must be at least 3 seconds and strictly below 140 seconds; maximum size is 512 MB. This is a byte-length check of the remote asset, not a remote SHA-256 comparison.

Execution uses the existing Vizard key, `getClips=0`, and all six enhancement switches off. It saves the project ID before polling, the single finished video ID before scheduling, and each complete scheduling response before the next leg. Pending times must still be in the future after processing. A 20-minute polling timeout preserves the project and can be resumed with the same command. Use the **same state path** for every rerun; acknowledged legs are skipped, including after their scheduled time. A lock prevents simultaneous use of the same state.

If create or publish returns an error or loses its response, automatic retry stops. Inspect the private state and Vizard UI before any manual recovery. Do not delete state, invent a new state path or resend a leg to resolve an uncertain outcome. A success response means Vizard accepted the schedule; it is not proof of eventual publication. Check the connected accounts remain active through the scheduled dates before executing a batch. Vizard create has a 3/minute, 20/hour limit; space multiple clips accordingly.

Undo is manual. Current official instructions: **Calendar → click scheduled post → Delete** in month/week view, or **More → Delete** in feed view. Cancel each platform's matching pending post and verify it disappeared. The older Content → Scheduled path was tested in this account on 7-Aug-2026; the current help page names Calendar. The public API reference lists no cancellation or scheduled-post status endpoint.

Sources checked 9-Sep-2026:

- [Calendar edit/delete instructions](https://help.vizard.ai/en/articles/10441547-how-to-schedule-using-the-calendar).
- [Vizard API endpoints and response codes](https://github.com/vizardai/vizard-api-skills/blob/main/api-reference.md).
- [Vizard posting limits](https://help.vizard.ai/en/articles/14631679-posting-limits-best-practices): Facebook 2,200, X 280, LinkedIn 3,000 characters. LinkedIn API copy excludes backslash and parentheses. X counting also applies a conservative weighted check with 23-character links; complex emoji can be overcounted, so leave space.
- [X text weighting configuration](https://github.com/twitter/twitter-text/blob/master/config/v3.json).

Local tests only: `python3 -m unittest discover -s tests -p 'test_vizard_batch.py' -v`.
