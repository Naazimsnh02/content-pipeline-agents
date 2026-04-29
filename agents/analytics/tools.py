"""
Analytics Agent tools — reads YouTube engagement metrics post-publish
and feeds results back to the Ideas DB to close the content flywheel.

Supports per-user YouTube OAuth credentials (stored in Firestore) so each
user's analytics are fetched with their own connected YouTube account.
Falls back to global .env credentials for backward compatibility.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional

from shared.config import settings
from shared.database import db

logger = logging.getLogger(__name__)


def fetch_video_analytics(
    youtube_video_id: str,
    video_id: str = "",
    user_uid: str = "",
) -> dict:
    """
    Fetch engagement metrics for a published YouTube video from the Analytics API.

    Uses per-user OAuth tokens if user_uid is provided and the user has
    connected their YouTube account. Falls back to global .env credentials
    if no per-user tokens are found.

    Args:
        youtube_video_id: The YouTube video ID (e.g. "dQw4w9WgXcQ").
        video_id: Internal video job ID for reference.
        user_uid: Firebase UID of the user — used to load per-user YouTube credentials.

    Returns:
        A dict with views, watch_time_minutes, avg_view_percentage, likes, comments,
        impressions, and ctr.
    """
    if settings.demo_mode:
        import random
        # Return realistic-looking demo data
        return {
            "youtube_video_id": youtube_video_id,
            "video_id": video_id,
            "views": random.randint(500, 15000),
            "watch_time_minutes": round(random.uniform(200, 8000), 1),
            "avg_view_percentage": round(random.uniform(35, 72), 1),
            "likes": random.randint(20, 800),
            "comments": random.randint(5, 120),
            "impressions": random.randint(2000, 50000),
            "ctr": round(random.uniform(3.5, 12.0), 2),
            "demo": True,
            "message": f"[DEMO] Simulated analytics for video {youtube_video_id}.",
        }

    # Build credentials — per-user first, then global fallback
    creds = None
    creds_source = "none"

    if user_uid:
        try:
            from shared.youtube_oauth import get_user_youtube_credentials
            creds = get_user_youtube_credentials(user_uid)
            if creds:
                creds_source = "per-user"
                logger.info("Using per-user YouTube credentials for analytics (user %s)", user_uid)
        except Exception as exc:
            logger.warning("Failed to load per-user YouTube creds for analytics: %s", exc)

    if not creds and settings.has_youtube:
        try:
            from google.oauth2.credentials import Credentials
            creds = Credentials(
                token=None,
                refresh_token=settings.youtube_refresh_token,
                client_id=settings.youtube_client_id,
                client_secret=settings.youtube_client_secret,
                token_uri="https://oauth2.googleapis.com/token",
            )
            creds_source = "global"
            logger.info("Using global YouTube credentials for analytics")
        except Exception as exc:
            logger.warning("Failed to load global YouTube creds: %s", exc)

    if not creds:
        return {
            "error": "YouTube not connected. Please connect your YouTube account in Settings, "
                     "or configure YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN in .env.",
            "youtube_video_id": youtube_video_id,
        }

    try:
        from googleapiclient.discovery import build

        # Try the YouTube Analytics API first (requires yt-analytics.readonly scope)
        try:
            analytics = build("youtubeAnalytics", "v2", credentials=creds)

            end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            start_date = (datetime.now(timezone.utc).replace(day=1)).strftime("%Y-%m-%d")

            response = analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="views,estimatedMinutesWatched,averageViewPercentage,likes,comments",
                dimensions="video",
                filters=f"video=={youtube_video_id}",
            ).execute()

            rows = response.get("rows", [])
            if not rows:
                return {
                    "error": "No analytics data yet (video may be too new)",
                    "youtube_video_id": youtube_video_id,
                }

            row = rows[0]
            return {
                "youtube_video_id": youtube_video_id,
                "video_id": video_id,
                "views": int(row[1]),
                "watch_time_minutes": float(row[2]),
                "avg_view_percentage": float(row[3]),
                "likes": int(row[4]),
                "comments": int(row[5]),
                "impressions": 0,
                "ctr": 0.0,
                "credentials_source": creds_source,
                "api": "youtube_analytics",
            }
        except Exception as analytics_exc:
            # If the Analytics API fails (e.g. insufficient scopes), fall back
            # to the YouTube Data API v3 which only needs youtube.readonly
            if "insufficientPermissions" in str(analytics_exc) or "403" in str(analytics_exc):
                logger.warning(
                    "YouTube Analytics API failed (insufficient scopes), "
                    "falling back to Data API v3: %s", analytics_exc,
                )
            else:
                raise analytics_exc

        # Fallback: YouTube Data API v3 — videos.list gives basic stats
        # (requires youtube.readonly which is always granted)
        youtube = build("youtube", "v3", credentials=creds)
        vid_response = youtube.videos().list(
            part="statistics",
            id=youtube_video_id,
        ).execute()

        items = vid_response.get("items", [])
        if not items:
            return {
                "error": "Video not found or not accessible with current credentials",
                "youtube_video_id": youtube_video_id,
            }

        stats = items[0].get("statistics", {})
        views = int(stats.get("viewCount", 0))
        likes = int(stats.get("likeCount", 0))
        comments = int(stats.get("commentCount", 0))

        return {
            "youtube_video_id": youtube_video_id,
            "video_id": video_id,
            "views": views,
            "watch_time_minutes": 0.0,       # Not available from Data API
            "avg_view_percentage": 0.0,       # Not available from Data API
            "likes": likes,
            "comments": comments,
            "impressions": 0,
            "ctr": 0.0,
            "credentials_source": creds_source,
            "api": "youtube_data_v3_fallback",
            "note": (
                "Watch time and avg view % are unavailable — the yt-analytics.readonly "
                "scope was not granted. Please disconnect and reconnect YouTube in "
                "Settings to grant the updated permissions."
            ),
        }
    except Exception as exc:
        error_str = str(exc)
        logger.error("Analytics fetch failed: %s", exc)
        if "invalid_grant" in error_str:
            return {
                "error": "YouTube OAuth token expired or revoked. Please reconnect YouTube in Settings.",
                "youtube_video_id": youtube_video_id,
                "auth_error": True,
            }
        return {"error": str(exc), "youtube_video_id": youtube_video_id}


def save_analytics(
    video_id: str,
    youtube_video_id: str,
    views: int,
    watch_time_minutes: float,
    avg_view_percentage: float,
    likes: int,
    comments: int,
    impressions: int = 0,
    ctr: float = 0.0,
    user_uid: str = "",
) -> dict:
    """
    Save video analytics to Firestore for the content flywheel.

    Args:
        video_id: Internal video job ID.
        youtube_video_id: YouTube video ID.
        views: Total view count.
        watch_time_minutes: Total watch time in minutes.
        avg_view_percentage: Average percentage of video watched (0-100).
        likes: Total likes.
        comments: Total comments.
        impressions: Total impressions (from YouTube Search).
        ctr: Click-through rate percentage.
        user_uid: Firebase UID of the user who owns this video.

    Returns:
        A dict confirming the save with performance_grade.
    """
    from shared.models import VideoAnalytics

    analytics = VideoAnalytics(
        video_id=video_id,
        youtube_video_id=youtube_video_id,
        views=views,
        watch_time_minutes=watch_time_minutes,
        avg_view_percentage=avg_view_percentage,
        likes=likes,
        comments=comments,
        impressions=impressions,
        ctr=ctr,
    )
    data = analytics.model_dump(mode="json")
    if user_uid:
        data["user_uid"] = user_uid
    db.save("analytics", video_id, data)

    # Simple performance grade
    grade = "C"
    if avg_view_percentage >= 60 and views >= 5000:
        grade = "A"
    elif avg_view_percentage >= 45 and views >= 1000:
        grade = "B"

    return {
        "video_id": video_id,
        "saved": True,
        "performance_grade": grade,
        "summary": f"Views: {views:,} | Watch time: {watch_time_minutes:.0f}min | Avg view: {avg_view_percentage:.1f}%",
    }


def update_topic_scores(niche: str, top_n: int = 10, user_uid: str = "") -> dict:
    """
    Update topic scores in Firestore based on analytics performance.
    High-performing topics get a score boost to influence future content selection.
    This closes the feedback loop: analytics → ideas.

    Args:
        niche: The content niche to update scores for.
        top_n: Number of top-performing videos to analyse.
        user_uid: Firebase UID — when provided, only analyses videos owned by this user.

    Returns:
        A dict with updated topic count and insights.
    """
    # Build filters — scope to user if user_uid is provided
    # Query all done videos first, then filter by ownership (user_id or pipeline_job_id)
    videos = db.query("videos", filters=[("status", "==", "done")], limit=top_n * 3)

    if user_uid:
        # Also get the user's pipeline jobs for fallback ownership check
        user_jobs = db.query("pipeline_jobs", filters=[("user_id", "==", user_uid)], limit=200)
        user_job_ids = {j.get("job_id") for j in user_jobs}
        videos = [
            v for v in videos
            if v.get("user_id") == user_uid or v.get("pipeline_job_id") in user_job_ids
        ][:top_n]
    else:
        videos = videos[:top_n]

    updated = 0
    insights = []

    for video in videos:
        video_id = video.get("id", "")
        script_id = video.get("script_id", "")

        # Get analytics for this video
        analytics_doc = db.get("analytics", video_id)
        if not analytics_doc:
            continue

        # Get the script to find the topic
        script_doc = db.get("scripts", script_id)
        if not script_doc:
            continue

        brief_id = script_doc.get("brief_id", "")
        brief_doc = db.get("research_briefs", brief_id)
        if not brief_doc:
            continue

        topic_id = brief_doc.get("topic_id", "")
        if not topic_id:
            continue

        # Calculate performance score (0-1 scale)
        avg_view_pct = analytics_doc.get("avg_view_percentage", 0)
        views = analytics_doc.get("views", 0)
        perf_score = min(1.0, (avg_view_pct / 100) * 0.6 + min(views / 10000, 1.0) * 0.4)

        db.update("topics", topic_id, {"performance_score": perf_score})
        updated += 1

        if perf_score > 0.7:
            topic_title = brief_doc.get("topic_title", "Unknown")
            insights.append(f"High performer: '{topic_title}' (score: {perf_score:.2f})")

    return {
        "niche": niche,
        "topics_updated": updated,
        "insights": insights[:5],
        "message": f"Updated scores for {updated} topics in '{niche}' niche. {len(insights)} high performers found.",
    }
