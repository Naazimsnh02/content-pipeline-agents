"""
Hermes — Autonomous YouTube Content Pipeline API.

Multi-agent system powered by Google ADK + Gemini. Converts a natural-language
request into a researched, A/B-tested, produced, and scheduled YouTube Short
with burned-in captions, AI thumbnails, and Twitter/X distribution.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Optional

import uvicorn
from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from shared.config import settings
from shared.database import db
from shared.models import PipelineRequest, PipelineResponse
from shared.niches import list_niches
from shared.auth import (
    init_firebase, get_current_user,
    signup_with_email, login_with_email, refresh_id_token,
    sign_in_with_google, sign_in_with_google_code, get_google_oauth_url,
)
from shared.youtube_oauth import (
    get_youtube_auth_url, exchange_code_for_tokens,
    is_youtube_connected, disconnect_youtube,
)
from shared.calendar_oauth import (
    get_calendar_auth_url, exchange_calendar_code_for_tokens,
    is_calendar_connected, disconnect_calendar,
)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Temp working directory (shared with production tools) ────────────────────
_WORK_DIR = Path(tempfile.gettempdir()) / "content_pipeline"
_WORK_DIR.mkdir(exist_ok=True)

# ── ADK Setup ─────────────────────────────────────────────────────────────────
# AQ. keys are Vertex AI Express keys. genai.Client(api_key="AQ.xxx") routes
# them to aiplatform.googleapis.com (paid tier) automatically. But ADK with
# GOOGLE_API_KEY set uses the AI Studio backend (generativelanguage.googleapis.com)
# even for AQ. keys, which applies free-tier quotas.
#
# Rule: if GOOGLE_GENAI_USE_VERTEXAI=true, clear GOOGLE_API_KEY so ADK picks
#       the Vertex AI backend. Set GOOGLE_GENAI_API_KEY for the Express key.
#       if false, set GOOGLE_API_KEY normally for AI Studio.

_effective_api_key = settings.vertex_api_key or settings.google_api_key

if settings.google_genai_use_vertexai:
    # Vertex AI mode — do NOT set GOOGLE_API_KEY (causes ADK to fall back to
    # AI Studio / GEMINI_API backend). Pass the Express key via the dedicated var.
    os.environ.pop("GOOGLE_API_KEY", None)
    if _effective_api_key:
        os.environ["GOOGLE_GENAI_API_KEY"] = _effective_api_key
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.google_cloud_project)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.google_cloud_location)
    logger.info("ADK backend: Vertex AI (project=%s, location=%s)",
                settings.google_cloud_project, settings.google_cloud_location)
else:
    # AI Studio mode — set GOOGLE_API_KEY normally.
    if _effective_api_key:
        os.environ["GOOGLE_API_KEY"] = _effective_api_key
    logger.info("ADK backend: Gemini AI Studio")

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from agents.coordinator.agent import root_agent as coordinator_agent

_session_service = InMemorySessionService()
_runner: Optional[Runner] = None


def get_runner() -> Runner:
    global _runner
    if _runner is None:
        _runner = Runner(
            agent=coordinator_agent,
            app_name=settings.app_name,
            session_service=_session_service,
        )
        logger.info("ADK Runner initialised with model: %s", settings.gemini_model)
    return _runner


# ── FastAPI App ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting YouTube Content Pipeline Agent [DEMO_MODE=%s]", settings.demo_mode)
    init_firebase()
    get_runner()  # Pre-warm the runner
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="Hermes — Autonomous YouTube Content Pipeline",
    description=(
        "Multi-agent system powered by Google ADK + Gemini. Converts a natural-language "
        "request into a researched, A/B-tested, produced, and scheduled YouTube Short "
        "with burned-in captions, AI thumbnails, and Twitter/X distribution.\n\n"
        "**Authentication:** Most endpoints require `Authorization: Bearer <firebase_id_token>`. "
        "SSE stream uses `?token=<id_token>` since EventSource doesn't support headers."
    ),
    version="2.0.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Health", "description": "Service health and configuration"},
        {"name": "Auth", "description": "Firebase Authentication — signup, login, Google SSO, token refresh"},
        {"name": "YouTube OAuth", "description": "Per-user YouTube channel connection via OAuth 2.0"},
        {"name": "Calendar OAuth", "description": "Per-user Google Calendar connection via OAuth 2.0"},
        {"name": "Pipeline", "description": "Content pipeline — submit jobs, track progress, download outputs"},
        {"name": "Chat", "description": "Conversational interface with the coordinator agent"},
        {"name": "Creator Profiles", "description": "Manage creator voice profiles (tone, pacing, hook style, CTA)"},
        {"name": "Videos", "description": "Video job status and upload retry"},
        {"name": "Analytics", "description": "Post-publish performance metrics and feedback loop"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static frontend files
import os as _os
_static_dir = _os.path.join(_os.path.dirname(__file__), "static")
if _os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# ── Request/Response Models ───────────────────────────────────────────────────

class AuthSignupRequest(BaseModel):
    email: str = Field(..., description="User email address")
    password: str = Field(..., description="Password (min 6 characters)")
    display_name: str = Field("", description="Optional display name")


class AuthLoginRequest(BaseModel):
    email: str = Field(..., description="Registered email address")
    password: str = Field(..., description="Account password")


class AuthRefreshRequest(BaseModel):
    refresh_token: str = Field(..., description="Firebase refresh token from login/signup response")


class AuthGoogleRequest(BaseModel):
    google_id_token: Optional[str] = Field(None, description="Google ID token JWT from GSI accounts.id")
    google_access_token: Optional[str] = Field(None, description="Google OAuth2 access token from popup flow")


class AuthGoogleCodeRequest(BaseModel):
    code: str = Field(..., description="Authorization code from google.accounts.oauth2.initCodeClient popup")


class ChatRequest(BaseModel):
    message: str = Field(..., description="Message to send to the coordinator agent")
    session_id: Optional[str] = Field(None, description="Existing session ID to continue a conversation; omit to start new")


class ChatResponse(BaseModel):
    session_id: str = Field(..., description="Session ID for continuing this conversation")
    user_id: str = Field(..., description="Authenticated user ID")
    response: str = Field(..., description="Agent response text")
    model: str = Field(..., description="LLM model used")
    timestamp: str = Field(..., description="ISO 8601 timestamp")


class JobStatusResponse(BaseModel):
    job_id: str = Field(..., description="Pipeline job ID")
    status: str = Field(..., description="Job status: running, completed, or failed")
    data: Optional[dict] = Field(None, description="Full job data including request, response, and stage info")


# ── Core ADK runner helper ────────────────────────────────────────────────────

async def run_agent(message: str, user_id: str, session_id: Optional[str] = None) -> tuple[str, str]:
    """Run the coordinator agent and return (response_text, session_id)."""
    # Prepend current date/time so agents use accurate dates in searches
    now = datetime.utcnow().strftime("%A, %B %d, %Y %H:%M UTC")
    message = f"[Current date and time: {now}]\n\n{message}"

    runner = get_runner()

    # Create or reuse session
    if session_id:
        session = await _session_service.get_session(
            app_name=settings.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if not session:
            session = await _session_service.create_session(
                app_name=settings.app_name, user_id=user_id
            )
    else:
        session = await _session_service.create_session(
            app_name=settings.app_name, user_id=user_id
        )

    new_message = Content(role="user", parts=[Part(text=message)])

    final_text = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=new_message,
    ):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    final_text += part.text

    return final_text or "No response generated.", session.id


# Maps agent name substrings → pipeline stage key
_AGENT_STAGE_MAP = {
    "ideas":      "ideas",
    "research":   "research",
    "script":     "script",
    "production": "production",
    "scheduler":  "scheduling",
    "analytics":  "analytics",
}

# ── SSE Event Queues ─────────────────────────────────────────────────────────
# job_id → list of asyncio.Queue for active SSE subscribers
_sse_subscribers: dict[str, list[asyncio.Queue]] = {}


def _emit_sse_event(job_id: str, event_type: str, data: dict) -> None:
    """Push an SSE event to all active subscribers for a job."""
    queues = _sse_subscribers.get(job_id, [])
    payload = {"event": event_type, "data": data}
    for q in queues:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


async def run_agent_with_progress(
    message: str,
    user_id: str,
    job_id: str,
) -> tuple[str, str]:
    """
    Run the coordinator agent, updating pipeline_jobs.current_stage in the DB
    whenever a sub-agent starts processing.
    Returns (response_text, session_id).
    """
    runner = get_runner()
    session = await _session_service.create_session(
        app_name=settings.app_name, user_id=user_id
    )
    new_message = Content(role="user", parts=[Part(text=message)])

    final_text = ""
    current_stage = "ideas"

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=new_message,
    ):
        # Detect which sub-agent is active from the event author
        author = getattr(event, "author", "") or ""
        stage_detected = False

        # Method 1: Check event author (works when sub-agent events bubble up)
        if author:
            logger.info("[stage-detect] event author=%r for job %s", author, job_id)
            for key, stage in _AGENT_STAGE_MAP.items():
                if key in author.lower() and stage != current_stage:
                    current_stage = stage
                    stage_detected = True
                    break

        # Method 2: Check function calls (AgentTool invocations from coordinator)
        if not stage_detected:
            try:
                for fc in event.get_function_calls():
                    fc_name = (getattr(fc, "name", "") or "").lower()
                    if fc_name:
                        logger.info("[stage-detect] function_call name=%r for job %s", fc_name, job_id)
                    for key, stage in _AGENT_STAGE_MAP.items():
                        if key in fc_name and stage != current_stage:
                            current_stage = stage
                            stage_detected = True
                            break
                    if stage_detected:
                        break
            except Exception:
                pass

        # Method 3: Check transfer_to_agent action
        if not stage_detected:
            try:
                transfer = getattr(event.actions, "transfer_to_agent", None) or ""
                if transfer:
                    logger.info("[stage-detect] transfer_to_agent=%r for job %s", transfer, job_id)
                    for key, stage in _AGENT_STAGE_MAP.items():
                        if key in transfer.lower() and stage != current_stage:
                            current_stage = stage
                            stage_detected = True
                            break
            except Exception:
                pass

        if stage_detected:
            logger.info("Pipeline stage → %s (author=%r) for job %s", current_stage, author, job_id)
            try:
                job = db.get("pipeline_jobs", job_id) or {}
                job["current_stage"] = current_stage
                db.save("pipeline_jobs", job_id, job)
            except Exception:
                pass
            _emit_sse_event(job_id, "stage_update", {"stage": current_stage, "status": "running"})

        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    final_text += part.text

    return final_text or "No response generated.", session.id


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
async def root():
    """Serve the landing page."""
    landing = _os.path.join(_static_dir, "index.html")
    if _os.path.isfile(landing):
        return FileResponse(landing, media_type="text/html")
    return {
        "service": "YouTube Content Pipeline — Multi-Agent System",
        "version": "1.0.0",
        "model": settings.gemini_model,
        "demo_mode": settings.demo_mode,
        "agents": [
            "coordinator", "ideas_agent", "research_agent",
            "script_agent", "production_agent", "scheduler_agent", "analytics_agent"
        ],
        "endpoints": {
            "POST /pipeline": "Full pipeline from natural-language request",
            "POST /chat": "Direct chat with coordinator agent",
            "GET /pipeline/{job_id}": "Check job status",
            "GET /health": "Health check",
        },
    }


@app.get("/app", tags=["Health"], include_in_schema=False)
async def serve_app():
    """Serve the dashboard application."""
    app_html = _os.path.join(_static_dir, "app.html")
    if _os.path.isfile(app_html):
        return FileResponse(app_html, media_type="text/html")
    raise HTTPException(status_code=404, detail="App not found")


@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "model": settings.gemini_model,
        "demo_mode": settings.demo_mode,
        "firestore": settings.has_firestore,
        "youtube": settings.has_youtube,
        "calendar": settings.has_calendar,
    }


@app.get("/niches", tags=["Health"])
async def get_niches():
    """Return all available content niches."""
    return {"niches": list_niches()}


# ── Auth Endpoints ────────────────────────────────────────────────────────────

@app.post("/auth/signup", tags=["Auth"])
async def auth_signup(req: AuthSignupRequest):
    """Create a new account with email and password."""
    return await signup_with_email(req.email, req.password, req.display_name)


@app.post("/auth/login", tags=["Auth"])
async def auth_login(req: AuthLoginRequest):
    """Sign in with email and password."""
    return await login_with_email(req.email, req.password)


@app.post("/auth/refresh", tags=["Auth"])
async def auth_refresh(req: AuthRefreshRequest):
    """Exchange a refresh token for a new ID token."""
    return await refresh_id_token(req.refresh_token)


@app.post("/auth/google", tags=["Auth"])
async def auth_google(req: AuthGoogleRequest):
    """Sign in or create account with a Google token from GSI (id_token or access_token)."""
    if req.google_access_token:
        return await sign_in_with_google(access_token=req.google_access_token)
    elif req.google_id_token:
        return await sign_in_with_google(id_token=req.google_id_token)
    else:
        raise HTTPException(status_code=400, detail="Provide google_id_token or google_access_token")


@app.get("/auth/google/popup", tags=["Auth"])
async def google_signin_popup(request: Request):
    """
    Redirect the browser (popup window) to Google's OAuth consent screen.
    Uses http://localhost redirect_uri — allowed for Desktop-type OAuth clients.
    """
    from fastapi.responses import RedirectResponse
    from shared.auth import get_google_oauth_url

    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/auth/google/callback"
    auth_url = get_google_oauth_url(redirect_uri)
    return RedirectResponse(url=auth_url)


@app.get("/auth/google/callback", tags=["Auth"])
async def google_signin_callback(request: Request, code: str = "", error: str = ""):
    """
    Google redirects here after the user grants (or denies) consent.
    Exchanges the auth code for a Firebase session, then returns an HTML page
    that postMessages the result back to the opener window and closes itself.
    """
    from fastapi.responses import HTMLResponse
    from shared.auth import sign_in_with_google_code

    if error:
        return _google_callback_html(success=False, error=f"Access denied: {error}")

    if not code:
        return _google_callback_html(success=False, error="No authorization code received")

    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/auth/google/callback"

    try:
        data = await sign_in_with_google_code(code, redirect_uri=redirect_uri)
        return _google_callback_html(success=True, auth_data=data)
    except HTTPException as exc:
        return _google_callback_html(success=False, error=exc.detail)
    except Exception as exc:
        logger.error("Google OAuth callback failed: %s", exc, exc_info=True)
        return _google_callback_html(success=False, error="Sign-in failed. Please try again.")


def _google_callback_html(success: bool, error: str = "", auth_data: dict = None):
    """Return a small HTML page that postMessages the auth result to the opener."""
    from fastapi.responses import HTMLResponse
    if success and auth_data:
        msg = json.dumps({
            "google_auth": "success",
            "uid": auth_data.get("uid", ""),
            "email": auth_data.get("email", ""),
            "display_name": auth_data.get("display_name", ""),
            "id_token": auth_data.get("id_token", ""),
            "refresh_token": auth_data.get("refresh_token", ""),
            "expires_in": auth_data.get("expires_in", ""),
        })
        icon, title, sub = "✓", "Signed in!", "Returning to Hermes…"
    else:
        msg = json.dumps({"google_auth": "error", "error": error})
        icon, title, sub = "✗", "Sign-in failed", error

    html = f"""<!DOCTYPE html>
