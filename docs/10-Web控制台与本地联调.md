# Web 控制台、本地联调与上云交接

2026-11-02 当前 Web：性能、告警、设置、Trace 深链均使用真实 API；admin 包含 investigator 入口。会话率由服务端返回状态控制，不硬编码不可计算，也不补缺失。当前 SDK 尚未上报标准 session/device/OS/session_start/span v2，所以其真实数据通常不可计算；历史浏览器/SDK 验收记录不能替代本次证据。

2026-10-14 本地统计扩展：总览/发布新增 Native、Tombstone 与标准运行会话率；Issue 可看设备/OS 及缺失覆盖；性能补齐 SDK 已采集事件；`/apps/:appId/traces` 与 `/traces/:traceId` 提供 investigator/admin 手动 Trace 列表、真实时间线与层级。旧数据保持缺失原因，所有深链使用同源 session/固定 scope。见 [统计契约](12-统计契约与口径.md) 与 [ADR 0011](adr/0011-运行会话统计与多信号导出.md)。

> 实现基线：2026-10-13。性能、告警、设置和 Sample 本地接通详见 [本地产品功能](11-本地产品功能.md)。PostgreSQL/TLS/SigNoz 与真实通知仍需环境准备后现场验证，下文标明日期的浏览器记录属于历史证据。2026-11-07 更正：云端已上线 HTTPS 服务，但公网可达不等于上述现场验收通过，运行事实与待办见 [云端运行环境](13-云端运行环境.md)。

## 当前结论

`AndroidAPM` 负责在 Android 端采集、落盘和至少一次上传，`AndroidAPM-Server` 负责认证、可靠 inbox、查询、网页、符号化和 OTLP/SigNoz 集成。当前仓库已有完整后端技术文档、Query/BFF 和可恢复多路由 Web 垂直切片；网页不是另一套脱离服务端的数据平台，而是同一个 FastAPI 进程提供的受控诊断入口。

部署后的主链路为：

```text
AndroidAPM V3 uploader
  -> https://apm.example.com/v1/events
  -> Gateway / PostgreSQL durable inbox
  -> worker / OTLP Logs / SigNoz

desktop browser
  -> https://apm.example.com/
  -> short-lived same-origin Web session
  -> fixed-scope /v1/query/*
  -> release health / fingerprints / audited evidence / human decision
```

网页当前覆盖：

- `apmq1` 登录换短时会话；长期 Query Key 不写入浏览器存储、URL 或静态文件。
- 新版本与明确基线的 Java Crash、ANR、受影响/活跃安装、安装率和指纹比较。
- 发生时版本、installation、SDK drop、迟到、protocol/schema/inbox 数据质量条。
- investigator 的事件列表、L1 元数据和 purpose/reason 审计后的 L2 raw。
- 人工 `continue/pause/rollback` 记录；不会自动调用发布平台。
- `ZERO/NO_DATA/UNAVAILABLE/UNKNOWN_COVERAGE/DEGRADED/LATE/ERROR`、部分失败、窄屏和深浅主题。
- 独立总览、Issues、Issue 详情、事件探索、事件详情、版本和数据质量路由；刷新或复制深链后可以恢复页面。
- Issue 发生趋势与版本/场景分布来自 durable inbox；设备/Android 版本缺标准协议字段时显示 `UNKNOWN_COVERAGE`。
- 性能为精确契约的分位数/覆盖/趋势/版本比较；告警有持久化规则、实例和通知 outbox；设置有 admin 管理 API 与审计。缺观测或渠道时明确显示无数据/未配置。

尚不属于当前本地网页完成项：SSO/账号密码体系、跨 tenant 管理、标准 session/device/OS/Trace 契约，以及自动发布与生产现场验收。2026-11-07 更正：云端已有一条运行中的 HTTPS 部署（见 [云端运行环境](13-云端运行环境.md)），但部署配置未回收、现场验收未通过，不能再把「真实云部署」整体列为未完成。制品上传使用 CI API，网页提供元数据和任务状态。

## 同源认证边界

浏览器不能长期保存 `apmq1`。当前流程冻结为：

