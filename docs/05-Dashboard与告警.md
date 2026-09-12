# 真实场景、查询、Dashboard 与告警

> 场景基线：2026-08-28。查询口径必须以 `docs/04-OTLP映射与SigNoz.md` 的真实事件目录为准；本文不是“把 15 个模块各画一页”的面板清单。

## 目标用户与入口

主要入口按任务而不是按采集模块组织：

1. **发布健康**：Android 开发/发布负责人比较新旧版本，决定继续灰度、暂停或回滚。
2. **问题详情**：值班开发从告警/趋势进入指纹、安装、原始事件和符号化证据。
3. **数据可信度**：SDK/质量负责人判断无数据、缺字段、采样、端上丢弃和服务端积压。
4. **平台运维**：SRE 查看 Gateway、Inbox、OTLP、dead letter、symbolizer 和查询资产健康。

密钥、租户、配额、制品和配置发布属于管理流，不混入普通诊断首页。当前首个 Query/BFF 已强制 tenant/app/environment scope；单租户/内部 staging 仍可直接使用 SigNoz 验证资产，共享多租户生产只能通过受控 BFF/托管 UI 或物理隔离，不能把 tenant filter 交给最终用户自行填写。

交互面 desktop-first：发布健康、趋势和详情在桌面并列；窄屏只保留告警摘要、关键状态和受控深链，不要求在手机上完成基线配置、长堆栈对比或管理操作。任何页面都不能因为响应式空间不足而隐藏数据质量/权限状态。

## 产品原则与首发边界

- 主要用户是负责 Android 发布质量的开发/发布负责人和值班开发；SDK 质量负责人、SRE 和平台管理员是次级角色。
- 承诺结果不是“展示更多采集指标”，而是给出带来源、样本、覆盖率和证据链的发布判断，并能解释为什么暂时不能判断。
- 三个最高频任务依次是：比较新旧版本 Crash/ANR 健康、从指纹下钻到可审计证据、定位无数据或质量降级发生在哪一层。
- 产品形态是 desktop-first 的诊断 Dashboard + 发布决策工作流。窄屏浏览器只承担告警分诊、关键状态确认和深链跳转；当前不规划独立移动 App。
- 第一可用版本不自动操作发布系统，不把共享 SigNoz UI 当多租户产品界面，不提供未经 occurrence/session/coverage 支撑的指标，也不把密钥、租户、制品和配置管理混入普通诊断导航。
- 当前已实现 fixed-scope Query/BFF、同源 React/TypeScript 多路由 Web 垂直切片和离线 SigNoz 资产。`/apps/:appId/overview`、Issues 列表/详情、investigator 事件探索/详情、版本发布和数据质量均为独立可恢复路由；总览和 Issue 详情有 occurrence-time 趋势，Issue 详情有真实版本/场景聚合，设备型号和 Android 版本因标准字段缺失明确为 `UNKNOWN_COVERAGE`。性能、告警、设置目前只展示真实阻塞状态；完整告警深链、平台运维、性能域、SSO 与真实云端/SigNoz 验收仍按下表推进。

## 页面清单与导航层级

| 页面 | 用户目的 | 首要操作 | 次要操作 | 入口与返回路径 | 导航层级 | 首发必需 |
| --- | --- | --- | --- | --- | --- | --- |
| 发布健康 | 判断新版本是否继续、暂停或回滚 | 选择新版本/基线并记录人工决策 | 调整窗口、查看质量原因、进入指纹 | 默认首页或发布深链；返回时保留筛选与滚动 | 一级：诊断 | 是 |
| 问题指纹 | 找到造成版本差异的具体问题族 | 打开一个指纹 | 排序、按事件族筛选、查看首次/最近 | 发布健康/告警进入；返回发布健康 | 二级 | 是 |
| 事故详情 | 用原始与符号化证据完成诊断 | 查看事件元数据与符号化证据 | investigator 按 purpose/reason 读取 L2 raw、核对 `app_exit` | 指纹/告警进入；返回原列表位置 | 二级 | 是 |
| 数据可信度 | 解释 `NO_DATA/UNAVAILABLE/DEGRADED/LATE` | 查看身份、SDK drop 与管线状态 | 跳到接入说明或运维 runbook | 发布健康质量条或一级入口；返回原上下文 | 一级：质量 | 是 |
| 平台运维 | 判断 Collector 到 SigNoz 的数据面是否健康 | 定位 backlog/dead letter/失败阶段 | 打开 runbook、按实例/错误码筛选 | 运维导航或管线告警；返回保留时间窗 | 一级：运维 | 内部首发 |
| 性能域分析 | 分析启动、帧、网络、内存等版本回归 | 比较受控分布/趋势 | 下钻 allow-listed 事件详情 | 诊断导航；返回保留域与筛选 | 一级：诊断 | 否，P1 |
| 管理控制台 | 管理凭据、租户、制品、配置与保留策略 | 执行一个受审计管理动作 | 查看历史与回滚状态 | 独立入口；不从普通诊断首页暴露 | 独立管理面 | 否 |

