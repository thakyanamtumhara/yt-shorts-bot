"""Calibrate the clip check (tools/clip_visual_check.py) on footage the final visual review already judged.

Windows are cut out of a run's rendered Short (rendered-video-backup artifact), so the burned-in captions, hook line
and watermark are disclosed to the reviewer. Prints each verdict next to the expected one. Reads only; publishes and
writes nothing. Workflow: .github/workflows/clip_check_probe.yml.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

if __package__:
    from .clip_visual_check import lesson_context, review_clip
else:
    from clip_visual_check import lesson_context, review_clip


NOTE = ('Calibration copy: this window was cut from the finished Short, so burned-in subtitles, a hook line, a cover '
        'card and a small Sale91.com watermark were added after generation. Ignore those overlays and judge only the '
        'generated footage under them.')


def window_narration(folder, start, end):
    """What was said in the window: the captions the final review saw there, else the whole English script."""
    try:
        checks = json.loads((folder / 'visual-assessment.json').read_text())['assessment']['segment_checks']
        segments = json.loads((folder / 'visual-assessment.json').read_text())['media']['segments_requested']
        times = {item['segment_index']: item for item in segments}
        texts = [item['visible_text'] for item in checks
                 if min(end, times[item['segment_index']]['end_seconds'])
                 - max(start, times[item['segment_index']]['start_seconds']) > 0.5]
        if texts:
            return ' '.join(texts)
    except (OSError, KeyError, ValueError, TypeError):
        pass
    manifest = json.loads((folder / 'review_manifest.json').read_text())
    return (manifest.get('script') or {}).get('english', '')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, help='folder holding one <run_id>/ artifact folder per run')
    parser.add_argument('--samples', required=True, help='run:start-end:good|bad, comma separated')
    args = parser.parse_args()
    rows = []
    for item in args.samples.split(','):
        match = re.fullmatch(r'\s*(\d+):([\d.]+)-([\d.]+):(good|bad)\s*', item)
        if not match:
            raise SystemExit(f'bad sample: {item!r}')
        run, start, end, expect = match.group(1), float(match.group(2)), float(match.group(3)), match.group(4)
        folder = Path(args.root) / run
        video = next(folder.glob('SHORT_*.mp4'))
        manifest = json.loads((folder / 'review_manifest.json').read_text())
        lesson = (manifest.get('run_flags') or {}).get('topic_lesson') or {}
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / 'window.mp4'
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(start), '-t', str(end - start),
                            '-i', str(video), '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', str(clip)],
                           check=True, capture_output=True, timeout=120)
            result = review_clip(clip, context=lesson_context(manifest.get('topic'), lesson),
                                 narration=window_narration(folder, start, end), extra_note=NOTE)
        agrees = None if result['usable'] is None else (result['usable'] is True) == (expect == 'good')
        rows.append(agrees)
        print(f"{'OK ' if agrees else '?? ' if agrees is None else 'MISMATCH'} run {run} {start:g}-{end:g}s "
              f"expected {expect}: {result['state']} | {str(result.get('observed_visual') or result.get('error'))[:220]}")
        for problem in result.get('problems') or []:
            print(f"      {problem['kind']}: {problem['detail'][:200]}")
        if result.get('avoid'):
            print(f"      avoid: {result['avoid'][:200]}")
    print(f"\n{rows.count(True)} agree, {rows.count(False)} disagree, {rows.count(None)} unchecked "
          f"of {len(rows)} windows")


if __name__ == '__main__':
    main()