1. 用户通过 HTTPS 或 loopback 向 `POST /v1/web/session` 提交一次 `apmq1`。
2. 服务端完成 Argon2id 校验，从数据库取得固定 tenant/app/environment/role。
3. 服务端返回不含 Query secret 的短时签名 Cookie；Cookie 为 host-only、`HttpOnly`、`SameSite=Strict`，生产必须设置 `Secure`。2026-11-07 起响应另含非敏感运行时 `environment` 标签（`APM_ENVIRONMENT`），仅供控制台区分 local 合成预览与真实部署；它不是授权输入，也不等于 Query Key 固定的数据 `environment`。
4. 签名 payload 只保存 Query key ID、签发/过期时间和 CSRF token digest，不保存 tenant 数据或长期 secret。
5. 每次请求按 key ID 回查 Query Key 与 tenant 状态；撤销、禁用或到期立即让现有会话失效。
6. Cookie 驱动的 POST/DELETE 还必须同时提交与签名会话绑定的 CSRF header；显式 Bearer API 客户端不使用 ambient cookie，保持原接口兼容。
7. `/v1/web/session` 和 `/v1/query/*` 均为 `private, no-store`；网页 CSP 只允许同源脚本、样式和 API。

独立 secret：

| 配置 | 用途 | 约束 |
| --- | --- | --- |
| `APM_WEB_SESSION_HMAC_KEY_B64` | Web 短时会话签名 | Base64 解码至少 32 bytes；不得与其他 key 复用 |
| `APM_QUERY_CURSOR_HMAC_KEY_B64` | 事件分页 cursor | 独立至少 32 bytes；轮换使旧 cursor 失效 |
| `APM_INSTALLATION_HMAC_KEYS_JSON` | installation 假名化 | 版本化 key ring；仍可能重放的旧版本不得删除 |
| `apmq1` | Query 身份与固定 scope | 数据库只存 Argon2id hash；只显示一次，可撤销 |

生产 `APM_ENVIRONMENT=production` 会拒绝非 Secure Web Cookie 或弱/空会话签名 key。TLS 终止代理必须保留正确的 forwarded proto/host，外部只能通过 443 进入。

决策背景见 `docs/adr/0007-同源Web控制台与短时会话.md`。

## 本地一键启动

本机没有 Docker 时使用明确的 SQLite compatibility preview；它只用于网页和 API 行为验证，不构成 PostgreSQL durability 证据。

```powershell
cd D:\workspace\AndroidAPM-Server
powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1
```

脚本会：

1. 在被 Git 忽略的 `.local/` 生成并持久化本地 HMAC 配置。
2. 按 lockfile 同步 Python/前端依赖并构建 `web/dist`。
3. 对 `.local/androidapm-preview.db` 执行 Alembic migration。
4. 写入明确标记为 synthetic 的 1.0.0/2.0.0 Crash/ANR 可信场景，包含 V3 occurrence 与一个被排除的 V2 compatibility fact。
5. 创建一天有效的 admin Query Key 和本地 Android ingest key，写入忽略文件 `.local/local-credentials.json`；客户端属性写入 `.local/apm-collector.properties`，不打印 secret。
6. 在 `http://127.0.0.1:8080` 启动同源 API 与网页。

synthetic fixture 只验证页面状态、查询口径和权限路径；页面不把它标为生产数据。要跳过重复依赖同步可使用 `-SkipSync`，要仅使用真实 Android 上传可使用 `-SkipSeed`。

本地停机使用 `Ctrl+C`。`.local/` 可以在确认不再需要本地 fixture/密钥后手工删除；脚本不会修改 PostgreSQL 或云端数据。

## Android 真机/模拟器本地上传

`start-local.ps1` 输出的 ingest key 仅供本地 debug build。生产长期密钥不得进入 APK；生产仍要建设短期 Token 签发/撤销。

物理设备优先使用 ADB 反向端口：

```powershell
adb reverse tcp:8080 tcp:8080
```

本地 debug 配置使用 compatibility profile 允许 loopback HTTP，同时仍选择 V3、SQLite durable outbox、Gzip 和三参数 occurrence 初始化。`PRODUCTION_STRICT` 会正确拒绝 HTTP；云端 TLS 可用后再切回 strict。

```kotlin
val localIngestKey = debugSecretProvider.requireLocalIngestKey()
val installation = installationIdStore.anonymousId()

Apm.init(
    this,
    ApmConfig(
        endpoint = "http://127.0.0.1:8080/v1/events",
        serializationFormat = SerializationFormat.PROTOBUF_ENVELOPE_V3,
        storageType = StorageType.SQLITE,
        enableHttpGzip = true,
        resourceContext = ApmResourceContext(
            serviceName = packageName,
            serviceVersion = BuildConfig.VERSION_NAME,
            deploymentEnvironment = "production",
            installationId = installation
        ),
        httpHeaderProvider = HttpHeaderProvider {
            mapOf("Authorization" to "Bearer $localIngestKey")
        }
    ),
    ApmOccurrenceContext(
        serviceVersion = BuildConfig.VERSION_NAME,
        versionCode = BuildConfig.VERSION_CODE.toString(),
        appBuild = BuildConfig.GIT_SHA,
        variant = BuildConfig.BUILD_TYPE,
        installationId = installation
    )
)
```