当前同源 Web 路由为：

- `/apps/:appId/overview`：发布健康、趋势、可信度和 Top Issues。
- `/apps/:appId/issues` 与 `/apps/:appId/issues/:fingerprint`：稳定指纹列表、发生趋势、版本/场景分布和权限受控样本。
- `/apps/:appId/explore` 与 `/apps/:appId/events/:eventId`：investigator-only L1 检索、事件详情和显式审计后的 L2 raw。
- `/apps/:appId/releases` 与 `/apps/:appId/quality`：人工发布决策和数据质量证据。
- `/apps/:appId/performance`、`alerts`、`settings`：能力边界页，不构成相应后端或云端能力已经完成的声明。

## 桌面与移动/窄屏编排

当前“移动端”仅指托管 Web 的窄屏分诊视图；业务状态和权限契约共用，但不复用同一整页布局。

| 页面 | 编排方式 | 移动/窄屏 | 桌面 | 共享内容与状态 |
| --- | --- | --- | --- | --- |
| 发布健康 | 共享业务逻辑、分开编排 | 质量条、关键结论、决策状态纵向排列；趋势和指纹进入二级页 | 固定筛选、质量条、指标、趋势和指纹同屏比较 | scope、窗口、指标 state、样本、coverage、人工决策 |
| 问题指纹 | 共享逻辑、响应式列表 | 单列列表；筛选折叠；详情全页打开 | 表格/列表与详情侧栏或分栏 | 排序、分页 cursor、筛选、选中项 |
| 事故详情 | 共享数据、分开编排 | 元数据、栈、符号状态分段；L2 raw 单独受控页面 | raw/symbolized 并列，关联证据置于侧栏 | 权限、审计状态、provenance、request ID |
| 数据可信度 | 共享编排、响应式尺寸 | 按链路阶段纵向展示，下一步动作紧邻原因 | coverage 矩阵与管线阶段并列 | 状态语义、来源、as-of、原因码 |
| 平台运维 | 平台特定能力 | 只读告警摘要和 runbook 深链 | 完整时序、队列、实例和 dead-letter 操作 | 时间窗、告警状态、来源 |
| 管理控制台 | 桌面专属 | 不提供高影响管理动作 | 独立管理布局，强确认与完整审计 | 身份、scope、审批/审计结果 |

## 数据可信契约

2026-09-12：趋势按版本/时间桶核对 occurrence 证据。无事件桶返回 null + `NO_DATA`，只有批次声明返回 null + `UNAVAILABLE`；有发生时证据且未观察到 Crash/ANR 才返回观测计数 0。发布桶新增 `newState/baselineState`，Issue 无样本桶也返回 null。Web 按缺口拆线，单点单独绘制，全空显示无可绘制数据；观测计数不代表完整用户群体无事故。总览可信度同时取当前版本、基线版本和 data-quality 的限制状态，样本不足不会被较宽松的 data-quality 覆盖。

