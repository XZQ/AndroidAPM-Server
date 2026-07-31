# OTLP 映射与 SigNoz 集成

## 集成原则

Gateway 只调用标准 OTLP/HTTP Protobuf endpoint，不写 SigNoz ClickHouse 内部表。所有 AndroidAPM 事件首先导出为 OTLP LogRecord，以保留完整事件语义；只有字段注册表明确声明为数值/单位/聚合类型的数据才附加导出为 OTLP Metric。不能为追求 APM 界面而把任意事件伪造成 Span。

## Resource 属性

| AndroidAPM | OTLP resource attribute |
| --- | --- |
| appId | `service.name`、`android.apm.app_id` |
| environment | `deployment.environment.name` |
| appVersion | `service.version` |
| appBuild | `service.instance.build_id` |
| sdkVersion | `telemetry.sdk.name=androidapm`、`telemetry.sdk.version` |
| processName | `process.executable.name` |
| installationId(HMAC) | `service.instance.id` |
| ABI/build-id | `host.arch`、`android.apm.native.build_id` |

tenant ID 作为受控 resource attribute `android.apm.tenant_id` 注入，绝不采信事件自身声明。

## LogRecord 映射

- `time_unix_nano`：事件 timestamp；`observed_time_unix_nano`：Gateway 接收时间。
- severity：DEBUG/INFO/WARN/ERROR/FATAL 映射 OTel severity number/text。
- body：稳定摘要 `<module>.<name>`，不把整个 raw payload 拼成字符串。
- attributes：`android.apm.event_id,module,name,kind,priority,thread,scene,foreground`，以及字段注册表允许的 fields/context/extras。
- 原始规范化 JSON 只在需要且符合保留/隐私策略时作为受限属性或留在 inbox；不默认索引所有 key。

Crash/ANR 使用 OTel exception 语义属性：`exception.type,message,stacktrace`，并附 `android.apm.error.fingerprint,symbolication.status`。未符号化堆栈与符号化结果分字段保存，禁止覆盖原始证据。

## Metric 映射

字段注册表按 `(schema_version,module,name,field)` 定义：类型、单位、temporality、monotonic、聚合方式、可用维度和高基数策略。例如：

| 事件字段 | Metric | 类型 |
| --- | --- | --- |
| `startup.*.durationMs` | `android.apm.startup.duration` | histogram, ms |
| `network.*.durationMs` | `android.apm.network.duration` | histogram, ms |
| `network.*.statusCode` | 不单独作为数值 metric | 作为受控维度分组 |
| `sdk_health.dropRate` | `android.apm.sdk.drop_ratio` | gauge, 1 |
| `sdk_health.queueSize` | `android.apm.sdk.queue.size` | gauge, `{event}` |
| `frame.*.durationMs` | `android.apm.frame.duration` | histogram, ms |

legacy Line/Protobuf 把 `fields` 序列化为 string，因此 worker 必须依据注册表进行严格解析；V2 envelope 为字段 15 携带显式标量类型，Gateway 同时持久化值和 `field_types`，其中 BigInteger/BigDecimal 保留精确文本。解析失败时保留 LogRecord、增加映射错误计数，不能猜测类型或丢掉整个事件。

## Trace 映射

只有事件携带有效 trace/span ID 和明确开始/结束语义时才生成 Span。网络、WebView 或慢方法的孤立 completion 事件第一阶段保持 LogRecord/Metric；后续 SDK 冻结 W3C trace context 和 span envelope 后再启用原生 Trace。这样避免虚假 parent/时长污染 SigNoz APM 图。

## SigNoz 版本与部署

- 应用版本锁：SigNoz `v0.133.0`。
- 官方安装配置锁：Foundry `v0.2.13`。
- 安装资产从 `SigNoz/foundry` 官方 release 获取并校验 checksum；本仓库只保存覆盖层、环境变量模板和验收脚本。
- 升级先在 staging 回放固定 OTLP fixture，验证字段、查询、Dashboard、告警和保留，再滚动生产。

## 失败语义

OTLP `2xx` 才视为成功；`429/502/503/504` 和连接错误可重试；明确的 schema/认证 4xx 进入 dead letter 并告警。响应丢失可能导致 OTLP 重发，稳定 eventId 必须随 LogRecord 发出，Dashboard/聚合作业按该 ID 去重。
