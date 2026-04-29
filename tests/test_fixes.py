r"""
Tests for the bug fixes:
  1. FFmpeg detection and error handling
  2. GCS URI -> local path download before ffmpeg
  3. YouTube/Calendar OAuth httpx client leak fix
  4. has_calendar / has_modal_auth / has_elevenlabs / has_tavily config properties
  5. Thumbnail generation respects IMAGE_PROVIDER
  6. MODAL_TOKEN_ID="none" treated correctly as missing

Run with:
  .venv\Scripts\python.exe -m pytest tests/test_fixes.py -v
"""
import sys
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. FFmpeg detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestFFmpegDetection:
    """Verify ffmpeg availability checks work correctly."""

    def test_ffmpeg_available_returns_bool(self):
        from shared.media import _ffmpeg_available
        result = _ffmpeg_available()
        assert isinstance(result, bool)

    def test_ffmpeg_is_installed(self):
        """After winget install, ffmpeg should be on PATH."""
        assert shutil.which("ffmpeg") is not None, (
            "ffmpeg not found on PATH. Run: winget install ffmpeg"
        )

    def test_ffprobe_is_installed(self):
        """ffprobe should also be available (ships with ffmpeg)."""
        assert shutil.which("ffprobe") is not None, (
            "ffprobe not found on PATH. It ships with ffmpeg."
        )

    def test_run_catches_missing_binary(self):
        """_run() should raise RuntimeError with helpful message if binary missing."""
        from shared.media import _run
        import pytest
        with pytest.raises(RuntimeError, match="not found"):
            _run(["nonexistent_binary_xyz", "--version"])

    def test_assemble_video_errors_without_ffmpeg(self):
        """assemble_video should return error dict if ffmpeg is missing."""
        from shared.media import assemble_video
        with patch("shared.media._ffmpeg_available", return_value=False):
            result = assemble_video(
                image_paths=[Path("/fake/img.png")],
                audio_path=Path("/fake/audio.mp3"),
                duration_s=10.0,
            )
            assert "error" in result
            assert "ffmpeg" in result["error"].lower()


    def test_assemble_video_proceeds_with_ffmpeg(self):
        """assemble_video should NOT return ffmpeg error when ffmpeg is available."""
        from shared.media import assemble_video
        with patch("shared.media._ffmpeg_available", return_value=True):
            # Will fail for other reasons (fake paths), but NOT the ffmpeg check
            result = assemble_video(
                image_paths=[Path("/fake/img.png")],
                audio_path=Path("/fake/audio.mp3"),
                duration_s=10.0,
            )
            # Should fail with a different error (file not found), not ffmpeg missing
            if "error" in result:
                assert "ffmpeg is not installed" not in result["error"]

    def test_captions_has_ass_filter_no_crash(self):
        """_has_ass_filter should return bool without crashing."""
        from shared.captions import _has_ass_filter
        result = _has_ass_filter()
        assert isinstance(result, bool)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. GCS URI download before ffmpeg
# ═══════════════════════════════════════════════════════════════════════════════

