#!/usr/bin/env python3
"""Weekly growth pull for bulkplaintshirt.com. Appends one row to growth/weekly.json.

Credentials: env GSC_TOKEN (the authorized-user JSON) in CI, else ~/.gsc_token.json locally.
The brand list must never change - every row is compared against the 17-Oct-2026 baseline.
"""
import json, os, pathlib, datetime as dt
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SITE = 'sc-domain:bulkplaintshirt.com'
SCOPES = ["https://www.googleapis.com/auth/webmasters"]
BRAND = ('bulkplain', 'sale91', 'own knitted', 'ownknitted', 'ketu', 'sales91',
         'sale 91', 'bulk plain tshirt.com', 'bulktshirt', 'plainbulk', 'bulkprint')
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / 'weekly.json'

def creds():
    raw = os.environ.get('GSC_TOKEN')
    if raw:
        return Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    return Credentials.from_authorized_user_file(os.path.expanduser('~/.gsc_token.json'), SCOPES)

svc = build('searchconsole', 'v1', credentials=creds(), cache_discovery=False)
today = dt.date.today()
def d(n): return (today - dt.timedelta(days=n)).isoformat()

def q(start, end, dims=None, limit=25, filters=None):
    body = {'startDate': start, 'endDate': end, 'rowLimit': limit}
    if dims: body['dimensions'] = dims
    if filters: body['dimensionFilterGroups'] = filters
    return svc.searchanalytics().query(siteUrl=SITE, body=body).execute().get('rows', [])

IND = [{'filters': [{'dimension': 'country', 'operator': 'equals', 'expression': 'ind'}]}]
row = {'pulled': today.isoformat(), 'window_28d': f"{d(31)}..{d(3)}"}

tot = q(d(31), d(3), filters=IND)
if tot:
    x = tot[0]
    row['india_28d'] = {'clicks': round(x['clicks']), 'impressions': round(x['impressions']),
                        'ctr_pct': round(x['ctr'] * 100, 2), 'avg_position': round(x['position'], 1)}

qs = q(d(31), d(3), ['query'], 25000, IND)
nb = [r for r in qs if not any(b in r['keys'][0].lower() for b in BRAND)]
br = [r for r in qs if any(b in r['keys'][0].lower() for b in BRAND)]
row['india_non_brand_28d'] = {'clicks': round(sum(r['clicks'] for r in nb)),
                              'impressions': round(sum(r['impressions'] for r in nb)),
                              'distinct_queries': len(nb)}
row['india_brand_28d'] = {'clicks': round(sum(r['clicks'] for r in br))}

pages = q(d(31), d(3), ['page'], 25000)
dtf = [r for r in pages if r['keys'][0].startswith('https://dtf.')]
row['dtf_site_28d'] = {'clicks': round(sum(r['clicks'] for r in dtf)),
                       'impressions': round(sum(r['impressions'] for r in dtf))}

watch = ['240 gsm t shirt', 'wholesale t shirts', 'bulk t shirts', 'tshirt manufacturer',
         'plain t shirt wholesale', 't shirt manufacturer in delhi', '320 gsm hoodie']
pos = {}
for r in qs:
    k = r['keys'][0].lower()
    if k in watch:
        pos[k] = {'position': round(r['position'], 1), 'clicks': round(r['clicks']),
                  'impressions': round(r['impressions'])}
row['watched_queries'] = pos

rows = json.loads(OUT.read_text()) if OUT.exists() else []
rows = [r for r in rows if r.get('pulled') != row['pulled']]
rows.append(row)
rows.sort(key=lambda r: r['pulled'])
OUT.write_text(json.dumps(rows, indent=2) + "\n")
print(json.dumps(row, indent=2))
print(f"\n{len(rows)} row(s) in {OUT}")
