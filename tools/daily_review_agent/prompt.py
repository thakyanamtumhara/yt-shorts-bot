"""prompt.py <run> <dir> <mode> <repo> <reviewer>: REVIEW.md with its placeholders filled (Mac and cloud reviewers)."""
import sys
from pathlib import Path

FIELDS = ('{{RUN}}', '{{DIR}}', '{{MODE}}', '{{REPO}}', '{{REVIEWER}}')


def fill(template, values):
    for field, value in zip(FIELDS, values):
        template = template.replace(field, value)
    missing = [field for field in FIELDS if field in template]
    if missing:
        raise SystemExit(f'unfilled placeholders: {missing}')
    return template


if __name__ == '__main__':
    if len(sys.argv) != 6 or not sys.argv[1].isdigit():
        raise SystemExit(__doc__)
    print(fill((Path(__file__).parent / 'REVIEW.md').read_text(), sys.argv[1:]))
