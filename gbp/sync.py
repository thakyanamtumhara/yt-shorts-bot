#!/usr/bin/env python3
"""New website photos and videos -> Google Business Profile, through Google's official Business Profile API.

Ketu, 19-Nov-2026: "Whatever new photos or new videos come on the website, you can post them on Google My Business"
and "small, small only coming, not bigger only coming". Google gave the business API access for its own profile the
same day, so nothing here drives a browser. This repo is public: no profile ids are written to the state.

The website's own public list (phphotos.js: PH_PHOTOS + PH_VIDEOS) is the only input. gbp/state.json remembers every
item ever seen. Everything on the site on the day of the baseline is marked "baseline" and never uploaded: only items
that appear later go up as "new", a few a day (GBP_MAX_PHOTOS / GBP_MAX_VIDEOS are the day's ceiling).

Since 20-Nov-2026 the older gallery goes up too, slowly: gbp/backlog.json is an ordered list of older items, each one
looked at before it was listed; GBP_OLD_PHOTOS / GBP_OLD_VIDEOS of them a day, only while the item is still on the
website and its product and colour are still in the live catalogue (/tmp/gbp_catalog.json, made by the workflow).

Modes: baseline | dry | run | heartbeat | whoami
"""
import datetime
import io
import json
import os
import re
import struct
import sys

import requests

SITE = 'https://www.bulkplaintshirt.com'
LIST_URL = SITE + '/phphotos.js'
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, 'state.json')
MAX_PHOTOS = int(os.environ.get('GBP_MAX_PHOTOS', '6'))
MAX_VIDEOS = int(os.environ.get('GBP_MAX_VIDEOS', '1'))
# Older gallery items (backlog.json): Ketu, 20-Nov-2026, "keep pushing slowly, maybe on an everyday basis ... just be
# safe". A few a day, inside the same daily ceiling as the new items, which always go first.
OLD_PHOTOS = int(os.environ.get('GBP_OLD_PHOTOS', '3'))
OLD_VIDEOS = int(os.environ.get('GBP_OLD_VIDEOS', '1'))
BACKLOG_FILE = os.path.join(HERE, 'backlog.json')
CATALOG_JSON = os.environ.get('GBP_CATALOG_JSON', '/tmp/gbp_catalog.json')
BUSINESS_TITLE = 'Own Knitted Blank Wears'
# Varsity is closed since 23-Sep-2026 (never show it anywhere); a gallery with "sale" in its name (cotton-polo-sale,
# sale-pant) is a closing-out lot.
SKIP_SLUGS = {'varsity-jacket'}
# Google's stated limits for a Business Profile: photos JPG / PNG 10 KB - 5 MB, at least 250 px (we ask 600, the
# website's own smallest gallery size, so a thumbnail never goes up); videos up to 30 s, 75 MB, 720p or more.
PHOTO_MIN, PHOTO_MAX, PHOTO_MIN_SIDE = 10 * 1024, 5 * 1024 * 1024, 600
VIDEO_MAX, VIDEO_MAX_SECONDS, VIDEO_MIN_SIDE = 75 * 1024 * 1024, 30.0, 720
NAMES = {
    'acidwash-os': 'Acid Wash Oversized T-shirt', 'bio-rneck': 'Bio Wash Round Neck T-shirt',
    'cotton-polo': 'Cotton Polo T-shirt', 'hoodie-320gsm': 'Hoodie 320 GSM', 'hoodie-430gsm': 'Drop Shoulder Hoodie 430 GSM',
    'kids-rneck': 'Kids Round Neck T-shirt', 'non-bio-rneck': 'Non Bio Round Neck T-shirt',
    'oversize-180gsm': 'Oversized T-shirt 180 GSM', 'oversize-210gsm': 'Oversized T-shirt 210 GSM',
    'oversize-240gsm': 'Oversized T-shirt 240 GSM', 'oversize-260gsm': 'Oversized T-shirt 260 GSM',
    'premium-polo': 'Premium Polo T-shirt', 'shorts': 'Shorts', 'sublimation': 'Sublimation T-shirt',
    'sweatshirt': 'Sweatshirt', 'true-bio-rneck': 'True Bio Round Neck T-shirt', 'zip-hoodie': 'Zip Hoodie',
    'kids-hoodie': 'Kids Hoodie',
}
UA = {'User-Agent': 'okbw-gbp-sync/1 (+https://www.bulkplaintshirt.com)'}


