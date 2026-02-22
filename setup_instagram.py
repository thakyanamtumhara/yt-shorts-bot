#!/usr/bin/env python3
"""
Instagram Business Account Setup Script for Sale91 YT Shorts Bot.
Run this on your LOCAL computer (NOT on GitHub Actions).

This script will:
1. Open Facebook login in your browser
2. Get a short-lived User token
3. Find your Facebook Pages
4. Get the Instagram Business Account connected to your Page
5. Exchange for a PERMANENT Page token (never expires!)
6. Print the values you need for GitHub Secrets

Prerequisites:
- A Facebook App (create at https://developers.facebook.com/apps/)
- Your Facebook Page must be connected to an Instagram Business/Creator account
- App must have: pages_show_list, instagram_basic, instagram_content_publish permissions

Usage:
    python setup_instagram.py

Or with env vars:
    FB_APP_ID=123 FB_APP_SECRET=abc python setup_instagram.py
"""

import json
import os
import sys
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode

import requests

# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════
# Get these from: https://developers.facebook.com/apps/ → Your App → Settings → Basic

FB_APP_ID = os.environ.get("FB_APP_ID", "")
FB_APP_SECRET = os.environ.get("FB_APP_SECRET", "")
REDIRECT_URI = "http://localhost:8888/callback"
GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Permissions — tries Instagram Business Login scopes first,
# falls back to legacy Instagram Graph API scopes.
# Your app needs at least one of these product sets configured.
SCOPES_BUSINESS_LOGIN = [
    "instagram_business_basic",
    "instagram_business_content_publish",
    "instagram_business_manage_messages",
]

