import json
from pathlib import Path
import tempfile

from tools.audit_short_output import AuditError, assess_audio, probe_and_extract


def require_native_audio_review(video_path, manifest_path):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('test_mode') is True:
        return {'passed': None, 'state': 'test_only_not_publishing'}
    if any((manifest.get('source_posts') or {}).values()):
        raise AuditError('Prepublication review requires a video with no publication receipts.')
    with tempfile.TemporaryDirectory(prefix='daily-audio-review-') as directory:
        wav, media = probe_and_extract(Path(video_path), Path(directory))
        assessment = assess_audio(wav, manifest, media['audio_seconds'], report_dir=manifest_path.parent)
    result = {'passed': assessment['passed'], 'review_type': assessment['review_type'],
              'model': assessment['model'], 'audio_sha256': assessment['audio_sha256']}
    manifest.setdefault('run_flags', {})['native_audio_review'] = result
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    if not assessment['passed']:
        raise AuditError('Finished audio did not pass pronunciation, completeness and ending review; no public upload.')
    return result
