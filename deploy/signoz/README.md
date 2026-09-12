# SigNoz deployment boundary

SigNoz is installed from the official [SigNoz Foundry](https://github.com/SigNoz/foundry) release. This directory intentionally contains only the validated version pair and future AndroidAPM-specific overrides; it does not vendor upstream Compose, Kubernetes, ClickHouse, or SigNoz source.

For the current baseline, acquire Foundry `v0.2.13`, select its Docker Compose or Kubernetes casting, and pin SigNoz `v0.133.0`. Verify release checksums before running it. Configure the AndroidAPM worker's `APM_OTLP_LOGS_ENDPOINT` to the private OTLP/HTTP logs endpoint and keep that endpoint inaccessible to mobile clients.

The local `compose.yaml` starts only Gateway, worker, and PostgreSQL. This separation keeps official SigNoz upgrades independent and makes it impossible for this repository to silently drift into a fork.

## Dashboard and alert assets

The dashboard uses SigNoz's current v2 Perses-compatible dashboard API (`schemaVersion: v6`). Alert templates use the current v2 rule schema (`schemaVersion: v2alpha1`). Synchronize a staging instance with a service-account API key:

```powershell
$env:SIGNOZ_BASE_URL = "https://signoz-staging.example.com"
$env:SIGNOZ_API_KEY = "<service-account-key>"
uv run python scripts/sync_signoz_assets.py --dry-run
uv run python scripts/sync_signoz_assets.py
```

Alerts are intentionally opt-in because notification policies/channels are environment-owned:

```powershell
uv run python scripts/sync_signoz_assets.py --include-alerts
```

The checked-in overview is an internal staging aid, not a multi-tenant authorization boundary. Its
first-slice panels use exact `crash/java_crash`, `anr/anr_detected`, and `core/sdk_health` filters.
The alert templates additionally require occurrence-bound release identity, carry
`production_ready=false`, and contain no channels. Do not route them until a target environment has
verified app/environment/version scope, minimum sample, installation coverage, SDK-drop and late-data
gates, plus trigger and recovery. Shared production readers must use the tenant-scoped Query/BFF (or
physical SigNoz isolation); an interactive Dashboard variable is not access control.

The sync is not considered verified until the target SigNoz instance accepts each asset and synthetic data renders every panel and triggers/recovers each alert.
