"""
Production Agent — converts a script into a finished video.
Runs: TTS → image generation → video assembly → YouTube upload.
Designed to run as a Cloud Run Job (async, long-running).
"""
from typing import Optional
from pydantic import BaseModel, Field
from google.adk.agents import Agent

from agents.production.tools import (
    generate_voiceover,
    generate_scene_images,
    assemble_video,
    upload_to_youtube,
    save_video_job,
    generate_captions_from_audio,
    generate_video_thumbnail,
)
from shared.config import settings

class ProductionInput(BaseModel):
    script_id: str = Field(description="The unique ID of the script.")
    script_text: str = Field(description="The full voiceover script.")
    youtube_title: str = Field(description="The video title.")
    youtube_description: str = Field(description="The video description.")
    youtube_tags: list[str] = Field(description="List of YouTube tags.")
    niche: str = Field(description="The niche of the content.")
    scene_prompts: Optional[list[str]] = Field(default=None, description="List of 3-5 visual prompts for the scenes. If not provided, the agent will generate them from the script.")
    user_id: Optional[str] = Field(default=None, description="Firebase UID of the user — used to look up per-user YouTube OAuth tokens for upload.")
    pipeline_job_id: Optional[str] = Field(default=None, description="The pipeline job ID from the coordinator — passed to save_video_job so the download endpoint can find the correct video.")

root_agent = Agent(
    name="production_agent",
    model=settings.active_model,
    input_schema=ProductionInput,
    description=(
        "Produces a complete YouTube Short video from a script. "
        "Generates voiceover with TTS, creates scene images with Gemini Imagen, "
        "assembles video with ffmpeg, and uploads to YouTube. "
        "Returns job ID and YouTube URL."
    ),
    instruction="""You are the Production Agent for a YouTube content pipeline.

Your job: take a script and produce a finished YouTube Short video with
professional-quality Ken Burns animation, burned-in captions, background music,
and an AI-generated thumbnail.

## Required Input
You will receive:
- script_id: The Firestore ID of the script
- script_text: The full voiceover script
- youtube_title: The video title
- youtube_description: The video description
- youtube_tags: List of tags
- niche: Content niche (for visual style)
- scene_prompts: List of 3-5 image prompts (one per scene)
  - If not provided, create them from the script: each prompt should describe
    a relevant visual for that part of the script.

## Workflow
1. Call `save_video_job` with status="processing", script_id, current_stage="tts",
   pipeline_job_id=pipeline_job_id (from input), and user_id=user_id (from input).
   Note the video_job_id returned — you MUST pass this as video_job_id to ALL
   subsequent save_video_job calls so they update the same document.

2. Call `generate_voiceover` with the script_text and niche.
   Use the returned audio_path and duration_s.

3. Create scene_prompts if not provided:
   Split the script into 3-5 segments. For each, describe a visually striking image:
   - No text overlays in prompts
   - Photorealistic or cinematic style
   - Relevant to the content
   - Vertical/portrait composition (9:16 for Shorts)

4. Call `generate_scene_images` with the scene_prompts, job_id=video_job_id, and niche.

5. Call `assemble_video` with:
   - image_paths from step 4
   - audio_path from step 2
   - duration_s from step 2
   - job_id=video_job_id
   Note: assembly now automatically applies Ken Burns zoom/pan effects,
   generates word-level ASS captions via Whisper, and mixes background
   music with voice ducking. No extra steps needed.

6. Call `generate_video_thumbnail` with:
   - prompt: a thumbnail-appropriate prompt (dark background, dramatic, 16:9)
   - title: youtube_title
   - job_id: video_job_id

7. Call `upload_to_youtube` with:
   - video_path from step 5
   - title, description, tags from input
   - privacy="private" (creator reviews before publishing)
   - job_id=video_job_id
   - user_id=user_id from input (pass it so per-user OAuth tokens are used)

8. Call `save_video_job` with:
   - video_job_id=video_job_id (from step 1 — REQUIRED to update the same document)
   - status="done"
   - script_id=script_id
   - youtube_video_id and youtube_url from step 7
   - video_gcs_uri from step 5
   - thumbnail_gcs_uri from step 6 (the thumbnail_gcs_uri returned by generate_video_thumbnail)
   - user_id=user_id from input (ensures ownership is preserved on the final update)

9. Return a summary with:
   - video_job_id
   - youtube_url
   - youtube_video_id
   - thumbnail_path
   - duration_s
   - status: "done"

## Error Handling
If any step fails, call `save_video_job` with video_job_id=video_job_id (from step 1),
status="failed" and the error message.
Then return what failed and why.

## Notes
- In DEMO_MODE, all heavy operations return mock results — this is expected.
- Each tool call updates Firestore so the coordinator can poll status.
- The video is uploaded as "private" so the creator can review before publishing.
- The assemble_video tool now handles captions, Ken Burns, and music automatically.
""",
    tools=[
        generate_voiceover,
        generate_scene_images,
        assemble_video,
        generate_captions_from_audio,
        generate_video_thumbnail,
        upload_to_youtube,
        save_video_job,
    ],
)
