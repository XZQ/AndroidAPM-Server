# OTLP 映射、事件目录与 SigNoz 集成

> 事件目录审计基线：2026-08-28，干净客户端工作树 `AndroidAPM/develop@e1dd627`。本文同时描述目标契约和当前实现；标记为“规划”的能力不得从文档推断为已上线。

## 集成原则

Gateway 只调用标准 OTLP/HTTP Protobuf endpoint，不写 SigNoz ClickHouse 内部表。所有 AndroidAPM 事件首先导出为 OTLP LogRecord，以保留完整事件语义；只有字段注册表明确声明为数值、单位、聚合类型和数据状态的字段才允许附加导出为 OTLP Metric。不能为追求 APM 界面而把任意 completion 事件伪造成 Span。

数据处理顺序冻结为：

```text
durable raw event
  -> protocol/resource quality classification
  -> source-reviewed field registry
  -> timestamp and incident normalization
  -> privacy/cardinality policy
  -> OTLP LogRecord (lossless searchable evidence)
  -> optional Metric/Span projection (registered fields only)
```

## 当前实现与目标差距

2026-09-07：normalization v2 对注册整数执行 OTLP int64 边界、对数值执行有限数校验，并检查计数/时长非负、比率 0–1 和历史退出时间范围。非法派生字段标记 INVALID，原始值继续保留，不让 NaN/Infinity 重新进入 PostgreSQL JSON。历史已入库坏行若无法映射，export worker 单独标记 recoverable dead letter，正常同批继续导出；非预期传输异常也必须消耗有限重试预算。

| 能力 | 当前代码事实 | 生产目标 |
| --- | --- | --- |
| OTLP signal | 仅实现 OTLP Logs | 注册表覆盖后再增加确定性的 Metrics；Trace 需有效 span 契约 |
| fields | initial source-reviewed registry covers `java_crash/anr_detected/app_exit/core/sdk_health`; legacy 类型恢复只发生在注册字段 | 继续按真实场景扩展版本化注册表；解析失败保留 Log 并标记质量 |
| attributes | mapper 只导出 normalization 的 allow-listed indexed attributes；任意 fields/context/extras 留在 inbox | 为新增属性执行 PII 与 cardinality review，不恢复全量展平 |
| installation | V2/V3 入库前写专用 tenant-scoped HMAC/key version并删除已知明文 | 用真实安装窗口验证活跃/受影响分母、轮换和删除语义 |
| device/OS | 客户端无标准 resource 字段，只有宿主可选 context | 冻结标准低基数字段后才成为保证维度；此前显示 coverage/unknown |
| session | 没有统一 telemetry session 契约 | 客户端新增 occurrence-bound session 后才能计算 Crash-free sessions |
| symbolization | 服务端 job/adapter 与 V3 identity schema 已有；真实采集链/工具/fixture 未验收 | 每事件 versionCode/appBuild/variant/ABI/build-id/relative PC 完整且可验证 |
| Query/Dashboard/alert | fixed-scope Query/BFF、一个 overview、Crash/ANR/SDK-drop 三个无 channel 模板；未做真实 SigNoz staging | 托管 UI、场景空状态、低样本、租户 scope、触发/恢复全部验收后推广 |

## Resource 与可信身份

tenant/app/environment 来自已认证凭据和服务端 scope，不采信 event field。V2 resource 与 V3 occurrence 仍是需要严格校验并记录质量的客户端声明；V3 的优势是绑定事件发生/落盘时刻，不是变成认证身份。

