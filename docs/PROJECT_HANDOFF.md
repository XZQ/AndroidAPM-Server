# AndroidAPM-Server 项目交接

> 当前本地产品基线：2026-11-02，服务端唯一分支 `main`。本文区分“本机已验证”和“需要外部环境验证”，历史条目保留当时事实。

## 2026-11-02 本地差异修复与交付验证

本轮从 GitHub/main 与本地 HEAD 同为 `24f5b8b4a6a2de12e29887421362e53558783133` 的未提交产品工作树继续，合并保存已有性能、告警、管理、九版迁移和三信号实现，并修复以下实际问题：

- 性能、告警、设置、Trace 页面接入真实 API，替换占位；支持观测/覆盖/趋势/类别、规则创建编辑/评估/启停/静默/确认、一次显示凭据/撤销、签名配置发布/递增回滚、运行/制品/审计与手动 Span 真实时间线。所有新增写入沿用绑定 CSRF，viewer 无管理/Trace 请求，admin 继承 L1/L2 和发布决策入口。旧/缺失字段保持状态和 null；刷新失败及迟到响应不复用旧证据。
- OTLP SDK-health 使用唯一性能目录，每事件每字段一个 Gauge 点；stage count 缺失/无效/非正时不导出占位延迟零，正样本真实零仍导出。
- 告警最后一次尝试后崩溃，过期重领直接 FAILED/RETRY_EXHAUSTED，不越过重试预算；活跃最后 owner 仍可完成。领取和最终写回复用数据库实时时钟 fencing。
- 文档扫描在遍历前剪除 .git/.local/依赖树；跨仓协议校验保留 message/type/name/number，只容许已审核的 ApmOccurrenceContext optional string 7–9，拒绝其他漂移。Docker Web build 复制 public 静态资产。
- 修正此前过度完成描述：当前 AndroidAPM SDK 仍为 durable V4，未发送标准 session/device/OS/session_start/span v2；这些是服务端可选输入及客户端待接入契约。本轮没有改动或提交 Android 客户端仓库。历史 2026-10-14 的 SDK/浏览器证据按当时条目保留，不能代表当前源码。

提交前验证：

- `uv sync --all-groups --frozen`、Ruff、mypy（63 source files）、14 份必需文档和 diff check 通过。
- 完整后端 **527 passed / 1 skipped**，分支覆盖率 **82.06%**（CI 门槛 70%）；skip 为未配置 `APM_TEST_NDK_BIN`。初次较长 Temp 路径触发既有 Windows 制品 os.replace 长路径问题；使用 `--basetemp C:/Users/24491/AppData/Local/Temp/af02` 重跑完整套件通过，未改变制品持久化实现。Docker 静态资产测试 3 项通过。
- 前端 lint/typecheck、**8 suites / 25 tests**、生产 build 通过；新增回归覆盖实际产品路由、admin/viewer、管理 CSRF、null/真零、一次显示 secret 与页面切换、配置发布/回滚和 Trace 深链。前端损坏依赖按 frozen lockfile 修复；lockfile 未改变。
- 独立 `.local` 测试库从空库执行九版 migration，另执行 SQLite head/check/downgrade base/upgrade head，通过且模型无差异。
- 实际 Edge headless + 生产 build + FastAPI 冒烟通过：性能/告警/设置/Trace/事件探索/质量深链，创建规则/手动评估/静默/停用，创建并撤销一次显示凭据，signed config 发布/递增回滚，Trace 层级和真实时间条；1440px 桌面及 390px 窄屏无页面横向溢出、无 JavaScript 异常。只使用隔离 synthetic 数据和本地无外部渠道配置。证据为忽略目录 `.local/review-*.png` 与 `.local/review-browser.cjs`。
- 6 项 PostgreSQL integration 因缺 `APM_TEST_POSTGRES_URL` 全部 skip；本机没有 Docker/Podman/psql，未执行容器运行、生产 PostgreSQL/SigNoz/TLS、真实通知、SDK 新契约或物理设备验收。浏览器 fixture 不替代这些门禁。

## 2026-10-14 本地统计补齐

