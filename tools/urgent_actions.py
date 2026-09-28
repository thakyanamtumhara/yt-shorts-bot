"""Owner to-dos that block or degrade social posting, published for the WWbun top bar.

Only real, owner-fixable problems belong here (billing, expired logins). Each
writer owns its items: health_watch (critical dependency checks) and
replicate (actual Replicate results from the daily run and the weekly image
self-test; health_watch's account ping cannot see billing refusals). A writer can only add or clear its own items, so one check
never erases another's evidence. The repo is public: no secrets, amounts or
account numbers in the text.
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile

PATH = Path(__file__).resolve().parents[1] / 'urgent_actions.json'
IST = timezone(timedelta(hours=5, minutes=30))
FORMAT = 'urgent-actions-v1'
ACTIONS = {
    'replicate_credit': ('Recharge Replicate',
                         'Reels go out without background music, and blogs and Instagram carousels get no images. '
                         'Log in with "Sign in with GitHub" (thakyanamtumhara).',
                         'https://replicate.com/account/billing'),
    'elevenlabs': ('Fix the ElevenLabs voice plan',
                   'The daily Short cannot be voiced in your cloned voice, so no video goes out.',
                   'https://elevenlabs.io/app/subscription'),
    'anthropic': ('Top up Claude (Anthropic) credit',
                  'Topics and scripts cannot be written, so no video goes out.',
                  'https://console.anthropic.com/settings/billing'),
    'google': ('Fix the Google AI key or billing',
               'Veo video clips and covers cannot be made, so no video goes out.',
               'https://aistudio.google.com/apikey'),
    'meta_token': ('Reconnect Instagram and Facebook',
                   'Reels, carousels and Facebook posts cannot be published.',
                   'https://business.facebook.com/'),
    'youtube': ('Reconnect YouTube',
                'The daily Short cannot be uploaded to YouTube.',
                'https://studio.youtube.com/'),
}


def now_ist():
    return datetime.now(IST).isoformat(timespec='seconds')


def load(path=None):
    try:
        data = json.loads(Path(path or PATH).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'format': FORMAT, 'items': []}
    if data.get('format') != FORMAT or not isinstance(data.get('items'), list):
        return {'format': FORMAT, 'items': []}
    return data


def update(owner, active, path=None, now=None):
    """active = {item_id: evidence}; sets those items for owner and clears the owner's others."""
    now = now or now_ist()
    path = Path(path or PATH)
    data = load(path)
    kept = [item for item in data['items'] if isinstance(item, dict) and item.get('owner') != owner]
    previous = {item.get('id'): item for item in data['items'] if isinstance(item, dict) and item.get('owner') == owner}
    for key, evidence in sorted(active.items()):
        if key not in ACTIONS:
            continue
        title, detail, url = ACTIONS[key]
        kept.append({'id': key, 'owner': owner, 'title': title, 'detail': detail, 'action_url': url,
                     'evidence': str(evidence or '')[:200],
                     'since': (previous.get(key) or {}).get('since') or now, 'last_seen': now})
    items = sorted(kept, key=lambda item: item.get('since') or '')
    if items == data['items']:
        return data
    output = {'format': FORMAT, 'updated_at': now, 'items': items}
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix=path.name + '.',
                                     suffix='.tmp', delete=False) as handle:
        json.dump(output, handle, ensure_ascii=False, indent=1)
        handle.write('\n')
        temporary = Path(handle.name)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)
    return output


def replicate_refused_credit(error):
    text = str(error).lower()
    status = getattr(error, 'status', None) or getattr(error, 'status_code', None)
    return status == 402 or 'insufficient credit' in text or 'payment required' in text