| 数据族 | 来源与刷新 | 可见状态 | 缺失/失败处理 | 用户可见来源 |
| --- | --- | --- | --- | --- |
| 发布健康与指纹 | fixed-scope Query/BFF 从 durable inbox 按请求窗口计算；响应携带 `asOfMs` | `ZERO/NO_DATA/UNAVAILABLE/DEGRADED/LATE/ERROR` | 不复用旧数字伪装实时结果；缺 occurrence/HMAC 时隐藏结论但保留原因与已知计数 | `durable_inbox`、查询窗口、request ID |
| L1/L2 事故证据 | investigator-only Query/BFF；L2 审计 commit 后返回，`no-store` | present、metadata missing、permission denied、error | 不向列表/通知降级泄漏 raw；失败保留 event/request ID 供追踪 | eventId、occurrence/received time、访问审计 ID |
| 符号化 | durable symbol job + 精确 artifact/tool provenance | disabled、metadata missing、symbols missing、pending、symbolized、failed | raw stack 始终保留；不可用时不生成伪函数名 | artifact identity、tool/version、job state |
| SDK 与身份质量 | `core/sdk_health`、protocol/release/installation coverage | present、zero、unknown coverage、degraded、unavailable | 未接线、被丢弃和无样本分别解释；不得填零 | exact module/name、coverage、drop reason |
| 管线运维 | Gateway/Prometheus、PostgreSQL inbox/job、OTLP/SigNoz health | live、delayed、partial、error | 发生时间与接收时间分开；下游失败不抹掉 durable 事实 | 组件、实例、received-time 窗口 |
| 人工发布决策 | durable release decision + audit | continue、pause、rollback、none | 记录失败不触发外部发布动作；不做乐观成功 | actor、scope、reason、created-at |

## 最小 UI 契约

- 页面框架：桌面筛选与 scope 常驻首屏；窄屏保留安全区，禁止横向滚动承载核心结论。返回列表必须恢复筛选、cursor、选中项和滚动位置。
- 层级与密度：页面标题 > 发布结论/状态 > 指标值 > 样本/来源。密集表格仅用于桌面；窄屏改为语义分组的列表行，不堆叠同尺寸大卡片。
- 卡片与列表：只对需要并列比较的少量决策指标使用卡片；指纹、事件、审计和 job 使用可分页列表/表格。
- 文本：指纹、异常类名和版本允许两行；eventId/build-id 等机器标识中间省略并可复制；原始栈/SQL/URL 使用受控等宽区域，不把横向溢出扩散到页面。
- 数字与图表：数值必须同时展示 state、样本、分母/coverage 和单位；大数字使用分组但保留精确值。图表在窄屏移到二级页，低样本、迟到和缺口不能通过连线伪装连续数据。
- 状态与来源：颜色不是唯一编码；图标/文本明确 `UNAVAILABLE/DEGRADED/LATE/ERROR`，并显示 as-of、source、scope 和 request ID。
- 选择与高影响动作：无权限/未配置状态禁用动作并说明获得权限的路径；`pause/rollback` 在写入人工决策前确认 scope、版本和 reason，成功后展示不可变审计结果。服务端不因此自动执行发布。
- 主题与无障碍：支持深浅主题、键盘操作、可见焦点、语义标签和 200% 文本缩放；状态对比度满足 WCAG AA，图表提供等价文本/表格摘要。

## 交付方式

产品 Web 源码与锁文件位于 `web/`，生产构建由 FastAPI 同源提供；Dashboard 和告警以版本化 SigNoz 资产保存在仓库，通过 API/导入脚本发布到 staging，再推广生产。禁止只在 UI 手工创建而不导出。每个页面/面板必须注明事件来源、过滤维度、单位、时间窗口和空数据含义。

当前 API 面为：`GET /v1/query/release-health`、`/fingerprints`、`/issues/{fingerprint}`、`/data-quality`、investigator-only `/events` 与 `/events/{event_id}`、purpose-bound `POST /events/{event_id}/raw`，以及 `GET/POST /release-decisions`。Issue 详情是 L0 聚合，viewer 可读；凭据创建时固定 scope，窗口、扫描行数、page size 与 cursor filter 均有上限，游标使用专用 HMAC 防篡改。`investigator` 才能枚举事件、读取原始证据和记录人工 `continue/pause/rollback`；服务端不会自动执行发布动作。

每个查询还必须声明：

- exact `module/name/field` 事件集，raw/aggregated 是否允许；
- 发生时间、接收时间和允许迟到窗口；
- 分母、最小样本、identity coverage 和排除规则；
- `ZERO / NO_DATA / UNAVAILABLE / UNKNOWN_COVERAGE / DEGRADED / LATE / ERROR` 状态；
- PII/高基数字段是否只允许详情查看；
- tenant/app/environment scope 是否由服务端强制。

