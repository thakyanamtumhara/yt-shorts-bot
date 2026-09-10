#!/usr/bin/env python3
"""Read-only weekly review of daily AI Shorts. This tool cannot publish."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import zipfile

import requests


REPOSITORY = "thakyanamtumhara/yt-shorts-bot"
IG_ACCOUNT = "17841407981790313"
IG_USERNAME = "bulkplaintshirt_com"
BOT_CHANNEL = "UCHZbA84OiM9COlTQ4JcVgeQ"
MAIN_CHANNEL = "UCdgOMA7WO48MYimj6q6mvNQ"
MIN_AGE_DAYS = 7
MIN_BATCH = 7
MAX_BATCH = 10
REVIEW_CHECKS = ("facts", "full_spoken_audio", "visuals", "buyer_useful", "cover")
AI_FLAGS = ("ai_visuals", "ai_voice", "ai_music", "ai_face", "containsSyntheticMedia")


class ReviewError(Exception):
    pass


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise ReviewError("Cannot read valid JSON: " + Path(path).name) from None


def stamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise ReviewError("A source timestamp is missing or has no timezone.") from None


def valid_source(source):
    return (isinstance(source, dict) and source.get("ai_generated") is True
            and source.get("bot_channel_id") == BOT_CHANNEL
            and re.fullmatch(r"[A-Za-z0-9_-]{11}", str(source.get("bot_youtube_id", "")))
            and re.fullmatch(r"\d{5,30}", str(source.get("instagram_id", "")))
            and isinstance(source.get("workflow_run_id"), int)
            and source["workflow_run_id"] > 0)


def safe_get(session, url, *, service, params=None, stream=False):
    try:
        response = session.get(url, params=params, timeout=60, stream=stream)
    except requests.RequestException:
        raise ReviewError(service + " transport failed; no empty-result fallback.") from None
    if response.status_code != 200:
        raise ReviewError(f"{service} rejected a read (HTTP {response.status_code}); no empty-result fallback.")
    if stream:
        return response
    try:
        data = response.json()
    except ValueError:
        raise ReviewError(service + " returned invalid JSON.") from None
    if not isinstance(data, dict) or "error" in data or "errors" in data:
        raise ReviewError(service + " returned an error or unexpected response schema.")
    return data


class Instagram:
    def __init__(self, token, account_id):
        if not token or account_id != IG_ACCOUNT:
            raise ReviewError("Existing Instagram credential/account configuration is missing or different.")
        self.session = requests.Session()
        self.session.headers["Authorization"] = "Bearer " + token
        self.account_id = account_id

    def get(self, path, params):
        return safe_get(self.session, "https://graph.facebook.com/v21.0/" + path,
                        service="Instagram", params=params)

    def owned_media(self, required_ids):
        identity = self.get(self.account_id, {"fields": "id,username"})
        if identity.get("id") != IG_ACCOUNT or identity.get("username") != IG_USERNAME:
            raise ReviewError("Instagram account identity does not match the configured account.")
        found, after = {}, None
        for _ in range(5):
            params = {"fields": "id,timestamp,permalink,media_type,media_product_type", "limit": 100}
            if after:
                params["after"] = after
            data = self.get(self.account_id + "/media", params)
            if not isinstance(data.get("data"), list):
                raise ReviewError("Instagram media list is incomplete or malformed.")
            for media in data["data"]:
                if media.get("id") in required_ids:
                    found[media["id"]] = media
            if set(found) == set(required_ids):
                return found
            paging = data.get("paging", {})
            if not paging.get("next"):
                break
            after = paging.get("cursors", {}).get("after")
            if not after:
                raise ReviewError("Instagram pagination is incomplete.")
        if required_ids - set(found):
            raise ReviewError("Some source reels were not found on the configured account; ownership is unverified.")
        return found

    def snapshot(self, media_id):
        names = "views,reach,shares,saved,ig_reels_avg_watch_time"
        response = self.get(media_id + "/insights", {"metric": names})
        if not isinstance(response.get("data"), list):
            raise ReviewError("Instagram insights response is incomplete.")
        values = {}
        for item in response["data"]:
            entries = item.get("values") or []
            value = entries[0].get("value") if len(entries) == 1 else None
            if item.get("name") in names.split(","):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ReviewError("Instagram insight is missing or not a nonnegative number.")
                values[item["name"]] = value
        if set(values) != set(names.split(",")):
            raise ReviewError("Instagram omitted required metrics; no invented zeros.")
        return {"views": values["views"], "reach": values["reach"], "shares": values["shares"],
                "saves": values["saved"], "avg_watch_time_ms": values["ig_reels_avg_watch_time"]}


def archive_source(path, *, artifact_id=None, run_id=None):
    try:
        with zipfile.ZipFile(path) as archive:
            manifests = [name for name in archive.namelist() if Path(name).name == "review_manifest.json"]
            if not manifests:
                return None
            if len(manifests) != 1 or archive.getinfo(manifests[0]).file_size > 2_000_000:
                raise ReviewError("Archive contains ambiguous or oversized review metadata.")
            manifest = json.loads(archive.read(manifests[0]).decode("utf-8"))
            if manifest.get("format") != "daily-short-review-v1" or manifest.get("ai_generated") is not True or manifest.get("test_mode") is not False:
                return None
            posts = manifest.get("source_posts") or {}
            workflow = manifest.get("workflow") or {}
            if workflow.get("github_repository") != REPOSITORY:
                raise ReviewError("Archive repository provenance does not match.")
            try:
                recorded_run = int(workflow.get("github_run_id"))
            except (ValueError, TypeError):
                raise ReviewError("Archive has no valid workflow run ID.") from None
            if run_id is not None and recorded_run != run_id:
                raise ReviewError("Archive workflow ID does not match GitHub metadata.")
            source = {"instagram_id": posts.get("instagram"), "bot_youtube_id": posts.get("bot_youtube"),
                      "bot_channel_id": BOT_CHANNEL, "ai_generated": True,
                      "workflow_run_id": recorded_run, "artifact_id": artifact_id,
                      "provenance": "daily-short-review-v1 archive"}
            if not valid_source(source):
                return None
            hashes = {}
            for kind in ("video", "cover"):
                asset = (manifest.get("assets") or {}).get(kind)
                if not isinstance(asset, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(asset.get("sha256", ""))):
                    raise ReviewError("Review archive lacks a valid original video/cover hash.")
                name = asset.get("file")
                if not isinstance(name, str) or Path(name).name != name:
                    raise ReviewError("Archive asset filename is not a basename.")
                matches = [entry for entry in archive.namelist() if Path(entry).name == name]
                if len(matches) != 1:
                    raise ReviewError("Archive asset is absent or ambiguous.")
                info = archive.getinfo(matches[0])
                if not 0 < info.file_size <= 250_000_000 or info.file_size != asset.get("bytes"):
                    raise ReviewError("Archive asset size does not match.")
                digest = hashlib.sha256()
                with archive.open(matches[0]) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != asset["sha256"]:
                    raise ReviewError("Archive original asset hash does not match.")
                hashes[kind] = asset["sha256"]
            source.update(master_verified=True, master_sha256=hashes["video"], cover_sha256=hashes["cover"])
            return source
    except (OSError, zipfile.BadZipFile, ValueError, UnicodeError, KeyError):
        raise ReviewError("Cannot inspect a valid review archive.") from None


def discover_archives(token, now, directory):
    if not token:
        raise ReviewError("GitHub read credential is missing; archive discovery unavailable.")
    session = requests.Session()
    session.headers.update(Authorization="Bearer " + token, Accept="application/vnd.github+json")
    api = "https://api.github.com/repos/" + REPOSITORY
    payload = safe_get(session, api + "/actions/artifacts", service="GitHub",
                       params={"name": "rendered-video-backup", "per_page": 100})
    if not isinstance(payload.get("artifacts"), list):
        raise ReviewError("GitHub artifact list is incomplete.")
    sources, scanned = {}, []
    for item in payload["artifacts"]:
        created = stamp(item.get("created_at"))
        age = (now - created).total_seconds() / 86400
        if item.get("name") != "rendered-video-backup" or not 7 <= age <= 22:
            continue
        detail = {"artifact_id": item["id"], "created_at": item["created_at"], "expired": bool(item.get("expired"))}
        scanned.append(detail)
        if item.get("expired"):
            continue
        run_id = (item.get("workflow_run") or {}).get("id")
        if not isinstance(run_id, int):
            raise ReviewError("GitHub archive has no workflow run provenance.")
        run = safe_get(session, api + "/actions/runs/" + str(run_id), service="GitHub")
        if (run.get("path") != ".github/workflows/daily_short.yml"
                or run.get("head_branch") != "main" or run.get("conclusion") != "success"
                or (run.get("head_repository") or {}).get("full_name") != REPOSITORY):
            detail["excluded"] = "Not a successful main-branch daily generator run"
            continue
        if not isinstance(item.get("size_in_bytes"), int) or not 0 < item["size_in_bytes"] <= 250_000_000:
            raise ReviewError("Review archive exceeds the bounded download size.")
        path = Path(directory) / (str(item["id"]) + ".zip")
        response = safe_get(session, api + "/actions/artifacts/" + str(item["id"]) + "/zip", service="GitHub", stream=True)
        size = 0
        try:
            with response, path.open("wb") as target:
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > 250_000_000:
                        raise ReviewError("Review archive exceeds the bounded download size.")
                    target.write(chunk)
        except requests.RequestException:
            raise ReviewError("GitHub archive download interrupted.") from None
        source = archive_source(path, artifact_id=item["id"], run_id=run_id)
        detail["manifest_present"] = source is not None
        if source:
            media_id = source["instagram_id"]
            if media_id in sources and sources[media_id]["master_sha256"] != source["master_sha256"]:
                raise ReviewError("Multiple original masters claim the same Instagram source ID.")
            sources[media_id] = source
    return sources, scanned


def source_pool(ledger, archive_sources):
    if not isinstance(ledger, dict) or ledger.get("format") != "selective-main-review-v1":
        raise ReviewError("Invalid selective review ledger format.")
    for key in ("known_sources", "reviews", "promotions"):
        if not isinstance(ledger.get(key), list):
            raise ReviewError("Review ledger requires lists of known sources, reviews and promotions.")
    sources = {}
    for source in ledger["known_sources"]:
        if not valid_source(source):
            raise ReviewError("Legacy source lacks explicit AI/BOT/workflow provenance.")
        value = dict(source)
        value.pop("master_verified", None)
        value.pop("master_sha256", None)
        value.pop("cover_sha256", None)
        if value["instagram_id"] in sources:
            raise ReviewError("Duplicate known source ID.")
        sources[value["instagram_id"]] = value
    for media_id, source in archive_sources.items():
        old = sources.get(media_id)
        if old and old["bot_youtube_id"] != source["bot_youtube_id"]:
            raise ReviewError("Conflicting BOT source IDs for an Instagram reel.")
        sources[media_id] = source
    return sources


def approval_reasons(source, review):
    reasons = []
    if not source.get("master_verified"):
        reasons.append("Exact original master and cover unavailable or unverified")
    if not review or review.get("decision") != "approved":
        reasons.append("Full content review is not approved")
        return reasons
    if review.get("reviewed_video_sha256") != source.get("master_sha256") or review.get("reviewed_cover_sha256") != source.get("cover_sha256"):
        reasons.append("Review does not match the exact original video and cover hashes")
    if any((review.get("checks") or {}).get(key) is not True for key in REVIEW_CHECKS):
        reasons.append("Factual, spoken-audio, visual, buyer-usefulness or cover review is incomplete")
    flags = review.get("ai_flags") or {}
    if any(type(flags.get(key)) is not bool for key in AI_FLAGS) or flags.get("containsSyntheticMedia") is not True:
        reasons.append("Explicit AI flags and synthetic-media disclosure intent are incomplete")
    if flags.get("ai_face") is not False:
        reasons.append("AI-face adoption requires separate review; excluded from this weekly selector")
    if not isinstance(review.get("reviewed_at"), str) or not review.get("reviewer"):
        reasons.append("Dated review provenance is missing")
    else:
        stamp(review["reviewed_at"])
    return reasons


def review_batch(history, youtube_history, ledger, archive_sources, instagram, now):
    if not isinstance(history, list) or not isinstance(youtube_history, list):
        raise ReviewError("Expected existing engagement history arrays.")
    sources = source_pool(ledger, archive_sources)
    yt_by_id = {entry.get("video_id"): entry for entry in youtube_history}
    reviews = {}
    for review in ledger["reviews"]:
        media_id = review.get("instagram_id")
        if media_id in reviews or review.get("decision") not in ("rejected", "approved", "hold"):
            raise ReviewError("Ambiguous or invalid content review record.")
        reviews[media_id] = review
    excluded = {"unknown_origin_or_join": 0, "immature": 0, "already_promoted_or_reserved": 0}
    rows, seen = [], set()
    for record in history:
        media_id = record.get("media_id")
        if media_id in seen:
            raise ReviewError("Duplicate Instagram history source ID.")
        seen.add(media_id)
        source = sources.get(media_id)
        if not source or source["bot_youtube_id"] not in yt_by_id:
            excluded["unknown_origin_or_join"] += 1
            continue
        if now - stamp(record.get("published_at")) < timedelta(days=MIN_AGE_DAYS):
            excluded["immature"] += 1
            continue
        if any(p.get("instagram_id") == media_id or p.get("bot_youtube_id") == source["bot_youtube_id"]
               or (source.get("master_sha256") and p.get("video_sha256") == source["master_sha256"])
               for p in ledger["promotions"]):
            excluded["already_promoted_or_reserved"] += 1
            continue
        rows.append((record, source))
    rows.sort(key=lambda pair: stamp(pair[0]["published_at"]), reverse=True)
    rows = rows[:MAX_BATCH]
    owned = instagram.owned_media({record["media_id"] for record, _ in rows})
    results = []
    for record, source in rows:
        media_id = record["media_id"]
        live = owned[media_id]
        if live.get("media_type") != "VIDEO" or live.get("media_product_type") != "REELS":
            raise ReviewError("A joined source is not an Instagram Reel.")
        age_days = (now - stamp(live.get("timestamp"))).total_seconds() / 86400
        if age_days < MIN_AGE_DAYS:
            excluded["immature"] += 1
            continue
        metrics = instagram.snapshot(media_id)
        reach = metrics["reach"]
        review = reviews.get(media_id)
        rejected = bool(review and review.get("decision") == "rejected")
        reasons = approval_reasons(source, review) if not rejected else list(review.get("reasons") or ["Explicit content rejection"])
        if reach <= 0 or metrics["shares"] + metrics["saves"] <= 0:
            reasons.append("No observed sharing/saving evidence at nonzero reach")
        row = {"instagram_id": media_id, "bot_youtube_id": source["bot_youtube_id"],
               "title": record.get("title", ""), "permalink": live.get("permalink"),
               "bot_title": yt_by_id[source["bot_youtube_id"]].get("title", ""),
               "published_at": live["timestamp"], "age_days": round(age_days, 3),
               "trial": record.get("trial", False), "paid_organic_split": "unknown",
               "metrics": metrics, "shares_per_reach": metrics["shares"] / reach if reach else None,
               "saves_per_reach": metrics["saves"] / reach if reach else None,
               "workflow_run_id": source["workflow_run_id"], "artifact_id": source.get("artifact_id"),
               "master_sha256": source.get("master_sha256"), "master_verified": bool(source.get("master_verified")),
               "cover_sha256": source.get("cover_sha256"),
               "content_status": "REJECT" if rejected else ("REVIEW_PASSED" if not reasons else "HOLD"),
               "reasons": reasons}
        results.append(row)
    ranked = sorted((r for r in results if r["content_status"] != "REJECT"),
                    key=lambda r: (r["shares_per_reach"] or 0, r["saves_per_reach"] or 0), reverse=True)
    priority = ranked[0] if ranked else None
    candidate = None
    batch_reasons = []
    if len(results) < MIN_BATCH:
        batch_reasons.append("Fewer than7 positively joined mature outputs; do not force a1-in7–10 selection")
    if not priority:
        batch_reasons.append("No non-rejected joined mature source available for further review")
    elif priority["content_status"] != "REVIEW_PASSED":
        batch_reasons.append("Highest review-priority source has not passed all publication-content gates")
    elif not batch_reasons:
        candidate = {"instagram_id": priority["instagram_id"], "bot_youtube_id": priority["bot_youtube_id"],
                     "video_sha256": priority["master_sha256"], "cover_sha256": priority["cover_sha256"],
                     "action": "Manual MAIN scheduling review only; this tool never uploads"}
    return {"format": "selective-main-report-v1", "checked_at": now.isoformat(),
            "status": "CANDIDATE_FOR_MAIN_REVIEW" if candidate else "HOLD", "publishes": False,
            "batch_policy": {"minimum_age_days": MIN_AGE_DAYS, "minimum_batch": MIN_BATCH, "maximum_batch": MAX_BATCH,
                             "maximum_candidate": 1, "kind": "Proposed operating choices, not universal quality thresholds"},
            "metric_caveats": ["Lifetime snapshots at different ages, not a controlled day7 experiment",
                               "Paid versus organic split unavailable; do not call these organic-only results",
                               "Trial and ordinary reels retain their trial flag and are not equivalent exposure",
                               "Shares/reach then saves/reach determine review priority, not proven buyer conversion",
                               "No per-video sample/bulk sales attribution is established by this report"],
            "excluded_counts": excluded, "batch": results, "batch_hold_reasons": batch_reasons,
            "priority_for_full_review": {"instagram_id": priority["instagram_id"], "bot_youtube_id": priority["bot_youtube_id"],
                                         "status": priority["content_status"]} if priority else None,
            "candidate_for_main": candidate}


def save_report(report, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = ["## Weekly selective MAIN review", "", "**" + report["status"] + "** — no publishing.", ""]
    if report.get("error"):
        lines += [report["error"], ""]
    for reason in report.get("batch_hold_reasons", []):
        lines.append("- " + reason)
    lines += ["", "| BOT source | Content status | Age(days) | Reach | Shares/reach | Saves/reach | Trial |", "|---|---|---:|---:|---:|---:|---|"]
    for row in report.get("batch", []):
        shares = f"{100 * row['shares_per_reach']:.2f}%" if row["shares_per_reach"] is not None else "unavailable"
        saves = f"{100 * row['saves_per_reach']:.2f}%" if row["saves_per_reach"] is not None else "unavailable"
        lines.append(f"| {row['bot_youtube_id']} | {row['content_status']} | {row['age_days']:.1f} | {row['metrics']['reach']} | {shares} | {saves} | {row['trial']} |")
    priority = report.get("priority_for_full_review")
    lines += ["", "Priority for full review: " + (priority["bot_youtube_id"] if priority else "none"),
              "MAIN candidate: " + ((report.get("candidate_for_main") or {}).get("bot_youtube_id") or "none"), "",
              "These are mixed-age lifetime metrics; paid/organic split is unknown. Trial exposure is marked. A shortlist is not content approval or a scheduled MAIN upload.", ""]
    if priority:
        row = next(item for item in report["batch"] if item["instagram_id"] == priority["instagram_id"])
        lines += ["Review this exact BOT source title: " + row.get("bot_title", ""), ""]
        lines += ["- " + reason for reason in row["reasons"]]
        lines.append("")
    summary = "\n".join(lines)
    (folder / "summary.md").write_text(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ig-history", default="ig_engagement_history.json")
    parser.add_argument("--youtube-history", default="engagement_history.json")
    parser.add_argument("--reviews", default="main_promotion_reviews.json")
    parser.add_argument("--output-dir", default="selective-main-review-output")
    parser.add_argument("--fetch-archives", action="store_true")
    parser.add_argument("--local-archive", action="append", default=[])
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    try:
        archives, scanned = {}, []
        with tempfile.TemporaryDirectory(prefix="selective-review-") as temporary:
            if args.fetch_archives:
                archives, scanned = discover_archives(os.environ.get("GH_TOKEN", "").strip(), now, temporary)
            for path in args.local_archive:
                source = archive_source(path)
                if source:
                    archives[source["instagram_id"]] = source
            instagram = Instagram(os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip(), os.environ.get("INSTAGRAM_BUSINESS_ID", "").strip())
            report = review_batch(read_json(args.ig_history), read_json(args.youtube_history), read_json(args.reviews), archives, instagram, now)
        report["archive_scan"] = scanned
        save_report(report, args.output_dir)
        print(report["status"] + "; no publishing; report saved.")
        return 0
    except ReviewError as error:
        report = {"format": "selective-main-report-v1", "checked_at": now.isoformat(), "status": "ERROR",
                  "publishes": False, "error": str(error), "candidate_for_main": None, "priority_for_full_review": None}
        save_report(report, args.output_dir)
        print("ERROR; read-only review could not complete. See the nonsecret report.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
