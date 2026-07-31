# AGENTS.md

## Purpose

This is the repository-local handoff entry for AndroidAPM-Server. Source code, migrations, and executable verification are the source of truth. Correct documentation whenever it disagrees with runtime behavior.

## Read order

1. `docs/00-总体规划.md`
2. `docs/01-总体架构.md`
3. `docs/02-SDK-Collector协议.md`
4. `docs/03-安全与租户.md`
5. `docs/04-OTLP映射与SigNoz.md`
6. `docs/07-测试与验收.md`
7. `docs/08-部署与运维.md`
8. `docs/09-实施路线图.md`
9. `docs/云端待建设清单.md`

## Current verified baseline

- Baseline date: `2026-07-31`
- Branch: `codex/collector-v2-e2e`
- Runtime: Python `3.11.15`, FastAPI `0.139.0`, SQLAlchemy `2.0.51`
- Persistence: PostgreSQL production model; SQLite is used only for fast compatibility tests
- Telemetry target: OTLP/HTTP Logs to SigNoz `v0.133.0`, installed with Foundry `v0.2.13`
- Verification: 53 local tests plus a real Android `HttpApmUploader` -> HTTP/Gzip -> Collector -> SQLite compatibility E2E; see `docs/PROJECT_HANDOFF.md`. Do not infer Docker/PostgreSQL/SigNoz deployment from this evidence.

## Non-negotiable invariants

- A `2xx` ingest response means the complete batch was durably committed to the inbox.
- V2 success additionally requires exact `X-Apm-Schema-Version`, `X-Apm-Batch-Id`, and `X-Apm-Event-Count` response headers after commit.
- A non-`2xx` response must never cause the client to delete the batch.
- Deduplication is enforced by a database unique constraint on `(tenant_id, event_id)`; duplicate replay is acknowledged successfully.
- A mixed valid/invalid batch is rejected as a whole until a versioned item-level ACK protocol exists.
- PostgreSQL is the production inbox. In-memory or SQLite substitutes are test/development-only and must not be presented as production delivery guarantees.
- Workers use owner/lease/expiry claims. Only the active owner may mark an event delivered or failed.
- Raw secrets are never stored. Ingest keys are hashed, scoped, rotatable, and auditable.
- Tenant identity comes from authenticated credentials, never from an untrusted event field or header alone.
- SigNoz is an external pinned dependency. Do not copy or fork its core application into this repository.
- Raw Android events remain recoverable until downstream export succeeds or an explicit retention/dead-letter policy disposes them.

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
7. `git diff --check`
8. Container build and Compose smoke test when Docker is available
9. After push, exact equality among `HEAD`, the pushed remote branch, and `git ls-remote`
