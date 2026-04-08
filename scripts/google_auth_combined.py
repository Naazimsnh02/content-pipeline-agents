import os
from google_auth_oauthlib.flow import InstalledAppFlow

# Get the directory where the script is located
current_dir = os.path.dirname(os.path.abspath(__file__))
client_secrets_path = os.path.join(current_dir, "client_secret.json")

# Initialize the OAuth flow with BOTH scopes
# 1. YouTube Upload
# 2. To manage Calendar Events
scopes = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/calendar.events"
]

print("Starting combined authentication for YouTube and Google Calendar...")
print(f"Using client secrets from: {client_secrets_path}")

try:
    flow = InstalledAppFlow.from_client_secrets_file(
        client_secrets_path,
        scopes=scopes
    )

    # Run local server to authenticate
    # port=0 means it will find an available port automatically
    creds = flow.run_local_server(port=0)

    print("\n" + "="*60)
    print("SUCCESS! AUTHENTICATION COMPLETE")
    print("="*60)
    print("\nCopy the following REFRESH_TOKEN into your .env file:")
    print(f"\nREFRESH_TOKEN: {creds.refresh_token}")
    print("\nUpdate your .env with:")
    print(f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")
    print(f"CALENDAR_REFRESH_TOKEN={creds.refresh_token}")
    print("="*60)

except FileNotFoundError:
    print(f"\nError: Could not find 'client_secret.json' in 'scripts/' directory.")
    print(f"Expected path: {client_secrets_path}")
    print("Please make sure you have downloaded your OAuth client secret from Google Cloud Console.")
except Exception as e:
    print(f"\nAn error occurred: {e}")
