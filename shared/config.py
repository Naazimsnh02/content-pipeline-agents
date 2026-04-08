"""
Centralised configuration via pydantic-settings.
All values can be set via environment variables or .env file.
"""
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Gemini / Google AI ──────────────────────────────────
    google_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    # Set to "true" to route through Vertex AI instead of AI Studio
    google_genai_use_vertexai: bool = False
    google_cloud_project: str = ""
    google_cloud_region: str = "us-central1"
    google_cloud_location: str = "us-central1"

    # ── Firestore ───────────────────────────────────────────
    firestore_database: str = "(default)"

    # ── Cloud Storage ───────────────────────────────────────
    gcs_bucket: str = ""

    # ── Research ────────────────────────────────────────────
    tavily_api_key: Optional[str] = None
    firecrawl_api_key: Optional[str] = None

    # ── TTS ─────────────────────────────────────────────────
    elevenlabs_api_key: Optional[str] = None
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"
    default_voice: str = "en-US-AriaNeural"

    # ── YouTube ─────────────────────────────────────────────
    youtube_client_id: Optional[str] = None
    youtube_client_secret: Optional[str] = None
    youtube_refresh_token: Optional[str] = None

    # ── Google Calendar ─────────────────────────────────────
    calendar_id: str = "primary"
    calendar_client_id: Optional[str] = None
    calendar_client_secret: Optional[str] = None
    calendar_refresh_token: Optional[str] = None

    # ── App ──────────────────────────────────────────────────
    app_name: str = "youtube-content-pipeline"
    demo_mode: bool = True          # Skip heavy TTS/video in demos
    log_level: str = "INFO"

    @property
    def has_youtube(self) -> bool:
        return bool(self.youtube_client_id and self.youtube_refresh_token)

    @property
    def has_calendar(self) -> bool:
        return bool(self.calendar_client_id and self.calendar_refresh_token)

    @property
    def has_elevenlabs(self) -> bool:
        return bool(self.elevenlabs_api_key)

    @property
    def has_tavily(self) -> bool:
        return bool(self.tavily_api_key)

    @property
    def has_firestore(self) -> bool:
        return bool(self.google_cloud_project)


settings = Settings()
