# AndroidAPM-Server 项目交接

> 当前验证基线：2026-09-12，分支 `codex/server-foundation`。本文区分“本机已验证”和“需要外部环境验证”，不能把后者改写成已完成。

## 2026-09-12 顺序整改

1. 异常日志隐私：SQLAlchemy 隐藏参数，structlog 与 Uvicorn/stdlib 共用不含消息正文的有界异常诊断；2 项回归覆盖原始参数、SQL、驱动 cause/notes 和 ASGI 二次日志。完整门禁通过：后端 152 tests、mypy 55 files、文档 12、前端 12 tests 及 lint/typecheck/build、依赖锁同步、Ruff、diff check。Windows Temp 退出清理权限告警不影响退出码 0。

2. 趋势与可信度：发布/Issue 缺失桶保留 null，发布桶区分 NO_DATA/UNAVAILABLE/观测 ZERO；Web 断开缺口并保留孤立点，可信度包含新旧版本质量门禁。30 项 Query 回归、155 项后端全测、16 项前端测试及全部静态/构建/文档门禁通过。

环境边界：本轮没有 Docker/Podman/psql，未配置 PostgreSQL 集成连接。SQLite/合成数据和本机门禁不能替代 PostgreSQL、真实 R8/LLVM、Compose/SigNoz/TLS 验收。

## 2026-09-07 顺序整改

基线实现已独立纳入 Git；依次修复异常导出、installation 派生数据、页面证据绑定、质量门禁、HMAC 轮换、查询预算、异步认证、运行指标、保留/背压和符号化租约。每项单独验证、commit、push，并核对远端 SHA。Docker/PostgreSQL/TLS/SigNoz 现场证据仍未取得。

1. 异常导出：增加 normalization v2 数值边界、历史坏行隔离及非预期传输异常的有限重试。新增真实 worker 周期与字段边界回归测试。依赖锁同步、Ruff、mypy（51 files）、pytest（108 passed）、文档（12）和前端四门禁（9 tests）通过；Windows Temp 退出清理告警不影响测试退出码 0。

2. Installation 派生证据：统一最小化 raw/registered/native/symbol input；覆盖 V2/V3、嵌套字符串、跨 key replay 和整批拒绝回归。依赖锁、Ruff、mypy、111 个后端测试及文档门禁通过。新数据修复不代表已清理外部历史存储。

3. 页面证据绑定：覆盖全部查询页面、raw 和人工决策；前端 lint/typecheck/test/build 通过（12 tests），新增失败刷新、乱序响应、跨事件 raw 三个回归。

4. 质量门禁：SDK 缺字段/非法字段/零分母均不可用；影响率增加身份、自健康、丢弃、迟到和最小样本门禁，原始计数保留。21 个相关测试通过，含 12 个新边界回归。

5. HMAC 轮换：查询读取真实 key version，对跨版本窗口/比较显式返回安装连续性断点；覆盖 API 入库到查询及 Issue/trend/distribution，前端显示空缺而非双计数。

6. 查询预算：SQL 过滤/加权聚合、分页解除整窗预检、5 秒查询时限、新增筛选时间索引。测试证明 500 个同类事件在 100 组预算下保留准确计数；SQLite 迁移 upgrade/check/downgrade/upgrade/check 通过，PostgreSQL 执行计划与建索引耗时尚无现场证据。

7. 异步认证：Argon2 有界线程执行、查 key 前固定内存速率门禁、取消不提前释放 hashing 槽；未知 key 保留 dummy verify。覆盖事件循环可推进、饱和/恢复及三种认证入口的查库前拒绝。

8. 运行指标：真实 DB backlog/最老年龄、读取失败 NaN/available=0、独立 worker 监听及采集配置、公网 metrics 隔离；导出计数在提交后更新。17 个相关测试通过，包括独立子进程 HTTP 抓取。

