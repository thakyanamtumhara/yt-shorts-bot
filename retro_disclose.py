#!/usr/bin/env python3
"""Backfill status.containsSyntheticMedia=true on already-published channel videos.

Why: every bot short is realistic Veo footage + cloned voice — YouTube's A/S
disclosure policy requires the flag, and daily_short.py only sets it on NEW
uploads (since 2026-07-06). This flags the back-catalog.

Safe-update pattern (verified against API docs 2026-07-06):
- videos.update REPLACES the status part — any mutable field omitted is CLEARED
  (a bare update would un-schedule scheduled videos and wipe privacy). So we
  read the full status via videos.list and write it back with only
  containsSyntheticMedia added, then verify the write round-trips.
- Skips videos already flagged, "Warehouse Live" archives and anything with
  liveStreamingDetails (real footage, not synthetic).
- Quota: videos.update = 50 units from the shared 10,000/day bucket, so
  MAX_UPDATES caps a run (100 = 5,000 units). Idempotent — re-run daily until
  "pending: 0". Halts cleanly on quotaExceeded (resets midnight PT).
"""
import os
import sys
import time

MAX_UPDATES = int(os.environ.get("MAX_UPDATES") or "120")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
TOKEN_FILE = os.environ.get("TOKEN_FILE", "/tmp/yt_shorts/youtube_token.json")

# status fields that are mutable via videos.update — all must be re-sent or
# the API clears them
MUTABLE_STATUS_FIELDS = (
    "privacyStatus", "publishAt", "license", "embeddable",
    "publicStatsViewable", "selfDeclaredMadeForKids",
)


def get_service():
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def main():
    yt = get_service()

    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    uploads_playlist = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    video_ids = []
    page_token = None
    while True:
        resp = yt.playlistItems().list(
            part="contentDetails", playlistId=uploads_playlist,
            maxResults=50, pageToken=page_token,
        ).execute()
        video_ids += [i["contentDetails"]["videoId"] for i in resp.get("items", [])]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    print(f"📼 Channel uploads found: {len(video_ids)}")

    updated, already_flagged, skipped_real, pending, errors = 0, 0, 0, 0, 0

    for chunk_start in range(0, len(video_ids), 50):
        chunk = video_ids[chunk_start:chunk_start + 50]
        resp = yt.videos().list(
            part="status,snippet,liveStreamingDetails", id=",".join(chunk)
        ).execute()

        for v in resp.get("items", []):
            vid = v["id"]
            title = v.get("snippet", {}).get("title", "?")
            status = v.get("status", {})

            if v.get("liveStreamingDetails") or "Warehouse Live" in title:
                skipped_real += 1
                print(f"  ⏭️ real footage (not flagged): {title[:60]}")
                continue
            if status.get("containsSyntheticMedia") is True:
                already_flagged += 1
                continue
            if updated >= MAX_UPDATES:
                pending += 1
                continue

            new_status = {k: status[k] for k in MUTABLE_STATUS_FIELDS if k in status}
            new_status["containsSyntheticMedia"] = True

            # a scheduled video must keep privacyStatus=private together with
            # publishAt — anything inconsistent is left alone rather than risked
            if "publishAt" in new_status and new_status.get("privacyStatus") != "private":
                print(f"  ⚠️ {vid}: publishAt present but privacy={new_status.get('privacyStatus')} — skipped")
                errors += 1
                continue

            print(f"  {'[DRY] ' if DRY_RUN else ''}🏷️ flagging: {title[:60]}")
            if not DRY_RUN:
                try:
                    wr = yt.videos().update(
                        part="status", body={"id": vid, "status": new_status}
                    ).execute()
                    ws = wr.get("status", {})
                    if ws.get("containsSyntheticMedia") is not True:
                        print(f"  ❌ {vid}: flag did not round-trip — HALTING")
                        sys.exit(1)
                    if "publishAt" in new_status and not ws.get("publishAt"):
                        print(f"  ❌ {vid}: publishAt was lost — HALTING (fix by re-scheduling in Studio)")
                        sys.exit(1)
                except Exception as e:
                    if "quotaExceeded" in str(e):
                        print("  🛑 quotaExceeded — stopping; re-run after midnight PT (idempotent)")
                        report(updated, already_flagged, skipped_real, pending, errors, halted=True)
                        sys.exit(0)
                    print(f"  ⚠️ {vid}: {e} — continuing")
                    errors += 1
                    continue
                time.sleep(1)
            updated += 1

    report(updated, already_flagged, skipped_real, pending, errors)


def report(updated, already_flagged, skipped_real, pending, errors, halted=False):
    print("\n──── retro-disclose summary ────")
    print(f"  flagged this run : {updated}{' (dry run — nothing written)' if DRY_RUN else ''}")
    print(f"  already flagged  : {already_flagged}")
    print(f"  real footage skip: {skipped_real}")
    print(f"  still pending    : {pending}{' (+unknown — halted early)' if halted else ''}")
    print(f"  errors/skipped   : {errors}")
    if pending or halted:
        print("  ➡️ re-run this workflow to continue (safe to repeat)")
    else:
        print("  ✅ back-catalog fully disclosed")


if __name__ == "__main__":
    main()
