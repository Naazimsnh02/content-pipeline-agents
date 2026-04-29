"""
Firebase Authentication helpers.

- init_firebase()        — initialise Firebase Admin SDK (called once at startup)
- verify_token()         — verify a Firebase ID token from the Authorization header
- get_current_user()     — FastAPI dependency that extracts the authenticated user
- signup_with_email()    — create a new user via Firebase Auth REST API
- login_with_email()     — sign in with email/password via Firebase Auth REST API
"""
from __future__ import annotations
import logging
from typing import Optional

import firebase_admin
from firebase_admin import auth as fb_auth, credentials
from fastapi import Depends, HTTPException, Request
import httpx

from shared.config import settings

logger = logging.getLogger(__name__)

_firebase_app: Optional[firebase_admin.App] = None


# ── Initialisation ───────────────────────────────────────────────────────────

def init_firebase() -> None:
    """Initialise Firebase Admin SDK using Application Default Credentials."""
    global _firebase_app
    if _firebase_app is not None:
        return
    try:
        # Uses GOOGLE_APPLICATION_CREDENTIALS or GCE metadata automatically.
        # For local dev with gcloud CLI: `gcloud auth application-default login`
        # The project is inferred from settings.google_cloud_project.
        cred = credentials.ApplicationDefault()
        _firebase_app = firebase_admin.initialize_app(cred, {
            "projectId": settings.google_cloud_project,
        })
        logger.info("Firebase Admin SDK initialised [project=%s]", settings.google_cloud_project)
    except Exception as exc:
        logger.warning("Firebase Admin SDK init failed: %s — auth will be disabled", exc)


# ── Token verification ───────────────────────────────────────────────────────

def verify_token(id_token: str) -> dict:
    """
    Verify a Firebase ID token and return the decoded claims.
    Raises HTTPException 401 on failure.
    """
    if _firebase_app is None:
        raise HTTPException(status_code=503, detail="Auth service not initialised")
    try:
        decoded = fb_auth.verify_id_token(id_token, app=_firebase_app)
        return decoded
    except fb_auth.ExpiredIdTokenError:
        raise HTTPException(status_code=401, detail="Token expired — please sign in again")
    except fb_auth.InvalidIdTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as exc:
        logger.error("Token verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Authentication failed")


# ── FastAPI dependency ───────────────────────────────────────────────────────

