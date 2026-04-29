"""Thumbnail generation — supports Gemini and Flux2 (Modal) image generation + Pillow text overlay.

Respects the IMAGE_PROVIDER setting:
  - "imagen" / "gemini" → Gemini native image generation REST API
  - "flux2"             → Modal Flux2 endpoint
"""

import base64
import logging
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from shared.config import settings

logger = logging.getLogger(__name__)

THUMB_WIDTH = 1280
THUMB_HEIGHT = 720


def _generate_thumb_image_gemini(prompt: str, output_path: str, api_key: str) -> None:
    """Generate a 16:9 thumbnail via Gemini native image generation using the google-genai SDK.

    Works with both Vertex AI and AI Studio. Uses the model configured in
    settings.gemini_image_model (default: gemini-2.5-flash-image).

    Retries up to 3 times with a 2-second delay on failure.
    """
    from google import genai
    from google.genai import types

    # Initialize client based on backend
    if settings.google_genai_use_vertexai:
        client = genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.effective_image_location,
        )
        logger.info("Thumbnail: using Vertex AI [project=%s, location=%s]",
                     settings.google_cloud_project, settings.effective_image_location)
    else:
        client = genai.Client(api_key=api_key)
        logger.info("Thumbnail: using AI Studio")

    model_id = settings.gemini_image_model

    last_error = None
    for attempt in range(1, 4):
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=f"Generate a 16:9 landscape image: {prompt}",
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                    image_config=types.ImageConfig(
                        aspect_ratio="16:9",
                    ),
                ),
            )

            # Extract image data from response
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.data:
                        Path(output_path).write_bytes(part.inline_data.data)
                        return

            raise RuntimeError("No image data found in Gemini response")
        except Exception as exc:
            last_error = exc
            logger.warning("Gemini thumbnail attempt %d/3 failed: %s", attempt, exc)
            if attempt < 3:
                time.sleep(2)

    raise last_error  # type: ignore[misc]


def _generate_thumb_image_flux2(prompt: str, output_path: str) -> None:
    """Generate a 16:9 thumbnail via Modal Flux2 endpoint.

    Retries up to 3 times with a 2-second delay on failure.
    """
    if not settings.modal_flux2_endpoint_url:
        raise RuntimeError("MODAL_FLUX2_ENDPOINT_URL not set in .env — cannot generate Flux2 thumbnail")

    headers = {"Content-Type": "application/json"}
    if settings.has_modal_auth:
        headers["Authorization"] = f"Bearer {settings.modal_token_id}:{settings.modal_token_secret}"

    payload = {
        "operation": "generate",
        "prompt": f"{prompt}, 16:9 landscape format, thumbnail style",
        "width": THUMB_WIDTH,
        "height": THUMB_HEIGHT,
    }

    last_error = None
    for attempt in range(1, 4):
        try:
            logger.info("Generating Flux2 thumbnail (attempt %d/3)...", attempt)
            r = requests.post(
                settings.modal_flux2_endpoint_url,
                json=payload,
                headers=headers,
                timeout=300,
            )
            if r.status_code != 200:
                raise RuntimeError(f"Modal Flux2 HTTP {r.status_code}: {r.text[:200]}")

            result = r.json()
            img_b64 = result.get("image_base64")
            output_url = result.get("output_url")

            if img_b64:
                Path(output_path).write_bytes(base64.b64decode(img_b64))
                return
            elif output_url:
                img_r = requests.get(output_url, timeout=60)
                img_r.raise_for_status()
                Path(output_path).write_bytes(img_r.content)
                return
            else:
                raise RuntimeError("No image data in Modal Flux2 response")
        except Exception as exc:
            last_error = exc
            logger.warning("Flux2 thumbnail attempt %d/3 failed: %s", attempt, exc)
            if attempt < 3:
                time.sleep(2)

    raise last_error  # type: ignore[misc]


def _generate_thumb_image(prompt: str, output_path: str, api_key: str) -> None:
    """Generate a thumbnail image using the configured IMAGE_PROVIDER.

    Dispatches to Gemini or Flux2 based on settings.image_provider.
    """
    provider = settings.image_provider.lower()

    if provider == "flux2":
        logger.info("Using Flux2 (Modal) for thumbnail generation")
        _generate_thumb_image_flux2(prompt, output_path)
    else:
        logger.info("Using Gemini for thumbnail generation")
        _generate_thumb_image_gemini(prompt, output_path, api_key)


def _wrap_text(draw: ImageDraw.Draw, text: str, font, max_width: int) -> list[str]:
    """Word-wrap text for Pillow rendering."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _overlay_title(image_path: str, title: str, output_path: str) -> None:
    """Overlay bold title text with drop shadow on the thumbnail."""
    img = Image.open(image_path).convert("RGB")
    img = img.resize((THUMB_WIDTH, THUMB_HEIGHT), Image.LANCZOS)
    draw = ImageDraw.Draw(img)

    font_size = 64
    font = None
    for font_path in [
        # Windows
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/Arial.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        # macOS
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSDisplay.ttf",
    ]:
        try:
            font = ImageFont.truetype(font_path, font_size)
            break
        except (OSError, IOError):
            continue
    if font is None:
        font = ImageFont.load_default()

    max_width = THUMB_WIDTH - 80  # 40px padding each side
    lines = _wrap_text(draw, title, font, max_width)
    text_block = "\n".join(lines)

    bbox = draw.multiline_textbbox((0, 0), text_block, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (THUMB_WIDTH - text_w) // 2
    y = THUMB_HEIGHT - text_h - 60

    # Drop shadow
    draw.multiline_text(
        (x + 3, y + 3), text_block,
        fill=(0, 0, 0), font=font, align="center",
    )
    # Main text
    draw.multiline_text(
        (x, y), text_block,
        fill=(255, 255, 255), font=font, align="center",
    )

    img.save(output_path)


def generate_thumbnail(
    prompt: str, title: str, output_path: str, api_key: str | None = None
) -> dict:
    """Generate a YouTube thumbnail with AI image generation + text overlay.

    Respects IMAGE_PROVIDER setting: uses Flux2 (Modal) or Gemini accordingly.

    Returns {"thumbnail_path": str, "generated": True} on success,
    or {"error": str, "thumbnail_path": None} on failure.
    """
    api_key = api_key or settings.google_api_key
    provider = settings.image_provider.lower()

    # For Gemini, we need an API key (unless using Vertex AI with ADC)
    if provider != "flux2" and not api_key and not settings.google_genai_use_vertexai:
        return {"error": "No API key provided and GOOGLE_API_KEY not set (and Vertex AI not enabled)", "thumbnail_path": None}

    out = Path(output_path)
    raw_path = out.with_stem(out.stem + "_raw")

    try:
        logger.info("Generating thumbnail via %s...", provider)
        _generate_thumb_image(prompt, str(raw_path), api_key or "")

        logger.info("Adding title overlay...")
        _overlay_title(str(raw_path), title, str(out))

        logger.info("Thumbnail saved: %s (provider: %s)", out.name, provider)
        return {"thumbnail_path": str(out), "generated": True, "provider": provider}
    except Exception as exc:
        logger.error("Thumbnail generation failed (%s): %s", provider, exc)
        return {"error": str(exc), "thumbnail_path": None}
    finally:
        if raw_path.exists():
            raw_path.unlink()
