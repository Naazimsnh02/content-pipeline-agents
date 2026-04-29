"""Test if YouTube and Calendar OAuth refresh tokens are still valid."""
import sys
sys.path.insert(0, ".")

import httpx
from shared.config import settings

TOKEN_URL = "https://oauth2.googleapis.com/token"


def test_token(name, client_id, client_secret, refresh_token):
    print(f"\n--- {name} ---")
    if not client_id or not client_secret or not refresh_token:
        print(f"  SKIPPED: credentials not configured")
        return False

    resp = httpx.post(TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }, timeout=10)

    data = resp.json()
    if resp.status_code == 200:
        expires = data.get("expires_in", "?")
        print(f"  OK: access token obtained (expires in {expires}s)")
        return True
    else:
        err = data.get("error", "unknown")
        desc = data.get("error_description", "no description")
        print(f"  FAILED: {err} — {desc}")
        if err == "invalid_grant":
            print(f"  FIX: The refresh token is expired or revoked.")
            print(f"       Re-run the OAuth flow to get a new one.")
            print(f"       Or connect via the app UI: /auth/{name.lower()}")
        return False


print("=== OAuth Token Validation ===")

yt_ok = test_token(
    "YouTube",
    settings.youtube_client_id,
    settings.youtube_client_secret,
    settings.youtube_refresh_token,
)

cal_ok = test_token(
    "Calendar",
    settings.calendar_client_id or settings.youtube_client_id,
    settings.calendar_client_secret or settings.youtube_client_secret,
    settings.calendar_refresh_token,
)

print("\n=== Summary ===")
print(f"  YouTube:  {'OK' if yt_ok else 'NEEDS RE-AUTH'}")
print(f"  Calendar: {'OK' if cal_ok else 'NEEDS RE-AUTH'}")

if not yt_ok or not cal_ok:
    print("\nTo fix invalid_grant errors:")
    print("  1. Start the app: .venv\\Scripts\\python.exe .\\app.py")
    print("  2. Open http://localhost:8080/app")
    print("  3. Sign in, then go to Settings")
    print("  4. Click 'Connect YouTube' and/or 'Connect Calendar'")
    print("  5. Complete the OAuth flow in the popup")
    print("  6. The new tokens are stored per-user in Firestore")
    print("     (no need to update .env)")
