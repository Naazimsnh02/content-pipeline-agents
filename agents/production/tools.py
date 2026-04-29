"""
Production Agent tools — TTS, image generation, video assembly, YouTube upload,
captions (Whisper ASS/SRT), thumbnails (Gemini + Pillow), Ken Burns animation.
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
import base64
import requests
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from shared.config import settings
from shared.database import db
from shared.niches import get_caption_style, get_visual_style, get_voice_style
from shared.storage import upload_file

logger = logging.getLogger(__name__)

_WORK_DIR = Path(tempfile.gettempdir()) / "content_pipeline"
_WORK_DIR.mkdir(exist_ok=True)


# ── Voiceover (Edge TTS / ElevenLabs) ───────────────────────────────────────

def generate_voiceover(script_text: str, voice: Optional[str] = None, job_id: str = "", niche: str = "general") -> dict:
    """
    Convert script text to speech audio using Edge TTS (free) or ElevenLabs (premium).

    Args:
        script_text: The full voiceover script text.
        voice: Voice name (Edge TTS format, e.g. "en-US-AriaNeural"). Uses niche default if not set.
        job_id: Optional job ID for file naming.
        niche: Content niche — used to select the default voice from the niche YAML profile.

    Returns:
        A dict with the output audio file path and duration estimate.
    """
    # Resolve voice: explicit arg > niche profile > settings default
    if not voice:
        niche_voice = get_voice_style(niche)
        voice = niche_voice.get("voice_id") or settings.default_voice
    if settings.demo_mode:
        fake_path = str(_WORK_DIR / f"voiceover_{job_id or int(time.time())}.mp3")
        word_count = len(script_text.split())
        duration_s = word_count / 3.0  # ~3 words/sec
        return {
            "audio_path": fake_path,
            "duration_s": round(duration_s, 1),
            "voice": voice,
            "demo": True,
            "message": f"[DEMO] Voiceover skipped. Would generate {duration_s:.0f}s audio.",
        }

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
            gcs_uri = upload_file(output_path, f"voiceovers/{Path(output_path).name}")
            return {"audio_path": output_path, "audio_gcs_uri": gcs_uri, "duration_s": round(word_count / 3.0, 1), "provider": "elevenlabs"}
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
        gcs_uri = upload_file(output_path, f"voiceovers/{Path(output_path).name}")
        return {"audio_path": output_path, "audio_gcs_uri": gcs_uri, "duration_s": round(word_count / 3.0, 1), "provider": "edge_tts"}
    except Exception as exc:
        return {"error": f"TTS failed: {exc}", "audio_path": None}


# ── Image Generation (Gemini Imagen) ─────────────────────────────────────────

def generate_scene_images(scene_prompts: list[str], job_id: str = "", niche: str = "general") -> dict:
    """
    Generate images for each video scene using Gemini Imagen via the new google-genai SDK.

    Args:
        scene_prompts: List of detailed image prompts (1 per scene, max 6).
        job_id: Optional job ID for file naming.
        niche: Content niche — used to inject visual style into prompts.

    Returns:
        A dict with 'image_paths' list of generated image file paths.
    """
    # Enrich prompts with niche visual style
    visual = get_visual_style(niche)
    style_suffix = f", {visual['visual_style']}, {visual['mood']} mood"
    enriched_prompts = [p + style_suffix if not p.endswith(style_suffix) else p for p in scene_prompts]

    if settings.demo_mode:
        fake_paths = [
            str(_WORK_DIR / f"scene_{i}_{job_id or int(time.time())}.png")
            for i in range(len(enriched_prompts))
        ]
        return {
            "image_paths": fake_paths,
            "count": len(fake_paths),
            "demo": True,
            "message": f"[DEMO] Image generation skipped. Would generate {len(enriched_prompts)} {settings.image_provider} images.",
        }

    # Dispatch to the configured provider
    if settings.image_provider == "flux2":
        return _generate_images_flux2(enriched_prompts, job_id)
    elif settings.image_provider == "gemini":
        return _generate_images_gemini(enriched_prompts, job_id)
    else:
        return _generate_images_imagen(enriched_prompts, job_id)


def _generate_images_imagen(scene_prompts: list[str], job_id: str = "") -> dict:
    """Internal helper for Google Imagen generation."""
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
                location=settings.effective_image_location,
            )
            logger.info("Using Vertex AI for image generation [project=%s, location=%s]",
                        settings.google_cloud_project, settings.effective_image_location)
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
                gcs_uri = upload_file(path, f"images/{Path(path).name}")
                image_paths.append(gcs_uri)
                # Respect rate limits for small projects
                time.sleep(2)

        return {"image_paths": image_paths, "count": len(image_paths)}
    except Exception as exc:
        logger.error("Image generation failed (Imagen): %s", exc, exc_info=True)
        return {"error": str(exc), "image_paths": []}


def _generate_images_gemini(scene_prompts: list[str], job_id: str = "") -> dict:
    """Generate images using Gemini native image generation (e.g. gemini-2.5-flash-image).

    Uses generate_content with response_modalities=["IMAGE", "TEXT"] which works
    on both Vertex AI and AI Studio via the google-genai SDK.
    Includes retry with exponential backoff for 429 rate-limit errors.
    """
    if not settings.google_api_key and not settings.google_genai_use_vertexai:
        return {"error": "GOOGLE_API_KEY not set and Vertex AI not enabled", "image_paths": []}

    try:
        from google import genai
        from google.genai import types

        # Initialize the GenAI client
        if settings.google_genai_use_vertexai:
            client = genai.Client(
                vertexai=True,
                project=settings.google_cloud_project,
                location=settings.effective_image_location,
            )
            logger.info("Using Vertex AI for Gemini image generation [project=%s, location=%s]",
                        settings.google_cloud_project, settings.effective_image_location)
        else:
            client = genai.Client(api_key=settings.google_api_key)
            logger.info("Using AI Studio for Gemini image generation")

        model_id = settings.gemini_image_model
        image_paths = []

        for i, prompt in enumerate(scene_prompts[:6]):
            full_prompt = f"Generate a high-quality vertical 9:16 portrait image: {prompt}"

            logger.info("Generating Gemini image %d/%d with %s: %s",
                        i + 1, len(scene_prompts), model_id, full_prompt[:60] + "...")

            # Retry with exponential backoff for rate limits (429)
            max_retries = 4
            response = None
            for attempt in range(1, max_retries + 1):
                try:
                    response = client.models.generate_content(
                        model=model_id,
                        contents=full_prompt,
                        config=types.GenerateContentConfig(
                            response_modalities=["IMAGE", "TEXT"],
                            image_config=types.ImageConfig(
                                aspect_ratio="9:16",
                            ),
                        ),
                    )
                    break  # Success — exit retry loop
                except Exception as api_exc:
                    is_rate_limit = "429" in str(api_exc) or "RESOURCE_EXHAUSTED" in str(api_exc)
                    if is_rate_limit and attempt < max_retries:
                        wait = 2 ** attempt * 5  # 10s, 20s, 40s
                        logger.warning(
                            "Rate limited on image %d (attempt %d/%d), waiting %ds...",
                            i + 1, attempt, max_retries, wait,
                        )
                        time.sleep(wait)
                    else:
                        logger.error("Image %d failed after %d attempts: %s", i + 1, attempt, api_exc)
                        break  # Non-retryable error or exhausted retries

            # Extract image data from response parts
            saved = False
            if response and response.candidates:
                for part in response.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.data:
                        path = str(_WORK_DIR / f"scene_{i}_{job_id or int(time.time())}.png")
                        Path(path).write_bytes(part.inline_data.data)
                        gcs_uri = upload_file(path, f"images/{Path(path).name}")
                        image_paths.append(gcs_uri)
                        saved = True
                        break

            if not saved:
                logger.warning("No image data in Gemini response for scene %d (skipping)", i)

            # Pause between requests to stay under rate limits
            time.sleep(5)

        return {"image_paths": image_paths, "count": len(image_paths)}
    except Exception as exc:
        logger.error("Image generation failed (Gemini native): %s", exc, exc_info=True)
        return {"error": str(exc), "image_paths": []}


def _generate_images_flux2(scene_prompts: list[str], job_id: str = "") -> dict:
    """Internal helper for Flux.2 generation via Modal.com endpoint."""
    if not settings.modal_flux2_endpoint_url:
        return {"error": "MODAL_FLUX_ENDPOINT_URL not set in .env", "image_paths": []}

    headers = {"Content-Type": "application/json"}
    if settings.has_modal_auth:
        headers["Authorization"] = f"Bearer {settings.modal_token_id}:{settings.modal_token_secret}"

    image_paths = []
    try:
        for i, prompt in enumerate(scene_prompts[:6]):
            out_path = _WORK_DIR / f"scene_{i}_{job_id or int(time.time())}.png"
            
            payload = {
                "operation": "generate",
                "prompt": f"{prompt}, vertical 9:16 portrait format",
                "width": 1080,
                "height": 1920,
            }

            logger.info("Generating Flux2 image %d/%d via Modal...", i+1, len(scene_prompts))
            r = requests.post(settings.modal_flux2_endpoint_url, json=payload, headers=headers, timeout=300)
            
            if r.status_code != 200:
                logger.error("Modal Flux2 failed (HTTP %d): %s", r.status_code, r.text[:200])
                continue

            result = r.json()
            img_b64 = result.get("image_base64")
            output_url = result.get("output_url")

            if img_b64:
                out_path.write_bytes(base64.b64decode(img_b64))
                gcs_uri = upload_file(out_path, f"images/{out_path.name}")
                image_paths.append(gcs_uri)
            elif output_url:
                img_r = requests.get(output_url, timeout=60)
                img_r.raise_for_status()
                out_path.write_bytes(img_r.content)
                gcs_uri = upload_file(out_path, f"images/{out_path.name}")
                image_paths.append(gcs_uri)
            else:
                logger.warning("No image data in Modal response for scene %d", i)

        return {"image_paths": image_paths, "count": len(image_paths)}
    except Exception as exc:
        logger.error("Image generation failed (Flux2/Modal): %s", exc, exc_info=True)
        return {"error": str(exc), "image_paths": []}


# ── Video Assembly (Ken Burns + captions + music ducking) ────────────────────

def assemble_video(
    image_paths: list[str],
    audio_path: str,
    duration_s: float,
    captions_text: str = "",
    job_id: str = "",
) -> dict:
    """
    Assemble final video from images + audio with Ken Burns animation,
    burned-in ASS captions, and background music with voice ducking.

    Args:
        image_paths: List of image file paths (one per scene).
        audio_path: Path to the voiceover MP3.
        duration_s: Total video duration in seconds.
        captions_text: Optional SRT caption text (unused — captions now via ASS).
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
            "message": f"[DEMO] Video assembly skipped. Would produce {duration_s:.0f}s 1080x1920 Ken Burns video with captions.",
        }

    if not image_paths:
        return {"error": "No images provided for assembly"}

    try:
        from shared.media import assemble_video as media_assemble
        from shared.storage import download_file

        # Generate captions from audio
        ass_path = None
        srt_path = None
        try:
            from shared.captions import generate_captions
            audio_p = Path(audio_path)
            cap_result = generate_captions(
                audio_path=audio_p,
                work_dir=audio_p.parent,
                highlight_color="#FFFF00",
                words_per_group=4,
            )
            ass_path = cap_result.get("ass_path")
            srt_path = cap_result.get("srt_path")
            if ass_path:
                logger.info("Captions generated: ASS=%s, SRT=%s", ass_path, srt_path)
        except Exception as exc:
            logger.warning("Caption generation failed (continuing without): %s", exc)

        # Select background music (bundled tracks with voice ducking)
        music_path = None
        duck_filter = None
        try:
            music_dir = Path(__file__).resolve().parent.parent.parent / "music"
            if music_dir.exists():
                import random
                tracks = sorted(music_dir.glob("*.mp3"))
                if tracks:
                    music_path = str(random.choice(tracks))
                    # Build speech regions for ducking from caption words
                    from shared.captions import _whisper_word_timestamps
                    from shared.media import build_duck_filter
                    # Reuse caption words if available, else just set flat volume
                    duck_filter = f"volume=0.12"
                    logger.info("Background music selected: %s", Path(music_path).name)
        except Exception as exc:
            logger.warning("Music selection failed (continuing without): %s", exc)

        # Assemble with Ken Burns — download GCS images to local paths first
        local_image_paths = []
        for i, p in enumerate(image_paths):
            if str(p).startswith("gs://"):
                local_p = _WORK_DIR / f"dl_scene_{i}_{job_id or int(time.time())}.png"
                logger.info("Downloading GCS image %d to %s", i, local_p)
                downloaded = download_file(str(p), local_p)
                local_image_paths.append(Path(downloaded))
            else:
                local_image_paths.append(Path(p))

        # Download audio from GCS if needed
        local_audio = Path(audio_path)
        if str(audio_path).startswith("gs://"):
            local_audio = _WORK_DIR / f"dl_audio_{job_id or int(time.time())}.mp3"
            logger.info("Downloading GCS audio to %s", local_audio)
            download_file(str(audio_path), local_audio)

        logger.info("Assembling video with %d local images, audio=%s", len(local_image_paths), local_audio)

        result = media_assemble(
            image_paths=local_image_paths,
            audio_path=local_audio,
            duration_s=duration_s,
            ass_path=ass_path,
            music_path=music_path,
            duck_filter=duck_filter,
            job_id=job_id or str(int(time.time())),
        )
        # Upload assembled video to GCS
        if result.get("video_path"):
            gcs_uri = upload_file(result["video_path"], f"videos/{Path(result['video_path']).name}")
            result["video_gcs_uri"] = gcs_uri
        # Attach SRT path for YouTube upload
        if srt_path:
            result["srt_path"] = srt_path
        return result
    except Exception as exc:
        logger.error("Ken Burns assembly failed, falling back to basic: %s", exc)
        return _assemble_basic(image_paths, audio_path, duration_s, job_id)


