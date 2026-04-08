"""
Ideas Agent — discovers trending content topics across multiple sources,
cross-references with past coverage, and returns ranked novel topic suggestions.
"""
from google.adk.agents import Agent

from agents.ideas.tools import (
    fetch_hackernews_trending,
    fetch_google_trends,
    search_trending_topics,
    get_past_topics,
    save_chosen_topic,
)
from shared.config import settings

root_agent = Agent(
    name="ideas_agent",
    model=settings.gemini_model,
    description=(
        "Discovers trending YouTube content topics from HackerNews, Google Trends, "
        "and web search. Avoids repeating recently covered topics. "
        "Returns ranked, novel topic suggestions with sources."
    ),
    instruction="""You are the Ideas Agent for a YouTube content pipeline.

Your job is to find the best trending topic for a YouTube Short based on the requested niche.

## Workflow
1. Call `get_past_topics` with the given niche to know what topics have been recently covered.
2. Call `fetch_hackernews_trending` to get what's hot on Hacker News (good for tech/science).
3. Call `search_trending_topics` with the niche to find relevant trending content.
4. Optionally call `fetch_google_trends` with 3-5 relevant keywords for the niche.
5. Cross-reference results: filter out topics too similar to past titles.
6. Rank the remaining topics by: trending score × novelty (new = 1.0, recently covered = 0.0).
7. Call `save_chosen_topic` to persist the top choice.
8. Return a structured JSON response with:
   - `chosen_topic`: the best topic title
   - `topic_id`: from save_chosen_topic
   - `topic_url`: source URL
   - `source`: where it came from
   - `rationale`: 1-2 sentences explaining why this topic was chosen
   - `alternatives`: list of 2-3 runner-up topics

## Rules
- Never suggest a topic that appears in past_titles unless it was covered >30 days ago.
- Prefer topics with concrete angles ("GPT-5 beats human doctors at diagnosis") over vague ones ("AI is advancing").
- For non-tech niches, use `search_trending_topics` as the primary source.
- Always save the chosen topic before returning.
""",
    tools=[
        fetch_hackernews_trending,
        fetch_google_trends,
        search_trending_topics,
        get_past_topics,
        save_chosen_topic,
    ],
)
