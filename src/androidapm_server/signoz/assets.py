"""Idempotent SigNoz dashboard and alert asset synchronization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx


def load_assets(directory: Path) -> list[dict[str, Any]]:
    """Load deterministic JSON assets in filename order."""
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def find_named_id(value: Any, name_field: str, expected_name: str) -> str | None:
    """Find an API object's id through SigNoz response envelope variations."""
    if isinstance(value, dict):
        item_id = value.get("id")
        if value.get(name_field) == expected_name and isinstance(item_id, str):
            return item_id
        for nested in value.values():
            found = find_named_id(nested, name_field, expected_name)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = find_named_id(nested, name_field, expected_name)
            if found:
                return found
    return None


def sync_assets(
    client: httpx.Client,
    dashboards: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    dry_run: bool,
) -> list[str]:
    """Upsert dashboards/rules by stable checked-in names and return action summaries."""
    actions: list[str] = []
    dashboard_list = _json(client.get("/api/v2/dashboards", params={"limit": 1000}))
    for dashboard in dashboards:
        name = str(dashboard["name"])
        existing_id = find_named_id(dashboard_list, "name", name)
        action = "update" if existing_id else "create"
        actions.append(f"dashboard:{name}:{action}")
        if not dry_run:
            path = f"/api/v2/dashboards/{existing_id}" if existing_id else "/api/v2/dashboards"
            response = (
                client.put(path, json=dashboard)
                if existing_id
                else client.post(path, json=dashboard)
            )
            _json(response, allow_empty=True)

    if alerts:
        rule_list = _json(client.get("/api/v2/rules"))
        for alert in alerts:
            name = str(alert["alert"])
            existing_id = find_named_id(rule_list, "alert", name)
            action = "update" if existing_id else "create"
            actions.append(f"alert:{name}:{action}")
            if not dry_run:
                path = f"/api/v2/rules/{existing_id}" if existing_id else "/api/v2/rules"
                response = (
                    client.put(path, json=alert)
                    if existing_id
                    else client.post(path, json=alert)
                )
                _json(response, allow_empty=True)
    return actions


def _json(response: httpx.Response, allow_empty: bool = False) -> Any:
    """Raise for an unsuccessful response and decode its JSON envelope."""
    response.raise_for_status()
    if allow_empty and not response.content:
        return None
    return response.json()
