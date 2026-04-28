"""Application settings (pydantic-settings). Loaded from `.env` locally and
from Cloud Run env bindings (incl. Secret Manager `secretKeyRef`) in cloud
environments. FR-041 startup gate: refuse to boot in cloud envs without an
explicit non-empty `ALLOWED_CALLERS` allow-list."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised when Settings validation fails at startup."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    port: int = 8080

    forecast_bucket: str = Field(min_length=1)

    expected_audience: str = Field(min_length=1)

    allowed_callers: Annotated[frozenset[str], NoDecode] = frozenset()

    model_cache_size: int = Field(default=100, ge=1)
    model_cache_ttl_seconds: int = Field(default=3600, ge=1)
    latest_pointer_ttl_seconds: int = Field(default=60, ge=1)

    sentry_dsn: str | None = None
    sentry_environment: str = "production"

    log_level: Literal["debug", "info", "warning", "error"] = "info"

    auth_bypass: bool = False

    git_sha: str = "unknown"

    forecast_allow_file_urls: bool = False

    gcs_fake_host: str | None = None

    @field_validator("allowed_callers", mode="before")
    @classmethod
    def _split_allowed_callers(cls, v: object) -> object:
        if isinstance(v, str):
            return frozenset(item.strip() for item in v.split(",") if item.strip())
        if v is None:
            return frozenset()
        return v

    @property
    def is_local_audience(self) -> bool:
        return self.expected_audience.startswith(("http://localhost", "http://127.0.0.1"))

    @model_validator(mode="after")
    def _enforce_cloud_env_invariants(self) -> Settings:
        if not self.is_local_audience and not self.allowed_callers:
            msg = (
                "ALLOWED_CALLERS must be a non-empty allow-list when "
                "EXPECTED_AUDIENCE is not localhost (FR-041)."
            )
            raise ConfigurationError(msg)

        if self.auth_bypass and not (self.is_local_audience and self.log_level == "debug"):
            msg = (
                "AUTH_BYPASS=1 is only honored when EXPECTED_AUDIENCE is "
                "localhost AND LOG_LEVEL=debug (R6 / FR-018)."
            )
            raise ConfigurationError(msg)

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
