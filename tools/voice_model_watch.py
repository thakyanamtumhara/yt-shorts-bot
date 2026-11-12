#!/usr/bin/env python3
"""Daily check for a newer ElevenLabs voice model.

voice_models.json says which model the voice jobs speak with. When ElevenLabs lists a higher
generation of the flagship or real-time model, this checks Ketu's clone on it (clone trained, same
words, same pace) and only then moves the file to it.

Way back, when the owner says "voice back to <model>": that model is `previous.flagship` or
`previous.realtime` in voice_models.json; run `gh workflow run voice_model_watch.yml -f rollback=flagship`
(or `rollback=realtime`, which also moves the voice agents back). A model that was left this way is
never moved to again by the daily run; `-f force_candidate=<model>` tests it again on purpose.

Public repo: never print keys, tokens, chat ids, agent ids, response bodies or clip locations.
"""
import argparse
import copy
import datetime
import difflib
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(ROOT, 'voice_models.json')
API = 'https://api.elevenlabs.io/v1/'
VOICE = os.environ.get('ELEVENLABS_VOICE_ID') or 'cejtKjfE9sHUZ1FnUYEV'
VOICE_SETTINGS = {'stability': 0.5, 'similarity_boost': 0.9}
OUTPUT_FORMAT = 'mp3_44100_128'
STT_MODELS = ('scribe_v2', 'scribe_v1')
KINDS = ('flagship', 'realtime')
DEFAULTS = {'flagship': 'eleven_v4', 'realtime': 'eleven_v4_turbo'}
NAME = {'flagship': 'Voice', 'realtime': 'Live call voice'}
REALTIME_WORDS = ('turbo', 'flash', 'conversational', 'realtime')
MODEL_ID = re.compile(r'eleven_[a-z0-9_]+')
GENERATION = re.compile(r'eleven_v(\d+)(?:_(\d+))?(?:_|$)')
WORDS_MEAN, WORDS_LINE = 0.85, 0.70
SPEED_RANGE = (0.90, 1.10)
RETEST_DAYS, MAX_RETESTS, REMIND_DAYS, LOG_ROWS = 7, 4, 7, 200
# ElevenLabs answered the v4 training request with 200 on 2-Oct-2026 and never showed a state for it, yet the
# clone speaks on v4: a model with no training state a day after asking is checked as it is.
ASK_WAIT_DAYS, TRAIN_WAIT_DAYS = 1, 14
UNANSWERED = {0, 429, 500, 502, 503, 504}
RETRY_WAITS = (5, 15)
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
BLIND = 'Voice model check could not read ElevenLabs today. Nothing was changed. It will try again tomorrow.'
# No numbers or hyphenated words: speech-to-text writes them two ways ("240" / "दो सौ चालीस", "prebook" / "pre book"),
# which cost a line 0.24 in the first real proof (12-Nov-2026) with nothing wrong in the voice.
LINES = (
    'हमारी oversize T-shirt का fabric बहुत soft है, और bulk order पर अच्छा discount मिलता है.',
    'दिल्ली में delivery उसी दिन हो जाती है, बाक़ी cities में courier से कुछ दिन लगते हैं.',
    'Black colour का stock कल सुबह तक आ जाएगा, आप अभी website पर order कर सकते हो.',
    'Sample order करके पहले fabric और fit check कर लो, फिर bulk order करो.',
    'Payment के बाद order same day dispatch होता है और tracking link WhatsApp पर आ जाता है.',
)
MISSED_DAYS = 2


def kind_of(model):
    return 'realtime' if any(word in model for word in REALTIME_WORDS) else 'flagship'


def generation(model):
    if not isinstance(model, str) or not MODEL_ID.fullmatch(model):
        return None
    match = GENERATION.match(model)
    return (int(match.group(1)), int(match.group(2) or 0)) if match else None


def higher(model, current):
    found = generation(model)
    return found is not None and found > (generation(current) or (0, 0))


def show(model):
    return re.sub(r'[^A-Za-z0-9_.:-]', '?', str(model))[:60]


