from __future__ import annotations

import json
from pathlib import Path

import httpx

from androidapm_server.signoz.assets import find_named_id, load_assets, sync_assets

ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_signoz_assets_have_current_schema_and_stable_names() -> None:
    dashboards = load_assets(ROOT / "deploy" / "signoz" / "dashboards")
    alerts = load_assets(ROOT / "deploy" / "signoz" / "alerts")
    assert dashboards and alerts
    assert all(item["schemaVersion"] == "v6" for item in dashboards)
    assert all(item["schemaVersion"] == "v2alpha1" for item in alerts)
    assert len({item["name"] for item in dashboards}) == len(dashboards)
    assert len({item["alert"] for item in alerts}) == len(alerts)
    # A parse/serialize round trip also rejects trailing or non-JSON content.
    json.dumps(dashboards + alerts)


def test_checked_in_assets_use_exact_runtime_events_and_stay_unrouted() -> None:
    dashboards = load_assets(ROOT / "deploy" / "signoz" / "dashboards")
    alerts = load_assets(ROOT / "deploy" / "signoz" / "alerts")
    rendered_dashboards = json.dumps(dashboards, sort_keys=True)
    rendered_alerts = json.dumps(alerts, sort_keys=True)
    assert "sdk_health_report" not in rendered_dashboards + rendered_alerts
    assert "android.apm.module = 'core' AND android.apm.name = 'sdk_health'" in (
        rendered_dashboards
    )
    assert "android.apm.module = 'crash' AND android.apm.name = 'java_crash'" in (
        rendered_dashboards
    )
    assert "android.apm.module = 'anr' AND android.apm.name = 'anr_detected'" in (
        rendered_dashboards
    )
    assert "android.apm.release_identity_quality = 'OCCURRENCE_BOUND'" in rendered_alerts
    assert {alert["labels"]["production_ready"] for alert in alerts} == {"false"}
    assert all(
        threshold["channels"] == []
        for alert in alerts
        for threshold in alert["condition"]["thresholds"]["spec"]
    )


def test_finds_named_id_inside_api_envelopes() -> None:
    envelope = {"status": "success", "data": {"items": [{"id": "123", "name": "target"}]}}
    assert find_named_id(envelope, "name", "target") == "123"
    assert find_named_id(envelope, "name", "missing") is None


def test_sync_updates_existing_dashboard_and_creates_new_alert() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/api/v2/dashboards":
            return httpx.Response(200, json={"data": [{"id": "dashboard-id", "name": "dash"}]})
        if request.method == "GET" and request.url.path == "/api/v2/rules":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(201, json={"status": "success"})

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://signoz",
    ) as client:
        actions = sync_assets(
            client,
            [{"name": "dash", "schemaVersion": "v6", "tags": [], "spec": {}}],
            [{"alert": "alert", "schemaVersion": "v2alpha1"}],
            False,
        )
    assert actions == ["dashboard:dash:update", "alert:alert:create"]
    assert ("PUT", "/api/v2/dashboards/dashboard-id") in requests
    assert ("POST", "/api/v2/rules") in requests
