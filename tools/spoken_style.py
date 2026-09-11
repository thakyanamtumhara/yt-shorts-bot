import argparse
import json
from pathlib import Path
import re
import unicodedata


REFERENCE = Path(__file__).resolve().parents[1] / 'spoken_style_reference.json'
EXCLUSIONS = Path(__file__).resolve().parents[1] / 'speech_source_exclusions.json'


class SpokenStyleError(ValueError):
    pass


def load_reference(path=REFERENCE):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('format') != 'recorded-spoken-style-v1':
        raise SpokenStyleError('Unrecognized spoken-style reference.')
    sources = data.get('sources')
    if not isinstance(sources, dict) or not sources:
        raise SpokenStyleError('Spoken-style reference needs real recording sources.')
    for source in sources.values():
        if (source.get('media_kind') != 'real-recording' or source.get('synthetic') is not False
                or source.get('reviewed_source') is not True
                or not re.fullmatch('[0-9a-f]{64}', source.get('reference_excerpt_sha256', ''))):
            raise SpokenStyleError('Only reviewed real recordings may supply spoken-style examples.')
    for example in data.get('examples', []):
        if (example.get('source_id') not in sources
                or type(example.get('start_seconds')) not in (int, float)
                or type(example.get('end_seconds')) not in (int, float)
                or not 0 <= example['start_seconds'] < example['end_seconds']):
            raise SpokenStyleError('Spoken-style example lacks a real source/time range.')
    if not data.get('examples') or not data.get('rejected_words'):
        raise SpokenStyleError('Spoken-style reference is incomplete.')
    return data


def normalized(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    return text.replace('\u200b', '').replace('\u200c', '').replace('\u200d', '')


def contains_word(text, word):
    def letter(character):
        return unicodedata.category(character)[0] in 'LNM' or character == '_'
    return any((match.start() == 0 or not letter(text[match.start() - 1]))
               and (match.end() == len(text) or not letter(text[match.end()]))
               for match in re.finditer(re.escape(word), text))


def vocabulary_issues(script, reference=None):
    if not isinstance(script, str):
        raise SpokenStyleError('Spoken script must be text.')
    reference = reference or load_reference()
    text = normalized(script)
    found = []
    for rule in reference['rejected_words']:
        matches = [form for form in rule['forms'] if contains_word(text, normalized(form))]
        if matches:
            found.append({'rule': rule['id'], 'words': matches, 'suggestion': rule['suggestion']})
    return found


def require_spoken_style(script):
    issues = vocabulary_issues(script)
    if issues:
        raise SpokenStyleError('Use familiar spoken wording before audio generation: '
                               + ' '.join(', '.join(item['words']) + ': ' + item['suggestion']
                                          for item in issues))
    return script


def excluded_source_ids(path=EXCLUSIONS):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    ids = data.get('youtube_ids')
    if data.get('format') != 'speech-source-exclusions-v1' or not isinstance(ids, dict):
        raise SpokenStyleError('Speech-source exclusion ledger is invalid.')
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{11}', key) for key in ids):
        raise SpokenStyleError('Speech-source exclusion ledger has an invalid video ID.')
    return set(ids)


def corpus_source_excluded(raw):
    header = '\n'.join(line for line in raw.splitlines() if line.startswith('#'))
    ids = set(re.findall(r'(?:watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])', header))
    return bool(ids & excluded_source_ids())


def eligible_recorded_source(video_id, channel_id, api_key, http_get=None):
    if video_id in excluded_source_ids():
        return False
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id) or not channel_id or not api_key:
        raise SpokenStyleError('YouTube source verification needs a valid video, MAIN channel and API key.')
    if http_get is None:
        import requests
        http_get = requests.get
    try:
        response = http_get('https://www.googleapis.com/youtube/v3/videos',
                            params={'id': video_id, 'part': 'snippet,status', 'key': api_key}, timeout=15)
        if response.status_code != 200:
            raise ValueError('Metadata request failed')
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get('items'), list):
            raise ValueError('Invalid metadata response')
        if not data['items']:
            return False
        if len(data['items']) != 1:
            raise ValueError('Ambiguous metadata response')
        item = data['items'][0]
        if item.get('id') != video_id or not isinstance(item.get('status'), dict):
            raise ValueError('Invalid video metadata')
        if item.get('snippet', {}).get('channelId') != channel_id:
            return False
        status = item['status']
        synthetic = status.get('containsSyntheticMedia')
        if (synthetic is not None and type(synthetic) is not bool
                or status.get('privacyStatus') not in {'public', 'unlisted', 'private'}):
            raise ValueError('Invalid status metadata')
        if synthetic is None:
            reviewed = {source.get('related_main_video_id') for source in load_reference()['sources'].values()}
            return video_id in reviewed
        return not synthetic
    except Exception:
        raise SpokenStyleError('YouTube speech-source verification unavailable; do not collect captions or transcribe.') from None


