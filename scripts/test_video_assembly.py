"""Quick smoke test for the video assembly pipeline (ffmpeg + Ken Burns).

Creates dummy images and audio, then runs the full assembly to verify
ffmpeg works end-to-end on this machine.

Run with: .venv\Scripts\python.exe scripts/test_video_assembly.py
"""
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from PIL import Image

WORK = Path(tempfile.gettempdir()) / "content_pipeline" / "assembly_test"
WORK.mkdir(parents=True, exist_ok=True)

print("=== Video Assembly Smoke Test ===\n")

# 1. Create test images
print("1. Creating test images...")
colors = ["red", "green", "blue", "orange", "purple"]
image_paths = []
for i, color in enumerate(colors):
    p = WORK / f"test_scene_{i}.png"
    img = Image.new("RGB", (1080, 1920), color=color)
    img.save(str(p))
    image_paths.append(p)
    print(f"   Created {p.name} ({color})")

# 2. Create a short silent audio file with ffmpeg
print("\n2. Creating test audio...")
audio_path = WORK / "test_audio.mp3"
os.system(
    f'ffmpeg -y -f lavfi -i anullsrc=r=44100:cl=mono '
    f'-t 5 -q:a 9 "{audio_path}" -loglevel error'
)
if audio_path.exists():
    print(f"   Created {audio_path.name} ({audio_path.stat().st_size} bytes)")
else:
    print("   FAILED to create test audio")
    sys.exit(1)

# 3. Test Ken Burns animation on a single frame
print("\n3. Testing Ken Burns animation...")
from shared.media import animate_frame, _ffmpeg_available

if not _ffmpeg_available():
    print("   FAILED: ffmpeg not found on PATH")
    sys.exit(1)

anim_out = WORK / "test_anim.mp4"
animate_frame(image_paths[0], anim_out, duration=2.0, effect="zoom_in")
if anim_out.exists() and anim_out.stat().st_size > 0:
    print(f"   OK: {anim_out.name} ({anim_out.stat().st_size} bytes)")
else:
    print(f"   FAILED: animation output not created")
    sys.exit(1)

# 4. Test full assembly pipeline
print("\n4. Testing full video assembly (Ken Burns + audio)...")
from shared.media import assemble_video

result = assemble_video(
    image_paths=image_paths,
    audio_path=audio_path,
    duration_s=5.0,
    job_id="smoke_test",
)

if result.get("error"):
    print(f"   FAILED: {result['error']}")
    sys.exit(1)

video_path = result.get("video_path")
if video_path and Path(video_path).exists():
    size = Path(video_path).stat().st_size
    print(f"   OK: {Path(video_path).name} ({size:,} bytes)")
else:
    print(f"   FAILED: video file not created at {video_path}")
    sys.exit(1)

print(f"\n=== All assembly tests passed! ===")
print(f"Output: {video_path}")
