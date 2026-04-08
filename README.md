# YouTube Content Pipeline — Multi-Agent System

> **Hackathon Build** | Google ADK · Gemini 2.5 Flash · Cloud Run · Firestore

A production-ready multi-agent content pipeline that converts a single natural-language request like _"Create a YouTube Short about AI trends for next Tuesday"_ into a fully researched, scripted, produced, and scheduled video — autonomously.

---

## Architecture

```
User Request
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│               COORDINATOR AGENT  (Cloud Run)                │
│          Google ADK · LlmAgent · Gemini 2.5 Flash           │
│                                                             │
│  Decomposes intent → dispatches sub-agents → assembles      │
│  final output. Orchestrates parallel + sequential tasks.    │
└──────┬──────────┬──────────┬──────────┬──────────┬──────────┘
       │          │          │          │          │
  [Ideas]   [Research]  [Script]  [Production] [Scheduler]
       │          │          │          │          │
       ▼          ▼          ▼          ▼          ▼
  Trending    Web Search  LLM Script  TTS+Video  Calendar
   Topics      + Brief     + Style    Assembly   + Upload
       │                                          │
       └──────────────┬───────────────────────────┘
                      │
                 [Analytics]        ← triggered 48h post-publish
                      │
              YouTube Metrics
                  → Firestore
                  → Feeds back
                    to Ideas Agent
```

### Agent Responsibilities

