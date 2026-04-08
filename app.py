"""
FastAPI application entry point.
Serves the ADK coordinator agent and exposes:
  POST /pipeline          — full natural-language pipeline request
  POST /chat              — direct chat with coordinator
  GET  /pipeline/{job_id} — check job status
  GET  /health            — health check
  GET  /                  — API info

For local dev with ADK web UI:
  adk web agents/coordinator
"""
from __future__ import annotations
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator, Optional

import uvicorn
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from shared.config import settings
from shared.database import db
from shared.models import PipelineRequest, PipelineResponse

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── ADK Setup ────────────────────────────────────────────────────────────────
# Set Google API key for Gemini
if settings.google_api_key:
    os.environ["GOOGLE_API_KEY"] = settings.google_api_key

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
    get_runner()  # Pre-warm the runner
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="YouTube Content Pipeline — Multi-Agent System",
    description=(
        "Autonomous content pipeline powered by Google ADK + Gemini 3 Flash. "
        "Converts a natural-language request into a researched, scripted, produced, "
        "and scheduled YouTube Short."
    ),
    version="1.0.0",
    lifespan=lifespan,
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

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    user_id: str = "anonymous"


class ChatResponse(BaseModel):
    session_id: str
    user_id: str
    response: str
    model: str
    timestamp: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    data: Optional[dict] = None


# ── Core ADK runner helper ────────────────────────────────────────────────────

async def run_agent(message: str, user_id: str, session_id: Optional[str] = None) -> tuple[str, str]:
    """Run the coordinator agent and return (response_text, session_id)."""
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
        for key, stage in _AGENT_STAGE_MAP.items():
            if key in author.lower() and stage != current_stage:
                current_stage = stage
                try:
                    job = db.get("pipeline_jobs", job_id) or {}
                    job["current_stage"] = current_stage
                    db.save("pipeline_jobs", job_id, job)
                except Exception:
                    pass
                break

        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    final_text += part.text

    return final_text or "No response generated.", session.id


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/")
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


@app.get("/app")
async def serve_app():
    """Serve the dashboard application."""
    app_html = _os.path.join(_static_dir, "app.html")
    if _os.path.isfile(app_html):
        return FileResponse(app_html, media_type="text/html")
    raise HTTPException(status_code=404, detail="App not found")


@app.get("/health")
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


async def _run_pipeline_background(job_id: str, prompt: str, user_id: str, request_data: dict):
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
            "created_at": (db.get("pipeline_jobs", job_id) or {}).get("created_at", datetime.utcnow().isoformat()),
        })
    except Exception as exc:
        logger.error("Pipeline failed for job %s: %s", job_id, exc, exc_info=True)
        job = db.get("pipeline_jobs", job_id) or {}
        job.update({"status": "failed", "error": str(exc)})
        db.save("pipeline_jobs", job_id, job)


@app.post("/pipeline", response_model=PipelineResponse)
async def run_pipeline(request: PipelineRequest, background_tasks: BackgroundTasks):
    """
    Run the full content pipeline from a natural-language request.

    Example:
        {"request": "Create a YouTube Short about AI trends for next Tuesday", "niche": "tech"}
    """
    job_id = str(uuid.uuid4())
    user_id = f"pipeline_{job_id[:8]}"

    # Build coordinator prompt
    prompt = (
        f"Create a complete YouTube Short content pipeline for the following request:\n\n"
        f"Request: {request.request}\n"
        f"Niche: {request.niche}\n"
        f"Creator ID: {request.creator_id}\n"
        f"Deadline: {request.deadline or 'no specific deadline — find the optimal time'}\n\n"
        f"Run the full pipeline: ideas → research → script → production → scheduling.\n"
        f"Job ID for tracking: {job_id}"
    )

    # Save job immediately so the frontend can start polling
    db.save("pipeline_jobs", job_id, {
        "job_id": job_id,
        "request": request.model_dump(),
        "status": "running",
        "current_stage": "ideas",
        "created_at": datetime.utcnow().isoformat(),
    })

    # Run pipeline in background so we can return job_id immediately
    background_tasks.add_task(
        _run_pipeline_background, job_id, prompt, user_id, request.model_dump()
    )

    return PipelineResponse(
        job_id=job_id,
        status_url=f"/pipeline/{job_id}",
        coordinator_response="",
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Chat directly with the coordinator agent.
    Maintains conversation state via session_id.

    Examples:
        {"message": "What trending tech topics should I cover this week?"}
        {"message": "Research OpenAI's latest model announcement"}
        {"message": "Run analytics on video job abc-123", "session_id": "previous-session-id"}
    """
    try:
        response_text, session_id = await run_agent(
            message=request.message,
            user_id=request.user_id,
            session_id=request.session_id,
        )
        return ChatResponse(
            session_id=session_id,
            user_id=request.user_id,
            response=response_text,
            model=settings.gemini_model,
            timestamp=datetime.utcnow().isoformat(),
        )
    except Exception as exc:
        logger.error("Chat failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/pipeline/{job_id}", response_model=JobStatusResponse)
async def get_pipeline_status(job_id: str):
    """Check the status of a pipeline job."""
    job = db.get("pipeline_jobs", job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobStatusResponse(
        job_id=job_id,
        status=job.get("status", "unknown"),
        data=job,
    )


@app.post("/pipeline/{job_id}/analytics")
async def trigger_analytics(job_id: str, youtube_video_id: str, niche: str = "tech"):
    """
    Trigger the Analytics Agent for a specific video.
    Typically called 48h post-publish by Cloud Scheduler.
    """
    try:
        prompt = (
            f"Run analytics for this video:\n"
            f"- Internal video_id: {job_id}\n"
            f"- YouTube video ID: {youtube_video_id}\n"
            f"- Niche: {niche}\n\n"
            f"Fetch metrics, save analytics, and update topic scores."
        )
        response_text, _ = await run_agent(prompt, user_id=f"analytics_{job_id[:8]}")
        return {"job_id": job_id, "youtube_video_id": youtube_video_id, "result": response_text}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port,
        reload=os.getenv("ENV", "production") == "development",
        log_level=settings.log_level.lower(),
    )