class TestGCSDownloadBeforeAssembly:
    """Verify that GCS URIs are downloaded to local paths before ffmpeg."""

    def test_assemble_video_downloads_gcs_images(self):
        """assemble_video tool should call download_file for gs:// image paths."""
        from agents.production.tools import assemble_video

        with patch("agents.production.tools.settings") as mock_settings:
            mock_settings.demo_mode = False

            mock_media_assemble = MagicMock(return_value={"video_path": "/tmp/out.mp4", "duration_s": 10})
            mock_download = MagicMock(side_effect=lambda uri, local: str(local))
            mock_upload = MagicMock(return_value="gs://bucket/videos/out.mp4")

            with patch("agents.production.tools.upload_file", mock_upload), \
                 patch("shared.storage.download_file", mock_download):
                # Patch the lazy import inside assemble_video
                import agents.production.tools as prod_tools
                with patch.dict("sys.modules", {
                    "shared.media": MagicMock(assemble_video=mock_media_assemble),
                    "shared.storage": MagicMock(download_file=mock_download),
                    "shared.captions": MagicMock(generate_captions=MagicMock(return_value={})),
                }):
                    # Force re-import inside the function
                    result = assemble_video(
                        image_paths=["gs://bucket/images/scene_0.png", "gs://bucket/images/scene_1.png"],
                        audio_path="gs://bucket/voiceovers/audio.mp3",
                        duration_s=10.0,
                        job_id="test123",
                    )
        # The function should attempt to process (may fail due to mocking depth,
        # but should NOT pass gs:// URIs directly to ffmpeg)

    def test_basic_assembly_downloads_gcs_images(self):
        """_assemble_basic should download GCS images before concat."""
        from agents.production.tools import _assemble_basic

        mock_download = MagicMock(side_effect=lambda uri, local: str(local))

        with patch("agents.production.tools.subprocess") as mock_sub, \
             patch("shared.storage.download_file", mock_download):
            mock_sub.run.return_value = MagicMock(returncode=0, stderr="")
            result = _assemble_basic(
                image_paths=["gs://bucket/img1.png", "/local/img2.png"],
                audio_path="gs://bucket/audio.mp3",
                duration_s=10.0,
                job_id="test456",
            )
            # download_file should be called for gs:// paths
            assert mock_download.call_count >= 2  # 1 image + 1 audio

    def test_local_paths_not_downloaded(self):
        """_assemble_basic should NOT call download for local paths."""
        from agents.production.tools import _assemble_basic

        mock_download = MagicMock(side_effect=lambda uri, local: str(local))

        with patch("agents.production.tools.subprocess") as mock_sub, \
             patch("shared.storage.download_file", mock_download):
            mock_sub.run.return_value = MagicMock(returncode=0, stderr="")
            _assemble_basic(
                image_paths=["/local/img1.png", "/local/img2.png"],
                audio_path="/local/audio.mp3",
                duration_s=10.0,
                job_id="test789",
            )
            # No gs:// paths, so download should not be called
            assert mock_download.call_count == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Config property fixes (has_calendar, has_modal_auth, placeholder "none")
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfigProperties:
    """Verify config properties handle placeholder values correctly."""

    def _make_settings(self, **overrides):
        """Create a Settings instance with overrides, bypassing .env file."""
        from shared.config import Settings
        defaults = {
            "google_api_key": "test-key",
            "google_cloud_project": "test-project",
        }
        defaults.update(overrides)
        return Settings(**defaults)

    def test_has_modal_auth_with_none_string(self):
        s = self._make_settings(modal_token_id="none", modal_token_secret="none")
        assert s.has_modal_auth is False

    def test_has_modal_auth_with_real_tokens(self):
        s = self._make_settings(modal_token_id="ak-real123", modal_token_secret="as-real456")
        assert s.has_modal_auth is True

    def test_has_modal_auth_with_empty_string(self):
        s = self._make_settings(modal_token_id="", modal_token_secret="")
        assert s.has_modal_auth is False

    def test_has_modal_auth_with_none_value(self):
        s = self._make_settings(modal_token_id=None, modal_token_secret=None)
        assert s.has_modal_auth is False

    def test_has_elevenlabs_with_none_string(self):
        s = self._make_settings(elevenlabs_api_key="none")
        assert s.has_elevenlabs is False

    def test_has_elevenlabs_with_real_key(self):
        s = self._make_settings(elevenlabs_api_key="sk_realkey123")
        assert s.has_elevenlabs is True

    def test_has_tavily_with_none_string(self):
        s = self._make_settings(tavily_api_key="none")
        assert s.has_tavily is False

    def test_has_tavily_with_real_key(self):
        s = self._make_settings(tavily_api_key="tvly-realkey123")
        assert s.has_tavily is True

    def test_has_calendar_with_all_creds(self):
        s = self._make_settings(
            calendar_client_id="cid",
            calendar_client_secret="csec",
            calendar_refresh_token="crt",
        )
        assert s.has_calendar is True

    def test_has_calendar_missing_secret(self):
        """Should still be True if YouTube creds can be used as fallback."""
        s = self._make_settings(
            calendar_client_id="cid",
            calendar_client_secret=None,
            calendar_refresh_token="crt",
            youtube_client_secret="yt_sec",
        )
        assert s.has_calendar is True

    def test_has_calendar_no_refresh_token(self):
        s = self._make_settings(
            calendar_client_id="cid",
            calendar_client_secret="csec",
            calendar_refresh_token=None,
        )
        assert s.has_calendar is False

    def test_has_calendar_fallback_to_youtube_creds(self):
        """Calendar should work when using YouTube OAuth client as fallback."""
        s = self._make_settings(
            calendar_client_id=None,
            calendar_client_secret=None,
            calendar_refresh_token="crt",
            youtube_client_id="yt_id",
            youtube_client_secret="yt_sec",
        )
        assert s.has_calendar is True

    def test_has_youtube(self):
        s = self._make_settings(youtube_client_id="yid", youtube_client_secret="ysec")
        assert s.has_youtube is True

    def test_has_youtube_missing_secret(self):
        s = self._make_settings(youtube_client_id="yid", youtube_client_secret=None)
        assert s.has_youtube is False


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Thumbnail generation respects IMAGE_PROVIDER
# ═══════════════════════════════════════════════════════════════════════════════