宿主若声明了自动 `ApmInitProvider`，V3 必须按 Android 客户端文档移除该 Provider并使用三参数手动初始化。模拟器也可把 endpoint 换为 `http://10.0.2.2:8080/v1/events`。这些 HTTP 例外只能存在于 debug/local 配置和本地 secret provider 中。

## 前端开发与门禁

技术栈为 React、TypeScript、Vite、Vitest 和 ESLint；构建产物由 FastAPI 同源提供，Docker 镜像用独立 Node stage 生成，不把 `node_modules` 带入 Python runtime。

```powershell
pnpm --dir web install --frozen-lockfile
pnpm --dir web run lint
pnpm --dir web run typecheck
pnpm --dir web run test
pnpm --dir web run build
```

开发期可并行启动 API 与 Vite：

```powershell
uv run androidapm-api
pnpm --dir web run dev
```

Vite 只把 `/v1` 和 `/health` 代理到 `127.0.0.1:8080`。任何环境都不得把 Query Key 编译进 `VITE_*`、源码或静态产物；前端也不得从响应裸数字推断状态。

## 2026-08-28 本地浏览器验收

在 `.local/androidapm-preview.db` 的明确 synthetic fixture 上，使用最终 production build 和真实浏览器完成：

- Query Key 一次交换、固定 `local-preview / com.example.androidapm / production / investigator` scope 和退出流程。
- 2.0.0 对 1.0.0 的发布健康、9 条事实、数据质量、2 个 Java Crash、1 个 ANR、安装覆盖和指纹聚合。
- 指纹筛选、L1 occurrence/installation/protocol 元数据，以及填写 purpose/reason 后的 L2 raw 审计读取。
- `continue/pause/rollback` 三种选择与暂停决策写入/回读；它只记录判断，不调用发布平台。
- 9.9.9 无样本查询显示 `NO_DATA`、破折号和空状态说明，不伪装为零。
- 深浅主题实际颜色切换，390 x 844 窄屏无页面级横向溢出，且不暴露租户/密钥管理动作。
- 浏览器控制台无 warning/error；页面图标最终返回 `200 image/svg+xml`。

这证明本地 UI、同源 API 和权限路径可运行，不证明公网 HTTPS、PostgreSQL/SigNoz、通知、备份或 HA 已部署。

## 2026-09-04 多路由浏览器验收

使用当前 production build、同源 FastAPI 和明确 synthetic fixture 完成以下增量验收：

- 1536 × 1024 下总览、Issues/详情、事件探索/详情、版本发布、数据质量及三张能力边界页均可达，页面与权限状态正确；Issue 深链刷新可恢复。
- Issue 详情可以切换真实版本/场景聚合；设备与 Android 版本没有标准 resource 时展示 `UNKNOWN_COVERAGE`，没有把 extras 冒充标准维度。
- L2 raw 在页面打开时不请求；调查员提交 purpose/reason 后才显示原始/符号化并列区，控制台无 warning/error。
- 390 × 844 下移动导航抽屉可用，根页面 client/scroll width 都为 375 px；宽表只在自己的容器内横向滚动。
- 768 CSS px 重排覆盖 1536 桌面在等效 200% 文本缩放下的布局；深浅主题切换后刷新仍保留。

两张预期设计参考保存在 `docs/design/apm-overview-concept.png` 与 `docs/design/issue-detail-concept.png`。实现保持其工作台层级、密度、可信度门禁、趋势和分布结构；没有实现参考图中的告警实例、Issue owner/lifecycle、异常类标题或标准设备/OS 值，因为当前后端/客户端契约尚不支持这些真实事实。

## 下午购云建议

如果目标是先完成一个可复核的单租户 staging，而不是立即承诺生产 HA，推荐购买：

| 项目 | 建议 | 原因 |
| --- | --- | --- |
| OS | Ubuntu 24.04 LTS x86_64 | Docker/Foundry/运维资料成熟 |
| 规格 | 8 vCPU / 16 GiB RAM | 单机同时跑 Gateway、PostgreSQL、SigNoz/ClickHouse 的保守起点 |
| 磁盘 | 200 GiB SSD/NVMe，可扩容并支持快照 | ClickHouse、PostgreSQL WAL 与原始 telemetry 增长快 |
| 网络 | 靠近主要设备地域；独立公网 IPv4；带宽至少 10 Mbps | 减少移动端上传延迟，便于 DNS/TLS |
| 域名 | 独立子域，例如 `apm.example.com` | HTTPS、Cookie 与客户端 endpoint 的稳定边界 |
| 安全组 | 公网仅 80/443；SSH 仅固定管理 IP | PostgreSQL、OTLP、SigNoz 内部端口不得公开 |
| 备份 | 云盘快照 + PostgreSQL WAL/PITR 目标 | 单机 staging 也要先验证恢复流程 |