每个指标响应至少包含 `state,value,numerator,denominator,sample_count,coverage,as_of,source` 中适用字段；前端不从裸数字猜状态，也不在请求失败时沿用旧值伪装当前结果。详情响应按 L0-L2 字段级别裁剪，权限契约见 `docs/03-安全与租户.md`。

## 场景契约与优先级

| 优先级 | 用户问题 | 输入 | 必须给出的答案 | 关键防误判 |
| --- | --- | --- | --- | --- |
| P0 | 新版本是否引入 Crash/ANR 回归？ | `java_crash`, `anr_detected`, occurrence-bound release identity、HMAC installation、`core/sdk_health` | 事件数、受影响安装、活跃安装、比率、样本与质量、版本差异 | 当前 V2 batch version 仅供受控 fixture；不把普通 `app_exit` 算 Crash；无 session 不算 session-free |
| P0 | 这个问题是什么、影响谁？ | exact event、raw stack、symbol job、HMAC installation | 稳定指纹、首次/最近、版本、scene/process、原始/符号化栈 | `metadata_missing`/`symbols_missing` 显式；不覆盖 raw |
| P0 | 为什么这里没有数据？ | SDK health、protocol/identity coverage、Gateway/Inbox/OTLP health | 端上丢弃、链路积压、字段缺失、未接线/未知的分层判断 | 无事件不自动等于 0 或未接线 |
| P1 | 启动/帧/网络是否回归？ | launch/fps/network exact fields | P50/P90/P99、版本/scene/endpoint 对比、样本与阈值 | raw/aggregated 不混算；endpoint 先模板化 |
| P1 | 内存/GC/线程/IO 等哪里异常？ | 注册事件目录与 availability 字段 | finding 趋势、受控维度和原始详情 | API 不可用字段不填 0；手动接线显示 coverage unknown |
| P0 Ops | 数据面是否健康？ | Prometheus + inbox/job state + OTLP outcome | ACK/可见延迟、积压、失败率、租约和 dead letter | 用 received time 诊断管线，不与 event time 混用 |

## 首个垂直切片：发布 Crash/ANR 可信闭环

### 入口与默认筛选

- 强制选择 tenant/app/environment；默认最近 24 小时，与上一个稳定版本同星期/同时间窗对比。
- 新版本必须明确，基线版本由发布负责人选择或由发布元数据标记，不能自动把所有旧版本混成一个基线。
- 主事件集固定为 `crash/java_crash` 和 `anr/anr_detected`。`app_exit` 作为异常退出核对流单独展示；`native_crash/tombstone_crash` 在 build identity 完整前单列 raw-only。
- 安装比率只纳入 HMAC installation 合格的数据；按版本比较时还要求 occurrence-bound release identity。legacy、当前 V2 batch-declared version 或 identity 缺失仍可按允许口径计事件数，并显示排除量。
- 生产版本比较要求 `release_identity_quality=OCCURRENCE_BOUND`；V3 行满足该前提，V2 `service_version` 仍只标记 `BATCH_DECLARED`。窗口只有 V2/legacy 时响应显示 `UNAVAILABLE: OCCURRENCE_IDENTITY_NOT_PROVIDED`，不能用兼容声明填补。

### 指标定义

| 指标 | 定义 | 空值/门禁 |
| --- | --- | --- |
| Java Crash events | distinct `eventId` where module/name=`crash/java_crash` | 查询成功且事件族可观测时 0 才是真零 |
| ANR events | distinct `eventId` where module/name=`anr/anr_detected` | 同上 |
| affected installations | distinct tenant-HMAC installation with exact incident event | installation 缺失时不可计算，缺失行单列 |
| active installations | 窗口内至少一条合格 telemetry 的 distinct HMAC installation；按版本分组还要求 occurrence release | 需要展示 reporting coverage、SDK drop 和版本 identity quality |
| affected-installation ratio | affected / active | active 达最小门槛、coverage 合格才计算；不是用户率/session-free rate |
| incident fingerprint count | distinct server fingerprint of exact event set | 未符号化可用受限 raw fingerprint，并标明质量 |
| late ratio | 允许窗口外才到达或 occurrence/received 差超过阈值的事件占比 | `app_exit` 用 exitTimestamp；tombstone collection-time 单列 |

当前客户端无统一 telemetry session，任何 “Crash-free sessions / ANR-free sessions” 卡片必须显示 `UNAVAILABLE: SESSION_ID_NOT_PROVIDED`，不能用 installation 或 event 数冒充。

