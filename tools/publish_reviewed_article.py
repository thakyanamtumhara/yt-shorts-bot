#!/usr/bin/env python3
"""Publish an authenticated, reviewed article without invoking generation code."""
import argparse
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.request import urlopen
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

BUCKET = "bulkplaintshirt.com"
DISTRIBUTION = "E21QLU9SBUBY7Z"
BASE = "https://www.bulkplaintshirt.com"
IST = ZoneInfo("Asia/Kolkata")
AGGREGATES = ("p/index.html", "p/map.xml", "p/feed.xml")
PREFIX = "p/automation-state-reviewed-articles/"


class Refused(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise Refused(message)


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "Timestamp needs a timezone")
    return result


def header(job):
    return {k: v for k, v in job.items() if k not in ("nonce", "ciphertext")}


def validate_job(job):
    require(set(job) == {"schema_version", "id", "status", "publish_at", "source_video_id", "slug", "payload_sha256", "nonce", "ciphertext"}, "Unexpected queue schema")
    require(job["schema_version"] == 1 and job["status"] == "reviewed", "Article is not reviewed")
    require(bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", job["id"])), "Invalid job ID")
    require(bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,160}", job["slug"])), "Invalid slug")
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", job["source_video_id"])), "Invalid video ID")
    require(bool(re.fullmatch(r"[a-f0-9]{64}", job["payload_sha256"])), "Invalid payload hash")
    timestamp(job["publish_at"])


def secret_key():
    try:
        key = base64.b64decode(os.environ["REVIEWED_ARTICLE_KEY"], validate=True)
    except Exception:
        raise Refused("REVIEWED_ARTICLE_KEY must be a base64 AES-256 key") from None
    require(len(key) == 32, "REVIEWED_ARTICLE_KEY must contain 32 bytes")
    return key


def encrypt_payload(meta, payload, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    plain = canonical(payload)
    job = {**meta, "payload_sha256": digest(plain)}
    nonce = os.urandom(12)
    encrypted = AESGCM(key).encrypt(nonce, plain, canonical(job))
    job.update(nonce=base64.b64encode(nonce).decode(), ciphertext=base64.b64encode(encrypted).decode())
    validate_job(job)
    return job


def decrypt_payload(job, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    validate_job(job)
    try:
        plain = AESGCM(key).decrypt(base64.b64decode(job["nonce"], validate=True), base64.b64decode(job["ciphertext"], validate=True), canonical(header(job)))
    except Exception:
        raise Refused("Draft authentication failed") from None
    require(digest(plain) == job["payload_sha256"], "Payload hash mismatch")
    payload = json.loads(plain)
    validate_payload(job, payload)
    return payload


def validate_payload(job, payload):
    require(set(payload) == {"files", "card_html", "history"}, "Unexpected payload schema")
    slug = job["slug"]
    expected = {f"p/{slug}.html", f"p/{slug}-hero.webp", f"p/{slug}-stock-room.jpg"}
    require(len(payload["files"]) == 3 and {f["key"] for f in payload["files"]} == expected, "Unexpected article destinations")
    types = {".html": "text/html; charset=utf-8", ".webp": "image/webp", ".jpg": "image/jpeg"}
    for item in payload["files"]:
        data = base64.b64decode(item["data"], validate=True)
        require(digest(data) == item["sha256"] and 0 < len(data) < 8_000_000, "Article asset hash or size mismatch")
        require(item["content_type"] == types[Path(item["key"]).suffix], "Incorrect content type")
        if item["key"].endswith(".html"):
            text = data.decode()
            require("<!doctype html>" in text.lower() and "noindex" not in text.lower(), "Expected full production HTML")
            require(f'{BASE}/p/{slug}.html' in text and job["source_video_id"] in text, "Article canonical or source missing")
    entry = payload["history"]
    require(entry["slug"] == slug and entry["url"] == f"{BASE}/p/{slug}.html", "History URL mismatch")
    require(entry["vid_url"] == f'https://www.youtube.com/watch?v={job["source_video_id"]}', "History source mismatch")
    require(entry["date"] == job["publish_at"], "History date mismatch")
    card = payload["card_html"]
    require(len(re.findall(r'<article\b', card)) == 1 and card.count('class="post-card"') == 1 and f'/p/{slug}.html' in card, "Expected one reviewed card")


def pack(manifest_path, output, key):
    path = Path(manifest_path)
    manifest = json.loads(path.read_text())
    require(manifest.get("reviewed") is True, "Set reviewed=true only after reviewing the production draft")
    files = []
    sources = [{"path": manifest["html_path"], "destination": f'p/{manifest["slug"]}.html', "sha256": manifest["html_sha256"], "content_type": "text/html; charset=utf-8"}, *manifest["assets"]]
    for item in sources:
        source = (path.parent / item["path"]).resolve()
        require(source.parent == path.parent.resolve(), "Draft files must be next to manifest")
        data = source.read_bytes()
        require(digest(data) == item["sha256"], "Reviewed file changed")
        files.append({"key": item["destination"], "content_type": item["content_type"], "sha256": digest(data), "data": base64.b64encode(data).decode()})
    payload = {"files": files, "card_html": (path.parent / "index-card.html").read_text(), "history": manifest["index_card"]}
    meta = {"schema_version": 1, "status": "reviewed", **{k: manifest[k] for k in ("id", "publish_at", "source_video_id", "slug")}}
    job = encrypt_payload(meta, payload, key)
    validate_payload(job, payload)
    output = Path(output)
    require(not output.exists(), "Encrypted queue file already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(job, indent=2) + "\n")


def append_index(data, payload):
    text = data.decode()
    entry = payload["history"]
    relative = f'/p/{entry["slug"]}.html'
    require(relative not in text, "Article already present in an unrecorded index")
    cards_before = re.findall(r'<article\b.*?</article>', text, re.S)
    require(bool(cards_before), "Index contains no article cards")
    marker = '<section class="posts-grid" id="posts">'
    require(text.count(marker) == 1, "Unknown index card layout")
    text = text.replace(marker, marker + "\n" + payload["card_html"], 1)
    text, count = re.subn(r'(<p class="post-count">)\d+( articles published</p>)', lambda m: f'{m[1]}{len(cards_before) + 1}{m[2]}', text)
    require(count == 1, "Unknown article count layout")
    footer = '<h2>All Articles</h2>\n        <ul>'
    require(text.count(footer) == 1, "Unknown article list layout")
    text = text.replace(footer, footer + f'\n<li><a href="{relative}">{html.escape(entry["title"])}</a></li>', 1)
    changed = 0
    def update_schema(match):
        nonlocal changed
        obj = json.loads(match[1])
        entity = obj.get("mainEntity", {})
        if obj.get("@type") != "CollectionPage" or entity.get("@type") != "ItemList":
            return match[0]
        items = entity["itemListElement"]
        require(not any(i.get("url") == entry["url"] for i in items), "Duplicate structured URL")
        position = next((i for i, item in enumerate(items) if "/p/" in item.get("url", "") and item["url"].endswith(".html") and not item["url"].endswith("/FQA.html")), len(items))
        items.insert(position, {"@type": "ListItem", "position": 0, "url": entry["url"]})
        for index, item in enumerate(items, 1):
            item["position"] = index
        entity["numberOfItems"] = len(items)
        changed += 1
        return '<script type="application/ld+json">' + json.dumps(obj, ensure_ascii=False) + '</script>'
    text = re.sub(r'<script type="application/ld\+json">(.*?)</script>', update_schema, text, flags=re.S)
    require(changed == 1, "Unknown index structured data")
    require(all(block in text for block in cards_before), "Existing cards changed")
    return text.encode()


def append_xml(key, data, payload):
    text = data.decode()
    entry = payload["history"]
    root = ET.fromstring(text)
    url = entry["url"]
    require(not any(node.text == url for node in root.iter()), "Article already present in unrecorded XML")
    esc = html.escape
    if key == "p/map.xml":
        require(root.tag.endswith("urlset") and text.count("</urlset>") == 1, "Unknown sitemap format")
        addition = f'<url><loc>{esc(url)}</loc><lastmod>{timestamp(entry["date"]).date()}</lastmod><changefreq>monthly</changefreq><priority>0.8</priority></url>\n'
        result = text.replace("</urlset>", addition + "</urlset>", 1)
    else:
        require(root.tag == "rss" and text.count("</channel>") == 1, "Unknown RSS format")
        hero = next(item for item in payload["files"] if item["key"] == f'p/{entry["slug"]}-hero.webp')
        length = len(base64.b64decode(hero["data"]))
        addition = f'<item><title>{esc(entry["title"])}</title><link>{esc(url)}</link><guid isPermaLink="true">{esc(url)}</guid><description>{esc(entry["description"])}</description><pubDate>{format_datetime(timestamp(entry["date"]))}</pubDate><enclosure url="{BASE}/p/{entry["slug"]}-hero.webp" type="image/webp" length="{length}"/></item>\n'
        marker = re.search(r'<item\b', text)
        result = text[:marker.start()] + addition + text[marker.start():] if marker else text.replace("</channel>", addition + "</channel>", 1)
        result = re.sub(r'<lastBuildDate>.*?</lastBuildDate>', f'<lastBuildDate>{format_datetime(timestamp(entry["date"]))}</lastBuildDate>', result, count=1)
    ET.fromstring(result)
    return result.encode()


class Storage:
    def __init__(self, client):
        self.client = client

    def get(self, key):
        try:
            listing = self.client.list_objects_v2(Bucket=BUCKET, Prefix=key, MaxKeys=1)
            if not any(item["Key"] == key for item in listing.get("Contents", [])):
                return None
            obj = self.client.get_object(Bucket=BUCKET, Key=key)
        except Exception:
            raise Refused("S3 read failed for " + key) from None
        return {"data": obj["Body"].read(), "etag": obj["ETag"], "content_type": obj.get("ContentType", "application/octet-stream"), "cache_control": obj.get("CacheControl", "no-cache")}

    def put(self, key, data, content_type, etag=None, cache_control="no-cache"):
        condition = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
        try:
            result = self.client.put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=content_type, CacheControl=cache_control, **condition)
        except Exception:
            raise Refused("S3 conditional write uncertain or refused for " + key + "; rerun reconciles by hash") from None
        return result["ETag"]

    def delete(self, key, etag):
        try:
            self.client.delete_object(Bucket=BUCKET, Key=key, IfMatch=etag)
        except Exception:
            raise Refused("S3 conditional deletion uncertain or refused for " + key) from None


class Transaction:
    def __init__(self, storage, job):
        self.storage = storage
        self.job = job
        self.key = PREFIX + job["id"] + "/receipt.json"
        obj = storage.get(self.key)
        self.etag = obj["etag"] if obj else None
        self.state = json.loads(obj["data"]) if obj else {"job_sha256": digest(canonical(job)), "phase": "started", "steps": []}
        require(self.state["job_sha256"] == digest(canonical(job)), "Queue changed after transaction started")

    def save(self):
        self.etag = self.storage.put(self.key, canonical(self.state), "application/json", self.etag)

    def write(self, key, body, content_type, aggregate=False, expected_previous=None):
        step = next((s for s in self.state["steps"] if s["key"] == key), None)
        if step is None:
            old = self.storage.get(key)
            require((old is not None) == aggregate, "Unexpected existing or missing destination: " + key)
            if aggregate:
                require(digest(old["data"]) == expected_previous, "Aggregate changed after planning: " + key)
            step = {"key": key, "sha256": digest(body), "phase": "pending", "previous_sha256": digest(old["data"]) if old else None, "previous_etag": old["etag"] if old else None}
            if old:
                backup = PREFIX + self.job["id"] + "/backup/" + key
                existing = self.storage.get(backup)
                if existing:
                    require(existing["data"] == old["data"], "Backup collision")
                else:
                    self.storage.put(backup, old["data"], old["content_type"], cache_control=old["cache_control"])
                require(self.storage.get(backup)["data"] == old["data"], "Backup verification failed")
                step.update(backup_key=backup, previous_content_type=old["content_type"], previous_cache_control=old["cache_control"])
            self.state["steps"].append(step)
            self.save()
        require(step["sha256"] == digest(body), "Planned output changed")
        current = self.storage.get(key)
        if current and digest(current["data"]) == step["sha256"]:
            step["phase"] = "written"
            self.save()
            return
        require(step["phase"] == "pending", "Completed output changed: " + key)
        require((digest(current["data"]) if current else None) == step["previous_sha256"], "Destination changed concurrently: " + key)
        self.storage.put(key, body, content_type, step["previous_etag"])
        actual = self.storage.get(key)
        require(actual and digest(actual["data"]) == step["sha256"], "S3 write verification failed")
        step["phase"] = "written"
        self.save()

    def aggregate_source(self, key):
        step = next((s for s in self.state["steps"] if s["key"] == key), None)
        obj = self.storage.get(step["backup_key"] if step else key)
        require(obj is not None, "Missing aggregate or backup")
        if step:
            require(digest(obj["data"]) == step["previous_sha256"], "Backup hash mismatch")
        return obj

    def undo(self):
        require(bool(self.etag), "No active owned transaction to undo")
        if self.state["phase"] == "rolled_back":
            return
        for step in self.state["steps"]:
            current = self.storage.get(step["key"])
            require((digest(current["data"]) if current else None) in (step["sha256"], step["previous_sha256"]), "Rollback would overwrite later work: " + step["key"])
            if step.get("backup_key"):
                backup = self.storage.get(step["backup_key"])
                require(backup and digest(backup["data"]) == step["previous_sha256"], "Rollback backup unavailable")
        self.state["phase"] = "rolling_back"
        self.save()
        for step in reversed(self.state["steps"]):
            current = self.storage.get(step["key"])
            if (digest(current["data"]) if current else None) != step["previous_sha256"]:
                if step.get("backup_key"):
                    old = self.storage.get(step["backup_key"])
                    self.storage.put(step["key"], old["data"], step["previous_content_type"], current["etag"], step["previous_cache_control"])
                else:
                    self.storage.delete(step["key"], current["etag"])
            actual = self.storage.get(step["key"])
            require((digest(actual["data"]) if actual else None) == step["previous_sha256"], "Rollback verification failed")
            step["phase"] = "rolled_back"
            self.save()
        self.state["phase"] = "rolled_back"
        self.save()


def source_available(video_id):
    key = os.environ.get("YOUTUBE_API_KEY_1", "").strip()
    require(bool(key), "YouTube source verification unavailable; YOUTUBE_API_KEY_1 is missing")
    try:
        query = urlencode({"id": video_id, "part": "status", "fields": "items(id,status/privacyStatus)", "key": key})
        with urlopen("https://www.googleapis.com/youtube/v3/videos?" + query, timeout=20) as response:
            require(response.status == 200, "Invalid API status")
            body = json.load(response)
            require(isinstance(body, dict) and isinstance(body.get("items"), list) and "error" not in body, "Invalid API response")
            items = body["items"]
            if not items:
                return False
            require(len(items) == 1 and items[0]["id"] == video_id, "Unexpected API video")
            privacy = items[0]["status"]["privacyStatus"]
            require(privacy in ("public", "private", "unlisted"), "Invalid privacy status")
            if privacy != "public":
                return False
    except Exception:
        raise Refused("YouTube source verification unavailable; API request, credentials or response validation failed") from None
    try:
        with urlopen(f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json", timeout=20) as response:
            require(response.status == 200 and json.load(response).get("type") == "video", "Invalid oEmbed response")
    except Exception:
        raise Refused("YouTube source verification unavailable; public video's oEmbed check failed") from None
    return True


def save_history(path, entry, remove=False):
    path = Path(path)
    history = json.loads(path.read_text())
    require(isinstance(history, list), "Invalid blog history")
    existing = [p for p in history if p.get("slug") == entry["slug"]]
    require(not existing or existing == [entry], "Existing history entry differs")
    if remove:
        updated = [p for p in history if p.get("slug") != entry["slug"]]
    else:
        updated = history if existing else history + [entry]
    if updated != history:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
        temporary.replace(path)


def publish(job, payload, storage, history_path, now, public_check=source_available):
    require(now >= timestamp(job["publish_at"]), "Article is not due")
    validate_payload(job, payload)
    require(public_check(job["source_video_id"]), "Source YouTube video is not publicly available; standard recap must stay skipped")
    history = json.loads(Path(history_path).read_text())
    require(isinstance(history, list), "Invalid blog history")
    matching = [entry for entry in history if entry.get("slug") == job["slug"]]
    require(not matching or matching == [payload["history"]], "Existing history entry differs")
    tx = Transaction(storage, job)
    require(tx.state["phase"] not in ("rolling_back", "rolled_back"), "Transaction was rolled back")
    if tx.state["phase"] == "published":
        save_history(history_path, payload["history"])
        return tx
    require(now.astimezone(IST).date() == timestamp(job["publish_at"]).astimezone(IST).date(), "Publication day changed; review a new dated draft before publishing")
    plans = []
    for key in AGGREGATES:
        old = tx.aggregate_source(key)
        body = append_index(old["data"], payload) if key == "p/index.html" else append_xml(key, old["data"], payload)
        plans.append((key, old, body))
    for item in payload["files"]:
        if not any(step["key"] == item["key"] for step in tx.state["steps"]):
            require(storage.get(item["key"]) is None, "Unexpected existing destination: " + item["key"])
    if tx.etag is None:
        tx.save()
    files = sorted(payload["files"], key=lambda item: item["key"].endswith(".html"))
    for item in files:
        tx.write(item["key"], base64.b64decode(item["data"]), item["content_type"])
    for key, old, body in plans:
        tx.write(key, body, old["content_type"], aggregate=True, expected_previous=digest(old["data"]))
    tx.state["phase"] = "published"
    tx.save()
    save_history(history_path, payload["history"])
    return tx


def verify_only(job, payload, storage, now, public_check=source_available):
    validate_payload(job, payload)
    tx = Transaction(storage, job)
    for item in payload["files"]:
        current = storage.get(item["key"])
        owned = next((step for step in tx.state["steps"] if step["key"] == item["key"]), None)
        require(current is None or (owned and digest(current["data"]) == item["sha256"]), "Article destination collision")
    for key in AGGREGATES:
        obj = tx.aggregate_source(key)
        if key == "p/index.html":
            append_index(obj["data"], payload)
        else:
            append_xml(key, obj["data"], payload)
    public = public_check(job["source_video_id"])
    future = now < timestamp(job["publish_at"])
    require(future or public, "Due source is not public")
    return {"id": job["id"], "source_public": public, "future_guard_active": future, "destinations_checked": 6, "writes": 0}


def invalidate(client, tx, undo=False):
    paths = ["/" + step["key"] for step in tx.state["steps"]]
    paths += ["/p/"]
    result = client.create_invalidation(DistributionId=DISTRIBUTION, InvalidationBatch={"Paths": {"Quantity": len(paths), "Items": paths}, "CallerReference": f'reviewed-{tx.job["id"]}-{tx.job["payload_sha256"][:20]}-{"undo" if undo else "publish"}'})
    tx.state["invalidation_id"] = result["Invalidation"]["Id"]
    tx.save()


def queue_choice(jobs, now):
    for job in jobs:
        validate_job(job)
    require(len({j["id"] for j in jobs}) == len(jobs), "Duplicate job ID")
    dates = [timestamp(j["publish_at"]).astimezone(IST).date() for j in jobs]
    require(len(set(dates)) == len(dates), "Only one reviewed article per IST day")
    today = now.astimezone(IST).date()
    due = [j for j in jobs if timestamp(j["publish_at"]) <= now]
    reserved = any(day == today for day in dates)
    return sorted(due, key=lambda j: timestamp(j["publish_at"])), reserved


def output(handled, status, **details):
    print(json.dumps({"handled_today": handled, "status": status, **details}))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as file:
            file.write(f'handled_today={str(handled).lower()}\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-dir", default="reviewed_articles")
    parser.add_argument("--history", default="blog_history.json")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--pack", metavar="REVIEWED_MANIFEST")
    parser.add_argument("--output")
    parser.add_argument("--undo", metavar="JOB_ID")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    require(not args.verify_only or not (args.execute or args.undo or args.pack), "Verify-only cannot mutate")
    if args.pack:
        require(args.output and not args.execute and not args.undo, "Pack needs --output and cannot publish")
        pack(args.pack, args.output, secret_key())
        output(False, "encrypted_queue_written")
        return
    jobs = [json.loads(path.read_text()) for path in sorted(Path(args.queue_dir).glob("*.json"))]
    now = datetime.now(timezone.utc)
    due, reserved = queue_choice(jobs, now)
    if args.undo:
        require(args.execute, "Undo requires --execute")
        due = [job for job in jobs if job["id"] == args.undo]
        require(len(due) == 1, "Undo ID must belong to this queue")
    if args.verify_only:
        due = jobs
    elif not due or not args.execute:
        output(reserved, "reserved_future" if reserved else "dry_run" if due else "no_reviewed_article")
        return
    import boto3
    from botocore.config import Config
    config = Config(retries={"max_attempts": 0})
    storage = Storage(boto3.client("s3", config=config))
    if args.verify_only:
        checks = [verify_only(job, decrypt_payload(job, secret_key()), storage, now) for job in due]
        output(True, "verified_without_writes", checks=checks)
        return
    cloudfront = boto3.client("cloudfront", config=config)
    handled = reserved
    for job in due:
        payload = decrypt_payload(job, secret_key())
        if args.undo:
            tx = Transaction(storage, job)
            tx.undo()
            save_history(args.history, payload["history"], remove=True)
            invalidate(cloudfront, tx, undo=True)
            output(True, "rolled_back")
            return
        existing = Transaction(storage, job)
        if existing.state["phase"] == "rolled_back":
            continue
        already_done = existing.state["phase"] == "published"
        if already_done:
            save_history(args.history, payload["history"])
            tx = existing
        else:
            tx = publish(job, payload, storage, args.history, now)
            handled = True
        if not tx.state.get("invalidation_id"):
            invalidate(cloudfront, tx)
    output(handled, "reviewed_articles_checked")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Reviewed article stopped: " + (str(error) if isinstance(error, Refused) else type(error).__name__), file=sys.stderr)
        sys.exit(1)