| 输入 | 目标 OTLP resource attribute | 当前状态 |
| --- | --- | --- |
| authenticated tenant | `android.apm.tenant_id` | 已实现；只允许服务端注入 |
| authenticated appId | `service.name`、`android.apm.app_id` | 已实现 |
| authenticated environment | `deployment.environment.name` | 已实现 |
| V2 `service_version` / legacy Header | `service.version` | 已实现兼容映射；质量仅为 `BATCH_DECLARED/REQUEST_DECLARED`，不能证明旧 outbox 事件的发生版本 |
| V3 occurrence release/build/variant | `service.version` 与 allow-listed Android identity attributes | 已实现；质量为 `OCCURRENCE_BOUND`，可进入版本切片 |
| `appBuild` Header | `service.instance.build_id` | 已实现可选映射；客户端未标准化自动提供 |
| SDK version | `telemetry.sdk.name=androidapm`、`telemetry.sdk.version` | 已实现 |
| event process | `process.executable.name` | 已实现 |
| V2/V3 `installation_id` | `android.apm.installation.hmac` + key version | 已实现假名化；明文不进入 durable payload/OTLP/Query |
| V3 ABI / GNU build-id | `host.arch`、`android.apm.native.build_id` | schema 与 durable native identity 已实现；真实 Native collector coverage 未验收 |
| device model / OS / locale / network | 受控 `device.*`、`os.*` 等 | 规划：当前只能来自宿主自定义 context，不能假设存在或可信 |

`installation_id` 必须是宿主生成的匿名稳定安装标识，不得使用 user ID、广告 ID、IMEI、手机号或 Token。HMAC key 属于服务端 secret，按 tenant/domain 版本化；轮换必须支持有限重叠或离线重算，不能把明文写入审计日志。

所有依赖身份的投影同时携带 `android.apm.identity.release_quality`、`android.apm.identity.installation_quality` 和缺失原因。只有 `OCCURRENCE_BOUND` release identity 才进入生产版本回归；只有已 HMAC 且 key version 明确的 installation 才进入安装分母。当前 V2 batch resource 可以驱动受控同版本 fixture，但不能通过 Dashboard filter 把它升级成 occurrence 事实。

## LogRecord 映射

- `time_unix_nano`：规范化发生时间；`observed_time_unix_nano`：Gateway 接收时间。
- severity：DEBUG/INFO/WARN/ERROR/FATAL 映射 OTel severity number/text。
- body：稳定摘要 `<module>.<name>`，不把整个 raw payload 拼成字符串。
- 基础 attributes：`android.apm.event_id,module,name,kind,priority,thread,scene,foreground,protocol,schema_version`。
- 注册 attributes：仅导出字段注册表允许的 fields/context/extras；高基数字段默认不进入可分组属性。
- 原始规范化 JSON 保留在 inbox/受限详情，按 retention 和隐私策略处置；它不是任意用户可查询的公共属性包。

Crash/ANR 使用 OTel exception 语义属性：`exception.type,message,stacktrace`，并附 `android.apm.error.fingerprint,symbolication.status`。未符号化堆栈与符号化结果分字段保存，禁止覆盖原始证据。

## 客户端真实事件目录

下表只列当前源码实际构造的事件。正常 `Apm.emit` 会经过有界队列、动态采样、可选聚合、限流、脱敏和 durable outbox；ERROR/FATAL 绕过动态采样/限流，但仍可能因本地资源预算失败。`java_crash` 与 `anr_detected` 使用 `emitCriticalSync`，绕过共享队列/采样/聚合/限流并同步到本地 durable hand-off。客户端 aggregation 默认关闭；开启后 METRIC 可能变成 `<name>_aggregated`，只保留窗口统计且按 `module/name` 聚合，会丢失 scene/context 维度，查询不得与 raw 事件直接混算。

