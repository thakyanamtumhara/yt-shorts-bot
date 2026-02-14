#!/usr/bin/env python3
"""
Simple script to generate a new YouTube OAuth token.
Run this on your local computer (NOT on GitHub).
"""
import json, os

print("=" * 50)
print("  YouTube Token Generator")
print("=" * 50)
print()

# Step 1: Check for client_secret.json
secret_file = "client_secret.json"
if not os.path.exists(secret_file):
    print("ERROR: client_secret.json not found!")
    print()
    print("Make sure client_secret.json is in the same folder as this script.")
    print("You can download it from: https://console.cloud.google.com")
    print("  -> Your Project -> APIs & Services -> Credentials -> OAuth 2.0 Client")
    exit(1)

print("Found client_secret.json")
print()

# Step 2: Run OAuth flow
try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("Installing required package...")
    os.system("pip install google-auth-oauthlib")
    from google_auth_oauthlib.flow import InstalledAppFlow

print("A browser window will open. Sign in with your YouTube channel's Google account.")
print("Click 'Allow' when asked for permissions.")
print()
input("Press Enter to continue...")

flow = InstalledAppFlow.from_client_secrets_file(
    secret_file,
    scopes=["https://www.googleapis.com/auth/youtube.upload"]
)
creds = flow.run_local_server(port=8080)

# Step 3: Output the token
token_json = creds.to_json()
token_pretty = json.dumps(json.loads(token_json), indent=2)

# Save to file
with open("new_youtube_token.json", "w") as f:
    f.write(token_json)

print()
print("=" * 50)
print("  TOKEN GENERATED SUCCESSFULLY!")
print("=" * 50)
print()
print("Token saved to: new_youtube_token.json")
print()
print("NOW COPY EVERYTHING BELOW THIS LINE:")
print("-" * 50)
print(token_json)
print("-" * 50)
print()
print("Paste this into GitHub Secret: YOUTUBE_TOKEN_JSON")
print("(See instructions in the terminal above)")
