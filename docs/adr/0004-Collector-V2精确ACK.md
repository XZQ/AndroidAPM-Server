# ADR-0004：Collector V2 envelope 与精确整批 ACK

- 状态：Accepted
- 日期：2026-07-31

## 决策

保留 legacy Line Protocol 和 4-byte length-prefixed Protobuf 的 schema 1 语义，并以独立 media type `application/x-protobuf; message=ApmBatchEnvelope; version=2` 引入 schema 2。V2 固定包含 SDK/resource、稳定 batch ID、有序事件列表和字段 15 typed scalar；请求头必须与 body 一致。只有 inbox 事务提交后，Gateway 才返回精确匹配 schema、batch ID 和 event count 的响应头。

## 理由

legacy payload 没有批次身份、标准 resource 或无歧义字段类型，普通 2xx 也不能证明 Collector 确认了客户端实际发送的完整物理批次。静默修改 legacy 解码会破坏已发布客户端。显式 V2 让客户端和 Collector 在解析前协商契约，并让响应丢失或错误代理响应安全退化为重传；最终由 `(tenant_id,event_id)` 唯一约束去重。

## 后果

客户端只有收到三项精确 ACK 才删除 V2 批次，缺失或不匹配的 ACK 即使是 2xx 也视为失败。服务端必须原子拒绝 header/body/resource/type 不一致，保留 BigInteger/BigDecimal 的精确文本和类型判别，并在提交前不得发 ACK。旧客户端继续使用 schema 1；未来的破坏性 envelope 或 ACK 变化必须升级版本，不能重解释 V2。