def day(value):
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        return datetime.date.min


def fails(state, model):
    return [row for row in state['log'] if row.get('to') == model and row.get('result') == 'failed']


def retired(state, model):
    return any((row.get('to') == model and row.get('result') == 'switched')
               or (row.get('from') == model and row.get('result') == 'rolled_back') for row in state['log'])


def due_retest(state, model, today):
    rows = fails(state, model)
    return bool(rows) and len(rows) <= MAX_RETESTS and (
        today - max(day(row.get('date')) for row in rows)).days >= RETEST_DAYS


def pick(state, entries, today, current):
    queue = {kind: [] for kind in KINDS}
    for model in entries:
        kind = kind_of(model)
        if not higher(model, current[kind]) or retired(state, model):
            continue
        if model in state['pending'] or model not in state['known'] or due_retest(state, model, today):
            queue[kind].append(model)
    for kind in KINDS:
        queue[kind].sort(key=lambda model: (tuple(-part for part in generation(model)), len(model), model))
    return queue


def clone_state(voice, model, entry):
    tuning = voice.get('fine_tuning') if isinstance(voice.get('fine_tuning'), dict) else {}
    states = tuning.get('state') if isinstance(tuning.get('state'), dict) else {}
    value = states.get(model)
    if value == 'fine_tuned':
        return 'trained'
    if value in ('queued', 'fine_tuning', 'delayed'):
        return 'training'
    if value == 'failed':
        return 'failed'
    if value in (None, 'not_started'):
        return 'trained' if entry.get('can_be_finetuned') is False else 'untrained'
    return 'unknown'


def normalise(text):
    kept = []
    for char in unicodedata.normalize('NFC', text or '').lower():
        category = unicodedata.category(char)
        if char == '़' or category == 'Cf':
            continue
        kept.append(' ' if category[0] in 'PSZC' else 'ं' if char == 'ँ' else char)
    return ''.join(kept).split()


def similarity(left, right):
    left, right = normalise(left), normalise(right)
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()


def down(score):
    return math.floor(score * 1000) / 1000


def seconds(audio):
    return len(audio) * 8 / 128000


def judge(kind, scores, speed):
    if sum(scores) / len(scores) < WORDS_MEAN or min(scores) < WORDS_LINE:
        return 'words'
    if kind == 'flagship' and not SPEED_RANGE[0] <= speed <= SPEED_RANGE[1]:
        return 'speed'
    return ''


def is_mp3(status, audio):
    return status == 200 and len(audio) > 1000 and (
        audio.startswith(b'ID3') or (audio[0] == 255 and audio[1] & 224 == 224))


def parse(body):
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None


def error_code(body):
    value = parse(body)
    detail = value.get('detail') if isinstance(value, dict) else None
    status = detail.get('status') if isinstance(detail, dict) else None
    return status if isinstance(status, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', status) else None


def mask(value):
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print(f'::add-mask::{value}', flush=True)


def multipart(fields, files):
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
             for name, value in fields.items()]
    for name, (filename, content, content_type) in files.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                     f'Content-Type: {content_type}\r\n\r\n'.encode() + content + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts), 'multipart/form-data; boundary=' + boundary


def http(method, url, headers=None, data=None, timeout=120):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read(65536)
    except Exception as error:
        print(f'request failed: {type(error).__name__}')
        return 0, b''


