#!/usr/bin/env python3
"""Weekly Quora answers that Ketu posts by hand (his go, 18-Oct-2026: "Whatever is the safest, you can keep doing it").

Quora's terms (3.2) give no right to post answers with automated tools, and 4.4 bars AI access for commercial use,
so nothing here posts and no Quora page is ever fetched: Claude Opus sees only web-search results, picks real
question URLs from them, and writes one disclosed, price-free answer per question from FACTS. The batch goes to
Ketu's Telegram as "question + link", then the answer alone, so one long-press copies it. quora_answers/log.json
keeps every question and answer ever sent, so none repeats and a batch can be sent again (--mode resend). Ketu asked
18-Oct-2026 "how would I know if something breaks": a failed run tells him on Telegram, and --mode check (Tuesday)
does the same when Monday's batch never went out.

Later on 18-Oct-2026 Ketu gave the pasting to a helper who "will not be reading": so every answer also passes a
fact check by a second model call before it is kept, and the answers live on one fixed web page (--mode page, uploaded
by the workflow) with Copy and Open buttons; Ketu gets only the link and the questions (--mode tell).
"""
import argparse
import datetime
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request

MODEL = os.environ.get("QA_MODEL", "claude-opus-5-5")
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "quora_answers", "log.json")
WANT = 5
CANDIDATES = 7

FACTS = """Own Knitted Blank Wears sells plain (unprinted) apparel wholesale from Delhi. Websites: sale91.com and bulkplaintshirt.com (the same business).
- A manufacturer: knits its own fabric and handles yarn sourcing, knitting, dyeing, processing and stitching.
- Factory in Tiruppur (Tamil Nadu): fabric knitting, dyeing and processing; no visits. Warehouse and dispatch hub: the godown in Khanpur, South Delhi, which buyers can visit Mon-Sat 10 AM-6 PM, Sun 12-4 PM. Never say the garments are made, knitted, dyed or stitched in Delhi; say the business is based in Delhi or ships from Delhi.
- Products, all plain: regular round-neck T-shirts 180 GSM (100% cotton biowash, plus a cotton-polyester non-bio option); oversized / drop-shoulder T-shirts in 180, 210, 240 and 260 GSM; acid-wash oversized 240 GSM; sublimation T-shirts 200 GSM (cotton-feel polyester); kids round-neck 180 GSM; polo T-shirts 220 GSM (a cotton polo and a 100% cotton honeycomb premium polo); hoodies 320 GSM (pullover and zip); drop-shoulder hoodie 430 GSM; sweatshirts 320 GSM; shorts 240 GSM.
- DTF transfer sheets printed from the buyer's own designs, ordered separately on dtf.bulkplaintshirt.com.
- Minimum order: 10 pieces in total; colours, sizes and products can be mixed. Single-piece samples can be bought.
- Ships across India; courier delivery usually takes 2-5 days.
- Payment online (UPI, card, net banking); part cash-on-delivery on courier orders.
- A GST invoice with every order.
- Replaces pieces with genuine manufacturing defects (like holes).
- Blind dropshipping for resellers: the parcel goes to the reseller's customer without our details.
- Does not print T-shirts itself (only the DTF transfer sheets)."""

RULES = """Write like the owner talking to a buyer: plain, warm, specific English.
Useful first: most of the answer gives practical advice the asker can use with ANY supplier. Then a last paragraph that starts exactly "Disclosure: I run Own Knitted Blank Wears (sale91.com)" and adds one or two facts from FACTS that matter for this question.
Hard rules:
- 130 to 240 words. No headings, emojis, hashtags, bold or markdown. Short numbered or dash lists are fine.
- No links. "sale91.com" appears once, inside the disclosure.
- Never a price, rate, amount, discount or anything in rupees ("₹", "Rs", "INR", a number per piece).
- About the business, only facts from FACTS. No colour counts, no stock claims, no delivery promise beyond "usually 2-5 days".
- Never claim "best quality / best price / best supplier", "cheapest", "No. 1" or "guaranteed", and say nothing about other companies; name no other brand or seller.
- Never a phone number, e-mail address or person's name, and never "Surat", "varsity" or "jacket".
- General market facts are fine when true and well known (Tiruppur is India's knitwear hub; sublimation needs polyester).
- Every answer reads differently from the others: different opening, structure and examples."""

TOPICS = [
    "wholesale plain T-shirts in India", "blank T-shirts for printing (DTF, screen print, DTG)",
    "oversized or heavyweight drop-shoulder T-shirts wholesale", "plain hoodies or sweatshirts in bulk",
    "starting a T-shirt or clothing brand in India", "print on demand or dropshipping T-shirts in India",
    "which GSM or fabric is right for T-shirts", "sublimation T-shirt blanks", "polo T-shirts in bulk or uniforms",
    "kids plain T-shirts wholesale", "T-shirt wholesale market in Delhi", "custom T-shirt business suppliers",
]

