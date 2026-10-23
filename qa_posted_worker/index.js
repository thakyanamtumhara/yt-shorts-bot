// The "Posted" tick of the answer posting page, kept in the cloud so Ketu's phone and the helper's phone see the same
// state and the Monday Telegram can count last week's posts (quora_answers.py --mode tell). The page and the
// workflow carry QA_POSTED_TOKEN (GitHub secret, also this Worker's secret); nothing works without it.
//   POST /tick  {t, id, date, url, posted, by}  posted:false removes the tick; a repeat tick keeps the first time
//   GET  /state?t=...&from=YYYY-MM-DD           every tick on or after that date
const ORIGINS = ['https://www.bulkplaintshirt.com', 'https://bulkplaintshirt.com'];
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const ID = /^[0-9a-f]{16}$/;

function same(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let x = 0;
  for (let i = 0; i < a.length; i++) x |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return x === 0;
}

export default {
  async fetch(req, env) {
    const origin = req.headers.get('origin') || '';
    const h = {
      'access-control-allow-origin': ORIGINS.includes(origin) ? origin : ORIGINS[0],
      'access-control-allow-methods': 'GET, POST, OPTIONS',
      'access-control-allow-headers': 'content-type',
      'access-control-max-age': '86400',
      vary: 'origin',
    };
    const json = (o, status = 200) => new Response(JSON.stringify(o), {
      status, headers: { ...h, 'content-type': 'application/json', 'cache-control': 'no-store' },
    });
    if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: h });
    if (!env.QA_POSTED_TOKEN) return json({ ok: false, error: 'not configured' }, 500);
    const url = new URL(req.url);

    if (url.pathname === '/state' && req.method === 'GET') {
      if (!same(url.searchParams.get('t') || '', env.QA_POSTED_TOKEN)) return json({ ok: false, error: 'bad token' }, 403);
      const from = url.searchParams.get('from') || '';
      if (from && !DATE.test(from)) return json({ ok: false, error: 'bad from' }, 400);
      const records = [];
      let cursor;
      do {
        const page = await env.QA_POSTED.list({ prefix: 'p:', cursor });
        for (const k of page.keys) {
          if (from && k.name.slice(2, 12) < from) continue;
          if (k.metadata) records.push(k.metadata);
        }
        cursor = page.list_complete ? null : page.cursor;
      } while (cursor);
      return json({ ok: true, records });
    }

    if (url.pathname === '/tick' && req.method === 'POST') {
      let b;
      try { b = await req.json(); } catch (e) { return json({ ok: false, error: 'bad json' }, 400); }
      if (!same(String(b.t || ''), env.QA_POSTED_TOKEN)) return json({ ok: false, error: 'bad token' }, 403);
      if (!ID.test(String(b.id || '')) || !DATE.test(String(b.date || ''))) return json({ ok: false, error: 'bad id or date' }, 400);
      const key = 'p:' + b.date + ':' + b.id;
      if (b.posted === false) {
        await env.QA_POSTED.delete(key);
        return json({ ok: true, posted: false });
      }
      const old = await env.QA_POSTED.get(key, 'json');
      if (old) return json({ ok: true, posted: true, posted_at: old.posted_at });
      const rec = {
        id: b.id, date: b.date, url: String(b.url || '').slice(0, 300),
        posted_at: new Date().toISOString(), by: String(b.by || '').slice(0, 40),
      };
      await env.QA_POSTED.put(key, JSON.stringify(rec), { metadata: rec });
      return json({ ok: true, posted: true, posted_at: rec.posted_at });
    }

    return json({ ok: false, error: 'not found' }, 404);
  },
};