class Eleven:
    def __init__(self, key, sleep=None):
        self.key, self.sleep, self.stt = key, sleep or time.sleep, list(STT_MODELS)

    def call(self, method, path, payload=None, form=None, timeout=120):
        headers, data = {'xi-api-key': self.key}, None
        if payload is not None:
            data, headers['Content-Type'] = json.dumps(payload, ensure_ascii=False).encode(), 'application/json'
        elif form is not None:
            data, headers['Content-Type'] = form
        for wait in (*RETRY_WAITS, None):
            status, body = http(method, API + path, headers, data, timeout)
            if status not in UNANSWERED or wait is None:
                return status, body
            self.sleep(wait)

    def read(self, path, kind):
        status, body = self.call('GET', path, timeout=30)
        value = parse(body)
        return value if status == 200 and isinstance(value, kind) else None

    def models(self):
        return self.read('models', list)

    def voice(self):
        return self.read('voices/' + VOICE, dict)

    def speak(self, model, text):
        return self.call('POST', f'text-to-speech/{VOICE}?output_format={OUTPUT_FORMAT}',
                         {'text': text, 'model_id': model, 'voice_settings': VOICE_SETTINGS})

    def transcribe(self, audio):
        while self.stt:
            form = multipart({'model_id': self.stt[0], 'language_code': 'hi', 'tag_audio_events': 'false'},
                             {'file': ('clip.mp3', audio, 'audio/mpeg')})
            status, body = self.call('POST', 'speech-to-text', form=form, timeout=180)
            value = parse(body)
            if status == 200 and isinstance(value, dict) and isinstance(value.get('text'), str):
                return value['text']
            print(f'speech-to-text {self.stt[0]}: HTTP {status} {error_code(body) or ""}'.rstrip())
            if status not in (400, 404, 422):
                return None
            self.stt.pop(0)
        return None

    def train(self, model):
        status, body = self.call('POST', f'voices/pvc/{VOICE}/train', {'model_id': model}, timeout=60)
        value = parse(body)
        return status, value if isinstance(value, dict) else {}, error_code(body)

    def agents(self):
        found, cursor = [], ''
        for _ in range(50):
            page = self.read('convai/agents?page_size=100' + cursor, dict)
            if page is None or not isinstance(page.get('agents'), list):
                return None
            for item in page['agents']:
                agent_id = item.get('agent_id') if isinstance(item, dict) else None
                if isinstance(agent_id, str) and agent_id:
                    mask(agent_id)
                    found.append(agent_id)
            if not page.get('has_more') or not isinstance(page.get('next_cursor'), str):
                return found
            cursor = '&cursor=' + urllib.parse.quote(page['next_cursor'], safe='')
        return None

    def agent(self, agent_id):
        return self.read('convai/agents/' + urllib.parse.quote(agent_id, safe=''), dict)

    def patch_agent(self, agent_id, tts):
        return self.call('PATCH', 'convai/agents/' + urllib.parse.quote(agent_id, safe=''),
                         {'conversation_config': {'tts': tts}}, timeout=60)[0]


def voice_check(api, kind, current, model, dry=False):
    old, new = [], []
    for line in LINES:
        for target, clips in ((model, new), (current, old)):
            status, audio = api.speak(target, line)
            if not is_mp3(status, audio):
                print(f'{show(target)}: speech HTTP {status} {error_code(audio) or ""}'.rstrip())
                if target == model and status not in UNANSWERED and status != 200:
                    return {'result': 'failed', 'reason': 'refused'}
                return {'result': 'error'}
            clips.append(audio)
    scores = []
    for before, after in zip(old, new):
        heard = api.transcribe(before), api.transcribe(after)
        if None in heard:
            return {'result': 'error'}
        if dry:
            print('   heard old: ' + ' '.join(normalise(heard[0])) + '\n   heard new: ' + ' '.join(normalise(heard[1])))
        scores.append(similarity(*heard))
    speed = sum(map(seconds, new)) / sum(map(seconds, old))
    reason = judge(kind, scores, speed)
    print(f'{show(model)} against {show(current)}: words {[round(score, 2) for score in scores]}, speed {speed:.3f}, '
          f'{"failed on " + reason if reason else "passed"}')
    return {'result': 'failed' if reason else 'switch', 'reason': reason, 'words': down(sum(scores) / len(scores)),
            'words_min': down(min(scores)), 'speed': round(speed, 3), 'clips': (old[0], new[0])}