| 事件族 / module | 实际 event name | 主要字段与单位 | 产生条件 | 服务端第一阶段语义与缺失解释 |
| --- | --- | --- | --- | --- |
| Crash / `crash` | `java_crash` | exceptionClass/message/stackTrace/threadName/processName | Java crash 默认开启；critical hand-off | 现场 Java Crash 事实；原始栈可查，精确 retrace 仍需 occurrence-bound build identity |
| Crash exit evidence / `crash` | `app_exit` | exitTimestamp, reasonCode/name, importance, optional trace | API 30+ 下一次启动读取，默认开启 | 历史退出证据；发生时间取 `exitTimestamp`，普通退出不算 Crash；与现场事件单列避免双计 |
| Native crash / `crash` | `native_crash`, `tombstone_crash` | signal/name, threadName, backtrace, optional faultAddr | Native 默认关闭；安全默认不在 signal handler 回调 Java，tombstone 为启动后降级 | 原始证据；缺 ABI/build-id/module-relative PC 时只显示 `metadata_missing`，不伪符号化 |
| ANR / `anr` | `anr_detected` | mainThreadStack, anrSource/cause, durationMs, optional tracesContent/stackSamples | 注册后 SIGQUIT/watchdog；critical hand-off | 现场 ANR 事实；可按 cause/source/版本下钻，和 `app_exit:ANR` 不相加 |
| Launch / `launch` | `cold_start`, `warm_start`, `hot_start`, `first_frame_rendered`, `launch_bottleneck` | launchDurationMs, launchType, firstActivityClass, phase*Ms, bottleneck* | Activity 自动；ContentProvider/Application 阶段需要宿主手动标记 | 分布/版本回归；缺 phase 不是 0，表示未接线或该启动类型不适用 |
| Network / `network` | `network_request`, `network_error`, `network_aggregate`, `network_phase` | url/method/statusCode/durationMs/size, dns/tcp/tls/header/body Ms | OkHttp interceptor/listener、显式 HttpURLConnection helper 或 callback | endpoint 需模板化；无事件只能是 coverage unknown；HttpURLConnection 不具备伪分阶段数据 |
| FPS / `fps` | `fps_stats` | fps, windowDurationMs, frame/jank/frozen/dropped counts, refreshRate, FrameMetrics ns | Activity 生命周期；API 24+ FrameMetrics，失败回退 Choreographer | 窗口 metric；真 0 与无回调分开，按实际 window 加权，不能把 callback 数当完整帧数 |
| Render / `render` | `view_count_spike`, `deep_hierarchy`, `frame_metrics` | viewCount/maxDepth/activity/threshold, frame counts/average/max Ms | Activity 自动；FrameMetrics API 24+ | View 树 finding 与帧 metric；不推断 GPU overdraw |
| Memory / `memory` | `memory_snapshot`, `memory_alert`, `memory_leak` | Java heap MB, PSS/native/system KB, GC, reason, leakClass/type/count | 周期采样；泄漏检测按配置/生命周期 | 快照、阈值告警和 finding 分开；缺快照可能是模块/进程/采样状态 unknown |
| OOM/native heap / `memory` | `oom_critical`, `oom_warn`, `system_low_memory`, `system_mem_warn`, `native_heap_warn`, `native_heap_stats` | ratios, heap/PSS KB/bytes, thresholds | 注册/阈值；native stats 使用 Android Debug API | 阈值 finding；API 不提供的 peak/allocCount 不得用 0 表示真实测量 |
| Hprof / `memory` | `hprof_dump` | reason, device-local filePath, fileSizeKb | dump 默认关闭，可能 STW | 仅“端上文件已生成”的 metadata；服务端不能访问设备路径，不等于制品已上传 |
| GC / `gc_monitor` | `memory_churn` | gcCount/time delta, countersAvailable, heap growth/ratio, allocation/reclaim availability/rates, windowMs | 后台 ART 累积计数采样 | availability=false 时对应派生值为 unavailable，不填 0；其余 heap 维度仍可用 |
| Thread / `thread_monitor` | `thread_count_spike`, `duplicate_thread`, `blocked_thread`, `thread_pool_backlog` | thread/pool name, count/threshold, queue/pool/active/completed | 线程扫描自动；真实 ThreadPoolExecutor backlog 需显式注册 | finding；未注册线程池时 backlog coverage unknown，不等于队列为 0 |
| Battery / `battery` | `cpu_high_usage`, `battery_drain`, `wakelock_held_too_long`, `wakelock_still_held`, `gps_used_too_long`, `gps_still_active`, `alarm_schedule_flood` | percent/level/drop/duration, tags/count/threshold | CPU/电量采样；WakeLock/GPS/Alarm 为宿主 callback | 只对已接线信号计算；tag 需归类/基数限制，缺事件不是无耗电 |
| IO / `io` | `io_issue`, `io_main_thread`, `io_small_buffer`, `io_duplicate_read`, `io_closeable_leak`, `io_fd_leak`, `io_zero_copy_opportunity`, `io_throughput` | path/op/duration/bytes/buffer/count/throughput | 显式 stream wrapper；Native path 依赖 xhook，默认不自动接管任意流 | finding/窗口分开；路径必须归类，Java/native 重叠需按 hookLevel 去重 |
| SQLite / `sqlite` | `slow_query`, `main_thread_db`, `large_db_operation`, `query_plan_issue` | sql/durationMs/rows/thread/db/issue/table/detail | wrapper 或 callback；完整 QueryPlan 仅 wrapper rawQuery | SQL 只进受限详情，Dashboard 用 statement fingerprint/table 分类；无事件为 coverage unknown |
| WebView / `webview` | `slow_page_load`, `slow_js_execution`, `white_screen`, `slow_js_bridge`, `js_console_error`, `slow_resource`, `resource_waterfall` | URL/duration/threshold, JS/bridge/error, resource counts | 对指定实例 install/delegate/callback | URL/source/JS/error 高基数且敏感；未 install 的实例不可从零事件推断健康 |
| IPC / `ipc` | `slow_binder_call`, `binder_call_aggregation` | interface/method/duration/mainThread, count/average/max/slowCount | 显式 `traceBinderCall` 或 completion callback | finding/聚合分开；不宣称 hidden Binder hook |
| Slow method / `slow_method` | `slow_method_detected`, `hot_methods_detected`, `slow_method_instrumented` | durationMs/severe/looper stack/top methods/sample count/method | Looper/采样；instrumented 需要宿主应用 ASM plugin | method/signature 高基数，需归一化；没有 ASM 事件不等于没有慢方法 |
| Trace / 配置的动态 module/name | event name 为宿主 span name | traceId, spanId, parentSpanId, duration_ms, status, `attr_*` | 宿主手动使用 `ApmTrace` | 可验证 ID/时间语义前保持 Log；动态 name/attr 先做 cardinality/PII 策略 |
| SDK 自健康 / `core` | `sdk_health` | emit/drop/rate, queue count/bytes, upload latency, internal errors, dropReason.*, dropPriority.*, dispatcherStage.* | self-monitor 默认开启，周期性 HIGH | 数据可信度主信号；当前运行时与已修正资产都使用 `core/sdk_health` |

