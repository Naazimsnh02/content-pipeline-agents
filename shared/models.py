"""
Shared Pydantic data models used across all agents.
These mirror the Firestore document schema.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field
import uuid


def new_id() -> str:
    return str(uuid.uuid4())


def now_utc():
    return datetime.now(timezone.utc)


# ── Topic (Ideas Agent output) ──────────────────────────────────────────────

class Topic(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str
    niche: str
    source: str                         # "hackernews" | "google_trends" | "manual" | ...
    score: float = 0.0                  # trending score from source
    novelty_score: float = 1.0          # 1.0 = never covered, 0.0 = covered recently
    url: Optional[str] = None
    used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=now_utc)


# ── Research Brief (Research Agent output) ──────────────────────────────────

class ResearchBrief(BaseModel):
    id: str = Field(default_factory=new_id)
    topic_id: str
    topic_title: str
    summary: str                         # 3–5 sentence executive summary
    key_facts: list[str] = []            # bullet-point facts with stats
    quotes: list[str] = []               # quotable lines
    sources: list[str] = []              # URLs / publication names
    raw_snippets: list[str] = []         # raw search result snippets
    created_at: datetime = Field(default_factory=now_utc)


# ── Script (Script Agent output) ────────────────────────────────────────────

class Script(BaseModel):
    id: str = Field(default_factory=new_id)
    brief_id: str
    topic_title: str
    niche: str
    platform: str = "youtube_shorts"
    script_text: str
    hook: str = ""                       # Opening 3 seconds
    cta: str = ""                        # Call to action
    youtube_title: str = ""
    youtube_description: str = ""
    youtube_tags: list[str] = []
    word_count: int = 0
    estimated_duration_s: int = 60
    creator_id: str = "default"
    pipeline_job_id: Optional[str] = None   # links back to pipeline_jobs collection
    created_at: datetime = Field(default_factory=now_utc)


# ── Video Job (Production Agent) ─────────────────────────────────────────────

class VideoStatus(str):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class VideoJob(BaseModel):
    id: str = Field(default_factory=new_id)
    script_id: str
    pipeline_job_id: Optional[str] = None   # links back to pipeline_jobs collection
    user_id: Optional[str] = None           # Firebase UID — needed for ownership queries
    status: str = "pending"
    progress: int = 0                    # 0–100
    current_stage: str = ""              # "tts" | "images" | "assembly" | "upload"
    voiceover_path: Optional[str] = None
    image_paths: list[str] = []
    video_path: Optional[str] = None     # GCS URI
    thumbnail_gcs_uri: Optional[str] = None  # GCS URI for the thumbnail PNG
    youtube_video_id: Optional[str] = None
    youtube_url: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


# ── Schedule (Scheduler Agent output) ───────────────────────────────────────

class Schedule(BaseModel):
    id: str = Field(default_factory=new_id)
    video_id: str
    publish_at: datetime
    platform: str = "youtube"
    calendar_event_id: Optional[str] = None
    calendar_event_url: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)


# ── Analytics (Analytics Agent output) ──────────────────────────────────────

class VideoAnalytics(BaseModel):
    video_id: str
    youtube_video_id: str
    views: int = 0
    watch_time_minutes: float = 0.0
    avg_view_percentage: float = 0.0
    likes: int = 0
    comments: int = 0
    impressions: int = 0
    ctr: float = 0.0                     # click-through rate
    fetched_at: datetime = Field(default_factory=now_utc)


# ── Creator Profile (stored in Firestore) ───────────────────────────────────

class CreatorProfile(BaseModel):
    id: str = "default"
    niche: str = "tech"
    tone: str = "conversational, energetic, direct"
    pacing: str = "fast"
    hook_style: str = "question or shocking fact"
    cta: str = "Follow for more tech insights"
    voice_id: str = "en-US-AriaNeural"   # Edge TTS voice
    caption_color: str = "#00FF88"
    music_mood: str = "upbeat electronic"
    youtube_channel_id: Optional[str] = None


# ── Pipeline Request (API input) ─────────────────────────────────────────────

class PipelineRequest(BaseModel):
    """Submit a content pipeline job."""
    request: str = Field(..., description="Natural language description of the video you want (e.g. 'Create a Short about GPT-5 rumors')")
    niche: str = Field("tech", description="Content niche — controls tone, visuals, music, and posting time. One of: tech, fitness, cooking, crypto, etc.")
    creator_id: str = Field("default", description="Creator profile ID to use for tone/pacing/CTA preferences")
    deadline: Optional[str] = Field(None, description="When to publish — ISO date string or natural language (e.g. 'Tuesday 9am')")

class PipelineResponse(BaseModel):
    """Pipeline job created — track progress via status_url or SSE stream."""
    job_id: str = Field(..., description="Unique pipeline job ID")
    topic: Optional[str] = Field(None, description="Selected topic title (populated after ideas stage)")
    script_id: Optional[str] = Field(None, description="Generated script ID (populated after script stage)")
    video_job_id: Optional[str] = Field(None, description="Video production job ID (populated after production stage)")
    publish_at: Optional[str] = Field(None, description="Scheduled publish time ISO string")
    calendar_event_url: Optional[str] = Field(None, description="Google Calendar event URL")
    status_url: str = Field(..., description="URL to poll for job status: GET /pipeline/{job_id}")
    coordinator_response: str = Field(..., description="Coordinator agent's initial response text")


# ── Twitter/X Content (Script Agent output) ─────────────────────────────────

class TwitterContent(BaseModel):
    id: str = Field(default_factory=new_id)
    script_id: str
    pipeline_job_id: Optional[str] = None   # links back to pipeline_jobs collection
    thread_tweets: list[str] = []        # 3-5 tweets, each ≤280 chars
    long_post: str = ""                  # 500-1000 chars for X long-form
    hashtags: list[str] = []
    created_at: datetime = Field(default_factory=now_utc)