### 页面结构

```text
Release health header
  tenant / app / environment / new version / baseline / time window
  data-quality strip: protocol, version, installation, SDK drop, late data

Decision row
  Java Crash events | ANR events | affected installs | active installs | ratio
  each card carries sample size and state, never a bare percentage

Trend and comparison
  exact event trend by version + deployment marker
  top fingerprints with delta, first seen, last seen, affected installs

Evidence drawer
  raw event metadata and raw stack
  symbolization status/result/provenance
  related app_exit evidence shown separately
  request/event ID and received/occurrence timestamps
```

### 事故口径

- `java_crash`/`anr_detected` 是第一版主事实。
- `app_exit.reasonName=CRASH|CRASH_NATIVE|ANR` 是下一启动获得的系统证据，普通 `EXIT_SELF/USER_REQUESTED/...` 不属于 Crash。
- 当前没有可靠 incident ID；同安装、相近时间和相同类型的关联只能显示“可能相关”，不能删除记录或增加/减少主指标。
- alert、趋势和版本门禁对 eventId 去重；OTLP 响应丢失造成的重复不会形成第二个事实。

### 验收 fixture

固定 fixture 必须由真实 Android serializer/`HttpApmUploader` 生成，而不是服务端手写 Protobuf；套件同时覆盖 V3 occurrence、V2 和 legacy：

1. 稳定版本与新版本各有多组 V3 `core/sdk_health`，用于版本/安装分母；另有 V2 样本验证兼容 wire、HMAC 和 `BATCH_DECLARED` 排除规则。
2. 新版本包含两个 `java_crash`、一个 `anr_detected`、一个普通 `app_exit` 和一个 `app_exit:CRASH`。
3. 重放同一物理 batch、模拟 ACK 丢失和事件迟到。
4. 一组 legacy 事件缺 installation，一组 legacy 字符串字段，一组 symbol metadata 缺失；当前 V2 的必填 resource 不伪造“缺 installation 但仍被接收”的样本。
5. 预期：普通退出不进入 Crash、app_exit 异常不与现场事件相加、安装率排除 identity 缺失、原始事件仍可查、告警可触发和恢复。

另加一个跨升级 fixture：旧版本事件先写入 durable outbox，应用升级后再上传。预期旧事件仍归旧 occurrence release；当前 V2 版本必须标为 `BATCH_DECLARED` 并被生产版本比率排除，不能跟随新进程 resource 错归新版本。

### 首个切片验收门槛

- [ ] 真实 Android V3/V2/legacy fixture 能从发布健康进入指纹、事件元数据和受审计 raw；重放不增加事实数。
- [ ] `viewer` 只能完成 L0 比较，`investigator` 才能下钻和记录决策；越权、跨 scope 和篡改 cursor 都有明确错误且不泄漏存在性。
- [ ] 桌面端从筛选到详情再返回，筛选、分页、选中项和滚动位置保持；窄屏能完成告警分诊但不会暴露桌面专属管理动作。
- [ ] loading、部分加载、空、查询错误、离线、无权限、未配置、长栈、长中英文、大数字、200% 文本、窄屏和深浅主题均有可观察结果与下一步动作。
- [ ] `ZERO` 与缺失状态视觉和文本均不同；每个结论展示样本、分母/coverage、as-of 和 source，不使用模拟数字填补。
- [ ] `pause/rollback` 写入前确认 scope/version/reason，失败不展示成功；成功只形成可审计人工决策，不触发未授权的外部发布动作。
- [ ] 真实 SigNoz staging 的查询、Dashboard 导入、告警触发/抑制/恢复、通知重试以及 PostgreSQL/TLS 多租户读取隔离通过后，才可宣称生产切片完成。

## Dashboard 集合

### 1. 发布健康（P0）

- 首个垂直切片中的版本/基线、数据质量、Java Crash/ANR、受影响/活跃安装、指纹与下钻。
- 设备/OS 只有在标准 resource 契约上线且 coverage 可见后才开放筛选；当前宿主自定义 context 不能作为保证维度。
- Session 指标显示不可计算，不用 installation 替代。

### 2. 事故详情（P0）

