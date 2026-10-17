#!/usr/bin/env python3
"""Weekly Quora answers that Ketu posts by hand (his go, 18-Oct-2026: "Whatever is the safest, you can keep doing it").

Quora's terms (3.2) give no right to post answers with automated tools, and 4.4 bars AI access for commercial use,
so nothing here posts and no Quora page is ever fetched: Claude Opus sees only web-search results, picks real
question URLs from them, and writes one disclosed, price-free answer per question from FACTS. The batch goes to
Ketu's Telegram as "question + link", then the answer alone, so one long-press copies it. quora_answers/log.json
keeps every question ever sent so none repeats.
"""
import argparse
import datetime
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
- Godown (warehouse) in Khanpur, South Delhi. Buyers can visit Mon-Sat 10 AM-6 PM, Sun 12-4 PM.
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


def claude(messages, system):
    key = os.environ["ANTHROPIC_API_KEY"]
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({
            "model": MODEL, "max_tokens": 8000, "system": system, "messages": messages,
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8,
                       "allowed_domains": ["quora.com"]}],
        }).encode(),
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
        f"Return {CANDIDATES} candidates as JSON only, in this shape: "
        '{"answers":[{"question":"exact question title","url":"https://www.quora.com/...","answer":"..."}]}. '
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
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        sys.exit(f"No JSON in the reply: {text[:400]!r}")
    print(f"web searches: {searches}, Quora question URLs seen: {len(found)}, usage: {resp.get('usage')}")
    return json.loads(m.group(0)).get("answers", []), found


def pick(cands, found, seen):
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
        if len(kept) == WANT:
            break
    return kept


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["dry-run", "send"], default="dry-run")
    args = ap.parse_args()
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date()
    log = load_log()
    seen = {e["url"] for e in log}
    cands, found = draft(seen, today)
    kept = pick(cands, found, seen)
    for i, k in enumerate(kept, 1):
        print(f"\n=== {i}. {k['question']}\n{k['url']}\n\n{k['answer']}")
    if len(kept) < 2:
        sys.exit(f"Only {len(kept)} answers passed the rules")
    if args.mode != "send":
        print("\ndry run: nothing sent, log unchanged")
        return
    n = len(kept)
    telegram(f"Quora answers for this week ({n}). Post 1 or 2 a day, not all at once: open the link, tap Answer, "
             f"paste the message under it, Post.")
    for i, k in enumerate(kept, 1):
        telegram(f"{i}/{n} {k['question']}\n{k['url']}")
        telegram(k["answer"])
    log += [{"date": today.isoformat(), "url": k["url"], "question": k["question"]} for k in kept]
    with open(LOG, "w") as f:
        json.dump(log, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"\nsent {n} answers")


if __name__ == "__main__":
    main()