`sdk_self_monitor/sdk_health_report` 由 helper `SdkHealthReport.toApmEvent()` 生成，但当前周期运行路径调用 `Apm.emit(module="core", name="sdk_health")`；服务端资产已经按真实运行路径修正，helper 不得重新进入生产查询。

## Metric 映射

字段注册表按 `(schema_version,module,name,field)` 版本化，至少包含：

- scalar type、单位、可空性、合法范围和 legacy 严格解析器；
- raw/aggregated form、temporality、monotonic、权重与允许的聚合；
- occurrence timestamp 规则和 late-arrival 规则；
- 允许的过滤/分组维度、归一化函数和 cardinality budget；
- PII/secret 分类、详情权限、保留期和删除范围；
- `PRESENT / UNAVAILABLE / NOT_APPLICABLE / INVALID / UNKNOWN` 状态语义；
- Log/Metric/Span 投影及 Dashboard/告警依赖。

V2 envelope 为 field 15 携带显式标量类型，Gateway 同时持久化值和 `field_types`，BigInteger/BigDecimal 保留精确文本。legacy Line/Protobuf 把 values 字符串化，worker 只能按注册表严格解析；失败时保留 LogRecord、标记 mapping error 并排除相应 Metric，不能猜测类型或丢掉整个事件。

计划中的确定性 Metric 示例：

| 真实事件字段 | Metric | 聚合与前置条件 |
| --- | --- | --- |
| `launch/* .launchDurationMs` | `android.apm.launch.duration` | histogram ms；按 launch type/version，字段存在时 |
| `network/network_* .durationMs` | `android.apm.network.duration` | histogram ms；URL 先模板化，raw/aggregate 不混算 |
| `fps/fps_stats .fps` | `android.apm.render.fps` | gauge；按 windowDurationMs 加权，必须有真实窗口 |
| `gc_monitor/memory_churn .gcTimeRatio` | `android.apm.gc.time_ratio` | gauge 1；`gcCountersAvailable=true` |
| `core/sdk_health .dropRate` | `android.apm.sdk.drop_ratio` | gauge 1；同时展示 emitCount/dropCount |
| `core/sdk_health .queueSize/.queueBytes` | `android.apm.sdk.queue.events/bytes` | gauge；不可把缺失当零 |