<html>
<head><title>Google Sign-In</title>
<style>
  body {{font-family:sans-serif;display:flex;align-items:center;justify-content:center;
         height:100vh;margin:0;background:#07060A;color:#EDEAF5;}}
  .card {{text-align:center;padding:40px;}}
  .icon {{font-size:48px;margin-bottom:16px;}}
  .msg  {{font-size:16px;margin-bottom:8px;}}
  .sub  {{font-size:13px;color:#9E9BB0;}}
</style></head>
<body>
<div class="card">
  <img src="/static/logo.png" style="width:48px;height:48px;margin-bottom:24px;object-fit:contain;" alt="Hermes Logo">
  <div class="icon">{icon}</div>
  <div class="msg">{title}</div>
  <div class="sub">{sub}</div>
</div>
<script>
  if (window.opener) {{ window.opener.postMessage({msg}, '*'); }}
  setTimeout(() => window.close(), 1500);
</script>
</body></html>"""
    return HTMLResponse(content=html)


@app.get("/auth/me", tags=["Auth"])
async def auth_me(user: dict = Depends(get_current_user)):
    """Return the current authenticated user's info, including YouTube and Calendar connection status."""
    yt_status  = is_youtube_connected(user["uid"])
    cal_status = is_calendar_connected(user["uid"])
    return {
        "uid": user["uid"],
        "email": user.get("email", ""),
        "name": user.get("name", ""),
        "youtube": yt_status,
        "calendar": cal_status,
    }


# ── YouTube OAuth Endpoints ──────────────────────────────────────────────────

@app.get("/auth/youtube", tags=["YouTube OAuth"])
async def youtube_connect(request: Request, user: dict = Depends(get_current_user)):
    """
    Redirect the user to Google's OAuth consent screen to connect their YouTube channel.
    The frontend should open this URL in a new window/popup.
    """
    if not settings.youtube_client_id or not settings.youtube_client_secret:
        raise HTTPException(status_code=503, detail="YouTube OAuth not configured. Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET.")

    # Build redirect URI from the current request's base URL
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/auth/youtube/callback"

    auth_url = get_youtube_auth_url(user["uid"], redirect_uri)
    return {"auth_url": auth_url}


@app.get("/auth/youtube/callback", tags=["YouTube OAuth"])
async def youtube_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """
    Google redirects here after the user consents (or denies).
    Exchanges the code for tokens and stores them.
    Returns an HTML page that sends a message to the opener window and closes itself.
    """
    # Handle denial
    if error:
        return _youtube_callback_html(success=False, error=f"Authorization denied: {error}")

    if not code or not state:
        return _youtube_callback_html(success=False, error="Missing authorization code or state")

    # Parse state: "user_uid:csrf_token"
    parts = state.split(":", 1)
    if len(parts) != 2:
        return _youtube_callback_html(success=False, error="Invalid state parameter")

    user_uid, csrf_token = parts

    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/auth/youtube/callback"

    try:
        result = await exchange_code_for_tokens(code, redirect_uri, user_uid, csrf_token)
        return _youtube_callback_html(
            success=True,
            channel=result.get("channel_title", ""),
        )
    except ValueError as exc:
        return _youtube_callback_html(success=False, error=str(exc))
    except Exception as exc:
        logger.error("YouTube OAuth callback failed: %s", exc, exc_info=True)
        return _youtube_callback_html(success=False, error="Something went wrong. Please try again.")


def _youtube_callback_html(success: bool, error: str = "", channel: str = ""):
    """Return a small HTML page that communicates the result back to the opener window."""
    from fastapi.responses import HTMLResponse
    status_json = json.dumps({"success": success, "error": error, "channel": channel})
    html = f"""<!DOCTYPE html>
<html><head><title>YouTube Connected</title>
<style>
  body {{ font-family: sans-serif; display: flex; align-items: center; justify-content: center;
         height: 100vh; margin: 0; background: #07060A; color: #EDEAF5; }}
  .card {{ text-align: center; padding: 40px; }}
  .icon {{ font-size: 48px; margin-bottom: 16px; }}
  .msg {{ font-size: 16px; margin-bottom: 8px; }}
  .sub {{ font-size: 13px; color: #9E9BB0; }}
</style></head><body>
<div class="card">
  <img src="/static/logo.png" style="width:48px;height:48px;margin-bottom:24px;object-fit:contain;" alt="Hermes Logo">
  <div class="icon">{"✓" if success else "✗"}</div>
  <div class="msg">{"YouTube connected!" if success else "Connection failed"}</div>
  <div class="sub">{f"Channel: {channel}" if channel else error or ""}</div>
  <div class="sub" style="margin-top:12px;">This window will close automatically…</div>
</div>
<script>
  if (window.opener) {{
    window.opener.postMessage({status_json}, '*');
  }}
  setTimeout(() => window.close(), 2000);
</script>
</body></html>"""
    return HTMLResponse(content=html)


@app.get("/auth/youtube/status", tags=["YouTube OAuth"])
async def youtube_status(user: dict = Depends(get_current_user)):
    """Check if the current user has connected their YouTube account."""
    return is_youtube_connected(user["uid"])


@app.post("/auth/youtube/disconnect", tags=["YouTube OAuth"])
async def youtube_disconnect(user: dict = Depends(get_current_user)):
    """Remove the user's stored YouTube tokens."""
    disconnect_youtube(user["uid"])
    return {"connected": False, "message": "YouTube disconnected"}


# ── Calendar OAuth Endpoints ─────────────────────────────────────────────────

@app.get("/auth/calendar", tags=["Calendar OAuth"])
async def calendar_connect(request: Request, user: dict = Depends(get_current_user)):
    """
    Return the Google OAuth URL to connect the user's Google Calendar.
    Frontend opens this URL in a popup window.
    """
    client_id = settings.calendar_client_id or settings.youtube_client_id
    client_secret = settings.calendar_client_secret or settings.youtube_client_secret
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail="Calendar OAuth not configured. Set CALENDAR_CLIENT_ID and CALENDAR_CLIENT_SECRET.",
        )
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/auth/calendar/callback"
    auth_url = get_calendar_auth_url(user["uid"], redirect_uri)
    return {"auth_url": auth_url}


@app.get("/auth/calendar/callback", tags=["Calendar OAuth"])
async def calendar_callback(
    request: Request, code: str = "", state: str = "", error: str = ""
):
    """
    Google redirects here after the user consents (or denies).
    Exchanges the code for tokens and stores them.
    Returns an HTML page that postMessages the result back to the opener.
    """
    if error:
        return _calendar_callback_html(success=False, error=f"Authorization denied: {error}")

    if not code or not state:
        return _calendar_callback_html(success=False, error="Missing authorization code or state")

    parts = state.split(":", 1)
    if len(parts) != 2:
        return _calendar_callback_html(success=False, error="Invalid state parameter")

    user_uid, csrf_token = parts
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/auth/calendar/callback"

    try:
        result = await exchange_calendar_code_for_tokens(code, redirect_uri, user_uid, csrf_token)
        return _calendar_callback_html(
            success=True,
            calendar_summary=result.get("calendar_summary", ""),
        )
    except ValueError as exc:
        return _calendar_callback_html(success=False, error=str(exc))
    except Exception as exc:
        logger.error("Calendar OAuth callback failed: %s", exc, exc_info=True)
        return _calendar_callback_html(success=False, error="Something went wrong. Please try again.")


def _calendar_callback_html(success: bool, error: str = "", calendar_summary: str = ""):
    """Return a small HTML page that communicates the result back to the opener window."""
    from fastapi.responses import HTMLResponse
    status_json = json.dumps({"success": success, "error": error, "calendar_summary": calendar_summary})
    icon = "✓" if success else "✗"
    msg  = "Calendar connected!" if success else "Connection failed"
    sub  = f"Calendar: {calendar_summary}" if calendar_summary else error or ""
    html = f"""<!DOCTYPE html>
<html><head><title>Calendar Connected</title>
<style>
  body {{ font-family: sans-serif; display: flex; align-items: center; justify-content: center;
         height: 100vh; margin: 0; background: #07060A; color: #EDEAF5; }}
  .card {{ text-align: center; padding: 40px; }}
  .icon {{ font-size: 48px; margin-bottom: 16px; }}
  .msg {{ font-size: 16px; margin-bottom: 8px; }}
  .sub {{ font-size: 13px; color: #9E9BB0; }}
</style></head><body>
<div class="card">
  <img src="/static/logo.png" style="width:48px;height:48px;margin-bottom:24px;object-fit:contain;" alt="Hermes Logo">
  <div class="icon">{icon}</div>
  <div class="msg">{msg}</div>
  <div class="sub">{sub}</div>
  <div class="sub" style="margin-top:12px;">This window will close automatically…</div>
</div>
<script>
  if (window.opener) {{
    window.opener.postMessage({status_json}, '*');
  }}
  setTimeout(() => window.close(), 2000);
</script>
</body></html>"""
    return HTMLResponse(content=html)


@app.get("/auth/calendar/status", tags=["Calendar OAuth"])
async def calendar_status(user: dict = Depends(get_current_user)):
    """Check if the current user has connected their Google Calendar."""
    return is_calendar_connected(user["uid"])


@app.post("/auth/calendar/disconnect", tags=["Calendar OAuth"])
async def calendar_disconnect(user: dict = Depends(get_current_user)):
    """Remove the user's stored Calendar tokens."""
    disconnect_calendar(user["uid"])
    return {"connected": False, "message": "Calendar disconnected"}


# ── Pipeline Endpoints (authenticated) ───────────────────────────────────────


async def _run_pipeline_background(job_id: str, prompt: str, user_id: str, request_data: dict, owner_uid: str):
    """Background task: runs the pipeline and updates job status in DB."""
    try:
        response_text, session_id = await run_agent_with_progress(prompt, user_id, job_id)
        db.save("pipeline_jobs", job_id, {
            "job_id": job_id,
            "session_id": session_id,
            "request": request_data,
            "response": response_text,
            "status": "completed",
            "current_stage": "done",
            "user_id": owner_uid,
            "created_at": (db.get("pipeline_jobs", job_id) or {}).get("created_at", datetime.utcnow().isoformat()),
        })
        _emit_sse_event(job_id, "complete", {"status": "completed", "stage": "done"})
    except Exception as exc:
        logger.error("Pipeline failed for job %s: %s", job_id, exc, exc_info=True)
        job = db.get("pipeline_jobs", job_id) or {}
        job.update({"status": "failed", "error": str(exc)})
        db.save("pipeline_jobs", job_id, job)
        _emit_sse_event(job_id, "error", {"status": "failed", "error": str(exc)})


@app.post("/pipeline", response_model=PipelineResponse, tags=["Pipeline"])
async def run_pipeline(
    request: PipelineRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """
    Run the full content pipeline from a natural-language request.
    Requires authentication.
    """
    job_id = str(uuid.uuid4())
    owner_uid = user["uid"]
    agent_user_id = f"pipeline_{job_id[:8]}"

    # Build coordinator prompt
    now = datetime.utcnow().strftime("%A, %B %d, %Y %H:%M UTC")
    prompt = (
        f"[Current date and time: {now}]\n\n"
        f"Create a complete YouTube Short content pipeline for the following request:\n\n"
        f"Request: {request.request}\n"
        f"Niche: {request.niche}\n"
        f"Creator ID: {request.creator_id}\n"
        f"Deadline: {request.deadline or 'no specific deadline — find the optimal time'}\n\n"
        f"Run the full pipeline: ideas → research → script → production → scheduling.\n"
        f"Job ID for tracking: {job_id}\n"
        f"User ID for YouTube upload: {owner_uid}"
    )

    # Save job immediately so the frontend can start polling
    db.save("pipeline_jobs", job_id, {
        "job_id": job_id,
        "user_id": owner_uid,
        "request": request.model_dump(),
        "status": "running",
        "current_stage": "ideas",
        "created_at": datetime.utcnow().isoformat(),
    })

    # Run pipeline in background so we can return job_id immediately
    background_tasks.add_task(
        _run_pipeline_background, job_id, prompt, agent_user_id, request.model_dump(), owner_uid
    )

    return PipelineResponse(
        job_id=job_id,
        status_url=f"/pipeline/{job_id}",
        coordinator_response="",
    )


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest, user: dict = Depends(get_current_user)):
    """
    Chat directly with the coordinator agent.
    Requires authentication. Maintains conversation state via session_id.
    Messages and responses are persisted in Firestore for chat history.
    """
    owner_uid = user["uid"]
    try:
        response_text, session_id = await run_agent(
            message=request.message,
            user_id=owner_uid,
            session_id=request.session_id,
        )
        ts = datetime.utcnow().isoformat()

        # ── Persist chat history in Firestore ────────────────────────────────
        # 1. Upsert the session metadata (title = first user message, truncated)
        session_doc = db.get("chat_sessions", session_id) or {}
        if not session_doc.get("title"):
            title = request.message[:60] + ("…" if len(request.message) > 60 else "")
        else:
            title = session_doc["title"]

        db.save("chat_sessions", session_id, {
            "session_id": session_id,
            "user_id": owner_uid,
            "title": title,
            "last_message_at": ts,
            "message_count": session_doc.get("message_count", 0) + 1,
        })

        # 2. Save the user message
        msg_id_user = f"{session_id}_u_{str(uuid.uuid4())[:8]}"
        db.save("chat_messages", msg_id_user, {
            "session_id": session_id,
            "user_id": owner_uid,
            "role": "user",
            "content": request.message,
            "created_at": ts,
        })

        # 3. Save the agent reply
        msg_id_agent = f"{session_id}_a_{str(uuid.uuid4())[:8]}"
        db.save("chat_messages", msg_id_agent, {
            "session_id": session_id,
            "user_id": owner_uid,
            "role": "agent",
            "content": response_text,
            "created_at": datetime.utcnow().isoformat(),
        })
        # ─────────────────────────────────────────────────────────────────────

        return ChatResponse(
            session_id=session_id,
            user_id=owner_uid,
            response=response_text,
            model=settings.gemini_model,
            timestamp=ts,
        )
    except Exception as exc:
        logger.error("Chat failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/chat/sessions", tags=["Chat"])
async def list_chat_sessions(limit: int = Query(30, description="Max number of sessions to return"), user: dict = Depends(get_current_user)):
    """List the current user's past chat sessions, newest first."""
    sessions = db.query(
        "chat_sessions",
        filters=[("user_id", "==", user["uid"])],
        order_by="last_message_at",
        limit=limit,
    )
    return {"sessions": sessions or [], "count": len(sessions or [])}


@app.get("/chat/sessions/{session_id}/messages", tags=["Chat"])
async def get_chat_messages(session_id: str, user: dict = Depends(get_current_user)):
    """Load all messages for a specific chat session (ownership enforced)."""
    session = db.get("chat_sessions", session_id)
    if not session or session.get("user_id") != user["uid"]:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = db.query(
        "chat_messages",
        filters=[("session_id", "==", session_id)],
        order_by="created_at",
        limit=500,
    )
    # Sort ascending (oldest first) for rendering
    messages = sorted(messages or [], key=lambda m: m.get("created_at", ""))
    return {"session_id": session_id, "messages": messages}


@app.delete("/chat/sessions/{session_id}", tags=["Chat"])
async def delete_chat_session(session_id: str, user: dict = Depends(get_current_user)):
    """Delete a chat session and all its messages."""
    session = db.get("chat_sessions", session_id)
    if not session or session.get("user_id") != user["uid"]:
        raise HTTPException(status_code=404, detail="Session not found")
    # Delete all messages for this session
    messages = db.query("chat_messages", filters=[("session_id", "==", session_id)], limit=500)
    for msg in (messages or []):
        msg_id = msg.get("_id") or msg.get("session_id", "") + "_" + msg.get("created_at", "")[:10]
        # best-effort delete by querying the known key pattern
    db.delete("chat_sessions", session_id)
    return {"deleted": True, "session_id": session_id}


@app.get("/jobs", tags=["Pipeline"])
async def list_jobs(limit: int = Query(50, description="Max number of jobs to return"), user: dict = Depends(get_current_user)):
    """List pipeline jobs for the authenticated user, newest first."""
    owner_uid = user["uid"]
    jobs = db.query(
        "pipeline_jobs",
        filters=[("user_id", "==", owner_uid)],
        order_by="created_at",
        limit=limit,
    )
    if not jobs:
        jobs = []
    return {"jobs": jobs, "count": len(jobs)}


@app.get("/pipeline/{job_id}", response_model=JobStatusResponse, tags=["Pipeline"])
async def get_pipeline_status(job_id: str, user: dict = Depends(get_current_user)):
    """Check the status of a pipeline job. Only the owner can view it."""
    job = db.get("pipeline_jobs", job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.get("user_id") and job["user_id"] != user["uid"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return JobStatusResponse(
        job_id=job_id,
        status=job.get("status", "unknown"),
        data=job,
    )


@app.get("/pipeline/{job_id}/stream", tags=["Pipeline"])
async def stream_pipeline_progress(job_id: str, token: str = Query("", description="Firebase ID token (required — EventSource doesn't support headers)")):
    """
    SSE endpoint for real-time pipeline stage updates.
    Auth via query param since EventSource doesn't support headers.
    """
    # Authenticate via query param token
    if not token:
        raise HTTPException(status_code=401, detail="Missing auth token")
    from shared.auth import verify_token
    user = verify_token(token)

    job = db.get("pipeline_jobs", job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.get("user_id") and job["user_id"] != user["uid"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # If already done, send a single terminal event and close
    if job.get("status") in ("completed", "failed"):
        event_type = "complete" if job["status"] == "completed" else "error"
        data = {"status": job["status"], "stage": job.get("current_stage", "done")}

        async def _done_stream():
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

        return StreamingResponse(_done_stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })

    # Register a queue for this subscriber
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sse_subscribers.setdefault(job_id, []).append(queue)

    async def _event_generator() -> AsyncGenerator[str, None]:
        # Send current stage immediately on connect
        current = db.get("pipeline_jobs", job_id) or {}
        yield f"event: stage_update\ndata: {json.dumps({'stage': current.get('current_stage', 'ideas'), 'status': 'running'})}\n\n"

        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: {payload['event']}\ndata: {json.dumps(payload['data'])}\n\n"
                    # Close stream on terminal events
                    if payload["event"] in ("complete", "error"):
                        break
                except asyncio.TimeoutError:
                    # Heartbeat to keep connection alive
                    yield ": heartbeat\n\n"
        finally:
            # Clean up subscriber
            subs = _sse_subscribers.get(job_id, [])
            if queue in subs:
                subs.remove(queue)
            if not subs:
                _sse_subscribers.pop(job_id, None)

    return StreamingResponse(_event_generator(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.get("/pipeline/{job_id}/twitter", tags=["Pipeline"])
async def get_twitter_content(job_id: str, user: dict = Depends(get_current_user)):
    """Retrieve Twitter/X content generated for a pipeline job."""
    job = db.get("pipeline_jobs", job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.get("user_id") and job["user_id"] != user["uid"]:
        raise HTTPException(status_code=403, detail="Access denied")

    twitter_data = db.query(
        "twitter_content",
        filters=[("pipeline_job_id", "==", job_id)],
        order_by="created_at",
        limit=50,
    )
    if not twitter_data:
        raise HTTPException(status_code=404, detail="No Twitter content found for this job yet.")
    return {"job_id": job_id, "twitter_content": twitter_data[0]}


@app.get("/pipeline/{job_id}/script", tags=["Pipeline"])
async def get_script_content(job_id: str, user: dict = Depends(get_current_user)):
    """Retrieve the generated script for a pipeline job."""
    job = db.get("pipeline_jobs", job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.get("user_id") and job["user_id"] != user["uid"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Query scripts linked to this specific pipeline job
    scripts = db.query(
        "scripts",
        filters=[("pipeline_job_id", "==", job_id)],
        order_by="_saved_at",
        limit=10,
    )
    if not scripts:
        raise HTTPException(status_code=404, detail="No script found for this job yet.")
    script = scripts[0]
    return {
        "job_id": job_id,
        "script_id": script.get("id", ""),
        "script_text": script.get("script_text", ""),
        "hook": script.get("hook", ""),
        "cta": script.get("cta", ""),
        "youtube_title": script.get("youtube_title", ""),
        "youtube_description": script.get("youtube_description", ""),
        "youtube_tags": script.get("youtube_tags", []),
        "word_count": script.get("word_count", 0),
        "estimated_duration_s": script.get("estimated_duration_s", 0),
        "topic_title": script.get("topic_title", ""),
        "niche": script.get("niche", ""),
    }


def _find_video_for_job(job_id: str, owner_uid: str, job: dict) -> dict | None:
    """
    Shared helper: find the video document linked to a pipeline job.

    Lookup order:
      1. Direct query by pipeline_job_id (works even if user_id was never set)
      2. Query by user_id and match pipeline_job_id
      3. Parse UUIDs from coordinator response and look up directly
      4. Most recent video owned by this user
    """
    import re as _re

    # 1. Direct query by pipeline_job_id — most reliable
    by_pipeline = db.query("videos", filters=[("pipeline_job_id", "==", job_id)], limit=10)
    if by_pipeline:
        for v in reversed(by_pipeline):
            return v

    # 2. Query by user_id (for videos that have user_id set)
    videos = db.query("videos", filters=[("user_id", "==", owner_uid)], order_by="updated_at", limit=200)
    if videos:
        for v in reversed(videos):
            if v.get("pipeline_job_id") == job_id:
                return v

    # 3. Parse UUIDs from coordinator response and look up directly
    response_text = job.get("response", "")
    uuid_pattern = _re.compile(
        r'\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b',
        _re.IGNORECASE,
    )
    for candidate_id in uuid_pattern.findall(response_text):
        if candidate_id == job_id:
            continue
        v = db.get("videos", candidate_id)
        if v:
            return v

    # 4. Last resort: most recent video owned by this user
    if videos:
        for v in reversed(videos):
            if v.get("user_id", owner_uid) == owner_uid:
                return v

    return None


@app.get("/pipeline/{job_id}/download", tags=["Pipeline"])
async def download_video(job_id: str, user: dict = Depends(get_current_user)):
    """
    Generate a signed GCS URL for the final video so users can download it.
    Returns a temporary HTTPS URL valid for 60 minutes.
    """
    from shared.storage import get_signed_url, gcs_object_exists

    job = db.get("pipeline_jobs", job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.get("user_id") and job["user_id"] != user["uid"]:
        raise HTTPException(status_code=403, detail="Access denied")

    def _valid_gcs(uri: str) -> bool:
        """Return True only if the URI is a gs:// path AND the object actually exists."""
        return str(uri).startswith("gs://") and gcs_object_exists(uri)

    video_gcs_uri = None
    owner_uid = user["uid"]

    video_doc = _find_video_for_job(job_id, owner_uid, job)
    if video_doc:
        candidate = video_doc.get("video_path", "")
        if _valid_gcs(candidate):
            video_gcs_uri = candidate

    if not video_gcs_uri:
        raise HTTPException(
            status_code=404,
            detail=(
                "No video file found for this job. "
                "The video may still be processing, the GCS object may have been deleted, "
                "or GCS is not configured."
            ),
        )

    signed_url = get_signed_url(video_gcs_uri, expiry_minutes=60)
    if not signed_url:
        raise HTTPException(
            status_code=503,
            detail="Could not generate download URL. Ensure the service account has Storage Object Viewer permissions.",
        )

    return {"download_url": signed_url, "expires_in_minutes": 60, "gcs_uri": video_gcs_uri}

@app.get("/pipeline/{job_id}/download-thumbnail", tags=["Pipeline"])
async def download_thumbnail(job_id: str, user: dict = Depends(get_current_user)):
    """
    Generate a signed GCS URL for the thumbnail image so users can download it.
    Returns a temporary HTTPS URL valid for 60 minutes.
    """
    from shared.storage import get_signed_url, gcs_object_exists

    job = db.get("pipeline_jobs", job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.get("user_id") and job["user_id"] != user["uid"]:
        raise HTTPException(status_code=403, detail="Access denied")

    def _valid_gcs(uri: str) -> bool:
        return str(uri).startswith("gs://") and gcs_object_exists(uri)

    thumbnail_gcs_uri = None
    owner_uid = user["uid"]

    video_doc = _find_video_for_job(job_id, owner_uid, job)
    if video_doc:
        candidate = video_doc.get("thumbnail_gcs_uri", "")
        if _valid_gcs(candidate):
            thumbnail_gcs_uri = candidate

    if not thumbnail_gcs_uri:
        raise HTTPException(
            status_code=404,
            detail=(
                "No thumbnail found for this job. "
                "The thumbnail may not have been generated yet, or GCS is not configured."
            ),
        )

    signed_url = get_signed_url(thumbnail_gcs_uri, expiry_minutes=60)
    if not signed_url:
        raise HTTPException(
            status_code=503,
            detail="Could not generate download URL. Ensure the service account has Storage Object Viewer permissions.",
        )

    return {"download_url": signed_url, "expires_in_minutes": 60, "gcs_uri": thumbnail_gcs_uri}


@app.get("/pipeline/{job_id}/thumbnail", tags=["Pipeline"])
async def get_thumbnail_url(job_id: str, user: dict = Depends(get_current_user)):
    """
    Return a short-lived signed URL for displaying the thumbnail in the browser.
    Intended for <img> src usage (5 min expiry).
    """
    from shared.storage import get_signed_url, gcs_object_exists

    job = db.get("pipeline_jobs", job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.get("user_id") and job["user_id"] != user["uid"]:
        raise HTTPException(status_code=403, detail="Access denied")

    def _valid_gcs(uri: str) -> bool:
        return str(uri).startswith("gs://") and gcs_object_exists(uri)

    thumbnail_gcs_uri = None
    owner_uid = user["uid"]

    video_doc = _find_video_for_job(job_id, owner_uid, job)
    if video_doc:
        candidate = video_doc.get("thumbnail_gcs_uri", "")
        if _valid_gcs(candidate):
            thumbnail_gcs_uri = candidate

    if not thumbnail_gcs_uri:
        raise HTTPException(status_code=404, detail="No thumbnail found for this job.")

    signed_url = get_signed_url(thumbnail_gcs_uri, expiry_minutes=5)
    if not signed_url:
        raise HTTPException(status_code=503, detail="Could not generate thumbnail URL.")

    return {"url": signed_url, "gcs_uri": thumbnail_gcs_uri}




# ── Creator Profile Endpoints ─────────────────────────────────────────────────


class CreatorProfileRequest(BaseModel):
    creator_id: str = Field(..., description="Unique identifier for this creator profile")
    tone: str = Field("", description="Script tone (e.g. 'casual', 'authoritative', 'humorous')")
    pacing: str = Field("", description="Script pacing (e.g. 'fast', 'moderate', 'slow')")
    hook_style: str = Field("", description="Preferred hook style (e.g. 'stat-bomb', 'curiosity-gap', 'story')")
    cta: str = Field("Follow for more", description="Default call-to-action line")


@app.get("/creator-profiles", tags=["Creator Profiles"])
async def list_creator_profiles(user: dict = Depends(get_current_user)):
    """List all creator profiles for the authenticated user."""
    owner_uid = user["uid"]
    profiles = db.query("creator_profiles", filters=[("owner_uid", "==", owner_uid)], limit=50)
    return {"profiles": profiles}


@app.get("/creator-profiles/{creator_id}", tags=["Creator Profiles"])
async def get_creator_profile(creator_id: str, user: dict = Depends(get_current_user)):
    """Get a single creator profile."""
    profile = db.get("creator_profiles", creator_id)
    if not profile or profile.get("owner_uid") != user["uid"]:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@app.post("/creator-profiles", tags=["Creator Profiles"])
async def save_creator_profile(req: CreatorProfileRequest, user: dict = Depends(get_current_user)):
    """Create or update a creator profile."""
    owner_uid = user["uid"]
    data = {
        "creator_id": req.creator_id,
        "owner_uid": owner_uid,
        "tone": req.tone,
        "pacing": req.pacing,
        "hook_style": req.hook_style,
        "cta": req.cta,
    }
    db.save("creator_profiles", req.creator_id, data)
    return {"saved": True, "creator_id": req.creator_id}


@app.delete("/creator-profiles/{creator_id}", tags=["Creator Profiles"])
async def delete_creator_profile(creator_id: str, user: dict = Depends(get_current_user)):
    """Delete a creator profile."""
    profile = db.get("creator_profiles", creator_id)
    if not profile or profile.get("owner_uid") != user["uid"]:
        raise HTTPException(status_code=404, detail="Profile not found")
    db.delete("creator_profiles", creator_id)
    return {"deleted": True, "creator_id": creator_id}


# ── Video Retry Upload Endpoint ───────────────────────────────────────────────


class RetryUploadRequest(BaseModel):
    niche: str = Field("general", description="Content niche for scheduling (e.g. 'tech', 'fitness', 'cooking')")
    deadline: Optional[str] = Field(None, description="Optional deadline for scheduling (ISO date or natural language)")


async def _retry_upload_background(video_job_id: str, owner_uid: str, niche: str, deadline: Optional[str]):
    """Background task: retry YouTube upload + scheduling for an already-assembled video."""
    from agents.production.tools import upload_to_youtube, save_video_job
    from agents.scheduler.tools import find_optimal_post_time, create_calendar_event, save_schedule
    from shared.storage import download_file

    video_doc = db.get("videos", video_job_id)
    if not video_doc:
        logger.error("retry_upload: video job %s not found", video_job_id)
        return

    video_gcs_uri = video_doc.get("video_path")
    if not video_gcs_uri:
        logger.error("retry_upload: no video_path on job %s", video_job_id)
        db.save("videos", video_job_id, {**video_doc, "error": "No assembled video found to retry upload.", "status": "failed"})
        return

    # Pull metadata stored on the video doc (set by save_video_job during production)
    script_id = video_doc.get("script_id", "")
    script_doc = db.get("scripts", script_id) if script_id else None
    youtube_title = (script_doc or {}).get("youtube_title", "Untitled Video")
    youtube_description = (script_doc or {}).get("youtube_description", "")
    youtube_tags = (script_doc or {}).get("youtube_tags", [])

    logger.info("retry_upload: downloading %s for job %s", video_gcs_uri, video_job_id)
    db.save("videos", video_job_id, {**video_doc, "status": "processing", "current_stage": "upload", "error": None})

    # Download the GCS video to a local temp path
    try:
        local_video = str(_WORK_DIR / f"retry_{video_job_id}.mp4")
        download_file(video_gcs_uri, local_video)
    except Exception as exc:
        logger.error("retry_upload: download failed: %s", exc)
        db.save("videos", video_job_id, {**video_doc, "status": "failed", "error": f"Download failed: {exc}"})
        return

    # ── Upload ────────────────────────────────────────────────────────────────
    upload_result = upload_to_youtube(
        video_path=local_video,
        title=youtube_title,
        description=youtube_description,
        tags=youtube_tags,
        privacy="private",
        job_id=video_job_id,
        user_id=owner_uid,
    )

    if upload_result.get("error"):
        logger.error("retry_upload: upload failed: %s", upload_result["error"])
        db.save("videos", video_job_id, {
            **video_doc,
            "status": "failed",
            "current_stage": "upload",
            "error": upload_result["error"],
        })
        return

    youtube_video_id = upload_result["youtube_video_id"]
    youtube_url = upload_result["youtube_url"]
    logger.info("retry_upload: uploaded → %s", youtube_url)

    # Persist the YouTube ID immediately
    save_video_job(
        script_id=script_id,
        status="done",
        video_job_id=video_job_id,
        youtube_video_id=youtube_video_id,
        youtube_url=youtube_url,
        current_stage="done",
        video_gcs_uri=video_gcs_uri,
        voiceover_gcs_uri=video_doc.get("voiceover_path"),
        image_gcs_uris=video_doc.get("image_paths", []),
        thumbnail_gcs_uri=video_doc.get("thumbnail_gcs_uri"),
        user_id=owner_uid,
    )

    # ── Schedule ──────────────────────────────────────────────────────────────
    try:
        timing = find_optimal_post_time(niche=niche, deadline=deadline)
        publish_at = timing["publish_at"]

        create_calendar_event(
            title=f"Publish: {youtube_title}",
            publish_at=publish_at,
            description=f"YouTube Short: {youtube_url}\nNiche: {niche}",
            video_id=video_job_id,
            user_uid=owner_uid,
        )

        save_schedule(
            video_id=video_job_id,
            publish_at=publish_at,
            calendar_event_id=None,
            calendar_event_url=None,
        )
        logger.info("retry_upload: scheduled for %s", publish_at)
    except Exception as exc:
        # Scheduling failure is non-fatal — upload already succeeded
        logger.warning("retry_upload: scheduling failed (non-fatal): %s", exc)


@app.post("/videos/{video_job_id}/retry-upload", tags=["Videos"])
async def retry_upload(
    video_job_id: str,
    request: RetryUploadRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """
    Retry the YouTube upload (and scheduling) for an already-assembled video.
    Use this when the upload step failed but the video file is already in GCS.
    """
    owner_uid = user["uid"]

    video_doc = db.get("videos", video_job_id)
    if not video_doc:
        raise HTTPException(status_code=404, detail=f"Video job {video_job_id} not found")

    if not video_doc.get("video_path"):
        raise HTTPException(
            status_code=400,
            detail="No assembled video found for this job. The assembly step may not have completed.",
        )

    if video_doc.get("youtube_video_id"):
        return {
            "message": "Video already uploaded.",
            "youtube_video_id": video_doc["youtube_video_id"],
            "youtube_url": video_doc.get("youtube_url"),
        }

    # Mark as retrying so the caller knows it's in progress
    db.save("videos", video_job_id, {**video_doc, "status": "processing", "current_stage": "upload", "error": None})

    background_tasks.add_task(
        _retry_upload_background, video_job_id, owner_uid, request.niche, request.deadline
    )

    return {
        "message": "Upload retry started.",
        "video_job_id": video_job_id,
        "status_url": f"/videos/{video_job_id}",
    }


@app.get("/videos/{video_job_id}", tags=["Videos"])
async def get_video_job(video_job_id: str, user: dict = Depends(get_current_user)):
    """Get the current status of a video job (upload status, YouTube URL, etc.)."""
    video_doc = db.get("videos", video_job_id)
    if not video_doc:
        raise HTTPException(status_code=404, detail=f"Video job {video_job_id} not found")
    return video_doc


# ── Analytics Endpoint ────────────────────────────────────────────────────────


@app.post("/pipeline/{job_id}/analytics", tags=["Analytics"])
async def trigger_analytics(job_id: str, youtube_video_id: str = Query(..., description="YouTube video ID (e.g. 'dQw4w9WgXcQ')"), niche: str = Query("tech", description="Content niche for topic score updates"), user: dict = Depends(get_current_user)):
    """Trigger the Analytics Agent for a specific video. Requires authentication."""
    try:
        owner_uid = user["uid"]
        prompt = (
            f"Run analytics for this video:\n"
            f"- Internal video_id: {job_id}\n"
            f"- YouTube video ID: {youtube_video_id}\n"
            f"- Niche: {niche}\n"
            f"- User ID (user_uid): {owner_uid}\n\n"
            f"Fetch metrics, save analytics, and update topic scores.\n"
            f"IMPORTANT: Pass user_uid={owner_uid} to all tool calls."
        )
        response_text, _ = await run_agent(prompt, user_id=f"analytics_{job_id[:8]}")
        return {"job_id": job_id, "youtube_video_id": youtube_video_id, "result": response_text}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/analytics", tags=["Analytics"])
async def list_analytics(limit: int = Query(50, description="Max number of videos to return"), user: dict = Depends(get_current_user)):
    """List analytics for the authenticated user's videos, newest first."""
    owner_uid = user["uid"]

    # Get the user's completed videos
    videos = db.query(
        "videos",
        filters=[("status", "==", "done")],
        order_by="created_at",
        limit=limit,
    )
    # Filter to user's videos (those linked to their pipeline jobs)
    user_jobs = db.query(
        "pipeline_jobs",
        filters=[("user_id", "==", owner_uid)],
        limit=200,
    )
    user_job_ids = {j.get("job_id") for j in user_jobs}

    results = []
    for video in videos:
        vid = video.get("id", "")
        pipeline_jid = video.get("pipeline_job_id", "")
        # Check ownership: either user_id matches or pipeline_job_id is one of the user's jobs
        if video.get("user_id") != owner_uid and pipeline_jid not in user_job_ids:
            continue

        analytics_doc = db.get("analytics", vid)

        # Find the topic title from the script chain
        topic_title = ""
        script_id = video.get("script_id", "")
        if script_id:
            script_doc = db.get("scripts", script_id)
            if script_doc:
                topic_title = script_doc.get("topic_title", "")
                if not topic_title:
                    brief_id = script_doc.get("brief_id", "")
                    if brief_id:
                        brief_doc = db.get("research_briefs", brief_id)
                        if brief_doc:
                            topic_title = brief_doc.get("topic_title", "")

        # Find the pipeline job to get the request text as fallback title
        if not topic_title and pipeline_jid:
            pj = db.get("pipeline_jobs", pipeline_jid)
            if pj:
                req = pj.get("request", {})
                topic_title = req.get("request", "") if isinstance(req, dict) else str(req)

        entry = {
            "video_id": vid,
            "youtube_video_id": video.get("youtube_video_id", ""),
            "youtube_url": video.get("youtube_url", ""),
            "topic_title": topic_title or "Untitled",
            "niche": video.get("niche", ""),
            "created_at": video.get("created_at", ""),
            "has_analytics": analytics_doc is not None,
        }
        if analytics_doc:
            entry.update({
                "views": analytics_doc.get("views", 0),
                "watch_time_minutes": analytics_doc.get("watch_time_minutes", 0),
                "avg_view_percentage": analytics_doc.get("avg_view_percentage", 0),
                "likes": analytics_doc.get("likes", 0),
                "comments": analytics_doc.get("comments", 0),
                "impressions": analytics_doc.get("impressions", 0),
                "ctr": analytics_doc.get("ctr", 0),
                "fetched_at": analytics_doc.get("fetched_at", ""),
            })
        results.append(entry)

    return {"analytics": results, "count": len(results)}


@app.get("/analytics/{video_id}", tags=["Analytics"])
async def get_video_analytics(video_id: str, user: dict = Depends(get_current_user)):
    """Get analytics for a specific video. Requires authentication."""
    analytics_doc = db.get("analytics", video_id)
    if not analytics_doc:
        raise HTTPException(status_code=404, detail="No analytics found for this video")
    return analytics_doc


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        reload=os.getenv("ENV", "production") == "development",
        log_level=settings.log_level.lower(),
    )