9. 保留/容量准入：终态 raw 清理、保留原 hash/HMAC 去重、quota 过期、事务审计、raw 410、行数/字节阈值和失败回滚；新增迁移 0005 与 ADR 0008。140 个后端测试、前端四门禁（12 tests）和 SQLite 五版迁移 upgrade/check/downgrade/upgrade/check 通过。操作只在隔离测试数据库执行，未清理任何外部数据。

10. 符号化租约：执行前逐个领取、每次独立 token、数据库时钟与过期写入保护、整项处理截止时间、工具取消/超时终止回收、缺制品轮询不消耗工具次数、崩溃重领不超预算；符号化指标在提交后增加。23 个相关回归通过，覆盖同进程旧结果写回、排队期间未占租约、缺制品等待、过期完成/失败以及真实子进程取消。导出完成/失败同步加上未过期租约保护。

### 最终验证

2026-09-07 完整执行依赖锁同步、Ruff、mypy（55 source files）、pytest（150 passed）、12 份文档校验、前端 lint/typecheck/test/build（12 tests）与 diff check，退出码均为 0。pytest 仍有 Windows Temp 退出清理权限告警，不计为测试失败。

以客户端当前干净的 `develop@90dd147` 运行真实 `tools/verify_collector_e2e.py`：Gradle 构建、五版 SQLite migration、真实 `HttpApmUploader` V2/V3 Gzip、exact ACK、typed/occurrence/HMAC 持久化与重复重放全部通过。客户端代码及 Git 状态在本轮验证前后保持一致。

独立执行 `uv run pytest integration_tests -rs`，5 项因未配置 `APM_TEST_POSTGRES_URL` 跳过。本机 Docker/Podman/psql 均不可用，未执行容器构建、Compose、PostgreSQL/SigNoz/TLS 现场验收；这些仍是部署门禁。当前 CI 只触发 main push 或面向 main 的 PR，因此推送功能分支不能视为远端 CI 已通过。

## 2026-09-04 多路由 APM 控制台与 Issue 聚合（历史）

本轮把原有发布健康长页面升级为同源多路由诊断控制台：总览、Issues、Issue 详情、investigator 事件探索/详情、版本发布和数据质量均有独立可恢复 URL；性能、告警和设置以 `UNAVAILABLE`/`UNCONFIGURED` 页面公开真实建设边界。viewer 导航不显示事件枚举入口，直接访问事件页也只返回权限说明；L2 raw 不自动读取，仍要求 purpose/reason 并等待服务端审计提交。

Query/BFF 新增 `GET /v1/query/issues/{fingerprint}`，在固定 scope 和有界窗口内返回真实事件/安装计数、首次/最近发生、最多 24 个 occurrence-time 桶、版本与场景分布。设备型号和 Android 版本没有标准客户端 resource 契约，因此返回 `UNKNOWN_COVERAGE`，不会从任意 context/extras 伪造；release-health 同步增加真实零值趋势桶。Crash-free/ANR-free Sessions 继续保持 `UNAVAILABLE: SESSION_ID_NOT_PROVIDED`。

当前已执行并通过：`uv sync --all-groups --frozen`、Ruff、mypy（51 source files）、pytest（96 passed）、12 份必需文档验证，以及前端 ESLint、TypeScript、Vitest（5 files / 9 tests）和 production build。当前生产 bundle 为 JS 290.45 kB（gzip 92.95 kB）、CSS 40.77 kB（gzip 8.28 kB）。pytest 仍只有本机 `.pytest_cache`/Windows Temp 清理权限 warning，测试退出码为 0。

同日使用 production build、真实同源 FastAPI、synthetic SQLite fixture 和应用内 Chromium 完成多路由浏览器验收：

