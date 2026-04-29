"""
Script Agent tools — loads creator style from Firestore and saves generated scripts.
The script writing itself is done by the Gemini LLM in the agent's instruction.
Includes A/B variant evaluation for hook optimisation.
"""
from __future__ import annotations
import logging

from shared.config import settings
from shared.database import db
from shared.niches import get_script_style

logger = logging.getLogger(__name__)


# ── Tools ────────────────────────────────────────────────────────────────────

def get_creator_style(creator_id: str = "default", niche: str = "tech") -> dict:
    """
    Load the creator's style profile for script generation.
    Tries Firestore first, falls back to built-in niche defaults.

    Args:
        creator_id: Creator profile ID in Firestore (default: "default").
        niche: Content niche as fallback key (tech, finance, fitness, gaming, general).

    Returns:
        A dict with tone, pacing, hook_style, cta, word_count_target, format.
    """
    # Try Firestore profile
    profile = db.get("creator_profiles", creator_id)
    if profile:
        return {
            "creator_id": creator_id,
            "tone": profile.get("tone", ""),
            "pacing": profile.get("pacing", ""),
            "hook_style": profile.get("hook_style", ""),
            "cta": profile.get("cta", "Follow for more"),
            "word_count_target": 155,
            "format": "hook (5s) → context → 3 key points → implication → CTA (5s)",
            "source": "firestore",
        }

    # Fall back to YAML niche profile
    style = get_script_style(niche)
    return {
        "creator_id": creator_id,
        **style,
        "source": "niche_profile",
        "note": f"No custom profile for '{creator_id}', using '{niche}' niche profile.",
    }


def save_script(
    brief_id: str,
    topic_title: str,
    niche: str,
    script_text: str,
    hook: str,
    cta: str,
    youtube_title: str,
    youtube_description: str,
    youtube_tags: list[str],
    creator_id: str = "default",
    platform: str = "youtube_shorts",
    pipeline_job_id: str = "",
) -> dict:
    """
    Save the generated script to Firestore.

    Args:
        brief_id: Research brief ID this script is based on.
        topic_title: The topic title.
        niche: Content niche.
        script_text: Full voiceover script text.
        hook: The opening hook line (first 3 seconds of speech).
        cta: The call-to-action line.
        youtube_title: SEO-optimised YouTube title (max 100 chars).
        youtube_description: YouTube description with relevant hashtags.
        youtube_tags: List of YouTube tags for discoverability.
        creator_id: Creator profile used.
        platform: "youtube_shorts" | "youtube_long" | "instagram_reel".
        pipeline_job_id: The pipeline job ID — links this script back to the originating job.

    Returns:
        A dict with the saved script_id.
    """
    from shared.models import Script

    word_count = len(script_text.split())
    estimated_duration = max(45, min(90, word_count // 3))  # ~3 words/sec

    script = Script(
        brief_id=brief_id,
        topic_title=topic_title,
        niche=niche,
        platform=platform,
        script_text=script_text,
        hook=hook,
        cta=cta,
        youtube_title=youtube_title[:100],
        youtube_description=youtube_description,
        youtube_tags=youtube_tags[:15],
        word_count=word_count,
        estimated_duration_s=estimated_duration,
        creator_id=creator_id,
        pipeline_job_id=pipeline_job_id or None,
    )
    db.save("scripts", script.id, script.model_dump(mode="json"))
    return {
        "script_id": script.id,
        "brief_id": brief_id,
        "word_count": word_count,
        "estimated_duration_s": estimated_duration,
        "youtube_title": youtube_title,
        "saved": True,
        "message": f"Script saved ({word_count} words, ~{estimated_duration}s). Ready for production.",
    }


def save_twitter_content(
    script_id: str,
    thread_tweets: list[str],
    long_post: str,
    hashtags: list[str],
    pipeline_job_id: str = "",
) -> dict:
    """
    Save generated Twitter/X content (thread + long post) to Firestore.

    Args:
        script_id: The script this content is derived from.
        thread_tweets: List of 3-5 tweets, each ≤280 characters.
        long_post: A 500-1000 character long-form X post.
        hashtags: List of relevant hashtags (without #).
        pipeline_job_id: The pipeline job ID — links this content back to the originating job.

    Returns:
        A dict with the saved twitter_content_id.
    """
    from shared.models import TwitterContent

    # Enforce 280-char limit per tweet
    trimmed = [t[:280] for t in thread_tweets]

    content = TwitterContent(
        script_id=script_id,
        thread_tweets=trimmed,
        long_post=long_post[:1000],
        hashtags=hashtags[:10],
        pipeline_job_id=pipeline_job_id or None,
    )
    db.save("twitter_content", content.id, content.model_dump(mode="json"))
    return {
        "twitter_content_id": content.id,
        "script_id": script_id,
        "tweet_count": len(trimmed),
        "saved": True,
        "message": f"Twitter content saved: {len(trimmed)} tweets + long post.",
    }


def evaluate_hook_ab(
    hook_a: str,
    hook_b: str,
    script_a: str,
    script_b: str,
    niche: str,
    topic_title: str,
) -> dict:
    """
    Evaluate two script variants (A/B) and pick the winner based on hook strength,
    retention potential, and niche fit. Uses Gemini to score both variants.

    Args:
        hook_a: The opening hook line of Variant A.
        hook_b: The opening hook line of Variant B.
        script_a: Full script text of Variant A.
        script_b: Full script text of Variant B.
        niche: Content niche for context.
        topic_title: The topic being scripted.

    Returns:
        A dict with winner ("A" or "B"), scores for each, and reasoning.
    """
    import google.genai as genai

    prompt = f"""You are a YouTube Shorts retention expert. Evaluate these two script variants for a {niche} Short about "{topic_title}".

## Variant A
Hook: "{hook_a}"
Full script:
{script_a}

## Variant B
Hook: "{hook_b}"
Full script:
{script_b}

## Scoring Criteria (1-10 each)
1. **Hook Power**: Does the first line stop the scroll? Curiosity gap? Shock value?
2. **Retention Flow**: Will viewers stay past 3s? Past 15s? Past 30s?
3. **Niche Fit**: Does the tone/style match the {niche} audience?
4. **CTA Strength**: Will viewers follow/like/comment?
5. **Shareability**: Would someone send this to a friend?

Respond in EXACTLY this JSON format (no markdown, no extra text):
{{"winner": "A" or "B", "score_a": {{"hook_power": N, "retention": N, "niche_fit": N, "cta": N, "shareability": N, "total": N}}, "score_b": {{"hook_power": N, "retention": N, "niche_fit": N, "cta": N, "shareability": N, "total": N}}, "reasoning": "one sentence why the winner is better"}}"""

    try:
        client = genai.Client()
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
        )
        text = response.text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        import json
        result = json.loads(text)
        result["evaluated"] = True
        return result
    except Exception as exc:
        logger.warning("A/B evaluation failed: %s — defaulting to Variant A", exc)
        return {
            "winner": "A",
            "score_a": {"total": 0},
            "score_b": {"total": 0},
            "reasoning": f"Evaluation failed ({exc}), defaulting to Variant A.",
            "evaluated": False,
            "error": str(exc),
        }