- 服务端本地、GitHub 唯一分支及默认分支再次核对为 `main`；远端 foundation tip 保持 `24f5b8b4a6a2de12e29887421362e53558783133`。本轮服务端和客户端功能改动均保留在各自工作目录，尚未提交或推送。客户端分支为 `develop`。
- 性能统计扩大为 **243 个数值字段 / 66 个精确事件 / 29 个固定类别**，覆盖客户端已经上报的 IO、Render、WebView、Battery 等全部目录；新增 mean/min/max、加权 FPS、真实类别分布和事件计数。整数计数、固定类别、duration availability/sample count、累计快照与窗口摘要分别处理；缺失、无效、不可用、过期、未知原值不混为零。SDK 未采集的 GPU overdraw/Native 峰值等不能在服务端补造，细节见统计契约。
- Java Crash、Native Crash、ANR、Tombstone 分开计数、趋势、告警和指纹。Native 只使用可信 typed frames 或实际解析的 PC/module 帧；JNI 的“see tombstone”占位堆栈不产生合并指纹，ASLR load_bias 不参与分组。历史回填保留 payload/hash、身份、帧、Java/symbolized 指纹及 aliases；过期原文和损坏帧安全跳过。
- 客户端 V3 occurrence 增加 optional 匿名 runtime session/device/OS；durable codec V5 读取 V1–V4，旧缓存不补当前身份。每次成功 SDK init 同步 durable 写入精确 `core/session_start`；重复 init 保持身份，stop/reinit 更换。Trace schema v2 使用真实 start/end epoch 和单调 duration。
- 服务端九版 migration 至 `20261014_0009`，session 使用独立 HMAC domain，已知安装/session 明文在任何落库和派生证据前移除。旧 V3 optional absent 不改变 payload hash；重放使用原 key version。会话率仅在窗口内有效 start、完整身份/SDK-health/采样/迟到/安装门槛满足时显示；历史 app_exit/tombstone 不进入现场事故分子。标准设备/OS 分布及覆盖进入 Issue、事件详情和数据质量页。
- 新增手动 Trace 列表/Span 详情及真实时间线；旧 span 缺起止、父节点缺失/重复/循环、时钟异常及 raw 过期保留具体状态。所有 Trace 查询保持 scope/RBAC、时间/行数预算，自由 name 和 attr_* 不进默认 OTLP 索引。
- OTLP Metrics/Traces 显式 opt-in，默认关闭。数值观测为 Gauge；不把累计快照伪造成 Counter/Histogram。Logs/Metrics/Traces 首次 claim 固定导出计划，成功逐项持久化、重试只做剩余项，并保留数据库时钟 lease/owner fencing、有界预算和 partial_success 拒绝处理。已 delivered 事件不自动重发。

验证证据：

- 已执行冻结依赖同步、Ruff、mypy（63 source files）、14 份必需文档、diff check；前端 lint/typecheck、7 suites / 18 tests 和生产 build 均通过。
- 服务端完整回归 **519 passed**，包含 NDK 27 / LLVM 18 的真实双 ELF 验证。Windows 默认 pytest Temp 深路径触发既有制品 `os.replace` 路径长度限制；使用短目录 `--basetemp C:/Users/xiazh/AppData/Local/Temp/as53e9` 重跑全部通过，未为此改变制品实现。结果记录在 `.local/statistics-backend-validation.log`。
- 客户端针对实际改动执行 55 suites / **452 tests**，无失败/错误/跳过；model/core/trace API、24 个 baseline、Sample Debug/Release 构建、core/trace lint、47 Markdown / 86 links 通过。未重复完整 SDK CI，未进行真机安装/采集。
- 当前客户端真实 `HttpApmUploader` V2/V3 → 服务端 loopback 联调通过：空 SQLite 升级九版、Gzip、提交后 exact ACK、typed/occurrence persistence、installation/session HMAC、device/OS、旧数据 unknown coverage、明文排除和重复重放。客户端日志 `.local/collector-statistics-e2e.log`。
- 告警/设置实际 Chrome/Playwright 冒烟再次通过：独立 evaluator 自动触发、创建/确认/静默/停用、凭据创建/撤销及 viewer 会话失效、signed config 发布/回滚、深链刷新、390px 无页面横向溢出、404 和无 JS/console 错误。QA 规则/凭据清理完成，历史审计保留。截图与结果在 `.local/browser-evidence/`。
- 本机无 Docker/PostgreSQL，6 项 PostgreSQL integration 明确 skip。用户明确先完成本地；生产 PostgreSQL/SigNoz/TLS/SSO/KMS、真实通知渠道和物理设备验收仍需对应环境。

统计页面的最终浏览器结果及过期分类修复门禁在本节完成收尾时补录。操作见 [本地产品功能](11-本地产品功能.md)，完整统计目录见 [统计契约与口径](12-统计契约与口径.md)，决策见 [ADR 0011](adr/0011-运行会话统计与多信号导出.md)。

## 2026-10-13 本地产品功能（历史）

