# AGENTS.md

## Purpose

This is the repository-local handoff entry for AndroidAPM-Server. Source code, migrations, and executable verification are the source of truth. Correct documentation whenever it disagrees with runtime behavior.

## Read order

1. `docs/00-总体规划.md`
2. `docs/01-总体架构.md`
3. `docs/02-SDK-Collector协议.md`
4. `docs/03-安全与租户.md`
5. `docs/04-OTLP映射与SigNoz.md`
6. `docs/05-Dashboard与告警.md`
7. `docs/06-符号化与远程配置.md`
8. `docs/07-测试与验收.md`
9. `docs/08-部署与运维.md`
10. `docs/09-实施路线图.md`
11. `docs/10-Web控制台与本地联调.md`
12. `docs/云端待建设清单.md`
13. `docs/adr/0006-发生时身份与installation假名化.md`
14. `docs/adr/0007-同源Web控制台与短时会话.md`

## Current verified baseline

- Baseline date: `2026-09-04`
- Branch: `codex/server-foundation`
- Runtime: Python `3.11.15`, FastAPI `0.139.0`, SQLAlchemy `2.0.51`
- Persistence: PostgreSQL production model; SQLite is used only for fast compatibility tests
- Telemetry target: OTLP/HTTP Logs to SigNoz `v0.133.0`, installed with Foundry `v0.2.13`
- Schema: append-only Alembic revisions `20260716_0001`, `20260716_0002`, and `20260828_0003`
- Collector V2/V3: typed explicit envelopes and exact post-commit schema/batch/count ACK; V3 carries occurrence-bound release/installation/native identity
- Identity: V2/V3 installation values are replaced before persistence by a tenant/domain-separated, versioned HMAC; V3 release identity is stored as `OCCURRENCE_BOUND`
- Query/BFF: fixed-scope `apmq1` viewer/investigator credentials, bounded release-health/fingerprint/Issue-detail/data-quality queries, investigator-only event/raw access, audited human release decisions, and HMAC-bound cursors
- Web console: React/TypeScript/Vite same-origin routed console for overview, Issues/detail, event exploration/detail, releases, quality, and explicit capability gaps; raw `apmq1` is exchanged once for a revocable, short-lived HttpOnly session with bound CSRF protection
- Symbolization: independent CI keys, private-volume artifact adapter, durable jobs, fixed-argv tool adapters; disabled by default until audited R8/LLVM tools are deployed
- Local verification: dependency sync, Ruff, mypy over 51 source files, 96 backend tests, 12 required documents, all four frontend gates with 9 Vitest tests, routed-console desktop/mobile browser smoke, and diff checks pass; the earlier three-revision migration round-trip/check remains valid historical evidence
- Verification: a real Android `HttpApmUploader` V2/V3 loopback E2E proves Gzip/exact ACK/typed and occurrence persistence/HMAC/replay against test-only SQLite; do not infer Docker/PostgreSQL/TLS/SigNoz deployment from it

## Non-negotiable invariants

- A `2xx` ingest response means the complete batch was durably committed to the inbox.
- V2/V3 success additionally requires exact `X-Apm-Schema-Version`, `X-Apm-Batch-Id`, and `X-Apm-Event-Count` response headers after commit.
- A non-`2xx` response must never cause the client to delete the batch.
- Deduplication is enforced by a database unique constraint on `(tenant_id, event_id)`; duplicate replay is acknowledged successfully.
- A mixed valid/invalid batch is rejected as a whole until a versioned item-level ACK protocol exists.
- PostgreSQL is the production inbox. In-memory or SQLite substitutes are test/development-only and must not be presented as production delivery guarantees.
- Workers use owner/lease/expiry claims. Only the active owner may mark an event delivered or failed.
- Raw secrets are never stored. Ingest keys are hashed, scoped, rotatable, and auditable.
- Tenant identity comes from authenticated credentials, never from an untrusted event field or header alone.
- SigNoz is an external pinned dependency. Do not copy or fork its core application into this repository.
- Raw Android events remain recoverable until downstream export succeeds or an explicit retention/dead-letter policy disposes them.
- Product facts use exact client `module/name/field` contracts. `module=crash` is not equivalent to a crash because it also contains `app_exit`.
- The default periodic SDK-health runtime identity is currently `core/sdk_health`; do not build production assets from helper/test-only `sdk_self_monitor/sdk_health_report` without a versioned client change.
- Missing, unavailable, not-applicable, invalid, unknown-coverage, late, and real zero are distinct states. Never fill a missing metric or denominator with zero.
- Crash-free/ANR-free session metrics remain unavailable until the client ships a standard occurrence-bound telemetry session identity.
- URL, SQL, path, stack, exception message, JavaScript, and arbitrary context/extras are high-cardinality or sensitive; raw retention does not authorize default OTLP attribute indexing.
- V2 resource/build values remain upload-batch declarations and must stay `BATCH_DECLARED`; V3 occurrence values are frozen into each durable Android event and stored as `OCCURRENCE_BOUND`.
- Known V2/V3 installation plaintext must never enter `payload_json`, logs, OTLP, query responses, or audits. Persistence keeps only the tenant-scoped HMAC and its key version; replay comparison uses the row's original key version.
- Query credentials own immutable tenant/app/environment scope. Request parameters and opaque cursors never grant or widen scope; `viewer` is L0-only and only `investigator` may read L1/L2 or record release decisions.
- Browsers never persist raw `apmq1` credentials. Production Web sessions require HTTPS Secure cookies, a dedicated signing key, database revocation checks, and CSRF on ambient-cookie writes.

## Engineering rules

- Target Python 3.11 and manage dependencies with `uv`.
- Type all public functions and keep `mypy` clean.
- Add docstrings to public modules, classes, and functions; comment security, durability, retry, and compatibility branches.
- Extract protocol limits, header names, retry bounds, and status values into named constants.
- Use structured logs. Never log credentials, authorization headers, raw mapping files, or unrestricted event bodies.
- Migrations are append-only after publication. Never edit an applied production migration.
- Keep protocol parsing independent from persistence and OTLP mapping so each layer is fuzzable and replaceable.
- Preserve unknown event fields in the raw payload for forward compatibility, while emitting only allow-listed indexed attributes.

## Documentation policy

- Update `README.md` and the matching `docs/*.md` whenever behavior or integration requirements change.
- Add or supersede an ADR when a durable architecture decision changes.
- Keep current verification evidence in `docs/PROJECT_HANDOFF.md`; do not rewrite historical claims as if they were current.
- `docs/云端待建设清单.md` is the single cross-repository cloud backlog. The Android client must link to it rather than duplicate it.

## Required finish checks

1. `git status --short --branch`
2. `uv sync --all-groups --frozen`
3. `uv run ruff check .`
4. `uv run mypy src`
5. `uv run pytest`
6. `uv run python scripts/verify_docs.py`
7. `pnpm --dir web run lint`
8. `pnpm --dir web run typecheck`
9. `pnpm --dir web run test`
10. `pnpm --dir web run build`
11. `git diff --check`
12. Container build and Compose smoke test when Docker is available
13. After push, exact equality among `HEAD`, the pushed remote branch, and `git ls-remote`
