"""
Scheduler Agent — finds the optimal posting time and creates a Google Calendar event.
"""
from typing import Optional
from pydantic import BaseModel, Field
from google.adk.agents import Agent

from agents.scheduler.tools import (
    find_optimal_post_time,
    create_calendar_event,
    save_schedule,
)
from shared.config import settings

class SchedulerInput(BaseModel):
    video_id: str = Field(description="Internal video job ID.")
    youtube_video_id: Optional[str] = Field(default=None, description="YouTube video ID. May be None if upload failed — a calendar reminder will still be created.")
    youtube_title: str = Field(description="Video title for scheduling.")
    niche: str = Field(description="The niche of the content.")
    deadline: Optional[str] = Field(None, description="Optional deadline (day name like 'Tuesday' or ISO date).")
    user_uid: Optional[str] = Field(None, description="Firebase UID of the user — required to load their connected YouTube and Calendar OAuth credentials.")

root_agent = Agent(
    name="scheduler_agent",
    model=settings.active_model,
    input_schema=SchedulerInput,
    description=(
        "Schedules YouTube video publishing. "
        "Finds the optimal post time for the niche and creates a Google Calendar event."
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
- user_uid: Firebase UID of the user (REQUIRED — pass this to create_calendar_event)

## Workflow
1. Call `find_optimal_post_time` with the niche and deadline.
   This returns the recommended publish_at datetime and reasoning.

2. Call `create_calendar_event` with:
   - title: "Publish: <youtube_title>"
   - publish_at: from step 1
   - description: include the YouTube video link and niche
   - video_id: the internal video_id
   - user_uid: pass the user_uid from input (REQUIRED for per-user Calendar credentials)

3. Call `save_schedule` to persist everything to Firestore.

4. Return a summary with:
   - schedule_id
   - publish_at (human-readable)
   - calendar_event_url
   - reasoning (why this time was chosen)
   - A note reminding the user to manually set the video to Public at the scheduled time in YouTube Studio.

## Notes
- Always pass user_uid to create_calendar_event — without it,
  the tool cannot load the user's OAuth credentials and will fail with auth errors.
- If no youtube_video_id is available (production not done yet), still create the
  calendar event as a reminder.
- Always call save_schedule at the end.
- In DEMO_MODE, all calendar calls return simulated results — this is expected.
""",
    tools=[find_optimal_post_time, create_calendar_event, save_schedule],
)
