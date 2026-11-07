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
    Current local operations: `docs/11-本地产品功能.md` and `docs/adr/0010-本地性能告警与管理面.md`.
13. `docs/13-云端运行环境.md` — current cloud runtime facts, deployment drift and remaining acceptance work.
14. `docs/adr/0006-发生时身份与installation假名化.md`
15. `docs/adr/0007-同源Web控制台与短时会话.md`
16. `docs/adr/0008-保留策略与容量准入.md`
17. `docs/adr/0009-符号化Issue指纹与旧链接.md`

## Current verified baseline

- Baseline date: `2026-09-12`
- Branch: `main` (sole local/remote branch since 2026-10-13; complete implementation history retained)
- Runtime: Python `3.11.15`, FastAPI `0.139.0`, SQLAlchemy `2.0.51`
- Persistence: PostgreSQL production model; SQLite is used only for fast compatibility tests
- Telemetry target: OTLP/HTTP Logs to SigNoz `v0.133.0`, installation method pinned to Foundry `v0.2.13`; the service is deployed behind HTTPS on a Seoul Lighthouse instance, but SigNoz/TLS/PostgreSQL/backup **on-site acceptance remains unverified** (see `docs/13-云端运行环境.md`)
- Schema: seven append-only Alembic revisions through `20260912_0007`; the two latest revisions widen release identities and preserve raw Issue aliases while backfilling Java symbol fingerprints
- Collector V2/V3: typed explicit envelopes and exact post-commit schema/batch/count ACK; V3 carries occurrence-bound release/installation/native identity
- Identity: V2/V3 installation values are replaced before persistence by a tenant/domain-separated, versioned HMAC; V3 release identity is stored as `OCCURRENCE_BOUND`
- Query/BFF: fixed-scope `apmq1` viewer/investigator credentials, bounded release-health/fingerprint/Issue-detail/data-quality queries, investigator-only event/raw access, audited human release decisions, and HMAC-bound cursors
- Web console: React/TypeScript/Vite same-origin routed console for overview, Issues/detail, event exploration/detail, releases, quality, and explicit capability gaps; raw `apmq1` is exchanged once for a revocable, short-lived HttpOnly session with bound CSRF protection
- Symbolization: independent CI keys, private-volume artifact adapter, just-in-time single-job claims, unique claim tokens, database-clock expiry fencing, bounded job deadlines and streaming output limits; Native frames use exact per-module ELF identities and explicit partial resolution, missing modules spend no tool attempts; disabled by default until audited production R8/LLVM tools are deployed
- Operations: bounded asynchronous key verification, private worker metrics, safe exception diagnostics, terminal raw retention with replay metadata and explicit evidence expiry/coverage states, and transactional ingest capacity backpressure
- Local verification (historical 2026-09-12 baseline, superseded by the dated notes below): dependency sync, Ruff, mypy over 55 source files, 204 backend tests with the opt-in local NDK 27 / LLVM 18 two-ELF regression, 12 required documents, all four frontend gates with 18 Vitest tests, seven-revision SQLite migration checks; routed-console desktop/mobile browser smoke remains historical 2026-09-04 evidence
- Verification (historical 2026-09-12): real Android `HttpApmUploader` V2/V3 loopback E2E passed against seven-revision test-only SQLite with Gzip/exact ACK/typed and occurrence persistence/HMAC/replay; six PostgreSQL integration tests were skipped without a configured connection. Do not infer deployment from local evidence; the cloud service is independently verified from its public HTTPS surface (see `docs/13-云端运行环境.md`)

2026-10-13 本地产品扩展：八版 migration 至 `20261013_0008`，Normalization v3 和 70 个性能数值契约；性能/告警/设置为真实 API 页面。告警独立持久化 evaluator/outbox，管理权限、CSRF、凭据/配置生命周期和 scope 审计见 ADR 0010、`docs/11-本地产品功能.md`。上文数字属于历史基线，当前验证以 PROJECT_HANDOFF 最新条目为准。

2026-10-14 本地统计：九版 migration 至 `20261014_0009`，Normalization v4、243 numeric fields / 66 event contracts / 29 fixed categories、Native/tombstone 独立统计、标准匿名 runtime sessions 与 device/OS 分布、手动 Trace 与 opt-in OTLP Metrics/Traces。服务端字段 7–9 是已审查的向后兼容 optional 扩展，客户端 durable codec 当时规划为 V5、实际仍是 V4。阅读 `docs/12-统计契约与口径.md` 与 `docs/adr/0011-运行会话统计与多信号导出.md`；逐信号 frozen export plan 和 lease fencing 必须保留。

2026-11-02 修正：durable V5 / session_start / span v2 的「已完成」描述与当前客户端源码不符；当前 SDK 仍为 V4。Web 产品页本轮已实际接入 API。

2026-11-07 现状（覆盖上文所有历史基线）：唯一分支 `main`；九版 migration head `20261014_0009`；服务端 Normalization v4 / 243 数值字段 / 66 事件 / 29 固定类别；SDK 仍为 V4。云端 HTTPS 服务已上线运行，但部署配置未回收、SigNoz/备份/SSO/真机等现场验收仍未完成。当前验证以 `docs/PROJECT_HANDOFF.md` 的 2026-11-07 条目为准，运行事实与待办见 `docs/13-云端运行环境.md`。上文 2026-09-12 的 55 files / 204 tests / 12 docs / 18 Vitest / 七版等数字均为历史基线，不代表当前状态。

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
- Crash-free/ANR-free session metrics require standard occurrence-bound runtime session identity, valid session_start inside the query window, complete identity/SDK-health coverage and quality gates. Never infer sessions from installation/event counts; historical app_exit/tombstone evidence does not count as a live session failure.
- URL, SQL, path, stack, exception message, JavaScript, and arbitrary context/extras are high-cardinality or sensitive; raw retention does not authorize default OTLP attribute indexing.
- V2 resource/build values remain upload-batch declarations and must stay `BATCH_DECLARED`; V3 occurrence values are frozen into each durable Android event and stored as `OCCURRENCE_BOUND`.
- Known V2/V3 installation and optional V3 session plaintext must never enter payloads, logs, OTLP, queries or audits. Separate HMAC domains retain tenant scope and original key versions for replay; absent new optional keys never change legacy V3 payload hashes.
- Query credentials own immutable tenant/app/environment scope. Request parameters/cursors never widen scope; viewer is L0-only, investigator/admin may read L1/L2 and record decisions, and only independently provisioned admin may manage keys/configs/alerts. Existing keys never acquire admin rights automatically.
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
