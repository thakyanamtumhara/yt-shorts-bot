#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Social post watcher — did the posts actually go out, and are they still up?

Ketu posts the weekly Factory Sach original BY HAND through a multi-platform
wizard (Facebook, X, LinkedIn, main YouTube). The bot posts the daily Short
itself. Either way a post can fail silently: a wizard schedule that never fired,
a Facebook reel stuck as a draft, a video pulled by a copyright claim. Nobody
finds out until someone happens to look.

This checks what is checkable for free and pings Telegram when something is
wrong. Deliberately conservative — a false "your post was deleted" alarm at 6am
is worse than a missed one, so every deletion check is gated on the token being
healthy first (Meta returns the same error for "post gone" and "your permission
broke").

Covers today:
  • YouTube MAIN channel  — public RSS feed, no auth, no quota
  • Instagram             — Graph API with the token we already own
  • Facebook Page reels   — status of reels the bot published

Not covered (see the 2026-08-01 research): LinkedIn personal (impossible),
LinkedIn company (needs 1-3 months of approval), X (needs a paid tier).

Usage:  python3 social_watch.py [--dry-run]
"""
import json, os, re, sys, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
STATE_FILE = "social_watch_state.json"

# main channel — originals, posted by hand. NOT the bot's @sale91_com channel.
YT_MAIN_ID = "UCdgOMA7WO48MYimj6q6mvNQ"      # @BulkPlainTshirt_com
YT_MAIN_RSS = f"https://www.youtube.com/feeds/videos.xml?channel_id={YT_MAIN_ID}"

IG_BIZ = (os.environ.get("INSTAGRAM_BUSINESS_ID") or "").strip()
IG_TOKEN = (os.environ.get("INSTAGRAM_ACCESS_TOKEN") or "").strip()
FB_PAGE_ID = (os.environ.get("FB_PAGE_ID") or "1999594936994410").strip()
FB_TOKEN = (os.environ.get("FB_PAGE_ACCESS_TOKEN") or IG_TOKEN).strip()
GRAPH = "https://graph.facebook.com/v21.0"

# a Factory Sach episode should land at least this often on the main channel
FACTORY_SACH_MAX_GAP_DAYS = 10
FACTORY_SACH_PAT = re.compile(r"factory\s*sach|फैक्ट्री\s*सच", re.I)


def now_ist():
    return datetime.now(IST)


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "sale91-social-watch/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def get_json(url, timeout=30):
    return json.loads(get(url, timeout))


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception as e:
            print(f"⚠️ state file unreadable ({e}) — starting fresh")
    return {"youtube_main": {}, "instagram": {}, "facebook": {}, "history": []}


def save_state(st):
    st["last_run_ist"] = now_ist().isoformat(timespec="seconds")
    json.dump(st, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ── platform checks ───────────────────────────────────────────────────────────

def check_youtube_main(state):
    """Public RSS — no OAuth, no API key, no quota. Returns (alerts, info)."""
    alerts, info = [], []
    try:
        xml = get(YT_MAIN_RSS)
    except Exception as e:
        alerts.append(f"YouTube main: could not read the channel feed ({e}). "
                      f"If this repeats, the feed may have been retired.")
        return alerts, info

    entries = {}
    for m in re.finditer(
            r"<entry>.*?<yt:videoId>([^<]+)</yt:videoId>.*?<title>(.*?)</title>"
            r".*?<published>([^<]+)</published>.*?</entry>", xml, re.S):
        vid, title, pub = m.group(1), m.group(2), m.group(3)
        entries[vid] = {"title": title.strip(), "published": pub}

    if not entries:
        alerts.append("YouTube main: feed returned no videos — format may have changed.")
        return alerts, info

    seen = state.setdefault("youtube_main", {})
    first_run = not seen          # don't announce the whole back catalogue on day one
    # 1. anything we saw before that has now vanished from the feed AND is gone
    for vid, meta in list(seen.items()):
        if vid in entries:
            continue
        # the feed only holds ~15 items, so falling off it is normal — only alert
        # if the video itself is actually unreachable
        try:
            code = urllib.request.urlopen(
                urllib.request.Request(f"https://www.youtube.com/oembed?url="
                                       f"https://www.youtube.com/watch?v={vid}&format=json",
                                       headers={"User-Agent": "sale91-social-watch/1"}),
                timeout=20).status
            if code == 200:
                continue                      # still live, just aged out of the feed
        except urllib.error.HTTPError as e:
            if e.code in (401, 404):
                alerts.append(f"YouTube main: video GONE — \"{meta.get('title','?')}\" ({vid}). "
                              f"Deleted, private, or taken down.")
                seen.pop(vid, None)
        except Exception:
            pass                              # network blip — say nothing

    # 2. new uploads
    for vid, meta in entries.items():
        if vid not in seen and not first_run:
            info.append(f"YouTube main: new video \"{meta['title'][:70]}\"")
        seen[vid] = meta
    if first_run:
        info.append(f"YouTube main: baseline saved ({len(entries)} videos) — "
                    f"will report changes from now on.")

    # 3. has a Factory Sach landed recently?
    latest_fs = None
    for vid, meta in entries.items():
        if FACTORY_SACH_PAT.search(meta["title"]):
            when = meta["published"]
            if latest_fs is None or when > latest_fs[1]:
                latest_fs = (meta["title"], when)
    if latest_fs:
        try:
            dt = datetime.fromisoformat(latest_fs[1].replace("Z", "+00:00"))
            gap = (datetime.now(timezone.utc) - dt).days
            if gap > FACTORY_SACH_MAX_GAP_DAYS:
                alerts.append(f"No Factory Sach on the main channel for {gap} days "
                              f"(last: \"{latest_fs[0][:50]}\").")
        except Exception:
            pass
    return alerts, info


def token_healthy():
    """Is the Meta token itself alive? Gate deletion alerts on this — Meta returns
    the same error for 'post deleted' and 'your permission broke'."""
    if not IG_TOKEN:
        return False, "no INSTAGRAM_ACCESS_TOKEN set"
    try:
        d = get_json(f"{GRAPH}/me?fields=id,name&access_token={IG_TOKEN}")
        return ("id" in d), d.get("name", "?")
    except Exception as e:
        return False, str(e)[:120]


def check_instagram(state):
    alerts, info = [], []
    ok, who = token_healthy()
    if not ok:
        alerts.append(f"Instagram/Facebook TOKEN looks broken ({who}) — "
                      f"post checks skipped so you don't get false alarms. Refresh the token.")
        return alerts, info
    if not IG_BIZ:
        return alerts, info
    try:
        d = get_json(f"{GRAPH}/{IG_BIZ}/media?fields=id,permalink,timestamp,media_type"
                     f"&limit=10&access_token={IG_TOKEN}")
    except Exception as e:
        alerts.append(f"Instagram: could not list posts ({str(e)[:120]})")
        return alerts, info

    items = d.get("data", [])
    if not items:
        alerts.append("Instagram: account returned zero posts — that's not normal.")
        return alerts, info

    seen = state.setdefault("instagram", {})
    first_run = not seen          # don't announce the whole backlog on day one
    for it in items:
        if it["id"] not in seen and not first_run:
            info.append(f"Instagram: new post {it.get('permalink','')}")
        seen[it["id"]] = {"permalink": it.get("permalink"), "ts": it.get("timestamp")}
    if first_run:
        info.append(f"Instagram: baseline saved ({len(items)} posts).")

    # gap check — the bot posts daily, so >48h of silence is a real failure
    try:
        newest = max(datetime.fromisoformat(i["timestamp"].replace("+0000", "+00:00"))
                     for i in items if i.get("timestamp"))
        hrs = (datetime.now(timezone.utc) - newest).total_seconds() / 3600
        if hrs > 48:
            alerts.append(f"Instagram: nothing posted for {hrs:.0f} hours. "
                          f"The daily reel or the publish queue has stopped.")
    except Exception:
        pass
    return alerts, info


def check_facebook(state):
    """Check reels the bot published are actually PUBLISHED, not stuck as drafts."""
    alerts, info = [], []
    pending = state.setdefault("facebook", {})
    if not pending:
        return alerts, info
    ok, _ = token_healthy()
    if not ok:
        return alerts, info                   # already alerted in check_instagram
    for vid, meta in list(pending.items()):
        if meta.get("confirmed"):
            continue
        try:
            d = get_json(f"{GRAPH}/{vid}?fields=id,status,permalink_url&access_token={FB_TOKEN}")
            status = (d.get("status") or {}).get("video_status") or d.get("status") or "?"
            if str(status).lower() in ("ready", "published"):
                meta["confirmed"] = True
                meta["permalink"] = d.get("permalink_url")
            else:
                alerts.append(f"Facebook reel not live — video {vid} status = {status}. "
                              f"Posted {meta.get('queued_at','?')}. Open the Page and publish it.")
        except Exception as e:
            alerts.append(f"Facebook: could not check reel {vid} ({str(e)[:100]})")
    return alerts, info


# ── alerting ──────────────────────────────────────────────────────────────────

def _tg_direct(title, message):
    """Send via the Bot API using this repo's own TELEGRAM_BOT_TOKEN.

    TELEGRAM_CHANNEL_ID here is the PUBLIC Sale91 channel — never send alerts
    there. TELEGRAM_ALERT_CHAT_ID is Ketu's private chat (the one the APK builds
    land in). If it isn't set we print candidate chat ids from getUpdates so it
    can be filled in without guesswork.
    """
    tok = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_ALERT_CHAT_ID") or "").strip()
    if not tok:
        return False, "no TELEGRAM_BOT_TOKEN"
    if not chat:
        hint = ""
        try:
            wh = get_json(f"https://api.telegram.org/bot{tok}/getWebhookInfo")
            wurl = (wh.get("result") or {}).get("url") or ""
            if wurl:
                hint += (f" NOTE: a webhook is set on this bot ({wurl[:60]}...), so getUpdates "
                         f"returns nothing — the chat id must be supplied manually.")
        except Exception:
            pass
        try:
            d = get_json(f"https://api.telegram.org/bot{tok}/getUpdates?limit=20")
            seen = {}
            for u in d.get("result", []):
                c = ((u.get("message") or u.get("channel_post") or {}).get("chat") or {})
                if c.get("id"):
                    seen[c["id"]] = f"{c.get('title') or c.get('username') or c.get('first_name','?')} ({c.get('type')})"
            if seen:
                hint = " Candidate chat ids: " + "; ".join(f"{k} = {v}" for k, v in seen.items())
        except Exception:
            pass
        return False, ("TELEGRAM_ALERT_CHAT_ID secret is not set, so alerts have nowhere "
                       "private to go (TELEGRAM_CHANNEL_ID is the public channel — not used)." + hint)
    body = urllib.parse.urlencode({
        "chat_id": chat,
        "text": f"*{title}*\n\n{message}",
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }).encode()
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage",
                                     data=body, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get("ok", False), "sent"
    except Exception as e:
        return False, f"sendMessage failed: {str(e)[:150]}"


def _tg_via_dashboard(title, message):
    """Fallback: the proven notify-telegram workflow in Website-Order-Dashboard.
    Needs a GH_PAT that can see THAT repo — ours currently 404s, so this is the
    backup, not the primary."""
    pat = (os.environ.get("GH_PAT") or "").strip()
    if not pat:
        return False, "no GH_PAT"
    body = json.dumps({"ref": "main", "inputs": {"title": title, "message": message}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/repos/thakyanamtumhara/Website-Order-Dashboard"
        "/actions/workflows/notify-telegram.yml/dispatches",
        data=body,
        headers={"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "sale91-social-watch/1"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status in (200, 204), "dispatched"
    except Exception as e:
        return False, f"dispatch failed: {str(e)[:120]}"


def send_telegram(title, message, dry=False):
    if dry:
        print(f"\n[DRY RUN] would send Telegram:\n  {title}\n  {message}\n")
        return True
    ok, why = _tg_direct(title, message)
    if ok:
        return True
    print(f"   ℹ️ direct Telegram unavailable: {why}")
    ok2, why2 = _tg_via_dashboard(title, message)
    if ok2:
        return True
    print(f"   ⚠️ ALERT NOT DELIVERED — {why2}. The alert was:\n      {title}: {message[:300]}")
    return False


def main():
    dry = "--dry-run" in sys.argv
    force = "--force-ping" in sys.argv   # prove the alert path actually delivers
    st = load_state()
    print(f"🔎 social watch — {now_ist().strftime('%d-%b %H:%M IST')}{'  [DRY RUN]' if dry else ''}")

    alerts, info = [], []
    for name, fn in (("YouTube main", check_youtube_main),
                     ("Instagram", check_instagram),
                     ("Facebook", check_facebook)):
        try:
            a, i = fn(st)
        except Exception as e:
            a, i = [f"{name}: watcher itself errored ({str(e)[:120]})"], []
        print(f"   {name:14s} {len(a)} alert(s), {len(i)} new")
        alerts += a; info += i

    for line in info:
        print(f"   ℹ️ {line}")
    for line in alerts:
        print(f"   🔴 {line}")

    stamp = now_ist().strftime("%d-%b %H:%M IST")
    if force:
        ok = send_telegram("🔔 Watcher test ping",
                           "If you can read this, social-post alerts reach you.\n"
                           f"IG ✅ · FB ✅ · YouTube main ✅\n\n_{stamp}_", dry)
        print(f"   force ping delivered: {ok}")
    if alerts:
        send_telegram("⚠️ Social post check", "\n\n".join(alerts) + f"\n\n_{stamp}_", dry)
    else:
        # one green summary a day so a dead watcher is visible
        last_ok = st.get("last_green_date")
        today = now_ist().strftime("%Y-%m-%d")
        if last_ok != today:
            send_telegram("🟢 Social posts all clear",
                          "IG ✅ · FB ✅ · YouTube main ✅\n"
                          "LinkedIn ⚪ X ⚪ (not wired — need approval / paid tier)\n"
                          + (("\n" + "\n".join(info)) if info else "")
                          + f"\n\n_{stamp}_", dry)
            if not dry:
                st["last_green_date"] = today

    st.setdefault("history", []).append(
        {"at": now_ist().isoformat(timespec="seconds"), "alerts": len(alerts), "new": len(info)})
    st["history"] = st["history"][-200:]
    if not dry:
        save_state(st)
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