- exact event、稳定 eventId、发生/接收时间、版本、HMAC installation、scene/process/thread。
- 指纹首次/最近、版本分布、受影响安装、raw stack 与 symbolized stack 并列。
- `disabled/metadata_missing/symbols_missing/symbolized/failed` 状态、artifact/tool provenance 和安全错误摘要。
- `app_exit` 相关证据单列；不自动合并为同一事故。

### 3. 数据可信度（P0）

- V2/legacy、version/installation/build identity coverage、迟到比例和字段 mapping error。
- `core/sdk_health` 的 emit/drop/dropRate、queueSize/queueBytes、上传延迟、internal error、dropReason/dropPriority、dispatcher stage latency。
- Gateway 拒绝、inbox 积压/最老年龄、OTLP 成功、dead letter、symbolizer 状态。
- 宿主 integration evidence 未上报前，显式接线模块只显示 `UNKNOWN_COVERAGE`。

### 4. 启动与渲染（P1）

- `cold_start/warm_start/hot_start` 的 `launchDurationMs` 分布与版本回归；phase 字段按 availability 展示。
- `fps_stats` 按真实 `windowDurationMs` 加权，展示 fps/jank/frozen/dropped；和 `render/frame_metrics` 分开。
- view count/depth 仅在 finding 存在时展示，不推断 GPU overdraw。

### 5. 网络、SQLite、IO、WebView、IPC（P1）

- endpoint template 的 duration/error/status family/size；raw URL 仅受限详情。
- SQL fingerprint/table/issue、IO path category/hook level、WebView page/resource template、Binder interface/method。
- 每组都展示 explicit integration coverage；SQL、path、URL、JS、error text 不作为默认 group-by。

### 6. 资源与电量（P1）

- Java/native heap、PSS、GC、thread finding、CPU/battery 与 WakeLock/GPS/Alarm callback finding。
- `gcCountersAvailable/allocationRateAvailable/reclaimRateAvailable=false` 的维度为 unavailable，不填零。
- Native Heap API 不提供的 peak/count 不构造伪值；Hprof 仅显示设备本地 metadata，不提供不存在的下载链接。

### 7. 平台运维（P0 Ops）

- Gateway request/5xx/429/503、ACK P95/P99、PostgreSQL 连接/WAL、inbox backlog/oldest、worker lease/retry/dead letter。
- OTLP 可见延迟、SigNoz ingestion/query health、symbolizer backlog/missing artifact/tool failure。
- received time 用于链路 SLO，event time 用于产品趋势；两者并列而不混算。

## 当前 SigNoz 资产审计

当前仓库有 `androidapm-overview` Dashboard，以及 Java Crash、ANR、SDK drop 三个 alert template，尚未形成上述完整 Dashboard 集合，也没有真实 SigNoz staging 证据。离线资产已修正为 exact runtime contract：

- SDK health 使用 `android.apm.module='core' AND android.apm.name='sdk_health'`。
- Java Crash 使用 `crash/java_crash`，ANR 使用 `anr/anr_detected`；普通 `app_exit` 不进入二者。
- 三个 alert 都没有 notification channel，metadata 明确 `production_ready=false`；阈值、app/environment/version、最小样本/基线与真实触发恢复尚未冻结。
- 资产没有 installation/session denominator、data-quality strip、告警恢复 fixture 或多租户读取隔离证明。

这些模板可以用于 API/schema 冒烟，但在真实 SigNoz 导入、运行时 fixture、样本/identity 门禁和触发/恢复测试通过前不得增加生产通知渠道。

## 初始告警规则

| 告警 | 触发 | 数据门禁 | 恢复/抑制 |
| --- | --- | --- | --- |
| Gateway 5xx | 5 分钟错误率 > 1% 且请求数 > 100 | 排除明确 4xx/配额拒绝；按实例与全局观察 | 连续 10 分钟 < 0.2% |
| Inbox backlog | 最老 pending > 5 分钟 | 数据库时间、lease 和 worker heartbeat 正常可比 | < 1 分钟并保持 10 分钟 |
| OTLP export | 10 分钟成功率 < 99% | 按 retryable/permanent 分组，响应不确定单列 | 15 分钟 > 99.9% |
| Dead letter | 5 分钟新增 > 0 | 同 error code/fingerprint 聚合，原始 payload 不进通知 | 人工确认根因；补偿后关闭 |
| Java Crash regression | 新版本 `crash/java_crash` affected-install ratio 超过冻结基线阈值 | occurrence release + HMAC installation coverage、最小 active installs、SDK drop/late 合格 | 回落到恢复线并保持两个窗口 |
| ANR regression | 新版本 `anr/anr_detected` affected-install ratio 超过冻结基线阈值 | 同上 | 同上 |
| SDK drop rate | `core/sdk_health.dropRate` P95 > 1% | 最小报告 installation、emitCount > 0；真实 runtime event identity | 30 分钟 < 0.2% |
| Missing symbols | production exact crash 的 `symbols_missing` 超过 15 分钟 | build identity 完整且 symbolizer enabled；metadata_missing 分开 | 制品上传、补偿完成且 backlog 清零 |

