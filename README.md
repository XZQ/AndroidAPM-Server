# AndroidAPM-Server

Symbol tools stream stdout/stderr concurrently with stdin, enforcing a separate 1 MiB limit per output stream before retaining additional bytes. Overflow terminates and reaps the direct child immediately; timeout/cancellation cleanup drains remaining pipe bytes without accumulating them.

Native symbolization verifies every typed occurrence frame against its raw module-relative PC, module name and optional inline build ID, then uses the exact scoped ELF per ABI/build ID. Missing modules wait without consuming tool attempts; all-unknown output fails, while function-only or mixed results are `partially_symbolized`. Per-frame resolution and artifact provenance are retained in L2 evidence. A local two-module NDK LLVM regression is available with `APM_TEST_NDK_BIN`; real-device crash capture and production tool images still require verification.

Java mapping validation accepts dotted JVM class names, including kept package/class names, inner classes, Unicode and generated lambda names. Empty segments, descriptor/path punctuation and NUL-containing input remain invalid.

Migration `20260912_0007` preserves raw fingerprint aliases and applies completed Java symbol fingerprints to Issue grouping. New completions update the Issue identity within the fenced symbolization transaction. Old links resolve within the authenticated scope/window; ambiguous splits return `409 ambiguous_issue_fingerprint`. Raw replay identity remains unchanged; previously exported SigNoz records are not rewritten. See ADR 0009.

Migration `20260912_0006` widens release/build/variant storage to match the existing 256-byte SDK protocol, including symbol identities and human release decisions. Apply migrations before rolling out this version; a downgrade refuses to narrow columns while values longer than 128 characters exist. Ingest headers, artifact uploads and query filters share the same 256-byte limit.

Trend buckets carry nullable counts: absent or V2-only occurrence evidence is never filled with zero. Release buckets include `newState`/`baselineState`; the console leaves gaps disconnected and shows an empty state when nothing is observable. The overview trust banner includes both releases' quality gates, including minimum installation samples.

Exception logs retain only the exception type and bounded code locations. Application, worker and Uvicorn handlers omit exception messages, SQL parameters, chained causes, source lines and locals; the production SQLAlchemy engine also enables `hide_parameters=True`.

The export worker executes bounded raw retention every 60 seconds (up to 1000 rows): delivered evidence after 7 days, dead letters after 30 days, and quota windows after 24 hours. Active export/symbol jobs are preserved. Minimal event/hash/HMAC-version records remain for replay protection; expired raw reads return audited `410 raw_evidence_expired`. New ingest batches exceeding 100000 live events or 10 GiB retained canonical raw bytes roll back with retryable 503; duplicate-only replay still ACKs. See ADR 0008 and migration `20260907_0005`.

Backlog metrics now use live database snapshots with explicit availability/freshness. Export and symbolizer counters are scraped from their own private listeners (`9101`/`9102`, loopback by default); Compose exposes them only inside its network. Use `deploy/prometheus-scrape.yaml`; the public Caddy route rejects `/metrics`.

Ingest/CI/Query key verification uses a shared, bounded two-thread Argon2 executor. A fixed-memory, per-process 20 attempts/second token bucket (burst 40) runs before key lookup. Saturation returns retryable `429 authentication_busy` with `Retry-After: 1`; cancellation does not release a running hash's memory slot. Unknown key IDs use the same executor for dummy verification.

Query filters run in SQL before budgeting. SQL reduces repeated events into weighted occurrence buckets; `APM_QUERY_MAX_ROWS` bounds aggregate groups rather than raw events. Investigator pagination is independently page-bounded. Queries have a 5-second driver/PostgreSQL statement budget and never return partial aggregates; migration `20260907_0004` adds release/time and fingerprint/time indexes.

Windows crossing installation HMAC key versions report `INSTALLATION_HMAC_CONTINUITY_BREAK` with null installation counts/ratios/deltas; event counts remain usable. Issue distributions and trends also preserve this gap. Replay verification still uses each row's original key version.

Release installation ratios require complete occurrence identity, valid SDK emission/drop samples, no reported loss or late events, at least `APM_QUERY_MIN_INSTALLATIONS=100` installations and `APM_QUERY_MIN_SDK_HEALTH_COVERAGE=1.0` installation health coverage. Missing quality returns an explicit state and null ratio; observed event counts remain available.

Console evidence is bound to its route, session scope and filter snapshot. Changed queries discard prior evidence, late responses are ignored, and failed refreshes cannot retain an actionable release decision panel. Failed audited submissions preserve the entered reason.

Installation privacy is applied before normalization and symbol-job preparation: registered attributes, nested raw values/keys, and native identities use minimized evidence. An installation value embedded in a required event/routing/release identifier rejects the whole batch with `422 privacy_identity_conflict`; identity is never silently rewritten.