4 vCPU / 8 GiB 可以仅跑 Gateway/Web/worker + 外部 PostgreSQL/SigNoz，但不建议把完整 SigNoz/ClickHouse 也挤在该规格上。真正 production 不应把“单台机器跑通”当 HA：至少分离托管 PostgreSQL，评估 SigNoz/ClickHouse 数据盘、第二 Gateway 故障域、对象存储、监控和备份。

## 服务器到手后的现场顺序（2026-11-07 状态校正）

> 状态：服务器已到手且云端已有一条运行中的 HTTPS 部署，但**并非按本节 compose 清单部署**（对外容器带 `public-trial` 前缀、存在空跑 gateway、反代配置与仓库不一致）。本节命令保留为「从干净服务器重建」的标准顺序，不能直接套用到现有实例；先按 [云端运行环境](13-云端运行环境.md) 第 5 节回收实际配置。域名仍未设置，短期证书续期依赖 443 常开。

1. 建立非 root 运维账号、SSH key、时钟同步、自动安全更新和最小安全组。
2. 安装 Docker Engine/Compose，配置独立数据盘和日志轮转。
3. 解析域名，部署 Caddy/Nginx，取得 TLS 证书；先验证 HTTPS 与 Secure Cookie。
4. 生成互不相同的 PostgreSQL、installation、cursor、Web session 等 secrets，放入服务器 secret/env 文件，禁止提交 Git。
5. 构建一次不可变镜像；执行 PostgreSQL migration，再启动 Gateway/worker。
6. 用真实 Android V3 uploader 验证 Gzip、exact ACK、重放、HMAC 明文删除和网页 fixed scope。
7. 安装官方锁定的 SigNoz/Foundry，把 OTLP endpoint 指到私网；验证查询、Dashboard 与 alert schema。
8. 完成代理丢 ACK、PostgreSQL transaction/并发、跨租户负向、通知触发/恢复、备份恢复和容量证据后，才提升为 production。

仓库已经提供多阶段 `Dockerfile`、loopback-only 基础 Compose、`compose.production.yaml`、Caddy 自动 TLS 和 `.env.production.example`。服务器首次执行：

```bash
cp .env.production.example .env.production
# 编辑域名、数据库密码、OTLP endpoint 和三组互不相同的 Base64/HMAC secret
# 部署前置强校验：只输出键名与失败原因，不打印任何值
python scripts/check_production_config.py --env-file .env.production

docker compose --env-file .env.production \
  -f compose.yaml -f compose.production.yaml config

docker compose --env-file .env.production \
  -f compose.yaml -f compose.production.yaml up -d --build \
  postgres migrate gateway worker caddy

docker compose --env-file .env.production \
  -f compose.yaml -f compose.production.yaml ps
```

只有固定 R8/LLVM 镜像和真实制品准备后才附加 `--profile symbolization`。基础 `gateway:8080` 默认只绑定宿主 `127.0.0.1`，公网由 Caddy 暴露 80/443；安全组仍必须阻止数据库、OTLP 和 SigNoz 内部端口。2026-11-07 起 `deploy/caddy/Caddyfile` 为所有响应补 HSTS / nosniff / frame-deny / no-referrer，并屏蔽公开 `/docs`、`/openapi.json`、`/redoc`；`reverse_proxy` 主动健康检查使用进程级 `/health/live`（连续 3 次失败才判不可用），确认 inbox schema 的 `/health/ready` 留作部署流程发布前的轮询门禁。线上仍是旧配置，需重新部署才生效。

2026-11-07 更新：云厂商/地域与服务器公网地址已确定（首尔轻量应用服务器，具体地址只保存在被忽略的 `.local/SERVER-CONFIG.md`），但**域名及 DNS 控制权仍未设置**，是否使用托管 PostgreSQL、数据保留天数和通知渠道仍待确认。当前线上部署形态未纳管，重建或升级前必须先回收配置；本机无 Docker，本节命令未在本轮实际执行。除此之外，本地代码、网页、镜像构建链和联调入口已经准备好。
# 2026-09-07 页面证据一致性

路由、会话 scope 或筛选条件变化时重新建立页面状态，立即移除旧 raw/聚合/决策表单。所有读取使用请求代次拒绝过期响应；同页重试先清空旧证据。总览依赖的任一请求失败都展示错误，避免拼接不同时刻的质量与发布结论。发布决策仅在当前证据加载完成时可提交，失败保留理由和确认状态。回归覆盖刷新失败、响应乱序和跨事件迟到 raw；本地前端四门禁通过（12 tests）。
