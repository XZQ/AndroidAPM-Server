"""Regression coverage for the deployment and maintenance guard scripts."""

from __future__ import annotations

import base64
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
KEY_B64 = base64.b64encode(b"k" * 32).decode("ascii")


def load(name: str) -> ModuleType:
    """Load a standalone script as a module without changing the import path."""
    spec = importlib.util.spec_from_file_location(f"scripts.{name}", SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_configuration() -> dict[str, str]:
    """Return a complete, non-placeholder production configuration."""
    return {
        "APM_ENVIRONMENT": "production",
        "APM_WEB_SESSION_COOKIE_SECURE": "true",
        "APM_WEB_SESSION_HMAC_KEY_B64": KEY_B64,
        "APM_QUERY_CURSOR_HMAC_KEY_B64": KEY_B64,
        "APM_INSTALLATION_HMAC_KEYS_JSON": json.dumps({"v1": KEY_B64}),
        "APM_INSTALLATION_HMAC_ACTIVE_KEY_VERSION": "v1",
        "POSTGRES_PASSWORD": "a" * 24,
        "APM_DATABASE_URL": "postgresql+asyncpg://apm:secret@db.internal:5432/apm",
        "APM_DOMAIN": "apm.company.net",
        "APM_OTLP_LOGS_ENDPOINT": "https://otlp.company.net/v1/logs",
    }


def test_complete_production_configuration_passes() -> None:
    config = load("check_production_config")
    assert config.validate(valid_configuration()) == []


def test_example_env_file_is_rejected_without_printing_values() -> None:
    config = load("check_production_config")
    values = config.parse_env_file(ROOT / ".env.production.example")
    failures = config.validate(values)
    assert any("APM_WEB_SESSION_HMAC_KEY_B64" in failure for failure in failures)
    assert any("POSTGRES_PASSWORD" in failure for failure in failures)
    assert all(KEY_B64 not in failure for failure in failures)


def test_weak_keys_and_insecure_cookies_are_rejected() -> None:
    config = load("check_production_config")
    values = valid_configuration()
    values["APM_WEB_SESSION_COOKIE_SECURE"] = "false"
    values["APM_QUERY_CURSOR_HMAC_KEY_B64"] = base64.b64encode(b"short").decode("ascii")
    failures = config.validate(values)
    assert any("COOKIE_SECURE" in failure for failure in failures)
    assert any("QUERY_CURSOR" in failure for failure in failures)


def test_env_file_parser_handles_quotes_and_comments(tmp_path: Path) -> None:
    config = load("check_production_config")
    path = tmp_path / "production.env"
    path.write_text(
        "# comment\nA=\"quoted value\"\nB='single'\nC={}\nNOT_AN_ASSIGNMENT\n",
        encoding="utf-8",
    )
    assert config.parse_env_file(path) == {
        "A": "quoted value",
        "B": "single",
        "C": "{}",
    }


def test_certificate_expiry_math_uses_utc() -> None:
    expiry = load("check_tls_expiry")
    now = datetime(2026, 11, 7, tzinfo=UTC)
    assert expiry.remaining_days("Nov 12 22:41:30 2026 GMT", now) == pytest.approx(
        5.95, abs=0.02
    )
    assert expiry.remaining_days("Nov  1 00:00:00 2026 GMT", now) < 0


def test_secret_scan_flags_real_keys_but_not_fixtures_or_placeholders() -> None:
    secrets = load("check_secrets")
    assert "query key" in secrets.findings(f"login('apmq1_{'A' * 40}')")
    assert secrets.findings("login('apmq1_key_secret')") == []
    assert secrets.findings("APM_QUERY_CURSOR_HMAC_KEY_B64=replace-with-me") == []
    assert "assigned secret value" in secrets.findings(
        f"APM_QUERY_CURSOR_HMAC_KEY_B64={KEY_B64}"
    )