- 分支整理：保留完整 foundation tip `24f5b8b4a6a2de12e29887421362e53558783133`，本地重命名为 `main`，GitHub 默认分支改为 `main`，删除两个旧远端 codex 分支；`git branch -a` 与 `git ls-remote --symref origin HEAD refs/heads/*` 复核只有 main。后续产品代码为当前本地未提交改动。
- 性能：normalization v3 和 70 个精确数值契约覆盖 13 个域，支持观测分布/版本差值/24 桶趋势、有效覆盖、GC availability、真实零/无效/缺失区分、迟到与发生版本质量。窗口/查询行数受限，超限拒绝而不返回部分统计；历史 raw 投影回填保持身份/hash/时钟不变，过期证据跳过。
- 告警：八版 migration 持久化规则、firing episode 与通知 outbox，独立 evaluator、阈值/窗口/最少样本/版本限定、确认/静默/启停、可靠恢复、唯一 transition、有界重试、数据库时钟租约与 owner fencing。通知目的地只由服务端配置，Web 只见别名；本地没有真实渠道，未发送外部通知。
- 设置：独立 admin、应用名称、有效运行/保留参数、scope 管线/制品元数据、Query/Ingest/CI 生命周期、一次显示 secret、signed config 发布/灰度/新 revision 回滚及安全审计。管理操作强制 scope/RBAC/CSRF；既有角色未自动升权，浏览器不保存 raw key。
- Web：性能/告警/设置替换能力缺口页，admin 继承 investigator 诊断权限；修复显式深链刷新，保留未知 API/页面 404。新增控件标签、内部表格滚动和深浅主题；已实际检查桌面与 390px 视图。
- 本地启动：`scripts/start-local.ps1` 支持 `-PrepareOnly`/`-ClientRepo`，准备独立忽略配置/短期凭据/合成事件/八版 migration/backfill，隐藏启动保留维护与告警进程，Ctrl+C 统一停止。Android Sample Debug 使用忽略 properties、V3 发生身份/稳定安装/动态 headers/签名配置与 debug HTTP；Release 七项接入字段为空，offline/control 优先。

验证证据：

- frozen uv sync、Ruff、mypy 59 source files、13 份必需文档及 diff check 通过。后端全测使用 NDK 27 / LLVM 18，包括真实双 ELF symbolizer 回归；最新结果为 **287 tests passed**。产品新增 83 项覆盖精确目录、权限/CSRF/租户、凭据、签名回滚、告警状态/通知/失租、UTC 和历史回填。
- 前端 lint/typecheck、7 suites / 18 Vitest tests、生产 build 通过；JS 316.44 kB / gzip 99.72 kB。真实 Chrome/Playwright 冒烟通过：深链登录/刷新、性能筛选/趋势、独立进程自动触发和手动评估、确认/静默/停用、创建/撤销/viewer 会话失效、配置发布/回滚、窄屏无页面横向溢出、未知路径 404、无浏览器 JS/console 错误。截图及结果：`.local/browser-evidence/`；QA 规则停用、QA key 撤销，历史保留。
- 客户端 JDK 17 的 Sample `assembleDebug/assembleRelease/lintDebug` 通过，无 lint error。四组严格依赖 metadata、47 Markdown / 83 links、Release 空配置和 gitignore 复核通过；没有因仅 Sample 接入重复完整 886 项 SDK CI。
- 真实客户端 `HttpApmUploader` V2/V3 → FastAPI loopback E2E 通过：空 SQLite 升级八版、Gzip、提交后 exact ACK、typed/occurrence persistence、HMAC/明文排除与重放去重。日志：服务端 `.local/product-backend-validation.log`、客户端 `.local/collector-product-e2e.log` 与 `.local-server-build.log`。
- Compose 两份 YAML 和 alerts service 配置静态解析通过；本机无 Docker/PostgreSQL，6 项 PostgreSQL integration 因无 `APM_TEST_POSTGRES_URL` 全部 skip，不计为通过。没有物理设备安装/运行、生产 TLS/SSO/KMS/SigNoz 或真实通知验收；标准 session/device/OS 和 Trace/OTLP Metrics 跨仓契约仍待建设。

操作见 [本地产品功能](11-本地产品功能.md)，架构见 [ADR 0010](adr/0010-本地性能告警与管理面.md)。用户已明确先做本地，生产环境尚未准备。

## 2026-09-12 顺序整改

1. 异常日志隐私：SQLAlchemy 隐藏参数，structlog 与 Uvicorn/stdlib 共用不含消息正文的有界异常诊断；2 项回归覆盖原始参数、SQL、驱动 cause/notes 和 ASGI 二次日志。完整门禁通过：后端 152 tests、mypy 55 files、文档 12、前端 12 tests 及 lint/typecheck/build、依赖锁同步、Ruff、diff check。Windows Temp 退出清理权限告警不影响退出码 0。

2. 趋势与可信度：发布/Issue 缺失桶保留 null，发布桶区分 NO_DATA/UNAVAILABLE/观测 ZERO；Web 断开缺口并保留孤立点，可信度包含新旧版本质量门禁。30 项 Query 回归、155 项后端全测、16 项前端测试及全部静态/构建/文档门禁通过。

3. 发布身份长度：新增 `20260912_0006`，把数据库及接收/制品/查询/决策入口统一到现有协议的 256-byte 上限。129/256/257 边界、中文 UTF-8 查询、制品幂等、六版 SQLite migration/autogenerate 和防截断 downgrade 通过；全门禁为后端 162 tests、前端 16 tests。新增 PostgreSQL 实存回归，当前 6 项集成测试因缺少连接全部跳过。

4. Issue 符号指纹：新增 `20260912_0007` 和 ADR 0009，保留 raw 别名、迁移历史完成任务，在 fenced 完成事务内切换 canonical Issue；旧链接限定 scope/window，拆分歧义返回 409。前端事件下钻使用服务端解析后的指纹。聚合、重放、旧 owner、跨租户、迁移/回退回归通过；全门禁为后端 165 tests、前端 16 tests。

