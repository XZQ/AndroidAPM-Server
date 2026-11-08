# AndroidAPM-Server

[AndroidAPM](https://github.com/XZQ/AndroidAPM) SDK 的服务端：把「至少一次」投递的 Android 遥测写入 PostgreSQL durable inbox，按 `(tenant_id, event_id)` 幂等去重、提交后返回精确整批 ACK，再把允许列表内的 OTLP 信号导出到外部 SigNoz；同时提供固定 scope 的查询/BFF、同源 Web 控制台、崩溃符号化与签名远程配置。

本仓库**不 fork SigNoz**、不负责端侧采集。SigNoz / ClickHouse 是外部依赖，版本锁定为 `v0.133.0` / Foundry `v0.2.13`（[版本文件](deploy/signoz/VERSIONS.env)）。唯一分支为 `main`。

> 现状看下面的状态速览；实现细节在 [docs/](docs/)，验证证据与历史变更在 [PROJECT_HANDOFF.md](docs/PROJECT_HANDOFF.md)。README 刻意不记录测试数量等易过时结果。

## 状态速览

| 项 | 当前状态 |
| --- | --- |
| 技术栈 | Python 3.11 / FastAPI / SQLAlchemy 2；`uv` 锁后端依赖，`pnpm` 锁前端依赖 |
| 持久化 | PostgreSQL 是生产 inbox；SQLite 只用于兼容性测试与本地预览 |
| 迁移 | Alembic 九版，head `20261014_0009`，发布前先升级 |
| 统计口径 | Normalization v4：243 数值字段 / 66 精确事件 / 29 固定类别（以 [统计契约](docs/12-统计契约与口径.md) 为准） |
| SDK 契约 | 客户端仍是 durable **V4**；服务端字段 7–9 为向后兼容 optional 扩展，标准 `session_start` / device / OS / span v2 尚未由客户端上报 |
| 云端 | 首尔轻量应用服务器上的 HTTPS 服务已上线，运行事实见 [云端运行环境](docs/13-云端运行环境.md) |
| 验收 | SigNoz 真实接收、通知渠道、备份/PITR、SSO/KMS、真机采集与容量/soak **仍未通过** |

> **公网可达不等于生产验收通过。** 云端配置尚未回收纳管，仓库改动也不会自动同步到线上。

## 能力概览

- **接收与持久化**：legacy Line / Protobuf、V2/V3 显式 envelope、有界 Gzip、整批校验、提交后精确 ACK、安装身份入库前 HMAC 假名化 — [协议](docs/02-SDK-Collector协议.md) · [ADR 0006](docs/adr/0006-发生时身份与installation假名化.md)
- **查询与诊断**：fixed-scope `apmq1` 角色、发布健康、Issue 指纹、事件分页、审计后的 L2 raw、人工发布决策、HMAC 游标 — [总体架构](docs/01-总体架构.md) · [安全与租户](docs/03-安全与租户.md)
- **Web 控制台**：总览 / Issues / 事件 / 性能 / Trace / 版本 / 质量 / 告警 / 设置，短时 HttpOnly 会话 + 绑定 CSRF — [ADR 0007](docs/adr/0007-同源Web控制台与短时会话.md) · [本地功能](docs/11-本地产品功能.md)
- **性能与告警**：分位数 / 趋势 / 覆盖 / 版本差值，独立 evaluator + outbox，规则启停 / 静默 / 确认，凭据与签名配置生命周期 — [ADR 0010](docs/adr/0010-本地性能告警与管理面.md)
- **符号化**：独立 CI key、Java mapping / 每 ABI 未剥离 ELF 有界上传、精确 build-id 路由、租约与输出上限、部分解析状态 — [符号化与远程配置](docs/06-符号化与远程配置.md) · [ADR 0009](docs/adr/0009-符号化Issue指纹与旧链接.md)
- **运维**：保留期证据状态、容量准入背压、私有 worker 指标、异常日志脱敏、逐信号导出计划与 lease fencing — [ADR 0008](docs/adr/0008-保留策略与容量准入.md) · [部署与运维](docs/08-部署与运维.md)

符号化与 OTLP Metrics / Traces 默认关闭（opt-in）；SigNoz 告警模板仍是 `channels: []` / `production_ready=false` 的未就绪模板。

## 架构

```text
AndroidAPM SDK (至少一次投递)
  -> HTTPS Gateway
       认证 / 租户边界 / 配额 -> 有界 Gzip 与 V2/V3 解码 -> schema 校验
       -> installation HMAC -> PostgreSQL durable inbox + (tenant_id,event_id) 去重
       -> 提交后整批 ACK（V2/V3 精确返回 schema/batch/count 头）
  -> leased export worker -> OTLP Logs（默认） / Metrics·Traces（opt-in）
  -> 外部 SigNoz -> ClickHouse -> Dashboard / 告警

发布调查员
  -> 同源 Web 控制台 -> apmq1 一次交换短时 HttpOnly 会话 + CSRF
  -> 有界 Query/BFF 聚合与事件元数据 -> 审计后的 L2 raw
  -> continue / pause / rollback 人工决策记录

可信构建 CI
  -> 独立 apmci1 凭据 -> Java mapping / 每 ABI 未剥离 ELF 上传（SHA-256 + 精确 build 身份）
  -> 私有制品卷 -> leased symbolizer -> 固定 argv 的 R8 retrace / llvm-symbolizer
```

详见 [总体架构](docs/01-总体架构.md) 与 [OTLP 映射与 SigNoz](docs/04-OTLP映射与SigNoz.md)。

## 快速开始

**无 Docker 本地预览（Windows）** — 一条命令准备被忽略的 `.local/` 配置、短期凭据、SQLite 兼容库、合成事件与 Web 产物，并启动 API / 告警进程：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1
```

打开 `http://127.0.0.1:8080` 并粘贴终端打印的 Query key。可选参数：`-PrepareOnly`、`-SkipSeed`、`-SkipSync`、`-ClientRepo <AndroidAPM 路径>`。合成数据只是本地 UI fixture，**不是生产证据**；打印的 ingest key 可在 `adb reverse tcp:8080 tcp:8080` 后用于 Android debug 构建。详见 [Web 控制台与本地联调](docs/10-Web控制台与本地联调.md)。

**完整开发栈（需要 PostgreSQL）**：

```powershell
uv sync --all-groups --frozen
uv run alembic upgrade head
uv run androidapm-admin create-ingest-key --tenant-id local --tenant-name Local --app-id com.example.app --environment development
uv run androidapm-api
uv run androidapm-worker
```

其它入口：`androidapm-alerts`、`androidapm-symbolizer`、`androidapm-admin`（定义见 [pyproject.toml](pyproject.toml)）。容器编排见 [compose.yaml](compose.yaml) 与 [compose.production.yaml](compose.production.yaml)；本地 Compose 不含 SigNoz / ClickHouse，OTLP 端点必须外部提供，否则导出失败并堆积 dead letter。

## 配置与凭据

三类凭据**互相独立、前缀互不接受**；数据库只保存 Argon2id 哈希，明文仅创建时显示一次，应放入 secret manager。

```powershell
uv run androidapm-admin create-ingest-key --tenant-id local --tenant-name Local --app-id com.example.app --environment development
uv run androidapm-admin create-query-key --tenant-id local --tenant-name Local --app-id com.example.app --environment production --role investigator
uv run androidapm-admin create-ci-key --tenant-id local --tenant-name Local --app-id com.example.app
```

- 接收含 installation 的 V2 或任何 V3 事件前，必须配置 `APM_INSTALLATION_HMAC_KEYS_JSON`（版本 → 至少 32 字节 Base64）与 `APM_INSTALLATION_HMAC_ACTIVE_KEY_VERSION`；不可用时 Gateway **fail closed**。轮换时保留旧版本，直到对应行不可能再重放。
- 事件分页另需 `APM_QUERY_CURSOR_HMAC_KEY_B64`；Web 会话另需 `APM_WEB_SESSION_HMAC_KEY_B64`，生产还要求 `APM_WEB_SESSION_COOKIE_SECURE=true`。空或弱配置一律 fail closed。
- `viewer` 只能读 L0 聚合，`investigator` 可额外读 L1/L2 并记录发布决策；只有独立创建的 `admin` 能管理当前 scope 的凭据、配置、告警与应用名称，既有凭据不会自动提权。
- 远程配置版本不可变且用 Ed25519 签名：`uv run androidapm-admin generate-config-keypair`，私钥只放服务端，公钥固定到客户端后再开启轮询。

完整样例见 [.env.example](.env.example) / [.env.production.example](.env.production.example)。部署前执行 `python scripts/check_production_config.py --env-file .env.production` 做强校验：只输出键名与失败原因，不打印任何值，也不改变 local/test 的启动行为。

## 符号化制品

CI 用独立的 `apmci1` 凭据把 `mapping.txt` 上传到 `POST /v1/artifacts/java-mapping`、每个 ABI/build-id 的未剥离 ELF 上传到 `POST /v1/artifacts/native-elf`，请求需带精确 app/versionCode/appBuild/variant 身份与期望 SHA-256，格式见 [符号化与远程配置](docs/06-符号化与远程配置.md)。

`androidapm-symbolizer` 默认关闭：仅在挂载私有制品卷、配置固定 JSON argv 与已审计工具版本后启用；基础镜像刻意不在启动时下载可变工具二进制。真实 R8 / 每 ABI Native 崩溃 fixture 与生产工具镜像仍属待验收项。

## 验证与发布门禁

```powershell
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/verify_docs.py
uv run python scripts/check_secrets.py
pnpm --dir web run lint
pnpm --dir web run typecheck
pnpm --dir web run test
pnpm --dir web run build
git diff --check
```

完整收尾顺序见 [AGENTS.md](AGENTS.md)，门禁口径见 [测试与验收](docs/07-测试与验收.md)。**最近一次通过数量、skip 原因与浏览器证据见 [PROJECT_HANDOFF.md](docs/PROJECT_HANDOFF.md) 最新日期条目**。CI 另执行 PostgreSQL 集成、迁移往返、production overlay `compose config` 与镜像构建；本机无 Docker / PostgreSQL / SigNoz，`integration_tests/` 需要 `APM_TEST_POSTGRES_URL`。

## 部署与未完成的验收

云端已有一条运行中的 HTTPS 部署，但**部署形态未纳管**：对外容器带 `public-trial` 前缀、存在空跑 gateway、反代配置与仓库资产存在差异。仓库改动需重新部署才会在线上生效。服务器侧 P0 待办：回收实际 compose / Caddy / 环境变量、处理主机安全事件、为短期证书设置 `APM_PUBLIC_HOST` 仓库变量以启用到期巡检、确认外部 OTLP 接收端与 dead letter、执行备份/PITR 演练。清单见 [云端运行环境](docs/13-云端运行环境.md) 与 [云端待建设清单](docs/云端待建设清单.md)。

发布前不得把以下项目标记为通过：真实 SigNoz staging E2E 与 Dashboard/告警、通知渠道触发/恢复、真实 R8/每 ABI 符号化、SSO/KMS/HSM、备份恢复与故障注入、容量与 72h soak、物理设备采集、PostgreSQL 生产并发与 TLS 现场验收。

## 文档索引

| 主题 | 文档 |
| --- | --- |
| 规划、架构与协议 | [00 总体规划](docs/00-总体规划.md) · [01 总体架构](docs/01-总体架构.md) · [02 SDK-Collector 协议](docs/02-SDK-Collector协议.md) |
| 安全与信号 | [03 安全与租户](docs/03-安全与租户.md) · [04 OTLP 映射与 SigNoz](docs/04-OTLP映射与SigNoz.md) · [05 Dashboard 与告警](docs/05-Dashboard与告警.md) |
| 符号化与产品 | [06 符号化与远程配置](docs/06-符号化与远程配置.md) · [11 本地产品功能](docs/11-本地产品功能.md) · [12 统计契约与口径](docs/12-统计契约与口径.md) |
| 工程与运维 | [07 测试与验收](docs/07-测试与验收.md) · [08 部署与运维](docs/08-部署与运维.md) · [09 实施路线图](docs/09-实施路线图.md) · [10 Web 控制台与本地联调](docs/10-Web控制台与本地联调.md) |
| 现状与待办 | [13 云端运行环境](docs/13-云端运行环境.md) · [PROJECT_HANDOFF](docs/PROJECT_HANDOFF.md) · [云端待建设清单](docs/云端待建设清单.md) |
| 架构决策 | [docs/adr/](docs/adr/)（0001–0011） |

## 关键不变量

- `2xx` ingest 表示整批已持久提交，V2/V3 还必须精确返回 schema/batch/count 头；非 `2xx` 绝不能让客户端删批。混合合法/非法批次整体拒绝，重复 `eventId` 重放 ACK 成功且不重复进入下游。
- PostgreSQL 是生产 inbox，SQLite / 内存实现只是测试替身；worker 使用 owner/lease/expiry 认领，只有活跃 owner 能标记 delivered 或 failed。
- 租户身份只来自认证凭据；raw secret 永不落库，凭据可撤销、可轮换、可审计。
- V2 resource 是上传批次声明 `BATCH_DECLARED`；V3 occurrence 冻结为 `OCCURRENCE_BOUND`，两者不可混用。
- 缺失、不可用、无效、覆盖未知、迟到与真实零是不同状态，**缺失指标和分母绝不补零**；URL / SQL / 堆栈 / 异常消息等高基数或敏感字段不默认进入 OTLP 属性索引。
- 浏览器不持久化 raw `apmq1`；生产会话要求 HTTPS Secure Cookie、独立签名 key 与 ambient-cookie 写入的 CSRF 校验。

完整清单见 [AGENTS.md](AGENTS.md)。

## 许可

[Apache-2.0](LICENSE)
