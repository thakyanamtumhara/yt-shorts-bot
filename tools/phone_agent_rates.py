"""Keep the phone voice agent's rate list equal to the website.

The ElevenLabs phone agent quotes rates from a block in its own prompt between
'=== RATES START ===' and '=== RATES END ==='. This job rebuilds that block from the
website's live catalogue (the same public voice tool the agent calls) and writes it back
only when a price, colour or size line changed. Stock is never in the block: the agent
asks the live stock tool on every question. Nothing else in the prompt is touched.
Exit 1 on any failure so the workflow sends the Telegram.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

CATALOG = 'https://digital-ketu2-production.up.railway.app/api/voice/catalog'
API = 'https://api.elevenlabs.io/v1/'
START = '=== RATES START ==='
END = '=== RATES END ==='
MIN_PRODUCTS = 15


def http(method, url, body=None, headers=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def catalog(q):
    status, body = http('GET', CATALOG + '?' + urllib.parse.urlencode({'q': q}))
    if status != 200:
        raise SystemExit(f'catalogue HTTP {status}')
    return json.loads(body)


def rate_lines():
    names = catalog('').get('products') or []
    lines, seen = [], set()
    for name in names:
        answer = catalog(name)
        if not answer.get('found'):
            continue
        for line in answer.get('text', '').splitlines():
            line = line.strip()
            if ' | ' not in line:
                continue
            key = line.split(' | ')[0]
            if key in seen:
                continue
            seen.add(key)
            if ' | → ' in line:
                line = line.rsplit(' | → ', 1)[0]
            lines.append(line)
    if len(lines) < MIN_PRODUCTS:
        raise SystemExit(f'only {len(lines)} products read from the catalogue, expected {MIN_PRODUCTS}+')
    return lines


def body_lines(block):
    return [x.strip() for x in block.splitlines() if ' | ' in x]


def main():
    key = os.environ.get('ELEVENLABS_API_KEY', '')
    if not key:
        raise SystemExit('ELEVENLABS_API_KEY missing')
    head = {'xi-api-key': key}
    force = os.environ.get('FORCE') == 'true'
    dry = os.environ.get('DRY_RUN') == 'true'

    fresh = rate_lines()
    ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    block = '\n'.join([START, f'(live website prices as of {ist:%d %b %Y %H:%M} IST)', *fresh, END])

    status, raw = http('GET', API + 'convai/agents?page_size=100', headers=head)
    if status != 200:
        raise SystemExit(f'agent list HTTP {status}')
    ids = [a.get('agent_id') for a in json.loads(raw).get('agents', []) if isinstance(a, dict)]

    touched = 0
    for n, agent_id in enumerate(ids, 1):
        status, raw = http('GET', API + 'convai/agents/' + urllib.parse.quote(agent_id, safe=''), headers=head)
        if status != 200:
            raise SystemExit(f'agent {n} read HTTP {status}')
        agent = json.loads(raw)
        prompt_obj = ((agent.get('conversation_config') or {}).get('agent') or {}).get('prompt') or {}
        text = prompt_obj.get('prompt') or ''
        if START not in text or END not in text:
            continue
        before, rest = text.split(START, 1)
        old_block, after = rest.split(END, 1)
        if body_lines(old_block) == fresh and not force:
            print(f'phone agent {n}: rates unchanged ({len(fresh)} products)')
            touched += 1
            continue
        changed = sorted(set(fresh) ^ set(body_lines(old_block)))
        print(f'phone agent {n}: {len(changed)} line(s) differ' + (' (forced)' if force else ''))
        if dry:
            touched += 1
            continue
        new_text = before + block + after
        status, raw = http('PATCH', API + 'convai/agents/' + urllib.parse.quote(agent_id, safe=''),
                           {'conversation_config': {'agent': {'prompt': dict(prompt_obj, prompt=new_text)}}}, head, timeout=60)
        if status != 200:
            raise SystemExit(f'phone agent {n}: update HTTP {status}')
        status, raw = http('GET', API + 'convai/agents/' + urllib.parse.quote(agent_id, safe=''), headers=head)
        check = json.loads(raw) if status == 200 else {}
        p2 = ((check.get('conversation_config') or {}).get('agent') or {}).get('prompt') or {}
        if body_lines(p2.get('prompt') or '') != fresh:
            raise SystemExit(f'phone agent {n}: read back does not show the new rates')
        for field in ('llm', 'tool_ids'):
            if field in prompt_obj and p2.get(field) != prompt_obj.get(field):
                raise SystemExit(f'phone agent {n}: {field} changed after the update')
        print(f'phone agent {n}: rates written and read back')
        touched += 1
    if not touched:
        raise SystemExit('no agent has a RATES block')


if __name__ == '__main__':
    main()
