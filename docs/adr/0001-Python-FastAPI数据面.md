# ADR-0001：首版数据面采用 Python 3.11 + FastAPI

- 状态：Accepted
- 日期：2026-07-16

## 决策

Gateway、OTLP export worker 和控制面首版统一采用 Python 3.11、FastAPI/Pydantic，并使用异步数据库/HTTP 客户端。通过无状态 Gateway 横向扩展，CPU 较重的符号化任务使用独立 worker/process。

## 理由

首要风险是协议、幂等、安全和映射正确性，而不是语言极限吞吐。Python 生态可快速建立属性测试、Protobuf/OTLP、PostgreSQL 和 API 契约；本机也已有 `uv` 管理的 Python 3.11。单一语言减少首版运维面。

## 后果

必须有 body/batch 并发限制、异步 I/O、连接池预算和实测容量。若在目标硬件上经过剖析与数据库优化后仍无法满足 `docs/00-总体规划.md` 的 SLO/吞吐预算，可把纯 ingest parser/API 改写为 Go/Rust，但 PostgreSQL/OTLP 契约和测试 fixture 保持不变。不能凭主观性能印象提前分裂技术栈。