def assess(api, kind, current, model, entry, waiting, today, dry=False):
    voice = api.voice()
    if voice is None:
        return {'result': 'error'}
    clone = clone_state(voice, model, entry)
    waited = (today - day(waiting.get('since'))).days if waiting else 0
    print(f'{show(model)}: clone is {clone}' + (f', first seen {waited} day(s) ago' if waiting else ''))
    if clone == 'trained':
        return voice_check(api, kind, current, model, dry)
    if clone in ('training', 'unknown'):
        if waiting and waited >= TRAIN_WAIT_DAYS:
            return voice_check(api, kind, current, model, dry)
        return {'result': 'pending', 'state': clone, 'train_started': clone == 'training'}
    if waiting:
        if clone == 'failed' and waiting.get('train_started'):
            return {'result': 'failed', 'reason': 'training'}
        if waited >= ASK_WAIT_DAYS:
            return voice_check(api, kind, current, model, dry)
        return {'result': 'pending', 'state': waiting.get('state') or 'asked',
                'train_started': bool(waiting.get('train_started'))}
    if dry:
        print(f'{show(model)}: a real run would first ask ElevenLabs to train the clone on it and test a day later')
        return voice_check(api, kind, current, model, dry)
    status, answer, code = api.train(model)
    print(f'{show(model)}: training request HTTP {status} {code or ""}'.rstrip())
    if status in UNANSWERED:
        return {'result': 'error'}
    if status == 200 and answer.get('status') == 'ok':
        return {'result': 'pending', 'state': 'asked', 'train_started': True}
    return voice_check(api, kind, current, model, dry)


def tts_fields(agent):
    config = agent.get('conversation_config') if isinstance(agent.get('conversation_config'), dict) else {}
    return config.get('tts') if isinstance(config.get('tts'), dict) else {}


def tts_of(agent):
    tts = tts_fields(agent)
    return tts.get('voice_id'), tts.get('model_id')


def put_back(api, moved):
    stuck = 0
    for agent_id, before in moved:
        wanted = before.get('voice_id'), before.get('model_id')
        if api.patch_agent(agent_id, before) != 200:
            api.patch_agent(agent_id, dict(zip(('voice_id', 'model_id'), wanted)))
        agent = api.agent(agent_id)
        stuck += agent is None or tts_of(agent) != wanted
    return stuck


def move_agents(api, old, new, dry=False):
    agent_ids = api.agents()
    if agent_ids is None:
        return {'state': 'unread', 'stuck': 0}
    moved = []
    for agent_id in agent_ids:
        agent = api.agent(agent_id)
        if agent is None:
            return {'state': 'unread', 'stuck': put_back(api, moved)}
        if tts_of(agent) != (VOICE, old):
            continue
        before = tts_fields(agent)
        if not dry:
            status = api.patch_agent(agent_id, {'model_id': new})
            after = api.agent(agent_id)
            if status != 200 or after is None or tts_of(after) != (VOICE, new):
                unread = status in UNANSWERED or (status == 200 and after is None)
                print(f'voice agent {len(moved) + 1}: change HTTP {status}')
                return {'state': 'unread' if unread else 'refused', 'stuck': put_back(api, moved + [(agent_id, before)])}
            also = sorted(show(name) for name in set(before) | set(tts_fields(after))
                          if name != 'model_id' and before.get(name) != tts_fields(after).get(name))
            if also:
                print(f'voice agent {len(moved) + 1}: other voice settings changed with the model: {", ".join(also)}')
        moved.append((agent_id, before))
    return {'state': 'moved', 'count': len(moved), 'stuck': 0}


def note(text, clips=()):
    return {'text': text, 'clips': list(clips)}


def percent(value):
    return f'{round(value * 100)}%'


def agents_line(count):
    return f'{count} voice agent{"" if count == 1 else "s"} moved.'


def stuck_line(stuck):
    return f' {stuck} voice agent{"" if stuck == 1 else "s"} could not be put back. Please tell an AI session now.' if stuck else ''


def text_switch(kind, old, new, outcome):
    moved = ' ' + agents_line(outcome['agents']) if kind == 'realtime' else ''
    return (f'{NAME[kind]} moved to the new ElevenLabs model {show(new)} (was {show(old)}). Words check '
            f'{percent(outcome["words"])}.{moved} Listen: clip A = old, clip B = new.\n'
            f"To go back: tell any AI session 'voice back to {show(old)}'.")