- 1536 × 1024：登录后进入固定 scope 总览，发布指标、真实零值趋势和 Top Issues 正常；从 Top Issues 下钻到 Issue 详情，再打开独立事件详情，URL 与返回路径正确。
- Issue 版本/场景 tab 返回真实聚合；设备 tab 明确显示 `UNKNOWN_COVERAGE` 和标准资源缺失原因。页面不会自动读取 L2 raw，填写 purpose/reason 后才出现原始/符号化并列证据，浏览器控制台无 warning/error。
- 事件探索、版本发布、数据质量、性能、告警、设置路由均可访问；后三者明确显示 `UNAVAILABLE`/`UNCONFIGURED`，未伪造指标或声称云服务已接通。
- Issue 深链刷新后保留同一 URL 和页面；深浅主题切换可持久恢复。768 CSS px 重排用于覆盖 1536 桌面在等效 200% 文本缩放下的布局，页面无水平滚动。
- 390 × 844 首轮发现宽表内容将根页面撑到 727 px；修复为 table 自身受控横向滚动并增加 paint/layout containment 后，根页面 client/scroll width 均为 375 px，`window.scrollX` 固定为 0，移动导航抽屉可打开且所有入口可见。

设计 fidelity ledger：左侧 224 px 工作台信息架构、顶栏固定 scope/时间窗、黄色可信度门禁、紧凑指标带、趋势+依据双栏、Issue 趋势+分布双栏均与 `docs/design/` 概念图一致。刻意不复刻概念图中的“最近告警”、Issue 生命周期/负责人、设备/OS 值和异常类标题，因为当前分别缺生产告警实例、生命周期 API、标准资源契约和安全 L0 字段；用能力状态或覆盖未知替代，避免演示数据冒充真实能力。

以上是本地 UI、同源 API 与 synthetic 数据证据，仍不证明 PostgreSQL、SigNoz、TLS、通知或公网部署。既有 2026-08-28 迁移往返属于历史证据。

## 已实现

- Python 3.11 + FastAPI/uv 工程、严格配置、结构化日志、Prometheus 端点。
- `POST /v1/events`：Bearer ingest key、tenant/app/environment scope、schema/header/content 校验。
- 应用层有界 body 读取、单成员 Gzip 有界解压、Line Protocol、4-byte big-endian length-prefixed Protobuf，以及显式 `ApmBatchEnvelope` V2/V3。
- 数据库固定窗口 request/event quota；PostgreSQL `(tenant_id,event_id)` 最终唯一约束。
- 同批和历史重复 ACK；同 eventId 不同内容显式 `409`；事务 commit 后才生成整批 2xx ACK。V2/V3 都要求响应 schema/batch ID/event count 与请求精确一致。
- Inbox worker 的 owner/lease/expiry、过期重领、owner-only 完成、退避、`Retry-After` 和 dead letter。
- Android event 到 OTLP Logs 的 resource/severity/context/eventId 映射；兼容 string fields 只按源代码审核白名单恢复数值/布尔类型。
- SigNoz v2 Perses Dashboard、v2 alert templates 和 service-account API key 幂等同步器。
- `GET /v1/config`：Ed25519 签名、递增 revision、过期、灰度稳定分桶、ETag/304；CLI 创建 key/publish config 并记录审计。
- V3 occurrence envelope：客户端发生时 release/build/installation/native identity、`b3-` batch ID、提交后 exact ACK；legacy/V2 继续兼容且保持低质量标签。
- 入库前 installation 假名化：tenant/domain/key-version HMAC，明文从 durable payload 删除；轮换后 replay 按原行 key version 比较。
- 初始 normalization/field registry 与 fixed-scope Query/BFF：`apmq1` viewer/investigator、发布健康、指纹、数据质量、事件分页/元数据/raw、人工 `continue/pause/rollback` 决策和审计。
- 独立 `apmci1` CI key 与 `artifact:write` scope；Java mapping/未剥离 Native ELF 的有界流式上传、SHA-256、格式/ABI/.symtab/GNU build-id 校验、精确身份幂等与冲突审计。
- Crash durable symbolization job：metadata_missing、awaiting_symbols、symbols_missing、制品上传重排队、owner/lease/expiry、retry/final failure 和 OTLP 符号状态/指纹映射。
- R8 retrace/llvm-symbolizer 固定 JSON argv adapter：不经过 shell，限制运行时间和输出，保存工具版本；symbolizer 默认关闭且工具由部署镜像提供。
- 五版 Alembic schema、非 root 只读容器、Gateway/export worker/symbolizer/PostgreSQL Compose、共享私有制品卷和官方 SigNoz/Foundry 版本边界。

