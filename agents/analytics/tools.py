"""
Analytics Agent tools — reads YouTube engagement metrics post-publish
and feeds results back to the Ideas DB to close the content flywheel.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional

from shared.config import settings
from shared.database import db

logger = logging.getLogger(__name__)


def fetch_video_analytics(youtube_video_id: str, video_id: str = "") -> dict:
    """
    Fetch engagement metrics for a published YouTube video from the Analytics API.

    Args:
        youtube_video_id: The YouTube video ID (e.g. "dQw4w9WgXcQ").
        video_id: Internal video job ID for reference.

    Returns:
        A dict with views, watch_time_minutes, avg_view_percentage, likes, comments,
        impressions, and ctr.
    """
    if settings.demo_mode or not settings.has_youtube:
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

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(
            token=None,
            refresh_token=settings.youtube_refresh_token,
            client_id=settings.youtube_client_id,
            client_secret=settings.youtube_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
        )
        analytics = build("youtubeAnalytics", "v2", credentials=creds)

        # Fetch last 30 days of data for the video
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
            return {"error": "No analytics data yet (video may be too new)", "youtube_video_id": youtube_video_id}

        row = rows[0]
        return {
            "youtube_video_id": youtube_video_id,
            "video_id": video_id,
            "views": int(row[1]),
            "watch_time_minutes": float(row[2]),
            "avg_view_percentage": float(row[3]),
            "likes": int(row[4]),
            "comments": int(row[5]),
            "impressions": 0,   # Requires separate Search Console API call
            "ctr": 0.0,
        }
    except Exception as exc:
        logger.error("Analytics fetch failed: %s", exc)
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
    db.save("analytics", video_id, analytics.model_dump(mode="json"))

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


def update_topic_scores(niche: str, top_n: int = 10) -> dict:
    """
    Update topic scores in Firestore based on analytics performance.
    High-performing topics get a score boost to influence future content selection.
    This closes the feedback loop: analytics → ideas.

    Args:
        niche: The content niche to update scores for.
        top_n: Number of top-performing videos to analyse.

    Returns:
        A dict with updated topic count and insights.
    """
    # Get videos with analytics for this niche
    videos = db.query("videos", filters=[("status", "==", "done")], limit=top_n)

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
