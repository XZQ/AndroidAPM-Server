# SDK ↔ Collector 协议

## v1 接口

```http
POST /v1/events
Authorization: Bearer <ingest-key>
Content-Type: text/plain; charset=utf-8 | application/x-protobuf
Content-Encoding: gzip                         # 可选
X-APM-Schema-Version: 1
X-APM-App-Id: com.example.app
X-APM-Environment: production
X-APM-SDK-Version: 0.1.0
X-APM-App-Version: 1.2.3                       # 建议
X-APM-App-Build: 123                           # 建议
X-APM-Version-Code: 42                         # 兼容字段；当前仍是请求级声明
X-APM-Variant: release                         # 兼容字段；当前仍是请求级声明
```

tenant 不接受客户端直接指定，而是由 Bearer 凭据解析。凭据允许的 app/environment 与请求头必须匹配。客户端已有每请求调用的 `HttpHeaderProvider`，可以在不重建 uploader 的情况下刷新短期 Token，provider 失败时 durable rows 保持 pending 且不会复用旧 Token。当前缺口是生产 Token 签发/撤销/刷新服务及真实部署联调，不是 SDK Header 扩展点。

`eventId` 的内容身份同时包含首次接收的 app/environment/build envelope；同 tenant/eventId 若随后携带不同 build 身份会返回 `409 event_id_conflict`，不能被当作普通重放。Java Crash 的 `fields.stackTrace` 与 Native Crash 的 `fields.backtrace/abi/buildId` 必须来自事件产生时的构建，不能在应用升级后用当前版本猜测旧 outbox 事件身份。

## 请求限制

| 项目 | v1 默认 |
| --- | --- |
| 压缩体 | 1 MiB |
| 解压体 | 8 MiB |
| 批次事件数 | 1–1,000；SDK 当前通常 ≤ 32 |
| 单事件规范 JSON | 256 KiB |
| eventId | 1–128 个 ASCII 安全字符 |
| map 字段数 | 每个 map 256 |
| key/value | 128/8,192 UTF-8 字节 |
| 时间偏移 | 未来最多 10 分钟；过旧数据允许但标记 |

反向代理和应用层都实施限制。Gzip 使用流式有界解压；压缩比、CRC、尾部垃圾和多成员输入均需测试，不能先无界解压到内存。

## Line Protocol 兼容格式

每行一个事件，当前 SDK 发送以下 `key=value` 段并以 `|` 分隔：

```text
ts=...|eventId=...|module=...|name=...|kind=...|severity=...|priority=...|process=...|thread=...|scene=...|foreground=...|fields=k=v,...|context=k=v,...|extras=k=v,...
```

SDK 会把 `|` 替换为 `/`、`,` 替换为 `;`、换行替换为空格。嵌套 map 的 key/value 仍以 `=` 连接，因此原格式无法无歧义恢复包含 `=` 的内容。v1 parser 按第一个 `=` 分割每段、map 项按第一个 `=` 分割，并保留兼容后的值；生产优先使用 Protobuf。未来 Line v2 必须采用明确 envelope（JSON/Protobuf），不能静默改变 v1 解释。

必填段：`ts,eventId,module,name,kind,severity,priority,process,thread`。`scene,foreground,fields,context,extras` 是可选段；map 为空时当前 SDK 不输出该段。枚举大小写敏感。未知顶层段进入 raw 扩展区但不索引。

## Protobuf 兼容格式

`application/x-protobuf` 不是单个 Protobuf batch message，而是重复帧：

```text
[4-byte unsigned big-endian length][ApmEventMessage bytes]...
```

消息字段冻结为：

| Field | Type | Name |
| --- | --- | --- |
| 1 | int64 | timestamp |
| 2 | string | module |
| 3 | string | name |
| 4 | string | kind |
| 5 | string | severity |
| 6 | string | process_name |
| 7 | string | thread_name |
| 8 | string | scene |
| 9 | bool | foreground |
| 10 | map<string,string> | fields |
| 11 | map<string,string> | global_context |
| 12 | map<string,string> | extras |
| 13 | string | priority |
| 14 | string | event_id |

