"""
Per-user Google Calendar OAuth 2.0 flow.

Each user connects their own Google Calendar by going through Google's
OAuth consent screen. The resulting refresh token is stored in Firestore
(collection: user_calendar_tokens) keyed by the user's Firebase UID.

Flow:
  1. GET  /auth/calendar          → redirect to Google consent screen
  2. GET  /auth/calendar/callback → exchange code for tokens, store in DB
  3. GET  /auth/calendar/status   → check if user has connected Calendar
  4. POST /auth/calendar/disconnect → remove stored tokens
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

_GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
# calendar.events  — create/edit events
# calendar.readonly — read calendar metadata (needed for /calendars/primary fetch)
_CALENDAR_SCOPE = (
    "https://www.googleapis.com/auth/calendar.events"
    " https://www.googleapis.com/auth/calendar.readonly"
)
_TOKEN_COLLECTION = "user_calendar_tokens"


def get_calendar_auth_url(user_uid: str, redirect_uri: str) -> str:
    """
    Build the Google OAuth consent URL for Google Calendar access.
    The user's Firebase UID is embedded in the `state` param so we can
    associate the callback with the correct user.
    """
    csrf_token = secrets.token_urlsafe(32)
    state = f"{user_uid}:{csrf_token}"

    db.save("calendar_oauth_state", user_uid, {
        "csrf_token": csrf_token,
        "user_uid": user_uid,
    })

    params = {
        "client_id": settings.calendar_client_id or settings.youtube_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _CALENDAR_SCOPE,
        "access_type": "offline",
        "prompt": "consent",        # Always request refresh_token
        "state": state,
        "include_granted_scopes": "true",
    }
    return f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_calendar_code_for_tokens(
    code: str,
    redirect_uri: str,
    user_uid: str,
    csrf_token: str,
) -> dict:
    """
    Exchange the authorization code from Google's callback for tokens.
    Validates CSRF, stores the refresh token in Firestore.
    """
    stored = db.get("calendar_oauth_state", user_uid)
    if not stored or stored.get("csrf_token") != csrf_token:
        raise ValueError("Invalid or expired OAuth state — please try connecting again")

    db.delete("calendar_oauth_state", user_uid)

    client_id     = settings.calendar_client_id or settings.youtube_client_id
    client_secret = settings.calendar_client_secret or settings.youtube_client_secret

    async with httpx.AsyncClient() as client:
        resp = await client.post(_GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=15)

    data = resp.json()
    if resp.status_code != 200:
        error_msg = data.get("error_description", data.get("error", "Token exchange failed"))
        raise ValueError(f"Google OAuth error: {error_msg}")

    refresh_token = data.get("refresh_token")
    access_token  = data.get("access_token")

    if not refresh_token:
        raise ValueError(
            "No refresh token received. This can happen if you previously "
            "connected and revoked access. Try revoking app access at "
            "https://myaccount.google.com/permissions and reconnecting."
        )

    # Fetch the primary calendar summary for display
    calendar_summary = ""
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient() as cal_client:
            cal_resp = await cal_client.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary",
                headers=headers,
                timeout=10,
            )
            if cal_resp.status_code == 200:
                calendar_summary = cal_resp.json().get("summary", "")
            else:
                logger.warning("Calendar primary fetch failed (%s): %s", cal_resp.status_code, cal_resp.text)
    except Exception as e:
        logger.warning("Could not fetch Calendar info: %s", e)

    db.save(_TOKEN_COLLECTION, user_uid, {
        "user_uid": user_uid,
        "refresh_token": refresh_token,
        "calendar_summary": calendar_summary,
        "connected": True,
    })

    logger.info(
        "Calendar connected for user %s (calendar: %s)",
        user_uid, calendar_summary or "primary",
    )

    return {
        "connected": True,
        "calendar_summary": calendar_summary,
    }


def get_user_calendar_tokens(user_uid: str) -> Optional[dict]:
    """Retrieve stored Calendar OAuth tokens for a user."""
    doc = db.get(_TOKEN_COLLECTION, user_uid)
    if not doc or not doc.get("connected"):
        return None
    return doc


def get_user_calendar_credentials(user_uid: str):
    """
    Build google.oauth2.credentials.Credentials for a specific user.
    Returns None if the user hasn't connected Calendar.
    Falls back to the server-side env tokens if no per-user token exists.
    """
    tokens = get_user_calendar_tokens(user_uid)
    if tokens:
        refresh_token = tokens["refresh_token"]
        client_id     = settings.calendar_client_id or settings.youtube_client_id
        client_secret = settings.calendar_client_secret or settings.youtube_client_secret
    elif settings.calendar_refresh_token:
        # Legacy: server-side token from .env
        refresh_token = settings.calendar_refresh_token
        client_id     = settings.calendar_client_id
        client_secret = settings.calendar_client_secret
    else:
        return None

    from google.oauth2.credentials import Credentials
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri=_GOOGLE_TOKEN_URL,
    )


def disconnect_calendar(user_uid: str) -> None:
    """Remove stored Calendar tokens for a user."""
    db.delete(_TOKEN_COLLECTION, user_uid)
    logger.info("Calendar disconnected for user %s", user_uid)


def is_calendar_connected(user_uid: str) -> dict:
    """Check if a user has connected their Google Calendar account."""
    tokens = get_user_calendar_tokens(user_uid)
    if tokens:
        return {
            "connected": True,
            "calendar_summary": tokens.get("calendar_summary", ""),
        }
    # Server-side .env token is a legacy fallback — do NOT report as connected
    # for per-user flows since those tokens may be expired and belong to a
    # different account. Only report connected if the user has their own token.
    return {"connected": False, "calendar_summary": ""}