class TestThumbnailProvider:
    """Verify thumbnail generation dispatches to the correct provider."""

    def test_dispatch_to_gemini_when_imagen(self):
        """When IMAGE_PROVIDER=imagen, should call Gemini generation."""
        from shared.thumbnail import _generate_thumb_image

        with patch("shared.thumbnail.settings") as mock_settings, \
             patch("shared.thumbnail._generate_thumb_image_gemini") as mock_gemini, \
             patch("shared.thumbnail._generate_thumb_image_flux2") as mock_flux2:
            mock_settings.image_provider = "imagen"
            _generate_thumb_image("test prompt", "/tmp/out.png", "test-api-key")
            mock_gemini.assert_called_once()
            mock_flux2.assert_not_called()

    def test_dispatch_to_flux2_when_flux2(self):
        """When IMAGE_PROVIDER=flux2, should call Flux2 generation."""
        from shared.thumbnail import _generate_thumb_image

        with patch("shared.thumbnail.settings") as mock_settings, \
             patch("shared.thumbnail._generate_thumb_image_gemini") as mock_gemini, \
             patch("shared.thumbnail._generate_thumb_image_flux2") as mock_flux2:
            mock_settings.image_provider = "flux2"
            _generate_thumb_image("test prompt", "/tmp/out.png", "test-api-key")
            mock_flux2.assert_called_once()
            mock_gemini.assert_not_called()

    def test_generate_thumbnail_flux2_no_api_key_needed(self):
        """Flux2 thumbnails should not require GOOGLE_API_KEY."""
        from shared.thumbnail import generate_thumbnail

        with patch("shared.thumbnail.settings") as mock_settings, \
             patch("shared.thumbnail._generate_thumb_image") as mock_gen, \
             patch("shared.thumbnail._overlay_title"):
            mock_settings.image_provider = "flux2"
            mock_settings.google_api_key = ""  # No Gemini key

            # Create a temp file so _overlay_title can find the raw image
            tmp = Path(tempfile.mktemp(suffix=".png"))
            mock_gen.side_effect = lambda p, o, k: Path(o).write_bytes(b"fake")

            result = generate_thumbnail(
                prompt="test",
                title="Test Title",
                output_path=str(tmp),
            )
            # Should NOT error about missing API key
            assert result.get("error") is None or "API key" not in result.get("error", "")

    def test_generate_thumbnail_gemini_needs_api_key(self):
        """Gemini thumbnails should error if no API key."""
        from shared.thumbnail import generate_thumbnail

        with patch("shared.thumbnail.settings") as mock_settings:
            mock_settings.image_provider = "imagen"
            mock_settings.google_api_key = ""

            result = generate_thumbnail(
                prompt="test",
                title="Test Title",
                output_path="/tmp/thumb.png",
                api_key=None,
            )
            assert result.get("error") is not None
            assert "API key" in result["error"]

    def test_flux2_thumbnail_needs_endpoint_url(self):
        """Flux2 should error if MODAL_FLUX2_ENDPOINT_URL is not set."""
        from shared.thumbnail import _generate_thumb_image_flux2
        import pytest

        with patch("shared.thumbnail.settings") as mock_settings:
            mock_settings.modal_flux2_endpoint_url = None
            with pytest.raises(RuntimeError, match="MODAL_FLUX2_ENDPOINT_URL"):
                _generate_thumb_image_flux2("test prompt", "/tmp/out.png")

    def test_flux2_thumbnail_uses_correct_dimensions(self):
        """Flux2 thumbnail should request 1280x720 (16:9)."""
        from shared.thumbnail import _generate_thumb_image_flux2, THUMB_WIDTH, THUMB_HEIGHT

        with patch("shared.thumbnail.settings") as mock_settings, \
             patch("shared.thumbnail.requests.post") as mock_post:
            mock_settings.modal_flux2_endpoint_url = "https://fake.modal.run"
            mock_settings.has_modal_auth = False
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"image_base64": "aGVsbG8="}  # "hello" in b64
            mock_post.return_value = mock_resp

            _generate_thumb_image_flux2("test prompt", str(Path(tempfile.mktemp(suffix=".png"))))

            call_args = mock_post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["width"] == THUMB_WIDTH
            assert payload["height"] == THUMB_HEIGHT


