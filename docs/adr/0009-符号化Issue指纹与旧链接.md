# ADR 0009：符号化 Issue 指纹与旧链接

- 日期：2026-09-12
- 状态：Accepted

## 决策

`raw_incident_fingerprint` 保存接收时的原始指纹，`incident_fingerprint` 是查询、详情、事件筛选和后续 OTLP 导出的当前 Issue 指纹。Java 符号化成功后，在同一个 owner/lease/expiry 保护事务中切换当前指纹；过期/失败任务不能改写它。原始 payload、payload hash、去重身份和 raw 指纹不变，重放仍按原始证据确认。

迁移 `20260912_0007` 先保存 raw 别名，再应用已经成功的 Java symbol job 指纹，覆盖历史完成任务和已按策略清理 raw 的任务。Native 不在当前 Java/ANR Issue 产品目录内，不由此次迁移扩展事件口径。此前已经导出到外部 SigNoz 的记录不会被自动回写；外部历史重建需要独立受控任务。

旧链接只在凭据固定 tenant/app/environment 和请求窗口内解析。别名唯一时返回规范指纹并按它聚合所有相关事件；别名对应多个 Issue（包括部分尚未还原）时返回 `409 ambiguous_issue_fingerprint`，由用户在当前 Issue 列表选择。分页 cursor 绑定解析后的规范指纹，身份变化使旧 cursor 失效，不能扩大范围。

## 后果与验证

Query 不需要在每次聚合时联表扫 symbol jobs。新增 alias/time 索引支持旧链接解析；迁移需要数据库变更窗口。downgrade 恢复 raw 指纹，不改写符号结果。回归覆盖同一符号指纹聚合、原始链接、scope/window、歧义、重放和旧 owner 拒绝写回；本地 SQLite 不代替 PostgreSQL 锁和生产迁移验证。
