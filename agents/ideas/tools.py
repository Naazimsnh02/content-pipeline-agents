"""
Ideas Agent tools — topic discovery from multiple sources.
All functions are plain Python (google-adk auto-wraps as FunctionTool).

Sources: HackerNews, Google Trends, DuckDuckGo, Reddit, RSS feeds, YouTube Trending.
"""
from __future__ import annotations
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import requests
from duckduckgo_search import DDGS

from shared.config import settings
from shared.database import db
from shared.niches import get_search_queries

logger = logging.getLogger(__name__)

# ── Niche → subreddit mapping ────────────────────────────────────────────────
_NICHE_SUBREDDITS: dict[str, list[str]] = {
    "tech":      ["technology", "programming", "artificial"],
    "finance":   ["finance", "stocks", "wallstreetbets"],
    "fitness":   ["fitness", "bodyweightfitness", "running"],
    "gaming":    ["gaming", "pcgaming", "Games"],
    "science":   ["science", "space", "Futurology"],
    "crypto":    ["CryptoCurrency", "bitcoin", "ethereum"],
    "business":  ["business", "Entrepreneur", "startups"],
    "cooking":   ["Cooking", "food", "recipes"],
    "education": ["education", "learnprogramming", "todayilearned"],
    "beauty":    ["SkincareAddiction", "MakeupAddiction", "beauty"],
    "travel":    ["travel", "solotravel", "backpacking"],
    "sports":    ["sports", "nba", "soccer"],
    "news":      ["worldnews", "news", "UpliftingNews"],
    "history":   ["history", "AskHistorians", "HistoryPorn"],
    "mindset":   ["getdisciplined", "selfimprovement", "productivity"],
    "health":    ["Health", "nutrition", "mentalhealth"],
    "general":   ["popular", "todayilearned", "Futurology"],
}

# ── Niche → RSS feed mapping ─────────────────────────────────────────────────
_NICHE_RSS_FEEDS: dict[str, list[str]] = {
    "tech": [
        "https://hnrss.org/newest?points=100",
        "https://www.theverge.com/rss/index.xml",
    ],
    "finance": [
        "https://feeds.bloomberg.com/markets/news.rss",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    ],
    "science": [
        "https://www.sciencedaily.com/rss/all.xml",
        "https://www.nasa.gov/rss/dyn/breaking_news.rss",
    ],
    "gaming": [
        "https://kotaku.com/rss",
    ],
    "crypto": [
        "https://cointelegraph.com/rss",
    ],
    "fitness": [
        "https://www.menshealth.com/rss/all.xml/",
    ],
    "business": [
        "https://feeds.hbr.org/harvardbusiness",
    ],
    "general": [
        "https://hnrss.org/newest?points=100",
    ],
}

# ── YouTube Trending region codes per niche (all use US) ─────────────────────
_NICHE_YT_CATEGORIES: dict[str, str] = {
    "tech":      "28",   # Science & Technology
    "science":   "28",
    "gaming":    "20",   # Gaming
    "sports":    "17",   # Sports
    "news":      "25",   # News & Politics
    "education": "27",   # Education
    "cooking":   "26",   # Howto & Style
    "beauty":    "26",
    "fitness":   "26",
    "travel":    "19",   # Travel & Events
    "general":   "0",    # All
}


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
    # Load search queries from niche YAML profile (falls back to general)
    queries = get_search_queries(niche)
    query = queries[0] if queries else f"viral trending {niche} news this week"
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


# ── Reddit ────────────────────────────────────────────────────────────────────