def text_failed(kind, old, new, outcome, tries):
    reason = outcome['reason']
    if reason == 'words' and outcome['words'] < WORDS_MEAN:
        why = f'it does not say our words the same way (words check {percent(outcome["words"])}, needs {percent(WORDS_MEAN)})'
    elif reason == 'words':
        why = f'it said one test line differently (that line {percent(outcome["words_min"])}, needs {percent(WORDS_LINE)})'
    elif reason == 'speed':
        pace = 'slower' if outcome['speed'] > 1 else 'faster'
        why = (f'it speaks {round(abs(outcome["speed"] - 1) * 100)}% {pace} than the voice now, so an AI session '
               f'must first change the Shorts script length')
    else:
        why = {'refused': 'ElevenLabs does not let us use it yet',
               'training': 'training your voice on it failed at ElevenLabs',
               'agents': 'the voice agents did not accept it'}[reason]
    again = (f'I will test it again in {RETEST_DAYS} days.' if tries <= MAX_RETESTS
             else 'I will not test it again by myself.')
    ask = f" To move sooner, tell any AI session: 'move the voice to {show(new)}'." if reason == 'speed' else ''
    return (f'ElevenLabs has a new voice model {show(new)}. It did not pass: {why}. {NAME[kind]} stays on '
            f'{show(old)}. {again}{ask}{stuck_line(outcome.get("stuck"))}')


def text_pending(kind, old, new, outcome):
    if outcome['state'] == 'asked':
        return (f'ElevenLabs has a new voice model {show(new)}. I asked ElevenLabs to train your voice on it and '
                f'will test it tomorrow. {NAME[kind]} stays on {show(old)} until it is checked.')
    if outcome['state'] == 'training':
        return (f'ElevenLabs has a new voice model {show(new)}. Your voice is being trained on it now. '
                f'{NAME[kind]} stays on {show(old)} until the training is done and checked.')
    return (f'ElevenLabs has a new voice model {show(new)}, but ElevenLabs shows a training state this check does '
            f'not know. {NAME[kind]} stays on {show(old)}. Please tell an AI session to look.')


def text_error(kind, old, new, outcome):
    return (f'ElevenLabs has a new voice model {show(new)}, but the test could not finish today because ElevenLabs '
            f'did not answer. {NAME[kind]} stays on {show(old)}. It will try again tomorrow.'
            f'{stuck_line(outcome.get("stuck"))}')