BANNED = [
    (r"₹|\bRs\.?\s*\d|\bINR\b|\brupees?\b|/-", "a price"),
    (r"\b\d+\s*(?:per|/)\s*(?:piece|pc|pcs)\b", "a per-piece number"),
    (r"(?:\+?91[\s-]?)?\b[6-9]\d{4}[\s-]?\d{5}\b", "a phone number"),
    (r"[\w.+-]+@[\w-]+\.[\w.]+", "an e-mail address"),
    (r"https?://|www\.", "a link"),
    (r"\b(?:ankit|ketu|gupta)\b", "a person's name"),
    (r"\b(?:surat|varsity|jackets?)\b", "a word that must never appear"),
    (r"\b(?:cheapest|lowest|no\.?\s?1|number one|guarantee[sd]?)\b", "a superlative or guarantee"),
    (r"\b(?:the )?best\s+(?:quality|price|prices|rates?|supplier|manufacturer|wholesaler|seller|in india)\b", "a 'best' claim"),
    (r"\*\*|^#|#\w", "markdown or a hashtag"),
]


def norm_url(u):
    m = re.match(r"https?://(?:www\.)?quora\.com/([^?#]+)", (u or "").strip(), re.I)
    if not m:
        return None
    slug = m.group(1).strip("/").split("/")[0]
    if not slug or slug.lower() in {"profile", "topic", "q", "search", "about", "answer", "unanswered", "spaces"}:
        return None
    return "https://www.quora.com/" + slug


def problems(answer):
    out = []
    words = len(answer.split())
    if not 120 <= words <= 260:
        out.append(f"{words} words")
    for pat, why in BANNED:
        if re.search(pat, answer, re.I | re.M):
            out.append(why)
    if answer.lower().count("sale91.com") != 1:
        out.append("sale91.com must appear exactly once")
    if "Disclosure: I run Own Knitted Blank Wears (sale91.com)" not in answer:
        out.append("no disclosure line")
    return out


def body(answer):
    return answer.split("Disclosure:")[0].lower()


def shingles(text, n=8):
    w = re.findall(r"[a-z0-9]+", text)
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def load_log():
    try:
        with open(LOG) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def claude(messages, system, search=True):
    key = os.environ["ANTHROPIC_API_KEY"]
    body = {"model": MODEL, "max_tokens": 8000, "system": system, "messages": messages}
    if search:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8,
                          "allowed_domains": ["quora.com"]}]
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"Anthropic API {e.code}: {e.read()[:500]!r}")


def draft(seen, today):
    week = today.isocalendar()[1]
    topics = [TOPICS[(week * 4 + i) % len(TOPICS)] for i in range(4)]
    system = f"FACTS about the business:\n{FACTS}\n\nRULES for every answer:\n{RULES}"
    ask = (
        f"Today is {today:%d %b %Y}. Search quora.com for real questions that buyers of plain T-shirts, hoodies or "
        f"blank apparel in India ask. Topics for this week: {'; '.join(topics)}. Pick questions where an honest answer "
        f"from a plain apparel manufacturer genuinely helps the asker. Skip questions about printing services only, "
        f"jobs, investing or personal fashion, and skip these, already answered: {', '.join(sorted(seen)) or 'none'}.\n"
        f"Return {CANDIDATES} candidates in exactly this plain-text form, one block each, and nothing else:\n"
        "@@@ QUESTION: exact question title\n@@@ URL: https://www.quora.com/...\n@@@ ANSWER:\nthe answer\n@@@ END\n"
        "Every url must be one that appeared in your search results."
    )
    messages = [{"role": "user", "content": ask}]
    found, searches = set(), 0
    for _ in range(4):
        resp = claude(messages, system)
        for blk in resp.get("content", []):
            if blk.get("type") == "web_search_tool_result" and isinstance(blk.get("content"), list):
                for r in blk["content"]:
                    u = norm_url(r.get("url"))
                    if u:
                        found.add(u)
        searches += (resp.get("usage", {}).get("server_tool_use") or {}).get("web_search_requests", 0)
        if resp.get("stop_reason") != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": resp["content"]}]
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    print(f"web searches: {searches}, Quora question URLs seen: {len(found)}, usage: {resp.get('usage')}")
    cands = blocks(text)
    if not cands:
        sys.exit(f"No answers in the reply: {text[:400]!r}")
    return cands, found


