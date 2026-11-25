"""Today's facts for the daily answer Short, each from a source we own.

  rate_*   the live website rate list, read this run (tools/current_rates.py)
  dtf_rate the live DTF price per metre, read this run from the DTF site
  others   answer_facts.json: reviewed facts about ordering, payment and delivery
  textile  daily_topic_lessons.json: the reviewed textile facts (fabric, GSM, printing)

A fact that cannot be read today is simply not offered, so the writer cannot use it.
The real pictures come from the website's own gallery (phphotos.js): product videos and photos.
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import urllib.request

from tools.current_rates import fetch_catalogue, parse_catalogue, product_rates, rate_facts, fact_id

ROOT = Path(__file__).resolve().parents[2]
SITE = 'https://www.bulkplaintshirt.com'
GALLERY_URL = SITE + '/phphotos.js'
DTF_META_URL = 'https://dtf.bulkplaintshirt.com/api/meta'
IST = timezone(timedelta(hours=5, minutes=30))
# Never shown or named: closed products, and products not launched yet.
CLOSED = re.compile(r'varsity|boxy', re.I)
# Gallery clips kept out of the Shorts: a car number plate can be read in them (seen 25-Nov-2026). They stay on the
# website gallery; a new clip with a plate goes here too (the final look also refuses a readable plate).
BLOCKED_CLIPS = {'true-bio-rneck-navy.mp4', 'true-bio-rneck-grey.mp4', 'kids-polo-sale.mp4', 'kids-hoodie-black.mp4',
                 'sale-pant-black.mp4'}
FOLDERS = {'acidwash os': 'acidwash-os', 'sublimation tshirt': 'sublimation', 'dropsho hoodie 430gsm': 'hoodie-430gsm',
           'sale: polo tshirt': 'cotton-polo-sale', 'sale: shorts': 'shorts'}


def _get(url, timeout=20):
    request = urllib.request.Request(url, headers={'User-Agent': 'yt-shorts-bot answer short', 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode('utf-8')


def gallery(text=None):
    """{folder: {'videos': [url], 'posters': [url], 'photos': [{'u': url, 'c': colour}]}} from the website gallery."""
    text = _get(GALLERY_URL) if text is None else text
    decoder = json.JSONDecoder()
    found = {}
    for name in ('PH_PHOTOS', 'PH_VIDEOS'):
        match = re.search(r'window\.' + name + r'\s*=\s*', text)
        found[name] = decoder.raw_decode(text[match.end():])[0] if match else {}
    out = {}
    for folder, items in found['PH_PHOTOS'].items():
        if CLOSED.search(folder):
            continue
        out.setdefault(folder, {'videos': [], 'posters': [], 'photos': []})['photos'] = [
            {'u': SITE + item['u'], 'c': item.get('c') or ''} for item in items
            if isinstance(item, dict) and str(item.get('u', '')).endswith('.webp')]
    for folder, items in found['PH_VIDEOS'].items():
        if CLOSED.search(folder):
            continue
        entry = out.setdefault(folder, {'videos': [], 'posters': [], 'photos': []})
        for item in items:
            if (isinstance(item, dict) and str(item.get('src', '')).endswith('.mp4')
                    and item['src'].rsplit('/', 1)[-1] not in BLOCKED_CLIPS):
                entry['videos'].append(SITE + item['src'])
                entry['posters'].append(SITE + item['poster'] if item.get('poster') else '')
    return out


def folder_for(product, folders):
    """The gallery folder of a rate-list product, or None."""
    name = product.strip().lower()
    if name in FOLDERS and FOLDERS[name] in folders:
        return FOLDERS[name]
    sale = name.startswith('sale:')
    slug = re.sub(r'[^a-z0-9]+', '-', re.sub(r'^sale:\s*', '', name)).strip('-')
    slug = re.sub(r'-\d$', '', slug)
    for candidate in ([slug + '-sale'] if sale else []) + [slug]:
        if candidate in folders:
            return candidate
    return None


def dtf_fact(today, text=None):
    meta = json.loads(_get(DTF_META_URL) if text is None else text)
    paise, gst = meta.get('pricePerMetrePaise'), meta.get('gstBp')
    if type(paise) is not int or type(gst) is not int or paise % 100 or not 5000 <= paise <= 100000 or meta.get('acceptingOrders') is not True:
        raise ValueError('DTF site price unreadable or orders closed')
    rate = paise // 100
    return {'dtf_rate': {
        'claim': (f"dtf.bulkplaintshirt.com live price checked on {today}: DTF print ₹{rate} per metre of a 22.8 inch wide "
                  f"sheet, plus {gst / 100:g}% GST and delivery."),
        'source_url': 'https://dtf.bulkplaintshirt.com/', 'source_title': 'DTF print site live price',
        'checked_on': today, 'amounts': [rate],
        'limits': ('Website price on the checked date only. Quote it exactly, per metre, before GST. Never round it, '
                   'never call it cheapest, never state a price per T-shirt or per design.')}}


def card_for(row):
    """What the on-screen rate card shows for one product: only numbers from the live rate list."""
    tiers = [{'sizes': (sizes[0] if len(sizes) == 1 else f'{sizes[0]} – {sizes[-1]}'), 'rate': price}
             for price, sizes in row['bulk'].items()]
    return {'product': re.sub(r'-\d$', '', row['product']), 'detail': row['description'], 'tiers': tiers,
            'sample': row['sample'], 'bulk_from': row['bulk_from']}


def build(today=None):
    today = today or datetime.now(IST).date().isoformat()
    tbl = parse_catalogue(fetch_catalogue())
    rows = [row for row in product_rates(tbl) if not CLOSED.search(row['product'])]
    facts = {key: fact for key, fact in rate_facts(tbl, today).items() if not CLOSED.search(key)}
    pictures = gallery()
    cards, visuals = {}, {}
    for row in rows:
        key = fact_id(row['product'])
        if key not in facts:
            continue
        cards[key] = card_for(row)
        folder = folder_for(row['product'], pictures)
        if folder:
            visuals[key] = folder
    notes = []
    try:
        facts.update(dtf_fact(today))
    except Exception as error:
        notes.append(f'DTF price not read ({type(error).__name__}); DTF questions are skipped today')
    own = json.loads((ROOT / 'answer_facts.json').read_text(encoding='utf-8'))
    if own.get('format') != 'answer-facts-v1' or own.get('reviewed') is not True:
        raise ValueError('answer_facts.json is not reviewed')
    for key, fact in own['facts'].items():
        if key == 'dtf_how_it_works' and 'dtf_rate' not in facts:
            continue
        facts[key] = fact
    try:
        bank = json.loads((ROOT / 'daily_topic_lessons.json').read_text(encoding='utf-8'))
        for key, fact in (bank.get('facts') or {}).items():
            if key not in facts and not fact.get('announcement_only') and not key.startswith('dtf_'):
                facts[key] = {name: fact[name] for name in ('claim', 'source_url', 'source_title', 'checked_on', 'limits')}
    except (OSError, ValueError, KeyError) as error:
        notes.append(f'textile facts not read ({type(error).__name__})')
    return {'today': today, 'facts': facts, 'cards': cards, 'visuals': visuals, 'gallery': pictures, 'notes': notes}


def allowed_amounts(facts, cited):
    """Rupee amounts a script may say: each cited fact's own amounts, the difference of two cited rates, and a
    cited rate times a round quantity (the cost of 10, 20, 25, 50 or 100 pieces; 1, 2, 5 or 10 metres of DTF)."""
    own, rates = set(), set()
    for key in cited:
        values = [value for value in (facts.get(key) or {}).get('amounts') or [] if type(value) is int]
        own.update(values)
        if key.startswith('rate_'):
            rates.update(values)
            for value in values:
                own.update(value * quantity for quantity in (10, 20, 25, 50, 100))
        if key == 'dtf_rate':
            for value in values:
                own.update(value * quantity for quantity in (2, 5, 10))
    ordered = sorted(rates)
    own.update(b - a for index, a in enumerate(ordered) for b in ordered[index + 1:])
    return own