阈值在 staging 取得业务基线前只能标记 `TBD`，不得凭经验直接连生产通知。发布前必须用固定 fixture 验证触发、通知、静默、同根因分组、抑制、恢复和通知重试。低样本版本不得用百分比制造噪声；每个产品告警都绑定最小 event/installation 门槛、基线窗口、identity coverage 和 owner/runbook。

## 空状态与错误状态

| 状态 | 用户文案 | 允许的动作 |
| --- | --- | --- |
| `ZERO` | 查询成功且可观测，当前窗口为 0 | 可与基线比较 |
| `NO_DATA` | 当前筛选范围没有合格样本 | 扩大时间窗/检查版本选择，不显示 0% |
| `UNAVAILABLE` | 所需字段/分母未由协议提供 | 查看跨仓库阻塞；不允许创建依赖此值的告警 |
| `UNKNOWN_COVERAGE` | 模块可能未接线或本会话没有运行证据 | 查看 integration evidence/接入文档；不判定健康 |
| `DEGRADED` | SDK drop、采样、identity coverage 或链路质量未达门禁 | 展示已知计数，但隐藏/弱化比率结论 |
| `LATE` | 数据迟到或仅有 collection-time timestamp | 单列核对，不用于实时发布阻断 |
| `ERROR` | 查询、权限或下游失败 | 显示 request ID/重试，不保留旧值伪装实时结果 |

### 页面级状态矩阵

| 页面/功能 | 加载与部分加载 | 空/未配置 | 错误与离线 | 权限 | 长内容/大数字/窄屏 | 下一步动作 |
| --- | --- | --- | --- | --- | --- | --- |
| 发布健康 | 筛选先可用，指标/趋势/指纹分区骨架；质量条先返回时立即展示 | 无合格样本显示 `NO_DATA`；未选版本阻止查询并说明必填项 | 当前没有离线数据源；网络失败显示 `ERROR` 和 request ID，任何未来缓存必须标 `STALE` | scope 不匹配直接拒绝，不提供 tenant 参数绕过 | 指标不截断 state/单位；窄屏趋势后置但质量条常驻 | 重试、扩大窗口、重选版本或打开数据可信度 |
| 指纹/事件列表 | 首屏与下一页分开加载；翻页不清空现有列表 | 真零说明事件族与窗口；缺指纹显示 unavailable 而非空列表 | 保留已加载页但明确 stale/error；重试沿用同一筛选 | viewer 不显示也不能请求 L1 入口 | 指纹两行、机器 ID 可复制；窄屏单列 | 调整筛选、重试或返回发布健康 |
| 事故详情/raw | L1 metadata 与 symbol job 可部分返回；L2 raw 单独等待审计 commit | 制品/metadata 缺失显示固定状态与上传/接线路径 | raw 获取失败不抹掉 L1；禁止共享缓存 | viewer 隐藏 raw 动作；investigator 仍须 purpose/reason | 长栈独立滚动/复制；窄屏分段，不截掉 provenance | 补制品、重试 job、提交合规访问理由 |
| 数据可信度 | 身份、SDK、管线分阶段加载，未知阶段保持 unknown | 未配置 SDK health/identity 明确 `UNAVAILABLE` | 单组件失败只降级对应阶段；全局失败显示 request ID | L0 viewer 可读固定 scope，不能下钻 raw | coverage 保留精度与分子分母；窄屏纵向 | 打开接入文档、运维 runbook 或扩大窗口 |
| 人工发布决策 | 写入期间动作锁定，禁止重复提交 | 无历史决策显示 none，不推断“继续发布” | 失败保留表单与 reason；不做乐观成功/离线队列 | 仅 investigator；管理身份也不隐式越权 | 版本/scope 完整展示，窄屏不提供管理型批量动作 | 重试或交由有权限人员；成功后查看审计记录 |
| 平台运维 | 时间序列、backlog 和 dead letter 独立加载 | 未接数据源显示 unconfigured，不显示健康零值 | 组件失败按阶段标红并保留其他事实 | 仅运维受控入口；无 L2 tenant raw 默认权限 | 大计数保留精确 tooltip/表格；窄屏只读摘要 | 打开 runbook、切换 received-time 窗口或升级事件 |

