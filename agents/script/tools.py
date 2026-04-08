"""
Script Agent tools — loads creator style from Firestore and saves generated scripts.
The script writing itself is done by the Gemini LLM in the agent's instruction.
"""
from __future__ import annotations
import logging

from shared.config import settings
from shared.database import db

logger = logging.getLogger(__name__)

# ── Default niche styles (used when no Firestore entry exists) ───────────────

_DEFAULT_STYLES: dict[str, dict] = {
    "tech": {
        "tone": "conversational, energetic, slightly nerdy — like explaining to a smart friend",
        "pacing": "fast — 3 words per second, punchy sentences",
        "hook_style": "open with a surprising stat or provocative question",
        "cta": "Follow for daily tech insights",
        "word_count_target": 160,
        "format": "hook (5s) → context (10s) → 3 key points (35s) → implication (8s) → CTA (5s)",
    },
    "finance": {
        "tone": "clear, authoritative, relatable — no jargon without explanation",
        "pacing": "medium — deliberate, let numbers land",
        "hook_style": "open with a money stat that feels unreal",
        "cta": "Follow for daily money insights",
        "word_count_target": 150,
        "format": "hook (5s) → problem (10s) → 3 actionable tips (35s) → takeaway (8s) → CTA (5s)",
    },
    "fitness": {
        "tone": "motivating, direct, backed by science",
        "pacing": "energetic — match the workout vibe",
        "hook_style": "open with a myth-busting statement",
        "cta": "Follow for science-backed fitness tips",
        "word_count_target": 150,
        "format": "hook (5s) → myth/problem (10s) → solution with 3 steps (35s) → motivation (8s) → CTA (5s)",
    },
    "general": {
        "tone": "engaging, curious, accessible to everyone",
        "pacing": "medium",
        "hook_style": "open with the most surprising fact",
        "cta": "Follow for more",
        "word_count_target": 155,
        "format": "hook → context → 3 points → takeaway → CTA",
    },
}


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

    # Fall back to niche default
    style = _DEFAULT_STYLES.get(niche, _DEFAULT_STYLES["general"])
    return {
        "creator_id": creator_id,
        **style,
        "source": "default",
        "note": f"No custom profile for '{creator_id}', using default '{niche}' style.",
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