| Agent | Role | Tools | Cloud Run Type |
|---|---|---|---|
| **Coordinator** | Brain — decomposes request, dispatches, assembles | AgentTool wrappers for all sub-agents | Service |
| **Ideas Agent** | Discovers trending topics, avoids repetition | HackerNews API, Google Trends, DuckDuckGo, Firestore | Service |
| **Research Agent** | Deep-dives chosen topic → structured brief | DuckDuckGo, Tavily, Firecrawl, web scraping | Service |
| **Script Agent** | Writes platform-optimised script in creator's voice | Niche style from Firestore, Gemini LLM | Service |
| **Production Agent** | Voiceover → images → captions → video → upload | Edge TTS, Gemini Imagen, ffmpeg, YouTube API | Job (async) |
| **Scheduler Agent** | Finds optimal post time, creates calendar event | Google Calendar API, YouTube scheduled publish | Service |
| **Analytics Agent** | Post-publish metrics → feeds back to Ideas DB | YouTube Analytics API, Firestore | Job (cron) |

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Agent Framework** | [Google ADK](https://google.github.io/adk-docs/) (`google-adk`) |
| **LLM** | Gemini 2.5 Flash (`gemini-2.5-flash`) |
| **Database** | Google Cloud Firestore |
| **Serving** | FastAPI + Uvicorn |
| **Deployment** | Google Cloud Run (Services + Jobs) |
| **CI/CD** | Google Cloud Build |
| **Media Storage** | Google Cloud Storage |
| **TTS** | Edge TTS (free) / ElevenLabs (premium) |
| **Images** | Gemini Imagen API |
| **Video Assembly** | ffmpeg |
| **Research** | DuckDuckGo → Tavily → Firecrawl (fallback chain) |

---

## Project Structure

```
content-pipeline-agents/
├── app.py                    # FastAPI entry point + ADK Runner
├── requirements.txt
├── Dockerfile
├── docker-compose.yaml       # Local dev (all services)
├── deploy.sh                 # Cloud Run deployment script
├── cloudbuild.yaml           # Cloud Build CI/CD
├── .env.example
│
├── agents/
│   ├── coordinator/
│   │   └── agent.py          # root_agent — Coordinator
│   ├── ideas/
│   │   ├── agent.py          # Ideas sub-agent
│   │   └── tools.py          # HN, Trends, DDG, Firestore tools
│   ├── research/
│   │   ├── agent.py          # Research sub-agent
│   │   └── tools.py          # Web search + brief tools
│   ├── script/
│   │   ├── agent.py          # Script sub-agent
│   │   └── tools.py          # Style loading + save tools
│   ├── production/
│   │   ├── agent.py          # Production sub-agent
│   │   └── tools.py          # TTS, Imagen, ffmpeg, YouTube
│   ├── scheduler/
│   │   ├── agent.py          # Scheduler sub-agent
│   │   └── tools.py          # Google Calendar + YouTube schedule
│   └── analytics/
│       ├── agent.py          # Analytics sub-agent
│       └── tools.py          # YouTube Analytics + Firestore
│
├── shared/
│   ├── config.py             # Pydantic settings (env vars)
│   ├── database.py           # Firestore client + helpers
│   └── models.py             # Shared Pydantic data models
│
└── demo/
    ├── test_pipeline.py      # End-to-end integration test
    └── sample_requests.http  # REST client test file
```

---

## Quickstart

### Prerequisites
- Python 3.11+
- Google Cloud project with Firestore, Cloud Run, Cloud Storage enabled
- `gcloud` CLI authenticated
- Gemini API key (from [Google AI Studio](https://aistudio.google.com))

### Local Setup

```bash
git clone <repo-url>
cd content-pipeline-agents

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Run locally
python app.py
# or
uvicorn app:app --reload --port 8080
```

### Environment Variables

See `.env.example` for the full list. Minimum required:

```bash
GOOGLE_API_KEY=AIza...          # Gemini API key from AI Studio
GOOGLE_CLOUD_PROJECT=my-project # GCP project ID
```

### Demo Endpoint

```bash
# Run the full pipeline
curl -X POST http://localhost:8080/pipeline \
  -H "Content-Type: application/json" \
  -d '{
    "request": "Create a YouTube Short about the latest AI breakthroughs",
    "niche": "tech",
    "creator_id": "default"
  }'

# Check job status
curl http://localhost:8080/pipeline/{job_id}/status

# Chat with coordinator directly
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What trending topics should I cover this week?"}'
```

### ADK Web UI (local dev)

```bash
# Launch the ADK web chat interface
adk web agents/coordinator
```

---

## Deployment to Cloud Run

### One-Command Deploy

```bash
chmod +x deploy.sh
./deploy.sh
```

### Manual Steps

```bash
# 1. Build and push image
gcloud builds submit --tag gcr.io/$PROJECT_ID/content-pipeline-agents

# 2. Deploy to Cloud Run
gcloud run deploy content-pipeline-agents \
  --image gcr.io/$PROJECT_ID/content-pipeline-agents \
  --platform managed \
  --region us-central1 \
  --memory 2Gi \
  --timeout 3600 \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT_ID" \
  --set-secrets "GOOGLE_API_KEY=google-api-key:latest" \
  --allow-unauthenticated

# 3. Deploy Analytics Agent as a Cloud Run Job (cron)
gcloud run jobs deploy analytics-agent \
  --image gcr.io/$PROJECT_ID/content-pipeline-agents \
  --command python \
  --args "agents/analytics/run_job.py" \
  --region us-central1

# 4. Schedule Analytics Job (every day at 10am UTC)
gcloud scheduler jobs create http analytics-cron \
  --schedule "0 10 * * *" \
  --uri "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/analytics-agent:run" \
  --oauth-service-account-email "$SA_EMAIL"
```

---

## Data Model (Firestore)

```
/topics/{topic_id}
  title, niche, source, score, used_at, created_at

/research_briefs/{brief_id}
  topic_id, summary, key_facts[], quotes[], sources[], created_at

/scripts/{script_id}
  brief_id, niche, script_text, title, description, tags[], platform, created_at

/videos/{video_id}
  script_id, status (pending|processing|done|failed), youtube_url,
  youtube_video_id, published_at, created_at

/schedules/{schedule_id}
  video_id, publish_at, calendar_event_id, platform, created_at

/analytics/{video_id}
  views, watch_time_minutes, avg_view_pct, likes, comments,
  impressions, ctr, fetched_at

/creator_profiles/{creator_id}
  niche, tone, pacing, cta, voice_id, caption_style, music_mood
```

---

## The Feedback Loop (Flywheel)

```
Analytics Agent reads YouTube metrics
        ↓
Updates /analytics/{video_id} in Firestore
        ↓
Recalculates topic score: high watch-time → score +
        ↓
Ideas Agent queries topics ordered by score
        ↓
Next content cycle prioritises proven formats
        ↓
Channel grows → better analytics → smarter ideas
```

---

## Agent Interaction Flow

```
POST /pipeline {"request": "AI trends video for Tuesday", "niche": "tech"}
    │
    ▼ Coordinator
    │  1. Parses: topic_hint="AI trends", deadline="Tuesday", niche="tech"
    │
    ├──► Ideas Agent
    │      - fetch_hackernews_trending(niche="tech")
    │      - fetch_google_trends(keywords=["AI", "machine learning"])
    │      - get_past_topics(niche="tech", limit=20)  ← avoid repeats
    │      - Returns: [{title, score, novelty_score}, ...]
    │
    ├──► Research Agent (with chosen topic)
    │      - web_search("AI trends 2025 breakthroughs")
    │      - web_search("AI statistics 2025")
    │      - synthesise_brief(raw_results)
    │      - save_research_brief(brief) → Firestore
    │      - Returns: {summary, key_facts, quotes, sources}
    │
    ├──► Script Agent (with research brief)
    │      - get_creator_style(creator_id, niche)  ← from Firestore
    │      - (Gemini writes the script using brief + style)
    │      - save_script(script) → Firestore
    │      - Returns: {script, title, description, tags}
    │
    ├──► Production Agent (async background)
    │      - generate_voiceover(script) → Edge TTS / ElevenLabs
    │      - generate_images(scene_prompts) → Gemini Imagen
    │      - assemble_video(frames, audio, captions) → ffmpeg
    │      - upload_to_youtube(video, metadata) → YouTube API
    │      - Returns: {job_id, status_url}
    │
    └──► Scheduler Agent
           - find_optimal_post_time(niche, deadline)
           - create_calendar_event(title, datetime)
           - schedule_youtube_publish(video_id, publish_at)
           - Returns: {publish_at, calendar_event_id}
    │
    ▼ Coordinator assembles response
    Returns: {job_id, topic, publish_at, calendar_event_id, status_url}
```

---

## MCP Server Availability Notes

For future extension with Model Context Protocol:

| Service | MCP Server | Status |
|---|---|---|
| Google Calendar | `nspady/google-calendar-mcp` | Community — OAuth2, full CRUD |
| Firestore | `google/firestore-mcp` | In development |
| Firecrawl | `firecrawl-dev/mcp-server-firecrawl` | Official, production-ready |
| Tavily | `tavily-ai/tavily-mcp` | Official, production-ready |
| GitHub | `github/mcp-server` | Official |
| Supabase | `supabase/mcp-server-supabase` | Official |
| YouTube | Community scrapers only | Read-only |
| YouTube Analytics | REST API only | No MCP yet |
| Buffer/Hootsuite | REST API only | No MCP yet |

Current implementation uses direct API calls for Calendar and Analytics (faster than MCP for Cloud Run).

---

## Extending to True Microservices

Each agent can be extracted to its own Cloud Run service by:

1. Each agent folder gets its own `Dockerfile` and `requirements.txt`
2. Coordinator uses HTTP `AgentTool` pattern instead of in-process:

```python
# Coordinator calls Ideas Agent via HTTP instead of in-process
import httpx

async def call_ideas_agent(niche: str, limit: int = 5) -> dict:
    """Call the Ideas Agent Cloud Run service."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{IDEAS_AGENT_URL}/run",
            json={"niche": niche, "limit": limit},
            timeout=60
        )
    return resp.json()
```

The current monorepo structure is designed so this extraction is a ~5 minute change per agent.