5. Java mapping：校验支持包名、保留类名、内部类、Unicode 和 JVM 合成名字，仍拒绝空段/非法 descriptor/NUL。15 个格式样本和 6 个制品 API 回归通过，包含带包名 mapping 的上传/重放；完整门禁为后端 181 tests、前端 16 tests。

6. Native 多模块：逐帧核对 typed/raw 模块身份和 PC，按精确 ABI/build-id 路由 ELF，缺少任一模块不花费尝试次数；未知、函数级和源码级解析分开，保存逐帧制品证据及部分完成状态。34 项相关测试通过，含 NDK 27.0.12077973 编译双 ELF 并实际执行 LLVM 的交错帧回归；完整门禁为后端 196 tests（显式提供 NDK 路径）、前端 16 tests。

7. 工具输出内存：并发分块处理三条管道，stdout/stderr 各自 1 MiB，在追加前检查；溢出立即 kill/reap，超时/取消清理只丢弃剩余字节。真实 Python 子进程覆盖双流超限后阻塞、精确边界和先输出后读大输入，38 项符号化测试通过；完整门禁为后端 200 tests（含本机 NDK）、前端 16 tests。

8. 保留期证据语义：场景 TTL 不变，仅保存 presence 状态；原可用但已清理的注册字段标 EXPIRED，历史场景缺少 presence 时标 RETENTION_UNKNOWN。SQL 加权覆盖包含可用/缺失/过期/历史未知事件，部分分布 DEGRADED；Web 显示覆盖和原因。SQLite 清理前后、历史兼容、API 元数据/Issue、SDK 真零保留与 Web 展示回归通过。完整门禁为后端 204 tests（含本机 NDK）、前端 18 tests；依赖锁同步、Ruff、mypy 55 files、文档 12、前端 lint/typecheck/build 和 diff check 通过。

最终跨仓库联调：客户端工作目录实际构建的 `HttpApmUploader` V2/V3 → 当前服务端 loopback 通过（退出码 0），测试数据库从空库升级七版至 `20260912_0007`，验证 Gzip、精确 ACK、typed/occurrence 持久化、HMAC/明文排除、重复重放。客户端有其他并行工作，末次检查 HEAD 为 `b776c68`，联调相关 model/uploader 路径干净，其最近改动为 `38969d0`；不能把本次证据归给旧 `90dd147`。两份 JAR 写入时间均早于联调，SHA-256 分别为 model `a128ac0c0d3ef21c595689e3756eff10637b305b0f02bce25cd69f26cd1b2c4a`、uploader `0a034ad075cdefb347c30e873f765a51dababf0d326276f65218a6d17d0d20ca`。首轮在 `C:\Windows\Temp` 清理数据库遇到文件占用；从客户端工作目录、使用用户 `LOCALAPPDATA\Temp` 重跑完整通过，本轮未修改或提交客户端源码。

环境边界：本轮没有 Docker/Podman/psql，未配置 PostgreSQL 集成连接，6 项集成测试明确 skip。SQLite/跨语言 loopback 及本机双 ELF LLVM 验证不能替代 PostgreSQL、真实 R8/设备崩溃采集、生产工具镜像、Compose/SigNoz/TLS 验收。

## 2026-09-07 顺序整改

基线实现已独立纳入 Git；依次修复异常导出、installation 派生数据、页面证据绑定、质量门禁、HMAC 轮换、查询预算、异步认证、运行指标、保留/背压和符号化租约。每项单独验证、commit、push，并核对远端 SHA。Docker/PostgreSQL/TLS/SigNoz 现场证据仍未取得。

1. 异常导出：增加 normalization v2 数值边界、历史坏行隔离及非预期传输异常的有限重试。新增真实 worker 周期与字段边界回归测试。依赖锁同步、Ruff、mypy（51 files）、pytest（108 passed）、文档（12）和前端四门禁（9 tests）通过；Windows Temp 退出清理告警不影响测试退出码 0。

2. Installation 派生证据：统一最小化 raw/registered/native/symbol input；覆盖 V2/V3、嵌套字符串、跨 key replay 和整批拒绝回归。依赖锁、Ruff、mypy、111 个后端测试及文档门禁通过。新数据修复不代表已清理外部历史存储。

3. 页面证据绑定：覆盖全部查询页面、raw 和人工决策；前端 lint/typecheck/test/build 通过（12 tests），新增失败刷新、乱序响应、跨事件 raw 三个回归。

4. 质量门禁：SDK 缺字段/非法字段/零分母均不可用；影响率增加身份、自健康、丢弃、迟到和最小样本门禁，原始计数保留。21 个相关测试通过，含 12 个新边界回归。

5. HMAC 轮换：查询读取真实 key version，对跨版本窗口/比较显式返回安装连续性断点；覆盖 API 入库到查询及 Issue/trend/distribution，前端显示空缺而非双计数。

