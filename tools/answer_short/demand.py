"""What buyers type into YouTube and Google: the question list the daily answer Short picks from.

Source = the two public autocomplete services (the same suggestions a person sees while typing),
asked for India. A suggestion proves people search those words; it is NOT a search volume, and
nothing here says it is. The list is refreshed weekly (answer_demand.yml) and kept in
demand_bank.json, so a daily run never depends on the services answering that day.

    python -m tools.answer_short.demand --refresh      re-read the suggestions, write the bank
    python -m tools.answer_short.demand                print the top of the saved bank
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
BANK_PATH = ROOT / 'demand_bank.json'
IST = timezone(timedelta(hours=5, minutes=30))
SEEDS = [
    'plain t shirt wholesale', 'plain t shirt wholesale price', 'plain t shirt wholesale delhi',
    'blank t shirt wholesale', 'plain t shirt for printing', 'oversized t shirt wholesale',
    'oversized t shirt 240 gsm', 'drop shoulder t shirt wholesale', 'acid wash t shirt wholesale',
    't shirt gsm', '180 gsm t shirt', '210 gsm t shirt', '240 gsm t shirt', '260 gsm t shirt',
    'hoodie wholesale', 'plain hoodie wholesale', 'zipper hoodie wholesale', 'sweatshirt wholesale',
    'polo t shirt wholesale', 'kids t shirt wholesale', 'kids hoodie wholesale',
    'sublimation t shirt wholesale', 't shirt printing business', 't shirt printing business kaise start kare',
    't shirt ka business', 't shirt business kaise shuru kare', 'clothing brand kaise start kare',
    't shirt brand kaise banaye', 't shirt business profit margin', 't shirt wholesale',
    't shirt wholesale market delhi', 'sasti t shirt wholesale', 'wholesale t shirt kaha se kharide',
    'dtf printing', 'dtf print price', 'dtf sticker', 'dtf vs screen printing', 'dtf print kaise kare',
    't shirt dropshipping india', 'print on demand india', 't shirt size chart', 'oversized t shirt size chart',
    'biowash t shirt', 't shirt fabric', 'cotton t shirt wholesale', 'hoodie gsm', 't shirt cod wholesale',
    't shirt sample order', 'export surplus t shirt wholesale', 't shirt lot wholesale',
]
# Words that mark a search we cannot answer: other countries, other languages, machines, other shops.
SKIP = re.compile(
    r'\b(bangladesh|bd|pakistan|karachi|lahore|sri lanka|sinhala|nepal|uk|usa|us|kenya|nigeria|africa|johannesburg|'
    r'china|europe|los angeles|canada|philippines|dhaka|dubai|tamil|telugu|malayalam|kannada|bangla|marathi|'
    r'machine|printer price|h&m|flipkart|amazon|meesho|myntra|indiamart|reddit|salem|painting|roblox|drawing|'
    r'near me|women|ladies|girls|lower|jeans|shirt wholesale market in (?!delhi))\b', re.I)


def suggestions(query, source, opener=urllib.request.urlopen):
    url = ('https://suggestqueries.google.com/complete/search?client=firefox&hl=hi&gl=in&q='
           + urllib.parse.quote(query) + ('&ds=yt' if source == 'yt' else ''))
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with opener(request, timeout=15) as response:
        raw = response.read()
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        text = raw.decode('latin-1')
    found = json.loads(text)[1]
    return [item.strip().lower() for item in found if isinstance(item, str) and item.strip()]


def collect(seeds=SEEDS, fetch=suggestions, pause=0.35):
    found, failures = {}, 0
    for seed in seeds:
        for source in ('yt', 'google'):
            try:
                items = fetch(seed, source)
            except Exception:
                failures += 1
                continue
            for rank, query in enumerate(items):
                if SKIP.search(query) or not re.search(r'[a-z]', query) or len(query) > 90:
                    continue
                entry = found.setdefault(query, {'q': query, 'yt': 0, 'google': 0, 'rank': 99})
                entry[source] += 1
                entry['rank'] = min(entry['rank'], rank)
            if pause:
                time.sleep(pause)
    ranked = sorted(found.values(), key=lambda entry: (-(entry['yt'] > 0) - (entry['google'] > 0),
                                                       -(entry['yt'] + entry['google']), entry['rank'], entry['q']))
    return ranked, failures


def refresh(path=BANK_PATH, fetch=suggestions, pause=0.35, now=None):
    ranked, failures = collect(fetch=fetch, pause=pause)
    calls = len(SEEDS) * 2
    # A mostly failed read must never replace a good bank with a thin one.
    if failures > calls // 3 or len(ranked) < 120:
        raise RuntimeError(f'Search suggestions unreadable ({failures} of {calls} calls failed, {len(ranked)} found); bank kept')
    bank = {'format': 'demand-bank-v1', 'updated_on': (now or datetime.now(IST)).date().isoformat(),
            'source': 'YouTube and Google search suggestions for India (public autocomplete). Not search volume.',
            'queries': ranked[:600]}
    Path(path).write_text(json.dumps(bank, ensure_ascii=False, indent=0) + '\n', encoding='utf-8')
    return bank


def load(path=BANK_PATH):
    bank = json.loads(Path(path).read_text(encoding='utf-8'))
    if bank.get('format') != 'demand-bank-v1' or not isinstance(bank.get('queries'), list) or len(bank['queries']) < 50:
        raise RuntimeError('Search question bank is missing or too small')
    return bank


def age_days(bank, now=None):
    return ((now or datetime.now(IST)).date() - datetime.strptime(bank['updated_on'], '%Y-%m-%d').date()).days


if __name__ == '__main__':
    if '--refresh' in sys.argv:
        saved = refresh()
        print(f"saved {len(saved['queries'])} searched questions ({saved['updated_on']})")
    else:
        saved = load()
        print(f"{len(saved['queries'])} searched questions, read {saved['updated_on']}")
    for entry in saved['queries'][:40]:
        print(f"  yt {entry['yt']} google {entry['google']}  {entry['q']}")