def _assemble_basic(
    image_paths: list[str],
    audio_path: str,
    duration_s: float,
    job_id: str = "",
) -> dict:
    """Fallback basic ffmpeg concat assembly (no Ken Burns)."""
    import shutil
    if not shutil.which("ffmpeg"):
        return {
            "error": (
                "ffmpeg is not installed or not on PATH. "
                "Install it with: winget install ffmpeg  or  choco install ffmpeg  (Windows), "
                "brew install ffmpeg (macOS), apt install ffmpeg (Linux)."
            ),
            "video_path": None,
        }

    from shared.storage import download_file

    output_path = str(_WORK_DIR / f"video_{job_id or int(time.time())}.mp4")
    per_image_dur = duration_s / len(image_paths)
    concat_file = str(_WORK_DIR / f"concat_{job_id or int(time.time())}.txt")

    # Download GCS images to local paths
    local_images = []
    for i, img in enumerate(image_paths):
        if str(img).startswith("gs://"):
            local_p = str(_WORK_DIR / f"dl_basic_{i}_{job_id or int(time.time())}.png")
            img = download_file(str(img), local_p)
        local_images.append(img)

    # Download audio from GCS if needed
    local_audio = audio_path
    if str(audio_path).startswith("gs://"):
        local_audio = str(_WORK_DIR / f"dl_basic_audio_{job_id or int(time.time())}.mp3")
        download_file(str(audio_path), local_audio)

    with open(concat_file, "w") as f:
        for img in local_images:
            f.write(f"file '{img}'\n")
            f.write(f"duration {per_image_dur}\n")

    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-i", local_audio,
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return {"error": f"ffmpeg failed: {result.stderr[-500:]}", "video_path": None}

        # Upload to GCS so download endpoints can find it
        gcs_uri = upload_file(output_path, f"videos/{Path(output_path).name}")
        return {"video_path": output_path, "video_gcs_uri": gcs_uri, "duration_s": duration_s, "assembled": True}
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
    user_id: str = "",
) -> dict:
    """
    Upload a video to YouTube using the YouTube Data API v3.

    Uses per-user OAuth tokens if user_id is provided and the user has
    connected their YouTube account. Falls back to global .env credentials
    if no per-user tokens are found.

    Args:
        video_path: Path to the MP4 file to upload.
        title: YouTube video title.
        description: YouTube video description.
        tags: List of tags for discoverability.
        privacy: "private" | "unlisted" | "public" (default: private for review).
        job_id: Optional job ID for tracking.
        user_id: Firebase UID of the user — used to look up per-user YouTube tokens.

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

    # Verify the file exists and is readable before attempting upload
    video_file = Path(video_path)
    if not video_file.exists():
        return {"error": f"Video file not found: {video_path}. The assembly step may have failed or used a different output path."}
    if not video_file.is_file() or video_file.stat().st_size == 0:
        return {"error": f"Video file is empty or invalid: {video_path}"}

    # Try per-user YouTube credentials first
    creds = None
    creds_source = "none"

    if user_id:
        try:
            from shared.youtube_oauth import get_user_youtube_credentials
            creds = get_user_youtube_credentials(user_id)
            if creds:
                creds_source = "per-user"
                logger.info("Using per-user YouTube credentials for user %s", user_id)
        except Exception as exc:
            logger.warning("Failed to load per-user YouTube creds: %s", exc)

    # Fall back to global .env credentials
    if not creds and settings.has_youtube:
        try:
            from google.oauth2.credentials import Credentials
            creds = Credentials(
                token=None,
                refresh_token=settings.youtube_refresh_token,
                client_id=settings.youtube_client_id,
                client_secret=settings.youtube_client_secret,
                token_uri="https://oauth2.googleapis.com/token",
            )
            creds_source = "global"
            logger.info("Using global YouTube credentials from .env")
        except Exception as exc:
            logger.warning("Failed to load global YouTube creds: %s", exc)

    if not creds:
        return {
            "error": "YouTube not connected. Please connect your YouTube account in Settings, "
                     "or configure YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN in .env.",
        }

    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

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
            "credentials_source": creds_source,
        }
    except Exception as exc:
        error_str = str(exc)
        logger.error("YouTube upload failed: %s", exc)
        if "invalid_grant" in error_str:
            return {
                "error": "YouTube OAuth token expired or revoked. Please reconnect YouTube in Settings (the user needs to re-authorize via /auth/youtube).",
                "youtube_video_id": None,
                "auth_error": True,
            }
        return {"error": error_str, "youtube_video_id": None}


# ── Save Video Job State ──────────────────────────────────────────────────────

# ── Captions (Whisper ASS/SRT) ───────────────────────────────────────────────

def generate_captions_from_audio(audio_path: str, highlight_color: str = "", niche: str = "general") -> dict:
    """
    Generate word-level captions from a voiceover audio file.
    Produces ASS (for burn-in) and SRT (for YouTube upload) subtitle files.

    Args:
        audio_path: Path to the voiceover MP3/WAV file.
        highlight_color: Hex color for the active word highlight. Uses niche profile default if empty.
        niche: Content niche — used to select caption color from the YAML profile.

    Returns:
        A dict with ass_path, srt_path, and word_count.
    """
    # Resolve highlight color from niche profile if not explicitly provided
    if not highlight_color:
        caption_style = get_caption_style(niche)
        highlight_color = caption_style.get("highlight_color", "#FFFF00")
    if settings.demo_mode:
        return {
            "ass_path": None,
            "srt_path": None,
            "word_count": 0,
            "demo": True,
            "message": "[DEMO] Caption generation skipped. Would produce ASS + SRT captions.",
        }

    try:
        from shared.captions import generate_captions
        audio_p = Path(audio_path)
        result = generate_captions(
            audio_path=audio_p,
            work_dir=audio_p.parent,
            highlight_color=highlight_color,
            words_per_group=4,
        )
        return {
            "ass_path": result.get("ass_path"),
            "srt_path": result.get("srt_path"),
            "word_count": len(result.get("words", [])),
            "message": f"Generated captions with {len(result.get('words', []))} words.",
        }
    except Exception as exc:
        logger.error("Caption generation failed: %s", exc)
        return {"error": str(exc), "ass_path": None, "srt_path": None}


# ── Thumbnail Generation ─────────────────────────────────────────────────────

def generate_video_thumbnail(
    prompt: str,
    title: str,
    job_id: str = "",
) -> dict:
    """
    Generate a YouTube thumbnail with AI image generation and text overlay.
    Uses Gemini native image generation for the background, then overlays
    the video title with bold text and drop shadow.

    Args:
        prompt: Image generation prompt (e.g. "Cinematic dark background with glowing AI circuits").
        title: Video title to overlay on the thumbnail.
        job_id: Optional job ID for file naming.

    Returns:
        A dict with thumbnail_path.
    """
    if settings.demo_mode:
        fake_path = str(_WORK_DIR / f"thumb_{job_id or int(time.time())}.png")
        return {
            "thumbnail_path": fake_path,
            "demo": True,
            "message": f"[DEMO] Thumbnail generation skipped. Would generate 1280x720 thumbnail.",
        }

    try:
        from shared.thumbnail import generate_thumbnail
        output_path = str(_WORK_DIR / f"thumb_{job_id or int(time.time())}.png")
        result = generate_thumbnail(
            prompt=prompt,
            title=title,
            output_path=output_path,
        )
        # Upload thumbnail to GCS
        if result.get("thumbnail_path"):
            gcs_uri = upload_file(result["thumbnail_path"], f"thumbnails/{Path(result['thumbnail_path']).name}")
            result["thumbnail_gcs_uri"] = gcs_uri
        return result
    except Exception as exc:
        logger.error("Thumbnail generation failed: %s", exc)
        return {"error": str(exc), "thumbnail_path": None}


# ── Save Video Job State ──────────────────────────────────────────────────────

def save_video_job(
    script_id: str,
    status: str,
    video_job_id: Optional[str] = None,
    youtube_video_id: Optional[str] = None,
    youtube_url: Optional[str] = None,
    error: Optional[str] = None,
    current_stage: str = "",
    video_gcs_uri: Optional[str] = None,
    voiceover_gcs_uri: Optional[str] = None,
    image_gcs_uris: Optional[List[str]] = None,
    thumbnail_gcs_uri: Optional[str] = None,
    pipeline_job_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict:
    """
    Create or update a video job in Firestore, storing GCS URIs for all media.

    When ``video_job_id`` is provided the existing document is updated in-place
    (preserving ``pipeline_job_id`` and other fields set on creation).  When it
    is omitted a new document is created — this should only happen on the very
    first call (status="processing").

    Args:
        script_id: The script this video is based on.
        status: "pending" | "processing" | "done" | "failed".
        video_job_id: Existing video job ID to update. Pass the value returned
            by the first save_video_job call so subsequent calls update the
            same document rather than creating a new one.
        youtube_video_id: YouTube video ID after upload.
        youtube_url: Full YouTube URL.
        error: Error message if failed.
        current_stage: Current pipeline stage (tts/images/assembly/upload).
        video_gcs_uri: GCS URI of the assembled MP4.
        voiceover_gcs_uri: GCS URI of the voiceover MP3.
        image_gcs_uris: List of GCS URIs for scene images.
        thumbnail_gcs_uri: GCS URI of the thumbnail PNG.
        pipeline_job_id: The pipeline job ID from the coordinator — used to
            link this video back to the originating pipeline_jobs document so
            the download endpoint can find the correct video.
        user_id: Firebase UID of the user who owns this video — required for
            ownership-based queries in the download/thumbnail endpoints.

    Returns:
        A dict with the video_job_id.
    """
    from shared.models import VideoJob

    if video_job_id:
        # Update the existing document — load it first so we don't lose fields
        # (e.g. pipeline_job_id, user_id) that were set on creation.
        existing = db.get("videos", video_job_id) or {}
        updates: dict = {
            "script_id": script_id,
            "status": status,
            "updated_at": datetime.utcnow().isoformat(),
        }
        if current_stage:
            updates["current_stage"] = current_stage
        if youtube_video_id is not None:
            updates["youtube_video_id"] = youtube_video_id
        if youtube_url is not None:
            updates["youtube_url"] = youtube_url
        if error is not None:
            updates["error"] = error
        if video_gcs_uri is not None:
            updates["video_path"] = video_gcs_uri
        if voiceover_gcs_uri is not None:
            updates["voiceover_path"] = voiceover_gcs_uri
        if image_gcs_uris is not None:
            updates["image_paths"] = image_gcs_uris
        if thumbnail_gcs_uri is not None:
            updates["thumbnail_gcs_uri"] = thumbnail_gcs_uri
        # Only set pipeline_job_id if explicitly provided (don't overwrite existing)
        if pipeline_job_id is not None:
            updates["pipeline_job_id"] = pipeline_job_id
        if user_id is not None:
            updates["user_id"] = user_id
        merged = {**existing, **updates}
        db.save("videos", video_job_id, merged)
        doc_id = video_job_id
    else:
        # First call — create a new document
        job = VideoJob(
            script_id=script_id,
            pipeline_job_id=pipeline_job_id,
            user_id=user_id,
            status=status,
            current_stage=current_stage,
            video_path=video_gcs_uri,
            voiceover_path=voiceover_gcs_uri,
            image_paths=image_gcs_uris or [],
            thumbnail_gcs_uri=thumbnail_gcs_uri,
            youtube_video_id=youtube_video_id,
            youtube_url=youtube_url,
            error=error,
            updated_at=datetime.utcnow(),
        )
        data = job.model_dump(mode="json")
        db.save("videos", job.id, data)
        doc_id = job.id

    return {
        "video_job_id": doc_id,
        "script_id": script_id,
        "status": status,
        "youtube_url": youtube_url,
        "video_gcs_uri": video_gcs_uri,
        "message": f"Video job {doc_id} saved with status '{status}'.",
    }
