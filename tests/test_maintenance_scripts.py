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
CURSOR_KEY_B64 = base64.b64encode(b"c" * 32).decode("ascii")
SESSION_KEY_B64 = base64.b64encode(b"s" * 32).decode("ascii")


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
        "APM_WEB_SESSION_HMAC_KEY_B64": SESSION_KEY_B64,
        "APM_QUERY_CURSOR_HMAC_KEY_B64": CURSOR_KEY_B64,
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
    # No configured value from the example file (or a real weak secret) may be echoed.
    for value in values.values():
        if len(value) >= 12:
            assert all(value not in failure for failure in failures)
    crafted = valid_configuration()
    secret_marker = base64.b64encode(b"short-secret-marker").decode("ascii")
    crafted["APM_QUERY_CURSOR_HMAC_KEY_B64"] = secret_marker
    crafted_failures = config.validate(crafted)
    assert any("APM_QUERY_CURSOR_HMAC_KEY_B64" in failure for failure in crafted_failures)
    assert all(secret_marker not in failure for failure in crafted_failures)


def test_production_example_passes_after_replacing_placeholders() -> None:
    """Replacing every documented placeholder in .env.production.example must pass validation."""
    config = load("check_production_config")
    values = config.parse_env_file(ROOT / ".env.production.example")
    values.update(
        {
            "APM_DOMAIN": "apm.company.net",
            "POSTGRES_PASSWORD": "a" * 24,
            "APM_OTLP_LOGS_ENDPOINT": "https://otlp.company.net/v1/logs",
            "APM_INSTALLATION_HMAC_KEYS_JSON": json.dumps({"v1": KEY_B64}),
            "APM_QUERY_CURSOR_HMAC_KEY_B64": base64.b64encode(b"q" * 32).decode("ascii"),
            "APM_WEB_SESSION_HMAC_KEY_B64": base64.b64encode(b"w" * 32).decode("ascii"),
        }
    )
    assert config.validate(values) == []


def test_compose_injected_database_url_is_not_required_in_env_file() -> None:
    config = load("check_production_config")
    values = valid_configuration()
    del values["APM_DATABASE_URL"]
    assert config.validate(values) == []


def test_explicit_sqlite_database_url_override_is_rejected() -> None:
    config = load("check_production_config")
    values = valid_configuration()
    values["APM_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
    assert any("APM_DATABASE_URL" in failure for failure in config.validate(values))


def test_ip_literal_public_host_is_accepted() -> None:
    """An IP-only public entry point is valid; no DNS domain is required."""
    config = load("check_production_config")
    values = valid_configuration()
    values["APM_DOMAIN"] = "203.0.113.10"  # RFC 5737 documentation address
    assert not any("APM_DOMAIN" in failure for failure in config.validate(values))


def test_ipv6_literal_public_host_is_accepted() -> None:
    """IPv6 literals are valid public entry points despite having no dot."""
    config = load("check_production_config")
    for host in ("2001:db8::1", "[2001:db8::1]"):
        values = valid_configuration()
        values["APM_DOMAIN"] = host
        assert not any("APM_DOMAIN" in failure for failure in config.validate(values)), host


def test_bare_label_and_invalid_ip_hosts_are_rejected() -> None:
    config = load("check_production_config")
    for host in ("intranet", "2001:db8:::1"):
        values = valid_configuration()
        values["APM_DOMAIN"] = host
        assert any("APM_DOMAIN" in failure for failure in config.validate(values)), host


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


def test_default_threshold_accepts_a_fresh_short_lived_certificate() -> None:
    """A ~6-day certificate must not fail the probe when no flag is supplied."""
    expiry = load("check_tls_expiry")
    now = datetime(2026, 11, 7, tzinfo=UTC)
    fresh = expiry.remaining_days("Nov 12 22:41:30 2026 GMT", now)
    assert fresh > expiry.DEFAULT_MIN_DAYS


def test_reused_hmac_keys_across_domains_are_rejected() -> None:
    config = load("check_production_config")
    values = valid_configuration()
    values["APM_QUERY_CURSOR_HMAC_KEY_B64"] = values["APM_WEB_SESSION_HMAC_KEY_B64"]
    failures = config.validate(values)
    assert any("must not reuse" in failure for failure in failures)
    assert all(SESSION_KEY_B64 not in failure for failure in failures)

    ring_reuse = valid_configuration()
    ring_reuse["APM_INSTALLATION_HMAC_KEYS_JSON"] = json.dumps({"v1": SESSION_KEY_B64})
    ring_failures = config.validate(ring_reuse)
    assert any("must not reuse" in failure for failure in ring_failures)


def test_secret_scan_flags_real_keys_but_not_fixtures_or_placeholders() -> None:
    secrets = load("check_secrets")
    assert "ingest key" in secrets.findings(f"Authorization: Bearer apm1_{'B' * 40}")
    assert "query key" in secrets.findings(f"login('apmq1_{'A' * 40}')")
    assert secrets.findings("login('apmq1_key_secret')") == []
    assert secrets.findings("APM_QUERY_CURSOR_HMAC_KEY_B64=replace-with-me") == []
    assert "assigned secret value" in secrets.findings(
        f"APM_QUERY_CURSOR_HMAC_KEY_B64={KEY_B64}"
    )
    assert "assigned database URL password" in secrets.findings(
        "APM_DATABASE_URL=postgresql+asyncpg://apm:SuperSecretProd99!@db.internal:5432/apm"
    )
    assert (
        secrets.findings(
            "APM_DATABASE_URL=postgresql+asyncpg://androidapm:change-me@localhost:5432/androidapm"
        )
        == []
    )
    assert "assigned installation HMAC key ring" in secrets.findings(
        f'APM_INSTALLATION_HMAC_KEYS_JSON={{"v1":"{KEY_B64}"}}'
    )
    assert (
        secrets.findings(
            'APM_INSTALLATION_HMAC_KEYS_JSON={"v1":"replace-with-base64-32-byte-secret"}'
        )
        == []
    )
