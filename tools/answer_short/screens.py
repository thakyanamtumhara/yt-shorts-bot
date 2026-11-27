"""Real pages of our own websites as a second picture source for the answer Short ("screens").

Some searched questions are about a thing that lives on the website: the size chart, the DTF order page, the page
where pieces are typed. A product clip cannot show that; the page itself can. A screen is a picture of one part of
a public page, taken this run by a headless browser at phone width, so whatever it shows is live.

Pages are only opened and read. No cart, no checkout and no form is touched: the checkout holds stock for a buyer.

Nothing here may stop a Short. No browser, a page that fails, a picture that looks wrong = that line keeps its
product picture, and the log says why.
"""

from pathlib import Path
import re

from PIL import Image, ImageStat

SITE = 'https://www.bulkplaintshirt.com'
DTF = 'https://dtf.bulkplaintshirt.com/'
PHONE = {'viewport': {'width': 390, 'height': 844}, 'device_scale_factor': 3, 'is_mobile': True, 'has_touch': True,
         'locale': 'en-IN', 'timezone_id': 'Asia/Kolkata',
         'user_agent': ('Mozilla/5.0 (Linux; Android 14; SM-S911B) AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/131.0.0.0 Mobile Safari/537.36')}
MAX_SCREENS = 2
# Never shown or named in a Short: closed products and products not launched yet (facts.CLOSED says the same).
NOT_SHOWN = re.compile(r'varsity|boxy', re.I)
BROKEN = re.compile(r'page not found|does not exist|404|something went wrong', re.I)

# fact id → the page part that shows it. "what" is the line the writer reads.
SCREENS = {
    'size_chart_on_site': {'what': 'the real size chart (chest, length, shoulder in inches) of the product in "show"',
                           'bar': 'bulkplaintshirt.com', 'by_product': True},
    'order_from_one_piece': {'what': 'the real ordering page: every product, colours down the side, sizes across, '
                                     'a box to type the pieces', 'bar': 'bulkplaintshirt.com'},
    'dtf_rate': {'what': 'the real DTF order page: its price per metre and the three steps',
                 'bar': 'dtf.bulkplaintshirt.com', 'page': 'dtf_page'},
    'dtf_how_it_works': {'what': 'the real DTF order page: upload designs, see the sheet and price, we print and deliver',
                         'bar': 'dtf.bulkplaintshirt.com', 'page': 'dtf_page'},
}


def offered(sheet):
    """{fact id: what it shows} for the facts that are known today."""
    return {key: spec['what'] for key, spec in SCREENS.items() if key in sheet['facts']}


def key_for(line, sheet):
    """The picture a line's screen needs. A size chart belongs to the product the line shows; two facts shown on
    the same page share one picture, so its second showing scrolls on instead of starting again."""
    fact = line.get('screen') or ''
    spec = SCREENS.get(fact) or {}
    if spec.get('by_product'):
        return fact + '@' + str(sheet['visuals'].get(line.get('show')) or '')
    return spec.get('page') or fact


def tidy(plan, sheet):
    """Keeps only screens that may be shown and returns their fact ids. A wrong "screen" is dropped, never a reason
    to refuse the plan: not the hook, not the closing question, not under a rate card, a cited fact, two at most."""
    lines = plan.get('lines')
    if not isinstance(lines, list):
        return []
    known, cited, kept = offered(sheet), plan.get('fact_ids') or [], []
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            continue
        fact = line.get('screen') or ''
        fine = (isinstance(fact, str) and fact in known and fact in cited and 0 < index < len(lines) - 1
                and not line.get('card') and len(kept) < MAX_SCREENS
                and not (SCREENS[fact].get('by_product') and '-sale' in str(sheet['visuals'].get(line.get('show')) or '')))
        if fine:
            kept.append(fact)
        else:
            line['screen'] = ''
    return kept


def looks_right(path):
    """A picture a buyer can read: big enough and not one flat colour."""
    try:
        image = Image.open(path).convert('L')
    except OSError:
        return False
    return image.width >= 900 and image.height >= 420 and ImageStat.Stat(image).stddev[0] >= 12


def _slug(text):
    return re.sub(r'[^a-z0-9]', '', str(text).lower())