def run(state, api, today, dry=False, pretend=None, forced=None):
    state, notes, blind = copy.deepcopy(state), [], False
    entries = {item['model_id']: item for item in api.models() or [] if isinstance(item, dict)
               and isinstance(item.get('model_id'), str) and item.get('can_do_text_to_speech') is True}
    if not entries:
        return state, [note(BLIND)], True
    current = {kind: (pretend or {}).get(kind) or state[kind] for kind in KINDS}
    seeding = not state['known']
    fresh = [model for model in sorted(entries) if model not in state['known']]
    for model in list(state['pending']):
        if not higher(model, current[kind_of(model)]) or retired(state, model):
            del state['pending'][model]
    if forced and forced not in entries:
        return state, [note(f"{show(forced)} is not in ElevenLabs' list of voice models today. Nothing was changed.")], False
    if forced and forced == current[kind_of(forced)]:
        return state, [note(f'{NAME[kind_of(forced)]} is already on {show(forced)}. Nothing was changed.')], False
    found = pick(state, entries, today, current)
    queue = {kind_of(forced): [forced]} if forced else found
    tested, untested = set(), set()
    for kind in KINDS:
        for model in queue.get(kind, []):
            old = current[kind]
            tested.add(model)
            outcome = assess(api, kind, old, model, entries[model], state['pending'].get(model), today, dry)
            if outcome['result'] == 'switch' and kind == 'realtime':
                agents = move_agents(api, old, model, dry)
                outcome['agents'], outcome['stuck'] = agents.get('count', 0), agents['stuck']
                if agents['state'] != 'moved':
                    outcome.update(result='error' if agents['state'] == 'unread' else 'failed', reason='agents')
            row = {'date': today.isoformat(), 'kind': kind, 'from': old, 'to': model,
                   **{name: outcome[name] for name in ('words', 'words_min', 'speed') if name in outcome}}
            blind = blind or bool(outcome.get('stuck'))
            if outcome['result'] == 'switch':
                state['previous'][kind], state[kind], current[kind] = old, model, model
                state['pending'].pop(model, None)
                state['log'].append({**row, 'result': 'switched'})
                notes.append(note(text_switch(kind, old, model, outcome),
                                  zip((f'A - old voice ({show(old)})', f'B - new voice ({show(model)})'), outcome['clips'])))
                break
            if outcome['result'] == 'failed':
                state['pending'].pop(model, None)
                state['log'].append({**row, 'result': 'failed', 'reason': outcome['reason']})
                notes.append(note(text_failed(kind, old, model, outcome, len(fails(state, model)))))
            elif outcome['result'] == 'pending':
                waiting = state['pending'].get(model)
                if waiting is None:
                    state['pending'][model] = {'kind': kind, 'since': today.isoformat(), 'told': today.isoformat(),
                                               'state': outcome['state'], 'train_started': outcome['train_started']}
                    notes.append(note(text_pending(kind, old, model, outcome)))
                else:
                    waiting.update(state=outcome['state'], train_started=outcome['train_started'])
                    if (today - day(waiting.get('told'))).days >= REMIND_DAYS:
                        waiting['told'] = today.isoformat()
                        notes.append(note(f'Still waiting: your voice is not ready on the new ElevenLabs model '
                                          f'{show(model)} ({(today - day(waiting.get("since"))).days} days). '
                                          f'{NAME[kind]} stays on {show(old)}.'))
            else:
                blind = True
                untested.add(model)
                notes.append(note(text_error(kind, old, model, outcome)))
    untested |= {model for kind in KINDS for model in found[kind]
                 if model not in tested and higher(model, current[kind])}
    state['known'] = sorted(set(state['known']) | (set(entries) - untested))
    others = [show(model) for model in fresh if model not in tested and model not in untested]
    if others and not seeding:
        notes.append(note(f'ElevenLabs added: {", ".join(others)}. Not a newer version of our voice models by name, '
                          f'so nothing was tested or changed. Voice stays on {show(state["flagship"])}.'))
    state['log'] = state['log'][-LOG_ROWS:]
    return state, notes, blind


def rollback(state, kind, api, today, dry=False):
    state = copy.deepcopy(state)
    current, target = state[kind], state['previous'].get(kind)
    if not target or target == current:
        return state, [note(f'No earlier model is saved to go back to. {NAME[kind]} stays on {show(current)}.')], True
    moved = ''
    if kind == 'realtime':
        agents = move_agents(api, current, target, dry)
        if agents['state'] != 'moved':
            return state, [note(f'{NAME[kind]} could not go back to {show(target)}: the voice agents did not accept '
                                f'it. It stays on {show(current)}.{stuck_line(agents["stuck"])}')], True
        moved = ' ' + agents_line(agents['count'])
    state[kind], state['previous'][kind] = target, current
    state['pending'].pop(target, None)
    state['known'] = sorted(set(state['known']) | {current, target})
    state['log'] = (state['log'] + [{'date': today.isoformat(), 'kind': kind, 'from': current, 'to': target,
                                     'result': 'rolled_back'}])[-LOG_ROWS:]
    return state, [note(f'{NAME[kind]} is back on {show(target)} (was {show(current)}).{moved} '
                        f'The daily check will not move to {show(current)} again by itself.')], False