AndroidAPM-Server is the backend for the [AndroidAPM](https://github.com/XZQ/AndroidAPM) SDK. It accepts legacy Line Protocol and length-prefixed Protobuf plus explicit Protobuf V2/V3 envelopes, durably deduplicates at-least-once deliveries, and exports allow-listed OpenTelemetry Logs to SigNoz. V3 binds release, build, installation, and optional native-frame identity to the event occurrence rather than its later upload batch.

The project deliberately does not fork SigNoz. Android-specific ingestion, tenancy, symbolization, remote configuration, release-artifact handling, and the fixed-scope release-health Web console live here; general telemetry storage, exploratory querying, dashboards, and alert evaluation are provided by SigNoz and its ClickHouse data plane.

## Target architecture

```text
AndroidAPM SDK
  -> HTTPS Gateway
     -> authentication / tenant boundary / quota
     -> gzip + Line Protocol / length-prefixed Protobuf / V2/V3 envelope decoding
     -> schema validation
     -> installation HMAC before durable persistence
     -> PostgreSQL durable inbox + (tenant_id, event_id) deduplication
     -> post-commit whole-batch ACK (V2/V3 require exact schema/batch/count headers)
  -> leased export worker
     -> OTLP logs / metrics
  -> SigNoz ingester
     -> ClickHouse
     -> SigNoz dashboards and alerts

Release investigator
  -> same-origin Web console
  -> one-time apmq1 exchange -> short-lived HttpOnly session + CSRF
  -> bounded Query/BFF aggregates and event metadata
  -> audited purpose-bound raw access
  -> human continue / pause / rollback decision record

Trusted build CI
  -> separately scoped CI key
  -> bounded Java mapping / unstripped Native ELF upload
  -> checksum + exact build identity + private artifact store
  -> leased symbolizer worker
  -> fixed argv R8 retrace / llvm-symbolizer adapter
```

## Delivery status

Normalization v2 keeps non-finite, out-of-range, and semantically invalid numeric values in raw evidence while excluding them from registered projections. The export worker isolates deterministic mapping failures into recoverable dead letters and continues exporting healthy rows; unexpected transport failures consume the normal bounded retry budget.

The runnable foundation includes authenticated whole-batch ingestion, bounded Gzip/Line/legacy-Protobuf/V2/V3 decoding, exact versioned acknowledgement, distributed quota counters, durable idempotent Inbox, occurrence normalization, versioned installation HMAC, leased OTLP Logs export, signed remote configuration, and minimal SigNoz assets. It also includes independent CI credentials, immutable Java mapping/Native ELF registration, durable symbolization jobs, fixed-argv retrace/llvm-symbolizer adapters, and a fixed-scope Query/BFF.

The first product slice now exposes a same-origin React/TypeScript Web console with recoverable routes for application overview, Issues, Issue detail, investigator event exploration/detail, releases, and data quality. It provides bounded release health, exact Crash/ANR fingerprint aggregation, occurrence-time trends, real release/scene breakdowns, audited raw evidence, and audited human release decisions. Performance, alerting, and settings are visible as honest `UNAVAILABLE`/`UNCONFIGURED` capability pages instead of demo metrics. It distinguishes `ZERO`, `NO_DATA`, `UNAVAILABLE`, `UNKNOWN_COVERAGE`, `DEGRADED`, and `LATE`; session-free metrics remain `UNAVAILABLE: SESSION_ID_NOT_PROVIDED`, and device/OS breakdowns remain `UNKNOWN_COVERAGE` until the client ships standard resource fields. A raw `apmq1` is exchanged once for a revocable short-lived HttpOnly session and is never persisted by the browser. This is not yet a complete hosted product: SSO, OTLP Metrics, a standard telemetry session/device contract, real SigNoz staging proof, notification routing, production key/KMS operations, PostgreSQL fault evidence, and scenario acceptance remain external work. SigNoz alerts have no notification channel and are explicitly `production_ready=false`.

The authoritative product plan is [docs/00-总体规划.md](docs/00-%E6%80%BB%E4%BD%93%E8%A7%84%E5%88%92.md), the real client event/data contract is [docs/04-OTLP映射与SigNoz.md](docs/04-OTLP%E6%98%A0%E5%B0%84%E4%B8%8ESigNoz.md), the user/query/alert scenarios are [docs/05-Dashboard与告警.md](docs/05-Dashboard%E4%B8%8E%E5%91%8A%E8%AD%A6.md), and local Web/cloud handoff is [docs/10-Web控制台与本地联调.md](docs/10-Web%E6%8E%A7%E5%88%B6%E5%8F%B0%E4%B8%8E%E6%9C%AC%E5%9C%B0%E8%81%94%E8%B0%83.md). Current proof and external blockers are in [docs/PROJECT_HANDOFF.md](docs/PROJECT_HANDOFF.md). The cloud-wide backlog remains [docs/云端待建设清单.md](docs/%E4%BA%91%E7%AB%AF%E5%BE%85%E5%BB%BA%E8%AE%BE%E6%B8%85%E5%8D%95.md).

## Planned local workflow

The Gateway targets Python 3.11 and uses `uv` for reproducible environments. PostgreSQL is the production persistence boundary. Docker Compose will provide the local Gateway/PostgreSQL stack; SigNoz is installed from its official Foundry release and is not vendored into this repository.

```powershell
uv sync --all-groups --frozen
uv run ruff check .
uv run mypy src
uv run pytest
pnpm --dir web install --frozen-lockfile
pnpm --dir web run lint
pnpm --dir web run typecheck
pnpm --dir web run test
pnpm --dir web run build
```

No production readiness claim is valid until the end-to-end and deployment checks in `docs/07-测试与验收.md` pass.

For the complete no-Docker local preview, including an ignored SQLite compatibility database, synthetic Crash/ANR fixture, one-day investigator/ingest keys, built Web assets, and the API server:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1
```

Open `http://127.0.0.1:8080` and paste the printed Query key. Synthetic rows are explicitly local UI fixtures, not production evidence. The printed ingest key can be used by an Android debug build after `adb reverse tcp:8080 tcp:8080`; production uses HTTPS and short-lived app tokens. See [docs/10-Web控制台与本地联调.md](docs/10-Web%E6%8E%A7%E5%88%B6%E5%8F%B0%E4%B8%8E%E6%9C%AC%E5%9C%B0%E8%81%94%E8%B0%83.md).

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

Before accepting installation-bearing V2 or any V3 event, configure `APM_INSTALLATION_HMAC_KEYS_JSON` as a JSON map from key version to a Base64 secret of at least 32 random bytes and set `APM_INSTALLATION_HMAC_ACTIVE_KEY_VERSION`. The Gateway fails closed when pseudonymization is unavailable. Rotation keeps old key versions readable so a duplicate replay is compared with the version stored on its original row; never remove an old version until its retained rows can no longer replay.

Create a separately scoped query credential for one tenant/app/environment and role:

```powershell
uv run androidapm-admin create-query-key `
  --tenant-id local --tenant-name Local `
  --app-id com.example.app --environment production `
  --role investigator
```

The one-time `apmq1` value is not an ingest or CI key. `viewer` can read L0 aggregates only; `investigator` can additionally read L1/L2 event data and record human `continue/pause/rollback` decisions. Event pagination also requires `APM_QUERY_CURSOR_HMAC_KEY_B64`, a dedicated Base64 secret of at least 32 random bytes. Empty or weak cursor configuration fails closed rather than returning unsigned pagination state.

Remote configuration revisions are immutable and Ed25519-signed. Generate a local keypair with `uv run androidapm-admin generate-config-keypair`, put only the private key in the server secret store, and pin the printed public key in the Android client before enabling config polling.

## Symbol artifacts

Artifact upload never accepts an APK ingest key. Create a separately scoped build credential:

```powershell
uv run androidapm-admin create-ci-key `
  --tenant-id local --tenant-name Local --app-id com.example.app
```

CI uploads raw `mapping.txt` to `POST /v1/artifacts/java-mapping` and one unstripped ELF per ABI/build-id to `POST /v1/artifacts/native-elf`. Requests include the exact app/versionCode/appBuild/variant identity and expected SHA-256 documented in [docs/06-符号化与远程配置.md](docs/06-%E7%AC%A6%E5%8F%B7%E5%8C%96%E4%B8%8E%E8%BF%9C%E7%A8%8B%E9%85%8D%E7%BD%AE.md).

`androidapm-symbolizer` is disabled by default. Enable it only after mounting the private artifact volume and configuring fixed JSON argv arrays plus audited tool versions for R8 retrace and llvm-symbolizer. The base Python image deliberately does not download mutable tool binaries during startup.

The symbolizer claims one job immediately before execution; `APM_SYMBOLIZER_BATCH_SIZE` limits work per cycle, without reserving queued leases. Each claim gets a fresh fencing token. Artifact resolution, tool execution and completion share a deadline five seconds before lease expiry, with tool timeout strictly below that budget. Cancellation/timeout kills and reaps the direct tool process. Missing-artifact polls spend no tool attempts. Both workers reject expired completion/failure writes using the PostgreSQL clock, and outcome counters follow committed transitions.