class Blind(Exception):
    pass


def now_ist():
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)


def today():
    return now_ist().strftime('%Y-%m-%d')


def tell(text):
    tok, chat = os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('TELEGRAM_ALERT_CHAT_ID')
    print('TELEGRAM>', text)
    if not tok or not chat or os.environ.get('GBP_NO_TELEGRAM'):
        return
    try:
        requests.post('https://api.telegram.org/bot%s/sendMessage' % tok, timeout=20,
                      data={'chat_id': chat, 'text': text, 'disable_web_page_preview': 'true'})
        open('/tmp/gbp_told', 'w').write('1')
    except Exception as e:
        print('telegram failed:', type(e).__name__)


def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE))
    return {'version': 1, 'seen': {}}


def save_state(st):
    with open(STATE_FILE, 'w') as f:
        json.dump(st, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write('\n')


def js_object(src, name):
    m = re.search(r'window\.%s\s*=\s*' % name, src)
    if not m:
        raise Blind('%s is not in phphotos.js' % name)
    obj, _ = json.JSONDecoder().raw_decode(src[m.end():])
    return obj


def website_items():
    """[(key, kind, slug, colour)] in the website's own order. key = the item's path on the website."""
    r = requests.get(LIST_URL, headers=UA, timeout=30)
    if r.status_code != 200:
        raise Blind('phphotos.js answered HTTP %s' % r.status_code)
    photos, videos = js_object(r.text, 'PH_PHOTOS'), js_object(r.text, 'PH_VIDEOS')
    out = []
    for slug, items in photos.items():
        for it in items:
            if it.get('u'):
                out.append((it['u'], 'photo', slug, it.get('c') or ''))
    for slug, items in videos.items():
        for it in items:
            if it.get('src'):
                out.append((it['src'], 'video', slug, it.get('color') or it.get('c') or ''))
    if len([x for x in out if x[1] == 'photo']) < 50:
        raise Blind('phphotos.js lists only %d photos' % len(out))
    return out


def skipped_slug(slug):
    return slug in SKIP_SLUGS or 'sale' in slug.split('-') or 'varsity' in slug.split('-')


def access_token():
    need = ('GBP_CLIENT_ID', 'GBP_CLIENT_SECRET', 'GBP_REFRESH_TOKEN')
    if not all(os.environ.get(k) for k in need):
        raise RuntimeError('Google key is missing (GBP_CLIENT_ID / GBP_CLIENT_SECRET / GBP_REFRESH_TOKEN)')
    r = requests.post('https://oauth2.googleapis.com/token', timeout=30, data={
        'client_id': os.environ['GBP_CLIENT_ID'], 'client_secret': os.environ['GBP_CLIENT_SECRET'],
        'refresh_token': os.environ['GBP_REFRESH_TOKEN'], 'grant_type': 'refresh_token'})
    if r.status_code != 200:
        raise RuntimeError('Google refused the saved permission (%s %s)' % (r.status_code, r.json().get('error', '')))
    return r.json()['access_token']


class Google:
    def __init__(self):
        self.h = {'Authorization': 'Bearer ' + access_token()}

    def call(self, method, url, **kw):
        r = requests.request(method, url, headers=dict(self.h, **kw.pop('headers', {})), timeout=kw.pop('timeout', 120), **kw)
        if r.status_code >= 300:
            try:
                msg = r.json().get('error', {}).get('message', '')
            except Exception:
                msg = r.text[:200]
            raise RuntimeError('Google %s on %s: %s' % (r.status_code, url.split('?')[0].split('/')[-1], msg))
        return r.json() if r.text.strip() else {}

    def target(self):
        """accounts/<id>/locations/<id> of the one verified profile, looked up on every run (never stored)."""
        accounts = self.call('GET', 'https://mybusinessaccountmanagement.googleapis.com/v1/accounts').get('accounts', [])
        found = []
        for a in accounts:
            locs = self.call('GET', 'https://mybusinessbusinessinformation.googleapis.com/v1/%s/locations' % a['name'],
                             params={'readMask': 'name,title', 'pageSize': 100}).get('locations', [])
            found += [(a['name'], l['name'], l.get('title', '')) for l in locs]
        mine = [f for f in found if f[2].strip().lower() == BUSINESS_TITLE.lower()]
        if len(mine) != 1:
            raise RuntimeError('expected one profile named %s, found %d (of %d locations)' % (BUSINESS_TITLE, len(mine), len(found)))
        return '%s/%s' % (mine[0][0], mine[0][1])

    def media_count(self, parent):
        d = self.call('GET', 'https://mybusiness.googleapis.com/v4/%s/media' % parent, params={'pageSize': 1})
        return d.get('totalMediaItemCount')

    def upload(self, parent, kind, data, description):
        ref = self.call('POST', 'https://mybusiness.googleapis.com/v4/%s/media:startUpload' % parent, json={})
        name = ref['resourceName']
        self.call('POST', 'https://mybusiness.googleapis.com/upload/v1/media/%s' % name, params={'upload_type': 'media'},
                  data=data, headers={'Content-Type': 'application/octet-stream'}, timeout=600)
        body = {'mediaFormat': 'PHOTO' if kind == 'photo' else 'VIDEO',
                'locationAssociation': {'category': 'ADDITIONAL'},
                'dataRef': {'resourceName': name}}
        if description:
            body['description'] = description
        return self.call('POST', 'https://mybusiness.googleapis.com/v4/%s/media' % parent, json=body).get('name', '')


def as_jpeg(raw):
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    im.load()
    if min(im.size) < PHOTO_MIN_SIDE:
        return None, 'too small (%dx%d)' % im.size
    if im.mode in ('RGBA', 'LA', 'P'):
        im = im.convert('RGBA')
        bg = Image.new('RGB', im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert('RGB')
    for q in (92, 88, 82, 75):
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=q, optimize=True)
        if buf.tell() <= PHOTO_MAX:
            break
    size = buf.tell()
    if size < PHOTO_MIN or size > PHOTO_MAX:
        return None, 'size %d bytes is outside Google\'s limits' % size
    return buf.getvalue(), ''


def mp4_facts(raw):
    """(seconds, width, height) from the mp4's own header, or None."""
    def boxes(buf, start, end):
        i = start
        while i + 8 <= end:
            size, kind = struct.unpack('>I4s', buf[i:i + 8])
            head = 8
            if size == 1:
                size = struct.unpack('>Q', buf[i + 8:i + 16])[0]
                head = 16
            elif size == 0:
                size = end - i
            if size < head:
                return
            yield kind, i + head, i + size
            i += size
    seconds, dims = None, []
    for kind, a, b in boxes(raw, 0, len(raw)):
        if kind != b'moov':
            continue
        for k2, a2, b2 in boxes(raw, a, b):
            if k2 == b'mvhd':
                v = raw[a2]
                ts, dur = struct.unpack('>IQ', raw[a2 + 20:a2 + 32]) if v == 1 else struct.unpack('>II', raw[a2 + 12:a2 + 20])
                seconds = dur / ts if ts else None
            if k2 == b'trak':
                for k3, a3, b3 in boxes(raw, a2, b2):
                    if k3 == b'tkhd':
                        w, h = struct.unpack('>II', raw[b3 - 8:b3])
                        if w and h:
                            dims.append((w >> 16, h >> 16))
    if seconds is None or not dims:
        return None
    w, h = max(dims, key=lambda d: d[0] * d[1])
    return seconds, w, h


def label(slug, colour):
    name = NAMES.get(slug)
    if not name:
        return ''
    return ('%s, %s' % (name, colour)) if colour and colour.lower() != 'assorted' else name


def norm_colour(c):
    c = re.sub(r'[^a-z]', '', (c or '').lower())
    return {'charcol': 'charcoal', 'biege': 'beige'}.get(c, c)


def live_catalogue():
    """{product: set(colours)} that the catalogue lists today, or None when the list could not be read."""
    try:
        d = json.load(open(CATALOG_JSON))
    except Exception:
        return None
    if not isinstance(d, dict) or len(d) < 10:
        return None
    return {name: set(norm_colour(c) for c in cols) for name, cols in d.items()}


def old_queue(seen, on_site, live):
    """Older gallery items (backlog.json) that may go up today, in their listed order.

    One goes up only while it is still on the website AND its product and colour are still in the catalogue. An item
    that fails that today is passed over, not dropped: it is looked at again on the next run.
    """
    if live is None or not os.path.exists(BACKLOG_FILE):
        return []
    out = []
    for e in json.load(open(BACKLOG_FILE))['items']:
        if seen.get(e['k'], {}).get('s') != 'baseline' or e['k'] not in on_site:
            continue
        if e['p'] not in live or (e['c'] and e['c'] not in live[e['p']]):
            continue
        out.append(e)
    return out


def old_waiting(seen):
    if not os.path.exists(BACKLOG_FILE):
        return 0
    return len([e for e in json.load(open(BACKLOG_FILE))['items'] if seen.get(e['k'], {}).get('s') == 'baseline'])


def is_video(key):
    return key.endswith('.mp4')


def counts(n_photos, n_videos):
    parts = (['%d photo%s' % (n_photos, '' if n_photos == 1 else 's')] if n_photos else []) + \
            (['%d video%s' % (n_videos, '' if n_videos == 1 else 's')] if n_videos else [])
    return ' and '.join(parts) or 'nothing'


def run(mode):
    st = load_state()
    seen = st.setdefault('seen', {})
    items = website_items()
    on_site = set(x[0] for x in items)
    now = now_ist()
    day = today()
    if mode == 'baseline':
        added = 0
        for key, kind, slug, colour in items:
            if key not in seen:
                seen[key] = {'s': 'baseline', 'at': day}
                added += 1
        st.setdefault('baseline_at', day)
        save_state(st)
        print('baseline: %d items on the website, %d newly marked' % (len(items), added))
        return
    fresh = []
    for key, kind, slug, colour in items:
        if key in seen:
            continue
        if skipped_slug(slug):
            if mode == 'run':
                seen[key] = {'s': 'skipped: closed or sale line', 'at': day}
            continue
        fresh.append({'k': key, 'kind': kind, 'slug': slug, 'd': label(slug, colour), 'src': 'new'})
    up_today = [k for k, v in seen.items() if v.get('s') == 'uploaded' and v.get('at') == day]
    room_p = max(0, MAX_PHOTOS - len([k for k in up_today if not is_video(k)]))
    room_v = max(0, MAX_VIDEOS - len([k for k in up_today if is_video(k)]))
    photos = [x for x in fresh if x['kind'] == 'photo'][:room_p]
    videos = [x for x in fresh if x['kind'] == 'video'][:room_v]
    left = len(fresh) - len(photos) - len(videos)
    live = live_catalogue()
    queue = old_queue(seen, on_site, live)
    old_p_today = len([k for k in up_today if not is_video(k) and seen[k].get('src') == 'old'])
    old_photos = [e for e in queue if e['kind'] == 'photo'][:max(0, min(OLD_PHOTOS - old_p_today, room_p - len(photos)))]
    old_videos = [e for e in queue if e['kind'] == 'video'][:max(0, min(OLD_VIDEOS, room_v - len(videos)))]
    old = [dict(e, src='old') for e in old_photos + old_videos]
    todo = photos + videos + old
    print('%d on the website | new: %d, %d this run, %d wait | older: %d waiting, %d may go, %d this run%s' % (
        len(items), len(fresh), len(photos) + len(videos), left, old_waiting(seen), len(queue), len(old),
        '' if live is not None else ' (product list unreadable: older items wait)'))
    if mode == 'dry':
        for x in todo:
            print('  would upload', x['src'], x['kind'], x['k'], '|', x['d'])
        return
    if live is None and old_waiting(seen):
        st['old_blind'] = sorted(set(st.get('old_blind', []) + [day]))[-14:]
    done, skipped, failed = [], [], None
    if todo:
        g = Google()
        parent = g.target()
    for x in todo:
        key, kind = x['k'], x['kind']
        try:
            r = requests.get(SITE + key, headers=UA, timeout=300)
            if r.status_code != 200:
                raise RuntimeError('website file answered HTTP %s' % r.status_code)
            raw = r.content
            if kind == 'photo':
                data, why = as_jpeg(raw)
            else:
                facts = mp4_facts(raw)
                why = ''
                if not facts:
                    why = 'could not read the video header'
                elif len(raw) > VIDEO_MAX:
                    why = 'larger than 75 MB'
                elif facts[0] > VIDEO_MAX_SECONDS:
                    why = 'longer than 30 seconds (%d s)' % round(facts[0])
                elif min(facts[1], facts[2]) < VIDEO_MIN_SIDE:
                    why = 'smaller than 720p (%dx%d)' % (facts[1], facts[2])
                data = None if why else raw
            if data is None:
                seen[key] = {'s': 'skipped: ' + why, 'at': day}
                skipped.append((key, why))
                continue
            name = g.upload(parent, kind, data, x['d'])
            seen[key] = {'s': 'uploaded', 'at': day, 'id': name.split('/')[-1], 'src': x['src']}
            done.append(x)
        except Exception as e:
            failed = '%s: %s' % (key, str(e)[:200])
            break
        finally:
            save_state(st)
    if done:
        st['last_upload'] = day
    new_done = [x for x in done if x['src'] == 'new']
    if new_done:
        names = []
        for x in new_done:
            n = x['d'] or x['slug']
            if n not in names:
                names.append(n)
        msg = 'Google Business: %s added from the website (%s). Google shows them after its own check.' % (
            counts(len([x for x in new_done if x['kind'] == 'photo']), len([x for x in new_done if x['kind'] == 'video'])),
            '; '.join(names[:6]))
        if left:
            msg += ' %d more wait for tomorrow (small batches).' % left
        tell(msg)
    for key, why in skipped:
        tell('Google Business: not uploaded, %s: %s. Nothing else is affected.' % (key, why))
    if failed:
        save_state(st)
        raise RuntimeError(failed)
    if os.path.exists(BACKLOG_FILE) and not st.get('old_done'):
        rest = old_queue(seen, on_site, live)
        rest_p = len([e for e in rest if e['kind'] == 'photo'])
        rest_v = len(rest) - rest_p
        olds = [(k, v) for k, v in seen.items() if v.get('s') == 'uploaded' and v.get('src') == 'old']
        if live is not None and not rest and olds:
            st['old_done'] = day
            msg = 'Google Business: all the older website photos and videos are now on the profile (%s over the weeks). From now only new website items go up.' % counts(
                len([k for k, v in olds if not is_video(k)]), len([k for k, v in olds if is_video(k)]))
            if old_waiting(seen):
                msg += ' %d were left out because that product or colour is no longer listed.' % old_waiting(seen)
            tell(msg)
        elif (st.get('old_note') or '') < (now - datetime.timedelta(days=now.weekday())).strftime('%Y-%m-%d'):
            # weekly note: on Monday, or on the first run after it when GitHub starts Monday's run past midnight IST
            st['old_note'] = day
            week = set((now - datetime.timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7))
            wk = [k for k, v in olds if v.get('at') in week]
            msg = 'Google Business, last 7 days: %s from the older website gallery added, a few each day.' % counts(
                len([k for k in wk if not is_video(k)]), len([k for k in wk if is_video(k)]))
            if live is not None:
                days = max(-(-rest_p // max(1, OLD_PHOTOS)), -(-rest_v // max(1, OLD_VIDEOS)))
                msg += ' Still to go: %s, about %d week%s.' % (counts(rest_p, rest_v), -(-days // 7), '' if days <= 7 else 's')
            blind = [d for d in st.get('old_blind', []) if d in week]
            if blind:
                msg += ' On %d day%s nothing older went up because the product list could not be read.' % (len(blind), '' if len(blind) == 1 else 's')
            tell(msg)
    save_state(st)


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else 'dry').strip()
    try:
        if mode == 'whoami':
            g = Google()
            print('profile:', BUSINESS_TITLE, '| media items on the profile:', g.media_count(g.target()))
        elif mode == 'heartbeat':
            st = load_state()
            seen = st.get('seen', {})
            up = [v for v in seen.values() if v.get('s') == 'uploaded']
            month = today()[:7]
            g = Google()
            count = g.media_count(g.target())
            tell('Google Business photo job is running and Google still accepts its permission. Uploaded by it so far: %d '
                 '(this month: %d), last upload: %s. Older website items still waiting: %d. Photos and videos on the profile now: %s.' % (
                     len(up), len([v for v in up if v.get('at', '').startswith(month)]), st.get('last_upload', 'none yet'), old_waiting(seen), count))
        elif mode in ('baseline', 'dry', 'run'):
            run(mode)
        else:
            raise SystemExit('unknown mode ' + mode)
    except Blind as e:
        tell('Google Business photo job is BLIND: it could not read the website\'s photo list (%s). Nothing was uploaded.' % e)
        raise SystemExit(1)
    except RuntimeError as e:
        tell('Google Business photo job FAILED: %s. It tries again tomorrow; nothing wrong was uploaded.' % e)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