def last_run_days(now):
    repo, token = os.environ.get('GITHUB_REPOSITORY'), os.environ.get('GH_TOKEN')
    if not repo or not token:
        return None
    status, body = http('GET', f'https://api.github.com/repos/{repo}/actions/workflows/voice_model_watch.yml/runs'
                        '?status=success&per_page=1',
                        {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                         'User-Agent': 'voice-model-watch'}, timeout=30)
    value = parse(body)
    try:
        created = datetime.datetime.fromisoformat(value['workflow_runs'][0]['created_at'].replace('Z', '+00:00'))
    except (TypeError, KeyError, IndexError, ValueError, AttributeError):
        return None
    return (now - created).days


def routine(state, today, gap):
    notes = []
    if gap is not None and gap > MISSED_DAYS:
        notes.append(note(f'Voice model check had not run for {gap} days (GitHub skipped it). It ran today.'))
    month = today.strftime('%Y-%m')
    if state.get('heartbeat') != month:
        state['heartbeat'] = month
        waiting = sorted(show(model) for model in state['pending'])
        extra = f' A newer model is waiting for its check: {", ".join(waiting)}.' if waiting else ''
        notes.append(note(f'Voice model check is running every day. Videos speak with {show(state["flagship"])}, live '
                          f'calls with {show(state["realtime"])}.{extra} You get this line once a month; if it '
                          f'stops coming, tell an AI session.'))
    return notes


def load_state(path=None):
    with open(path or STATE_FILE, encoding='utf-8') as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError('voice_models.json must hold an object')
    state = dict(raw)
    for kind in KINDS:
        if not isinstance(raw.get(kind), str) or not MODEL_ID.fullmatch(raw[kind]):
            state[kind] = DEFAULTS[kind]
    previous = raw.get('previous') if isinstance(raw.get('previous'), dict) else {}
    state['previous'] = {kind: value for kind, value in previous.items()
                         if kind in KINDS and isinstance(value, str) and MODEL_ID.fullmatch(value)}
    state['known'] = sorted({item for item in raw.get('known') or [] if isinstance(item, str)})
    pending = raw.get('pending') if isinstance(raw.get('pending'), dict) else {}
    state['pending'] = {model: dict(value) for model, value in pending.items() if isinstance(value, dict)}
    state['log'] = [row for row in raw.get('log') or [] if isinstance(row, dict)]
    return state


def save_state(state, path=None):
    path = path or STATE_FILE
    with open(path + '.tmp', 'w', encoding='utf-8') as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    os.replace(path + '.tmp', path)


def telegram(method, fields, files=None):
    token = (os.environ.get('TELEGRAM_BOT_TOKEN') or '').strip()
    chat = (os.environ.get('TELEGRAM_ALERT_CHAT_ID') or '').strip()
    if not token or not chat:
        print('telegram: not configured')
        return False
    fields = {'chat_id': chat, **fields}
    data, content_type = multipart(fields, files) if files else (
        urllib.parse.urlencode(fields).encode(), 'application/x-www-form-urlencoded')
    status, body = http('POST', f'https://api.telegram.org/bot{token}/{method}', {'Content-Type': content_type}, data, 60)
    value = parse(body)
    sent = status == 200 and isinstance(value, dict) and value.get('ok') is True
    if not sent:
        print(f'telegram {method}: HTTP {status}')
    return sent


def send(notes):
    sent = True
    for item in notes:
        sent = telegram('sendMessage', {'text': item['text']}) and sent
        for title, audio in item['clips']:
            sent = telegram('sendAudio', {'title': title}, {'audio': (title[0] + '.mp3', audio, 'audio/mpeg')}) and sent
    return sent


def save_outbox(folder, notes):
    os.makedirs(folder, exist_ok=True)
    listing = []
    for index, item in enumerate(notes):
        clips = []
        for position, (title, audio) in enumerate(item['clips']):
            name = f'clip-{index}-{position}.mp3'
            with open(os.path.join(folder, name), 'wb') as handle:
                handle.write(audio)
            clips.append([title, name])
        listing.append({'text': item['text'], 'clips': clips})
    with open(os.path.join(folder, 'outbox.json'), 'w', encoding='utf-8') as handle:
        json.dump(listing, handle, ensure_ascii=False)