6. 查询预算：SQL 过滤/加权聚合、分页解除整窗预检、5 秒查询时限、新增筛选时间索引。测试证明 500 个同类事件在 100 组预算下保留准确计数；SQLite 迁移 upgrade/check/downgrade/upgrade/check 通过，PostgreSQL 执行计划与建索引耗时尚无现场证据。

7. 异步认证：Argon2 有界线程执行、查 key 前固定内存速率门禁、取消不提前释放 hashing 槽；未知 key 保留 dummy verify。覆盖事件循环可推进、饱和/恢复及三种认证入口的查库前拒绝。

8. 运行指标：真实 DB backlog/最老年龄、读取失败 NaN/available=0、独立 worker 监听及采集配置、公网 metrics 隔离；导出计数在提交后更新。17 个相关测试通过，包括独立子进程 HTTP 抓取。

9. 保留/容量准入：终态 raw 清理、保留原 hash/HMAC 去重、quota 过期、事务审计、raw 410、行数/字节阈值和失败回滚；新增迁移 0005 与 ADR 0008。140 个后端测试、前端四门禁（12 tests）和 SQLite 五版迁移 upgrade/check/downgrade/upgrade/check 通过。操作只在隔离测试数据库执行，未清理任何外部数据。

10. 符号化租约：执行前逐个领取、每次独立 token、数据库时钟与过期写入保护、整项处理截止时间、工具取消/超时终止回收、缺制品轮询不消耗工具次数、崩溃重领不超预算；符号化指标在提交后增加。23 个相关回归通过，覆盖同进程旧结果写回、排队期间未占租约、缺制品等待、过期完成/失败以及真实子进程取消。导出完成/失败同步加上未过期租约保护。

### 最终验证

2026-09-07 完整执行依赖锁同步、Ruff、mypy（55 source files）、pytest（150 passed）、12 份文档校验、前端 lint/typecheck/test/build（12 tests）与 diff check，退出码均为 0。pytest 仍有 Windows Temp 退出清理权限告警，不计为测试失败。

以客户端当前干净的 `develop@90dd147` 运行真实 `tools/verify_collector_e2e.py`：Gradle 构建、五版 SQLite migration、真实 `HttpApmUploader` V2/V3 Gzip、exact ACK、typed/occurrence/HMAC 持久化与重复重放全部通过。客户端代码及 Git 状态在本轮验证前后保持一致。

独立执行 `uv run pytest integration_tests -rs`，5 项因未配置 `APM_TEST_POSTGRES_URL` 跳过。本机 Docker/Podman/psql 均不可用，未执行容器构建、Compose、PostgreSQL/SigNoz/TLS 现场验收；这些仍是部署门禁。当前 CI 只触发 main push 或面向 main 的 PR，因此推送功能分支不能视为远端 CI 已通过。

## 2026-09-04 多路由 APM 控制台与 Issue 聚合（历史）

本轮把原有发布健康长页面升级为同源多路由诊断控制台：总览、Issues、Issue 详情、investigator 事件探索/详情、版本发布和数据质量均有独立可恢复 URL；性能、告警和设置以 `UNAVAILABLE`/`UNCONFIGURED` 页面公开真实建设边界。viewer 导航不显示事件枚举入口，直接访问事件页也只返回权限说明；L2 raw 不自动读取，仍要求 purpose/reason 并等待服务端审计提交。

Query/BFF 新增 `GET /v1/query/issues/{fingerprint}`，在固定 scope 和有界窗口内返回真实事件/安装计数、首次/最近发生、最多 24 个 occurrence-time 桶、版本与场景分布。设备型号和 Android 版本没有标准客户端 resource 契约，因此返回 `UNKNOWN_COVERAGE`，不会从任意 context/extras 伪造；release-health 同步增加真实零值趋势桶。Crash-free/ANR-free Sessions 继续保持 `UNAVAILABLE: SESSION_ID_NOT_PROVIDED`。

当前已执行并通过：`uv sync --all-groups --frozen`、Ruff、mypy（51 source files）、pytest（96 passed）、12 份必需文档验证，以及前端 ESLint、TypeScript、Vitest（5 files / 9 tests）和 production build。当前生产 bundle 为 JS 290.45 kB（gzip 92.95 kB）、CSS 40.77 kB（gzip 8.28 kB）。pytest 仍只有本机 `.pytest_cache`/Windows Temp 清理权限 warning，测试退出码为 0。

同日使用 production build、真实同源 FastAPI、synthetic SQLite fixture 和应用内 Chromium 完成多路由浏览器验收：

- 1536 × 1024：登录后进入固定 scope 总览，发布指标、真实零值趋势和 Top Issues 正常；从 Top Issues 下钻到 Issue 详情，再打开独立事件详情，URL 与返回路径正确。
- Issue 版本/场景 tab 返回真实聚合；设备 tab 明确显示 `UNKNOWN_COVERAGE` 和标准资源缺失原因。页面不会自动读取 L2 raw，填写 purpose/reason 后才出现原始/符号化并列证据，浏览器控制台无 warning/error。
- 事件探索、版本发布、数据质量、性能、告警、设置路由均可访问；后三者明确显示 `UNAVAILABLE`/`UNCONFIGURED`，未伪造指标或声称云服务已接通。
- Issue 深链刷新后保留同一 URL 和页面；深浅主题切换可持久恢复。768 CSS px 重排用于覆盖 1536 桌面在等效 200% 文本缩放下的布局，页面无水平滚动。
- 390 × 844 首轮发现宽表内容将根页面撑到 727 px；修复为 table 自身受控横向滚动并增加 paint/layout containment 后，根页面 client/scroll width 均为 375 px，`window.scrollX` 固定为 0，移动导航抽屉可打开且所有入口可见。

