"""Whisper word-level transcription, ASS subtitle generation, and SRT generation."""

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _has_ass_filter() -> bool:
    """Check if ffmpeg has libass (for ASS subtitle burn-in)."""
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not found on PATH — ASS filter unavailable")
        return False
    try:
        r = subprocess.run(
            ["ffmpeg", "-filters"],
            capture_output=True, text=True, timeout=5,
        )
        return "ass" in r.stdout
    except Exception:
        return False


def _whisper_word_timestamps(audio_path: Path, lang: str = "en") -> list[dict]:
    """Get word-level timestamps from Whisper.

    Returns list of {"word": str, "start": float, "end": float}.
    """
    try:
        import whisper
    except ImportError:
        logger.warning("Whisper not installed — skipping word timestamps")
        return []

    logger.info("Running Whisper for word-level timestamps...")
    model = whisper.load_model("base")
    result = model.transcribe(
        str(audio_path),
        language=lang[:2],
        word_timestamps=True,
    )

    words = []
    for segment in result.get("segments", []):
        for w in segment.get("words", []):
            words.append({
                "word": w["word"].strip(),
                "start": w["start"],
                "end": w["end"],
            })

    logger.info(f"Got {len(words)} word timestamps.")
    return words


def _group_words(words: list[dict], group_size: int = 4) -> list[list[dict]]:
    """Split words into fixed-size groups."""
    groups = []
    for i in range(0, len(words), group_size):
        groups.append(words[i:i + group_size])
    return groups


def _format_ass_time(seconds: float) -> str:
    """Format seconds to ASS timestamp: H:MM:SS.cc (centiseconds)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _generate_ass(
    words: list[dict],
    output_path: Path,
    video_width: int = 1080,
    video_height: int = 1920,
    highlight_color: str = "#FFFF00",
    group_size: int = 4,
) -> Path:
    """Generate ASS subtitle file with word-by-word color highlighting.

    White text for inactive words, highlighted color for current word.
    Semi-transparent background, positioned at lower third (~70% down).
    """
    margin_v = int(video_height * 0.25)  # ~75% down from top = 25% from bottom
    header = f"""[Script Info]
Title: Pipeline Captions
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,72,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,3,0,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Convert hex color #RRGGBB to ASS BGR format &H00BBGGRR&
    hc = highlight_color.lstrip("#")
    if len(hc) == 6:
        ass_highlight = f"&H00{hc[4:6]}{hc[2:4]}{hc[0:2]}&"
    else:
        ass_highlight = "&H0000FFFF&"  # fallback yellow

    groups = _group_words(words, group_size=group_size)
    events = []

    for group in groups:
        if not group:
            continue

        for active_idx, active_word in enumerate(group):
            start = active_word["start"]
            end = active_word["end"]

            parts = []
            for j, w in enumerate(group):
                if j == active_idx:
                    parts.append(f"{{\\c{ass_highlight}\\b1}}{w['word']}{{\\r}}")
                else:
                    parts.append(w["word"])

            text = " ".join(parts)
            events.append(
                f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},Default,,0,0,0,,{text}"
            )

    output_path.write_text(header + "\n".join(events), encoding="utf-8")
    logger.info(f"ASS captions saved: {output_path.name}")
    return output_path


def _generate_srt(words: list[dict], output_path: Path, group_size: int = 4) -> Path:
    """Generate standard SRT file from word timestamps."""
    groups = _group_words(words, group_size=group_size)
    lines = []

    for i, group in enumerate(groups, 1):
        if not group:
            continue
        start = group[0]["start"]
        end = group[-1]["end"]
        text = " ".join(w["word"] for w in group)

        start_ts = _srt_time(start)
        end_ts = _srt_time(end)
        lines.append(f"{i}\n{start_ts} --> {end_ts}\n{text}\n")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"SRT captions saved: {output_path.name}")
    return output_path


def _srt_time(seconds: float) -> str:
    """Format seconds to SRT timestamp: HH:MM:SS,mmm."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_captions(
    audio_path: Path,
    work_dir: Path,
    lang: str = "en",
    highlight_color: str = "#FFFF00",
    words_per_group: int = 4,
) -> dict:
    """Generate captions: ASS (for burn-in) + SRT (for upload).

    Returns dict with keys: words, srt_path, ass_path.
    """
    words = _whisper_word_timestamps(audio_path, lang)

    result = {"words": words}

    if not words:
        logger.warning("No word timestamps — skipping caption generation")
        return result

    # Generate SRT
    srt_path = work_dir / f"captions_{lang}.srt"
    _generate_srt(words, srt_path, group_size=words_per_group)
    result["srt_path"] = str(srt_path)

    # Generate ASS for burn-in
    ass_path = work_dir / f"captions_{lang}.ass"
    _generate_ass(words, ass_path, highlight_color=highlight_color, group_size=words_per_group)
    result["ass_path"] = str(ass_path)

    return result