def _size_chart(page, folder, out):
    page.goto(f'{SITE}/?tab={_slug(folder)}', wait_until='load', timeout=45000)
    page.wait_for_selector('#navbar button', timeout=20000)
    page.wait_for_timeout(1500)
    page.evaluate('sizchart()')
    body = page.locator('#cgszbody')
    body.wait_for(state='visible', timeout=20000)
    page.wait_for_timeout(1200)
    text = body.inner_text()
    title = _slug(page.locator('.cg-sztitle').first.inner_text().split('—')[0])
    want = _slug(folder)
    if not title or not (title.startswith(want) or want.startswith(title)):
        raise ValueError(f'the chart on the page is "{title}", not {folder}')
    if 'Chest' not in text or len(re.findall(r'\d', text)) < 8:
        raise ValueError('the size chart has no numbers')
    body.screenshot(path=str(out))
    return text


def _order_page(page, folder, out):
    page.goto(SITE + '/', wait_until='load', timeout=45000)
    page.wait_for_selector('#navbar button', timeout=20000)
    page.locator('table.pky').first.wait_for(state='visible', timeout=20000)
    page.wait_for_timeout(2500)
    if page.locator('#navbar button').count() < 8:
        raise ValueError('the product bar is not there')
    # From the product bar down: the products, then the colour and size grid where the pieces are typed.
    box = page.locator('#navbar').bounding_box()
    top = box['y'] + page.evaluate('window.scrollY')
    page.screenshot(path=str(out), full_page=True, clip={'x': 0, 'y': top, 'width': 390, 'height': 640})
    return page.locator('#navbar').inner_text() + ' ' + page.locator('table.pky').first.inner_text()


def _dtf_page(page, folder, out, rate=None):
    page.goto(DTF, wait_until='load', timeout=45000)
    intro = page.locator('section#intro')
    intro.wait_for(state='visible', timeout=20000)
    page.wait_for_timeout(2500)
    text = intro.inner_text()
    if rate and f'₹{rate}' not in text.replace(',', ''):
        raise ValueError(f'the DTF page does not show ₹{rate}')
    if 'Upload' not in text:
        raise ValueError('the DTF page has no upload step')
    intro.screenshot(path=str(out))
    return text


TAKE = {'size_chart_on_site': _size_chart, 'order_from_one_piece': _order_page, 'dtf_rate': _dtf_page,
        'dtf_how_it_works': _dtf_page}


def capture(plan, sheet, out_dir, log=print):
    """{screen key: file} for the screens this plan asks for. A line whose screen could not be taken loses it."""
    wanted = {}
    for line in plan['lines']:
        if line.get('screen'):
            wanted.setdefault(key_for(line, sheet), line)
    files = {}
    if wanted:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log('   ⚠️ website screens: no browser tool on this machine; product pictures are used instead')
            wanted = {}
    if wanted:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            with sync_playwright() as tool:
                browser = tool.chromium.launch()
                for key, line in wanted.items():
                    fact, folder = line['screen'], sheet['visuals'].get(line.get('show')) or ''
                    out = out_dir / (re.sub(r'[^a-z0-9]+', '-', key.lower()).strip('-') + '.png')
                    context = browser.new_context(**PHONE)
                    try:
                        page = context.new_page()
                        extra = {}
                        if fact.startswith('dtf_'):
                            amounts = (sheet['facts'].get('dtf_rate') or {}).get('amounts') or []
                            extra['rate'] = amounts[0] if amounts else None
                        text = TAKE[fact](page, folder, out, **extra)
                        if BROKEN.search(page.title()) or NOT_SHOWN.search(text):
                            raise ValueError('the page shows an error or a product we do not show')
                        if not looks_right(out):
                            raise ValueError('the picture is blank or too small')
                        files[key] = str(out)
                    except Exception as error:
                        log(f'   ⚠️ website screen {key} not taken ({type(error).__name__}: {str(error)[:140]})')
                    finally:
                        context.close()
                browser.close()
        except Exception as error:
            log(f'   ⚠️ website screens: the browser did not start ({type(error).__name__}: {str(error)[:140]})')
    for line in plan['lines']:
        if line.get('screen') and key_for(line, sheet) not in files:
            line['screen'] = ''
    return files
