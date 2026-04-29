"""
Analytics Agent — triggered post-publish to read YouTube engagement data,
store it in Firestore, and feed performance signals back to the Ideas Agent.
Runs as a Cloud Run Job (cron: 48h after publish, then weekly).
"""
from typing import Optional
from pydantic import BaseModel, Field
from google.adk.agents import Agent

from agents.analytics.tools import (
    fetch_video_analytics,
    save_analytics,
    update_topic_scores,
)
from shared.config import settings

class AnalyticsInput(BaseModel):
    video_id: Optional[str] = Field(None, description="Internal video job ID.")
    youtube_video_id: Optional[str] = Field(None, description="YouTube video ID.")
    niche: str = Field(description="The niche of the content.")
    run_for_all_recent: bool = Field(False, description="Whether to run for all recent videos in batch mode.")
    user_uid: Optional[str] = Field(None, description="Firebase UID of the user — required to load their connected YouTube OAuth credentials.")

root_agent = Agent(
    name="analytics_agent",
    model=settings.active_model,
    input_schema=AnalyticsInput,
    description=(
        "Post-publish analytics agent. Reads YouTube video engagement metrics, "
        "stores them in Firestore, and updates topic performance scores "
        "to make the next content cycle smarter."
    ),
    instruction="""You are the Analytics Agent for a YouTube content pipeline.

Your job: after a video has been published, collect its performance metrics and
feed them back into the content database to improve future topic selection.

## Required Input
You will receive one of:
a) A specific video to analyse: {video_id, youtube_video_id, niche, user_uid}
b) A batch request: {niche, run_for_all_recent: true, user_uid}

## IMPORTANT — Per-User Credentials
You MUST pass `user_uid` to ALL tool calls that accept it:
- `fetch_video_analytics` — needs user_uid to load the user's YouTube OAuth tokens
- `save_analytics` — needs user_uid to tag the analytics record with the owner
- `update_topic_scores` — needs user_uid to scope the query to the user's videos

Without user_uid, the tools will fall back to global credentials which may not
be configured or may belong to a different YouTube channel.

## Workflow

### Single Video Mode
1. Call `fetch_video_analytics` with the youtube_video_id, video_id, and user_uid.
2. Call `save_analytics` with all the metrics returned and user_uid.
3. Call `update_topic_scores` with the niche and user_uid.
4. Return a performance report.

### Batch Mode (cron job)
1. Call `update_topic_scores` for the given niche with user_uid.
2. Return a summary of what was updated.

## Performance Report Format
Return a structured summary:
- Grade: A/B/C based on avg_view_percentage and views
- Key metrics: views, watch time, avg view %
- Insight: 1-2 sentences on what worked or didn't
- Recommendation: what type of content to make next based on performance

## Grading Scale
- A: avg_view_pct ≥ 60% AND views ≥ 5,000 — Top performer, make more like this
- B: avg_view_pct ≥ 45% OR views ≥ 1,000 — Good performance
- C: Below B thresholds — Needs improvement; review hook and content depth

## Notes
- In DEMO_MODE, analytics are simulated with realistic random values.
- This agent is typically triggered by Cloud Scheduler 48h after a video is published.
- The performance scores updated in Firestore directly influence the Ideas Agent's
  topic ranking, closing the content flywheel.
""",
    tools=[fetch_video_analytics, save_analytics, update_topic_scores],
)
