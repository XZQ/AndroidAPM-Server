# ADR-0002：PostgreSQL 作为可靠 Inbox 与幂等边界

- 状态：Accepted
- 日期：2026-07-16

## 决策

生产使用 PostgreSQL 保存 ACK 前的全部规范化事件，并用 `(tenant_id,event_id)` 唯一约束实现最终幂等。批次在单事务中插入，worker 使用 owner/lease/expiry 和 `FOR UPDATE SKIP LOCKED` 并发认领。

## 理由

SDK 是至少一次投递且只有整批 ACK。直接同步转发 SigNoz 会把下游延迟/故障暴露给移动端，也难以判断响应丢失后的状态；内存队列在进程故障时会造成已经 ACK 的数据丢失。PostgreSQL 能同时提供事务 ACK、唯一约束、租约和审计。

## 后果

Gateway 可用性依赖 PostgreSQL，必须部署 HA/PITR、控制连接和监控 WAL/容量。ACK 只承诺进入本产品 durable inbox，不承诺已经在 SigNoz 可见；可见延迟由单独 SLO 约束。
