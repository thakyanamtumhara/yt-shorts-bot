"""Owner review gate (owner, 3-Oct-2026: "you become the quality gate ... first verify from your side and give it 100%
thumbs up, then only you move forward with the video making").

After the machine reviews pass, the rendered Short waits for a reviewer's written decision on THIS exact file before
anything is published. The run puts a review copy at <bucket>/p/review/<run_id>/ (video.mp4, cover.png, review.json
with the video's sha256 and the titles / description / script that will be published), then polls
review_decisions/<run_id>.json on the default branch. A decision counts only when it names this run and this video:
    {"run_id": "...", "video_sha256": "...", "decision": "approve" | "reject", "reviewer": "...", "notes": "..."}
approve -> the run publishes exactly this file; reject, a decision for another file, or no decision before the
deadline -> nothing is published. An approval may also correct the published text (never the video):
"youtube_title", "instagram_title" and/or "youtube_description" (the description body only; the run adds its link
lines and footer). Each correction passes the same price gate as generated text, else nothing is published.
"""
import base64
import hashlib
import json
import time
from pathlib import Path

DECISIONS = ('approve', 'reject')
TEXT_LIMITS = {'youtube_title': 100, 'instagram_title': 150, 'youtube_description': 4000}


class OwnerReviewStop(RuntimeError):
    pass


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def review_prefix(run_id):
    run = str(run_id)
    if not run.isdigit():
        raise OwnerReviewStop('owner review needs a numeric workflow run id')
    return f'p/review/{run}'


def publish_review_copy(s3, bucket, run_id, video_path, cover_path, summary):
    prefix = review_prefix(run_id)
    s3.upload_file(str(video_path), bucket, f'{prefix}/video.mp4',
                   ExtraArgs={'ContentType': 'video/mp4', 'CacheControl': 'no-store'})
    if cover_path and Path(cover_path).is_file():
        s3.upload_file(str(cover_path), bucket, f'{prefix}/cover.png',
                       ExtraArgs={'ContentType': 'image/png', 'CacheControl': 'no-store'})
    s3.put_object(Bucket=bucket, Key=f'{prefix}/review.json', ContentType='application/json', CacheControl='no-store',
                  Body=json.dumps(summary, ensure_ascii=False, indent=1).encode('utf-8'))
    return prefix


def read_decision(fetch, repo, token, run_id, branch='main'):
    """The decision file on the default branch, or None (missing, unreadable or a transient API error)."""
    url = f'https://api.github.com/repos/{repo}/contents/review_decisions/{run_id}.json?ref={branch}'
    try:
        response = fetch(url, headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                                       'X-GitHub-Api-Version': '2022-11-28'}, timeout=20)
        if response.status_code != 200:
            return None
        return json.loads(base64.b64decode(response.json()['content']).decode('utf-8'))
    except Exception:
        return None


def valid_decision(decision, run_id, sha):
    return (isinstance(decision, dict) and str(decision.get('run_id')) == str(run_id)
            and decision.get('video_sha256') == sha and decision.get('decision') in DECISIONS
            and isinstance(decision.get('reviewer'), str) and bool(decision['reviewer'].strip()))


def text_overrides(decision):
    """The reviewer's corrected texts from an approving decision: only known fields, each a non-empty string within
    its platform limit. Anything else stops the run, so no half-reviewed text is published."""
    corrections = {}
    for key, limit in TEXT_LIMITS.items():
        if key not in decision:
            continue
        value = decision[key]
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
            raise OwnerReviewStop(f'the reviewer\'s {key} is empty or longer than {limit} characters')
        corrections[key] = value.strip()
    return corrections


def await_owner_review(*, run_id, sha, repo, token, wait_seconds, fetch, poll_seconds=30, sleep=time.sleep,
                       clock=time.monotonic, log=print):
    """Return the approving decision; raise OwnerReviewStop on a rejection or when the deadline passes."""
    deadline = clock() + max(0, wait_seconds)
    warned = False
    while True:
        decision = read_decision(fetch, repo, token, run_id)
        if decision is not None:
            if valid_decision(decision, run_id, sha):
                if decision['decision'] == 'approve':
                    return decision
                notes = str(decision.get('notes') or '').strip()
                raise OwnerReviewStop(f"rejected by {decision['reviewer'].strip()}" + (f': {notes}' if notes else ''))
            if not warned:
                log('   ⚠️ A review decision exists but does not name this run and this exact video; ignored.')
                warned = True
        if clock() >= deadline:
            raise OwnerReviewStop('no reviewer decision before the deadline; the Short is held and nothing is published')
        sleep(poll_seconds)
