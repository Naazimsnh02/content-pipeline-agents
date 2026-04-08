import os
from google_auth_oauthlib.flow import InstalledAppFlow

# Get the directory where the script is located
current_dir = os.path.dirname(os.path.abspath(__file__))
client_secrets_path = os.path.join(current_dir, "client_secret.json")

# Initialize the OAuth flow
flow = InstalledAppFlow.from_client_secrets_file(
    client_secrets_path,  # Path to your downloaded client secret file
    scopes=["https://www.googleapis.com/auth/youtube.upload"]
)

# Run local server to authenticate
creds = flow.run_local_server(port=0)

# Print the refresh token
print("REFRESH_TOKEN:", creds.refresh_token)