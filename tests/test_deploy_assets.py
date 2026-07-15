from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_compose_contains_separate_durable_runtime_components() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert {"postgres", "migrate", "gateway", "worker"} <= services.keys()
    assert services["gateway"]["read_only"] is True
    assert services["worker"]["read_only"] is True
    assert services["gateway"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["worker"]["command"] == ["androidapm-worker"]


def test_container_drops_root_and_signoz_versions_are_pinned() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    versions = (ROOT / "deploy" / "signoz" / "VERSIONS.env").read_text(encoding="utf-8")
    assert "USER 10001:10001" in dockerfile
    assert "SIGNOZ_VERSION=v0.133.0" in versions
    assert "SIGNOZ_FOUNDRY_VERSION=v0.2.13" in versions
