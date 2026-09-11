#!/usr/bin/env python3
"""Schedule one finished short on Facebook, X and LinkedIn, with durable receipts."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit
import urllib.error
import urllib.request

import vizard_publish as vizard


HOSTS = {"bulkplaintshirt.com", "www.bulkplaintshirt.com"}
PLATFORMS = ("fb", "x", "li")


class BatchError(Exception):
    pass


def owned_url(url):
    if not isinstance(url, str):
        raise BatchError("video_url must be an owned HTTPS MP4 URL")
    parts = urlsplit(url)
    path = unquote(parts.path)
    if (parts.scheme != "https" or parts.hostname not in HOSTS
            or parts.username or parts.password or parts.port not in (None, 443)
            or parts.fragment or not path.startswith("/p/")
            or not path.lower().endswith(".mp4") or ".." in path.split("/")):
        raise BatchError("video_url must be an HTTPS MP4 under bulkplaintshirt.com/p/")
    return url


def probe_video(path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
             "-of", "json", str(path)], check=True, capture_output=True, text=True)
        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])
        if not any(stream.get("codec_type") == "video" for stream in data["streams"]):
            raise ValueError("no video stream")
        if not 3 <= duration < 140:
            raise ValueError("duration must be at least 3 and strictly below 140 seconds")
        return duration
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        raise BatchError(f"Invalid local video: {exc}") from exc


def check_asset(url, size):
    request = urllib.request.Request(url, headers={"Range": "bytes=0-31",
        "User-Agent": "Mozilla/5.0 (compatible; Sale91VideoPreflight/1.0)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        owned_url(response.geturl())
        if response.status == 206:
            content_range = re.fullmatch(r"bytes 0-31/(\d+)", response.headers.get("Content-Range", ""))
            if not content_range or int(content_range.group(1)) != size:
                raise BatchError("Hosted partial asset must report the local total byte length")
        elif response.status != 200 or response.headers.get("Content-Length") != str(size):
            raise BatchError("Hosted asset must return HTTP 206 or 200 and match the local byte length")
        mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if mime not in {"video/mp4", "application/octet-stream"}:
            raise BatchError(f"Hosted asset is not served as video: {mime}")
        if response.read(32)[4:8] != b"ftyp":
            raise BatchError("Hosted asset does not begin with an MP4 file-type box")


def load_manifest(path, probe=probe_video, platforms=PLATFORMS):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(data) != {"name", "local_video_path", "video_url", "legs"}:
        raise BatchError("Manifest requires exactly name, local_video_path, video_url and legs")
    if not isinstance(data["name"], str) or not data["name"].strip():
        raise BatchError("name must be nonempty")
    video = Path(data["local_video_path"]).expanduser().resolve(strict=True)
    if not video.is_file() or video.stat().st_size == 0:
        raise BatchError("local_video_path must be a nonempty file")
    if video.stat().st_size > 512 * 1024 * 1024:
        raise BatchError("Video exceeds X's 512 MB upload limit")
    owned_url(data["video_url"])
    if not platforms or len(set(platforms)) != len(platforms) or not set(platforms).issubset(PLATFORMS):
        raise BatchError("Choose unique supported destination platforms")
    if not isinstance(data["legs"], dict) or set(data["legs"]) != set(platforms):
        raise BatchError("Manifest legs must exactly match the requested platforms: " + ", ".join(platforms))
    for platform, leg in data["legs"].items():
        if not isinstance(leg, dict) or set(leg) != {"post", "when_ist"}:
            raise BatchError(f"{platform}: requires exactly post and when_ist")
        post = leg["post"]
        if not isinstance(post, str) or not post.strip() or len(post) > vizard.CHAR_LIMITS[platform]:
            raise BatchError(f"{platform}: post must have 1–{vizard.CHAR_LIMITS[platform]} characters")
        if platform == "li" and any(character in post for character in "\\()"):
            raise BatchError("li: Vizard does not support backslashes or parentheses in API copy")
        if platform == "x":
            weighted_text = re.sub(r"https?://\S+|(?<![\w@])(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/\S*)?",
                                   "x" * 23, post)
            weight = sum(1 if ord(c) <= 0x10ff or 0x2000 <= ord(c) <= 0x200d
                         or 0x2010 <= ord(c) <= 0x201f or 0x2032 <= ord(c) <= 0x2037
                         else 2 for c in weighted_text)
            if weight > 280:
                raise BatchError("x: conservative weighted count exceeds 280 after link shortening")
        try:
            millis, parsed = vizard.ist_millis(leg["when_ist"])
            if parsed.strftime("%Y-%m-%d %H:%M") != leg["when_ist"]:
                raise ValueError("use exact date format")
        except (TypeError, ValueError, AssertionError) as exc:
            raise BatchError(f"{platform}: invalid when_ist; use YYYY-MM-DD HH:MM in IST") from exc
        leg["publishTime"] = millis
        leg["socialAccountId"] = vizard.ACCOUNTS[platform][0]
    data["local_video_path"] = str(video)
    data["duration"] = probe(video)
    data["size"] = video.stat().st_size
    digest = hashlib.sha256()
    with video.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    data["video_sha256"] = digest.hexdigest()
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return data, fingerprint


class State:
    def __init__(self, path, fingerprint):
        self.path = Path(path).expanduser().resolve()
        self.fingerprint = fingerprint
        self.data = {"version": 1, "fingerprint": fingerprint, "legs": {}}

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = open(str(self.path) + ".lock", "a+")
        os.chmod(self.lock.name, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.exists():
                self.data = json.loads(self.path.read_text())
                if self.data.get("version") != 1 or self.data.get("fingerprint") != self.fingerprint:
                    raise BatchError("State belongs to a changed manifest or video; refusing another submission")
            return self
        except BaseException:
            self.lock.close()
            raise

    def save(self):
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".vizard-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                json.dump(self.data, target, indent=2, ensure_ascii=False)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(name, self.path)
            parent_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def __exit__(self, *args):
        self.lock.close()


def validate_pending(manifest, state, now):
    if state.get("create_status") == "started":
        raise BatchError("Create outcome is unconfirmed; inspect Vizard before any manual recovery")
    if state.get("create_status") == "blocked":
        raise BatchError("Create was not confirmed; saved receipt requires manual inspection")
    for platform in manifest["legs"]:
        leg_state = state["legs"].get(platform, {})
        if leg_state.get("status") in {"started", "blocked"}:
            raise BatchError(f"{platform}: publish outcome needs manual inspection; automatic duplicate blocked")
        if leg_state.get("status") != "accepted" and manifest["legs"][platform]["publishTime"] <= int(now() * 1000):
            raise BatchError(f"{platform}: pending publish time must be in the future (IST)")


def valid_id(value):
    return not isinstance(value, bool) and isinstance(value, (int, str)) and str(value).isdigit() and int(value) > 0


def error_receipt(exc):
    result = {"error": type(exc).__name__, "message": str(exc)}
    if isinstance(exc, urllib.error.HTTPError):
        try:
            result.update({"http_status": exc.code, "response": exc.read(16384).decode("utf-8", errors="replace")})
        finally:
            exc.close()
    return result


def reuse_ingest(manifest, source_manifest_path, source_state_path):
    source_raw = json.loads(Path(source_manifest_path).read_text())
    source, fingerprint = load_manifest(source_manifest_path, platforms=tuple(source_raw.get("legs", {})))
    saved = json.loads(Path(source_state_path).read_text())
    if saved.get("version") != 1 or saved.get("fingerprint") != fingerprint:
        raise BatchError("Source state does not match its original manifest and video")
    if source["video_sha256"] != manifest["video_sha256"] or source["video_url"] != manifest["video_url"]:
        raise BatchError("Reused ingestion must reference the same exact video bytes and URL")
    if set(source["legs"]) & set(manifest["legs"]):
        raise BatchError("Reused ingestion must add different destination platforms")
    request = saved.get("create_request", {})
    videos = saved.get("query_receipt", {}).get("videos", [])
    if (saved.get("create_status") != "accepted" or not valid_id(saved.get("projectId"))
            or not valid_id(saved.get("finalVideoId"))
            or request.get("videoUrl") != manifest["video_url"] or request.get("getClips") != 0
            or saved.get("query_receipt", {}).get("code") != 2000 or len(videos) != 1
            or videos[0].get("videoId") != saved["finalVideoId"]
            or saved.get("create_receipt", {}).get("projectId") != saved["projectId"]):
        raise BatchError("Source ingestion has no confirmed single finished video")
    if any(request.get(key) != 0 for key in ("subtitleSwitch", "headlineSwitch", "emojiSwitch",
            "highlightSwitch", "autoBrollSwitch", "removeSilenceSwitch")):
        raise BatchError("Reused ingestion must preserve the finished edit")
    return {key: saved[key] for key in ("create_status", "create_request", "create_receipt",
                                       "projectId", "query_receipt", "finalVideoId")}


def run(manifest, fingerprint, state_path, *, execute=False, api=vizard.call,
        asset_check=check_asset, now=time.time, sleep=time.sleep, poll_seconds=30, wait_seconds=1200,
        reused_ingest=None):
    with State(state_path, fingerprint) as store:
        state = store.data
        validate_pending(manifest, state, now)
        asset_check(manifest["video_url"], manifest["size"])
        if not execute:
            return {"mode": "dry-run", "name": manifest["name"], "duration": manifest["duration"],
                    "legs": {p: state["legs"].get(p, {}).get("status", "pending") for p in manifest["legs"]}}
        if reused_ingest:
            if state.get("projectId") and (state["projectId"] != reused_ingest["projectId"]
                    or state.get("finalVideoId") != reused_ingest["finalVideoId"]):
                raise BatchError("Target state already references a different ingestion")
            if not state.get("projectId"):
                state.update(reused_ingest)
                state["reused_ingestion"] = True
                store.save()
        if not state.get("projectId"):
            validate_pending(manifest, state, now)
            body = {"lang": "hi", "preferLength": [0], "videoUrl": manifest["video_url"],
                    "videoType": 1, "ext": "mp4", "ratioOfClip": 1, "getClips": 0,
                    "projectName": manifest["name"], "subtitleSwitch": 0, "headlineSwitch": 0,
                    "emojiSwitch": 0, "highlightSwitch": 0, "autoBrollSwitch": 0,
                    "removeSilenceSwitch": 0}
            state.update({"name": manifest["name"], "create_status": "started", "create_request": body})
            store.save()
            try:
                receipt = api("/project/create", body)
            except Exception as exc:
                state["create_error"] = error_receipt(exc)
                store.save()
                raise BatchError("Create response lost; inspect Vizard before retrying") from exc
            state["create_receipt"] = receipt
            state["create_status"] = "blocked"
            if isinstance(receipt, dict) and receipt.get("code") == 2000 and valid_id(receipt.get("projectId")):
                state.update({"projectId": receipt["projectId"], "create_status": "accepted"})
            store.save()
            if state["create_status"] != "accepted":
                raise BatchError("Create not confirmed; receipt saved. Inspect Vizard before recovery")
        if not state.get("finalVideoId"):
            deadline = now() + wait_seconds
            while True:
                receipt = api(f"/project/query/{state['projectId']}")
                state["query_receipt"] = receipt
                store.save()
                if isinstance(receipt, dict) and receipt.get("code") == 2000:
                    videos = receipt.get("videos") or []
                    if len(videos) != 1 or not valid_id(videos[0].get("videoId")):
                        raise BatchError("Expected one finished video; inspect saved query receipt")
                    state["finalVideoId"] = videos[0]["videoId"]
                    store.save()
                    break
                if not isinstance(receipt, dict) or receipt.get("code") != 1000:
                    raise BatchError("Processing not ready; receipt saved. Rerun queries this same project")
                if now() + poll_seconds > deadline:
                    raise BatchError("Processing timed out; rerun resumes the saved project without creating another")
                sleep(poll_seconds)
        for platform in manifest["legs"]:
            if state["legs"].get(platform, {}).get("status") == "accepted":
                continue
            validate_pending(manifest, state, now)
            leg = manifest["legs"][platform]
            body = {"finalVideoId": state["finalVideoId"], "socialAccountId": leg["socialAccountId"],
                    "post": leg["post"], "publishTime": leg["publishTime"]}
            state["legs"][platform] = {"status": "started", "request": body}
            store.save()
            try:
                receipt = api("/project/publish-video", body)
            except Exception as exc:
                state["legs"][platform]["error"] = error_receipt(exc)
                store.save()
                raise BatchError(f"{platform}: publish response lost; inspect Vizard before retrying") from exc
            state["legs"][platform].update({"receipt": receipt, "status": "blocked"})
            if isinstance(receipt, dict) and receipt.get("code") == 2000:
                state["legs"][platform]["status"] = "accepted"
            store.save()
            if state["legs"][platform]["status"] != "accepted":
                raise BatchError(f"{platform}: publish not confirmed; inspect saved receipt before recovery")
        return {"mode": "execute", "projectId": state["projectId"], "finalVideoId": state["finalVideoId"],
                "legs": {p: state["legs"][p]["status"] for p in manifest["legs"]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--state", required=True, help="Private durable JSON; reuse this exact path on every retry")
    parser.add_argument("--platform", action="append", choices=PLATFORMS,
                        help="Explicit destination subset; default remains fb, x and li")
    parser.add_argument("--reuse-manifest", help="Original manifest for already ingested exact media")
    parser.add_argument("--reuse-state", help="Original durable state; adds different platforms only")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=1200)
    args = parser.parse_args()
    try:
        if args.wait_seconds < 0:
            raise BatchError("--wait-seconds cannot be negative")
        if Path(args.state).expanduser().resolve().is_relative_to(Path(__file__).resolve().parents[1]):
            raise BatchError("Keep --state outside the public repository; it contains private publishing receipts")
        manifest, fingerprint = load_manifest(args.manifest, platforms=tuple(args.platform) if args.platform else PLATFORMS)
        if bool(args.reuse_manifest) != bool(args.reuse_state):
            raise BatchError("--reuse-manifest and --reuse-state must be supplied together")
        reuse = reuse_ingest(manifest, args.reuse_manifest, args.reuse_state) if args.reuse_state else None
        result = run(manifest, fingerprint, args.state, execute=args.execute,
                     wait_seconds=args.wait_seconds, reused_ingest=reuse)
        print(json.dumps(result, indent=2))
        return 0
    except (BatchError, OSError, ValueError, KeyError) as exc:
        print(f"Vizard batch stopped: {exc}. Preserve the state file before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
