# ADR 0006：发生时身份与 installation 假名化

- 状态：Accepted / Implemented
- 日期：2026-08-28

## 背景

Collector V2 的 `ApmResourceContext` 在 uploader drain durable outbox 时附到整个 batch。历史 `ApmEvent` 和 durable codec 没有保存该 resource；应用升级后，旧版本事件可能携带新进程的 `serviceVersion` 和请求 Header。历史服务端还曾把 V2 installation 明文复制到 `payload_json.unknown.resource.installationId`，且没有专用 HMAC/key-version 列。

这两个事实会让发布版本回归、受影响安装分母和符号化看似可算、实际归因错误。tenant/app/environment 可以由凭据认证，但版本、构建和 installation 仍只是客户端声明，必须同时记录来源质量。

## 决策

1. 不重解释 schema V2。显式 envelope schema V3 为每条事件携带 occurrence snapshot，并要求客户端在事件写入 durable outbox 前固化：app version name、versionCode、appBuild、variant 和 anonymous installation；Native 再携带 ABI、module build-id 与 module-relative PC/load-bias 语义。
2. 服务端持久化 `AUTHENTICATED/OCCURRENCE_BOUND/BATCH_DECLARED/REQUEST_DECLARED/HOST_CONTEXT/ABSENT` 等身份质量。legacy/V2 继续接收，但 batch/request release identity 默认不进入生产版本比率或精确符号化。
3. installation wire 值只允许宿主生成的匿名标识。Gateway 在认证和完整 envelope 校验后、durable inbox 写入前，使用版本化 secret、固定 domain 和 tenant ID 计算 HMAC-SHA-256，持久化 HMAC 与 key version，并从规范化 payload 删除明文。
4. 同一 tenant/eventId 的 occurrence snapshot 变化属于 `event_id_conflict`。原始事件内容继续保留，但“保留 unknown”不覆盖已知 installation 的入库前最小化规则。
5. key 轮换必须定义双版本/alias 过渡或显式连续性断点；查询不能把 key 变化造成的标识拆分解释成安装增长。

## 不采用的方案

- **继续使用当前 V2 batch resource/Header**：无法区分升级前 outbox 事件和当前进程版本。
- **按接收时间、上传版本或最近制品猜测**：会静默产生错误版本归因和错误符号结果。
- **只在 Dashboard 增加提示**：不能修复底层身份错误，其他查询仍会误用。
- **长期保存 installation 明文或可逆密文供分析**：扩大泄漏、权限和删除面；首个切片不需要恢复原值。
- **把业务必需字段作为 V2 可选字段静默追加**：旧服务端会忽略并仍返回 V2 ACK，双方无法证明语义一致。

## 后果

- Android 客户端已用 additive API、durable codec V4、V3 serializer/uploader 和显式 occurrence-aware `Apm.init` 实现该决策；服务端已用 V3 decoder、append-only `20260828_0003` migration、identity normalization 和兼容查询实现该决策。
- V2 仍只能关闭 Collector/wire 和受控兼容查询验收；生产版本/安装分母只纳入 V3 `OCCURRENCE_BOUND` 行。
- installation HMAC 是假名化而非匿名化，仍受最小权限、保留、导出、删除和审计规则约束。
- Dashboard、告警和 Query/BFF 必须显示 identity quality/coverage；缺失身份的事件继续作为 raw evidence 和允许的事件计数存在，不能填零或进入相应分母。

## 验收

- 旧版本事件先入 outbox，升级应用后上传，仍归旧 occurrence release/build。
- 当前 V2 相同场景被标为 `BATCH_DECLARED` 并排除在生产版本比率和符号化之外。
- 两个 tenant 对相同 installation 输入得到不同 HMAC；数据库 payload、日志、OTLP、Dashboard 导出和通知均不存在明文。
- 同 eventId 改变 occurrence snapshot 被拒绝，合法重放仍精确 ACK 且只形成一个逻辑事实。

2026-08-28 的本地跨仓 E2E 构建真实 Android `HttpApmUploader`，经 Gzip/uvicorn 分别发送并重放 V2/V3 batch；测试用 SQLite inbox 证明精确 ACK、唯一事件、occurrence release/build/variant、native frame、tenant-scoped HMAC 和明文删除。该证据不替代 PostgreSQL/TLS/SigNoz 现场验收。
