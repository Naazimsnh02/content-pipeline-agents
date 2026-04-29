"""
Niche profile loader — reads YAML profiles from the niches/ directory.
Provides a single `get_niche_profile(niche)` function used by all agents.

Profile fields:
  niche, display_name
  tone, pacing, hook_style, cta, word_count_target, format
  visual_style, color_palette, mood
  caption_highlight_color, caption_font_weight, caption_style
  music_mood, music_energy, music_bpm_range
  voice_style, voice_id
  search_queries
  posting_windows
"""
from __future__ import annotations
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_NICHES_DIR = Path(__file__).parent.parent / "niches"

# ── Loader ────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _load_profile(niche: str) -> dict[str, Any] | None:
    """Load and cache a single YAML niche profile. Returns None if not found."""
    path = _NICHES_DIR / f"{niche}.yaml"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as exc:
        logger.warning("Failed to load niche profile '%s': %s", niche, exc)
        return None


def get_niche_profile(niche: str) -> dict[str, Any]:
    """
    Return the full niche profile for the given niche key.
    Falls back to 'general' if the requested niche has no YAML file.

    Args:
        niche: Niche key, e.g. "tech", "finance", "cooking".

    Returns:
        A dict with all profile fields. Never raises — always returns something.
    """
    profile = _load_profile(niche.lower().strip())
    if profile is None:
        logger.info("No profile for niche '%s', falling back to 'general'", niche)
        profile = _load_profile("general") or {}
    return profile


def list_niches() -> list[str]:
    """Return all available niche keys (YAML filenames without extension)."""
    if not _NICHES_DIR.exists():
        return ["general"]
    return sorted(p.stem for p in _NICHES_DIR.glob("*.yaml"))


# ── Convenience accessors ─────────────────────────────────────────────────────

def get_script_style(niche: str) -> dict[str, Any]:
    """Return only the script-writing fields for the given niche."""
    p = get_niche_profile(niche)
    return {
        "tone": p.get("tone", "engaging, accessible"),
        "pacing": p.get("pacing", "medium"),
        "hook_style": p.get("hook_style", "open with the most surprising fact"),
        "cta": p.get("cta", "Follow for more"),
        "word_count_target": p.get("word_count_target", 155),
        "format": p.get("format", "hook → context → 3 points → takeaway → CTA"),
    }


def get_visual_style(niche: str) -> dict[str, Any]:
    """Return visual/image generation fields for the given niche."""
    p = get_niche_profile(niche)
    return {
        "visual_style": p.get("visual_style", "clean, modern, engaging"),
        "color_palette": p.get("color_palette", ["#3498DB", "#FFFFFF"]),
        "mood": p.get("mood", "engaging"),
    }


def get_caption_style(niche: str) -> dict[str, Any]:
    """Return caption/subtitle styling fields for the given niche."""
    p = get_niche_profile(niche)
    return {
        "highlight_color": p.get("caption_highlight_color", "#FFFF00"),
        "font_weight": p.get("caption_font_weight", "bold"),
        "style": p.get("caption_style", "word-by-word yellow highlight"),
    }


def get_music_style(niche: str) -> dict[str, Any]:
    """Return music selection fields for the given niche."""
    p = get_niche_profile(niche)
    return {
        "mood": p.get("music_mood", "upbeat, neutral"),
        "energy": p.get("music_energy", "medium"),
        "bpm_range": p.get("music_bpm_range", [100, 120]),
    }


def get_voice_style(niche: str) -> dict[str, Any]:
    """Return TTS voice fields for the given niche."""
    p = get_niche_profile(niche)
    return {
        "style": p.get("voice_style", "clear, friendly"),
        "voice_id": p.get("voice_id", "en-US-AriaNeural"),
    }


def get_search_queries(niche: str) -> list[str]:
    """Return topic discovery search queries for the given niche."""
    p = get_niche_profile(niche)
    return p.get("search_queries", [f"viral trending {niche} news this week"])


def get_posting_windows(niche: str) -> list[dict[str, Any]]:
    """Return optimal posting windows for the given niche."""
    p = get_niche_profile(niche)
    return p.get("posting_windows", [
        {"day": "Wednesday", "hour_utc": 14, "reason": "Mid-week peak engagement"},
    ])