零长度帧、截断长度、超限帧、尾部不足和无 eventId 的消息均拒绝整批。未知 Protobuf 字段按规范忽略，原始请求哈希保留用于审计。

## Protobuf V2 envelope

V2 是显式的新契约，不会把 legacy `application/x-protobuf` 静默改义。请求必须同时满足：

```http
Content-Type: application/x-protobuf; message=ApmBatchEnvelope; version=2
X-Apm-Schema-Version: 2
X-Apm-Sdk-Version: 0.1.0
X-Apm-Batch-Id: b2-<32 lowercase hex>
X-Apm-Event-Count: <complete event count>
```

body 为 `ApmBatchEnvelope`，包含 schema/SDK 版本、按有序 eventId 计算的稳定 batch ID、发送时间、固定 resource 和完整事件列表。resource 的 `service_name/service_version/deployment_environment/installation_id` 必须非空且有界；app/environment/SDK/app-version 请求头与 body 必须一致。batch ID 使用 schema 字节和每个 UTF-8 eventId 的 4-byte big-endian 长度前缀计算 SHA-256，保留前 16 字节并加 `b2-` 前缀。

V2 事件只允许字段 15 `typed_fields`，拒绝同时使用 legacy 字段 10。支持 `NULL/STRING/BOOLEAN/BYTE/SHORT/INT/LONG/FLOAT/DOUBLE/CHAR/BIG_INTEGER/BIG_DECIMAL`；整数使用规范十进制，有限浮点存数值，Kotlin 规范 `NaN/Infinity/-Infinity` 因 JSON/JSONB 限制而连同类型判别保留为精确文本，任意精度数值最多 4,096 字符并以精确文本持久化。任一 envelope、resource、header 或事件不一致都拒绝整批且不写 inbox。

### V2 resource 与身份质量

当前 V2 的 `ApmResourceContext` 是**批次级上传资源**：`HttpApmUploader` 在 drain durable outbox 时把同一个 `service_name/service_version/deployment_environment/installation_id` 写入整批 envelope，durable `ApmEvent` 本身没有保存这组 resource。应用升级后，旧版本遗留在 outbox 的事件可能被新进程使用新版本 resource 上传。因此：

- tenant/app/environment 仍以服务端认证 scope 为准；resource/header 只能用于一致性校验，不能提升为认证身份。
- 当前 V2 `service_version` 只能标为 `BATCH_DECLARED`，不能单独证明事件发生版本；legacy 版本/build header 只能标为 `REQUEST_DECLARED`。
- 服务端把 V2 release 标记为 `BATCH_DECLARED`；installation 在 durable insert 前转为 tenant-scoped HMAC/key version，并从 `payload_json.unknown` 删除已知明文。该假名可以支持兼容统计，但不能把 V2 release 升级为发生时事实。
- 受控 E2E fixture 可以在“事件产生到上传期间没有升级、resource 固定”的前提下验证 wire 和查询原型；生产版本回归、精确 Java/Native 符号化不能依赖这个测试前提。

## Protobuf V3 envelope

V3 已冻结为显式 occurrence-bound 契约，对应 Android `SerializationFormat.PROTOBUF_ENVELOPE_V3`：

```http
Content-Type: application/x-protobuf; message=ApmBatchEnvelope; version=3
X-Apm-Schema-Version: 3
X-Apm-Sdk-Version: 0.1.0
X-Apm-Batch-Id: b3-<32 lowercase hex>
X-Apm-Event-Count: <complete event count>
```

envelope 头和 resource 结构沿用 V2，但 batch ID 以 schema 字节 `3` 参与哈希并使用 `b3-` 前缀。每个 event 必须使用 field 15 `typed_fields`，必须设置 field 16 `occurrence`，不得写 legacy field 10。客户端在事件进入 dispatcher/IPC/SQLite durable handoff 前冻结以下快照；服务端缺少或校验失败时整批返回 `422`：

