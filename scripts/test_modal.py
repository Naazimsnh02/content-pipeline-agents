
import os
import requests
import json
import base64
from dotenv import load_dotenv

load_dotenv()

def test_flux_modal():
    url = os.environ.get("MODAL_FLUX2_ENDPOINT_URL")
    token_id = os.environ.get("MODAL_TOKEN_ID")
    token_secret = os.environ.get("MODAL_TOKEN_SECRET")

    print(f"Testing Modal Flux2 Endpoint: {url}")
    
    if not url:
        print("Error: MODAL_FLUX2_ENDPOINT_URL not set in .env")
        return

    headers = {"Content-Type": "application/json"}
    if token_id and token_secret:
        headers["Authorization"] = f"Bearer {token_id}:{token_secret}"

    # Try payload without wrapping (what I implemented in tools.py)
    payload = {
        "operation": "generate",
        "prompt": "Cyberpunk city, neon lights, vertical 9:16 portrait format",
        "width": 1080,
        "height": 1920,
    }

    print("Attempt 1: Sending flat payload...")
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=60)
        print(f"Response Code: {r.status_code}")
        print(f"Response Text: {r.text[:500]}")
    except Exception as e:
        print(f"Request failed: {e}")

    # Try payload with "input" wrapping (just in case)
    print("\nAttempt 2: Sending wrapped {'input': ...} payload...")
    payload_wrapped = {"input": payload}
    try:
        r = requests.post(url, json=payload_wrapped, headers=headers, timeout=60)
        print(f"Response Code: {r.status_code}")
        print(f"Response Text: {r.text[:500]}")
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    test_flux_modal()
