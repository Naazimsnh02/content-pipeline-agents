"""
Production Agent — converts a script into a finished video.
Runs: TTS → image generation → video assembly → YouTube upload.
Designed to run as a Cloud Run Job (async, long-running).
"""
from google.adk.agents import Agent

from agents.production.tools import (
    generate_voiceover,
    generate_scene_images,
    assemble_video,
    upload_to_youtube,
    save_video_job,
)
from shared.config import settings

root_agent = Agent(
    name="production_agent",
    model=settings.gemini_model,
    description=(
        "Produces a complete YouTube Short video from a script. "
        "Generates voiceover with TTS, creates scene images with Gemini Imagen, "
        "assembles video with ffmpeg, and uploads to YouTube. "
        "Returns job ID and YouTube URL."
    ),
    instruction="""You are the Production Agent for a YouTube content pipeline.

Your job: take a script and produce a finished YouTube Short video.

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
1. Call `save_video_job` with status="processing", script_id, current_stage="tts".
   Note the video_job_id returned.

2. Call `generate_voiceover` with the script_text.
   Use the returned audio_path and duration_s.

3. Create scene_prompts if not provided:
   Split the script into 3-5 segments. For each, describe a visually striking image:
   - No text overlays in prompts
   - Photorealistic or cinematic style
   - Relevant to the content
   - Vertical/portrait composition

4. Call `generate_scene_images` with the scene_prompts and job_id=video_job_id.

5. Call `assemble_video` with:
   - image_paths from step 4
   - audio_path from step 2
   - duration_s from step 2
   - job_id=video_job_id

6. Call `upload_to_youtube` with:
   - video_path from step 5
   - title, description, tags from input
   - privacy="private" (creator reviews before publishing)
   - job_id=video_job_id

7. Call `save_video_job` with status="done", youtube_video_id and youtube_url from step 6.

8. Return a summary with:
   - video_job_id
   - youtube_url
   - youtube_video_id
   - duration_s
   - status: "done"

## Error Handling
If any step fails, call `save_video_job` with status="failed" and the error message.
Then return what failed and why.

## Notes
- In DEMO_MODE, all heavy operations return mock results — this is expected.
- Each tool call updates Firestore so the coordinator can poll status.
- The video is uploaded as "private" so the creator can review before publishing.
""",
    tools=[
        generate_voiceover,
        generate_scene_images,
        assemble_video,
        upload_to_youtube,
        save_video_job,
    ],
)
