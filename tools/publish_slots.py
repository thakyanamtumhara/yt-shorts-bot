"""Clash-aware YouTube publish time for the daily Short.

Owner rule 6-Oct-2026: never two videos within 2 h of each other; other videos can also be scheduled. Per day from
today: the weekday table's slot, then that slot + 2 h. The first candidate at least 20 min ahead and under 2 h from
no taken time wins, up to 7 days ahead. Taken = this channel's scheduled videos and the ones public in the last 2 days
(the rule is per account: MAIN-channel bookings do not move the bot channel's Short). ledger_taken() lists the YouTube
bookings in crosspost_ledger.json, for checking a manual MAIN release before booking it.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

IST = timezone(timedelta(hours=5, minutes=30))
GAP = timedelta(hours=2)
LEAD = timedelta(minutes=20)
LATER = timedelta(hours=2)
DAYS_AHEAD = 7
RECENT = timedelta(days=2)
NEWEST_UPLOADS = 25
LEDGER = Path(__file__).resolve().parents[1] / "crosspost_ledger.json"


class Taken(NamedTuple):
    at: datetime
    source: str


def parse_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        try:
            stamp = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return stamp


def when(stamp):
    return stamp.astimezone(IST).strftime("%a %d-%b %H:%M")


def to_minute(stamp):
    # A Short scheduled for 19:00 goes public at 19:00:05-19:00:45; 21:00 must still count as 2 h after it.
    return stamp.replace(second=0, microsecond=0)


def candidates(now, slot_for_day, days_ahead=DAYS_AHEAD):
    today = now.astimezone(IST).date()
    for offset in range(days_ahead + 1):
        day = today + timedelta(days=offset)
        hour, minute = slot_for_day(day)[:2]
        first = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
        yield first
        yield first + LATER


def pick_publish_time(now, slot_for_day, taken, *, gap=GAP, lead=LEAD, days_ahead=DAYS_AHEAD):
    """(publish_at in IST, notes on skipped candidates); publish_at is None when nothing is free in range."""
    now = now.astimezone(IST)
    taken = sorted(taken)
    skipped = []
    for candidate in candidates(now, slot_for_day, days_ahead):
        if candidate - now < lead:
            why = "passed" if candidate <= now else f"under {int(lead.total_seconds() // 60)} min away"
            skipped.append(f"{when(candidate)} {why}")
            continue
        clash = next((item for item in taken if abs(candidate - item.at) < gap), None)
        if clash:
            skipped.append(f"{when(candidate)} taken by {clash.source}")
            continue
        return candidate, skipped
    return None, skipped


def table_publish_time(now, slot_for_day):
    """The rule before 6-Oct-2026, kept for when the channel cannot be read: today's slot, else tomorrow's."""
    now = now.astimezone(IST)
    for offset in (0, 1):
        day = now.date() + timedelta(days=offset)
        hour, minute = slot_for_day(day)[:2]
        slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
        if offset or now < slot:
            return slot


def videos_taken(videos, now, *, recent=RECENT):
    taken = []
    for video in videos:
        video_id = video.get("id") or "?"
        status = video.get("status") or {}
        snippet = video.get("snippet") or {}
        live = video.get("liveStreamingDetails") or {}
        privacy = status.get("privacyStatus")
        if privacy == "private" and status.get("publishAt"):
            stamp = parse_time(status["publishAt"])
            if stamp:
                taken.append(Taken(to_minute(stamp), f"{video_id} (scheduled {when(stamp)})"))
        elif privacy == "public":
            stamp = parse_time(snippet.get("publishedAt"))
            if stamp and now - stamp <= recent:
                taken.append(Taken(to_minute(stamp), f"{video_id} (public {when(stamp)})"))
        if snippet.get("liveBroadcastContent") == "upcoming":
            stamp = parse_time(live.get("scheduledStartTime"))
            if stamp:
                taken.append(Taken(to_minute(stamp), f"{video_id} (premiere/live {when(stamp)})"))
    return taken


def channel_taken(youtube, now, *, recent=RECENT, newest=NEWEST_UPLOADS):
    """Taken times on the token's own channel, from its newest uploads. Raises on any API failure."""
    channels = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = channels.get("items") or []
    if not items:
        raise LookupError("the YouTube token has no channel")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    listing = youtube.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=newest).execute()
    ids = [item["contentDetails"]["videoId"] for item in listing.get("items", [])]
    if not ids:
        return []
    videos = youtube.videos().list(part="status,snippet,liveStreamingDetails", id=",".join(ids)).execute()
    return videos_taken(videos.get("items", []), now, recent=recent)


def ledger_taken(now, path=None, *, gap=GAP):
    """YouTube bookings in the crosspost ledger that can still clash (due later than now - 2 h)."""
    try:
        with open(path or LEDGER, encoding="utf-8") as handle:
            ledger = json.load(handle)
    except FileNotFoundError:
        return []
    entries = ledger.get("entries", []) if isinstance(ledger, dict) else ledger
    taken = []
    for entry in entries or []:
        if not isinstance(entry, dict) or not str(entry.get("platform", "")).lower().startswith("youtube"):
            continue
        try:
            due = datetime.strptime(str(entry.get("due_ist", "")).strip(), "%Y-%m-%d %H:%M").replace(tzinfo=IST)
        except ValueError:
            continue
        if due <= now - gap:
            continue
        name = entry.get("id") or "?"
        label = entry.get("video_id") or name
        taken.append(Taken(due, f"{label} (ledger {name}, {when(due)})" if label != name
                           else f"ledger {name} ({when(due)})"))
    return taken
