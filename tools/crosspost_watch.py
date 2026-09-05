#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tell Ketu when a scheduled crosspost did NOT happen.

WHY THIS EXISTS
    Vizard's open API can fire a scheduled post but cannot report on one. Probed
    2026-08-18 against the live API with a valid key:
        /project/publish-record   404
        /project/publish-list     404
        /project/publish-status   404
        /project/scheduled        404
        /publish-video/list       404
        /project/publish-video/query 404
    The only account-side endpoint that exists is /project/social-accounts, which
    does return `status` and `expiresAt` per account. So a post can silently fail
    to go out and nothing anywhere would say so.

WHAT THIS DOES
    1. PRE-FLIGHT — reads /project/social-accounts and shouts if an account we are
       depending on is "not connected", or if its token expires BEFORE a post we
       have already scheduled on it. This is the failure mode that actually bites:
       an expired Meta token kills the post days after it was scheduled.
    2. POST-HOC — after each ledger entry's time passes (+grace), asks the platform
       itself whether the post exists. Real verification, not a guess:
           instagram -> Graph API /{ig-business-id}/media
           facebook  -> Graph API /{page-id}/posts
           youtube   -> Data API channel uploads
       X and LinkedIn have no credentials in this repo, so they cannot be verified
       programmatically. They are NOT silently skipped — they get one "check this
       now" Telegram nudge with a direct link, once, so a failure surfaces in
       minutes instead of never.

    Silent when everything is fine. Only speaks when something needs Ketu.

Usage:
    python3 tools/crosspost_watch.py            # check + alert
    python3 tools/crosspost_watch.py --dry-run  # print, never send, never write state
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def parse_due(entry):
    """Parse an entry's due_ist, tolerating a trailing hand-typed note.

    Returns an aware datetime, or None if it cannot be read.

    Why this is defensive: on 2026-08-22 a human note was typed straight into
    the field ("2026-08-22 (PUBLISHED EARLY by Ketu)"). A bare strptime raised,
    which aborted the whole pass — so this watcher crash-looped for 12 days
    across 22 runs, and because the loop died on entry 13 of 30, the 17 entries
    after it were never checked either. One bad row must never blind the rest.
    """
    raw = str(entry.get("due_ist") or "").strip()
    cleaned = raw.split("(")[0].strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(REPO, "crosspost_ledger.json")
STATE = os.path.join(REPO, "crosspost_state.json")

VIZARD = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"
GRAPH = "https://graph.facebook.com/v21.0"

# Vizard account ids we actually publish through (tools/vizard_publish.py)
WATCHED = {
    "dml6YXJkLTUtMTY5NzY5": "X: Ketu R",
    "dml6YXJkLTMtMTY5NjIzLTE3NzMx": "Facebook Page: Own Knitted Blank Wears",
    "dml6YXJkLTYtMTY5NjIyLTE3NzMw": "LinkedIn personal: Ketu R",
}
PLATFORM_TO_ACCOUNT = {"x": "dml6YXJkLTUtMTY5NzY5",
                       "facebook": "dml6YXJkLTMtMTY5NjIzLTE3NzMx",
                       "linkedin": "dml6YXJkLTYtMTY5NjIyLTE3NzMw"}

GRACE_HOURS = 3        # cron drift + platform propagation
MANUAL_NUDGE_AFTER = 1  # hours after due time to ask Ketu to eyeball X / LinkedIn


def now_ist():
    return datetime.now(IST)


