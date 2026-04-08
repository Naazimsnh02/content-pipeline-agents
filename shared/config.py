"""
Centralised configuration via pydantic-settings.
All values can be set via environment variables or .env file.

LLM Provider switching:
  LLM_PROVIDER=gemini            → uses GEMINI_MODEL via Google ADK (default)
  LLM_PROVIDER=openai_compatible → uses OPENAI_MODEL via LiteLLM with
                                    OPENAI_API_BASE as the custom base URL
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

    # ── LLM Provider ────────────────────────────────────────
    # "gemini" uses Google ADK with GEMINI_MODEL.
    # "openai_compatible" uses LiteLLM with OPENAI_MODEL + OPENAI_API_BASE.
    llm_provider: str = "gemini"

    # ── Gemini / Google AI ──────────────────────────────────
    google_api_key: str = ""
    gemini_model: str = "gemini-3-flash-preview"
    # Set to "true" to route through Vertex AI instead of AI Studio
    google_genai_use_vertexai: bool = False
    google_cloud_project: str = ""
    google_cloud_region: str = "us-central1"
    google_cloud_location: str = "us-central1"

    # ── OpenAI-Compatible Endpoint ──────────────────────────
    # Used when LLM_PROVIDER=openai_compatible.
    # OPENAI_API_BASE: custom base URL (e.g. Nebius, Ollama, vLLM, LM Studio)
    # OPENAI_API_KEY:  API key for that endpoint
    # OPENAI_MODEL:    model name as understood by the endpoint
    openai_api_base: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_model: str = "moonshotai/Kimi-K2.5"

    # ── Image Generation ────────────────────────────────────
    image_provider: str = "imagen"  # "imagen" or "flux2"
    modal_flux2_endpoint_url: Optional[str] = None
    modal_token_id: Optional[str] = None
    modal_token_secret: Optional[str] = None
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

    @property
    def active_model(self) -> str:
        """Returns the ADK model string to use for all agents.

        For Gemini: returns the model name directly (e.g. 'gemini-3-flash-preview').
        For OpenAI-compatible: returns 'openai/<model>' so ADK routes through
        LiteLLM, which picks up OPENAI_API_BASE and OPENAI_API_KEY automatically.
        """
        if self.llm_provider == "openai_compatible":
            return f"openai/{self.openai_model}"
        return self.gemini_model


def _apply_openai_env(s: Settings) -> None:
    """Mirrors OPENAI_API_BASE / OPENAI_API_KEY into os.environ so LiteLLM
    picks them up at call time (LiteLLM reads these env vars natively)."""
    import os
    if s.llm_provider == "openai_compatible":
        if s.openai_api_base:
            os.environ.setdefault("OPENAI_API_BASE", s.openai_api_base)
        if s.openai_api_key:
            os.environ.setdefault("OPENAI_API_KEY", s.openai_api_key)


settings = Settings()
_apply_openai_env(settings)
