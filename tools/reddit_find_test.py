#!/usr/bin/env python3
"""Can a licensed search find Reddit threads for us? (18-Oct-2026)

Anthropic's web search returns no reddit.com results, so the Quora job cannot look there. OpenAI has a Reddit data
partnership, so this asks OpenAI's web search, limited to reddit.com, for threads where Indian buyers ask about plain
T-shirt suppliers. It only prints what it finds; nothing is posted and no Reddit page is fetched by us.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

KEY = os.environ.get("OPENAI_API_KEY") or sys.exit("OPENAI_API_KEY missing")
PREFER = ["gpt-5.2", "gpt-5.1", "gpt-5", "gpt-5-mini", "gpt-4.1"]


def call(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"error": f"{e.code} {e.read()[:300]!r}"}


ids = {m["id"] for m in call("https://api.openai.com/v1/models").get("data", [])}
model = next((m for m in PREFER if m in ids), None) or sys.exit(f"none of {PREFER} available")
print("model:", model)
ask = ("Find Reddit threads from the last 12 months where people in India ask where to buy plain or blank T-shirts, "
       "hoodies or oversized tees in bulk or wholesale, or which supplier or manufacturer to use for a clothing brand or "
       "a T-shirt printing business. List up to 10, each as: subreddit | thread title | URL | date.")
resp = call("https://api.openai.com/v1/responses", {
    "model": model, "input": ask,
    "tools": [{"type": "web_search", "filters": {"allowed_domains": ["reddit.com"]}}],
    "include": ["web_search_call.action.sources"],
})
if resp.get("error"):
    sys.exit(f"responses API: {resp['error']}")
urls, text = set(), ""
for item in resp.get("output", []):
    if item.get("type") == "web_search_call":
        for src in (item.get("action") or {}).get("sources") or []:
            urls.add(src.get("url", ""))
    if item.get("type") == "message":
        for c in item.get("content", []):
            text += c.get("text", "")
            for a in c.get("annotations", []):
                urls.add(a.get("url", ""))
threads = sorted(u for u in urls if re.search(r"reddit\.com/r/[^/]+/comments/", u or ""))
print("usage:", resp.get("usage"))
print(f"\nreddit thread URLs found: {len(threads)}")
for u in threads:
    print(" ", u)
print("\n--- model text ---\n" + text[:3000])
