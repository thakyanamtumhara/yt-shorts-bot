#!/usr/bin/env python3
"""Store the thakyanamtumhara GitHub CLI token as this Worker's encrypted secret; the value is never printed."""
import subprocess
from pathlib import Path

directory = Path(__file__).resolve().parent
credential = subprocess.run(['gh', 'auth', 'token', '--user', 'thakyanamtumhara'], check=True,
                            capture_output=True).stdout.strip()
if not credential:
    raise SystemExit('No thakyanamtumhara GitHub credential is available.')
result = subprocess.run(['npx', '--yes', 'wrangler', 'secret', 'put', 'DAILY_SHORT_GH_TOKEN', '--config', 'wrangler.jsonc'],
                        cwd=directory, input=credential + b'\n', capture_output=True)
if result.returncode:
    raise SystemExit('Encrypted Worker secret installation failed; response omitted.')
print('GitHub credential stored as encrypted Worker secret DAILY_SHORT_GH_TOKEN; value omitted.')