def style_prompt(script_system='devanagari'):
    if script_system not in {'devanagari', 'roman'}:
        raise SpokenStyleError('Choose devanagari or roman script.')
    reference = load_reference()
    key = 'roman' if script_system == 'roman' else 'hindi'
    lines = [
        'RECORDED SPOKEN STYLE — prompt reference, not model training.',
        'Use the ordinary Hindi/Hinglish register in these original recording fragments.',
        'They are normalized transcript fragments, not pronunciation approval or product evidence.',
        'Imitate sentence structure, not stock, prices, promises or website pitches.',
        'Keep useful business/technical words and explain them. Do not force ornate Hindi.',
        'Use a complete explanation and a settled conclusion; do not add filler mechanically.',
        'Write Roman Hinglish.' if key == 'roman' else 'Write natural Hindi in Devanagari with familiar business words.',
    ]
    for example in reference['examples']:
        lines.append('- ' + example[key] + ' | ' + example['lesson'])
    lines.append('Explicit wording preferences — rewrite the sentence, never blindly replace a word:')
    for rule in reference['rejected_words']:
        lines.append('- Avoid ' + ', '.join(rule['forms'][:3]) + '. ' + rule['suggestion'])
    lines.append('A new word is not forbidden merely because it is absent from this small reference. '
                 'Generated dialogue, daily BOT narration and descriptions must never become style exemplars.')
    return '\n'.join(lines)


def document_scripts(document, top_level=True):
    if isinstance(document, str):
        return [('script', document)] if top_level else []
    if isinstance(document, list):
        return [item for child in document for item in document_scripts(child, False)]
    if isinstance(document, dict):
        found = []
        for key, value in document.items():
            if key in {'script', 'script_voice'} and isinstance(value, str):
                found.append((str(document.get('id', key)), value))
            elif isinstance(value, (list, dict)):
                found.extend(document_scripts(value, False))
        return found
    return []


def main():
    parser = argparse.ArgumentParser(description='Local preparation/reference and vocabulary check; no providers or publication.')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prompt', action='store_true')
    mode.add_argument('--check', type=Path)
    parser.add_argument('--script-system', choices=('devanagari', 'roman'), default='devanagari')
    args = parser.parse_args()
    if args.prompt:
        print(style_prompt(args.script_system))
        return
    content = args.check.read_text(encoding='utf-8')
    scripts = document_scripts(json.loads(content) if args.check.suffix == '.json' else content)
    if not scripts:
        raise SystemExit('No spoken scripts found.')
    findings = [{'id': name, 'issues': vocabulary_issues(script)} for name, script in scripts]
    print(json.dumps({'checked_scripts': len(findings), 'scripts': findings,
                      'scope': 'Wording gate only; facts, pronunciation and release still require their existing checks.'},
                     ensure_ascii=False, indent=2))
    if any(item['issues'] for item in findings):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
