"""Configuration management using Pydantic Settings."""

import json
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API Keys
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")

    # Paths
    output_dir: Path = Field(default=Path("output"), alias="OUTPUT_DIR")
    assets_dir: Path = Field(default=Path("assets"), alias="ASSETS_DIR")
    temp_dir: Path = Field(default=Path("temp"), alias="TEMP_DIR")

    # Audio Settings
    tts_model: str = Field(default="gpt-4o-mini-tts", alias="TTS_MODEL")
    quant_voice: str = Field(default="echo", alias="QUANT_VOICE")
    hustler_voice: str = Field(default="fable", alias="HUSTLER_VOICE")
    crossfade_ms: int = Field(default=200, alias="CROSSFADE_MS")
    bg_music_intro_duration_ms: int = Field(default=5000, alias="BG_MUSIC_INTRO_DURATION_MS")
    bg_music_intro_volume_db: int = Field(default=-12, alias="BG_MUSIC_INTRO_VOLUME_DB")
    bg_music_background_volume_db: int = Field(default=-30, alias="BG_MUSIC_BACKGROUND_VOLUME_DB")
    bg_music_fade_duration_ms: int = Field(default=2000, alias="BG_MUSIC_FADE_DURATION_MS")

    # Video Settings (16:9 landscape for regular YouTube)
    video_width: int = Field(default=1920, alias="VIDEO_WIDTH")
    video_height: int = Field(default=1080, alias="VIDEO_HEIGHT")
    max_duration_seconds: int = Field(default=300, alias="MAX_DURATION_SECONDS")
    background_color: str = Field(default="#1a1a2e", alias="BACKGROUND_COLOR")

    # Subtitle Settings
    subtitle_font: str = Field(default="Montserrat-Bold", alias="SUBTITLE_FONT")
    subtitle_font_size: int = Field(default=30, alias="SUBTITLE_FONT_SIZE")
    subtitle_margin_v: int = Field(default=140, alias="SUBTITLE_MARGIN_V")
    quant_color: str = Field(default="&H00FFD700", alias="QUANT_COLOR")  # Gold in ASS BGR format
    hustler_color: str = Field(default="&H0000FF00", alias="HUSTLER_COLOR")  # Green in ASS BGR format
    quant_dim_color: str = Field(default="&H00808060", alias="QUANT_DIM_COLOR")  # Dim upcoming karaoke
    hustler_dim_color: str = Field(default="&H00608060", alias="HUSTLER_DIM_COLOR")  # Dim upcoming karaoke

    # Whisper Settings
    whisper_enabled: bool = Field(default=True, alias="WHISPER_ENABLED")

    # Script Settings
    target_duration_seconds: int = Field(default=298, alias="TARGET_DURATION_SECONDS")
    target_word_count: int = Field(default=745, alias="TARGET_WORD_COUNT")
    llm_model: str = Field(default="gpt-4o", alias="LLM_MODEL")

    # RSS Feed Bundles Configuration
    rss_feed_bundles: Optional[str] = Field(default=None, alias="RSS_FEED_BUNDLES")
    rss_fetch_limit: int = Field(default=20, alias="RSS_FETCH_LIMIT")
    rss_cache_ttl_minutes: int = Field(default=30, alias="RSS_CACHE_TTL_MINUTES")

    # arXiv Configuration
    arxiv_categories: list[str] = Field(
        default_factory=lambda: ["cs.CR", "q-fin.TR", "cs.AI", "cs.LG"],
        alias="ARXIV_CATEGORIES"
    )
    arxiv_max_results: int = Field(default=10, alias="ARXIV_MAX_RESULTS")
    arxiv_lookback_days: int = Field(default=365, alias="ARXIV_LOOKBACK_DAYS")

    # Convergence Engine Configuration
    convergence_enabled: bool = Field(default=True, alias="CONVERGENCE_ENABLED")
    convergence_top_n_news: int = Field(default=5, alias="CONVERGENCE_TOP_N_NEWS")
    convergence_categories_per_news: int = Field(default=3, alias="CONVERGENCE_CATEGORIES_PER_NEWS")
    convergence_papers_per_category: int = Field(default=3, alias="CONVERGENCE_PAPERS_PER_CATEGORY")
    convergence_min_similarity: float = Field(default=0.35, alias="CONVERGENCE_MIN_SIMILARITY")
    convergence_min_relevance: float = Field(default=0.4, alias="CONVERGENCE_MIN_RELEVANCE")
    convergence_weight: float = Field(default=0.6, alias="CONVERGENCE_WEIGHT")
    convergence_cache_ttl_days: int = Field(default=30, alias="CONVERGENCE_CACHE_TTL_DAYS")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")

    # Podcast branding
    podcast_name: str = Field(default="The Agentic Ledger", alias="PODCAST_NAME")

    # YouTube Settings (deferred but included for completeness)
    youtube_client_secrets_path: Optional[Path] = Field(
        default=None, alias="YOUTUBE_CLIENT_SECRETS_PATH"
    )
    youtube_credentials_path: Optional[Path] = Field(
        default=None, alias="YOUTUBE_CREDENTIALS_PATH"
    )

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    gcp_project_id: Optional[str] = Field(default=None, alias="GCP_PROJECT_ID")

    def ensure_directories(self) -> None:
        """Create required directories if they don't exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    @property
    def lofi_music_path(self) -> Path:
        """Path to the lo-fi background music."""
        return self.assets_dir / "music" / "lofi_loop.mp3"

    @property
    def background_video_path(self) -> Path:
        """Path to the background video loop."""
        return self.assets_dir / "video" / "background_loop.mp4"

    @property
    def fonts_dir(self) -> Path:
        """Path to the fonts directory."""
        return self.assets_dir / "fonts"

    @property
    def font_path(self) -> Path:
        """Path to the subtitle font."""
        return self.fonts_dir / "Montserrat-Bold.ttf"

    @property
    def parsed_feed_bundles(self) -> list[dict]:
        """Parse RSS_FEED_BUNDLES JSON into list of bundle dicts."""
        if not self.rss_feed_bundles:
            return []
        try:
            return json.loads(self.rss_feed_bundles)
        except json.JSONDecodeError:
            return []


# Global settings instance
settings = Settings()
