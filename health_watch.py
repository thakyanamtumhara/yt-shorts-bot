#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Paid-dependency health watch.

Built after the 10-13 Aug 2026 wrong-voice reels: the ElevenLabs plan was
downgraded Creator -> Starter at the 7-Aug renewal, the Professional Voice Clone
stopped being allowed, daily_short.py fell through to Sarvam (402) then OpenAI
TTS, and every Actions run still went green. Four reels shipped in a stranger's
voice before anyone noticed.

Two modes:
  --gate   runs inside daily_short.yml before any paid call. Exits non-zero when
           a CRITICAL dependency is down, so a $7.50 Veo render is never spent on
           a video that would speak in the wrong voice.
  (none)   scheduled watch: alerts on any status transition and sends one green
           heartbeat a day so a dead watcher is itself visible.

Usage:  python3 health_watch.py [--gate] [--dry-run] [--json] [--force-ping]
"""
import json, os, sys, time, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timedelta, timezone

from social_watch import send_telegram, now_ist

IST = timezone(timedelta(hours=5, minutes=30))
STATE_FILE = "health_state.json"
COST_FILE = "cost_tracker.json"

ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "cejtKjfE9sHUZ1FnUYEV")
PVC_TIERS = {"creator", "pro", "scale", "business", "enterprise"}

# two videos of headroom — enough time to notice and top up before a run dies
MIN_TTS_CHARS_LEFT = 6000
TOKEN_EXPIRY_WARN_DAYS = 21

CRITICAL, WARN, INFO = "CRITICAL", "WARN", "INFO"


def _req(url, headers=None, data=None, method=None, timeout=25):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {"User-Agent": "sale91-health-watch/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def _json_req(url, headers=None, data=None, method=None, timeout=25):
    status, raw = _req(url, headers, data, method, timeout)
    try:
        return status, json.loads(raw)
    except Exception:
        return status, {"_raw": raw[:400]}


def _env(name):
    return (os.environ.get(name) or "").strip()


class Result:
    def __init__(self, key, label, severity, ok, detail, extra=None):
        self.key, self.label, self.severity = key, label, severity
        self.ok, self.detail, self.extra = ok, detail, extra or {}

    def as_dict(self):
        return {"ok": self.ok, "severity": self.severity, "detail": self.detail, **self.extra}


# ── the check that would have caught this on 10 Aug ───────────────────────────

def check_last_video_voice():
    """Which TTS provider actually spoke on the most recent render.

    Independent of every API probe below: cost_tracker.json is written by the run
    itself, so this is ground truth about what shipped rather than what should
    have shipped.
    """
    key, label, sev = "last_video_voice", "Last video's voice", CRITICAL
    if not os.path.exists(COST_FILE):
        return Result(key, label, sev, True, "no cost_tracker.json yet — skipped")
    try:
        entries = json.load(open(COST_FILE, encoding="utf-8"))
    except Exception as e:
        return Result(key, label, WARN, True, f"cost_tracker.json unreadable ({str(e)[:80]})")
    renders = [e for e in entries if any(k.startswith("tts_") for k in (e.get("breakdown") or {}))]
    if not renders:
        return Result(key, label, sev, True, "no render with a TTS line yet — skipped")
    last = renders[-1]
    used = [k[4:] for k in (last.get("breakdown") or {}) if k.startswith("tts_")]
    when = (last.get("date") or "")[:16].replace("T", " ")
    extra = {"provider": ",".join(used), "at": when, "title": (last.get("title") or "")[:70]}
    if "elevenlabs" in used:
        return Result(key, label, sev, True, f"ElevenLabs ✅ ({when})", extra)
    return Result(key, label, sev, False,
                  f"last render used {' + '.join(used) or 'no TTS'} instead of ElevenLabs — "
                  f"that video is in the WRONG VOICE ({when})", extra)


# ── providers ─────────────────────────────────────────────────────────────────

def check_elevenlabs():
    key, label, sev = "elevenlabs", "ElevenLabs voice", CRITICAL
    api_key = _env("ELEVENLABS_API_KEY")
    if not api_key:
        return Result(key, label, sev, False, "ELEVENLABS_API_KEY not set")
    h = {"xi-api-key": api_key}

    status, sub = _json_req("https://api.elevenlabs.io/v1/user/subscription", h)
    if status != 200:
        return Result(key, label, sev, False,
                      f"subscription lookup failed (HTTP {status}) {json.dumps(sub)[:160]}")

    tier = str(sub.get("tier", "?")).lower()
    can_pvc = bool(sub.get("can_use_professional_voice_cloning"))
    used, limit = sub.get("character_count", 0), sub.get("character_limit", 0)
    left = max(0, (limit or 0) - (used or 0))
    reset = sub.get("next_character_count_reset_unix")
    extra = {"tier": tier, "can_use_pvc": can_pvc, "chars_left": left, "char_limit": limit,
             "sub_status": sub.get("status")}
    if reset:
        extra["quota_resets"] = datetime.fromtimestamp(reset, IST).strftime("%d %b %Y")

    if not can_pvc or tier not in PVC_TIERS:
        return Result(key, label, sev, False,
                      f"plan is '{tier}' — Professional Voice Clone is BLOCKED "
                      f"(needs Creator or above). The reel will fall back to a generic voice. "
                      f"Fix: https://elevenlabs.io/app/settings/subscription", extra)

    status, voice = _json_req(f"https://api.elevenlabs.io/v1/voices/{ELEVENLABS_VOICE_ID}", h)
    if status != 200:
        return Result(key, label, sev, False,
                      f"plan '{tier}' ok but voice {ELEVENLABS_VOICE_ID} unreadable "
                      f"(HTTP {status})", extra)
    extra["voice_name"] = voice.get("name")
    extra["voice_category"] = voice.get("category")
    if voice.get("category") != "professional":
        return Result(key, label, sev, False,
                      f"voice '{voice.get('name')}' is category "
                      f"'{voice.get('category')}' — not the PVC clone", extra)

    if left < MIN_TTS_CHARS_LEFT:
        return Result(key, label, sev, False,
                      f"plan '{tier}' ok but only {left:,} characters left of {limit:,} — "
                      f"roughly {left // 1500} videos before it silently falls back", extra)

    return Result(key, label, sev, True,
                  f"{tier} · PVC allowed · {left:,}/{limit:,} chars left", extra)


def check_sarvam():
    key, label, sev = "sarvam", "Sarvam (fallback 1)", WARN
    api_key = _env("SARVAM_API_KEY")
    if not api_key:
        return Result(key, label, sev, True, "not configured — skipped")
    payload = json.dumps({"text": "ok", "target_language_code": "hi-IN"}).encode()
    status, body = _json_req("https://api.sarvam.ai/text-to-speech",
                             {"api-subscription-key": api_key, "Content-Type": "application/json"},
                             data=payload, method="POST")
    if status == 402:
        return Result(key, label, sev, False,
                      "402 Payment Required — Sarvam credits are exhausted. "
                      "Top up: https://dashboard.sarvam.ai/ (this is fallback #1, "
                      "so the reel drops straight to the generic OpenAI voice)")
    if status in (401, 403):
        return Result(key, label, sev, False, f"auth rejected (HTTP {status}) — key may be revoked")
    if status not in (200, 201):
        return Result(key, label, sev, False, f"HTTP {status} {json.dumps(body)[:140]}")
    return Result(key, label, sev, True, "credits available")


def check_openai():
    key, label, sev = "openai", "OpenAI (last-resort voice)", WARN
    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        return Result(key, label, sev, False, "OPENAI_API_KEY not set")
    status, body = _json_req("https://api.openai.com/v1/models",
                             {"Authorization": f"Bearer {api_key}"})
    if status != 200:
        return Result(key, label, sev, False, f"HTTP {status} {json.dumps(body)[:140]}")
    return Result(key, label, sev, True, "key valid (no balance endpoint exists)")


def check_anthropic():
    key, label, sev = "anthropic", "Claude (script writer)", CRITICAL
    api_key = _env("ANTHROPIC_API_KEY")
    if not api_key:
        return Result(key, label, sev, False, "ANTHROPIC_API_KEY not set")
    h = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
    # a 1-token completion, not /v1/models: an empty credit balance only shows up
    # on a real message call
    payload = json.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 1,
                          "messages": [{"role": "user", "content": "hi"}]}).encode()
    status, body = _json_req("https://api.anthropic.com/v1/messages", h, data=payload, method="POST")
    if status == 200:
        return Result(key, label, sev, True, "key valid, credit available")
    err = ((body.get("error") or {}).get("message") or json.dumps(body))[:160]
    if status == 400 and "credit balance" in err.lower():
        return Result(key, label, sev, False, f"CREDIT BALANCE EMPTY — {err}")
    if status == 429:
        return Result(key, label, sev, True, "rate limited on the probe — treating as alive")
    return Result(key, label, sev, False, f"HTTP {status} {err}")


def check_google():
    key, label, sev = "google", "Google (Veo clips + Gemini cover)", CRITICAL
    api_key = _env("GOOGLE_API_KEY")
    if not api_key:
        return Result(key, label, sev, False, "GOOGLE_API_KEY not set")
    status, body = _json_req(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={urllib.parse.quote(api_key)}")
    if status != 200:
        err = ((body.get("error") or {}).get("message") or json.dumps(body))[:160]
        return Result(key, label, sev, False, f"HTTP {status} {err}")
    names = [m.get("name", "") for m in body.get("models", [])]
    veo = [n for n in names if "veo" in n.lower()]
    return Result(key, label, sev, True,
                  f"key valid · {len(names)} models · veo visible: {'yes' if veo else 'no'}",
                  {"veo_models": veo[:4]})


def check_replicate():
    key, label, sev = "replicate", "Replicate (background music)", WARN
    tok = _env("REPLICATE_API_TOKEN")
    if not tok:
        return Result(key, label, sev, True, "not configured — skipped")
    status, body = _json_req("https://api.replicate.com/v1/account",
                             {"Authorization": f"Bearer {tok}"})
    if status != 200:
        return Result(key, label, sev, False, f"HTTP {status} {json.dumps(body)[:140]}")
    return Result(key, label, sev, True, f"account {body.get('username', '?')} ok")


def check_fal():
    key, label, sev = "fal", "fal.ai (Kling clip fallback)", INFO
    tok = _env("FAL_KEY")
    if not tok:
        return Result(key, label, sev, True, "not configured — skipped")
    status, _ = _json_req("https://rest.alpha.fal.ai/tokens/", {"Authorization": f"Key {tok}"})
    if status in (200, 201, 204, 405):
        return Result(key, label, sev, True, "key accepted")
    if status in (401, 403):
        return Result(key, label, sev, False, f"auth rejected (HTTP {status})")
    return Result(key, label, sev, True, f"inconclusive (HTTP {status}) — not treated as down")


def check_meta_token():
    key, label, sev = "meta_token", "Instagram / Facebook token", CRITICAL
    tok = _env("INSTAGRAM_ACCESS_TOKEN")
    app_id, app_secret = _env("FB_APP_ID"), _env("FB_APP_SECRET")
    if not tok:
        return Result(key, label, sev, False, "INSTAGRAM_ACCESS_TOKEN not set")
    if not (app_id and app_secret):
        status, body = _json_req(
            f"https://graph.facebook.com/v21.0/me?access_token={urllib.parse.quote(tok)}")
        if status != 200:
            return Result(key, label, sev, False, f"token rejected (HTTP {status})")
        return Result(key, label, sev, True, "token valid (no app secret — expiry unknown)")

    app_token = urllib.parse.quote(f"{app_id}|{app_secret}")
    status, body = _json_req(
        f"https://graph.facebook.com/v21.0/debug_token"
        f"?input_token={urllib.parse.quote(tok)}&access_token={app_token}")
    data = body.get("data") or {}
    if status != 200 or not data:
        return Result(key, label, sev, False, f"debug_token failed (HTTP {status}) "
                                              f"{json.dumps(body)[:140]}")
    if not data.get("is_valid"):
        return Result(key, label, sev, False,
                      f"token INVALID — {(data.get('error') or {}).get('message', 'no reason given')}")
    exp = data.get("expires_at") or 0
    if not exp:
        return Result(key, label, sev, True, "valid, never expires", {"expires_at": "never"})
    days = (datetime.fromtimestamp(exp, IST) - now_ist()).days
    extra = {"expires_on": datetime.fromtimestamp(exp, IST).strftime("%d %b %Y"), "days_left": days}
    if days <= TOKEN_EXPIRY_WARN_DAYS:
        return Result(key, label, sev, False,
                      f"expires in {days} days ({extra['expires_on']}) — refresh it or "
                      f"Instagram posting stops dead", extra)
    return Result(key, label, sev, True, f"valid, {days} days left", extra)


def check_youtube():
    key, label, sev = "youtube", "YouTube OAuth", CRITICAL
    path = "/tmp/yt_shorts/youtube_token.json"
    if not os.path.exists(path):
        return Result(key, label, sev, True, "token file not present in this context — skipped")
    try:
        tok = json.load(open(path))
    except Exception as e:
        return Result(key, label, sev, False, f"token file unreadable ({str(e)[:80]})")
    refresh, cid, csec = tok.get("refresh_token"), tok.get("client_id"), tok.get("client_secret")
    if not (refresh and cid and csec):
        return Result(key, label, sev, False, "token json missing refresh_token/client credentials")
    payload = urllib.parse.urlencode({"client_id": cid, "client_secret": csec,
                                      "refresh_token": refresh,
                                      "grant_type": "refresh_token"}).encode()
    status, body = _json_req("https://oauth2.googleapis.com/token",
                             {"Content-Type": "application/x-www-form-urlencoded"},
                             data=payload, method="POST")
    if status != 200:
        return Result(key, label, sev, False,
                      f"refresh REJECTED (HTTP {status}) {json.dumps(body)[:140]} — "
                      f"re-run generate_token.py")
    return Result(key, label, sev, True, "refresh token still works")


def check_vizard():
    key, label, sev = "vizard", "Vizard (multi-platform publish)", INFO
    tok = _env("VIZARD_API_KEY")
    if not tok:
        return Result(key, label, sev, True, "not configured — skipped")
    status, _ = _json_req("https://elb-api.vizard.ai/hvc/v1/project/query",
                          {"VIZARDAI_API_KEY": tok, "Content-Type": "application/json"},
                          data=json.dumps({"projectId": 0}).encode(), method="POST")
    if status in (401, 403):
        return Result(key, label, sev, False, f"auth rejected (HTTP {status})")
    return Result(key, label, sev, True, "key accepted")


CHECKS = (
    check_last_video_voice,
    check_elevenlabs,
    check_sarvam,
    check_anthropic,
    check_google,
    check_meta_token,
    check_youtube,
    check_openai,
    check_replicate,
    check_fal,
    check_vizard,
)


# ── state + reporting ─────────────────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception as e:
            print(f"⚠️ state file unreadable ({e}) — starting fresh")
    return {"checks": {}, "history": []}


def save_state(st, results):
    st["last_run_ist"] = now_ist().isoformat(timespec="seconds")
    st["checks"] = {r.key: r.as_dict() for r in results}
    st.setdefault("history", []).append({
        "at": now_ist().isoformat(timespec="seconds"),
        "down": [r.key for r in results if not r.ok],
    })
    st["history"] = st["history"][-200:]
    json.dump(st, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def run_all():
    results = []
    for fn in CHECKS:
        try:
            results.append(fn())
        except Exception as e:
            name = fn.__name__.replace("check_", "")
            results.append(Result(name, name, WARN, True,
                                  f"probe itself errored ({type(e).__name__}: {str(e)[:100]}) — "
                                  f"not treated as down"))
    return results


def render(results):
    icon = {True: "🟢", False: "🔴"}
    lines = []
    for r in results:
        mark = icon[r.ok] if r.ok or r.severity != INFO else "⚪"
        lines.append(f"{mark} {r.label}: {r.detail}")
    return "\n".join(lines)


def main():
    dry = "--dry-run" in sys.argv
    gate = "--gate" in sys.argv
    force = "--force-ping" in sys.argv
    stamp = now_ist().strftime("%d-%b %H:%M IST")

    print(f"🩺 health watch — {stamp}{'  [GATE]' if gate else ''}{'  [DRY RUN]' if dry else ''}")
    results = run_all()
    for r in results:
        print(f"   {'🟢' if r.ok else '🔴'} {r.label:34s} {r.detail}")

    if "--json" in sys.argv:
        print(json.dumps({r.key: r.as_dict() for r in results}, indent=1, ensure_ascii=False))

    down = [r for r in results if not r.ok]
    # last_video_voice is retrospective — it stays red until a good render exists,
    # so gating on it would deadlock the very run that fixes it
    blocking = [r for r in down if r.severity == CRITICAL and r.key != "last_video_voice"]

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### 🩺 Dependency health — {stamp}\n\n")
            for r in results:
                f.write(f"- {'🟢' if r.ok else '🔴'} **{r.label}** — {r.detail}\n")

    if gate:
        if blocking:
            body = ("Today's reel was NOT made — a paid dependency is down and the video "
                    "would have shipped degraded.\n\n" + render(down) +
                    f"\n\nFix it, then re-run: gh workflow run daily_short.yml "
                    f"-R thakyanamtumhara/yt-shorts-bot\n\n_{stamp}_")
            send_telegram("🔴 Daily reel BLOCKED — dependency down", body, dry)
            for r in blocking:
                print(f"::error title={r.label}::{r.detail}")
            print(f"\n🚫 GATE FAILED — {len(blocking)} critical dependency down. "
                  f"Refusing to spend Veo credits on a degraded video.")
            return 1
        if down:
            for r in down:
                print(f"::warning title={r.label}::{r.detail}")
        print("\n✅ GATE PASSED — safe to build today's reel.")
        return 0

    st = load_state()
    prev = st.get("checks", {})
    changed = [r for r in results if bool(prev.get(r.key, {}).get("ok", True)) != r.ok]

    if force:
        send_telegram("🔔 Health watch test ping",
                      render(results) + f"\n\n_{stamp}_", dry)

    if changed:
        broke = [r for r in changed if not r.ok]
        healed = [r for r in changed if r.ok]
        parts = []
        if broke:
            parts.append("*Broke:*\n" + render(broke))
        if healed:
            parts.append("*Recovered:*\n" + render(healed))
        send_telegram("⚠️ Pipeline dependency changed", "\n\n".join(parts) + f"\n\n_{stamp}_", dry)
    elif down:
        # still broken and already announced — re-ping once a day, not every run
        today = now_ist().strftime("%Y-%m-%d")
        if st.get("last_still_down_date") != today:
            send_telegram("🔴 Still down", render(down) + f"\n\n_{stamp}_", dry)
            if not dry:
                st["last_still_down_date"] = today
    else:
        today = now_ist().strftime("%Y-%m-%d")
        if st.get("last_green_date") != today:
            send_telegram("🟢 Pipeline dependencies all clear", render(results) + f"\n\n_{stamp}_", dry)
            if not dry:
                st["last_green_date"] = today

    if not dry:
        save_state(st, results)
    print(f"\ndone — {len(down)} down ({len(blocking)} critical).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
