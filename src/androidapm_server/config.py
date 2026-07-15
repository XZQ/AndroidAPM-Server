"""Strict runtime configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated application settings using the ``APM_`` prefix."""

    model_config = SettingsConfigDict(
        env_prefix="APM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )

    environment: str = "local"
    log_level: str = "INFO"
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://androidapm:change-me@localhost:5432/androidapm"
    )
    otlp_logs_endpoint: str = "http://localhost:4318/v1/logs"
    otlp_headers_json: str = "{}"
    otlp_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    ingest_enabled: bool = True
    export_enabled: bool = True
    remote_config_signing_private_key_b64: SecretStr | None = None
    remote_config_public_key_id: str = "local-development-key"
    max_compressed_body_bytes: int = Field(default=1_048_576, ge=1_024, le=16_777_216)
    max_decompressed_body_bytes: int = Field(
        default=8_388_608, ge=1_024, le=67_108_864
    )
    max_batch_events: int = Field(default=1_000, ge=1, le=10_000)
    worker_batch_size: int = Field(default=100, ge=1, le=1_000)
    worker_lease_seconds: int = Field(default=60, ge=10, le=3_600)
    worker_poll_seconds: float = Field(default=1.0, ge=0.05, le=60)
    worker_max_attempts: int = Field(default=10, ge=1, le=100)
    delivered_retention_days: int = Field(default=7, ge=1, le=365)
    dead_letter_retention_days: int = Field(default=30, ge=1, le=3650)

    @field_validator("environment", "log_level")
    @classmethod
    def values_must_not_be_blank(cls, value: str) -> str:
        """Reject blank operational labels that would break filtering."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""
    return Settings()
