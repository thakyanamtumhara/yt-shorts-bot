"""Read-only final-visual replay of one verified completed daily Short artifact."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.audit_short_output import (AuditError, REPOSITORY, download_artifact, github_json,
                                      run_id, validate_archive, validate_run)
from tools.prepublication_visual import VisualReviewError, assess_final_visuals


REPORT = Path('audit-short-report')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args(argv)
    REPORT.mkdir(exist_ok=True)
    result = {'format': 'daily-short-visual-replay-v1', 'checked_at': datetime.now(timezone.utc).isoformat(),
              'read_only': True, 'public_writes': 0, 'content_generation_calls': 0, 'passed': False,
              'audio_review': 'not_repeated; this visual replay cannot replace the separate native audio gate'}
    try:
        source_id = run_id(args.run_id)
        if os.environ.get('GITHUB_REPOSITORY', REPOSITORY) != REPOSITORY:
            raise AuditError('Visual replay must run in the intended repository.')
        run = github_json('/actions/runs/' + source_id)
        result['source'] = validate_run(run, source_id)
        data, result['artifact_id'] = download_artifact(source_id, run)
        with tempfile.TemporaryDirectory(prefix='visual-output-audit-') as directory:
            manifest, video = validate_archive(data, run, Path(directory))
            result['video_asset'] = manifest['assets']['video']
            assessment = assess_final_visuals(video, manifest, report_dir=REPORT)
            result.update(passed=assessment['passed'], model_visual_passed=assessment['model_visual_passed'],
                          state=assessment['state'], visual_assessment_artifact='visual-assessment.json')
    except Exception as error:
        result['error'] = str(error) if isinstance(error, (AuditError, VisualReviewError)) else type(error).__name__
    (REPORT / 'visual-replay-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
