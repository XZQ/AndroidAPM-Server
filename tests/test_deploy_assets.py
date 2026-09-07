from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_compose_contains_separate_durable_runtime_components() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert {"postgres", "migrate", "gateway", "worker", "symbolizer"} <= services.keys()
    assert services["gateway"]["read_only"] is True
    assert services["worker"]["read_only"] is True
    assert services["gateway"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["worker"]["command"] == ["androidapm-worker"]
    assert services["symbolizer"]["command"] == ["androidapm-symbolizer"]
    assert services["symbolizer"]["read_only"] is True
    assert "artifact-data:/var/lib/androidapm/artifacts" in services["gateway"]["volumes"]
    assert "artifact-data:/var/lib/androidapm/artifacts" in services["symbolizer"]["volumes"]


def test_container_drops_root_and_signoz_versions_are_pinned() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    versions = (ROOT / "deploy" / "signoz" / "VERSIONS.env").read_text(encoding="utf-8")
    assert "USER 10001:10001" in dockerfile
    assert "SIGNOZ_VERSION=v0.133.0" in versions
    assert "SIGNOZ_FOUNDRY_VERSION=v0.2.13" in versions


def test_web_assets_are_built_once_and_production_tls_proxy_is_explicit() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    web_index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    favicon = (ROOT / "web" / "public" / "favicon.svg").read_text(encoding="utf-8")
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    production = yaml.safe_load((ROOT / "compose.production.yaml").read_text(encoding="utf-8"))
    caddyfile = (ROOT / "deploy" / "caddy" / "Caddyfile").read_text(encoding="utf-8")

    assert "AS web-builder" in dockerfile
    assert "pnpm install --frozen-lockfile" in dockerfile
    assert "COPY --from=web-builder /web/dist ./web/dist" in dockerfile
    assert 'href="/favicon.svg"' in web_index
    assert favicon.startswith("<svg ")
    assert compose["services"]["gateway"]["ports"] == [
        "${APM_GATEWAY_BIND:-127.0.0.1}:8080:8080"
    ]
    assert production["services"]["gateway"]["environment"][
        "APM_WEB_SESSION_COOKIE_SECURE"
    ] == "true"
    assert production["services"]["caddy"]["ports"] == ["80:80", "443:443", "443:443/udp"]
    assert "reverse_proxy gateway:8080" in caddyfile
    assert "Strict-Transport-Security" in caddyfile
