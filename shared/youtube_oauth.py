"""
Per-user YouTube OAuth 2.0 flow.

Each user connects their own YouTube channel by going through Google's
OAuth consent screen. The resulting refresh token is stored in Firestore
(collection: user_youtube_tokens) keyed by the user's Firebase UID.

Flow:
  1. GET  /auth/youtube          → redirect to Google consent screen
  2. GET  /auth/youtube/callback → exchange code for tokens, store in DB
  3. GET  /auth/youtube/status   → check if user has connected YouTube
  4. POST /auth/youtube/disconnect → remove stored tokens
"""
from __future__ import annotations
import logging
import secrets
from typing import Optional
from urllib.parse import urlencode

import httpx

from shared.config import settings
from shared.database import db

logger = logging.getLogger(__name__)

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
# youtube.upload  — allows uploading videos
# youtube.readonly — allows reading channel/video metadata (needed for channel info fetch)
# yt-analytics.readonly — allows reading YouTube Analytics reports (views, watch time, etc.)
_YOUTUBE_UPLOAD_SCOPE = (
    "https://www.googleapis.com/auth/youtube.upload"
    " https://www.googleapis.com/auth/youtube.readonly"
    " https://www.googleapis.com/auth/yt-analytics.readonly"
)
_TOKEN_COLLECTION = "user_youtube_tokens"


def get_youtube_auth_url(user_uid: str, redirect_uri: str) -> str:
    """
    Build the Google OAuth consent URL for YouTube upload access.
    The user's Firebase UID is embedded in the `state` param so we can
    associate the callback with the correct user.
    """
    # Generate a CSRF token and store it temporarily
    csrf_token = secrets.token_urlsafe(32)
    state = f"{user_uid}:{csrf_token}"

    # Store CSRF token for validation on callback
    db.save("youtube_oauth_state", user_uid, {
        "csrf_token": csrf_token,
        "user_uid": user_uid,
    })

    params = {
        "client_id": settings.youtube_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _YOUTUBE_UPLOAD_SCOPE,
        "access_type": "offline",       # Get a refresh token
        "prompt": "consent",            # Always show consent to get refresh token
        "state": state,
        "include_granted_scopes": "true",
    }
    return f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_tokens(
    code: str,
    redirect_uri: str,
    user_uid: str,
    csrf_token: str,
) -> dict:
    """
    Exchange the authorization code from Google's callback for access + refresh tokens.
    Validates the CSRF token, then stores the refresh token in Firestore.
    """
    # Validate CSRF
    stored = db.get("youtube_oauth_state", user_uid)
    if not stored or stored.get("csrf_token") != csrf_token:
        raise ValueError("Invalid or expired OAuth state — please try connecting again")

    # Clean up the state token
    db.delete("youtube_oauth_state", user_uid)

    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        resp = await client.post(_GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": settings.youtube_client_id,
            "client_secret": settings.youtube_client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=15)

    data = resp.json()
    if resp.status_code != 200:
        error_msg = data.get("error_description", data.get("error", "Token exchange failed"))
        raise ValueError(f"Google OAuth error: {error_msg}")

    refresh_token = data.get("refresh_token")
    access_token = data.get("access_token")

    if not refresh_token:
        raise ValueError(
            "No refresh token received. This can happen if you previously "
            "connected and revoked access. Try revoking app access at "
            "https://myaccount.google.com/permissions and reconnecting."
        )

    # Fetch the user's YouTube channel info for display
    channel_title = ""
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient() as ch_client:
            ch_resp = await ch_client.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "snippet", "mine": "true"},
                headers=headers,
                timeout=10,
            )
            if ch_resp.status_code == 200:
                items = ch_resp.json().get("items", [])
                if items:
                    channel_title = items[0]["snippet"]["title"]
            else:
                logger.warning("YouTube channel fetch failed (%s): %s", ch_resp.status_code, ch_resp.text)
    except Exception as e:
        logger.warning("Could not fetch YouTube channel info: %s", e)

    # Store tokens in Firestore
    db.save(_TOKEN_COLLECTION, user_uid, {
        "user_uid": user_uid,
        "refresh_token": refresh_token,
        "channel_title": channel_title,
        "connected": True,
    })

    logger.info("YouTube connected for user %s (channel: %s)", user_uid, channel_title or "unknown")

    return {
        "connected": True,
        "channel_title": channel_title,
    }


def get_user_youtube_tokens(user_uid: str) -> Optional[dict]:
    """
    Retrieve stored YouTube OAuth tokens for a user.
    Returns None if the user hasn't connected YouTube.
    """
    doc = db.get(_TOKEN_COLLECTION, user_uid)
    if not doc or not doc.get("connected"):
        return None
    return doc


def get_user_youtube_credentials(user_uid: str):
    """
    Build google.oauth2.credentials.Credentials for a specific user.
    Returns None if the user hasn't connected YouTube.
    """
    tokens = get_user_youtube_tokens(user_uid)
    if not tokens:
        return None

    from google.oauth2.credentials import Credentials
    return Credentials(
        token=None,
        refresh_token=tokens["refresh_token"],
        client_id=settings.youtube_client_id,
        client_secret=settings.youtube_client_secret,
        token_uri=_GOOGLE_TOKEN_URL,
    )


def disconnect_youtube(user_uid: str) -> None:
    """Remove stored YouTube tokens for a user."""
    db.delete(_TOKEN_COLLECTION, user_uid)
    logger.info("YouTube disconnected for user %s", user_uid)


def is_youtube_connected(user_uid: str) -> dict:
    """Check if a user has connected their YouTube account."""
    tokens = get_user_youtube_tokens(user_uid)
    if tokens:
        return {
            "connected": True,
            "channel_title": tokens.get("channel_title", ""),
        }
    return {"connected": False, "channel_title": ""}