def blocks(text):
    return [{"question": m.group(1).strip(), "url": m.group(2).strip(), "answer": m.group(3).strip()}
            for m in re.finditer(r"@@@ QUESTION:(.*?)\n@@@ URL:(.*?)\n@@@ ANSWER:[ \t]*\n(.*?)\n@@@ END", text, re.S)]


def pick(cands, found, seen, limit=WANT):
    kept, used = [], set()
    for c in cands:
        q, a, u = (c.get("question") or "").strip(), (c.get("answer") or "").strip(), norm_url(c.get("url"))
        why = problems(a)
        if not u:
            why.append("not a Quora question URL")
        elif u not in found:
            why.append("URL not in the search results")
        elif u in seen or u in {k["url"] for k in kept}:
            why.append("already answered")
        sh = shingles(body(a))
        if sh & used:
            why.append("repeats another answer")
        if why:
            print(f"SKIP {q!r}: {', '.join(why)}")
            continue
        used |= sh
        kept.append({"question": q, "url": u, "answer": a})
        if len(kept) == limit:
            break
    return kept


def fact_check(kept):
    ask = ("These answers will be pasted on Quora by someone who will not read them, so you are the last check. "
           "Fail an answer if it says anything about Own Knitted Blank Wears that FACTS do not say, gives advice that is "
           "wrong or could mislead a buyer, names, praises or criticises another seller or brand, or does not answer its "
           "question. True, well-known market facts (Tiruppur is India's knitwear hub, Delhi's wholesale markets) are "
           "fine.\n\n" + "\n\n".join(f"ANSWER {i}\nQuestion: {k['question']}\n{k['answer']}" for i, k in enumerate(kept)) +
           "\n\nReply with one line per answer and nothing else: its number, then OK, or FAIL and a short reason. "
           "Example:\n0 OK\n1 FAIL: says we print T-shirts")
    resp = claude([{"role": "user", "content": ask}], f"FACTS about the business:\n{FACTS}", search=False)
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    verdicts = verdict_lines(text)
    out = []
    for i, k in enumerate(kept):
        ok, why = verdicts.get(i, (False, "not checked"))
        if ok:
            out.append(k)
        else:
            print(f"FACT CHECK dropped {k['question']!r}: {why}")
    print(f"fact check usage: {resp.get('usage', {}).get('input_tokens')} in, {resp.get('usage', {}).get('output_tokens')} out")
    return out


def verdict_lines(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*(?:answer\s*)?(\d+)\s*[:.)-]?\s*(OK|FAIL)\b[\s:.-]*(.*)", line, re.I)
        if m:
            out[int(m.group(1))] = (m.group(2).upper() == "OK", m.group(3).strip())
    return out


def page_html(log, now):
    dates = sorted({e["date"] for e in log if e.get("answer")}, reverse=True)[:2]
    parts = []
    for d in dates:
        items = [e for e in log if e["date"] == d and e.get("answer")]
        parts.append(f"<h2>Answers from {datetime.date.fromisoformat(d):%d %b}</h2>")
        for i, e in enumerate(items, 1):
            parts.append(
                f'<section class="card" data-url="{html.escape(e["url"])}">'
                f'<p class="q">{i}. {html.escape(e["question"])}</p>'
                f'<div class="btns"><button class="copy" type="button">Copy answer</button>'
                f'<a class="open" href="{html.escape(e["url"])}" target="_blank" rel="noopener">Open on Quora</a></div>'
                f'<p class="ans">{html.escape(e["answer"])}</p>'
                f'<label><input type="checkbox"> Posted</label></section>')
    return PAGE.replace("{{BODY}}", "\n".join(parts)).replace("{{UPDATED}}", f"{now:%d %b %Y, %H:%M} IST")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Quora answers</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--muted:#5d6470;--line:#dde1e7;--blue:#1f5fd6}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b21;--ink:#e8eaed;--muted:#9aa3ad;--line:#2a2f37;--blue:#5b9bff}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:4px 0 6px}
