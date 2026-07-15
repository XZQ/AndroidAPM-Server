# ADR-0003：复用但不 Fork SigNoz

- 状态：Accepted
- 日期：2026-07-16

## 决策

SigNoz 作为标准 OTLP 下游和观测 UI/查询/告警平台，通过官方 Foundry 安装并锁定已验证版本。本仓库不复制、修改或 fork SigNoz 核心代码；Android 专属功能通过 Gateway、标准属性、Dashboard/告警资产和受控 API 集成。

## 理由

自研观测存储、查询、Dashboard 和告警成本高且偏离 Android SDK 的差异化价值。直接 fork 会持续承担上游合并、安全修复和部署迁移。OTLP 边界使后端可替换，并保留升级路径。

## 后果

需要维护 SigNoz/Foundry 兼容矩阵和 staging 升级测试。Community 多租户能力不能被假定满足互不信任客户隔离；第一阶段使用独立实例/信任域或受控查询层。