## 2026-08-28 V3、Query/BFF 与真实跨仓闭环

当前统一工作树已实现 ADR 0006：Android 客户端以 additive `Apm.init(application, config, occurrenceContext)` 保持 `ApmConfig`/`ApmEvent` 原 data-class ABI，durable codec V4 在 dispatcher/IPC/SQLite handoff 前保留 occurrence；`PROTOBUF_ENVELOPE_V3` 使用 typed fields、每事件 occurrence 和 exact post-commit ACK。服务端 `20260828_0003` 增加 occurrence/normalization/query schema，V2/V3 installation 在 durable insert 前只留下 tenant-scoped HMAC/key version。

同轮新增首个 Query/BFF：凭据固定 tenant/app/environment，viewer 只能读取 L0；investigator 才能分页读取 L1、purpose/reason 审计后的 L2 raw，并记录人工继续/暂停/回滚。窗口、扫描行数、page limit 和 HMAC cursor 都有上限；session 指标固定返回 `UNAVAILABLE: SESSION_ID_NOT_PROVIDED`。

客户端执行：

```powershell
python tools/verify_collector_e2e.py --server-repo D:\workspace\AndroidAPM-Server
```

脚本构建真实 `HttpApmUploader`，启动实际 FastAPI/uvicorn，分别发送并重放 V2/V3 Gzip batch。迁移到 head 的测试用 SQLite inbox 最终只有 4 个唯一 eventId，并独立核对 exact ACK、V2 typed scalar、V3 occurrence release/build/variant、native frame、HMAC/key version、低质量请求声明不覆盖 occurrence 和 installation 明文未进入 `payload_json`。这仍是 SQLite 兼容闭环，不是 PostgreSQL/TLS/SigNoz 生产 durability 证据。

## 2026-08-28 当前统一工作树最终本机门禁

在保留 Server 与 Android 两仓现有未提交改动的前提下，Server 当前统一工作树执行：

```powershell
uv sync --all-groups --frozen
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/verify_docs.py
pnpm --dir web install --frozen-lockfile
pnpm --dir web run lint
pnpm --dir web run typecheck
pnpm --dir web run test
pnpm --dir web run build
git diff --check
```

依赖锁同步、Ruff、mypy（51 source files）、pytest（95 passed）、12 份必需文档验证和 diff check 全部通过。前端 ESLint、TypeScript、Vitest（3 files / 5 tests）和 production build 全部通过；生产 JS 为 224.49 kB、gzip 70.69 kB。pytest 仅报告本机 `.pytest_cache` 写权限警告，不影响收集、执行或退出码。文档校验器会排除 `.venv` 与 `node_modules`，不会把本地第三方依赖 README 当成仓库文档。

另对唯一明确的随机临时 SQLite 数据库执行 `alembic upgrade head -> downgrade base -> upgrade head -> alembic check`；三版 revision 的 upgrade/downgrade 均完成且 autogenerate 报告 `No new upgrade operations detected`。临时数据库随后已删除。该结果只证明迁移的 SQLite 兼容往返和 metadata 一致性，不能替代 PostgreSQL DDL、锁、事务和并发验收。