.how{color:var(--muted);margin:0 0 8px}
h2{font-size:14px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin:22px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:0 0 14px}
.card.done{opacity:.5}
.q{font-weight:600;margin:0 0 10px}
.btns{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 10px}
.btns>*{flex:1 1 140px;box-sizing:border-box;text-align:center;padding:12px;border-radius:10px;font:600 15px system-ui,sans-serif;cursor:pointer;text-decoration:none}
.copy{background:var(--blue);color:#fff;border:2px solid var(--blue)}
.open{color:var(--blue);border:2px solid var(--blue)}
.ans{white-space:pre-wrap;border-top:1px solid var(--line);padding-top:10px;margin:0}
label{display:flex;gap:10px;align-items:center;margin-top:12px;font-weight:600}
label input{width:22px;height:22px}
footer{color:var(--muted);font-size:13px;margin:24px 0 8px}
</style></head><body><main>
<h1>Quora answers</h1>
<p class="how">For each answer: 1. Copy answer. 2. Open on Quora. 3. Tap Answer, paste, Post. 4. Tick Posted.</p>
<p class="how">Post 1 or 2 a day, not all at once. New answers come every Monday, on top.</p>
{{BODY}}
<footer>Updated {{UPDATED}}</footer>
</main>
<script>
document.querySelectorAll('.card').forEach(function(c){
  var k='qa:'+c.dataset.url,box=c.querySelector('input'),btn=c.querySelector('.copy');
  try{if(localStorage.getItem(k)){box.checked=true;c.classList.add('done');}}catch(e){}
  box.addEventListener('change',function(){c.classList.toggle('done',box.checked);try{box.checked?localStorage.setItem(k,'1'):localStorage.removeItem(k);}catch(e){}});
  btn.addEventListener('click',function(){
    var t=c.querySelector('.ans').textContent;
    function ok(){btn.textContent='Copied \u2713';setTimeout(function(){btn.textContent='Copy answer';},2000);}
    function old(){var a=document.createElement('textarea');a.value=t;a.setAttribute('readonly','');a.style.position='fixed';a.style.opacity='0';document.body.appendChild(a);a.select();try{document.execCommand('copy');ok();}catch(e){}a.remove();}
    if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(ok,old);}else{old();}
  });
});
</script>
</body></html>
"""


def missing_batch(log, today):
    last = max((e["date"] for e in log), default="")
    monday = today - datetime.timedelta(days=today.weekday())
    return None if last >= monday.isoformat() else (last or "never")


def telegram(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_ALERT_CHAT_ID")
    if not tok or not chat:
        sys.exit("Telegram secrets missing")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{tok}/sendMessage",
        data=json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        if not json.load(r).get("ok"):
            sys.exit("Telegram refused a message")


def send_batch(header, items):
    n = len(items)
    telegram(f"{header} ({n}). Post 1 or 2 a day, not all at once: open the link, tap Answer, "
             f"paste the message under it, Post.")
    for i, k in enumerate(items, 1):
        telegram(f"{i}/{n} {k['question']}\n{k['url']}")
        telegram(k["answer"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["dry-run", "send", "page", "tell", "resend", "check"], default="dry-run")
    ap.add_argument("--date", default="", help="resend: the date of the saved batch, e.g. 2026-10-18")
    ap.add_argument("--out", default="", help="page: where to write the HTML")
    args = ap.parse_args()
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    today = now.date()
    log = load_log()
    if args.mode == "check":
        gap = missing_batch(log, today)
        if gap:
            sys.exit(f"No Quora batch this week; the last one was {gap}")
        print("this week's batch is in the log")
        return
    if args.mode == "resend":
        day = datetime.date.fromisoformat(args.date)
        items = [e for e in log if e["date"] == args.date and e.get("answer")]
        if not items:
            sys.exit(f"No saved answers for {args.date}")
        send_batch(f"Quora answers written {day:%d %b}", items)
        print(f"sent {len(items)} saved answers")
        return
    if args.mode == "page":
        with open(args.out, "w") as f:
            f.write(page_html(log, now))
        print(f"page written: {args.out}")
        return
    if args.mode == "tell":
        url = os.environ.get("QA_PAGE_URL") or sys.exit("QA_PAGE_URL missing")
        last = max(e["date"] for e in log if e.get("answer"))
        items = [e for e in log if e["date"] == last and e.get("answer")]
        lines = "\n".join(f"{i}. {e['question']}" for i, e in enumerate(items, 1))
        telegram(f"Quora: {len(items)} new answers are on the posting page, on top:\n{url}\n\n{lines}\n\n"
                 "The link never changes; new answers appear every Monday. Post 1 or 2 a day.")
        print("told Ketu")
        return
    seen = {e["url"] for e in log}
    cands, found = draft(seen, today)
    kept = fact_check(pick(cands, found, seen, limit=CANDIDATES))[:WANT]
    for i, k in enumerate(kept, 1):
        print(f"\n=== {i}. {k['question']}\n{k['url']}\n\n{k['answer']}")
    if len(kept) < 2:
        sys.exit(f"Only {len(kept)} answers passed the rules and the fact check")
    if args.mode != "send":
        print("\ndry run: nothing saved")
        return
    log += [{"date": today.isoformat(), "url": k["url"], "question": k["question"], "answer": k["answer"]} for k in kept]
    with open(LOG, "w") as f:
        json.dump(log, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"\nsaved {len(kept)} answers")


if __name__ == "__main__":
    main()