async def get_current_user(request: Request) -> dict:
    """
    FastAPI dependency — extracts and verifies the Firebase ID token
    from the Authorization: Bearer <token> header.

    Returns the decoded token dict with at least 'uid' and 'email'.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth_header[7:]
    return verify_token(token)


# ── Client-side auth via Firebase REST API ───────────────────────────────────
# These use the Firebase Auth REST API so the server can handle signup/login
# without requiring the Firebase JS SDK on the client.

_FIREBASE_AUTH_URL = "https://identitytoolkit.googleapis.com/v1/accounts"


async def signup_with_email(email: str, password: str, display_name: str = "") -> dict:
    """Create a new Firebase user with email/password."""
    if not settings.firebase_api_key:
        raise HTTPException(status_code=503, detail="FIREBASE_API_KEY not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_FIREBASE_AUTH_URL}:signUp",
            params={"key": settings.firebase_api_key},
            json={
                "email": email,
                "password": password,
                "returnSecureToken": True,
            },
            timeout=15,
        )

    data = resp.json()
    if resp.status_code != 200:
        msg = data.get("error", {}).get("message", "Signup failed")
        # Translate Firebase error codes to friendly messages
        friendly = {
            "EMAIL_EXISTS": "An account with this email already exists",
            "WEAK_PASSWORD": "Password must be at least 6 characters",
            "INVALID_EMAIL": "Please enter a valid email address",
        }
        raise HTTPException(status_code=400, detail=friendly.get(msg, msg))

    # Update display name if provided
    if display_name:
        try:
            fb_auth.update_user(data["localId"], display_name=display_name, app=_firebase_app)
        except Exception:
            pass  # Non-critical

    return {
        "uid": data["localId"],
        "email": data["email"],
        "id_token": data["idToken"],
        "refresh_token": data["refreshToken"],
        "expires_in": data["expiresIn"],
    }


async def login_with_email(email: str, password: str) -> dict:
    """Sign in an existing Firebase user with email/password."""
    if not settings.firebase_api_key:
        raise HTTPException(status_code=503, detail="FIREBASE_API_KEY not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_FIREBASE_AUTH_URL}:signInWithPassword",
            params={"key": settings.firebase_api_key},
            json={
                "email": email,
                "password": password,
                "returnSecureToken": True,
            },
            timeout=15,
        )

    data = resp.json()
    if resp.status_code != 200:
        msg = data.get("error", {}).get("message", "Login failed")
        friendly = {
            "EMAIL_NOT_FOUND": "No account found with this email",
            "INVALID_PASSWORD": "Incorrect password",
            "INVALID_LOGIN_CREDENTIALS": "Invalid email or password",
            "USER_DISABLED": "This account has been disabled",
            "TOO_MANY_ATTEMPTS_TRY_LATER": "Too many failed attempts — try again later",
        }
        raise HTTPException(status_code=401, detail=friendly.get(msg, msg))

    return {
        "uid": data["localId"],
        "email": data["email"],
        "display_name": data.get("displayName", ""),
        "id_token": data["idToken"],
        "refresh_token": data["refreshToken"],
        "expires_in": data["expiresIn"],
    }


async def refresh_id_token(refresh_token: str) -> dict:
    """Exchange a refresh token for a new ID token."""
    if not settings.firebase_api_key:
        raise HTTPException(status_code=503, detail="FIREBASE_API_KEY not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://securetoken.googleapis.com/v1/token",
            params={"key": settings.firebase_api_key},
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=15,
        )

    data = resp.json()
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Session expired — please sign in again")

    return {
        "id_token": data["id_token"],
        "refresh_token": data["refresh_token"],
        "expires_in": data["expires_in"],
    }


async def sign_in_with_google(id_token: str = None, access_token: str = None) -> dict:
    """
    Sign in (or create account) using a Google credential.

    Accepts either:
    - id_token:     JWT from GSI accounts.id (One Tap / FedCM)
    - access_token: OAuth2 access token from accounts.oauth2.initTokenClient popup

    Firebase's signInWithIdp REST endpoint accepts both via the postBody field.
    """
    if not settings.firebase_api_key:
        raise HTTPException(status_code=503, detail="FIREBASE_API_KEY not configured")

    if not id_token and not access_token:
        raise HTTPException(status_code=400, detail="No Google token provided")

    # Build postBody for Firebase signInWithIdp
    if access_token:
        post_body = f"access_token={access_token}&providerId=google.com"
    else:
        post_body = f"id_token={id_token}&providerId=google.com"

    # requestUri must match an authorized redirect URI in the Google OAuth client.
    # Use the app's base URL from settings, falling back to localhost for dev.
    app_base_url = getattr(settings, "app_base_url", None) or "http://localhost"
    app_base_url = app_base_url.rstrip("/")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_FIREBASE_AUTH_URL}:signInWithIdp",
            params={"key": settings.firebase_api_key},
            json={
                "postBody": post_body,
                "requestUri": app_base_url,
                "returnIdpCredential": True,
                "returnSecureToken": True,
            },
            timeout=15,
        )

    data = resp.json()
    if resp.status_code != 200:
        msg = data.get("error", {}).get("message", "Google sign-in failed")
        raise HTTPException(status_code=401, detail=msg)

    return {
        "uid": data["localId"],
        "email": data.get("email", ""),
        "display_name": data.get("displayName", ""),
        "photo_url": data.get("photoUrl", ""),
        "id_token": data["idToken"],
        "refresh_token": data["refreshToken"],
        "expires_in": data["expiresIn"],
        "is_new_user": data.get("isNewUser", False),
    }


import urllib.parse


def get_google_oauth_url(redirect_uri: str) -> str:
    """
    Build the Google OAuth2 authorization URL for the sign-in popup.
    Uses the Desktop OAuth client (YOUTUBE_CLIENT_ID) — Desktop clients allow
    http://localhost redirect URIs, which is what we use here.
    """
    client_id = settings.youtube_client_id
    if not client_id:
        raise HTTPException(status_code=503, detail="YOUTUBE_CLIENT_ID not configured")

    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


async def sign_in_with_google_code(auth_code: str, redirect_uri: str = None) -> dict:
    """
    Exchange a Google OAuth2 authorization code for a Firebase session.

    Flow:
      1. POST to Google's token endpoint with the exact redirect_uri used in the
         authorization request → {id_token, access_token}
      2. Pass the id_token to Firebase signInWithIdp → Firebase session tokens
    """
    if not settings.firebase_api_key:
        raise HTTPException(status_code=503, detail="FIREBASE_API_KEY not configured")

    client_id = settings.youtube_client_id
    client_secret = settings.youtube_client_secret
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth client credentials not configured (YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET)",
        )

    if not redirect_uri:
        raise HTTPException(status_code=400, detail="redirect_uri is required for token exchange")

    # Step 1: Exchange auth code for tokens at Google's token endpoint.
    # redirect_uri must exactly match the one used in the authorization request.
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": auth_code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=15,
        )

    token_data = token_resp.json()
    if token_resp.status_code != 200:
        err = token_data.get("error_description") or token_data.get("error", "Token exchange failed")
        logger.error("Google token exchange failed: %s", token_data)
        raise HTTPException(status_code=401, detail=f"Google auth failed: {err}")

    id_token = token_data.get("id_token")
    if not id_token:
        raise HTTPException(status_code=401, detail="Google did not return an ID token")

    # Step 2: Pass the id_token to Firebase signInWithIdp to create/link the account.
    return await sign_in_with_google(id_token=id_token)