同日使用 `scripts/start-local.ps1` 的 synthetic fixture 和真实浏览器完成同源 Web smoke：`apmq1` 一次交换、固定 scope、发布健康/数据质量、Crash/ANR 指纹筛选、L1 事件详情、purpose/reason 审计后的 L2 raw、三种人工决策选择、暂停决策写入/回读、`NO_DATA` 非零值空状态、深浅主题、390 px 窄屏无页面横向溢出和退出流程均通过，浏览器控制台无 warning/error。烟测发现并修复 favicon 404；最终 `/favicon.svg` 返回 `200 image/svg+xml` 和显式缓存策略。fixture 与本地 SQLite 仍不构成真实生产数据或云端 durability 证据。

同日只读现场能力审计确认：Windows PATH 中没有 Docker、Podman、`psql`、R8 `retrace` 或 `llvm-symbolizer`；WSL `2.7.10.0` 可用，默认 `Ubuntu-24.04`/WSL2 处于 `Stopped`，本轮没有为探测而启动它。因此镜像/Compose/PostgreSQL/SigNoz、真实 R8/LLVM、TLS/通知、备份恢复、故障注入和 72h soak 仍是现场阻塞，不能由上述本地门禁替代。

## 2026-08-28 真实场景规划复核与本机门禁

本轮以干净客户端 `AndroidAPM/develop@e1dd627` 重新核对 wire、durable outbox、事件目录、SDK 自健康、PII、动态 Header/endpoint 和 signed remote-config 实现，并把首个产品切片冻结为“新版本 `crash/java_crash`、`anr/anr_detected` 可信回归闭环”。规划明确：普通 `app_exit` 不算 Crash，异常退出证据不与现场事件相加；无统一 telemetry session 时不计算 Crash-free/ANR-free sessions；高基数 raw 字段不默认进入 OTLP attributes。

复核在实现前确认两个生产阻塞：V2 resource 无法可靠归因升级前 outbox 事件，且 installation 明文当时会进入 `payload_json`。这两个本地代码阻塞现已由 accepted ADR 0006、V3 occurrence 和 installation HMAC 关闭；本节以下 71-test 结果保留为实现前的历史证据，不代表当前最新门禁。

在保留现有未提交 M3 实现的统一工作树上执行：

```powershell
uv sync --all-groups --frozen
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/verify_docs.py
git diff --check
```

该轮依赖锁同步、Ruff、mypy（42 source files）、pytest（71 passed）和文档验证通过；结果现已被上面的 V3/Query 闭环与本文最终门禁记录取代。Docker 当时不可用，因此没有执行镜像 build、Compose/PostgreSQL、Foundry/SigNoz 或真实 R8/LLVM smoke。

## 2026-08-27 本机统一验证

`codex/server-foundation` 已快进包含 Collector V2 的 `f1214c1`、`2feb2f5` 两个提交，当前主工作区同时恢复了尚未提交的 M3 制品/符号化实现。以下结果验证的是这个统一工作树，不代表尚未提交的 M3 变更已经发布到远端：

```powershell
uv sync --all-groups --frozen
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/verify_docs.py
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
git diff --check
```

依赖同步、Ruff 和文档校验通过；mypy 对 42 个 source files 无问题；pytest 收集并通过 71 tests，覆盖 legacy/V2 ingest、精确 ACK、制品上传、symbolization 状态机和 worker。两版 Alembic migration 在全新临时 SQLite 数据库完成 `upgrade -> downgrade base -> upgrade`。pytest 仅报告本机 `.pytest_cache` 写权限警告，不影响测试执行或结果。

本机没有 Docker 命令，因此不能执行镜像 build、Compose/PostgreSQL 冒烟或真实 SigNoz/R8/LLVM 验证。

## 2026-07-31 本机验证

```powershell
uv sync --all-groups --frozen
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/verify_docs.py
git diff --check
```

以上命令通过：ruff 无问题，mypy 对 35 个 source files 无问题，pytest 为 57 tests / 0 failures。协议测试覆盖 V2 media/schema 协商、SDK/resource/header 一致性、稳定 batch ID、12 种 typed scalar、JSON-safe 非有限浮点保真、任意精度输入上限、legacy 字段隔离、原子拒绝、提交后精确 ACK 和重复重放。

