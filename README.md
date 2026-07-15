# AndroidAPM-Server

AndroidAPM-Server is the backend for the [AndroidAPM](https://github.com/XZQ/AndroidAPM) SDK. It accepts the SDK's existing Line Protocol and length-prefixed Protobuf batches, durably deduplicates at-least-once deliveries, and exports normalized OpenTelemetry data to SigNoz.

The project deliberately does not fork SigNoz. Android-specific ingestion, tenancy, symbolization, remote configuration, and release-artifact handling live here; telemetry storage, querying, dashboards, and alert evaluation are provided by SigNoz and its ClickHouse data plane.

## Target architecture

```text
AndroidAPM SDK
  -> HTTPS Gateway
     -> authentication / tenant boundary / quota
     -> gzip + Line Protocol / length-prefixed Protobuf decoding
     -> schema validation
     -> PostgreSQL durable inbox + (tenant_id, event_id) deduplication
     -> whole-batch ACK
  -> leased export worker
     -> OTLP logs / metrics
  -> SigNoz ingester
     -> ClickHouse
     -> SigNoz dashboards and alerts
```

## Delivery status

The first runnable foundation now includes authenticated whole-batch ingestion, bounded Gzip/Line/Protobuf decoding, distributed database quota counters, durable idempotent Inbox, leased OTLP Logs export, signed remote configuration, and versioned SigNoz Dashboard/alert assets. The authoritative roadmap and acceptance criteria are in [docs/00-总体规划.md](docs/00-%E6%80%BB%E4%BD%93%E8%A7%84%E5%88%92.md). Current proof and external blockers are in [docs/PROJECT_HANDOFF.md](docs/PROJECT_HANDOFF.md). The cloud-wide backlog remains [docs/云端待建设清单.md](docs/%E4%BA%91%E7%AB%AF%E5%BE%85%E5%BB%BA%E8%AE%BE%E6%B8%85%E5%8D%95.md).

## Planned local workflow

The Gateway targets Python 3.11 and uses `uv` for reproducible environments. PostgreSQL is the production persistence boundary. Docker Compose will provide the local Gateway/PostgreSQL stack; SigNoz is installed from its official Foundry release and is not vendored into this repository.

```powershell
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run mypy src
```

No production readiness claim is valid until the end-to-end and deployment checks in `docs/07-测试与验收.md` pass.

## Local database and first key

After PostgreSQL is available:

```powershell
uv run alembic upgrade head
uv run androidapm-admin create-ingest-key `
  --tenant-id local --tenant-name Local `
  --app-id com.example.app --environment development
uv run androidapm-api
uv run androidapm-worker
```

The CLI prints the ingest key once. Store it in a secret manager; the database retains only its Argon2id hash.

Remote configuration revisions are immutable and Ed25519-signed. Generate a local keypair with `uv run androidapm-admin generate-config-keypair`, put only the private key in the server secret store, and pin the printed public key in the Android client before enabling config polling.
