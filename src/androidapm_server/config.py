"""Strict runtime configuration loaded from environment variables."""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
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
    installation_hmac_keys_json: SecretStr = SecretStr("{}")
    installation_hmac_active_key_version: str = "v1"
    remote_config_signing_private_key_b64: SecretStr | None = None
    remote_config_public_key_id: str = "local-development-key"
    max_compressed_body_bytes: int = Field(default=1_048_576, ge=1_024, le=16_777_216)
    max_decompressed_body_bytes: int = Field(default=8_388_608, ge=1_024, le=67_108_864)
    max_batch_events: int = Field(default=1_000, ge=1, le=10_000)
    worker_batch_size: int = Field(default=100, ge=1, le=1_000)
    worker_lease_seconds: int = Field(default=60, ge=10, le=3_600)
    worker_poll_seconds: float = Field(default=1.0, ge=0.05, le=60)
    worker_max_attempts: int = Field(default=10, ge=1, le=100)
    metrics_bind: str = "127.0.0.1"
    worker_metrics_port: int = Field(default=9101, ge=0, le=65535)
    symbolizer_metrics_port: int = Field(default=9102, ge=0, le=65535)
    metrics_refresh_seconds: float = Field(default=15, ge=1, le=300)
    delivered_retention_days: int = Field(default=7, ge=1, le=365)
    dead_letter_retention_days: int = Field(default=30, ge=1, le=3650)
    retention_enabled: bool = True
    retention_batch_size: int = Field(default=1000, ge=1, le=5000)
    retention_interval_seconds: float = Field(default=60, ge=1, le=3600)
    inbox_max_live_events: int = Field(default=100_000, ge=1)
    inbox_max_raw_bytes: int = Field(default=10 * 1024**3, ge=1024)
    artifact_storage_path: Path = Path("artifacts")
    max_java_mapping_bytes: int = Field(
        default=32 * 1_024 * 1_024, ge=1_024, le=128 * 1_024 * 1_024
    )
    max_native_elf_bytes: int = Field(
        default=512 * 1_024 * 1_024, ge=1_024, le=1_024 * 1_024 * 1_024
    )
    symbolizer_batch_size: int = Field(default=20, ge=1, le=200)
    symbolizer_lease_seconds: int = Field(default=120, ge=10, le=3_600)
    symbolizer_poll_seconds: float = Field(default=1.0, ge=0.05, le=60)
    symbolizer_max_attempts: int = Field(default=5, ge=1, le=20)
    symbolizer_timeout_seconds: float = Field(default=30.0, ge=1, le=300)
    symbolization_enabled: bool = False
    retrace_command_json: str = '["retrace"]'
    retrace_tool_version: str = "unconfigured"
    llvm_symbolizer_command_json: str = '["llvm-symbolizer"]'
    llvm_symbolizer_tool_version: str = "unconfigured"
    query_max_window_days: int = Field(default=31, ge=1, le=366)
    query_max_rows: int = Field(default=10_000, ge=100, le=100_000)
    query_max_limit: int = Field(default=100, ge=1, le=1_000)
    query_late_after_seconds: int = Field(default=900, ge=60, le=86_400)
    query_min_installations: int = Field(default=100, ge=1, le=100_000)
    query_min_sdk_health_coverage: float = Field(default=1.0, gt=0, le=1)
    query_cursor_hmac_key_b64: SecretStr = SecretStr("")
    web_dist_path: Path = Path("web/dist")
    web_session_hmac_key_b64: SecretStr = SecretStr("")
    web_session_ttl_seconds: int = Field(default=3_600, ge=300, le=43_200)
    web_session_cookie_secure: bool = False

    @field_validator(
        "environment",
        "log_level",
        "installation_hmac_active_key_version",
    )
    @classmethod
    def values_must_not_be_blank(cls, value: str) -> str:
        """Reject blank operational labels that would break filtering."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @model_validator(mode="after")
    def symbolizer_timeout_must_fit_lease(self) -> Settings:
        """Validate cross-field worker and production Web security constraints."""
        if self.symbolizer_timeout_seconds >= self.symbolizer_lease_seconds:
            raise ValueError("symbolizer timeout must be shorter than its lease")
        if self.environment.lower() == "production":
            if not self.web_session_cookie_secure:
                raise ValueError("production Web sessions require secure cookies")
            try:
                web_session_key = base64.b64decode(
                    self.web_session_hmac_key_b64.get_secret_value(),
                    validate=True,
                )
            except (ValueError, binascii.Error) as error:
                raise ValueError("production Web session signing key is invalid") from error
            if len(web_session_key) < 32:
                raise ValueError(
                    "production Web session signing key must contain at least 32 bytes"
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""
    return Settings()
