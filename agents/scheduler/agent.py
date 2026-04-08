"""
Scheduler Agent — finds the optimal posting time, creates a Google Calendar event,
and schedules the YouTube video for automatic publishing.
"""
from typing import Optional
from pydantic import BaseModel, Field
from google.adk.agents import Agent

from agents.scheduler.tools import (
    find_optimal_post_time,
    create_calendar_event,
    schedule_youtube_publish,
    save_schedule,
)
from shared.config import settings

class SchedulerInput(BaseModel):
    video_id: str = Field(description="Internal video job ID.")
    youtube_video_id: str = Field(description="YouTube video ID.")
    youtube_title: str = Field(description="Video title for scheduling.")
    niche: str = Field(description="The niche of the content.")
    deadline: Optional[str] = Field(None, description="Optional deadline (day name like 'Tuesday' or ISO date).")

root_agent = Agent(
    name="scheduler_agent",
    model=settings.gemini_model,
    input_schema=SchedulerInput,
    description=(
        "Schedules YouTube video publishing. "
        "Finds the optimal post time for the niche, creates a Google Calendar event, "
        "and schedules the video for auto-publish on YouTube."
    ),
    instruction="""You are the Scheduler Agent for a YouTube content pipeline.

Your job: schedule a produced YouTube video for optimal publishing.

## Required Input
You will receive:
- video_id: Internal video job ID
- youtube_video_id: YouTube video ID (from Production Agent)
- youtube_title: Video title (for calendar event)
- niche: Content niche (for optimal time selection)
- deadline: Optional deadline (day name like "Tuesday" or ISO date)

## Workflow
1. Call `find_optimal_post_time` with the niche and deadline.
   This returns the recommended publish_at datetime and reasoning.

2. Call `create_calendar_event` with:
   - title: "Publish: <youtube_title>"
   - publish_at: from step 1
   - description: include the YouTube video link and niche
   - video_id: the internal video_id

3. Call `schedule_youtube_publish` with:
   - youtube_video_id from input
   - publish_at from step 1
   (This sets the video to auto-publish at the scheduled time)

4. Call `save_schedule` to persist everything to Firestore.

5. Return a summary with:
   - schedule_id
   - publish_at (human-readable)
   - calendar_event_url
   - reasoning (why this time was chosen)

## Notes
- If no youtube_video_id is available (production not done yet), still create the
  calendar event as a reminder — scheduling the YouTube video can be done later.
- Always call save_schedule at the end.
- In DEMO_MODE, all calendar/YouTube calls return simulated results — this is expected.
""",
    tools=[find_optimal_post_time, create_calendar_event, schedule_youtube_publish, save_schedule],
)
