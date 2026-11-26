# AndroidAPM-Server 项目交接

> 当前基线：2026-11-21，服务端唯一分支 `main`。本文区分“本机已验证”“公网只读已验证”和“需要外部环境验证”，历史条目保留当时事实。

## 2026-11-26 全仓深度审查与确证修复（本地已验证，未部署）

对后端核心/协议/Worker/OTLP/符号化、API/DB/11 版迁移、Web 控制台与脚本/部署/CI/文档全量审计并完成两轮修复（含全部待审查项按最佳实践落地），补充对应回归：

- **第二轮审查项落地（最佳实践）**：
  - `ReleasesPage.tsx` 并行拉取 `getDataQuality(filters)`，决策快照写入真实 `quality.state`（含 `nativeCrashEvents`/`nativeTombstoneEvents`）；`ConsoleLayout.tsx` 对非时间窗路由（`/settings`、`/alerts`、`/events/:id`）不随 `filters` 重挂载，保留未保存表单、一次性 secret 与 L1 审计；`useProductRead.ts` 接入 `AbortController` 取消过期 GET 且刷新不闪白整页。
  - 新增 append-only 迁移 `20261126_0011_settings_status_index.py`（模型同步声明 `ix_inbox_scope_status` 与 `ix_symbol_job_tenant_status`），使 `GET /v1/manage/settings` 状态聚合走覆盖索引。
  - `db/query.py` + `api/query.py` 移除未用 `installation_identity_quality` 分组并按端点裁剪 `load_window_facts` 维度（`/data-quality` 将高基数安装/会话/设备/OS 折叠为存在性）。
  - `scripts/check_production_config.py` 强制 session/cursor/installation 三域及环内密钥互不复用；`scripts/check_secrets.py` 增补内嵌 `APM_DATABASE_URL` 口令与 `APM_INSTALLATION_HMAC_KEYS_JSON` 真实值检测。
  - `symbolization.py` 在 POSIX 使用独立进程组 `start_new_session` + `killpg` 与 2s 有界清理超时；`otlp/metrics.py` + `otlp/traces.py` 补齐 `android.apm.app_id` 与 `OTLP_SCHEMA_URL`；`performance_catalog.py` 对齐 `queueBytes` 为 `integer` 并清除 `normalization.py` 11 条重叠定义；`alerts.py` + `AlertsPage.tsx` 按指标单位开放 `ratio_delta`（`[-1, 1]`）与 `http_status`（`>= -1`）有符号阈值。
  - `auth_budget.py` + `auth/ci_auth/query_auth.py` 改为共享懒初始化 Argon2id dummy hash；`domain.py` 规范化 `session_id` UUID 并拒绝 `installation_id` 等发生时身份的控制字符；`admin.py` 为 `publish_config` 加租户行锁并拒绝为停用租户发凭据；`api/product.py` 统一 `acknowledged_by = query-key:{key_id}` 并校验 `displayName`/`newRelease`/`baselineRelease`；`.github/workflows/ci.yml` 增加 `uv`/`pnpm` 缓存；`docs/adr/0007` 增补 2026-11-07 复核轮未知环境注记。

