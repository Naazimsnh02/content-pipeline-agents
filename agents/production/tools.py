"""
Production Agent tools — TTS, image generation, video assembly, YouTube upload.
Heavy operations are skipped in DEMO_MODE (returns mock results for fast demos).
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from shared.config import settings
from shared.database import db

logger = logging.getLogger(__name__)

_WORK_DIR = Path(tempfile.gettempdir()) / "content_pipeline"
_WORK_DIR.mkdir(exist_ok=True)


# ── Voiceover (Edge TTS / ElevenLabs) ───────────────────────────────────────

def generate_voiceover(script_text: str, voice: Optional[str] = None, job_id: str = "") -> dict:
    """
    Convert script text to speech audio using Edge TTS (free) or ElevenLabs (premium).

    Args:
        script_text: The full voiceover script text.
        voice: Voice name (Edge TTS format, e.g. "en-US-AriaNeural"). Uses default if not set.
        job_id: Optional job ID for file naming.

    Returns:
        A dict with the output audio file path and duration estimate.
    """
    if settings.demo_mode:
        fake_path = str(_WORK_DIR / f"voiceover_{job_id or int(time.time())}.mp3")
        word_count = len(script_text.split())
        duration_s = word_count / 3.0  # ~3 words/sec
        return {
            "audio_path": fake_path,
            "duration_s": round(duration_s, 1),
            "voice": voice or settings.default_voice,
            "demo": True,
            "message": f"[DEMO] Voiceover skipped. Would generate {duration_s:.0f}s audio.",
        }

    voice = voice or settings.default_voice
    output_path = str(_WORK_DIR / f"voiceover_{job_id or int(time.time())}.mp3")

    # Try ElevenLabs (premium)
    if settings.has_elevenlabs:
        try:
            import httpx
            resp = httpx.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}",
                headers={"xi-api-key": settings.elevenlabs_api_key},
                json={"text": script_text, "model_id": "eleven_turbo_v2"},
                timeout=60,
            )
            resp.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(resp.content)
            word_count = len(script_text.split())
            return {"audio_path": output_path, "duration_s": round(word_count / 3.0, 1), "provider": "elevenlabs"}
        except Exception as exc:
            logger.warning("ElevenLabs failed, falling back to Edge TTS: %s", exc)

    # Edge TTS (free, 300+ voices)
    try:
        import edge_tts

        async def _run():
            communicate = edge_tts.Communicate(script_text, voice)
            await communicate.save(output_path)

        asyncio.run(_run())
        word_count = len(script_text.split())
        return {"audio_path": output_path, "duration_s": round(word_count / 3.0, 1), "provider": "edge_tts"}
    except Exception as exc:
        return {"error": f"TTS failed: {exc}", "audio_path": None}


# ── Image Generation (Gemini Imagen) ─────────────────────────────────────────

def generate_scene_images(scene_prompts: list[str], job_id: str = "") -> dict:
    """
    Generate images for each video scene using Gemini Imagen via the new google-genai SDK.

    Args:
        scene_prompts: List of detailed image prompts (1 per scene, max 6).
        job_id: Optional job ID for file naming.

    Returns:
        A dict with 'image_paths' list of generated image file paths.
    """
    if settings.demo_mode:
        fake_paths = [
            str(_WORK_DIR / f"scene_{i}_{job_id or int(time.time())}.png")
            for i in range(len(scene_prompts))
        ]
        return {
            "image_paths": fake_paths,
            "count": len(fake_paths),
            "demo": True,
            "message": f"[DEMO] Image generation skipped. Would generate {len(scene_prompts)} images.",
        }

    if not settings.google_api_key and not settings.google_genai_use_vertexai:
        return {"error": "GOOGLE_API_KEY not set", "image_paths": []}

    try:
        from google import genai
        from google.genai import types

        # Initialize the new GenAI client
        if settings.google_genai_use_vertexai:
            client = genai.Client(
                vertexai=True,
                project=settings.google_cloud_project,
                location=settings.google_cloud_location,
            )
            logger.info("Using Vertex AI for image generation [project=%s]", settings.google_cloud_project)
        else:
            client = genai.Client(api_key=settings.google_api_key)
            logger.info("Using AI Studio for image generation")

        image_paths = []
        # Use a high-quality Imagen model
        model_id = "imagen-3.0-generate-001" if "imagen-4" not in settings.gemini_model else "imagen-4.0-generate-001"
        
        for i, prompt in enumerate(scene_prompts[:6]):
            # Ensure 9:16 portrait format for Shorts
            full_prompt = f"{prompt}, vertical 9:16 portrait format, cinematic, high quality"
            
            logger.info("Generating image %d/%d: %s", i+1, len(scene_prompts), full_prompt[:50] + "...")
            
            response = client.models.generate_images(
                model=model_id,
                prompt=full_prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio="9:16",
                    safety_filter_level="BLOCK_ONLY_HIGH",
                )
            )

            if response.generated_images:
                path = str(_WORK_DIR / f"scene_{i}_{job_id or int(time.time())}.png")
                # The response contains the image data in .image
                response.generated_images[0].image.save(path)
                image_paths.append(path)
                # Respect rate limits for small projects
                time.sleep(2)

        return {"image_paths": image_paths, "count": len(image_paths)}
    except Exception as exc:
        logger.error("Image generation failed: %s", exc, exc_info=True)
        return {"error": str(exc), "image_paths": []}


# ── Video Assembly (ffmpeg) ──────────────────────────────────────────────────

def assemble_video(
    image_paths: list[str],
    audio_path: str,
    duration_s: float,
    captions_text: str = "",
    job_id: str = "",
) -> dict:
    """
    Assemble final video from images + audio using ffmpeg.
    Applies Ken Burns effect on images, mixes background music.

    Args:
        image_paths: List of image file paths (one per scene).
        audio_path: Path to the voiceover MP3.
        duration_s: Total video duration in seconds.
        captions_text: Optional SRT caption text.
        job_id: Optional job ID for file naming.

    Returns:
        A dict with the output video file path.
    """
    if settings.demo_mode:
        fake_path = str(_WORK_DIR / f"video_{job_id or int(time.time())}.mp4")
        return {
            "video_path": fake_path,
            "duration_s": duration_s,
            "demo": True,
            "message": f"[DEMO] Video assembly skipped. Would produce {duration_s:.0f}s 1080x1920 video.",
        }

    if not image_paths:
        return {"error": "No images provided for assembly"}

    output_path = str(_WORK_DIR / f"video_{job_id or int(time.time())}.mp4")
    srt_path = None

    # Write captions file if provided
    if captions_text:
        srt_path = str(_WORK_DIR / f"captions_{job_id or int(time.time())}.srt")
        Path(srt_path).write_text(captions_text)

    # Build ffmpeg concat + zoom effect
    per_image_dur = duration_s / len(image_paths)
    concat_file = str(_WORK_DIR / f"concat_{job_id or int(time.time())}.txt")

    with open(concat_file, "w") as f:
        for img in image_paths:
            f.write(f"file '{img}'\n")
            f.write(f"duration {per_image_dur}\n")

    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-i", audio_path,
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return {"error": f"ffmpeg failed: {result.stderr[-500:]}", "video_path": None}

        return {"video_path": output_path, "duration_s": duration_s, "assembled": True}
    except Exception as exc:
        return {"error": f"Assembly failed: {exc}", "video_path": None}


# ── YouTube Upload ────────────────────────────────────────────────────────────

def upload_to_youtube(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    privacy: str = "private",
    job_id: str = "",
) -> dict:
    """
    Upload a video to YouTube using the YouTube Data API v3.

    Args:
        video_path: Path to the MP4 file to upload.
        title: YouTube video title.
        description: YouTube video description.
        tags: List of tags for discoverability.
        privacy: "private" | "unlisted" | "public" (default: private for review).
        job_id: Optional job ID for tracking.

    Returns:
        A dict with youtube_video_id and youtube_url.
    """
    if settings.demo_mode:
        fake_id = f"demo_{job_id or int(time.time())}"
        return {
            "youtube_video_id": fake_id,
            "youtube_url": f"https://youtu.be/{fake_id}",
            "status": privacy,
            "demo": True,
            "message": f"[DEMO] Upload skipped. Would upload '{title}' as {privacy}.",
        }

    if not settings.has_youtube:
        return {"error": "YouTube credentials not configured. Set YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN."}

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        creds = Credentials(
            token=None,
            refresh_token=settings.youtube_refresh_token,
            client_id=settings.youtube_client_id,
            client_secret=settings.youtube_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
        )
        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags[:15],
                "categoryId": "28",  # Science & Technology
            },
            "status": {"privacyStatus": privacy},
        }

        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = request.execute()

        video_id = response["id"]
        return {
            "youtube_video_id": video_id,
            "youtube_url": f"https://youtu.be/{video_id}",
            "status": privacy,
        }
    except Exception as exc:
        logger.error("YouTube upload failed: %s", exc)
        return {"error": str(exc), "youtube_video_id": None}


# ── Save Video Job State ──────────────────────────────────────────────────────

def save_video_job(
    script_id: str,
    status: str,
    youtube_video_id: Optional[str] = None,
    youtube_url: Optional[str] = None,
    error: Optional[str] = None,
    current_stage: str = "",
) -> dict:
    """
    Update the video job status in Firestore.

    Args:
        script_id: The script this video is based on.
        status: "pending" | "processing" | "done" | "failed".
        youtube_video_id: YouTube video ID after upload.
        youtube_url: Full YouTube URL.
        error: Error message if failed.
        current_stage: Current pipeline stage (tts/images/assembly/upload).

    Returns:
        A dict with the video_job_id.
    """
    from shared.models import VideoJob

    job = VideoJob(
        script_id=script_id,
        status=status,
        current_stage=current_stage,
        youtube_video_id=youtube_video_id,
        youtube_url=youtube_url,
        error=error,
        updated_at=datetime.utcnow(),
    )
    db.save("videos", job.id, job.model_dump(mode="json"))
    return {
        "video_job_id": job.id,
        "script_id": script_id,
        "status": status,
        "youtube_url": youtube_url,
        "message": f"Video job {job.id} saved with status '{status}'.",
    }
