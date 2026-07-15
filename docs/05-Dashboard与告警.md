# Dashboard 与告警设计

## 交付方式

Dashboard 和告警以版本化资产保存在仓库，通过 SigNoz API/导入脚本发布到 staging，再推广生产。禁止只在 UI 手工创建而不导出。每个面板必须注明事件来源、过滤维度、单位、时间窗口和空数据含义。

## Dashboard 集合

### 1. Android 健康总览

- 活跃 installation/app version、事件吞吐、Gateway 拒绝率。
- Crash-free / ANR-free sessions（session 字段上线前明确显示不可计算）。
- Crash、ANR、启动 P50/P90/P99、慢帧/冻帧、网络错误率。
- inbox 积压、最老年龄、OTLP 成功率、dead letter。

### 2. Crash / ANR

- 按 app/environment/version/device/OS 的趋势与版本对比。
- 指纹 Top N、首次/最近发生、受影响 installation/session。
- 符号化状态、mapping/build-id 缺失、原始与符号化堆栈链接。

### 3. 启动与渲染

- cold/warm/hot startup 分布、版本回归。
- Frame duration、slow/frozen frames、页面/scene 维度。
- 视图树深度/数量仅在字段存在时展示，不推断 GPU overdraw。

### 4. 网络、SQLite 与 IO

- endpoint 模板维度的 duration、错误率、status family、上下行字节。
- 慢 SQL/查询计划 finding、数据库操作分布。
- Java/native IO、重复小 IO、路径类别；路径必须经过脱敏/归类。

### 5. 资源与电量

- Java/native heap、GC、线程、CPU、WakeLock/GPS/Alarm 回调事件。
- 缺失 ART counter 的窗口按 unavailable 处理，不填零。

### 6. SDK 自健康

- emit/drop/drop rate、dispatcher queue、上传延迟、内部错误。
- 诊断 writer drop/failure、客户端 outbox 积压（字段可用后）。
- 按 SDK/app version 对比，识别 SDK 自身回归。

## 初始告警规则

| 告警 | 触发 | 恢复/抑制 |
| --- | --- | --- |
| Gateway 5xx | 5 分钟错误率 > 1% 且请求数 > 100 | 连续 10 分钟 < 0.2% |
| Inbox backlog | 最老 pending > 5 分钟 | < 1 分钟 |
| OTLP export | 10 分钟成功率 < 99% | 15 分钟 > 99.9% |
| Dead letter | 5 分钟新增 > 0 | 人工确认；同根因聚合 |
| Crash regression | 新版本 crash rate 相对基线显著升高且样本达门槛 | 回落并保持窗口 |
| ANR regression | 同上 | 同上 |
| SDK drop rate | P95 > 1% 且活跃实例达门槛 | 30 分钟 < 0.2% |
| Missing symbols | production crash 未符号化 > 15 分钟 | 对应制品上传并补偿完成 |

发布前必须用合成数据验证触发、通知、静默、抑制、恢复和通知重试。低样本版本不得用百分比制造噪声；每个产品告警都有最小事件/installation 门槛和基线窗口。
