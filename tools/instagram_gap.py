"""Keep posts on the Instagram account at least 2 h apart (owner rule 6-Oct-2026: never two at the same time).

publish_queue.yml asks before each due job. Its GitHub cron starts 3-9 h late, so a queued Reel could land minutes
after the daily bot's own Reel (posted right after its review, ~15:05 IST). A job that would land inside the gap, or
whose check cannot be read, stays queued for the next run.
"""

from datetime import datetime, timedelta, timezone

GAP = timedelta(hours=2)
IST = timezone(timedelta(hours=5, minutes=30))


class GapReadError(RuntimeError):
    pass


def parse_graph_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    for parse in (datetime.fromisoformat, lambda raw: datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S%z")):
        try:
            stamp = parse(text)
        except ValueError:
            continue
        if stamp.tzinfo is not None and stamp.utcoffset() is not None:
            return stamp.astimezone(timezone.utc)
    return None


def newest_post_time(get, account_id, token, version="v21.0", timeout=30):
    """Newest media time on the account (aware UTC), None when it has no media. Errors never carry the token."""
    try:
        response = get(f"https://graph.facebook.com/{version}/{account_id}/media",
                       params={"fields": "timestamp", "limit": 5, "access_token": token}, timeout=timeout)
    except Exception as error:
        raise GapReadError(f"request failed ({type(error).__name__})") from None
    try:
        body = response.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    if response.status_code != 200:
        message = str((body.get("error") or {}).get("message") or "")[:160]
        if token:
            message = message.replace(token, "***")
        raise GapReadError(f"HTTP {response.status_code} {message}".strip())
    media = [item for item in (body.get("data") or []) if isinstance(item, dict)]
    stamps = [stamp for stamp in (parse_graph_time(item.get("timestamp")) for item in media) if stamp]
    if media and not stamps:
        raise GapReadError("no readable media timestamp")
    return max(stamps) if stamps else None


def hold_reason(newest, now, gap=GAP):
    """None when a post may go out now, else why the job waits."""
    if newest is None or now - newest >= gap:
        return None
    minutes = max(0, int((now - newest).total_seconds() // 60))
    return (f"newest Instagram post went out {minutes} min ago ({newest.astimezone(IST):%d-%b %H:%M} IST); "
            f"posts stay {gap.total_seconds() / 3600:g} h apart, so this job waits for the next run")


class PostGap:
    """One queue run: reads the newest post time once (only when a job is due) and counts this run's own posts."""

    def __init__(self, read_newest, gap=GAP):
        self._read_newest = read_newest
        self.gap = gap
        self._loaded = False
        self._newest = None
        self._error = None

    def hold(self, now):
        if not self._loaded:
            self._loaded = True
            try:
                self._newest = self._read_newest()
            except Exception as error:
                self._error = str(error) if isinstance(error, GapReadError) else type(error).__name__
        if self._error:
            return f"could not read the newest Instagram post time ({self._error}); trying again next run"
        return hold_reason(self._newest, now, self.gap)

    def posted(self, when):
        self._loaded, self._error = True, None
        self._newest = when if self._newest is None else max(self._newest, when)