| 每事件快照 | 用途 | 缺失处理 |
| --- | --- | --- |
| app version name、canonical decimal versionCode、appBuild、variant | 版本归因、Java mapping 精确匹配 | V3 缺失即整批拒绝；legacy/V2 仍按低质量兼容接收 |
| installation anonymous ID | 活跃/受影响安装；服务端入库前 HMAC | V3 缺失即整批拒绝；持久层只保存 HMAC/key version |
| ABI、module build-id、module name、module-relative PC、可选 load bias | 精确选择每 ABI 制品并符号化 | frame 可为空；一旦提供则每项非空、有界且地址非负 |

occurrence 属于 event 内容冲突比较的一部分；同 tenant/eventId 改变 occurrence 或 payload 返回 `409 event_id_conflict`，相同重放继续成功 ACK。V3 release/installation 标记为 `OCCURRENCE_BOUND`；legacy/V2 继续兼容接收但默认排除在需要 occurrence-bound release 的指标之外。决策背景见 `docs/adr/0006-发生时身份与installation假名化.md`。

V2 resource installation 与 V3 occurrence installation 在 wire 上都必须是宿主生成的匿名值。Gateway 用版本化 secret、固定 domain 和 authenticated tenant 计算 HMAC 后删除明文再持久化；不得放 user ID、广告 ID、IMEI、手机号、Token 或其他直接身份。具体存储、轮换和读取权限见 `docs/03-安全与租户.md`。

## 整批 ACK

成功响应只在事务提交后产生：

```json
{
  "requestId": "01J...",
  "status": "accepted",
  "received": 32,
  "inserted": 30,
  "duplicates": 2
}
```

重复事件是成功，不重新进入 inbox。legacy 返回体仅供诊断并按 2xx 判断成功。V2/V3 只有在事务提交后返回 2xx，并且以下响应头精确匹配请求，客户端才删除该物理批次：

```http
X-Apm-Schema-Version: <2 or 3, exactly matching the request>
X-Apm-Batch-Id: <request batch ID>
X-Apm-Event-Count: <request event count>
```

任何一条非法事件使整批返回非 2xx，数据库不插入其中任何事件。2xx 丢失或 ACK 头不匹配时客户端会安全重传，由 `(tenant_id,event_id)` 唯一约束去重。

## 错误语义

| HTTP | code | SDK 行为 |
| --- | --- | --- |
| 400 | `invalid_request` | 请求级格式错误；修正配置/客户端 |
| 401 | `invalid_credential` | 不重试风暴；等待凭据刷新 |
| 403 | `scope_mismatch` | app/environment 越权 |
| 413 | `payload_too_large` | 缩小批次/字段 |
| 415 | `unsupported_media_type` | 更换 serializer |
| 422 | `invalid_event` | 整批含非法事件；返回有限定位 |
| 429 | `quota_exceeded` | 按 `Retry-After` 重试 |
| 503 | `temporarily_unavailable` | 按有界 `Retry-After` 重试 |
| 500 | `internal_error` | 可重试；服务端不得泄露堆栈 |

统一错误体包含 `requestId,code,message,retryable`，可选 `eventIndex`，不回显完整敏感事件。`Retry-After` 以秒为主，并限制在客户端可接受的 60 秒内。

## 版本演进

- URL 主版本决定不兼容变更；header schema version 决定 envelope/字段规则。
- 新增可选字段允许前向兼容；删除/改义/类型变化必须升级版本。
- item-level ACK 只能在 SDK 与服务端共同支持的新版本中引入。
- 每个版本至少保留一个客户端发布周期并统计使用量后下线。
- V2 与 V3 使用独立 media type、schema header、batch ID prefix 和客户端枚举；不得在同一版本下按服务端开关静默改变 occurrence 语义。
