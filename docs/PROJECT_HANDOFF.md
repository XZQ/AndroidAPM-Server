# AndroidAPM-Server 项目交接

> 当前验证基线：2026-07-16，分支 `codex/server-foundation`。本文区分“本机已验证”和“需要外部环境验证”，不能把后者改写成已完成。

## 已实现

- Python 3.11 + FastAPI/uv 工程、严格配置、结构化日志、Prometheus 端点。
- `POST /v1/events`：Bearer ingest key、tenant/app/environment scope、schema/header/content 校验。
- 应用层有界 body 读取、单成员 Gzip 有界解压、Line Protocol、4-byte big-endian length-prefixed Protobuf。
- 数据库固定窗口 request/event quota；PostgreSQL `(tenant_id,event_id)` 最终唯一约束。
- 同批和历史重复 ACK；同 eventId 不同内容显式 `409`；事务 commit 后才生成整批 2xx ACK。
- Inbox worker 的 owner/lease/expiry、过期重领、owner-only 完成、退避、`Retry-After` 和 dead letter。
- Android event 到 OTLP Logs 的 resource/severity/context/eventId 映射；兼容 string fields 只按源代码审核白名单恢复数值/布尔类型。
- SigNoz v2 Perses Dashboard、v2 alert templates 和 service-account API key 幂等同步器。
- `GET /v1/config`：Ed25519 签名、递增 revision、过期、灰度稳定分桶、ETag/304；CLI 创建 key/publish config 并记录审计。
- Alembic 初始 schema、非 root 只读容器、Gateway/worker/PostgreSQL Compose 和官方 SigNoz/Foundry 版本边界。

## 2026-07-16 本机验证

```powershell
uv sync --all-groups --frozen
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/verify_docs.py
```

以上命令通过；本地测试基线为 44 tests，0 failures，核心包分支覆盖率 71.76%（生成的 Protobuf 代码排除）。另使用临时 SQLite 数据库执行了 `alembic upgrade head -> downgrade base -> upgrade head`，随后 `androidapm-admin create-ingest-key` 成功创建 tenant、哈希凭据和审计记录。这是迁移/CLI 兼容冒烟，不替代 PostgreSQL 事务与并发验证。

## 未完成或尚未现场验证

- 本机没有 Docker/Compose，所以镜像 build、Compose health、官方 Foundry 安装和真实 SigNoz OTLP/Dashboard/告警尚未执行。
- GitHub CI 已定义 PostgreSQL 16.9 migration round-trip、并发 `ON CONFLICT` 唯一事实和 `FOR UPDATE SKIP LOCKED` 双 owner 测试；在 workflow 首次真实运行前仍属于待验证资产，不能写成已通过。
- Dashboard/alert JSON 已按 SigNoz `v0.133.0` 当前官方 v2 API 源码编写并做离线测试，但还没有由真实 SigNoz 校验查询表达式和渲染结果。
- Native/Java 符号制品上传、对象存储、retrace/llvm-symbolizer worker 尚未实施。
- OTLP Metrics 独立信号、Trace context、查询代理、生产 RLS/KMS/HSM、备份/PITR、TLS/WAF、压测/72h soak 尚未验收。
- Android SDK 还需实现动态 AuthProvider、配置拉取/验签/缓存/过期，以及补齐 session/installation/build-id 等 envelope 字段。

## 下一步顺序

1. 在有 Docker 的 Linux/Windows 环境跑 PostgreSQL integration、Compose 和真实 SigNoz staging E2E。
2. 修正真实 SigNoz 返回的任何 dashboard/rule schema/query 差异并保存截图/请求证据。
3. 实施 CI service account、符号制品索引/对象存储和 Java/Native symbolization worker。
4. 补生产 RLS、retention/prune、备份恢复、故障注入、容量/性能和安全门禁。
