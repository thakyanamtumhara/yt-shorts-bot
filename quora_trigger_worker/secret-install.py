#!/usr/bin/env python3
"""Store quora-trigger's 3 encrypted secrets; no value is ever printed.
QA_GH_TOKEN = the gh CLI token of thakyanamtumhara. TG_TOKEN + TG_CHAT = WWbun's Telegram bot and chat from Railway,
the same pair payerr-watch uses (its deploy/tg-pull.mjs prints only the bot name and chat type)."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

directory = Path(__file__).resolve().parent
wrangler = shutil.which('wrangler') or str(Path.home() / '.npm-global/bin/wrangler')


def put(name, value):
    result = subprocess.run([wrangler, 'secret', 'put', name, '--config', 'wrangler.jsonc'], cwd=directory,
                            input=value + b'\n', capture_output=True)
    if result.returncode:
        raise SystemExit(f'{name}: encrypted Worker secret installation failed; response omitted.')
    print(f'{name} stored as an encrypted Worker secret; value omitted.')


credential = subprocess.run(['gh', 'auth', 'token', '--user', 'thakyanamtumhara'], check=True,
                            capture_output=True).stdout.strip()
if not credential:
    raise SystemExit('No thakyanamtumhara GitHub credential is available.')
put('QA_GH_TOKEN', credential)
with tempfile.TemporaryDirectory() as folder:
    os.chmod(folder, 0o700)
    pulled = subprocess.run(['node', str(Path.home() / 'Projects/payerr-watch/deploy/tg-pull.mjs'), folder],
                            capture_output=True, text=True)
    print(pulled.stdout.strip())
    if pulled.returncode:
        raise SystemExit('The Telegram bot and chat could not be read from Railway.')
    put('TG_TOKEN', Path(folder, 'tg_token').read_bytes().strip())
    put('TG_CHAT', Path(folder, 'tg_chat').read_bytes().strip())
