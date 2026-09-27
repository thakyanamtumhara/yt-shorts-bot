"""Real current rates from the live shop catalogue, as dated facts a lesson may cite.

pc.js is the whole BulkPlainTshirt.com catalogue: tbl[0] = product → colour →
size → rate for 10+ pieces, tbl[1] = [code, description], tbl[2] = rate per
piece under 10, tbl[3].moq = 9 (bulk rate from 10 pieces), tbl[6].gst = 5
(added at checkout). "-1"/"-2" rows are colour bands of one garment, so their
facts name the colours. Read the CloudFront origin: the www copy is
edge-cached for hours and once served a day-old catalogue.

A rupee amount in public copy is allowed only when it is an exact rate from
the cited facts (or the difference between two of them). Anything else is the
invented-number defect this pipeline was stopped for in Aug 2026.
"""

import itertools
import json
import re
import urllib.request

CATALOGUE_URL = 'https://d2fzc2z1ecgedb.cloudfront.net/pc.js'
SHOP_URL = 'https://www.bulkplaintshirt.com/'
DIGITS = str.maketrans('०१२३४५६७८९', '0123456789')
RUPEE_WORD = r'(?:\b(?:rupa?y[ae]e?s?|rupaiye|rupees?|rupya)\b|रुपये|रुपए|रूपये|रुपया|रुपयों)'
MONEY = re.compile(r'(?:₹|\brs\.?|\binr)\s*(\d[\d,]*(?:\.\d+)?)'
                   r'|(\d[\d,]*(?:\.\d+)?)\s*(?:/-\s*)?' + RUPEE_WORD, re.I)
RUPEE = re.compile(RUPEE_WORD, re.I)
LIMITS = ('Website rate on the checked date only; rates can change later. Excludes {gst}% GST, delivery and any '
          'discount. Quote an amount exactly as listed and call it the current website rate. Never round it, never '
          'call it permanent, cheapest or better value, never mention a discount, and never give a reason for a '
          'price difference beyond the product details listed in these facts.')


class RateError(RuntimeError):
    pass


def fetch_catalogue(url=CATALOGUE_URL, opener=urllib.request.urlopen):
    request = urllib.request.Request(url, headers={'User-Agent': 'yt-shorts-bot rate check',
                                                   'Cache-Control': 'no-cache'})
    with opener(request, timeout=20) as response:
        return response.read().decode('utf-8')


def parse_catalogue(text):
    text = (text or '').strip()
    if not text.startswith('let tbl='):
        raise RateError('Catalogue is not the live pc.js table')
    try:
        tbl = json.loads(text[len('let tbl='):].rstrip(';').strip())
    except ValueError as error:
        raise RateError('Catalogue does not parse') from error
    if (not isinstance(tbl, list) or len(tbl) != 12
            or not all(isinstance(tbl[index], dict) for index in (0, 1, 2, 3, 6))
            or type(tbl[3].get('moq')) is not int or type(tbl[6].get('gst')) not in (int, float)
            or len(tbl[0]) < 10):
        raise RateError('Catalogue shape changed; no rate facts')
    return tbl


def product_rates(tbl):
    bulk_from = tbl[3]['moq'] + 1
    rows = []
    for product, colours in tbl[0].items():
        sample, detail = tbl[2].get(product), tbl[1].get(product)
        if (not isinstance(colours, dict) or not colours or type(sample) is not int or not 0 < sample < 100000
                or not isinstance(detail, list) or len(detail) != 2
                or not all(isinstance(part, str) and part.strip() for part in detail)):
            continue
        first = next(iter(colours.values()))
        # Colour-dependent prices inside one row would need guessing; such a row is left out.
        if (not isinstance(first, dict) or not first or any(sizes != first for sizes in colours.values())
                or any(type(price) is not int or not 0 < price < 100000 for price in first.values())):
            continue
        tiers = {}
        for size, price in first.items():
            tiers.setdefault(price, []).append(size)
        rows.append({'product': product, 'code': detail[0].strip(), 'description': detail[1].strip(),
                     'colours': list(colours), 'bulk': tiers, 'sample': sample, 'bulk_from': bulk_from})
    return rows


def fact_id(product):
    return 'rate_' + re.sub(r'[^a-z0-9]+', '_', product.lower()).strip('_')


def rate_facts(tbl, checked_on):
    rows = product_rates(tbl)
    family = lambda row: re.sub(r'-\d+$', '', row['code'])
    sizes = {}
    for row in rows:
        sizes[family(row)] = sizes.get(family(row), 0) + 1
    gst = f"{tbl[6]['gst']:g}"
    facts = {}
    for row in rows:
        colours = f" in {', '.join(row['colours'])}" if sizes[family(row)] > 1 else ''
        tiers = '; '.join(f"₹{price} per piece for sizes {', '.join(group)}" for price, group in row['bulk'].items())
        facts[fact_id(row['product'])] = {
            'claim': (f"BulkPlainTshirt.com live rate list checked on {checked_on}: {row['product']}{colours} "
                      f"({row['description']}). {row['bulk_from']} or more pieces: {tiers}. Fewer than "
                      f"{row['bulk_from']} pieces: ₹{row['sample']} per piece. Rates exclude {gst}% GST and delivery."),
            'source_url': SHOP_URL,
            'source_title': 'BulkPlainTshirt.com live rate list',
            'checked_on': checked_on,
            'limits': LIMITS.format(gst=gst),
            'amounts': sorted(set(row['bulk']) | {row['sample']}),
        }
    return facts


def money_amounts(text):
    values = []
    for match in MONEY.finditer((text or '').translate(DIGITS)):
        number = float((match.group(1) or match.group(2)).replace(',', ''))
        values.append(int(number) if number.is_integer() else number)
    return values


def allowed_amounts(facts):
    rates = {value for key, fact in (facts or {}).items() if key.startswith('rate_') and isinstance(fact, dict)
             for value in fact.get('amounts') or [] if type(value) is int}
    return rates | {abs(a - b) for a, b in itertools.combinations(sorted(rates), 2)}


def unsupported_amounts(text, allowed):
    return [value for value in money_amounts(text) if value not in allowed]


def loose_rupee_words(text):
    """A rupee word with no digits in front ('sau rupaye') cannot be checked against the rate list."""
    text = (text or '').translate(DIGITS)
    return [match.group(0) for match in RUPEE.finditer(text)
            if not re.search(r'\d\s*(?:/-)?\s*$', text[:match.start()])]


def cited_rates_unchanged(cited, live):
    """True when every cited rate fact still has exactly the same amounts in the live catalogue."""
    rates = {key: fact for key, fact in (cited or {}).items() if key.startswith('rate_')}
    return bool(rates) and all(key in live and live[key].get('amounts') == fact.get('amounts')
                               for key, fact in rates.items())
