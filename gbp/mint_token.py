#!/usr/bin/env python3
"""Make the Google permission for gbp/sync.py again (only when Google says the saved one is refused).

Run on the Mac: python3 gbp/mint_token.py <path to the OAuth desktop client json>
It prints a Google link. The OWNER opens it in the Chrome that is signed in to the Business Profile's Google account
and presses Continue / Allow himself (an AI session never clicks a Google permission screen). The key is then saved
to ~/.gbp_token.json (0600). Copy its client_id / client_secret / refresh_token into the repo secrets GBP_CLIENT_ID /
GBP_CLIENT_SECRET / GBP_REFRESH_TOKEN with `gh secret set` (stdin, never printed).
"""
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], ['https://www.googleapis.com/auth/business.manage'])
creds = flow.run_local_server(port=8766, open_browser=False, prompt='consent', access_type='offline',
                              authorization_prompt_message='Open this link: {url}',
                              success_message='Done. You can close this tab.')
out = os.path.expanduser('~/.gbp_token.json')
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write(creds.to_json())
print('saved', out, '| refresh token:', bool(creds.refresh_token))
