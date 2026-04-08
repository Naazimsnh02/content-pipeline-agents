"""
Ideas Agent tools — topic discovery from multiple sources.
All functions are plain Python (google-adk auto-wraps as FunctionTool).
"""
from __future__ import annotations
import logging
import time
from datetime import datetime, timezone
from typing import Any

import requests
from duckduckgo_search import DDGS

from shared.config import settings
from shared.database import db

logger = logging.getLogger(__name__)


# ── HackerNews ───────────────────────────────────────────────────────────────

def fetch_hackernews_trending(limit: int = 15) -> dict:
    """
    Fetch the top trending stories from Hacker News.

    Args:
        limit: Number of stories to return (max 30).

    Returns:
        A dict with a 'topics' list, each item having title, url, score, source.
    """
    try:
        resp = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10
        )
        story_ids = resp.json()[: min(limit * 2, 60)]  # fetch extra, filter below

        topics = []
        for sid in story_ids[:limit]:
            story = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=5
            ).json()
            if not story or story.get("type") != "story":
                continue
            topics.append(
                {
                    "title": story.get("title", ""),
                    "url": story.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                    "score": story.get("score", 0),
                    "source": "hackernews",
                    "comments": story.get("descendants", 0),
                }
            )
        return {"topics": topics, "source": "hackernews", "count": len(topics)}
    except Exception as exc:
        logger.warning("HN fetch failed: %s", exc)
        return {"topics": [], "source": "hackernews", "error": str(exc)}


# ── Google Trends ────────────────────────────────────────────────────────────

def fetch_google_trends(keywords: list[str], timeframe: str = "now 7-d") -> dict:
    """
    Fetch Google Trends interest data for a list of keywords.

    Args:
        keywords: Up to 5 keywords/phrases to compare (e.g. ["AI", "ChatGPT"]).
        timeframe: Google Trends timeframe string (default "now 7-d" = last 7 days).

    Returns:
        A dict with 'trending_keywords' list sorted by interest score.
    """
    try:
        from pytrends.request import TrendReq

        pytrends = TrendReq(hl="en-US", tz=0, timeout=(5, 15))
        kw_list = keywords[:5]
        pytrends.build_payload(kw_list, timeframe=timeframe)
        interest = pytrends.interest_over_time()

        if interest.empty:
            return {"trending_keywords": [], "source": "google_trends"}

        averages = interest.drop(columns=["isPartial"], errors="ignore").mean()
        ranked = sorted(
            [{"keyword": k, "interest_score": float(v)} for k, v in averages.items()],
            key=lambda x: x["interest_score"],
            reverse=True,
        )
        return {"trending_keywords": ranked, "source": "google_trends"}
    except Exception as exc:
        logger.warning("Google Trends failed: %s", exc)
        return {"trending_keywords": [], "source": "google_trends", "error": str(exc)}


# ── DuckDuckGo topic search ──────────────────────────────────────────────────

def search_trending_topics(niche: str, limit: int = 8) -> dict:
    """
    Search DuckDuckGo for trending topics in a given content niche.

    Args:
        niche: Content niche — e.g. "tech", "finance", "fitness", "gaming".
        limit: Number of results to return.

    Returns:
        A dict with a 'topics' list from web search results.
    """
    niche_queries = {
        "tech": "latest AI technology breakthroughs this week",
        "finance": "trending personal finance investing news this week",
        "fitness": "viral fitness health trends this week",
        "gaming": "trending gaming news releases this week",
        "science": "breakthrough science discoveries this week",
        "general": f"viral trending {niche} news this week",
    }
    query = niche_queries.get(niche, niche_queries["general"])
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=limit))
        topics = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", "")[:200],
                "source": "duckduckgo",
                "score": 0,
            }
            for r in results
        ]
        return {"topics": topics, "source": "duckduckgo", "count": len(topics)}
    except Exception as exc:
        logger.warning("DDG search failed: %s", exc)
        return {"topics": [], "source": "duckduckgo", "error": str(exc)}


# ── Firestore: past topics (for deduplication) ───────────────────────────────

def get_past_topics(niche: str, limit: int = 20) -> dict:
    """
    Retrieve recently covered topics for a niche to avoid repetition.

    Args:
        niche: The content niche to query.
        limit: How many past topics to return.

    Returns:
        A dict with 'past_titles' list of recently used topic titles.
    """
    titles = db.get_recent_topic_titles(niche=niche, limit=limit)
    return {
        "past_titles": titles,
        "niche": niche,
        "count": len(titles),
        "message": f"Found {len(titles)} recently covered topics in '{niche}' niche.",
    }


def save_chosen_topic(
    title: str,
    niche: str,
    source: str,
    url: str = "",
    score: float = 0.0,
) -> dict:
    """
    Save the chosen topic to Firestore for tracking and deduplication.

    Args:
        title: Topic title.
        niche: Content niche.
        source: Where it was discovered (hackernews, google_trends, etc.).
        url: Source URL if available.
        score: Trending score.

    Returns:
        A dict with the saved topic_id.
    """
    from shared.models import Topic

    topic = Topic(
        title=title,
        niche=niche,
        source=source,
        url=url,
        score=score,
        used_at=datetime.now(timezone.utc),
    )
    db.save("topics", topic.id, topic.model_dump(mode="json"))
    return {
        "topic_id": topic.id,
        "title": title,
        "saved": True,
        "message": f"Topic '{title}' saved with ID {topic.id}.",
    }
