#!/usr/bin/env python3
"""
Instagram Business Account Setup Script for Sale91 YT Shorts Bot.
Run this on your LOCAL computer (NOT on GitHub Actions).

This script will:
1. Open Instagram login in your browser
2. Get an access token via Instagram Business Login API
3. Exchange for a long-lived token (60 days)
4. Get your Instagram Business Account ID
5. Print values for GitHub Secrets

Prerequisites:
- A Facebook App with "Instagram Business Login" product added
- Your Instagram account must be Business or Creator type
- Add https://localhost:8888/callback as Valid OAuth Redirect URI
  in App Dashboard -> Instagram Business Login -> Settings

Usage:
    python3 setup_instagram.py
"""

import json
import os
import ssl
import subprocess
import sys
import tempfile
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode

import requests

# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════

FB_APP_ID = os.environ.get("FB_APP_ID", "")
FB_APP_SECRET = os.environ.get("FB_APP_SECRET", "")
REDIRECT_URI = "https://localhost:8888/callback"

# Instagram Business Login scopes
SCOPES = [
    "instagram_business_basic",
    "instagram_business_content_publish",
]


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth redirect callback."""

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
        pass


def main():
    global FB_APP_ID, FB_APP_SECRET

    print("=" * 60)
    print("  Instagram Setup for YT Shorts Bot (Sale91)")
    print("  Uses Instagram Business Login API")
    print("=" * 60)
    print()

    # ── Step 0: Get App credentials ───────────────────────────────
    if not FB_APP_ID:
        print("Go to: https://developers.facebook.com/apps/")
        print("  -> Select your app -> App settings -> Basic")
        print()
        FB_APP_ID = input("Enter your Facebook App ID: ").strip()

    if not FB_APP_SECRET:
        FB_APP_SECRET = input("Enter your Facebook App Secret: ").strip()
    print()

    if not FB_APP_ID or not FB_APP_SECRET:
        print("App ID and App Secret are required!")
        sys.exit(1)

    # ── Step 1: OAuth via Instagram (NOT Facebook!) ───────────────
    print("1. Opening Instagram login in your browser...")

    # Instagram Business Login uses instagram.com OAuth, not facebook.com
    auth_url = (
        "https://www.instagram.com/oauth/authorize?"
        + urlencode({
            "client_id": FB_APP_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": ",".join(SCOPES),
            "response_type": "code",
            "enable_fb_login": "0",
            "force_authentication": "1",
        })
    )

    # Start HTTPS local server
    server = HTTPServer(("localhost", 8888), OAuthCallbackHandler)
    server.timeout = 120

    cert_dir = tempfile.mkdtemp()
    cert_file = os.path.join(cert_dir, "cert.pem")
    key_file = os.path.join(cert_dir, "key.pem")
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key_file, "-out", cert_file,
            "-days", "1", "-nodes",
            "-subj", "/CN=localhost",
        ],
        capture_output=True,
    )
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(cert_file, key_file)
    server.socket = ssl_ctx.wrap_socket(server.socket, server_side=True)

    print("   Waiting for login (timeout: 2 minutes)...")
    print("   NOTE: Browser may show 'unsafe' warning — click 'Advanced' -> 'Proceed' (it's localhost)")
    print()
    webbrowser.open(auth_url)

    while OAuthCallbackHandler.auth_code is None:
        server.handle_request()

    auth_code = OAuthCallbackHandler.auth_code
    server.server_close()
    print("   Login successful!")

    # The code may have #_ appended by Instagram
    if auth_code.endswith("#_"):
        auth_code = auth_code[:-2]

    # ── Step 2: Exchange code for short-lived token ───────────────
    print("\n2. Exchanging auth code for access token...")

    # Instagram uses a POST to api.instagram.com (not graph.facebook.com)
    resp = requests.post(
        "https://api.instagram.com/oauth/access_token",
        data={
            "client_id": FB_APP_ID,
            "client_secret": FB_APP_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code": auth_code,
        },
        timeout=30,
    )
    token_data = resp.json()

    if "access_token" not in token_data:
        print(f"   Failed! Response: {json.dumps(token_data, indent=2)}")
        sys.exit(1)

    short_token = token_data["access_token"]
    ig_user_id = str(token_data.get("user_id", ""))
    print(f"   Got short-lived token. User ID: {ig_user_id}")

    # ── Step 3: Exchange for long-lived token (60 days) ───────────
    print("\n3. Exchanging for long-lived token (60 days)...")

    resp = requests.get(
        "https://graph.instagram.com/access_token",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": FB_APP_SECRET,
            "access_token": short_token,
        },
        timeout=30,
    )
    long_data = resp.json()

    if "access_token" not in long_data:
        print(f"   Failed! Response: {json.dumps(long_data, indent=2)}")
        print("   Using short-lived token instead (1 hour validity).")
        long_token = short_token
        token_type = "Short-lived (1 hour)"
    else:
        long_token = long_data["access_token"]
        expires = long_data.get("expires_in", 0)
        days = expires // 86400 if expires else "?"
        token_type = f"Long-lived ({days} days)"
        print(f"   Got long-lived token ({days} days).")

    # ── Step 4: Get Instagram account info ────────────────────────
    print("\n4. Getting Instagram account info...")

    resp = requests.get(
        f"https://graph.instagram.com/v21.0/me",
        params={
            "fields": "user_id,username,name,account_type,profile_picture_url,followers_count,media_count",
            "access_token": long_token,
        },
        timeout=30,
    )
    me_data = resp.json()

    if "error" in me_data:
        print(f"   API Error: {me_data['error'].get('message', 'Unknown')}")
        # Still try to use the user_id from token exchange
        ig_username = "unknown"
    else:
        ig_user_id = str(me_data.get("user_id", me_data.get("id", ig_user_id)))
        ig_username = me_data.get("username", "unknown")
        print(f"   Account: @{ig_username}")
        print(f"   Type: {me_data.get('account_type', '?')}")
        if me_data.get("followers_count"):
            print(f"   Followers: {me_data['followers_count']}")
        if me_data.get("media_count"):
            print(f"   Posts: {me_data['media_count']}")

    # ── Step 5: Output results ────────────────────────────────────
    print()
    print("=" * 60)
    print(f"  SETUP COMPLETE!")
    print("=" * 60)
    print()
    print(f"  Instagram: @{ig_username}")
    print(f"  User/Business ID: {ig_user_id}")
    print(f"  Token type: {token_type}")
    print(f"  Token length: {len(long_token)} chars")
    print()
    print("-" * 60)
    print("  ADD THESE TO YOUR GITHUB SECRETS:")
    print("-" * 60)
    print()
    print(f"  INSTAGRAM_BUSINESS_ID = {ig_user_id}")
    print()

    # Save token to file
    token_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instagram_token.txt")
    with open(token_file, "w") as f:
        f.write(long_token)

    print(f"  INSTAGRAM_ACCESS_TOKEN = (saved to instagram_token.txt)")
    print(f"  Copy FULL content of instagram_token.txt into GitHub Secret.")
    print()
    print("-" * 60)
    print("  NOTES:")
    print("-" * 60)
    print("  - Token is valid for 60 days")
    print("  - Run this script again before it expires to refresh")
    print("  - DELETE instagram_token.txt after copying to GitHub!")
    print("  - NEVER commit the token file to git!")
    print()


if __name__ == "__main__":
    main()
