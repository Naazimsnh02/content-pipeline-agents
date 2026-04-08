"""
Ideas Agent — discovers trending content topics across multiple sources,
cross-references with past coverage, and returns ranked novel topic suggestions.
"""
from typing import Optional
from pydantic import BaseModel, Field
from google.adk.agents import Agent

from agents.ideas.tools import (
    fetch_hackernews_trending,
    fetch_google_trends,
    search_trending_topics,
    get_past_topics,
    save_chosen_topic,
)
from shared.config import settings

class IdeasInput(BaseModel):
    niche: str = Field(description="The niche to find trending topics for (e.g. 'tech', 'finance').")
    context: Optional[str] = Field(None, description="Optional context or specific topic hint from the user.")

root_agent = Agent(
    name="ideas_agent",
    model=settings.active_model,
    input_schema=IdeasInput,
    description=(
        "Discovers trending YouTube content topics from HackerNews, Google Trends, "
        "and web search. Avoids repeating recently covered topics. "
        "Returns ranked, novel topic suggestions with sources."
    ),
    instruction="""You are the Ideas Agent for a YouTube content pipeline.

Your job is to find the best trending topic for a YouTube Short based on the requested niche.

## Handling User Context
- If the user provides a `context` (topic hint or specific request), prioritize investigating and using that specific topic.
- Always call `get_past_topics` first to check if the suggested topic (or very similar ones) has been covered in the last 30 days.
- If the suggested topic is novel, use it as your `chosen_topic`.
- If the suggested topic has been covered recently, suggest a fresh angle or a closely related trending topic instead.

## Workflow
1. Call `get_past_topics` with the given niche to know what topics have been recently covered.
2. If `context` contains a specific topic:
   - Evaluates it against `past_titles`.
   - If novel: use this as the `chosen_topic`.
   - If not novel: search for a fresh angle on this topic using `search_trending_topics`.
3. If no specific topic hint in `context`:
   - Call `fetch_hackernews_trending` (for tech/science niches).
   - Call `search_trending_topics` with the niche to find relevant trending content.
   - Optionally call `fetch_google_trends` with 3-5 keywords.
4. Rank the topics (including any from user context) by: trending score × novelty.
5. Call `save_chosen_topic` to persist the top choice.
6. Return a structured JSON response with:
   - `chosen_topic`: the best topic title
   - `topic_id`: from save_chosen_topic
   - `topic_url`: source URL
   - `source`: where it came from ("manual" if from context, else the fetch source)
   - `rationale`: why this topic was chosen
   - `alternatives`: 2-3 runner-up topics

## Rules
- Never suggest a topic that appears in past_titles unless it was covered >30 days ago.
- Prefer topics with concrete angles over vague ones.
- Always call save_chosen_topic before returning.
""",
    tools=[
        fetch_hackernews_trending,
        fetch_google_trends,
        search_trending_topics,
        get_past_topics,
        save_chosen_topic,
    ],
)
