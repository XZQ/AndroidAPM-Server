"""Idempotently create or update checked-in SigNoz dashboard and alert assets."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import httpx

from androidapm_server.signoz.assets import load_assets, sync_assets

ROOT = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    """Build the asset synchronization CLI parser."""
    command = argparse.ArgumentParser()
    command.add_argument("--base-url", default=os.environ.get("SIGNOZ_BASE_URL"))
    command.add_argument("--api-key", default=os.environ.get("SIGNOZ_API_KEY"))
    command.add_argument("--include-alerts", action="store_true")
    command.add_argument("--dry-run", action="store_true")
    return command


def main() -> int:
    """Synchronize selected assets using a SigNoz service-account API key."""
    arguments = parser().parse_args()
    if not arguments.base_url or not arguments.api_key:
        raise SystemExit("SIGNOZ_BASE_URL and SIGNOZ_API_KEY (or flags) are required")
    headers = {"SIGNOZ-API-KEY": arguments.api_key}
    dashboards = load_assets(ROOT / "deploy" / "signoz" / "dashboards")
    alerts = (
        load_assets(ROOT / "deploy" / "signoz" / "alerts")
        if arguments.include_alerts
        else []
    )
    with httpx.Client(
        base_url=arguments.base_url.rstrip("/"),
        headers=headers,
        timeout=30,
    ) as client:
        actions = sync_assets(client, dashboards, alerts, arguments.dry_run)
    for action in actions:
        print(action)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
