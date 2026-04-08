"""
Research Agent — takes a chosen topic, performs multi-angle web research,
and produces a structured brief ready for the Script Agent.
"""
from pydantic import BaseModel, Field
from google.adk.agents import Agent

from agents.research.tools import web_search, deep_web_search, save_research_brief
from shared.config import settings

class ResearchInput(BaseModel):
    topic_title: str = Field(description="The title of the topic to research.")
    topic_id: str = Field(description="The unique ID of the topic.")
    niche: str = Field(description="The niche of the topic.")

root_agent = Agent(
    name="research_agent",
    model=settings.active_model,
    input_schema=ResearchInput,
    description=(
        "Researches a given topic in depth using web search. "
        "Produces a structured brief with summary, key facts, quotes, and sources "
        "that the Script Agent can consume to write an accurate, engaging script."
    ),
    instruction="""You are the Research Agent for a YouTube content pipeline.

Your job: given a topic title and optional topic_id, research it thoroughly and
produce a structured brief for the Script Agent.

## Workflow
1. Call `deep_web_search` with the topic title as the query.
2. Review results. If you have enough info, STOP SEARCHING.
3. If the initial results were irrelevant, call `web_search` ONCE.
4. IMPORTANT: Do not call search tools more than 2 times total.
5. Call `save_research_brief` with the best data available.
6. Return a structured JSON response with: brief_id, summary, key_facts, quotes, sources.

## Rules
- Be decisive. Do not over-research.
- Prefer facts with numbers, but don't loop if they aren't found.
- Keep each key_fact under 25 words.
- Always call save_research_brief before returning.
""",
    tools=[web_search, deep_web_search, save_research_brief],
)
