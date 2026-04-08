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
a) A specific video to analyse: {video_id, youtube_video_id, niche}
b) A batch request: {niche, run_for_all_recent: true}

## Workflow

### Single Video Mode
1. Call `fetch_video_analytics` with the youtube_video_id and video_id.
2. Call `save_analytics` with all the metrics returned.
3. Call `update_topic_scores` with the niche.
4. Return a performance report.

### Batch Mode (cron job)
1. Call `update_topic_scores` for the given niche.
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