同日从 Android 客户端仓库运行 `tools/verify_collector_e2e.py`：它以 JDK 17 构建真实 `apm-model` 与 `apm-uploader`，启动实际 FastAPI/uvicorn Gateway，通过 Gzip HTTP 连续发送两次相同 V2 batch。真实 `HttpApmUploader` 两次均收到精确 ACK；测试用 SQLite inbox 最终只有 2 条唯一事件，12 种标量、resource、schema 和 protocol 元数据均经数据库读取复核。这是跨语言/真实 HTTP 兼容证据；SQLite 仅用于该测试，不构成 PostgreSQL 生产 durability 或并发结论。

## 2026-07-16 本机验证

```powershell
uv sync --all-groups --frozen
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/verify_docs.py
```

以上命令通过；本地测试基线为 56 tests，0 failures，核心包分支覆盖率 70.82%（生成的 Protobuf 代码排除）。另使用全新临时 SQLite 数据库执行两版 migration 的 `alembic upgrade head -> downgrade base -> upgrade head`，随后 `androidapm-admin create-ci-key` 成功创建 tenant、独立哈希 CI 凭据和审计记录。这是迁移/CLI 兼容冒烟，不替代 PostgreSQL 事务与并发验证。

## 未完成或尚未现场验证

- 本机没有 Docker/Compose，所以镜像 build、Compose health、官方 Foundry 安装和真实 SigNoz OTLP/Dashboard/告警尚未执行。
- Foundation 提交的 GitHub CI 已两次通过 PostgreSQL 16.9 migration round-trip、并发 Inbox/`SKIP LOCKED`、Compose config 和镜像 build。本次 M3 变更新增并发 artifact identity 测试；在对应 PR workflow 运行前仍属于待验证变更。
- Dashboard/alert JSON 已按 SigNoz `v0.133.0` 当前官方 v2 API 源码编写并做离线测试，但还没有由真实 SigNoz 校验查询表达式和渲染结果。
- Java/Native 制品 API、私有本地/持久卷 store、任务状态机和工具 adapter 已实现；生产对象存储/KMS、固定 R8/LLVM 工具镜像、真实 mapping/ELF/crash E2E 尚未完成。基础镜像没有工具二进制，`APM_SYMBOLIZATION_ENABLED` 默认 `false`。
- OTLP Metrics 独立信号、Trace context、托管诊断 UI、生产身份系统/RLS/KMS/HSM、真实多租户部署、备份/PITR、TLS/WAF、压测/72h soak 尚未验收；fixed-scope Query/BFF 本地实现不属于这些现场完成项。
- Android SDK 已有动态 header/endpoint provider、签名远程配置、V2 兼容 resource 和 V3 occurrence identity；生产凭据签发/轮换及服务端部署联调仍待完成。精确符号化 schema 已能保存 versionCode/variant/ABI/build-id/module-relative pc，但真实 Java/Native crash 采集、工具和制品 fixture 仍不足以完成现场验收。

## 下一步顺序

1. 在有 Docker 的 Linux/Windows 环境跑 PostgreSQL integration、Compose 和真实 SigNoz staging E2E。
2. 修正真实 SigNoz 返回的任何 dashboard/rule schema/query 差异并保存截图/请求证据。
3. 构建固定 R8/LLVM 工具镜像，接 S3 兼容对象存储/KMS，用真实 Java 与每 ABI Native 样本完成符号化 E2E。
4. 用真实跨升级 outbox、Java mapping 和每 ABI Native crash fixture 验收 occurrence/build/native identity coverage，完成 SDK→Gateway→symbolizer→SigNoz 联调。
5. 补生产 RLS、已实现 retention/backpressure 的现场容量验收、备份恢复、故障注入、容量/性能和安全门禁。