SCOPES_LEGACY = [
    "pages_show_list",
    "pages_read_engagement",
    "instagram_basic",
    "instagram_content_publish",
    "instagram_manage_insights",
    "business_management",
]


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth redirect callback from Facebook."""

    auth_code = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "code" in params:
            OAuthCallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                b"<h1>&#10004; Login Successful!</h1>"
                b"<p>You can close this tab and go back to the terminal.</p>"
                b"</body></html>"
            )
        elif "error" in params:
            error = params.get("error_description", params.get("error", ["Unknown"]))[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                f"<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                f"<h1>&#10060; Login Failed</h1><p>{error}</p>"
                f"</body></html>".encode()
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs


def api_get(endpoint, params=None):
    """Make a GET request to the Facebook Graph API."""
    url = f"{GRAPH_BASE}/{endpoint}" if not endpoint.startswith("http") else endpoint
    resp = requests.get(url, params=params, timeout=30)
    data = resp.json()
    if "error" in data:
        err = data["error"]
        print(f"\n   API Error ({err.get('code', '?')}): {err.get('message', 'Unknown error')}")
        return None
    return data


def exchange_code_for_token(code):
    """Exchange the OAuth authorization code for a short-lived user token."""
    print("\n2. Exchanging auth code for user token...")
    data = api_get("oauth/access_token", {
        "client_id": FB_APP_ID,
        "client_secret": FB_APP_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    })
    if not data or "access_token" not in data:
        print("   Failed to get user token!")
        return None
    print("   Got short-lived user token.")
    return data["access_token"]


def get_long_lived_user_token(short_token):
    """Exchange short-lived token for a long-lived user token (60 days)."""
    print("\n3. Exchanging for long-lived user token...")
    data = api_get("oauth/access_token", {
        "grant_type": "fb_exchange_token",
        "client_id": FB_APP_ID,
        "client_secret": FB_APP_SECRET,
        "fb_exchange_token": short_token,
    })
    if not data or "access_token" not in data:
        print("   Failed to get long-lived token!")
        return None
    expires = data.get("expires_in", 0)
    days = expires // 86400 if expires else "?"
    print(f"   Got long-lived user token (expires in {days} days).")
    return data["access_token"]


def get_pages(user_token):
    """Get all Facebook Pages the user manages."""
    print("\n4. Finding your Facebook Pages...")
    data = api_get("me/accounts", {
        "fields": "id,name,access_token,instagram_business_account{id,name,username}",
        "access_token": user_token,
    })
    if not data or "data" not in data:
        print("   No pages found! Make sure your Facebook App has 'pages_show_list' permission.")
        return []
    return data["data"]


def do_oauth_login(scopes, label):
    """Run OAuth flow with given scopes. Returns auth code or None."""
    OAuthCallbackHandler.auth_code = None

    auth_url = (
        f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth?"
        + urlencode({
            "client_id": FB_APP_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": ",".join(scopes),
            "response_type": "code",
        })
    )

    server = HTTPServer(("localhost", 8888), OAuthCallbackHandler)
    server.timeout = 120

    print(f"   Opening Facebook login ({label})...")
    print(f"   Waiting for login (timeout: 2 minutes)...")
    print()
    webbrowser.open(auth_url)

    while OAuthCallbackHandler.auth_code is None:
        server.handle_request()

    code = OAuthCallbackHandler.auth_code
    server.server_close()
    return code


def try_business_login_flow(long_user_token):
    """Instagram Business Login flow — direct IG token, no Page needed."""
    print("\n4. Getting Instagram account (Business Login)...")

    # With Instagram Business Login, /me/accounts gives IG accounts directly
    data = api_get("me", {
        "fields": "id,name,instagram_business_account{id,name,username}",
        "access_token": long_user_token,
    })

    # Also try to get IG user info directly
    ig_data = api_get("me/accounts", {
        "fields": "id,name,username,instagram_business_account{id,name,username},access_token",
        "access_token": long_user_token,
    })

    # Check if we got pages with IG accounts
    if ig_data and ig_data.get("data"):
        return ig_data["data"], "page"

    # For Instagram Business Login, try getting IG user directly
    ig_user = api_get("me/instagram_accounts", {
        "fields": "id,name,username",
        "access_token": long_user_token,
    })

    if ig_user and ig_user.get("data"):
        return ig_user["data"], "direct"

    return None, None


def main():
    global FB_APP_ID, FB_APP_SECRET

    print("=" * 60)
    print("  Instagram Setup for YT Shorts Bot (Sale91)")
    print("=" * 60)
    print()

    # ── Step 0: Get App credentials ───────────────────────────────
    if not FB_APP_ID:
        print("Go to: https://developers.facebook.com/apps/")
        print("  -> Select your app -> Settings -> Basic")
        print()
        FB_APP_ID = input("Enter your Facebook App ID: ").strip()

    if not FB_APP_SECRET:
        FB_APP_SECRET = input("Enter your Facebook App Secret: ").strip()
    print()

    if not FB_APP_ID or not FB_APP_SECRET:
        print("App ID and App Secret are required!")
        sys.exit(1)

    # ── Step 1: Choose API mode ───────────────────────────────────
    print("Which product does your Facebook App have?")
    print("  [1] Instagram Business Login (newer — recommended)")
    print("  [2] Facebook Login + Instagram Graph API (legacy)")
    print()
    mode = input("Enter 1 or 2 [default: 1]: ").strip() or "1"

    if mode == "2":
        scopes = SCOPES_LEGACY
        label = "Facebook Login + Instagram Graph API"
    else:
        scopes = SCOPES_BUSINESS_LOGIN
        label = "Instagram Business Login"

    # ── Step 2: OAuth Login ───────────────────────────────────────
    print(f"\n1. Starting OAuth login...")
    auth_code = do_oauth_login(scopes, label)
    if not auth_code:
        print("   Login failed!")
        sys.exit(1)
    print("   Login successful!")

    # ── Step 3: Exchange code for tokens ──────────────────────────
    short_token = exchange_code_for_token(auth_code)
    if not short_token:
        sys.exit(1)

    long_user_token = get_long_lived_user_token(short_token)
    if not long_user_token:
        sys.exit(1)

    # ── Step 4: Find Instagram account ────────────────────────────
    if mode == "2":
        # Legacy flow: get Pages with connected Instagram
        pages = get_pages(long_user_token)
        if not pages:
            print("\nNo Facebook Pages found!")
            print("Make sure:")
            print("  1. You have a Facebook Page")
            print("  2. Your Instagram Business account is connected to it")
            print("  3. Your app has 'pages_show_list' permission approved")
            sys.exit(1)
    else:
        # Business Login flow
        pages, flow_type = try_business_login_flow(long_user_token)
        if not pages:
            # Fallback: try legacy page lookup anyway
            print("   Trying page-based lookup as fallback...")
            pages = get_pages(long_user_token)
            if not pages:
                print("\nCould not find Instagram account!")
                print("Make sure your Instagram is a Business or Creator account")
                print("and is connected to your Facebook Page.")
                sys.exit(1)

    # ── Step 5: Select the right Page/Account ─────────────────────
    print(f"\n   Found {len(pages)} account(s):\n")

    pages_with_ig = []
    for i, page in enumerate(pages):
        ig = page.get("instagram_business_account", {})
        ig_id = ig.get("id", "")
        ig_name = ig.get("username", ig.get("name", ""))

        # For direct IG accounts (Business Login), the page itself IS the IG account
        if not ig_id and page.get("username"):
            ig = page
            ig_id = page.get("id", "")
            ig_name = page.get("username", page.get("name", ""))

        has_ig = bool(ig_id)
        pages_with_ig.append((page, ig, has_ig))

        status = f"@{ig_name}" if ig_name else (ig_id or "NOT CONNECTED")
        marker = "" if has_ig else " (no Instagram connected)"
        print(f"   [{i + 1}] {page.get('name', page.get('username', 'Unknown'))} -> Instagram: {status}{marker}")

    # Filter accounts with Instagram
    ig_pages = [(p, ig) for p, ig, has_ig in pages_with_ig if has_ig]
    if not ig_pages:
        print("\n   No Instagram Business account found!")
        print("\n   Make sure:")
        print("   1. Your Instagram is a Business or Creator account")
        print("   2. It's connected to your Facebook Page")
        print("   3. Run this script again")
        sys.exit(1)

    if len(ig_pages) == 1:
        selected_page, selected_ig = ig_pages[0]
        print(f"\n   Auto-selected: {selected_page.get('name', selected_page.get('username', ''))}")
    else:
        while True:
            choice = input(f"\n   Select number [1-{len(pages)}]: ").strip()
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(pages):
                    page, ig, has_ig = pages_with_ig[idx]
                    if not has_ig:
                        print("   That has no Instagram connected. Pick another.")
                        continue
                    selected_page, selected_ig = page, ig
                    break
            except ValueError:
                pass
            print("   Invalid choice, try again.")

    # ── Step 6: Get the access token ──────────────────────────────
    # Page tokens from long-lived user tokens are PERMANENT (never expire)
    # Direct IG tokens (Business Login) last 60 days but can be refreshed
    page_access_token = selected_page.get("access_token", long_user_token)
    ig_business_id = selected_ig.get("id", selected_ig.get("user_id", ""))
    ig_username = selected_ig.get("username", selected_ig.get("name", ""))

    is_page_token = "access_token" in selected_page and mode == "2"
    token_type = "PERMANENT (Page token)" if is_page_token else "Long-lived (60 days)"

    print(f"\n5. Verifying Instagram Business Account...")
    verify = api_get(ig_business_id, {
        "fields": "id,name,username,profile_picture_url,followers_count,media_count",
        "access_token": page_access_token,
    })
    if verify:
        print(f"   Account: @{verify.get('username', verify.get('name', ig_business_id))}")
        print(f"   Followers: {verify.get('followers_count', '?')}")
        print(f"   Posts: {verify.get('media_count', '?')}")
        if not ig_username:
            ig_username = verify.get("username", verify.get("name", ""))
        if not ig_business_id:
            ig_business_id = verify.get("id", "")
    else:
        print("   Warning: Could not verify, but token was obtained.")

    # ── Step 7: Output results ────────────────────────────────────
    print()
    print("=" * 60)
    print(f"  SETUP COMPLETE! Token type: {token_type}")
    print("=" * 60)
    print()
    print(f"  Instagram: @{ig_username}")
    print(f"  Business ID: {ig_business_id}")
    print(f"  Token length: {len(page_access_token)} chars")
    print(f"  Token type: {token_type}")
    print()
    print("-" * 60)
    print("  ADD THESE TO YOUR GITHUB SECRETS:")
    print("-" * 60)
    print()
    print(f"  INSTAGRAM_BUSINESS_ID = {ig_business_id}")
    print()

    # Save token to file (too long to copy from terminal)
    token_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instagram_token.txt")
    with open(token_file, "w") as f:
        f.write(page_access_token)

    print(f"  INSTAGRAM_ACCESS_TOKEN = (saved to instagram_token.txt)")
    print(f"  Copy FULL content of instagram_token.txt into GitHub Secret.")
    print()
    print("-" * 60)
    print("  NOTES:")
    print("-" * 60)
    if is_page_token:
        print("  - This Page token NEVER expires (permanent!)")
        print("  - No need to refresh every 60 days")
        print("  - Only expires if you change FB password or remove the app")
    else:
        print("  - This token is valid for 60 days")
        print("  - Run this script again before it expires to get a new one")
        print("  - Or switch to Facebook Login + Instagram Graph API (option 2)")
        print("    for a permanent token")
    print("  - DELETE instagram_token.txt after copying to GitHub!")
    print("  - NEVER commit the token file to git!")
    print()


if __name__ == "__main__":
    main()