设计 fidelity ledger：左侧 224 px 工作台信息架构、顶栏固定 scope/时间窗、黄色可信度门禁、紧凑指标带、趋势+依据双栏、Issue 趋势+分布双栏均与 `docs/design/` 概念图一致。刻意不复刻概念图中的“最近告警”、Issue 生命周期/负责人、设备/OS 值和异常类标题，因为当前分别缺生产告警实例、生命周期 API、标准资源契约和安全 L0 字段；用能力状态或覆盖未知替代，避免演示数据冒充真实能力。

以上是本地 UI、同源 API 与 synthetic 数据证据，仍不证明 PostgreSQL、SigNoz、TLS、通知或公网部署。既有 2026-08-28 迁移往返属于历史证据。

## 已实现

- Python 3.11 + FastAPI/uv 工程、严格配置、结构化日志、Prometheus 端点。
- `POST /v1/events`：Bearer ingest key、tenant/app/environment scope、schema/header/content 校验。
- 应用层有界 body 读取、单成员 Gzip 有界解压、Line Protocol、4-byte big-endian length-prefixed Protobuf，以及显式 `ApmBatchEnvelope` V2/V3。
- 数据库固定窗口 request/event quota；PostgreSQL `(tenant_id,event_id)` 最终唯一约束。
- 同批和历史重复 ACK；同 eventId 不同内容显式 `409`；事务 commit 后才生成整批 2xx ACK。V2/V3 都要求响应 schema/batch ID/event count 与请求精确一致。
- Inbox worker 的 owner/lease/expiry、过期重领、owner-only 完成、退避、`Retry-After` 和 dead letter。
- Android event 到 OTLP Logs 的 resource/severity/context/eventId 映射；兼容 string fields 只按源代码审核白名单恢复数值/布尔类型。
- SigNoz v2 Perses Dashboard、v2 alert templates 和 service-account API key 幂等同步器。
- `GET /v1/config`：Ed25519 签名、递增 revision、过期、灰度稳定分桶、ETag/304；CLI 创建 key/publish config 并记录审计。
- V3 occurrence envelope：客户端发生时 release/build/installation/native identity、`b3-` batch ID、提交后 exact ACK；legacy/V2 继续兼容且保持低质量标签。
- 入库前 installation 假名化：tenant/domain/key-version HMAC，明文从 durable payload 删除；轮换后 replay 按原行 key version 比较。
- 初始 normalization/field registry 与 fixed-scope Query/BFF：`apmq1` viewer/investigator、发布健康、指纹、数据质量、事件分页/元数据/raw、人工 `continue/pause/rollback` 决策和审计。
- 独立 `apmci1` CI key 与 `artifact:write` scope；Java mapping/未剥离 Native ELF 的有界流式上传、SHA-256、格式/ABI/.symtab/GNU build-id 校验、精确身份幂等与冲突审计。
- Crash durable symbolization job：metadata_missing、awaiting_symbols、symbols_missing、制品上传重排队、owner/lease/expiry、retry/final failure 和 OTLP 符号状态/指纹映射。
- R8 retrace/llvm-symbolizer 固定 JSON argv adapter：不经过 shell，限制运行时间和输出，保存工具版本；symbolizer 默认关闭且工具由部署镜像提供。
- 五版 Alembic schema、非 root 只读容器、Gateway/export worker/symbolizer/PostgreSQL Compose、共享私有制品卷和官方 SigNoz/Foundry 版本边界。

## 2026-08-28 V3、Query/BFF 与真实跨仓闭环

当前统一工作树已实现 ADR 0006：Android 客户端以 additive `Apm.init(application, config, occurrenceContext)` 保持 `ApmConfig`/`ApmEvent` 原 data-class ABI，durable codec V4 在 dispatcher/IPC/SQLite handoff 前保留 occurrence；`PROTOBUF_ENVELOPE_V3` 使用 typed fields、每事件 occurrence 和 exact post-commit ACK。服务端 `20260828_0003` 增加 occurrence/normalization/query schema，V2/V3 installation 在 durable insert 前只留下 tenant-scoped HMAC/key version。

同轮新增首个 Query/BFF：凭据固定 tenant/app/environment，viewer 只能读取 L0；investigator 才能分页读取 L1、purpose/reason 审计后的 L2 raw，并记录人工继续/暂停/回滚。窗口、扫描行数、page limit 和 HMAC cursor 都有上限；session 指标固定返回 `UNAVAILABLE: SESSION_ID_NOT_PROVIDED`。