## 时间、事故与迟到语义

- 现场 `java_crash`、`anr_detected` 等使用事件 timestamp 作为发生时间。
- `app_exit` 在下一次启动采集，ApmEvent timestamp 是采集时刻；规范化发生时间必须优先取 `fields.exitTimestamp`，并保留 collection/received 两个时间。
- `tombstone_crash` 当前没有可靠原始崩溃时间，标记 `timestamp_quality=COLLECTION_TIME`，不能悄悄放进精确版本发布窗口。
- alert 使用发生时间并设置允许迟到窗口；Collector/worker SLO 使用 received/observed 时间。两类时间不能混在同一延迟指标。
- `java_crash` 与后续 `app_exit:CRASH`、`anr_detected` 与 `app_exit:ANR` 可能描述同一事故。没有客户端 incident ID 前，主指标只使用现场事件，退出证据单列；任何启发式关联都必须显示置信度且不能销毁原始记录。

## 分母与“零”的定义

| 指标 | 定义 | 当前可用性 |
| --- | --- | --- |
| 事件数 | 去重后的 exact event set 数量 | 可用；必须按真实 name/field 过滤 |
| 受影响安装数 | distinct tenant-HMAC installation with incident | 本地 Query/BFF 已实现；只纳入 occurrence/HMAC 合格行，缺失时返回 `UNAVAILABLE/DEGRADED`，生产窗口与 coverage 门槛未验收 |
| 活跃安装数 | 窗口内有合格 telemetry 的 distinct HMAC installation；按版本分组时还要求 occurrence-bound release | 本地 Query/BFF 已实现并返回 identity coverage；真实生产活跃口径、SDK drop 门槛和跨升级窗口未验收 |
| 受影响安装比率 | affected / active，满足最小分母与 coverage | 本地 Query/BFF 已实现 full-coverage 且分母大于 0 时的比率；生产最小样本/阈值仍待冻结，不是用户率或 session-free rate |
| Crash-free/ANR-free sessions | 1 - affected sessions / eligible sessions | 不可计算；客户端无统一 session 契约 |

只有“查询成功、事件族可观测、身份覆盖合格、分母大于 0”时结果 0 才是真零。无样本、字段缺失、模块未接线、被采样/聚合、链路延迟和查询失败分别显示为 `NO_DATA`、`UNAVAILABLE`、`UNKNOWN_COVERAGE`、`DEGRADED`、`LATE`、`ERROR`。

## Trace 映射

只有事件携带有效 trace/span ID、明确开始/结束语义和受控 operation name 时才生成 Span。当前手动 `ApmSpan` 在结束时发送 `traceId/spanId/parentSpanId/duration_ms/status`，但字段名、ID 格式、发生时间和动态属性尚未形成跨仓库版本化契约；网络、WebView、慢方法的孤立 completion 第一阶段均保持 LogRecord/Metric。待 SDK 冻结 W3C trace context 和 span envelope 后再启用原生 Trace，避免虚假 parent、duration 和 service graph。

## SigNoz 版本与部署

- 应用版本锁：SigNoz `v0.133.0`。
- 官方安装配置锁：Foundry `v0.2.13`。
- 安装资产从 `SigNoz/foundry` 官方 release 获取并校验 checksum；本仓库只保存覆盖层、环境变量模板和验收脚本。
- 第一阶段单租户/内部 staging 可直接使用 SigNoz UI；共享多租户生产必须经过受控 Query/BFF 或等价强制隔离，不能依赖用户手填 tenant filter。
- 升级先在 staging 回放固定 OTLP fixture，验证字段、数据状态、查询、Dashboard、告警和保留，再滚动生产。

## 失败语义

OTLP `2xx` 才视为成功；`429/502/503/504` 和连接错误可重试，明确的 schema/认证 4xx 进入 dead letter 并告警。响应丢失可能导致 OTLP 重发，稳定 eventId 必须随 LogRecord 发出，Dashboard/派生作业按该 ID 去重。映射失败不能污染原始 durable 事实：保留 raw、记录安全错误摘要和质量状态，再按字段/投影粒度降级。
