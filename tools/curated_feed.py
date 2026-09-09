#!/usr/bin/env python3
"""Publish reviewed real-photo Instagram feed jobs; default to read-only validation."""

import argparse
import fcntl
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from PIL import Image


ACCOUNT_ID = "17841407981790313"
ACCOUNT_USERNAME = "bulkplaintshirt_com"
IST = ZoneInfo("Asia/Kolkata")
MAX_IMAGE_BYTES = 8_000_000
POLL_SECONDS = 60
WAIT_SECONDS = 300
JOB_FIELDS = {"id", "status", "publish_at", "caption", "image_urls", "image_sha256", "alt_texts"}
REMOTE_FIELDS = {"format", "mode", "job_id", "job_sha256", "account_id", "phase", "child_ids",
                 "pending", "parent_id", "media_id", "publish_attempt_at", "published_at"}


class FeedError(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise FeedError("Cannot read valid JSON: " + Path(path).name) from None
    if not isinstance(data, dict):
        raise FeedError("Expected a JSON object: " + Path(path).name)
    return data


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() != IST.utcoffset(parsed):
            raise ValueError()
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise FeedError("publish_at must be an ISO timestamp with +05:30 IST timezone.") from None


def load_job(path):
    data = read_json(path)
    if set(data) != JOB_FIELDS:
        raise FeedError("Use exactly the documented real-photo feed fields; AI flags are not supported.")
    if not isinstance(data["id"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", data["id"]):
        raise FeedError("Invalid feed job id.")
    if data["status"] != "reviewed":
        raise FeedError("Only status=reviewed jobs may enter the curated feed queue.")
    parse_time(data["publish_at"])
    if not isinstance(data["caption"], str) or not data["caption"].strip() or len(data["caption"]) > 2200:
        raise FeedError("Caption must contain 1–2200 characters.")
    urls, hashes, alts = data["image_urls"], data["image_sha256"], data["alt_texts"]
    if not all(isinstance(x, list) for x in (urls, hashes, alts)) or not 1 <= len(urls) <= 10 or len(hashes) != len(urls) or len(alts) != len(urls):
        raise FeedError("Provide 1 image or 2–10 carousel images with matching hashes and alt texts.")
    if any(not isinstance(url, str) for url in urls) or len(set(urls)) != len(urls):
        raise FeedError("Image URLs must be unique strings.")
    for url in urls:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise FeedError("Images require public HTTPS URLs without embedded credentials.")
    if any(not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes):
        raise FeedError("Each image needs its exact lowercase SHA256.")
    if len(set(hashes)) != len(hashes):
        raise FeedError("Duplicate image content is not a useful carousel.")
    if any(not isinstance(alt, str) or not alt.strip() or len(alt) > 1000 for alt in alts):
        raise FeedError("Each image needs 1–1000 characters of alt text.")
    return data


def validate_image_bytes(data, expected_hash):
    if not 0 < len(data) <= MAX_IMAGE_BYTES or hashlib.sha256(data).hexdigest() != expected_hash:
        raise FeedError("Image size or SHA256 does not match the reviewed asset.")
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format != "JPEG" or im.mode != "RGB":
                raise FeedError("Curated feed assets must be RGB JPEG images.")
            im.verify()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise FeedError("A reviewed image is not a valid JPEG.") from None


def validate_assets(job):
    for url, expected in zip(job["image_urls"], job["image_sha256"]):
        with requests.get(url, stream=True, timeout=30) as response:
            if response.status_code != 200 or response.headers.get("Content-Type", "").split(";")[0].lower() != "image/jpeg":
                raise FeedError("An image URL did not return HTTP 200 image/jpeg.")
            chunks, total = [], 0
            for chunk in response.iter_content(64 * 1024):
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise FeedError("A feed image exceeds 8 MB.")
                chunks.append(chunk)
            validate_image_bytes(b"".join(chunks), expected)


class S3StateBackend:
    def __init__(self, client, bucket, prefix):
        if bucket != "bulkplaintshirt.com" or prefix != "automation-state/ig-curated":
            raise FeedError("Unexpected curated state bucket or prefix.")
        self.client, self.bucket, self.prefix = client, bucket, prefix + "/"

    def read(self, filename):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self.prefix + filename)
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code == "NoSuchKey":
                return None, None
            raise FeedError("Remote feed state could not be read; local fallback is forbidden.") from None
        try:
            with response["Body"] as body:
                raw = body.read(65537)
            state = json.loads(raw)
            etag = response["ETag"]
            if len(raw) > 65536 or not isinstance(state, dict) or not isinstance(etag, str) or not etag:
                raise ValueError()
            if set(state) - REMOTE_FIELDS:
                raise ValueError()
            return state, etag
        except (ValueError, KeyError, TypeError):
            raise FeedError("Remote feed state is malformed; no local fallback or publication.") from None

    def save(self, filename, state, etag):
        if state.get("mode") != "publish" or set(state) - (REMOTE_FIELDS | {"permalink"}):
            raise FeedError("Only publish-state identifiers, hashes and timestamps may be saved remotely.")
        body = json.dumps({key: value for key, value in state.items() if key in REMOTE_FIELDS},
                          ensure_ascii=False, sort_keys=True).encode()
        condition = {"IfMatch": etag} if etag is not None else {"IfNoneMatch": "*"}
        try:
            response = self.client.put_object(Bucket=self.bucket, Key=self.prefix + filename,
                Body=body, ContentType="application/json", CacheControl="no-store", **condition)
            if not response.get("ETag"):
                raise ValueError()
            return response["ETag"]
        except Exception:
            raise FeedError("Remote feed state write was not confirmed or lost a race; no next Graph POST. Inspect remote state and the local recovery copy.") from None

    def history(self):
        records, continuation = [], None
        while True:
            args = {"Bucket": self.bucket, "Prefix": self.prefix}
            if continuation:
                args["ContinuationToken"] = continuation
            try:
                page = self.client.list_objects_v2(**args)
            except Exception:
                raise FeedError("Remote feed history could not be listed; no local fallback or publication.") from None
            for item in page.get("Contents", []):
                key = item.get("Key", "")
                filename = key[len(self.prefix):] if key.startswith(self.prefix) else ""
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}\.json", filename):
                    raise FeedError("Unexpected object in the curated state prefix.")
                state, _ = self.read(filename)
                if state is None:
                    raise FeedError("Remote feed history changed during inspection; stop before publication.")
                records.append(state)
            if not page.get("IsTruncated"):
                return records
            continuation = page.get("NextContinuationToken")
            if not continuation:
                raise FeedError("Remote feed history pagination is incomplete.")


def state_backend_from_environment():
    bucket = os.environ.get("CURATED_STATE_BUCKET", "").strip()
    if not bucket:
        return None
    prefix = os.environ.get("CURATED_STATE_PREFIX", "automation-state/ig-curated").strip().rstrip("/")
    import boto3
    from botocore.config import Config
    client = boto3.client("s3", config=Config(retries={"total_max_attempts": 1}))
    members = client.meta.service_model.operation_model("PutObject").input_shape.members
    if not {"IfMatch", "IfNoneMatch"}.issubset(members):
        raise FeedError("Installed boto3 lacks conditional S3 writes; no publication.")
    return S3StateBackend(client, bucket, prefix)


class StateStore:
    def __init__(self, path, backend=None):
        self.path = Path(path).resolve()
        self.backend = backend
        self.etag = None
        self.remote_read = False

    def read(self):
        if self.backend is not None:
            state, self.etag = self.backend.read(self.path.name)
            self.remote_read = True
            return state
        return read_json(self.path) if self.path.exists() else None

    def save(self, state):
        if self.backend is not None and not self.remote_read:
            raise FeedError("Remote state must be read before a conditional update.")
        self.save_local(state)
        if self.backend is not None:
            self.etag = self.backend.save(self.path.name, state, self.etag)

    def save_local(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp = tempfile.mkstemp(prefix=".feed-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
            parent = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


@contextmanager
def queue_lock(queue):
    key = hashlib.sha256(str(Path(queue).resolve()).encode()).hexdigest()[:24]
    descriptor = os.open(Path(tempfile.gettempdir()) / ("curated-feed-" + key + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise FeedError("Another process holds this feed queue.") from None
        yield
    finally:
        os.close(descriptor)


class Instagram:
    def __init__(self):
        token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
        self.account_id = os.environ.get("INSTAGRAM_BUSINESS_ID", "").strip()
        if not token or self.account_id != ACCOUNT_ID:
            raise FeedError("Missing token or wrong configured Instagram account.")
        self.session = requests.Session()
        self.session.headers["Authorization"] = "Bearer " + token

    def request(self, method, path, **kwargs):
        response = self.session.request(method, "https://graph.facebook.com/v21.0/" + path, timeout=60, **kwargs)
        try:
            data = response.json()
        except ValueError:
            raise FeedError("Graph response was not JSON; HTTP " + str(response.status_code)) from None
        if response.status_code >= 400 or "error" in data:
            error = data.get("error", {})
            raise FeedError("Graph rejected request: HTTP %s, code %s, subcode %s. Response bodies omitted." % (response.status_code, error.get("code"), error.get("error_subcode")))
        return data

    def identity(self):
        return self.request("GET", self.account_id, params={"fields": "id,username"})

    def create(self, data):
        return self.request("POST", self.account_id + "/media", data=data).get("id")

    def status(self, container_id):
        return self.request("GET", container_id, params={"fields": "status_code"}).get("status_code")

    def publish(self, container_id):
        return self.request("POST", self.account_id + "/media_publish", data={"creation_id": container_id}).get("id")

    def media(self, media_id):
        return self.request("GET", media_id, params={"fields": "id,username,caption,permalink"})


def require_account(api):
    identity = api.identity()
    if identity.get("id") != ACCOUNT_ID or identity.get("username") != ACCOUNT_USERNAME:
        raise FeedError("Live Instagram id/username differs from the configured account; refusing mutation.")


def bind_state(state, job, mode):
    if state is None:
        return {"format": "curated-feed-v1", "mode": mode, "job_id": job["id"], "job_sha256": digest(job),
                "account_id": ACCOUNT_ID, "phase": "validated", "child_ids": [], "pending": None}
    if state.get("format") != "curated-feed-v1" or state.get("account_id") != ACCOUNT_ID or state.get("mode") != mode:
        raise FeedError("Execution state belongs to another account or mode.")
    if state.get("job_id") != job["id"] or state.get("job_sha256") != digest(job):
        raise FeedError("Reviewed copy, schedule or image metadata changed; existing state cannot be reused.")
    if state.get("pending"):
        raise FeedError("Previous %s POST outcome is uncertain; inspect the saved state before any retry." % state["pending"])
    return state


def record_post(api_call, store, state, slot, data=None):
    state["pending"] = slot
    state["phase"] = slot + "_requested"
    store.save(state)
    result = api_call(data) if data is not None else api_call()
    if not isinstance(result, str) or not re.fullmatch(r"[0-9]+", result):
        raise FeedError("POST returned no valid ID; its outcome is uncertain and retries are blocked.")
    if slot.startswith("child_"):
        state["child_ids"].append(result)
    else:
        state[slot + "_id"] = result
    state["pending"] = None
    state["phase"] = slot + "_saved"
    store.save(state)
    return result


def wait_finished(api, container_id):
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        status = api.status(container_id)
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED", "PUBLISHED"):
            raise FeedError("Saved container is %s; automatic recreation/republication is blocked." % status)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FeedError("Saved container has not reached FINISHED; no publish call was made.")
        time.sleep(min(POLL_SECONDS, remaining))


def run_job(api, job, store, execute=False, prepare_only=False, clock=utc_now, assets_check=validate_assets):
    if prepare_only and store.backend is not None:
        raise FeedError("Prepare-only state must remain local; no S3 state writes are allowed.")
    mode = "prepare_only" if prepare_only else "publish"
    state = bind_state(store.read(), job, mode)
    due = parse_time(job["publish_at"])
    if execute and not prepare_only and due > clock():
        raise FeedError("This job is not due; publishing and container creation are blocked before its actual time.")
    require_account(api)
    assets_check(job)
    if not execute:
        return {"dry_run": True, "job_id": job["id"], "images": len(job["image_urls"]), "publish_at": job["publish_at"]}
    if state.get("media_id"):
        return state
    store.save(state)
    if len(job["image_urls"]) > 1:
        for i, (url, alt) in enumerate(zip(job["image_urls"], job["alt_texts"])):
            if i < len(state["child_ids"]):
                child = state["child_ids"][i]
            else:
                child = record_post(api.create, store, state, "child_" + str(i), {"image_url": url, "is_carousel_item": "true", "alt_text": alt})
            wait_finished(api, child)
        parent_body = {"media_type": "CAROUSEL", "children": ",".join(state["child_ids"]), "caption": job["caption"]}
    else:
        parent_body = {"image_url": job["image_urls"][0], "caption": job["caption"], "alt_text": job["alt_texts"][0]}
    parent = state.get("parent_id") or record_post(api.create, store, state, "parent", parent_body)
    wait_finished(api, parent)
    state["phase"] = "prepared" if prepare_only else "ready"
    store.save(state)
    if prepare_only:
        return state
    if due > clock():
        raise FeedError("Actual time is earlier than publish_at; refusing media_publish.")
    require_account(api)
    state["publish_attempt_at"] = clock().isoformat()
    media = record_post(lambda: api.publish(parent), store, state, "media")
    state["published_at"] = state["publish_attempt_at"]
    store.save(state)
    remote = api.media(media)
    if remote.get("id") != media or remote.get("username") != ACCOUNT_USERNAME or remote.get("caption") != job["caption"]:
        raise FeedError("Published media readback differs; ID is saved and publication must not be repeated.")
    state["permalink"] = remote.get("permalink", "")
    state["phase"] = "published"
    store.save(state)
    return state


def run_queue(queue, api_factory=Instagram, execute=False, clock=utc_now, assets_check=validate_assets, state_backend=None):
    queue = Path(queue)
    backend = state_backend if state_backend is not None else state_backend_from_environment()
    today = clock().astimezone(IST).date()
    jobs = [load_job(path) for path in sorted(queue.glob("*.json"))]
    if len({j["id"] for j in jobs}) != len(jobs):
        raise FeedError("Duplicate queue job IDs.")
    states = []
    for job in jobs:
        store = StateStore(queue / "state" / (job["id"] + ".json"), backend=backend)
        state = bind_state(store.read(), job, "publish")
        states.append((job, store, state))
    history = backend.history() if backend is not None else [read_json(path) for path in sorted((queue / "state").glob("*.json"))]
    for state in history:
        if state.get("format") != "curated-feed-v1" or state.get("account_id") != ACCOUNT_ID or state.get("mode") != "publish":
            raise FeedError("Unexpected execution history in the feed queue.")
        if state.get("pending"):
            raise FeedError("A saved POST outcome is uncertain; inspect queue history before another publication.")
        posted_at = state.get("published_at") or (state.get("publish_attempt_at") if state.get("media_id") else None)
        if posted_at and datetime.fromisoformat(posted_at).astimezone(IST).date() == today:
            return {"handled_today": True, "reason": "curated_already_published_today", "job_id": state["job_id"], "media_id": state["media_id"]}
    pending = [(job, store) for job, store, state in states if not state.get("media_id") and parse_time(job["publish_at"]) <= clock()]
    if pending:
        job, store = min(pending, key=lambda pair: parse_time(pair[0]["publish_at"]))
        state = run_job(api_factory(), job, store, execute=execute, clock=clock, assets_check=assets_check)
        return {"handled_today": True, "reason": "curated_published" if execute else "curated_due_dry_run", "job_id": job["id"], "media_id": state.get("media_id")}
    reserved = any(parse_time(job["publish_at"]).date() == today and not state.get("media_id") for job, _, state in states)
    return {"handled_today": reserved, "reason": "curated_reserved_later_today" if reserved else "standard_fallback"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-dir", type=Path, default=Path("feed_queue"))
    parser.add_argument("--job", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.prepare_only and (not args.job or not args.state):
        parser.error("--prepare-only requires --job and a separate --state")
    if args.state and not args.prepare_only:
        parser.error("--state is only for isolated --prepare-only checks")
    if args.job and args.execute and not args.prepare_only:
        parser.error("Execution must use the whole queue so the one-per-day guard applies")
    if args.prepare_only and (args.state.resolve().is_relative_to(args.queue_dir.resolve()) or args.state.resolve().is_relative_to(args.job.resolve().parent)):
        parser.error("Prepare-only state must be outside the queue/job directory")
    try:
        with queue_lock(args.queue_dir):
            if args.job:
                job = load_job(args.job)
                state_path = args.state or args.queue_dir / "state" / (job["id"] + ".json")
                backend = None if args.prepare_only else state_backend_from_environment()
                result = run_job(Instagram(), job, StateStore(state_path, backend=backend), execute=args.execute, prepare_only=args.prepare_only)
            else:
                result = run_queue(args.queue_dir, execute=args.execute)
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as stream:
                stream.write("handled_today=" + str(result.get("handled_today", False)).lower() + "\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except FeedError as error:
        print("Curated feed stopped: " + str(error), file=sys.stderr)
    except Exception as error:
        print("Curated feed stopped: " + type(error).__name__ + "; credentials and response bodies omitted. Saved state must be reused.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