客户端执行：

```powershell
python tools/verify_collector_e2e.py --server-repo D:\workspace\AndroidAPM-Server
```

脚本构建真实 `HttpApmUploader`，启动实际 FastAPI/uvicorn，分别发送并重放 V2/V3 Gzip batch。迁移到 head 的测试用 SQLite inbox 最终只有 4 个唯一 eventId，并独立核对 exact ACK、V2 typed scalar、V3 occurrence release/build/variant、native frame、HMAC/key version、低质量请求声明不覆盖 occurrence 和 installation 明文未进入 `payload_json`。这仍是 SQLite 兼容闭环，不是 PostgreSQL/TLS/SigNoz 生产 durability 证据。

## 2026-08-28 当前统一工作树最终本机门禁

在保留 Server 与 Android 两仓现有未提交改动的前提下，Server 当前统一工作树执行：

```powershell
uv sync --all-groups --frozen
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/verify_docs.py
pnpm --dir web install --frozen-lockfile
pnpm --dir web run lint
pnpm --dir web run typecheck
pnpm --dir web run test
pnpm --dir web run build
git diff --check
```

依赖锁同步、Ruff、mypy（51 source files）、pytest（95 passed）、12 份必需文档验证和 diff check 全部通过。前端 ESLint、TypeScript、Vitest（3 files / 5 tests）和 production build 全部通过；生产 JS 为 224.49 kB、gzip 70.69 kB。pytest 仅报告本机 `.pytest_cache` 写权限警告，不影响收集、执行或退出码。文档校验器会排除 `.venv` 与 `node_modules`，不会把本地第三方依赖 README 当成仓库文档。

另对唯一明确的随机临时 SQLite 数据库执行 `alembic upgrade head -> downgrade base -> upgrade head -> alembic check`；三版 revision 的 upgrade/downgrade 均完成且 autogenerate 报告 `No new upgrade operations detected`。临时数据库随后已删除。该结果只证明迁移的 SQLite 兼容往返和 metadata 一致性，不能替代 PostgreSQL DDL、锁、事务和并发验收。

同日使用 `scripts/start-local.ps1` 的 synthetic fixture 和真实浏览器完成同源 Web smoke：`apmq1` 一次交换、固定 scope、发布健康/数据质量、Crash/ANR 指纹筛选、L1 事件详情、purpose/reason 审计后的 L2 raw、三种人工决策选择、暂停决策写入/回读、`NO_DATA` 非零值空状态、深浅主题、390 px 窄屏无页面横向溢出和退出流程均通过，浏览器控制台无 warning/error。烟测发现并修复 favicon 404；最终 `/favicon.svg` 返回 `200 image/svg+xml` 和显式缓存策略。fixture 与本地 SQLite 仍不构成真实生产数据或云端 durability 证据。

同日只读现场能力审计确认：Windows PATH 中没有 Docker、Podman、`psql`、R8 `retrace` 或 `llvm-symbolizer`；WSL `2.7.10.0` 可用，默认 `Ubuntu-24.04`/WSL2 处于 `Stopped`，本轮没有为探测而启动它。因此镜像/Compose/PostgreSQL/SigNoz、真实 R8/LLVM、TLS/通知、备份恢复、故障注入和 72h soak 仍是现场阻塞，不能由上述本地门禁替代。

## 2026-08-28 真实场景规划复核与本机门禁

本轮以干净客户端 `AndroidAPM/develop@e1dd627` 重新核对 wire、durable outbox、事件目录、SDK 自健康、PII、动态 Header/endpoint 和 signed remote-config 实现，并把首个产品切片冻结为“新版本 `crash/java_crash`、`anr/anr_detected` 可信回归闭环”。规划明确：普通 `app_exit` 不算 Crash，异常退出证据不与现场事件相加；无统一 telemetry session 时不计算 Crash-free/ANR-free sessions；高基数 raw 字段不默认进入 OTLP attributes。

复核在实现前确认两个生产阻塞：V2 resource 无法可靠归因升级前 outbox 事件，且 installation 明文当时会进入 `payload_json`。这两个本地代码阻塞现已由 accepted ADR 0006、V3 occurrence 和 installation HMAC 关闭；本节以下 71-test 结果保留为实现前的历史证据，不代表当前最新门禁。

在保留现有未提交 M3 实现的统一工作树上执行：

```powershell
uv sync --all-groups --frozen
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/verify_docs.py
git diff --check
```

该轮依赖锁同步、Ruff、mypy（42 source files）、pytest（71 passed）和文档验证通过；结果现已被上面的 V3/Query 闭环与本文最终门禁记录取代。Docker 当时不可用，因此没有执行镜像 build、Compose/PostgreSQL、Foundry/SigNoz 或真实 R8/LLVM smoke。

## 2026-08-27 本机统一验证

`codex/server-foundation` 已快进包含 Collector V2 的 `f1214c1`、`2feb2f5` 两个提交，当前主工作区同时恢复了尚未提交的 M3 制品/符号化实现。以下结果验证的是这个统一工作树，不代表尚未提交的 M3 变更已经发布到远端：