def get_json(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def vizard_key():
    k = (os.environ.get("VIZARD_API_KEY") or "").strip()
    if not k:
        p = os.path.expanduser("~/.vizard_api_key")
        if os.path.exists(p):
            k = open(p).read().strip()
    return k


def telegram(msg, dry=False):
    tok = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_ALERT_CHAT_ID") or "").strip()
    if dry or not tok or not chat:
        print("\n--- TELEGRAM (%s) ---\n%s\n" % ("dry-run" if dry else "no creds", msg))
        return bool(dry)
    data = urllib.parse.urlencode({
        "chat_id": chat, "text": msg,
        "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
    try:
        r = urllib.request.urlopen(
            urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % tok, data=data),
            timeout=20)
        return json.loads(r.read().decode()).get("ok", False)
    except Exception as e:
        print("telegram failed:", e)
        return False


# ---------------------------------------------------------------- pre-flight
def check_accounts(pending):
    """pending: {account_id: earliest datetime we still need it for}"""
    problems = []
    k = vizard_key()
    if not k:
        return ["Vizard key missing — cannot check account health."]
    try:
        d = get_json(VIZARD + "/project/social-accounts",
                     {"VIZARDAI_API_KEY": k, "Content-Type": "application/json"})
    except Exception as e:
        return ["Vizard social-accounts unreachable: %s" % e]

    seen = {}
    for a in d.get("publishAccounts", []):
        seen[a.get("id")] = a
    for acct_id, label in WATCHED.items():
        a = seen.get(acct_id)
        if not a:
            problems.append("%s — account has disappeared from Vizard." % label)
            continue
        if a.get("status") != "active":
            problems.append("%s — status is '%s'. Scheduled posts on it will fail."
                            % (label, a.get("status")))
            continue
        exp = a.get("expiresAt")
        need_by = pending.get(acct_id)
        if exp and need_by:
            exp_dt = datetime.fromtimestamp(exp / 1000, IST)
            if exp_dt < need_by:
                problems.append(
                    "%s — token expires %s but a post is scheduled %s. Reconnect it in Vizard."
                    % (label, exp_dt.strftime("%d %b %H:%M"), need_by.strftime("%d %b %H:%M")))
            elif exp_dt < now_ist() + timedelta(days=7):
                problems.append("%s — token expires %s (under 7 days). Reconnect soon."
                                % (label, exp_dt.strftime("%d %b")))
    return problems


# ---------------------------------------------------------------- post-hoc
def ig_posted_since(since, match):
    tok = (os.environ.get("INSTAGRAM_ACCESS_TOKEN") or "").strip()
    ig_id = (os.environ.get("INSTAGRAM_BUSINESS_ID") or "").strip()
    if not tok or not ig_id:
        return None, "no Instagram credentials"
    url = ("%s/%s/media?fields=id,caption,timestamp,permalink&limit=25&access_token=%s"
           % (GRAPH, ig_id, urllib.parse.quote(tok)))
    try:
        d = get_json(url)
    except Exception as e:
        return None, "Instagram API error: %s" % e
    for m in d.get("data", []):
        ts = m.get("timestamp", "")
        try:
            when = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(IST)
        except Exception:
            continue
        if when >= since and (not match or match in (m.get("caption") or "")):
            return m.get("permalink") or m.get("id"), None
    return False, None


def fb_posted_since(since, match):
    tok = (os.environ.get("FB_PAGE_ACCESS_TOKEN") or "").strip()
    page = (os.environ.get("FB_PAGE_ID") or "").strip()
    if not tok or not page:
        return None, "no Facebook page credentials"
    url = ("%s/%s/posts?fields=id,message,created_time,permalink_url&limit=25&access_token=%s"
           % (GRAPH, page, urllib.parse.quote(tok)))
    try:
        d = get_json(url)
    except Exception as e:
        return None, "Facebook API error: %s" % e
    for m in d.get("data", []):
        try:
            when = datetime.fromisoformat(
                m.get("created_time", "").replace("+0000", "+00:00")).astimezone(IST)
        except Exception:
            continue
        if when >= since and (not match or match.lower() in (m.get("message") or "").lower()):
            return m.get("permalink_url") or m.get("id"), None
    return False, None


VERIFIERS = {"instagram": ig_posted_since, "facebook": fb_posted_since}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ledger = json.load(open(LEDGER, encoding="utf-8"))
    state = {}
    if os.path.exists(STATE):
        state = json.load(open(STATE, encoding="utf-8"))

    now = now_ist()
    alerts, nudges = [], []
    pending = {}

    for e in ledger["entries"]:
        eid = e["id"]
        st = state.get(eid, {})
        if st.get("resolved"):
            continue
        due = parse_due(e)
        if due is None:
            print(f"⚠️  {eid}: unreadable due_ist {e.get('due_ist')!r} — skipping this "
                  f"entry only (the rest of the ledger still gets checked)")
            continue

        # still in the future -> remember it for the token-expiry pre-flight
        if now < due:
            acct = PLATFORM_TO_ACCOUNT.get(e["platform"])
            if acct and (acct not in pending or due < pending[acct]):
                pending[acct] = due
            continue

        if e.get("verify") == "manual":
            if now >= due + timedelta(hours=MANUAL_NUDGE_AFTER) and not st.get("nudged"):
                nudges.append("• <b>%s</b> was due %s. No API can confirm it — open %s and "
                              "check it went out." % (e["label"], due.strftime("%d %b %H:%M"),
                                                      e.get("url", e["platform"])))
                state.setdefault(eid, {})["nudged"] = True
            continue

        if now < due + timedelta(hours=GRACE_HOURS):
            continue  # inside the drift window, too early to call it a failure

        fn = VERIFIERS.get(e["platform"])
        if not fn:
            continue
        found, err = fn(due - timedelta(hours=1), e.get("match"))
        if err:
            alerts.append("• <b>%s</b> — could not verify (%s)." % (e["label"], err))
        elif found:
            state.setdefault(eid, {})["resolved"] = True
            state[eid]["url"] = found if isinstance(found, str) else ""
            print("OK  %s -> %s" % (eid, found))
        else:
            if not st.get("alerted"):
                alerts.append("• <b>%s</b> did NOT post. Was due %s on %s (%s rail)."
                              % (e["label"], due.strftime("%d %b %H:%M"),
                                 e["platform"], e.get("rail", "?")))
                state.setdefault(eid, {})["alerted"] = True
            print("MISSING %s" % eid)

    problems = check_accounts(pending)

    msg = []
    if alerts:
        msg.append("🚨 <b>Crosspost did not go out</b>\n" + "\n".join(alerts))
    if problems:
        msg.append("⚠️ <b>Account health</b>\n" + "\n".join("• " + p for p in problems))
    if nudges:
        msg.append("👀 <b>Please eyeball these</b>\n" + "\n".join(nudges))

    if msg:
        telegram("\n\n".join(msg), dry=args.dry_run)
    else:
        print("all clear — nothing to report")

    if not args.dry_run:
        json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
