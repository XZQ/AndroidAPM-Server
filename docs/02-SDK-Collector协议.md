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
```

tenant 不接受客户端直接指定，而是由 Bearer 凭据解析。凭据允许的 app/environment 与请求头必须匹配。过渡期可用静态 Header 接入现有 SDK；动态短期 Token 需要客户端增加 `HeaderProvider/AuthProvider` 后启用。

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

重复事件是成功，不重新进入 inbox。legacy 返回体仅供诊断并按 2xx 判断成功。V2 只有在事务提交后返回 2xx，并且以下响应头精确匹配请求，客户端才删除该物理批次：

```http
X-Apm-Schema-Version: 2
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