```powershell
uv sync --all-groups --frozen
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/verify_docs.py
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
git diff --check
```

依赖同步、Ruff 和文档校验通过；mypy 对 42 个 source files 无问题；pytest 收集并通过 71 tests，覆盖 legacy/V2 ingest、精确 ACK、制品上传、symbolization 状态机和 worker。两版 Alembic migration 在全新临时 SQLite 数据库完成 `upgrade -> downgrade base -> upgrade`。pytest 仅报告本机 `.pytest_cache` 写权限警告，不影响测试执行或结果。

本机没有 Docker 命令，因此不能执行镜像 build、Compose/PostgreSQL 冒烟或真实 SigNoz/R8/LLVM 验证。

## 2026-07-31 本机验证

```powershell
uv sync --all-groups --frozen
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/verify_docs.py
git diff --check
```

以上命令通过：ruff 无问题，mypy 对 35 个 source files 无问题，pytest 为 57 tests / 0 failures。协议测试覆盖 V2 media/schema 协商、SDK/resource/header 一致性、稳定 batch ID、12 种 typed scalar、JSON-safe 非有限浮点保真、任意精度输入上限、legacy 字段隔离、原子拒绝、提交后精确 ACK 和重复重放。

同日从 Android 客户端仓库运行 `tools/verify_collector_e2e.py`：它以 JDK 17 构建真实 `apm-model` 与 `apm-uploader`，启动实际 FastAPI/uvicorn Gateway，通过 Gzip HTTP 连续发送两次相同 V2 batch。真实 `HttpApmUploader` 两次均收到精确 ACK；测试用 SQLite inbox 最终只有 2 条唯一事件，12 种标量、resource、schema 和 protocol 元数据均经数据库读取复核。这是跨语言/真实 HTTP 兼容证据；SQLite 仅用于该测试，不构成 PostgreSQL 生产 durability 或并发结论。

## 2026-07-16 本机验证

```powershell
uv sync --all-groups --frozen
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/verify_docs.py
```

以上命令通过；本地测试基线为 56 tests，0 failures，核心包分支覆盖率 70.82%（生成的 Protobuf 代码排除）。另使用全新临时 SQLite 数据库执行两版 migration 的 `alembic upgrade head -> downgrade base -> upgrade head`，随后 `androidapm-admin create-ci-key` 成功创建 tenant、独立哈希 CI 凭据和审计记录。这是迁移/CLI 兼容冒烟，不替代 PostgreSQL 事务与并发验证。

## 未完成或尚未现场验证

- 本机没有 Docker/Compose，所以镜像 build、Compose health、官方 Foundry 安装和真实 SigNoz OTLP/Dashboard/告警尚未执行。
- Foundation 提交的 GitHub CI 已两次通过 PostgreSQL 16.9 migration round-trip、并发 Inbox/`SKIP LOCKED`、Compose config 和镜像 build。本次 M3 变更新增并发 artifact identity 测试；在对应 PR workflow 运行前仍属于待验证变更。
- Dashboard/alert JSON 已按 SigNoz `v0.133.0` 当前官方 v2 API 源码编写并做离线测试，但还没有由真实 SigNoz 校验查询表达式和渲染结果。
- Java/Native 制品 API、私有本地/持久卷 store、任务状态机和工具 adapter 已实现；生产对象存储/KMS、固定 R8/LLVM 工具镜像、真实 mapping/ELF/crash E2E 尚未完成。基础镜像没有工具二进制，`APM_SYMBOLIZATION_ENABLED` 默认 `false`。
- OTLP Metrics 独立信号、Trace context、生产托管诊断 UI/身份系统/RLS/KMS/HSM、真实多租户部署、备份/PITR、TLS/WAF、压测/72h soak 尚未验收；本地 Query/BFF/Web 已实现不等于生产现场完成。
- Android SDK 已有动态 header/endpoint provider、签名远程配置、V2 兼容 resource 和 V3 occurrence identity；生产凭据签发/轮换及服务端部署联调仍待完成。精确符号化 schema 已能保存 versionCode/variant/ABI/build-id/module-relative pc，但真实 Java/Native crash 采集、工具和制品 fixture 仍不足以完成现场验收。

## 下一步顺序

1. 在有 Docker 的 Linux/Windows 环境跑 PostgreSQL integration、Compose 和真实 SigNoz staging E2E。
2. 修正真实 SigNoz 返回的任何 dashboard/rule schema/query 差异并保存截图/请求证据。
3. 构建固定 R8/LLVM 工具镜像，接 S3 兼容对象存储/KMS，用真实 Java 与每 ABI Native 样本完成符号化 E2E。
4. 用真实跨升级 outbox、Java mapping 和每 ABI Native crash fixture 验收 occurrence/build/native identity coverage，完成 SDK→Gateway→symbolizer→SigNoz 联调。
5. 补生产 RLS、已实现 retention/backpressure 的现场容量验收、备份恢复、故障注入、容量/性能和安全门禁。