def send_outbox(folder):
    try:
        with open(os.path.join(folder, 'outbox.json'), encoding='utf-8') as handle:
            listing = json.load(handle)
    except FileNotFoundError:
        listing = []
    notes = []
    for item in listing:
        clips = []
        for title, name in item['clips']:
            with open(os.path.join(folder, os.path.basename(name)), 'rb') as handle:
                clips.append((title, handle.read()))
        notes.append(note(item['text'], clips))
    if not notes:
        print('nothing to tell today')
        return 0
    if not send(notes):
        return 1
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as handle:
            handle.write('told=1\n')
    print(f'told: {len(notes)} message{"" if len(notes) == 1 else "s"}')
    return 0


def deliver(notes, dry=False, outbox=None):
    if dry:
        for item in notes:
            print('--- TELEGRAM (dry run, not sent) ---\n' + item['text'])
            for title, audio in item['clips']:
                print(f'   + audio "{title}", {seconds(audio):.1f} s')
        return True
    if outbox:
        save_outbox(outbox, notes)
        return True
    return send(notes) if notes else True


def main(argv=None):
    parser = argparse.ArgumentParser(description='Move the voice jobs to a newer ElevenLabs model after its checks.')
    parser.add_argument('--dry-run', action='store_true', help='no file write, no agent change, no training, no Telegram')
    parser.add_argument('--pretend-current', default='', help='proof only, forces a dry run: flagship=eleven_v3')
    parser.add_argument('--force-candidate', default='', help='test this model id now, whatever its name or history')
    parser.add_argument('--rollback', choices=KINDS, help='go back to the previous model of this kind')
    parser.add_argument('--outbox', help='save the Telegram messages here for --send-outbox instead of sending')
    parser.add_argument('--send-outbox', help='send the messages an earlier run saved')
    parser.add_argument('--scheduled', action='store_true', help='timer run: monthly line and missed-days alert')
    args = parser.parse_args(argv)
    if args.send_outbox:
        return send_outbox(args.send_outbox)
    pretend = {}
    for part in filter(None, (part.strip() for part in args.pretend_current.split(','))):
        kind, _, model = part.partition('=')
        if kind not in KINDS or not MODEL_ID.fullmatch(model):
            parser.error('--pretend-current takes flagship=<model id> or realtime=<model id>')
        pretend[kind] = model
    forced = args.force_candidate.strip()
    if forced and not MODEL_ID.fullmatch(forced):
        parser.error('--force-candidate takes one ElevenLabs model id')
    dry = args.dry_run or bool(pretend)
    today = datetime.datetime.now(IST).date()
    try:
        state = load_state()
    except Exception as error:
        print(f'voice_models.json unreadable: {type(error).__name__}')
        deliver([note('Voice model check could not read its own settings file today. Nothing was changed. '
                      'Please tell an AI session.')], dry, args.outbox)
        return 1
    key = (os.environ.get('ELEVENLABS_API_KEY') or '').strip()
    if args.rollback:
        changed, notes, blind = rollback(state, args.rollback, Eleven(key), today, dry)
    elif not key:
        print('ELEVENLABS_API_KEY is not set')
        changed, notes, blind = state, [note(BLIND)], True
    else:
        changed, notes, blind = run(state, Eleven(key), today, dry, pretend, forced)
        if args.scheduled and not dry and not blind:
            notes = notes + routine(changed, today, last_run_days(datetime.datetime.now(datetime.timezone.utc)))
    if not dry and changed != state:
        save_state(changed)
    print(json.dumps({'flagship': changed['flagship'], 'realtime': changed['realtime'], 'known': len(changed['known']),
                      'pending': sorted(changed['pending']), 'messages': len(notes), 'blind': blind, 'dry_run': dry}))
    told = deliver(notes, dry, args.outbox)
    return 1 if blind or not told else 0


if __name__ == '__main__':
    sys.exit(main())
