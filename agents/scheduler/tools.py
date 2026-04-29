"""
Scheduler Agent tools — Google Calendar integration and optimal posting time selection.
Falls back to demo mode if credentials are not configured.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from shared.config import settings
from shared.database import db
from shared.niches import get_posting_windows

logger = logging.getLogger(__name__)


def find_optimal_post_time(niche: str, deadline: Optional[str] = None) -> dict:
    """
    Find the optimal YouTube posting time for the given niche.
    Uses engagement heuristics if no analytics data is available.

    Args:
        niche: Content niche (tech, finance, fitness, general).
        deadline: Optional deadline as ISO date string or day name (e.g. "Tuesday", "2025-07-15").

    Returns:
        A dict with recommended publish_at (ISO string), day, hour_utc, reason.
    """
    windows = get_posting_windows(niche)
    now = datetime.now(timezone.utc)

    # If deadline is a day name, find next occurrence
    target_day = None
    if deadline:
        day_map = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        deadline_lower = deadline.lower().strip()
        if deadline_lower in day_map:
            target_weekday = day_map[deadline_lower]
            days_ahead = (target_weekday - now.weekday()) % 7 or 7
            target_day = now + timedelta(days=days_ahead)
        else:
            try:
                target_day = datetime.fromisoformat(deadline).replace(tzinfo=timezone.utc)
            except ValueError:
                pass

    # Pick the best window
    best_window = windows[0]

    if target_day:
        publish_at = target_day.replace(
            hour=best_window["hour_utc"], minute=0, second=0, microsecond=0
        )
    else:
        # Find next occurrence of the recommended day
        day_map = {
            "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
            "Friday": 4, "Saturday": 5, "Sunday": 6,
        }
        target_weekday = day_map.get(best_window["day"], 2)  # default Wednesday
        days_ahead = (target_weekday - now.weekday()) % 7 or 7
        publish_at = now + timedelta(days=days_ahead)
        publish_at = publish_at.replace(
            hour=best_window["hour_utc"], minute=0, second=0, microsecond=0
        )

    return {
        "publish_at": publish_at.isoformat(),
        "day": best_window["day"],
        "hour_utc": best_window["hour_utc"],
        "reason": best_window["reason"],
        "niche": niche,
        "alternatives": [w for w in windows[1:3]],
    }


def create_calendar_event(
    title: str,
    publish_at: str,
    description: str = "",
    video_id: str = "",
    user_uid: str = "",
) -> dict:
    """
    Create a Google Calendar event for the video publishing deadline.

    Args:
        title: Event title (usually "Publish: {video_title}").
        publish_at: ISO datetime string for when to publish.
        description: Event description with video details.
        video_id: Internal video job ID for reference.
        user_uid: Firebase UID of the user — used to load their connected Calendar.

    Returns:
        A dict with calendar_event_id and calendar_event_url.
    """
    from shared.calendar_oauth import get_user_calendar_credentials

    creds = get_user_calendar_credentials(user_uid) if user_uid else None

    # Fall back to env-based config for backward compatibility
    if not creds and settings.calendar_refresh_token and not settings.demo_mode:
        from google.oauth2.credentials import Credentials
        creds = Credentials(
            token=None,
            refresh_token=settings.calendar_refresh_token,
            client_id=settings.calendar_client_id,
            client_secret=settings.calendar_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
        )

    if settings.demo_mode or not creds:
        fake_event_id = f"demo_event_{video_id or int(datetime.now().timestamp())}"
        return {
            "calendar_event_id": fake_event_id,
            "calendar_event_url": f"https://calendar.google.com/event?eid={fake_event_id}",
            "demo": not creds,
            "message": f"[{'DEMO' if settings.demo_mode else 'NO_CREDS'}] Calendar event would be created: '{title}' at {publish_at}",
        }

    try:
        from googleapiclient.discovery import build

        service = build("calendar", "v3", credentials=creds)

        start_dt = datetime.fromisoformat(publish_at)
        end_dt = start_dt + timedelta(hours=1)

        event_body = {
            "summary": title,
            "description": description or f"YouTube Short publishing task. Video ID: {video_id}",
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "UTC"},
            "end":   {"dateTime": end_dt.isoformat(),   "timeZone": "UTC"},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 60},
                    {"method": "email", "minutes": 1440},
                ],
            },
        }

        event = service.events().insert(
            calendarId=settings.calendar_id,
            body=event_body,
        ).execute()

        return {
            "calendar_event_id": event["id"],
            "calendar_event_url": event.get("htmlLink", ""),
            "created": True,
        }
    except Exception as exc:
        error_str = str(exc)
        logger.error("Calendar event creation failed: %s", exc)
        if "invalid_grant" in error_str:
            return {
                "error": "Calendar OAuth token expired or revoked. Please reconnect Calendar in Settings.",
                "calendar_event_id": None,
                "auth_error": True,
            }
        return {"error": error_str, "calendar_event_id": None}


def save_schedule(
    video_id: str,
    publish_at: str,
    calendar_event_id: Optional[str] = None,
    calendar_event_url: Optional[str] = None,
) -> dict:
    """
    Save the publishing schedule to Firestore.

    Args:
        video_id: Internal video job ID.
        publish_at: ISO datetime string of scheduled publish time.
        calendar_event_id: Google Calendar event ID.
        calendar_event_url: Google Calendar event URL.

    Returns:
        A dict with the schedule_id.
    """
    from shared.models import Schedule

    schedule = Schedule(
        video_id=video_id,
        publish_at=datetime.fromisoformat(publish_at),
        calendar_event_id=calendar_event_id,
        calendar_event_url=calendar_event_url,
    )
    db.save("schedules", schedule.id, schedule.model_dump(mode="json"))
    return {
        "schedule_id": schedule.id,
        "video_id": video_id,
        "publish_at": publish_at,
        "calendar_event_url": calendar_event_url,
        "saved": True,
    }
