"""Centralized application settings for speech-jobs-backend."""

from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_DISTRIBUTION_NAME = "speech-jobs-backend"
FALLBACK_PROJECT_VERSION = "0.1.0"


def _get_project_version() -> str:
    """Return the installed project version from package metadata."""
    try:
        return version(PROJECT_DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return FALLBACK_PROJECT_VERSION


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_debug: bool = True
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/speech_jobs_backend"
    )
    storage_root: Path = Path("./storage")
    input_storage_dir: Path = Path("./storage/inputs")
    artifact_storage_dir: Path = Path("./storage/artifacts")
    default_profile: str = "balanced"
    device_preference: str = "auto"
    worker_poll_interval_seconds: int = 5
    worker_cleanup_every_n_jobs: int = 10
    worker_id: str = "local-worker-1"
    input_retention_days: int = 7
    artifact_retention_days: int = 7
    delete_input_on_success: bool = False
    store_intermediate_artifacts: bool = False
    max_upload_size_mb: int = 200
    allowed_audio_extensions: list[str] = Field(
        default_factory=lambda: ["m4a", "mp3", "wav", "flac", "ogg", "opus"]
    )
    huggingface_token: str | None = None
    app_version_override: str | None = Field(
        default=None,
        validation_alias="APP_VERSION",
        repr=False,
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def app_version(self) -> str:
        """Return the effective application version."""
        return self.app_version_override or _get_project_version()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def app_title(self) -> str:
        """Return the public application title."""
        return PROJECT_DISTRIBUTION_NAME

    @field_validator("allowed_audio_extensions", mode="before")
    @classmethod
    def _normalize_allowed_audio_extensions(
        cls, value: str | list[str] | tuple[str, ...]
    ) -> list[str]:
        """Normalize the allowed-audio-extension setting."""
        if isinstance(value, str):
            items = value.split(",")
        else:
            items = value

        normalized = [item.strip().lower() for item in items if item.strip()]
        return normalized


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance for the current process."""
    return Settings()
