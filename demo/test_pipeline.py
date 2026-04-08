"""
End-to-end integration tests for the content pipeline.
Can run locally (DEMO_MODE=true) or against a deployed Cloud Run service.

Usage:
  # Local
  python demo/test_pipeline.py

  # Against deployed service
  PIPELINE_URL=https://your-service-url.run.app python demo/test_pipeline.py
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time

import httpx

BASE_URL = os.getenv("PIPELINE_URL", "http://localhost:8080")
TIMEOUT = int(os.getenv("TEST_TIMEOUT", "120"))


def print_section(title: str):
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print("═" * 60)


def print_result(label: str, value):
    if isinstance(value, dict):
        print(f"  {label}:")
        for k, v in value.items():
            if k not in ("coordinator_response",):
                print(f"    {k}: {v}")
    else:
        print(f"  {label}: {value}")


# ── Test 1: Health Check ──────────────────────────────────────────────────────

def test_health(client: httpx.Client):
    print_section("TEST 1: Health Check")
    resp = client.get("/health")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    print_result("Health", data)
    assert data["status"] == "ok"
    print("  ✅ PASSED")
    return data


# ── Test 2: Root / API Info ───────────────────────────────────────────────────

def test_root(client: httpx.Client):
    print_section("TEST 2: API Info")
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    print_result("Service", data)
    assert "agents" in data
    assert len(data["agents"]) >= 6
    print("  ✅ PASSED")


# ── Test 3: Chat with Coordinator ────────────────────────────────────────────

def test_chat(client: httpx.Client):
    print_section("TEST 3: Chat — Topic Ideas Request")
    resp = client.post(
        "/chat",
        json={
            "message": "What are the top 3 trending tech topics I should cover this week?",
            "user_id": "test_user",
        },
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200, f"Chat failed: {resp.text}"
    data = resp.json()
    print(f"  Session ID: {data['session_id']}")
    print(f"  Model: {data['model']}")
    print(f"\n  Response Preview:")
    # Print first 500 chars of response
    response_preview = data["response"][:500]
    for line in response_preview.split("\n"):
        print(f"    {line}")
    if len(data["response"]) > 500:
        print("    [... truncated ...]")
    assert len(data["response"]) > 50, "Response too short"
    print("\n  ✅ PASSED")
    return data["session_id"]


# ── Test 4: Full Pipeline ─────────────────────────────────────────────────────

def test_pipeline(client: httpx.Client):
    print_section("TEST 4: Full Pipeline (DEMO_MODE)")
    print("  Running full pipeline: ideas → research → script → production → schedule")
    print("  This may take 30-90 seconds...")

    start = time.time()
    resp = client.post(
        "/pipeline",
        json={
            "request": "Create a YouTube Short about the latest breakthroughs in AI medical diagnosis",
            "niche": "tech",
            "creator_id": "default",
            "deadline": "next Tuesday",
        },
        timeout=TIMEOUT,
    )
    elapsed = time.time() - start

    assert resp.status_code == 200, f"Pipeline failed ({resp.status_code}): {resp.text}"
    data = resp.json()

    print(f"\n  Job ID: {data.get('job_id')}")
    print(f"  Status URL: {data.get('status_url')}")
    print(f"  Time: {elapsed:.1f}s")
    print(f"\n  Coordinator Response Preview:")
    response = data.get("coordinator_response", "")
    for line in response[:800].split("\n"):
        print(f"    {line}")
    if len(response) > 800:
        print("    [... truncated ...]")

    assert data.get("job_id"), "No job_id in response"
    print("\n  ✅ PASSED")
    return data["job_id"]


# ── Test 5: Job Status ────────────────────────────────────────────────────────

def test_job_status(client: httpx.Client, job_id: str):
    print_section("TEST 5: Job Status Check")
    resp = client.get(f"/pipeline/{job_id}")
    assert resp.status_code == 200, f"Status check failed: {resp.text}"
    data = resp.json()
    print(f"  Status: {data.get('status')}")
    print(f"  Job ID: {data.get('job_id')}")
    print("  ✅ PASSED")


# ── Test 6: Individual Agent via Chat ─────────────────────────────────────────

def test_research_only(client: httpx.Client):
    print_section("TEST 6: Research-Only Chat")
    resp = client.post(
        "/chat",
        json={
            "message": (
                "Only run the research step for this topic: "
                "'GPT-5 performance on medical benchmarks'. "
                "Give me a research brief with key facts and statistics."
            ),
            "user_id": "test_user_research",
        },
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200, f"Research chat failed: {resp.text}"
    data = resp.json()
    print(f"\n  Response Preview:")
    for line in data["response"][:600].split("\n"):
        print(f"    {line}")
    print("\n  ✅ PASSED")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'╔' + '═' * 58 + '╗'}")
    print(f"║  YouTube Content Pipeline — Integration Tests{' ' * 13}║")
    print(f"║  Target: {BASE_URL:<49}║")
    print(f"╚{'═' * 58}╝")

    with httpx.Client(base_url=BASE_URL) as client:
        try:
            health = test_health(client)
            demo_mode = health.get("demo_mode", True)
            print(f"\n  Note: DEMO_MODE={demo_mode} (heavy production steps {'simulated' if demo_mode else 'REAL'})")

            test_root(client)
            session_id = test_chat(client)
            job_id = test_pipeline(client)
            test_job_status(client, job_id)
            test_research_only(client)

            print(f"\n{'═' * 60}")
            print(f"  ✅ All tests passed!")
            print(f"{'═' * 60}\n")

        except AssertionError as e:
            print(f"\n  ❌ Test FAILED: {e}\n")
            sys.exit(1)
        except httpx.ConnectError:
            print(f"\n  ❌ Cannot connect to {BASE_URL}")
            print(f"     Make sure the server is running: python app.py\n")
            sys.exit(1)


if __name__ == "__main__":
    main()
