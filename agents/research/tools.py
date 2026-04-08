"""
Research Agent tools — web search and brief synthesis.
Uses DuckDuckGo (free), Tavily (if key present), or Firecrawl (if key present).
"""
from __future__ import annotations
import logging
from datetime import datetime

import httpx
from duckduckgo_search import DDGS

from shared.config import settings
from shared.database import db

logger = logging.getLogger(__name__)


# ── Web Search (DuckDuckGo → Tavily fallback) ────────────────────────────────

def web_search(query: str, max_results: int = 6) -> dict:
    """
    Search the web for information on a topic.
    Uses Tavily if available (better quality), falls back to DuckDuckGo.

    Args:
        query: Search query string.
        max_results: Number of results to return.

    Returns:
        A dict with 'results' list, each having title, url, snippet.
    """
    # Try Tavily first (higher quality, structured answers)
    if settings.has_tavily:
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=settings.tavily_api_key)
            response = client.search(query=query, max_results=max_results, search_depth="advanced")
            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", "")[:400],
                    "source": "tavily",
                }
                for r in response.get("results", [])
            ]
            answer = response.get("answer", "")
            return {"results": results, "answer": answer, "source": "tavily"}
        except Exception as exc:
            logger.warning("Tavily failed, falling back to DDG: %s", exc)

    # Fallback: DuckDuckGo
    try:
        logger.info("Performing DDG search: %s", query)
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", "")[:400],
                "source": "duckduckgo",
            }
            for r in raw
        ]
        logger.info("DDG returned %d results", len(results))
        return {"results": results, "source": "duckduckgo"}
    except Exception as exc:
        logger.error("DDG search failed: %s", exc)
        return {"results": [], "error": str(exc)}


def deep_web_search(query: str) -> dict:
    """
    Perform a focused deep web search for statistics, quotes, and data points.
    Runs multiple searches: main query + 'statistics' + 'expert opinion'.

    Args:
        query: The main research topic or question.

    Returns:
        A dict with combined results from multiple angle searches.
    """
    all_results = []

    queries = [
        query,
        f"{query} statistics data 2025",
        f"{query} expert opinion impact",
    ]

    logger.info("Deep web search started for: %s", query)
    for i, q in enumerate(queries):
        logger.info("Executing angle search %d/%d: %s", i+1, len(queries), q)
        result = web_search(q, max_results=4)
        all_results.extend(result.get("results", []))

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique_results = []
    for r in all_results:
        if r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            unique_results.append(r)

    logger.info("Deep web search complete. Found %d unique results across %d angles.", len(unique_results), len(queries))
    return {
        "results": unique_results[:12],
        "query": query,
        "total_sources": len(unique_results),
    }


# ── Save Research Brief ──────────────────────────────────────────────────────

def save_research_brief(
    topic_id: str,
    topic_title: str,
    summary: str,
    key_facts: list[str],
    quotes: list[str],
    sources: list[str],
) -> dict:
    """
    Save the structured research brief to Firestore.

    Args:
        topic_id: The Firestore ID of the parent topic.
        topic_title: Human-readable topic title.
        summary: 3-5 sentence executive summary of the research.
        key_facts: List of bullet-point facts with statistics.
        quotes: List of quotable statements.
        sources: List of source URLs or publication names.

    Returns:
        A dict with the saved brief_id.
    """
    from shared.models import ResearchBrief

    brief = ResearchBrief(
        topic_id=topic_id,
        topic_title=topic_title,
        summary=summary,
        key_facts=key_facts,
        quotes=quotes,
        sources=sources,
    )
    db.save("research_briefs", brief.id, brief.model_dump(mode="json"))
    return {
        "brief_id": brief.id,
        "topic_id": topic_id,
        "saved": True,
        "message": f"Research brief saved with ID {brief.id}. Ready for Script Agent.",
    }
