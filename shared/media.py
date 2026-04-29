"""Ken Burns animation and production-grade video assembly.

Provides frame animation with zoom/pan effects, caption burn-in via ASS
subtitles, and background music ducking during speech regions.

Ported from youtube-shorts-pipeline verticals/broll.py + verticals/assemble.py.
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920

_WORK_DIR = Path(tempfile.gettempdir()) / "content_pipeline"


def _ffmpeg_available() -> bool:
    """Return True if ffmpeg is on PATH."""
    return shutil.which("ffmpeg") is not None


def _run(cmd: list, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a subprocess command with standard timeout and error logging."""
    logger.debug("Running: %s", " ".join(str(c) for c in cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=cwd)
    except FileNotFoundError:
        logger.error("Command not found: %s — is ffmpeg installed and on PATH?", cmd[0])
        raise RuntimeError(
            f"'{cmd[0]}' not found. Install ffmpeg and ensure it is on your PATH. "
            "On Windows: winget install ffmpeg  or  choco install ffmpeg"
        )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "(no stderr)"
        stdout = result.stdout.strip() if result.stdout else "(no stdout)"
        logger.error(
            "Command failed (rc=%d): %s\nstderr: %s\nstdout: %s\ncmd: %s",
            result.returncode, cmd[0], stderr, stdout,
            " ".join(str(c) for c in cmd),
        )
    return result


def _esc(path: Path) -> str:
    """Escape a path for ffmpeg concat demuxer (single-quote safe)."""
    return str(path).replace("'", "'\\''")


# ── Ken Burns animation ─────────────────────────────────────────────────────


def animate_frame(
    img_path: Path,
    out_path: Path,
    duration: float,
    effect: str = "zoom_in",
) -> None:
    """Ken Burns animation on a single frame image.

    Uses scale → dynamic-crop to achieve smooth sub-pixel zoom/pan.
    The crop window is driven by ffmpeg's float ``t`` timestamp so
    movement is perfectly continuous.

    Effects: ``zoom_in``, ``pan_right``, ``zoom_out``.
    """
    fps = 30
    frames = int(duration * fps)
    w, h = VIDEO_WIDTH, VIDEO_HEIGHT
    D = duration

    def even(n: int) -> int:
        return n + n % 2

    # Dynamic zoom range — motion speed stays consistent across clip lengths.
    MIN_ZOOM_RANGE = 0.20
    zoom_range = max(MIN_ZOOM_RANGE, 2.0 / w * frames)
    ZOOM = min(1.50, 1.0 + zoom_range)
    zr = round(ZOOM - 1.0, 4)

    wl = even(int(w * ZOOM))
    hl = even(int(h * ZOOM))

    if effect == "zoom_in":
        vf = (
            f"scale=w='trunc({w}*(1+{zr}*t/{D})/2)*2':"
            f"h='trunc({h}*(1+{zr}*t/{D})/2)*2':eval=frame,"
            f"crop={w}:{h}:x='(in_w-{w})/2':y='(in_h-{h})/2'"
        )
    elif effect == "pan_right":
        dx = wl - w
        dy = hl - h
        vf = (
            f"scale={wl}:{hl},"
            f"crop={w}:{h}:x='{dx}*(t/{D})':y='{dy}/2'"
        )
    else:  # zoom_out
        vf = (
            f"scale=w='trunc({w}*(1+{zr}*(1-t/{D}))/2)*2':"
            f"h='trunc({h}*(1+{zr}*(1-t/{D}))/2)*2':eval=frame,"
            f"crop={w}:{h}:x='(in_w-{w})/2':y='(in_h-{h})/2'"
        )

    _run([
        "ffmpeg", "-loop", "1", "-i", str(img_path),
        "-vf", vf, "-t", str(duration), "-r", str(fps),
        "-pix_fmt", "yuv420p", str(out_path), "-y", "-loglevel", "error",
    ])


# ── Audio helpers ────────────────────────────────────────────────────────────


def get_audio_duration(path: Path) -> float:
    """Get duration of an audio file in seconds via ffprobe."""
    result = _run([
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(path),
    ])
    return float(result.stdout.strip())


def build_duck_filter(
    speech_regions: list[tuple[float, float]],
    buffer: float = 0.3,
    vol_speech: float = 0.12,
    vol_gap: float = 0.25,
) -> str:
    """Build ffmpeg volume filter expression for music ducking.

    Args:
        speech_regions: List of (start, end) tuples in seconds.
        buffer: Extra seconds of padding around each region.
        vol_speech: Music volume during speech.
        vol_gap: Music volume during gaps.

    Returns:
        An ffmpeg volume filter string, e.g.
        ``volume='if(between(t,0.00,5.50)+between(t,6.20,12.30), 0.12, 0.25)':eval=frame``
    """
    conditions = "+".join(
        f"between(t,{max(0, s - buffer):.2f},{e + buffer:.2f})"
        for s, e in speech_regions
    )
    return f"volume='if({conditions}, {vol_speech}, {vol_gap})':eval=frame"


# ── Video assembly ───────────────────────────────────────────────────────────


def assemble_video(
    image_paths: list[Path],
    audio_path: Path,
    duration_s: float,
    ass_path: str | None = None,
    music_path: str | None = None,
    duck_filter: str | None = None,
    job_id: str = "",
) -> dict:
    """Full assembly pipeline: animate frames → concat → mux audio/music/captions.

    Returns:
        ``{"video_path": str, "duration_s": float, "assembled": True}``
        or ``{"error": str}`` on failure.
    """
    if not _ffmpeg_available():
        return {
            "error": (
                "ffmpeg is not installed or not on PATH. "
                "Install it with: winget install ffmpeg  or  choco install ffmpeg  (Windows), "
                "brew install ffmpeg (macOS), apt install ffmpeg (Linux)."
            )
        }
    try:
        work = _WORK_DIR / (job_id or "default")
        work.mkdir(parents=True, exist_ok=True)

        # Validate input files exist
        for i, img in enumerate(image_paths):
            if not Path(img).exists():
                return {"error": f"Image file not found: {img} (index {i}). If this is a gs:// URI, it needs to be downloaded to a local path first."}
        if not Path(audio_path).exists():
            return {"error": f"Audio file not found: {audio_path}. If this is a gs:// URI, it needs to be downloaded to a local path first."}

        num_images = len(image_paths)
        per_frame = duration_s / num_images + 0.1
        effects = ["zoom_in", "pan_right", "zoom_out"]

        # 1. Animate each frame with Ken Burns effect
        animated: list[Path] = []
        for i, img in enumerate(image_paths):
            anim = work / f"anim_{i}.mp4"
            animate_frame(img, anim, per_frame, effects[i % len(effects)])
            if not anim.exists():
                return {"error": f"Ken Burns animation failed for image {i}: {img}. Check ffmpeg logs above."}
            animated.append(anim)

        # 2. Concat animated segments via ffmpeg concat demuxer
        concat_file = work / "concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{_esc(p)}'" for p in animated),
            encoding="utf-8",
        )

        merged_video = work / "merged_video.mp4"
        _run([
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            str(merged_video), "-y", "-loglevel", "error",
        ])

        if not merged_video.exists():
            return {"error": "Video concat step failed — merged_video.mp4 was not created. Check ffmpeg logs."}

        # 3. Build final ffmpeg command
        out_path = work / f"final_{job_id}.mp4"

        # Determine video filter (captions via ASS)
        # Copy ASS file into the work dir so it sits next to the other
        # build artifacts.  Then build a platform-safe absolute path for
        # the ass= filter:
        #   - Forward slashes (ffmpeg accepts them everywhere)
        #   - Escaped colons (on Windows the drive-letter colon, e.g.
        #     C:, is misread as a filter-option separator by ffmpeg)
        # On Linux there are no colons in paths so the replace is a no-op.
        vf_parts: list[str] = []
        ass_used = False
        if ass_path and Path(ass_path).exists():
            local_ass = work / Path(ass_path).name
            if Path(ass_path).resolve() != local_ass.resolve():
                import shutil as _shutil
                _shutil.copy2(ass_path, local_ass)
                logger.info("Copied ASS file to work dir: %s → %s", ass_path, local_ass)
            # Absolute path, forward slashes, colon-escaped (Windows-safe, Linux no-op)
            ass_filter_path = str(local_ass.resolve()).replace("\\", "/").replace(":", "\\:")
            vf_parts.append(f"ass='{ass_filter_path}'")
            ass_used = True
            logger.info("ASS filter path: %s", ass_filter_path)
        vf = ",".join(vf_parts) if vf_parts else None

        if music_path and Path(music_path).exists():
            # Three inputs: video, voiceover, music
            music_filter = f"[2:a]aloop=loop=-1:size=2e+09,atrim=0:{duration_s}"
            if duck_filter:
                music_filter += f",{duck_filter}"
            music_filter += "[music]"
            audio_filter = (
                f"{music_filter};[1:a][music]amix=inputs=2"
                f":duration=first:dropout_transition=2:normalize=0[aout]"
            )

            cmd = [
                "ffmpeg", "-i", str(merged_video), "-i", str(audio_path),
                "-stream_loop", "-1", "-i", str(music_path),
                "-filter_complex", audio_filter,
            ]
            if vf:
                cmd += ["-vf", vf]
            cmd += [
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest",
                str(out_path), "-y", "-loglevel", "error",
            ]
        else:
            # Two inputs: video + voiceover (no music)
            cmd = ["ffmpeg", "-i", str(merged_video), "-i", str(audio_path)]
            if vf:
                cmd += ["-vf", vf]
            cmd += [
                "-c:v", "libx264" if vf else "copy",
                "-c:a", "aac", "-shortest",
                str(out_path), "-y", "-loglevel", "error",
            ]

        # Run final mux — no need for cwd trick since ASS uses absolute path
        mux_result = _run(cmd)

        if not out_path.exists():
            stderr_tail = (mux_result.stderr or "")[-800:]
            # If captions were used and the mux failed, retry without captions
            if ass_used and mux_result.returncode != 0:
                logger.warning("Final mux failed (likely ASS filter issue), retrying without captions. stderr: %s", stderr_tail)
                if music_path and Path(music_path).exists():
                    cmd_retry = [
                        "ffmpeg", "-i", str(merged_video), "-i", str(audio_path),
                        "-stream_loop", "-1", "-i", str(music_path),
                        "-filter_complex", audio_filter,
                        "-map", "0:v", "-map", "[aout]",
                        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest",
                        str(out_path), "-y", "-loglevel", "error",
                    ]
                else:
                    cmd_retry = [
                        "ffmpeg", "-i", str(merged_video), "-i", str(audio_path),
                        "-c:v", "copy", "-c:a", "aac", "-shortest",
                        str(out_path), "-y", "-loglevel", "error",
                    ]
                _run(cmd_retry)
                if out_path.exists():
                    logger.info("Retry without captions succeeded: %s", out_path)
                else:
                    return {"error": f"Final video mux failed even without captions — {out_path.name} was not created. ffmpeg stderr: {stderr_tail}"}
            else:
                return {"error": f"Final video mux failed — {out_path.name} was not created. ffmpeg stderr: {stderr_tail}"}

        logger.info("Video assembled: %s (%d bytes)", out_path, out_path.stat().st_size)
        return {"video_path": str(out_path), "duration_s": duration_s, "assembled": True}

    except Exception as exc:
        logger.exception("Assembly failed")
        return {"error": str(exc)}
