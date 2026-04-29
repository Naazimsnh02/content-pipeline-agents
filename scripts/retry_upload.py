"""
Retry the YouTube upload for an already-assembled video job.

Usage:
    python scripts/retry_upload.py

It will:
  1. Log in with email/password to get a Firebase ID token
  2. Check GET /videos/{video_job_id} to confirm the doc exists and has a video_path
  3. POST /videos/{video_job_id}/retry-upload to kick off the upload + schedule
  4. Poll GET /videos/{video_job_id} until youtube_video_id is set or status=failed
"""
import time
import httpx

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL      = "http://localhost:8080"
VIDEO_JOB_ID  = "dacd5804-19c6-416a-ac44-566a958048ee"
NICHE         = "tech"
DEADLINE      = None   # e.g. "Tuesday" or None

# Your login credentials (same account that connected YouTube)
EMAIL    = input("Email: ").strip()
PASSWORD = input("Password: ").strip()

# ── Step 1: Login ─────────────────────────────────────────────────────────────
print("\n[1/4] Logging in...")
resp = httpx.post(f"{BASE_URL}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
resp.raise_for_status()
token = resp.json()["id_token"]
uid   = resp.json()["uid"]
print(f"      ✓ Logged in as {uid}")

headers = {"Authorization": f"Bearer {token}"}

# ── Step 2: Check the video doc ───────────────────────────────────────────────
print(f"\n[2/4] Checking video job {VIDEO_JOB_ID}...")
resp = httpx.get(f"{BASE_URL}/videos/{VIDEO_JOB_ID}", headers=headers, timeout=15)

if resp.status_code == 404:
    print("      ✗ Video job not found in Firestore.")
    print("        Make sure the video_job_id is correct.")
    raise SystemExit(1)

resp.raise_for_status()
doc = resp.json()

print(f"      status       : {doc.get('status')}")
print(f"      current_stage: {doc.get('current_stage')}")
print(f"      video_path   : {doc.get('video_path')}")
print(f"      youtube_id   : {doc.get('youtube_video_id')}")
print(f"      error        : {doc.get('error')}")

if not doc.get("video_path"):
    print("\n      ✗ No video_path on this job — assembly may not have completed.")
    raise SystemExit(1)

if doc.get("youtube_video_id"):
    print(f"\n      ✓ Already uploaded: {doc.get('youtube_url')}")
    raise SystemExit(0)

# ── Step 3: Trigger retry ─────────────────────────────────────────────────────
print(f"\n[3/4] Triggering retry upload...")
payload = {"niche": NICHE, "deadline": DEADLINE}
resp = httpx.post(
    f"{BASE_URL}/videos/{VIDEO_JOB_ID}/retry-upload",
    headers=headers,
    json=payload,
    timeout=15,
)
resp.raise_for_status()
print(f"      ✓ {resp.json()['message']}")

# ── Step 4: Poll until done ───────────────────────────────────────────────────
print(f"\n[4/4] Polling for result (up to 5 min)...")
for attempt in range(60):   # 60 × 5s = 5 min
    time.sleep(5)
    resp = httpx.get(f"{BASE_URL}/videos/{VIDEO_JOB_ID}", headers=headers, timeout=15)
    resp.raise_for_status()
    doc = resp.json()
    status = doc.get("status")
    stage  = doc.get("current_stage", "")
    print(f"      [{attempt+1:02d}] status={status}  stage={stage}")

    if doc.get("youtube_video_id"):
        print(f"\n      ✓ Upload successful!")
        print(f"        YouTube ID : {doc['youtube_video_id']}")
        print(f"        YouTube URL: {doc.get('youtube_url')}")
        break
    if status == "failed":
        print(f"\n      ✗ Upload failed: {doc.get('error')}")
        break
else:
    print("\n      ✗ Timed out after 5 minutes.")