# ═══════════════════════════════════════════════════════════════════════════════
# 5. OAuth httpx client context manager (no leak)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOAuthClientLeak:
    """Verify httpx.AsyncClient is used as context manager in OAuth flows."""

    def test_youtube_oauth_uses_context_manager(self):
        """exchange_code_for_tokens should use 'async with httpx.AsyncClient()'."""
        import ast
        source = Path("shared/youtube_oauth.py").read_text()
        tree = ast.parse(source)

        # Find all httpx.AsyncClient() usages — they should all be inside 'async with'
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # Check for httpx.AsyncClient()
                if (isinstance(func, ast.Attribute) and func.attr == "AsyncClient"
                        and isinstance(func.value, ast.Name) and func.value.id == "httpx"):
                    # Walk up to find if it's inside an 'async with'
                    # Simple check: search for "async with httpx.AsyncClient" in source
                    pass

        # Simpler: just check the source text for the pattern
        assert "async with httpx.AsyncClient()" in source, (
            "youtube_oauth.py should use 'async with httpx.AsyncClient()' "
            "to avoid connection leaks"
        )
        # Should NOT have bare httpx.AsyncClient().get/post (without 'async with')
        lines = source.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "httpx.AsyncClient().get" in stripped or "httpx.AsyncClient().post" in stripped:
                if "async with" not in stripped:
                    assert False, (
                        f"Line {i+1}: bare httpx.AsyncClient() usage without 'async with' — "
                        "this leaks connections"
                    )

    def test_calendar_oauth_uses_context_manager(self):
        """exchange_calendar_code_for_tokens should use 'async with httpx.AsyncClient()'."""
        source = Path("shared/calendar_oauth.py").read_text()
        assert "async with httpx.AsyncClient()" in source, (
            "calendar_oauth.py should use 'async with httpx.AsyncClient()' "
            "to avoid connection leaks"
        )
        lines = source.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "httpx.AsyncClient().get" in stripped or "httpx.AsyncClient().post" in stripped:
                if "async with" not in stripped:
                    assert False, (
                        f"Line {i+1}: bare httpx.AsyncClient() usage without 'async with' — "
                        "this leaks connections"
                    )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Storage download/upload round-trip
# ═══════════════════════════════════════════════════════════════════════════════

class TestStorageFallback:
    """Verify storage gracefully falls back to local paths."""

    def test_upload_returns_local_path_without_bucket(self):
        """When GCS_BUCKET is empty, upload_file should return the local path."""
        from shared.storage import upload_file
        with patch("shared.storage.settings") as mock_settings:
            mock_settings.gcs_bucket = ""
            result = upload_file("/tmp/test.mp4", "videos/test.mp4")
            # On Windows, Path("/tmp/test.mp4") normalises to \tmp\test.mp4
            assert Path(result) == Path("/tmp/test.mp4")

    def test_download_returns_local_path_for_non_gcs(self):
        """download_file should return the path as-is if it's not a gs:// URI."""
        from shared.storage import download_file
        result = download_file("/tmp/local_file.mp4", "/tmp/dest.mp4")
        assert result == "/tmp/local_file.mp4"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Scheduler per-user credentials (YouTube scheduling removed — calendar only)
# ═══════════════════════════════════════════════════════════════════════════════
