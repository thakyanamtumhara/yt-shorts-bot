#!/usr/bin/env python3
import subprocess
from pathlib import Path

directory = Path(__file__).resolve().parent
credential = subprocess.run(['gh', 'auth', 'token'], check=True, capture_output=True).stdout.strip()
if not credential:
    raise SystemExit('No existing GitHub credential is available.')
result = subprocess.run(['wrangler', 'secret', 'put', 'REVIEWED_IG_GH_TOKEN', '--config', 'wrangler.jsonc'], cwd=directory, input=credential + b'\n', capture_output=True)
if result.returncode:
    raise SystemExit('Encrypted Worker secret installation failed; response omitted.')
print('Existing GitHub credential stored as encrypted Worker secret; value omitted.')
