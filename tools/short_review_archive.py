import hashlib
import json
import os
from pathlib import Path
import shutil


def _asset(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    size = path.stat().st_size
    if not size:
        raise ValueError('Review asset is empty')
    return {'file': path.name, 'bytes': size, 'sha256': digest.hexdigest()}


def save_review_archive(*, video_path, thumbnail_path, topic, youtube_title,
                        instagram_title, script_voice, tts_input, script_english,
                        youtube_id, instagram_id, test_mode, run_flags):
    video = Path(video_path).resolve()
    video_asset = _asset(video)
    cover_asset = None
    if thumbnail_path and Path(thumbnail_path).is_file():
        thumbnail = Path(thumbnail_path)
        cover = video.parent / ('review_cover' + thumbnail.suffix.lower())
        shutil.copyfile(thumbnail, cover)
        cover_asset = _asset(cover)
    manifest = {
        'format': 'daily-short-review-v1',
        'review_status': 'unreviewed',
        'main_publication_approved': False,
        'ai_generated': True,
        'test_mode': bool(test_mode),
        'topic': topic,
        'titles': {'youtube': youtube_title, 'instagram': instagram_title},
        'script': {'voice': script_voice, 'tts_input': tts_input,
                   'english': script_english},
        'source_posts': {'bot_youtube': youtube_id or None,
                         'instagram': instagram_id or None},
        'assets': {'video': video_asset, 'cover': cover_asset},
        'run_flags': dict(run_flags),
        'workflow': {key.lower(): os.environ.get(key) for key in (
            'GITHUB_REPOSITORY', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA')},
    }
    path = video.parent / 'review_manifest.json'
    temporary = path.with_suffix('.json.tmp')
    with temporary.open('w', encoding='utf-8') as target:
        json.dump(manifest, target, ensure_ascii=False, indent=2)
        target.write('\n')
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(path)
    return path