- **数据丢失 / 正确性修复**：
  - `api/artifacts.py` + `artifacts.py`：重复上传失败时不再误删磁盘上已提交的活制品文件；`LocalArtifactStore.promote`/`discard` 补齐目录创建与删除的 Windows `\\?\` 长路径处理。
  - `query.py`：`native_crash_events` / `native_tombstone_events` 移至 `quality_state = gate_state` 之前计算，修复安装样本或 SDK-health 门禁未满足时二者静默变为 `UNAVAILABLE`/`value=None` 的计数 bug。
  - `db/query.py`：`load_window_facts` 中 `session_schema_version`/`session_count` 改 `CAST(... AS BIGINT)`，避免 PostgreSQL 32 位 `int4` 溢出 500；`get_releases` 改用 `list_discovered_releases`（`_execute_bounded` + `occurrence_timestamp_ms` 排序 + 单整型 `COUNT` 预算探针）。
  - `alerts.py` + `performance.py` + `api/product.py`：`.p95` 告警改为按单事件 `(module, name)` + `occurrence_only` 过滤；后台 `alert_loop` 启用按规则隔离预算错误（防止单条 `.p95` 回滚同 scope 的 SQL 计数规则）；`deliver_once` 在 PG 下加 `FOR UPDATE SKIP LOCKED`；`GET /v1/alerts` 容忍非法渠道配置而不 503；`edit_rule` 重置 `last_sample_count=0`；CI key 创建/撤销审计显式写入 `environment: null`；重复撤销保留原 `revoked_at`。
  - `db/symbolization.py` + `symbolization.py` + `symbolizer_worker.py`：`resolve_artifact` 补 `status == active` 过滤；统一 `build_id` 小写键；`requeue_matching_missing_jobs` 改批量 `UPDATE` 并更新 `updated_at`。
  - `logging.py` / `main.py` / `observability.py` / `normalization.py` / `otlp/logs.py` / `remote_config.py` / `web_auth.py` / `api/web.py` / `db/maintenance.py`：修复 `SafeExceptionFormatter.formatException` 在 `ei[1]` 为 `None` 时崩溃与未知 `log_level` 报错；生产模式大小写无关并关闭 `/openapi.json`、lifespan 释放 `HASH_VERIFIER`；`_snapshot_loop` 捕获会话异常不永久退出；ANR 空白 `mainThreadStack` 回退 `stackSamples`；修复 naive UTC 在 `otlp/logs.py`/`remote_config.py` 偏移及 `b64decode(validate=True)`；拒绝 `bool` session 版本；登出清 Cookie 补 `secure`/`httponly` 并补 `/apps/{app_id}` SPA 路由；容量准入捕获 PG `57014`。
- **性能与死代码清理**：
  - `protocol/compression.py`：修复 `decompressor.flush(remaining + 1)` 每次小请求分配 8.4 MiB 缓冲区，改为分块上限。
  - `performance.py` / `otlp/metrics.py` / `worker.py` / `tracing.py` / `db/inbox.py` / `query.py`：`build_performance` 按事件预分组（消除 O(275·N) 扫描）与单次排序；`otlp/metrics.py` 预索引 `METRIC_FIELDS`；`worker.py` 批量续租与批量 `mark_delivered`、按 signal pending 懒建客户端、关闭异步引擎；`get_trace_detail` 按 1,000 span 上限截断加载；`db/inbox.py` 消除每事件三次 JSON/UTF-8 编码；移除 `normalization.py` 重复 `FieldDefinition` 与 `protocol/envelope_v2,v3.py` 预检 map/frame 上限。
- **前端控制台与测试环境**：
  - 修复 `OverviewPage` 单版本自动发现把 `baselineRelease` 设成等于 `newRelease` 触发 400 的死锁、移除不可达分支并补 Native delta 与 `encodeURIComponent`；`App.tsx` 主题读写加 `try/catch` 与持久化窗口校验；补齐 `SESSION_KEY_ROTATION_BREAKS_CONTINUITY` / `SESSION_START_NOT_IN_WINDOW` 标签与前后端类型对齐；修复 `IssuesPage` 指纹大小写搜索、`SettingsPage` 过期凭据误显 `ACTIVE` 与空状态、`IssueDetailPage` `NO_DATA` 分布与摘要网格布局、`TracesPage` O(N) 深度计算、`DecisionPanel`/`AlertsPage`/`PerformancePage` 输入校验、`productApi.ts` 异步 CSRF、`api.ts` 422 `detail` 解析、`ConsoleLayout` 死按钮与图标区分；修复 `.quality-grid`/`.console-metric-row`/`.trust-*` CSS 冲突并移除死规则；`web/src/test/setup.ts` 兼容 Node 25+/26 全局 `localStorage`。
- **部署、脚本、测试与文档漂移**：
  - `alembic/env.py` 直建 `create_async_engine` 避免含 `%` 密码崩溃与泄漏；`.env.production.example` 补 `APM_ENVIRONMENT=production`；`compose.yaml` 透传 12 项缺失运维变量并为 `worker`/`symbolizer`/`alerts`/`migrate` 覆写健康检查；`Dockerfile` 移除未用 `proto`/`eslint.config.js` 拷贝；`scripts/check_secrets.py` 补 `apm1_` ingest key 扫描；`scripts/check_tls_expiry.py` 补 `None` 证书与方括号 IPv6 处理；对齐 3 份 SigNoz 告警模板 `groupBy`；修复 12 处测试 `Settings` 缺 `_env_file=None` 与无效果 secret-leak 断言；同步 `README.md` / `AGENTS.md` / `docs/01,03,04,10,13` 事实漂移。
- **本轮完整门禁（本机，全部通过）**：Ruff `All checks passed`；mypy `63 source files` 无问题；pytest **609 passed / 1 skipped**（skip 为未配置 `APM_TEST_NDK_BIN`；两轮合计 +11 项回归）；11 版 Alembic SQLite 往返（`upgrade head` / `check` / `downgrade -1` / `upgrade head` / `downgrade base` / `upgrade head` / `check`）无漂移；15 份文档校验、secret scan、`git diff --check` 通过；前端 ESLint / TypeScript / **8 suites 34 tests** / 生产 build 通过（CSS 30.83 kB / gzip 6.74 kB，JS 323.53 kB / gzip 101.77 kB）。本机无 Docker / PostgreSQL，镜像与 PG 集成仍由 CI 验证。

## 2026-11-26 待审查项逐个核验与修复（已并入同日提交，未部署）

- **release discovery 扫描预算（确证问题，已修）**：`/v1/query/releases` 原直接 `GROUP BY` 全窗口、无行数预算。新增 `db/query.py::enforce_release_discovery_budget`，复用 `_execute_bounded`（5s 超时 + PG statement_timeout）做 `LIMIT max_rows+1` 探针，超限返回 `query_budget_exceeded`（422），与既有查询同口径。
- **release discovery 时间口径不一致（连带确证问题，已修）**：原按 `event_timestamp_ms` 过滤，与 `QueryWindow`（occurrence-time）及其余产品查询不符；聚合与预算探针统一改为 `occurrence_timestamp_ms`。新增回归覆盖超预算拒绝、跨租户不计入、event-time 出窗但 occurrence 在窗仍命中。
- **`ReleaseSummary.has_data` 冗余（确证，已删）**：全仓仅该响应链使用且恒为 true，无跨 API 复用；从 `query.py` 模型、`api/query.py` 构造与 `web/src/types.ts` 同步移除。
- **mypy 生成存根报错（确证配置缺陷，已修）**：`exclude` 只挡目录发现、不挡导入跟随，导致 `apm_event_pb2.pyi` 的 15 项 protobuf 存根诊断污染业务检查。新增 `[[tool.mypy.overrides]] module=androidapm_server.generated.apm_event_pb2 ignore_errors=true`，业务 63 文件仍 strict，现 `Success: no issues found in 63 source files`。未改生成文件、未放宽业务规则。
- **release discovery 访问审计（核验为非问题）**：该端点为 viewer 可读 L0 低基数版本摘要，不含 eventId/安装标识/raw，与其余 L0 聚合一致不逐次审计；不加审计以免 audit_logs 无谓增长。
- **GitHub CI（已查明）**：`8a328ad` CI success；`b5418d4` CI run 44979310624 在 `Static verification` 失败（即 ASYNC230，上轮已本地修复）。远端该失败为历史记录，需提交+推送后重跑才会转绿；本轮未推送。
- **TLS 巡检（已查明，正常）**：run #20（2026-11-25 12:52 UTC）success，近 4 次定时巡检均 success，证书自动续期链路工作正常；`/health/ready` 公网返回 `{"status":"ready"}`。
- **云端验收（外部，未变）**：真机 V3 E2E、OTLP 接收/导出（云端 export=false）、PITR/自动异地、主机安全事件等仍待外部环境，见 docs/13 与云端待建设清单。
- **本轮完整门禁（本机，全部通过）**：Ruff `All checks passed`；mypy `63 source files` 无问题；pytest **598 passed / 1 skipped**（skip 为未配置 `APM_TEST_NDK_BIN`；新增 release discovery 预算回归）；15 份文档校验、secret scan、`git diff --check` 通过；前端 ESLint / typecheck / **8 suites 31 tests** / 生产 build 通过（JS 320.60 kB，gzip 100.70 kB）。改动共 13 文件，均未提交、未推送、未部署。

## 2026-11-25 全仓审计（文档漂移与发布状态）

- 审计起点：`b5418d4`，工作树初始干净且 `main` 与 `origin/main` 一致。补记其前两次提交：`8a328ad` 增加 release discovery 与控制台筛选持久化；`b5418d4` 修复 Windows extended-length artifact promote 路径。当前云端发布证据仍仅到 `aac2199`，故这两次提交尚无此处记录的部署证据。
- 确证修复：当前数值契约为 246（含 3 个 network `statusCode`），更新 docs/00、05、09、11 的现状数字；docs/05 补列 `GET /v1/query/releases`。将 release discovery SQL 上限 50 提取为 `MAX_RELEASE_VERSIONS` 常量，保持运行行为不变。
- 运行证据：本轮尝试门禁时发现当前 shell 找不到 `uv`，未能实际执行 Ruff/mypy/pytest；此前 2026-11-21 最新已记录结果为 597 passed / 1 skipped，不能视为本轮改动已验证。需在具备项目工具链的环境重跑完整 finish checks。
- 待审查：release discovery 聚合扫描预算及审计是否应与 Trace 等自由字符串查询一致（需评估索引/租户数据量/隐私口径）；`ReleaseSummary.has_data` 在该 endpoint 目前恒为 true，是否保留取决于跨 API 类型约定；前端 bundle 本地构建变化不代表部署；证书 2026-11-26 到期前需查 tls-monitor 与续期结果；GitHub Actions 对上述两提交的 CI 状态需核对。
- 云端仍待验收：真机 V3 E2E、OTLP 接收与导出、PITR/自动异地副本、主机安全告警处置、audit_logs 留存决策等，以 `docs/13-云端运行环境.md` 和 `云端待建设清单.md` 为准；本地审计不改变这些外部状态。

## 2026-11-21 晚续轮（跨仓审计 → statusCode 契约 → 异地备份 → 客户端 strict V3 → 二次发布）

- **跨仓覆盖审计**（客户端 AndroidAPM develop `4530fcb`）：客户端 65 个固定 module/name 全部被服务端 66 事件目录覆盖（66 = 65 + 预留 core/session_start）；字符串形态数值兼容；dropReason 22 枚举一致；`threshold` 为配置回显有意不注册；`statusCode` 原仅 raw → 本轮注册。
- **statusCode 契约**（服务端 `d1cde11`，已推送、CI success、**已发布上线**）：network_request/network_error/network_phase 各增 integer `statusCode`（unit http_status，客户端 -1/0 哨兵有效，0 显示 ZERO），数值契约 243→246、network 18→21、integer 86→89；OTLP UNIT_MAP 补 http_status→1；+1 回归；docs/12/README/AGENTS 同步。门禁：Ruff、mypy 63 files、pytest **597 passed / 1 skipped**、15 docs、secret scan 全过。二次发布沿用首次流程（fetch→build→up gateway/worker/alerts→重建 public gateway），公网 200、ready 200，旧镜像 tag rollback-20261121b。
- **异地备份闭环**：服务器 cron 每日 03:17 本地 pg_dump（保留 7 天）已在跑；本轮新鲜备份拉回本机 `.local/backups/androidapm-20261121T150734Z.dump`（SHA256 与服务器一致），并在服务器临时库 `restore_check` **实际 pg_restore 成功**（alembic 0010、inbox 5 行、query_keys 5 行核对后删库清理）。异地副本 + 恢复演练两项首次闭环；PITR/自动化异地同步仍待建设。
- **客户端 strict V3 落地**（AndroidAPM develop `0f68241`，已推送）：sample app 新增构建期 opt-in（`-Papm.ingestKey=`，不进源码）→ PRODUCTION_STRICT + PROTOBUF_ENVELOPE_V3 + occurrence 身份（BuildConfig 版本/变体 + 一次性匿名安装 UUID 本地持久化）+ Bearer ingest key + staging resource context；无 key 时保持 logcat/soak 原行为。`:apm-sample-app:compileDebugKotlin` BUILD SUCCESSFUL（JBR 25 / Java 17 toolchain）。
- **联调凭据已创建**（90 天，scope seoul-staging / com.apm.sample.debug / staging）：ingest key `753ced7df926059b97` + 配套 admin query key `6df5cc928f2b8ea7f6`（会话实测 200）；明文仅在 `.local/SAMPLE-V3-CREDENTIALS.md`。**真机安装上报仍未执行**（需设备/模拟器），是下一步验收动作。
- 剩余：真机 V3 上报 E2E、OTLP 接收端与导出验收、31 主机安全事件、PITR/自动异地备份、绑域名、客户端 V5/session_start（会话率前提）、告警空窗口恢复语义与 audit_logs 留存决策。

## 2026-11-21 审查轮（两项确证修复 + 四项决策记录）

主线复核告警链路、保留清理、导出 worker、认证/CSRF、前端 API 层与部署资产。核心不变量（去重、fencing、CSRF 绑定、HMAC、缺失≠零、告警保留只清已解决实例、11-15 五项修复无回归）复核成立。

**确证并修复**：

| 项 | 核实结论 | 修复 |
|---|---|---|
| 通知渠道配置错误打断送达循环（P1） | `deliver_once` 在领取提交后调用 `configured_channels()`，`APM_NOTIFICATION_CHANNELS_JSON` 非法时 ApiError(503) 逃逸至 `alert_loop` 整轮 except：被领取行卡 SENDING 至 30s 租约过期，每轮重领烧一次 attempt 直至 RETRY_EXHAUSTED（配置修好后通知也永久失败），同轮其余 pending 通知全部跳过 | 渠道解析包 try/except，该行 `FAILED/CHANNEL_UNCONFIGURED` 落库 + `alert_channel_configuration_invalid` 日志，循环继续；预算仍约束重复配错。补回归：两行同轮失败不逃逸、其余行不受影响、配置修复后正常送达 |
| `APM_ALERT_HISTORY_RETENTION_DAYS` 未透传 Docker 部署（P2） | Settings/docs 已有该变量，但 compose 环境锚与 `.env.example` 均缺失，容器部署下 env 文件设置不生效 | compose 补透传（默认 90）、example 补条目；新增部署资产守卫要求 4 个告警变量同时存在于 compose 与 example；docs/08 同步 |

**核实后保留（需产品/合规决策或打磨，详见云端待建设清单 2026-11-21 节）**：告警空窗口不自动恢复（有意 fail-closed，改语义需独立决策）；audit_logs 无保留策略（合规最短留存需运营决策）；告警清理无租户公平性（与 inbox 同款既有模式）；前端时间无时区标注（显示正确，仅缺标注）。

**本轮验证（本机）**：Ruff、mypy 63 files、`pytest --basetemp <短目录>` **593 passed / 1 skipped**（skip 为未配置 `APM_TEST_NDK_BIN`；新增 2 项回归）、15 份文档门禁、凭据扫描全过。前端本轮无改动。

**发布状态**：**2026-11-21 22:15 已发布上线**（见下方同日发布条目）。

## 2026-11-21 云端发布（aac2199 上线，首尔）

发现本机 `~/.ssh/id_ed25519_sub2api` 可免密登录首尔服务器（ubuntu），首次具备服务器侧执行能力。按 runbook 完成发布：

- **回收确认**：部署根 `/opt/androidapm`（releases + current 软链 + shared/.env.seoul）；compose 项目名 `androidapm-server`，文件组合 `compose.yaml + compose.seoul.yaml`（staging env、export=false、内存 override）；对外容器 `androidapm-public-trial-gateway` 是**手工 docker run**（8081→8080，env 在 trials/gateway.env，cookie secure=true、独立会话密钥）；Caddy 是**宿主机 systemd 服务**（非容器），配置 trials/Caddyfile，IP 站点用 shortlived ACME profile；同机另有 sub2api 站点经 import 共存，未触碰。
- **执行**：pg_dump 备份（backups/androidapm-20261121T141219Z.dump）→ 旧镜像 tag `androidapm-server:rollback-20261121`（00ed954b038d）→ clone aac2199 至 releases/aac2199-src + 复用 compose.seoul.yaml → config 校验 → build 新镜像（5f15582f01f4）→ `alembic upgrade head`（0009→0010，`ix_inbox_raw_bytes` 已确认落库）→ `up -d --no-deps gateway worker alerts` → 按原参数重建 public-trial gateway（同 env/网络/卷/384m/read-only）→ Caddyfile IP 站点块替换为仓库加固版（保留 shortlived TLS 与 8081 upstream，备份 Caddyfile.bak-*）→ systemctl restart androidapm-tls-trials → current 软链指向新 release。
- **验证（公网直连）**：`/`、live、ready 200；`/v1/alerts` 401；`/docs` `/openapi.json` `/metrics` 404；**HSTS 出现、`Server: uvicorn` 消失**、Last-Modified 变为 2026-11-21（新前端资产 index-D3x2QzK0.js 200/319KB）；SPA 深链 200；证书未受影响（11-26 到期）；sub2api 站点与 quick-trial 服务均正常；worker/alerts 启动日志干净，inbox 5 pending 不变（export=false 为 staging 既有配置，非本次引入）。
- **运行时环境证据**：容器 env `APM_ENVIRONMENT=staging`（首次服务器侧核实，此前记为"未核实"）；数据 scope 与运行时标签一致为 staging。
- **回滚预案**：`docker tag androidapm-server:rollback-20261121 androidapm-server:seoul-staging` 后重建容器；Caddyfile.bak-* 可还原；0010 可独立 downgrade。
- **剩余服务器侧待办**：31 个主机安全事件；备份已有本地 7 天 pg_dump（backup-seoul.sh），异地/PITR 与恢复演练仍未做；OTLP 接收端未配置（export=false，无 dead letter 风险但导出链路未验收）；域名未绑定；trials/Caddyfile 与仓库资产的纳管同步。

## 2026-11-15 遗留项深度核实轮（逐项确认 + 修复确证问题）

对清扫轮与 11-11 轮记录的"有意延后"清单逐项深度核实，**确证为问题的修复，非问题/需外部决策的保留并说明证据**。

**确证并修复**：

| 项 | 核实结论 | 修复 |
|---|---|---|
| `registry_snapshot()` | 服务端仓与 AndroidAPM 客户端仓**均零引用**（客户端根本不 import 服务端模块）→ 确证死代码 | 删除函数及仅其使用的 `asdict` 导入 |
| check_production_config IPv6 | `"." not in domain` 拒绝合法 IPv6 字面量，与"接受 IP 字面量"注释矛盾 → 确证不一致 | 改用 `ipaddress.ip_address` 解析（IPv4/IPv6，含括号形式），主机名仍需点号；补 IPv6 接受/裸标签与非法 IP 拒绝回归 |
| inbox 8 列 server-default 漂移 | 迁移 0003/0005 设 DB 默认值而模型仅 Python 默认值，`create_all` 与迁移库 DDL 不一致 → 确证（无运行时影响，ORM 总供值） | 模型镜像迁移现状的 8 个 `server_default`（零 DB 变更，`alembic check` 仍干净）；normalization_version 注明 '1' 为迁移遗留 floor、ORM 供当前版本 |
| 告警实例/送达无保留 | `maintain_once` 从不清理 `alert_instances`/`alert_deliveries`，长期运行无界增长 → 确证 operational 问题 | 新增 `APM_ALERT_HISTORY_RETENTION_DAYS=90`（1–3650），保留循环按 batch 清理**已解决**实例及其 outbox（先删送达再删实例，不依赖 FK 级联），按 tenant 审计 `alert.history.prune` + 计数指标；未解决/触发中实例永不清理，`active_instance_id` 安全；补 2 回归 |
| 容量准入 raw 字节 SUM | `check_inbox_capacity` 在全局 advisory lock 下对未清理行做无覆盖索引 SUM，随 inbox 线性扫描 → 确证效率缺陷 | append-only 迁移 `20261115_0010` 增部分覆盖索引 `ix_inbox_raw_bytes (payload_size_bytes) WHERE raw_pruned_at IS NULL`（PG index-only scan），模型同步声明；临时 SQLite 实证 upgrade→check→downgrade -1→upgrade→downgrade base→upgrade→check 全干净；补索引守卫回归 |

**核实后保留（非当前问题或需外部决策，证据见云端待建设清单）**：
- **dead-letter 重放工具**：raw 在保留期内可恢复（手动 DB），不变量已满足；缺的是 admin 审计 requeue 能力，属功能增强而非缺陷。
- **tombstone COLLECTION_TIME 标记**：核实 tombstone 靠精确事件族隔离（`HISTORICAL_EVENTS`、独立 `native_tombstone_events` metric、不进 live session 分子），缺失标记仅元数据层面、**非计数错误**；清扫轮已更正文档。实现独立标记属行为变更，需单独决策。
- **SigNoz 同步分页**：仅超大实例有重复创建风险，staging 规模不构成问题；无 live 实例不宜盲改外部 API 分页。
- **FLOAT 规范文本 / Native 指纹 signal 编码**：分别受重放兼容不变量与 ADR 0009 指纹版本化约束，不能静默改。
- **控制台默认版本 API / 符号化 resolution 退避 / Actions SHA 钉扎**：功能增强或推测性加固，非当前缺陷。

**本轮验证（本机）**：Ruff、mypy 63 files、`pytest --basetemp <短目录>` **591 passed / 1 skipped**、15 份文档（head 更新为 `20261115_0010`，AGENTS/README/docs11 现状同步，历史条目与 ADR 不改）、凭据扫描、`git diff --check` 全过；迁移往返与 autogenerate 实证干净。前端本轮无改动。

**发布状态**：新增迁移 0010 与告警保留需随下次发布生效；发布前先 `alembic upgrade head`（0010 仅性能索引，可独立 downgrade）。线上仍是旧镜像。

## 2026-11-15 全项目清扫轮（工具链/迁移/文档/前端残留）

在 11-11 五区域深度 review 之上，本轮覆盖此前未深入的面：scripts 工具链、SigNoz 资产、proto 链、Alembic 与模型漂移、全量文档一致性、前端配置/CSS/依赖残留。两个子代理 + 主线自查，每条发现先核实再修。

**已修复**：

| 区域 | 问题 | 修复 |
|---|---|---|
| 依赖 | `hypothesis` 声明但全仓零使用 | 从 dev 组移除，`uv.lock` 同步（-44 行） |
| 前端 CSS | 11-11 删除 5 个遗留组件后 CSS 未清理：styles.css 58 个、console.css 4 个孤儿类 | 精确 token 审计（含动态前缀/NavLink 自动类/三元类甄别）后剪枝，styles.css 987→557 行；CSS bundle 43.20→31.55 kB（gzip 8.72→6.85）；活规则逐字节保留 |
| 前端 | 冒烟发现长原因码 `SESSION_IDENTITY_COVERAGE_INCOMPLETE` 溢出指标卡且无中文翻译（服务端 query.py 确实发出） | 补 REASON_LABELS；`.console-metric p` 加 `overflow-wrap: anywhere`；全量比对服务端 21 个 reason 码仅此一处遗漏 |
| 本地预览 | fixture 系统性缺陷：6 小时桶锚定使预览**几乎总是 LATE**；手工 `v4b-` 前缀使目录漂移后 reseed 409；数值 sweep 生成**无证据占位事故**污染计数/指纹覆盖/会话率；V2 兼容行共享 2.0.0 永久阻塞新版本会话率；sdk_health dropReason 合计 440 与 dropCount=0 矛盾 | 10 分钟新鲜桶（<900s 迟到阈值）；内容哈希身份（目录/spec 变更自动换身份）；sweep 跳过事故契约（ANR durationMs 移入显式证据 spec）；V2 行独立 `1.9.0-legacy`；dropCount=0 时 dropReason/dropPriority 归零；同事务**替换**旧合成代（仅本地 SQLite 的 `preview-` 行，真实批次 append-only 契约不变） |
| 本地预览 | 默认 min_installations=100 vs 3 个合成安装 → 会话率永不显示 | start-local.ps1 生成 `APM_QUERY_MIN_INSTALLATIONS=3`（仅预览，注释禁止生产使用），旧 env 幂等补写；顺带修 worker 清理 HasExited 竞态 |
| OTLP | scalar-coercion 死层（~80 行，normalization v4 后唯一调用点从不启用）+ `UNIT_MAP["percent"]` 死映射 | 删除；`_merge_prefixed` 简化为直接导出已审核类型 |
| SigNoz | Startup P90 过滤器 `module='launch'` 含 first_frame_rendered/launch_bottleneck，与"cold/warm/hot"描述不符；README compose 描述停留在三服务 | 过滤器限定三个 start 事件；README 更新为六服务 |
| 文档 | 13 处不一致（2×P2）：docs/04 声称导出 OTel `exception.*` 与不存在的 `android.apm.error.fingerprint`；docs/04+01 声称 tombstone `COLLECTION_TIME` 标记（从未实现）；docs/03 `IDENTITY_MISSING`（实为 ABSENT）；`android.apm.thread`（实为 thread.name）；"13 性能域"过期×3；docs/00/01/09 "OTLP Metrics 未实现"已被 opt-in 实现取代；docs/08 Prometheus 名/标签错；backlog "4 MiB 绝对预算"与服务器 8 MiB 矛盾；docs/10 trace 深链路由错；HANDOFF 无日期历史列表 | 全部按代码事实更正；COLLECTION_TIME 选择**更正文档**而非新增标记（行为变更需独立决策，当前 tombstone 靠精确事件族隔离已安全）；历史列表标注"2026-09-12 顺序整改之前" |
| 前端配置 | jsdom 无显式 origin，node 版本变化时 localStorage opaque-origin 抛错（子代理 node 26 复现 16/31 失败） | vite.config.ts 固定 `environmentOptions.jsdom.url` |

**新增测试（+7）**：5 个 seed 场景回归（新鲜非 LATE、基线会话率 2/3 PRESENT、新版本 SDK-drop 门禁、事故 100% 指纹、V2 分离、drop 一致性、替换不累积）；proto 生成漂移守卫（grpcio-tools 重生成与已提交模块逐字节比对）；SigNoz 资产 field/base 属性与导出 allow-list 交叉校验。

**子代理实证为干净（未改动）**：九版迁移 upgrade/downgrade base/upgrade 往返 + `alembic check` 无漂移 + 分级 backfill 21 断言（hash/HMAC/时钟不变）；proto 链重生成逐字节一致、跨仓校验器对真实 AndroidAPM 仓通过（仅注释与已审核 optional 7–9 差异）；SigNoz 版本钉一致、JSON 有效、属性均在 allow-list；README/AGENTS 全部本地链接与 CLI 子命令存在；docs/02/03/05/06/07/08/10/11/13 事实性声明逐条核对通过；ADR 0001–0011 与实现一致、无静默取代。

**本轮验证（本机，2026-11-15）**：Ruff、mypy 63 files、`pytest --basetemp <短目录>` **586 passed / 1 skipped**、15 份文档与 head/版本一致性、凭据扫描、`git diff --check` 全过；前端 ESLint、TypeScript、**8 suites / 31 tests**、build（CSS 31.55 kB / gzip 6.85 kB，JS 319.53 kB / gzip 100.38 kB）全过。**真实浏览器冒烟**（本地预览服务 + 修复后 fixture）：总览基线 PRESENT、会话率按门禁渲染、SDK-drop 门禁正确；Issues **100% 指纹覆盖率**（修复前 47% DEGRADED）；性能页 243 契约行全渲染；1440px 与 390px 无页面横向溢出。前端门禁仍用 ZCode Electron 运行时（node v24.21.0，本机无 pnpm）；vite jsdom url 加固后跨 node 版本稳定。

**有意延后**：inbox 8 列 server-default 漂移、`registry_snapshot()` 死代码、check_production_config IPv6、告警历史保留、容量 SUM 索引——这些已在同日"遗留项深度核实轮"确证并修复（见上）。SigNoz 同步分页（超大实例才相关）等其余项保留，记录于云端待建设清单。

**发布状态**：线上仍是旧镜像，本轮与 11-11 修复均待下次发布生效；服务器侧待办不变（docs/13 第 4、5 节）。

## 2026-11-11 深度 review 轮（五区域审查 + 逐条核实修复）

本轮对 ingest/协议/身份、query/web/告警、worker/符号化/OTLP、Web 前端、配置/部署/CI/文档五个区域做深度审查（并行子代理 + 主线复核），**每条发现先亲自核实代码路径再修**，延续 2026-11-07 的"只修证实问题"纪律。核心 durability/dedup/租户隔离/HMAC/ACK/fencing 不变量经审查与既有测试确认成立，未发现 P0/P1。

**已修复（全部有回归测试）**：

| # | 问题 | 修复 |
|---|---|---|
| ① P2 | 极端 int64 事件时间戳（如 `2**63-1`）使 `datetime.fromtimestamp` 抛 OSError/OverflowError，穿透四个 decoder 的 `except (ValueError, ValidationError)` 变成**可重试 500 毒丸批次**（SDK 永不删除、无限重放） | `domain.validate_timestamp_skew` 捕获并转为非重试 422；line/V2/V3/protobuf 各加回归 |
| ② P2 | line 协议 `str.splitlines()` 在 CR/VT/FF/NEL/Unicode 分隔符处过度切分，异常消息含合法 CR 的整批被**非重试 422 永久拒绝**（崩溃证据静默丢失） | 只按 `\n` 切分，容忍 CRLF 与单个结尾换行；值内分隔符保留；docs/02 明确分隔符语义 |
| ③ P2 | 导出 worker 缺 `otlp_timeout + 5 < worker_lease` 校验（symbolizer 有同类约束）：合法配置组合下慢 OTLP 发送活过租约 → 第二 worker 重领并**重复发送**同一 pending 信号 | config 启动强校验 + `EXPORT_FINALIZE_MARGIN_SECONDS` 常量；docs/08 同步 |
| ④ P3 | 末次尝试发送后崩溃的重领会**超预算再发送一次**（claim 即递增 attempt_count，无导出前守卫；symbolizer 有守卫） | `export_once` 对 `attempt_count > max` 且仍有 pending 信号的事件直接 dead-letter（`export_attempts_exhausted`），不再发送；注意守卫用 `>` 而非 `>=`（领取计数语义与 symbolizer 不同） |
| ⑤ P3 | 非 ASCII CSRF cookie/header 使 `hmac.compare_digest(str,str)` 抛 TypeError → 所有 cookie 写端点 **500 而非 403**（已复现；fail-closed 非绕过） | 显式拒绝非 ASCII（签发 token 恒为 ASCII）；补 3 组字节级回归 + 跨会话 CSRF 绑定回归 |
| ⑥ P3 | 隐私清洗器对**结构键**做子串匹配：短 installation ID（如 `occ`）会删除整个 `occurrence` 对象（native 身份证据静默销毁）；服务端 request_id 参与身份冲突检查导致随机 422 | 值仍全量清洗；键只在 sender-controlled map（fields/field_types/context/extras/unknown）内清洗；request_id 移出冲突检查。UUID 形态真实输入行为不变（既有隐私测试全绿） |
| ⑦ P3 | 告警计数规则加载全窗口行，高流量 scope 超 `query_max_rows` 时**整轮静默失败**（只有 warning 日志，规则永不触发/恢复） | 计数指标改 SQL 聚合 `count_window_events`（与行投影严格同谓词，含 release+OCCURRENCE_BOUND 过滤）；p95 规则保持 fail-closed 但日志可区分（`alert_evaluation_budget_blocked`）；docs/11 同步 |
| ⑧ P3 | 制品 promote 后注册/提交失败留下**孤儿不可变文件**（仅 conflict 分支清理） | post-promote 全路径 try/except 清理（幂等 discard，不掩盖原异常） |
| ⑨ P3 | Trace 列表/详情读取**无审计**（同为 L1 自由 name，事件读取有审计）；CLI `remote_config.publish` 审计缺 app/env、`ci_key.create` 缺 environment → 均不出现在 scope 审计 UI | Trace 补 `query.trace.list`/`query.trace.read`（含 not_found）；CLI publish 补 scope 字段；CI key 审计显式 `environment: null`，审计过滤器纳入 app 级无 environment 行（同 app 各环境管理员可见） |
| ⑩ P3 | 前端契约漂移与 UX：IssuesPage 筛选/描述缺 `NATIVE_CRASH`/`NATIVE_TOMBSTONE`（服务端指纹早已包含）；配置 JSON 解析错误被吞成通用"操作失败"；登录网络错误文案写死"本地"；告警/设置页裸 ISO 时间；ConsoleLayout 死分支、format.ts 冗余三元 | 全部修复；删除路由化重构后无引用的 5 个遗留组件（EventInspector/FingerprintList/DataQualityStrip/MetricCard+test），"真零 vs 不可用"不变量测试移植到实际使用的 MetricTile（Primitives.test.tsx） |
| ⑪ P3 | CLI `--role` 错误文案漏 admin（choices 与校验本身正确） | 文案修正 |

**核实后有意不修（记录于云端待建设清单 2026-11-11 节）**：FLOAT/DOUBLE 非规范文本收紧（破坏历史批次重放兼容不变量，文档只承诺整数规范文本）；Native 指纹 signal 的 typed/legacy 编码差异（改指纹需 ADR 0009 版本化迁移）；dead-letter 重放工具、告警实例保留策略、容量 SUM 覆盖索引、Actions SHA 固定、控制台默认版本 API、符号化 resolution-only 重试退避。

**测试缺口补齐（新增 38 项后端 + 3 项前端）**：四协议极端时间戳、line 分隔符/CRLF/ts 不回显、短 ID 结构证据保留、request_id 非身份、会话 token 7 类拒绝路径 + 签名/base64 篡改、非 ASCII CSRF、跨会话 CSRF 绑定、key 到期使 cookie 会话失效、跨租户游标重放 400、导出预算崩溃窗口、otlp timeout/lease 校验、告警高流量计数/p95 fail-closed/release+batch-declared 过滤/规则上限、CLI 审计可见性、Trace 审计、制品孤儿清理、`/v1/config` expired/rollout/inactive/scope 边界、quota 回滚不烧预算、**PostgreSQL DB 时钟 fencing 集成测试**（host clock 偏斜下过期租约拒绝 stale owner；本机 skip，CI 有 `APM_TEST_POSTGRES_URL` 时执行——此前该生产机制零覆盖）。

**本轮验证（本机，2026-11-11）**：`uv sync --all-groups --frozen`、Ruff、mypy（63 source files）、`pytest --basetemp <短目录>` **579 passed / 1 skipped**（skip 为未配置 `APM_TEST_NDK_BIN`；默认长 Temp 下仍有 4 个既有 artifact 长路径失败，属历史记载的 Windows MAX_PATH 问题）、15 份必需文档与 Alembic head/版本号一致性、凭据扫描、`git diff --check` 全部通过。前端 ESLint、TypeScript、**8 suites / 31 Vitest tests**、生产 build（JS 319.46 kB / gzip 100.37 kB）通过。本机 PATH 无 node/pnpm，前端门禁用 ZCode 自带 Electron 运行时（node v24.21.0）等价执行四个 scripts 的底层命令；CI 仍用 pinned node 24.15.0 + pnpm 11.19.0。本机无 Docker CLI，镜像 build 与 production overlay `compose config` 由 CI 验证；6+1 项 PostgreSQL 集成测试本机 skip。配置/部署自查：Dockerfile（pinned 基础镜像、非 root、read-only、healthcheck）、compose 双层（cap_drop/no-new-privileges/内部 metrics）、Caddyfile（HSTS/安全头/私有路径 404/liveness 探针）、CI（PG round-trip、覆盖率门禁、secret scan、overlay config）、.gitignore（.env*/.local 覆盖）均与文档一致，未发现新漂移。

**发布状态**：本轮为代码/测试/文档修复，**线上仍是旧镜像，尚未重新部署**；③④⑦ 等修复需随下次发布生效。服务器侧待办（配置回收、证书告警变量、备份演练等）不变，见 `docs/13` 第 4、5 节。

## 2026-11-07 复核轮（review 结论再验证与修复）

对上一轮 review 的 6 条意见逐条复核，只修确认为真的问题，撤回不能证实的结论：

| 意见 | 复核判定 | 处理 |
|---|---|---|
| ① Caddy `health_uri /health/ready` 导致「DB 抖动 → 全站 502」 | **撤回强结论**：核对 Caddy 官方 `reverse_proxy` 文档全文，未找到「所有 upstream 不健康时忽略健康状态」的表述，502 因果链无法证实 | 仍做风险收敛：单 upstream 的主动检查是后台定时器、不门禁单个请求，而 `health_fails` 默认 1 会把一次瞬时 DB 抖动放大。改回 `/health/live` 并设 `health_fails 3`；含 `inbox_events` 的 `/health/ready` 留作部署发布前轮询 |
| ② `ConsoleLayout` 把缺省 `environment` 显示成「已连接 远端 服务」 | 成立 | 增加「部署环境未知」第三态，不再把未知当真实部署，也不丢合成预览警告分支 |
| ③ `check_tls_expiry.py` 默认 7 天 vs 约 6 天短证书 | 成立 | 默认改 3.0，与 workflow / 文档一致，新增回归 |
| ④ `check_secrets.py` 文案称只扫 tracked，实现还扫 untracked | 成立 | 模块 / 函数 docstring 与成功提示统一为 candidate files 口径 |
| ⑤ 用 `/docs` 404 反推 `APM_ENVIRONMENT=production` | 成立 | 新 Caddyfile 无条件 404 `/docs`，观测不唯一；`docs/13` 与本文改为「运行时环境未核实」，指向 `/v1/web/session.environment` 或服务器侧只读清单 |
| ⑥ `check_production_config.py` 要求 `APM_DOMAIN` 与「域名未设置」不兼容 | **撤回**：实测 IP 字面量通过校验（只要求非占位且含点），且该脚本是部署前置检查、不是线上状态断言 | 补注释说明 IP-only 入口合法，新增文档保留地址回归 |

另复核：默认 pytest 临时目录下的 4 个 `test_artifact_api.py` 失败是本文早已记载的**既有 Windows MAX_PATH 问题**（制品 storage_key 的 16+12+64+64 段把目标路径撑到 262 字符），不是 `a0d75e1` 引入，也不改制品持久化实现；按历史条目记载的短 `--basetemp` 重跑即全绿。本轮把该前置条件写进验证记录，避免「全绿」被误读为默认环境也通过。

**本轮验证（本机）**：Ruff、mypy 63 files、`pytest --basetemp <短目录>` **541 passed / 1 skipped**（skip 为未配置 `APM_TEST_NDK_BIN`；默认长 Temp 下仍有 4 个既有 artifact 长路径失败）、15 份文档与 Alembic head / 四处版本号一致性、凭据扫描、`git diff --check` 全过；前端 ESLint、TypeScript、**8 suites / 28 Vitest tests**、生产 build（JS 319.12 kB / gzip 100.28 kB）通过。本机仍无 Docker CLI，production overlay `compose config` 与镜像 build 由 CI 验证。

**发布状态**：本轮完成代码与文档修复、提交推送；线上仍是旧镜像（仍有 `Server: uvicorn`、无 HSTS），**尚未重新部署**。本机无 SSH 私钥、云 API 凭据、tccli 或 Docker CLI，无法直接登录云端；发布需在云控制台 OrcaTerm / 自动化助手执行，见 `docs/13` 第 4、5 节。

## 2026-11-07 云端事实纠偏与缺口收尾

本轮先复核运行现实，再按证据逐项修复可在本机闭环的缺口。

**运行事实**：服务已在首尔轻量应用服务器以 HTTPS 对外运行；2026-11-07 直连复核 `/health/live`、`/health/ready` 均 200，`GET /` 200 且带完整页面安全头，`/docs`、`/openapi.json`、`/metrics` 404，未鉴权 `/v1/alerts` 401，仅 22/443 可达；Let's Encrypt 短期证书 `notAfter 2026-11-12 22:41:30 UTC`。运行时 `APM_ENVIRONMENT` 与 Query Key 的数据 `environment` scope 是两个概念，历史记录中的 `staging` 指凭据 scope；`/docs` 404 **不能反推运行时为 `production`**（仓库 Caddyfile 会无条件对 `/docs` 返回 404，`main.py` 也会在非 production 关闭 docs，观测结果相同），运行时环境仍属**未核实**，应通过 `/v1/web/session.environment` 或服务器侧只读清单确认。公网可达不代表 SigNoz、通知、备份、SSO、真机或容量验收通过。详见 `13-云端运行环境.md`。

**此前文档缺陷**：`AGENTS.md`、`README.md`、`docs/08`、`云端待建设清单.md` 及本文最新条目仍停留在"未部署 / 本机无 Docker"的 2026-11-02 视角；`README` 内 v3/70 与 v4/243 口径冲突，`AGENTS.md` / `云端待建设清单.md` 内 "durable V5 已完成" 与 "SDK 仍为 V4" 并存。

**本轮变更**：

- 新增 `docs/13-云端运行环境.md`（脱敏），记录公网事实、部署形态差异、运行时开关、P0 服务器侧待办、只读配置回收清单与历史误判更正；同步修正 `AGENTS.md` 阅读顺序与基线、`README.md`、`docs/08-部署与运维.md`、`云端待建设清单.md` 的过期口径。
- 反代与响应头：`deploy/caddy/Caddyfile` 为所有响应补 HSTS / nosniff / X-Frame-Options / Referrer-Policy，统一屏蔽 `/docs`、`/openapi.json`、`/redoc`；`Dockerfile` 与 `main.py` 禁用 uvicorn `Server` 头；`reverse_proxy` 主动健康检查保持进程级 `/health/live` 并要求连续 3 次失败（2026-11-07 复核轮更正，原「改用 `/health/ready`」已撤回）。
- `/health/ready` 从仅 `SELECT 1` 增强为同时确认 inbox schema 已迁移，避免"进程活着但表不存在"被判为就绪。
- Web 会话响应新增运行时 `environment` 字段，控制台页脚按环境显示证据提示；`local` 仍明确标注合成预览，不再在真实部署上无条件显示"synthetic preview 不能代表生产部署"。
- 新增 `scripts/check_production_config.py`（部署前置配置强校验，不改动运行时启动行为）、`scripts/check_tls_expiry.py`（证书到期巡检）、`scripts/check_secrets.py`（被跟踪文件明文凭据扫描）；`scripts/verify_docs.py` 增加文档清单、Alembic head 与版本号一致性校验；`.github/workflows/tls-monitor.yml` 在配置仓库变量 `APM_PUBLIC_HOST` 后每日巡检短期证书。
- CI 增加 production overlay `compose config`、secret scan 与可选 NDK 回归变量；`tests/test_deploy_assets.py` 及新增回归覆盖上述门禁。`docs/08` 补备份/PITR 与季度恢复演练清单，明确资源限制不写死是等待回收云端 override。

**文档同步（同日后续一轮）**：`AGENTS.md` 收尾检查加入 secret scan 与部署前置校验，并新增「会话 `environment` 仅作 UI 证据标签、不作授权输入」不变式；`docs/03`、`docs/07`、`docs/08`、`docs/09`、`docs/10`、`README.md`、`ADR 0007`、`docs/13` 同步运行时 environment 字段、ready 探针语义、Caddy/容器响应头加固、门禁脚本与"线上尚未生效"边界；`docs/10` 的"服务器到手后现场顺序"改为按云端现状校正，并加入部署前置校验命令。另修正 `check_production_config.py`：`APM_DATABASE_URL` 由 compose 注入，改为仅校验显式 override，同时补 `.env.production.example` 注释与两项回归。`README.md` 另按标准工程结构重构：状态速览 / 能力概览 / 架构 / 快速开始 / 配置与凭据 / 符号化制品 / 验证门禁 / 部署与未完成验收 / 文档索引 / 关键不变量 / 许可；原先按时间堆叠的 30 余段实现细节改为按主题指向具体文档与 ADR，统一中文表述，命令改为可直接复制的完整形式，48 个本地链接全部校验通过。文档同步轮门禁：Ruff、mypy 63 files、**pytest 539 passed / 1 skipped**、15 份文档、凭据扫描、示例 env 负例被正确拒绝、`git diff --check`、前端 lint/typecheck/8 suites 27 tests/build 全部通过；上面的 537 passed 属于同日前一轮。

**本轮验证（本机，2026-11-07）**：Ruff、mypy（63 source files）、`pytest` **537 passed / 1 skipped**（skip 为未配置 `APM_TEST_NDK_BIN`）、15 份必需文档与 Alembic head / 四处版本号一致性、被跟踪文件明文凭据扫描、`git diff --check` 全部通过；前端 ESLint、TypeScript、**8 suites / 27 Vitest tests**、生产 build（JS 319.02 kB / gzip 100.26 kB）通过。新增回归覆盖会话 `environment` 字段、ready 的 schema 探针、反代/容器资产、部署配置与证书/凭据脚本，以及控制台页脚 local / 远端两种分支。本机没有 Docker CLI，因此 `docker compose -f compose.yaml -f compose.production.yaml config` 与镜像 build 由 CI 验证、本轮未执行；`integration_tests/` 仍需 `APM_TEST_POSTGRES_URL`，本地默认 testpaths 不含它。浏览器实机冒烟沿用 2026-11-02 的历史证据，本轮未重跑。

**仍需服务器侧完成（本机无法闭环）**：回收实际 compose / Caddyfile / 环境变量并脱敏纳管；处理 31 个待处理主机安全事件；为约 6 天有效期的证书设置 `APM_PUBLIC_HOST` 仓库变量以启用到期告警，并考虑绑域名；清理空跑的 `androidapm-server-gateway-1`；确认外部 OTLP 接收端与 dead letter、磁盘水位；执行备份/PITR 恢复演练；把回收后的反代配置与本轮 Caddyfile 加固同步到线上。这些都写在 `docs/13` 第 4、5 节，不能标记完成。

## 2026-11-02 本地差异修复与交付验证

本轮从 GitHub/main 与本地 HEAD 同为 `24f5b8b4a6a2de12e29887421362e53558783133` 的未提交产品工作树继续，合并保存已有性能、告警、管理、九版迁移和三信号实现，并修复以下实际问题：

- 性能、告警、设置、Trace 页面接入真实 API，替换占位；支持观测/覆盖/趋势/类别、规则创建编辑/评估/启停/静默/确认、一次显示凭据/撤销、签名配置发布/递增回滚、运行/制品/审计与手动 Span 真实时间线。所有新增写入沿用绑定 CSRF，viewer 无管理/Trace 请求，admin 继承 L1/L2 和发布决策入口。旧/缺失字段保持状态和 null；刷新失败及迟到响应不复用旧证据。
- OTLP SDK-health 使用唯一性能目录，每事件每字段一个 Gauge 点；stage count 缺失/无效/非正时不导出占位延迟零，正样本真实零仍导出。
- 告警最后一次尝试后崩溃，过期重领直接 FAILED/RETRY_EXHAUSTED，不越过重试预算；活跃最后 owner 仍可完成。领取和最终写回复用数据库实时时钟 fencing。
- 文档扫描在遍历前剪除 .git/.local/依赖树；跨仓协议校验保留 message/type/name/number，只容许已审核的 ApmOccurrenceContext optional string 7–9，拒绝其他漂移。Docker Web build 复制 public 静态资产。
- 修正此前过度完成描述：当前 AndroidAPM SDK 仍为 durable V4，未发送标准 session/device/OS/session_start/span v2；这些是服务端可选输入及客户端待接入契约。本轮没有改动或提交 Android 客户端仓库。历史 2026-10-14 的 SDK/浏览器证据按当时条目保留，不能代表当前源码。

提交前验证：

- `uv sync --all-groups --frozen`、Ruff、mypy（63 source files）、14 份必需文档和 diff check 通过。
- 完整后端 **527 passed / 1 skipped**，分支覆盖率 **82.06%**（CI 门槛 70%）；skip 为未配置 `APM_TEST_NDK_BIN`。初次较长 Temp 路径触发既有 Windows 制品 os.replace 长路径问题；使用 `--basetemp C:/Users/24491/AppData/Local/Temp/af02` 重跑完整套件通过，未改变制品持久化实现。Docker 静态资产测试 3 项通过。
- 前端 lint/typecheck、**8 suites / 25 tests**、生产 build 通过；新增回归覆盖实际产品路由、admin/viewer、管理 CSRF、null/真零、一次显示 secret 与页面切换、配置发布/回滚和 Trace 深链。前端损坏依赖按 frozen lockfile 修复；lockfile 未改变。
- 独立 `.local` 测试库从空库执行九版 migration，另执行 SQLite head/check/downgrade base/upgrade head，通过且模型无差异。
- 实际 Edge headless + 生产 build + FastAPI 冒烟通过：性能/告警/设置/Trace/事件探索/质量深链，创建规则/手动评估/静默/停用，创建并撤销一次显示凭据，signed config 发布/递增回滚，Trace 层级和真实时间条；1440px 桌面及 390px 窄屏无页面横向溢出、无 JavaScript 异常。只使用隔离 synthetic 数据和本地无外部渠道配置。证据为忽略目录 `.local/review-*.png` 与 `.local/review-browser.cjs`。
- 6 项 PostgreSQL integration 因缺 `APM_TEST_POSTGRES_URL` 全部 skip；本机没有 Docker/Podman/psql，未执行容器运行、生产 PostgreSQL/SigNoz/TLS、真实通知、SDK 新契约或物理设备验收。浏览器 fixture 不替代这些门禁。

## 2026-10-14 本地统计补齐

- 服务端本地、GitHub 唯一分支及默认分支再次核对为 `main`；远端 foundation tip 保持 `24f5b8b4a6a2de12e29887421362e53558783133`。本轮服务端和客户端功能改动均保留在各自工作目录，尚未提交或推送。客户端分支为 `develop`。
- 性能统计扩大为 **243 个数值字段 / 66 个精确事件 / 29 个固定类别**，覆盖客户端已经上报的 IO、Render、WebView、Battery 等全部目录；新增 mean/min/max、加权 FPS、真实类别分布和事件计数。整数计数、固定类别、duration availability/sample count、累计快照与窗口摘要分别处理；缺失、无效、不可用、过期、未知原值不混为零。SDK 未采集的 GPU overdraw/Native 峰值等不能在服务端补造，细节见统计契约。
- Java Crash、Native Crash、ANR、Tombstone 分开计数、趋势、告警和指纹。Native 只使用可信 typed frames 或实际解析的 PC/module 帧；JNI 的“see tombstone”占位堆栈不产生合并指纹，ASLR load_bias 不参与分组。历史回填保留 payload/hash、身份、帧、Java/symbolized 指纹及 aliases；过期原文和损坏帧安全跳过。
- 客户端 V3 occurrence 增加 optional 匿名 runtime session/device/OS；durable codec V5 读取 V1–V4，旧缓存不补当前身份。每次成功 SDK init 同步 durable 写入精确 `core/session_start`；重复 init 保持身份，stop/reinit 更换。Trace schema v2 使用真实 start/end epoch 和单调 duration。
- 服务端九版 migration 至 `20261014_0009`，session 使用独立 HMAC domain，已知安装/session 明文在任何落库和派生证据前移除。旧 V3 optional absent 不改变 payload hash；重放使用原 key version。会话率仅在窗口内有效 start、完整身份/SDK-health/采样/迟到/安装门槛满足时显示；历史 app_exit/tombstone 不进入现场事故分子。标准设备/OS 分布及覆盖进入 Issue、事件详情和数据质量页。
- 新增手动 Trace 列表/Span 详情及真实时间线；旧 span 缺起止、父节点缺失/重复/循环、时钟异常及 raw 过期保留具体状态。所有 Trace 查询保持 scope/RBAC、时间/行数预算，自由 name 和 attr_* 不进默认 OTLP 索引。
- OTLP Metrics/Traces 显式 opt-in，默认关闭。数值观测为 Gauge；不把累计快照伪造成 Counter/Histogram。Logs/Metrics/Traces 首次 claim 固定导出计划，成功逐项持久化、重试只做剩余项，并保留数据库时钟 lease/owner fencing、有界预算和 partial_success 拒绝处理。已 delivered 事件不自动重发。

验证证据：

- 已执行冻结依赖同步、Ruff、mypy（63 source files）、14 份必需文档、diff check；前端 lint/typecheck、7 suites / 18 tests 和生产 build 均通过。
- 服务端完整回归 **519 passed**，包含 NDK 27 / LLVM 18 的真实双 ELF 验证。Windows 默认 pytest Temp 深路径触发既有制品 `os.replace` 路径长度限制；使用短目录 `--basetemp C:/Users/xiazh/AppData/Local/Temp/as53e9` 重跑全部通过，未为此改变制品实现。结果记录在 `.local/statistics-backend-validation.log`。
- 客户端针对实际改动执行 55 suites / **452 tests**，无失败/错误/跳过；model/core/trace API、24 个 baseline、Sample Debug/Release 构建、core/trace lint、47 Markdown / 86 links 通过。未重复完整 SDK CI，未进行真机安装/采集。
- 当前客户端真实 `HttpApmUploader` V2/V3 → 服务端 loopback 联调通过：空 SQLite 升级九版、Gzip、提交后 exact ACK、typed/occurrence persistence、installation/session HMAC、device/OS、旧数据 unknown coverage、明文排除和重复重放。客户端日志 `.local/collector-statistics-e2e.log`。
- 告警/设置实际 Chrome/Playwright 冒烟再次通过：独立 evaluator 自动触发、创建/确认/静默/停用、凭据创建/撤销及 viewer 会话失效、signed config 发布/回滚、深链刷新、390px 无页面横向溢出、404 和无 JS/console 错误。QA 规则/凭据清理完成，历史审计保留。截图与结果在 `.local/browser-evidence/`。
- 本机无 Docker/PostgreSQL，6 项 PostgreSQL integration 明确 skip。用户明确先完成本地；生产 PostgreSQL/SigNoz/TLS/SSO/KMS、真实通知渠道和物理设备验收仍需对应环境。

统计页面的最终浏览器结果及过期分类修复门禁在本节完成收尾时补录。操作见 [本地产品功能](11-本地产品功能.md)，完整统计目录见 [统计契约与口径](12-统计契约与口径.md)，决策见 [ADR 0011](adr/0011-运行会话统计与多信号导出.md)。

## 2026-10-13 本地产品功能（历史）

- 分支整理：保留完整 foundation tip `24f5b8b4a6a2de12e29887421362e53558783133`，本地重命名为 `main`，GitHub 默认分支改为 `main`，删除两个旧远端 codex 分支；`git branch -a` 与 `git ls-remote --symref origin HEAD refs/heads/*` 复核只有 main。后续产品代码为当前本地未提交改动。
- 性能：normalization v3 和 70 个精确数值契约覆盖 13 个域，支持观测分布/版本差值/24 桶趋势、有效覆盖、GC availability、真实零/无效/缺失区分、迟到与发生版本质量。窗口/查询行数受限，超限拒绝而不返回部分统计；历史 raw 投影回填保持身份/hash/时钟不变，过期证据跳过。
- 告警：八版 migration 持久化规则、firing episode 与通知 outbox，独立 evaluator、阈值/窗口/最少样本/版本限定、确认/静默/启停、可靠恢复、唯一 transition、有界重试、数据库时钟租约与 owner fencing。通知目的地只由服务端配置，Web 只见别名；本地没有真实渠道，未发送外部通知。
- 设置：独立 admin、应用名称、有效运行/保留参数、scope 管线/制品元数据、Query/Ingest/CI 生命周期、一次显示 secret、signed config 发布/灰度/新 revision 回滚及安全审计。管理操作强制 scope/RBAC/CSRF；既有角色未自动升权，浏览器不保存 raw key。
- Web：性能/告警/设置替换能力缺口页，admin 继承 investigator 诊断权限；修复显式深链刷新，保留未知 API/页面 404。新增控件标签、内部表格滚动和深浅主题；已实际检查桌面与 390px 视图。
- 本地启动：`scripts/start-local.ps1` 支持 `-PrepareOnly`/`-ClientRepo`，准备独立忽略配置/短期凭据/合成事件/八版 migration/backfill，隐藏启动保留维护与告警进程，Ctrl+C 统一停止。Android Sample Debug 使用忽略 properties、V3 发生身份/稳定安装/动态 headers/签名配置与 debug HTTP；Release 七项接入字段为空，offline/control 优先。

验证证据：

- frozen uv sync、Ruff、mypy 59 source files、13 份必需文档及 diff check 通过。后端全测使用 NDK 27 / LLVM 18，包括真实双 ELF symbolizer 回归；最新结果为 **287 tests passed**。产品新增 83 项覆盖精确目录、权限/CSRF/租户、凭据、签名回滚、告警状态/通知/失租、UTC 和历史回填。
- 前端 lint/typecheck、7 suites / 18 Vitest tests、生产 build 通过；JS 316.44 kB / gzip 99.72 kB。真实 Chrome/Playwright 冒烟通过：深链登录/刷新、性能筛选/趋势、独立进程自动触发和手动评估、确认/静默/停用、创建/撤销/viewer 会话失效、配置发布/回滚、窄屏无页面横向溢出、未知路径 404、无浏览器 JS/console 错误。截图及结果：`.local/browser-evidence/`；QA 规则停用、QA key 撤销，历史保留。
- 客户端 JDK 17 的 Sample `assembleDebug/assembleRelease/lintDebug` 通过，无 lint error。四组严格依赖 metadata、47 Markdown / 83 links、Release 空配置和 gitignore 复核通过；没有因仅 Sample 接入重复完整 886 项 SDK CI。
- 真实客户端 `HttpApmUploader` V2/V3 → FastAPI loopback E2E 通过：空 SQLite 升级八版、Gzip、提交后 exact ACK、typed/occurrence persistence、HMAC/明文排除与重放去重。日志：服务端 `.local/product-backend-validation.log`、客户端 `.local/collector-product-e2e.log` 与 `.local-server-build.log`。
- Compose 两份 YAML 和 alerts service 配置静态解析通过；本机无 Docker/PostgreSQL，6 项 PostgreSQL integration 因无 `APM_TEST_POSTGRES_URL` 全部 skip，不计为通过。没有物理设备安装/运行、生产 TLS/SSO/KMS/SigNoz 或真实通知验收；标准 session/device/OS 和 Trace/OTLP Metrics 跨仓契约仍待建设。

操作见 [本地产品功能](11-本地产品功能.md)，架构见 [ADR 0010](adr/0010-本地性能告警与管理面.md)。用户已明确先做本地，生产环境尚未准备。

## 2026-09-12 顺序整改

1. 异常日志隐私：SQLAlchemy 隐藏参数，structlog 与 Uvicorn/stdlib 共用不含消息正文的有界异常诊断；2 项回归覆盖原始参数、SQL、驱动 cause/notes 和 ASGI 二次日志。完整门禁通过：后端 152 tests、mypy 55 files、文档 12、前端 12 tests 及 lint/typecheck/build、依赖锁同步、Ruff、diff check。Windows Temp 退出清理权限告警不影响退出码 0。

2. 趋势与可信度：发布/Issue 缺失桶保留 null，发布桶区分 NO_DATA/UNAVAILABLE/观测 ZERO；Web 断开缺口并保留孤立点，可信度包含新旧版本质量门禁。30 项 Query 回归、155 项后端全测、16 项前端测试及全部静态/构建/文档门禁通过。

3. 发布身份长度：新增 `20260912_0006`，把数据库及接收/制品/查询/决策入口统一到现有协议的 256-byte 上限。129/256/257 边界、中文 UTF-8 查询、制品幂等、六版 SQLite migration/autogenerate 和防截断 downgrade 通过；全门禁为后端 162 tests、前端 16 tests。新增 PostgreSQL 实存回归，当前 6 项集成测试因缺少连接全部跳过。

4. Issue 符号指纹：新增 `20260912_0007` 和 ADR 0009，保留 raw 别名、迁移历史完成任务，在 fenced 完成事务内切换 canonical Issue；旧链接限定 scope/window，拆分歧义返回 409。前端事件下钻使用服务端解析后的指纹。聚合、重放、旧 owner、跨租户、迁移/回退回归通过；全门禁为后端 165 tests、前端 16 tests。

5. Java mapping：校验支持包名、保留类名、内部类、Unicode 和 JVM 合成名字，仍拒绝空段/非法 descriptor/NUL。15 个格式样本和 6 个制品 API 回归通过，包含带包名 mapping 的上传/重放；完整门禁为后端 181 tests、前端 16 tests。

6. Native 多模块：逐帧核对 typed/raw 模块身份和 PC，按精确 ABI/build-id 路由 ELF，缺少任一模块不花费尝试次数；未知、函数级和源码级解析分开，保存逐帧制品证据及部分完成状态。34 项相关测试通过，含 NDK 27.0.12077973 编译双 ELF 并实际执行 LLVM 的交错帧回归；完整门禁为后端 196 tests（显式提供 NDK 路径）、前端 16 tests。

7. 工具输出内存：并发分块处理三条管道，stdout/stderr 各自 1 MiB，在追加前检查；溢出立即 kill/reap，超时/取消清理只丢弃剩余字节。真实 Python 子进程覆盖双流超限后阻塞、精确边界和先输出后读大输入，38 项符号化测试通过；完整门禁为后端 200 tests（含本机 NDK）、前端 16 tests。

8. 保留期证据语义：场景 TTL 不变，仅保存 presence 状态；原可用但已清理的注册字段标 EXPIRED，历史场景缺少 presence 时标 RETENTION_UNKNOWN。SQL 加权覆盖包含可用/缺失/过期/历史未知事件，部分分布 DEGRADED；Web 显示覆盖和原因。SQLite 清理前后、历史兼容、API 元数据/Issue、SDK 真零保留与 Web 展示回归通过。完整门禁为后端 204 tests（含本机 NDK）、前端 18 tests；依赖锁同步、Ruff、mypy 55 files、文档 12、前端 lint/typecheck/build 和 diff check 通过。

最终跨仓库联调：客户端工作目录实际构建的 `HttpApmUploader` V2/V3 → 当前服务端 loopback 通过（退出码 0），测试数据库从空库升级七版至 `20260912_0007`，验证 Gzip、精确 ACK、typed/occurrence 持久化、HMAC/明文排除、重复重放。客户端有其他并行工作，末次检查 HEAD 为 `b776c68`，联调相关 model/uploader 路径干净，其最近改动为 `38969d0`；不能把本次证据归给旧 `90dd147`。两份 JAR 写入时间均早于联调，SHA-256 分别为 model `a128ac0c0d3ef21c595689e3756eff10637b305b0f02bce25cd69f26cd1b2c4a`、uploader `0a034ad075cdefb347c30e873f765a51dababf0d326276f65218a6d17d0d20ca`。首轮在 `C:\Windows\Temp` 清理数据库遇到文件占用；从客户端工作目录、使用用户 `LOCALAPPDATA\Temp` 重跑完整通过，本轮未修改或提交客户端源码。

环境边界：本轮没有 Docker/Podman/psql，未配置 PostgreSQL 集成连接，6 项集成测试明确 skip。SQLite/跨语言 loopback 及本机双 ELF LLVM 验证不能替代 PostgreSQL、真实 R8/设备崩溃采集、生产工具镜像、Compose/SigNoz/TLS 验收。

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

## 已实现（历史快照：2026-09-12 顺序整改之前，五版 schema 时期；当前状态以最新日期条目为准）

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
- OTLP Metrics 独立信号、Trace context、生产托管诊断 UI/身份系统/RLS/KMS/HSM、真实多租户部署、备份/PITR、TLS/WAF、压测/72h soak 尚未验收；本地 Query/BFF/Web 已实现不等于生产现场完成。
- Android SDK 已有动态 header/endpoint provider、签名远程配置、V2 兼容 resource 和 V3 occurrence identity；生产凭据签发/轮换及服务端部署联调仍待完成。精确符号化 schema 已能保存 versionCode/variant/ABI/build-id/module-relative pc，但真实 Java/Native crash 采集、工具和制品 fixture 仍不足以完成现场验收。

## 下一步顺序

1. 在有 Docker 的 Linux/Windows 环境跑 PostgreSQL integration、Compose 和真实 SigNoz staging E2E。
2. 修正真实 SigNoz 返回的任何 dashboard/rule schema/query 差异并保存截图/请求证据。
3. 构建固定 R8/LLVM 工具镜像，接 S3 兼容对象存储/KMS，用真实 Java 与每 ABI Native 样本完成符号化 E2E。
4. 用真实跨升级 outbox、Java mapping 和每 ABI Native crash fixture 验收 occurrence/build/native identity coverage，完成 SDK→Gateway→symbolizer→SigNoz 联调。
5. 补生产 RLS、已实现 retention/backpressure 的现场容量验收、备份恢复、故障注入、容量/性能和安全门禁。