def fetch_reddit_trending(niche: str = "tech", limit: int = 10) -> dict:
    """
    Fetch hot posts from niche-relevant subreddits via Reddit's public JSON API.

    Args:
        niche: Content niche — maps to curated subreddits (e.g. "tech" → r/technology, r/programming).
        limit: Number of posts to return across all subreddits.

    Returns:
        A dict with a 'topics' list, each having title, url, score, source, subreddit, comments.
    """
    subreddits = _NICHE_SUBREDDITS.get(niche.lower(), _NICHE_SUBREDDITS["general"])
    headers = {"User-Agent": "Hermes-ContentPipeline/1.0"}
    all_posts: list[dict] = []

    per_sub = max(3, limit // len(subreddits))
    for sub in subreddits:
        try:
            resp = requests.get(
                f"https://www.reddit.com/r/{sub}/hot.json",
                params={"limit": per_sub, "raw_json": 1},
                headers=headers,
                timeout=8,
            )
            if resp.status_code != 200:
                continue
            data = resp.json().get("data", {}).get("children", [])
            for post in data:
                d = post.get("data", {})
                if d.get("stickied"):
                    continue
                all_posts.append({
                    "title": d.get("title", ""),
                    "url": d.get("url", f"https://reddit.com{d.get('permalink', '')}"),
                    "score": d.get("score", 0),
                    "comments": d.get("num_comments", 0),
                    "subreddit": sub,
                    "source": "reddit",
                })
        except Exception as exc:
            logger.warning("Reddit fetch failed for r/%s: %s", sub, exc)

    # Sort by score descending, take top N
    all_posts.sort(key=lambda p: p["score"], reverse=True)
    topics = all_posts[:limit]
    return {"topics": topics, "source": "reddit", "count": len(topics)}


# ── RSS Feeds ─────────────────────────────────────────────────────────────────

def fetch_rss_feeds(niche: str = "tech", limit: int = 10) -> dict:
    """
    Fetch recent articles from niche-relevant RSS feeds (The Verge, HN RSS, Bloomberg, etc.).

    Args:
        niche: Content niche — maps to curated RSS feed URLs.
        limit: Number of articles to return across all feeds.

    Returns:
        A dict with a 'topics' list, each having title, url, published, source.
    """
    feeds = _NICHE_RSS_FEEDS.get(niche.lower(), _NICHE_RSS_FEEDS.get("general", []))
    all_items: list[dict] = []

    for feed_url in feeds:
        try:
            resp = requests.get(feed_url, timeout=10, headers={
                "User-Agent": "Hermes-ContentPipeline/1.0",
            })
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.content)

            # Handle both RSS 2.0 (<rss><channel><item>) and Atom (<feed><entry>)
            items = root.findall(".//item")  # RSS 2.0
            if not items:
                # Atom format
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                items = root.findall("atom:entry", ns)
                for item in items[:limit]:
                    title = item.findtext("atom:title", "", ns).strip()
                    link_el = item.find("atom:link", ns)
                    link = link_el.get("href", "") if link_el is not None else ""
                    published = item.findtext("atom:published", "", ns) or item.findtext("atom:updated", "", ns)
                    if title:
                        all_items.append({
                            "title": title,
                            "url": link,
                            "published": published or "",
                            "source": "rss",
                            "feed": feed_url.split("/")[2],
                        })
            else:
                for item in items[:limit]:
                    title = (item.findtext("title") or "").strip()
                    link = (item.findtext("link") or "").strip()
                    pub_date = item.findtext("pubDate") or ""
                    if title:
                        all_items.append({
                            "title": title,
                            "url": link,
                            "published": pub_date,
                            "source": "rss",
                            "feed": feed_url.split("/")[2],
                        })
        except Exception as exc:
            logger.warning("RSS fetch failed for %s: %s", feed_url, exc)

    return {"topics": all_items[:limit], "source": "rss", "count": len(all_items[:limit])}


# ── YouTube Trending ──────────────────────────────────────────────────────────

def fetch_youtube_trending(niche: str = "tech", limit: int = 10) -> dict:
    """
    Fetch trending YouTube videos for a niche using the YouTube Data API v3.
    Falls back to a web scrape of YouTube trending page if no API key is available.

    Args:
        niche: Content niche — maps to a YouTube video category ID.
        limit: Number of trending videos to return.

    Returns:
        A dict with a 'topics' list, each having title, url, views, channel, source.
    """
    category_id = _NICHE_YT_CATEGORIES.get(niche.lower(), "0")
    api_key = settings.effective_youtube_data_api_key

    if api_key:
        return _fetch_yt_via_api(api_key, category_id, limit)
    else:
        return _fetch_yt_via_scrape(limit)


def _fetch_yt_via_api(api_key: str, category_id: str, limit: int) -> dict:
    """Use YouTube Data API v3 to get trending videos."""
    try:
        params = {
            "part": "snippet,statistics",
            "chart": "mostPopular",
            "regionCode": "US",
            "maxResults": min(limit, 50),
            "key": api_key,
        }
        if category_id and category_id != "0":
            params["videoCategoryId"] = category_id

        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params=params,
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("YouTube API returned %s: %s", resp.status_code, resp.text[:200])
            return {"topics": [], "source": "youtube_trending", "error": f"API {resp.status_code}"}

        data = resp.json()
        topics = []
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            topics.append({
                "title": snippet.get("title", ""),
                "url": f"https://youtube.com/watch?v={item['id']}",
                "channel": snippet.get("channelTitle", ""),
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "source": "youtube_trending",
            })
        return {"topics": topics, "source": "youtube_trending", "count": len(topics)}
    except Exception as exc:
        logger.warning("YouTube API fetch failed: %s", exc)
        return {"topics": [], "source": "youtube_trending", "error": str(exc)}


def _fetch_yt_via_scrape(limit: int) -> dict:
    """Fallback: search DuckDuckGo for 'YouTube trending today' to get topic ideas."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text("YouTube trending videos today", max_results=limit))
        topics = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", "")[:200],
                "source": "youtube_trending_search",
            }
            for r in results
        ]
        return {"topics": topics, "source": "youtube_trending_search", "count": len(topics)}
    except Exception as exc:
        logger.warning("YouTube trending scrape failed: %s", exc)
        return {"topics": [], "source": "youtube_trending_search", "error": str(exc)}


# ── Firestore: past topics (for deduplication) ───────────────────────────────

def get_past_topics(niche: str, limit: int = 20) -> dict:
    """
    Retrieve topics covered in the last 30 days for a niche to avoid repetition.

    Args:
        niche: The content niche to query.
        limit: How many past topics to return.

    Returns:
        A dict with 'past_titles' list of recently used topic titles (last 30 days only).
    """
    titles = db.get_recent_topic_titles(niche=niche, limit=limit, days=30)
    return {
        "past_titles": titles,
        "niche": niche,
        "count": len(titles),
        "message": f"Found {len(titles)} topics covered in the last 30 days in '{niche}' niche.",
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