## 权限与审计

- 所有读取 scope 由认证主体决定，tenant/app/environment 不能只靠 Dashboard variable。
- raw stack、SQL、URL、path、JS、exception message、context/extras 属于受限详情；列表和通知只显示安全摘要/指纹。
- 查看 raw、导出、人工重放、符号制品下载和告警规则变更都记录 actor、scope、理由、request ID 和结果。
- Dashboard 深链不得携带原始 credential、installation 明文或未脱敏 payload。

## 未决策事项与当前假设

| 事项 | 影响 | 决策角色/时点 | 当前假设 |
| --- | --- | --- | --- |
| 生产登录身份系统 | SSO、MFA、账号生命周期和 session bootstrap | 平台负责人；真实多用户 staging 前 | 当前 React/Vite 同源 Web 用 `apmq1` 一次交换短时 HttpOnly session；未来替换为 OIDC，不保存长期 key |
| 最小 active installations、coverage 和告警阈值 | 发布结论与噪声风险 | 发布负责人 + 数据/SRE；接通知渠道前 | API 返回事实与状态，前端不自行发明阈值；alert 保持 `production_ready=false` |
| 多租户 SigNoz 拓扑 | 隔离、成本和运维复杂度 | 安全/平台；生产 staging 前 | 受控 Query/BFF 或按信任域物理隔离，不开放共享 UI tenant filter |
| 窄屏产品范围 | 是否需要移动端完整诊断 | 产品/值班团队；桌面垂直切片验收后 | 只读分诊、关键状态和深链；不做长栈比较或高影响管理 |
| retention、删除、导出与 break-glass | 合规与证据保留 | 安全/法务/平台；接入真实 tenant 前 | 不扩大现有 raw 可见范围，不把本地默认值当生产策略 |
| 趋势图表库 | 时间趋势、无障碍等价表格和包体 | 前端负责人；实现版本趋势前 | 首个切片用事实卡片/表格，不为占位引入图表依赖；选型后必须有等价文本摘要 |
# 2026-09-07 比例质量门禁

安装影响率默认要求 100 个独立安装（`APM_QUERY_MIN_INSTALLATIONS`）和 100% 安装级 SDK health 覆盖（`APM_QUERY_MIN_SDK_HEALTH_COVERAGE`）。同时要求发生时版本/安装身份完整、无已报告丢弃、无迟到事件；任一条件不满足返回 null 比例及明确 reason，比较差值也为 null。SDK health 必须有有效 `emitCount > 0`、`dropCount >= 0`、`0 <= dropRate <= 1` 才能声明真实零丢弃，字段缺失/无效/无发出样本为 UNAVAILABLE。指标表达的是收到的安装样本；SDK 自报告覆盖不证明全部真实用户已接入监控。已接收事件的确切计数继续展示，不用采样不足抹去事故。
# 2026-09-07 查询执行预算

版本和指纹过滤下推到 SQL。数据库按身份/指纹/场景/协议/状态、相同迟到状态及至多 24 个时间桶合并事件，保留 weight、首次/最近时间、SDK 字段有效性和最大丢弃率；应用端按 weight 计算精确事件数，安装仍按 HMAC 去重。`APM_QUERY_MAX_ROWS` 现在限制聚合组数（默认 10000），不再直接限制原始事件数。事件分页只读取 limit+1，并保留 scope/filter/window 绑定游标。所有聚合/列表 SQL 有 5 秒预算，PostgreSQL 同时设置事务级 statement_timeout；超时为 retryable 503，不返回部分统计。高安装/指纹/场景基数组合仍可能超过组预算，需缩窄查询或建设独立 OLAP rollup，不提高上限掩盖规模边界。索引迁移 `20260907_0004` 为 append-only；生产执行前需安排建索引窗口。
