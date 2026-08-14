# 更新日志

Memora 的所有重要变更都记录在此文件中。

本文档遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [1.2.1] — 2026-08-15

Memora 1.2.1 更新 Dashboard 与 GitHub Actions 工具链依赖，修复其间接依赖中的安全问题。

### 改进

- Dashboard 升级 `@base-ui/react`、Vite、PostCSS、Playwright、shadcn、React Vite 插件与 Node 类型定义；保留 React 18 与 Tailwind CSS 3 运行时，未将未完成的大版本迁移纳入本版。
- GitHub Actions 升级 checkout、Node 设置、uv 设置、GitHub Pages 部署与 Pages artifact 上传动作。

### 安全

- 升级 PostCSS 间接依赖 `nanoid`，修复其自定义字母表参数为零时可能无限循环的问题。

### 升级说明

- 从 `1.2.0` 升级无需迁移配置、canonical memory、索引或数据库。

## [1.2.0] — 2026-08-15

Memora 1.2.0 完成后端 package-by-feature 架构切换，引入可热重载的记忆写入门禁与 mark_write 隔离语义，并收紧再巩固、备份、发布打包和 Windows 开发链路的正确性边界。

### 破坏性变更

- 删除从未接入生产且与好感度域重复的 `relationship_tracking` 配置（`enabled`、`warmth_decay_per_day`）。Bot 与用户的关系/好感度统一由好感度域权威维护，用户间关系由社交关系域维护；旧配置会被安全忽略，无需迁移数据。
- 删除无收益证据且从未正确装配的 `weight_learning`（MAB）配置（`enabled`、`epsilon`、`group_by_persona`）与实现；统一反馈与参数候选改由 FeedbackSignal 管线和自主学习 shadow 候选承担。
- 自主学习改为默认关闭的 shadow 候选模式：删除无消费者的 `learning_rate`、`target_hit_rate_low/high`、`quality_ema_alpha` 配置叶，`enabled` 默认从 `true` 改为 `false`。
- 后端完成 package-by-feature 单轨切换并删除旧 `core/api`、`core/managers`、`core/processors`、`core/security` 等技术分层兼容路径。命令、Page API 与持久化契约保持不变；直接导入插件内部旧路径的自定义扩展需要改用 `core/features`、`core/platform` 或 `core/shared` 下的新路径。

### 新增

- mark_write 低置信记忆写入 canonical 后默认不参与召回与演化，避免污染注入与派生证据；`/memora search` 末尾位置参数 `true` 与记忆列表 API `include_mark_write=true` 可显式包含。
- 记忆写入门禁可配置化：新增 `quality.gate` 配置域与 Dashboard 门禁页（profile 与绑定、检查/阈值/词表/处置/Judge/自定义规则/dry-run），支持按绑定上下文解析 profile、规则树与六类动作、三处置路由（quarantine/discard/mark_write）与保存即时生效的热重载。
- Dashboard 配置状态可读取文件化 Prompt 默认值，并展示抽取模板和 Judge 模板的默认内容、占位符与校验结果。

### 改进

- 后端按领域职责收敛到 `core/features`、宿主与传输能力收敛到 `core/platform`、跨域端口与纯工具收敛到 `core/shared`；所有生产调用方、测试和脚本同步迁移，不保留双轨实现。
- 插件初始化、Provider 装配、重载与关闭使用明确的组合根和资源定位边界；初始化失败会回滚已发布组件和后台任务，取消信号继续传播。
- Page API 路由注册、命令端点与 Agent 工具按传输职责拆分，保持既有 AstrBot bridge、路由与响应 envelope 不变。
- Python 开发环境新增 Pyright 门禁并锁定 AstrBot `4.27.2` 作为开发验证基线；插件运行时最低版本约束仍为 AstrBot `>=4.24.2`。

### 修复

- 记忆再巩固改为候选闭环：召回只生成 pending 候选，不再自动改写 canonical；人工按来源 revision CAS 应用并可回滚旧正文。
- 移除从未发布且无生产消费者的 Trait Evolution 隐藏实现与生命周期入口。
- 修复 `/memora summarize` 把 quarantine、discard 或 mark_write 处置误报为普通长期记忆写入、将候选数量误作消息进度，以及混合结果把隔离候选计入重要性的问题；命令现分别报告四类处置计数。
- 修复 Dashboard 活跃会话从 canonical 记忆 metadata 推导，导致对话已采集但长期记忆为空时错误显示 0 的问题。
- 修复门禁链路未完整传递 `group_id`、人工批准时 `needs_judge` 未经 Judge 解析，以及 Judge 取消未正确传播的问题。
- 修复 `scripts/package_plugin.py` 源码包收集依赖手写排除清单，导致 `.uv-cache/`、`.idea/`、`.pytest_memora_data/` 等被 `.gitignore` 排除的本地文件与目录被打入源码包的问题；收集改为以 `git ls-files --exclude-standard` 的跟踪与忽略语义为准。
- 修复旧版仅含反馈数据库的备份不能通过完整性检查和恢复的问题，同时保持当前多数据库备份的严格校验。
- 修复指标采样在空样本、非有限值和类型检查导入边界上的异常，避免观测故障影响主链路。
- 修复 Windows 缺少 `os.fchmod`、不支持目录描述符持久化、CRT 文本模式转换随机二进制密钥中的 CRLF/`0x1A`，以及反馈备份完整性检查未及时关闭 SQLite 句柄时，HMAC 安装密钥首次创建、备份校验或备份目录发布会失败的问题；Windows 现使用二进制模式读取并显式释放校验连接，POSIX 平台仍严格执行 `0600` 文件权限校验。
- 修复 Windows 开发与回退门禁中的平台假设：多进程测试不再让 spawn worker 导入无关重型包，SQLite 测试和回退证据显式释放文件句柄，Git 探针仅对显式源目录设置命令级 `safe.directory`，manifest 比较改用 Python 标准库，插件 Skill 发现测试不再依赖 AstrBot 私有更新器路径。

### 升级说明

- 从 `1.1.0` 升级不需要迁移 canonical memory、重建索引或修改数据库；删除的旧配置叶会被安全忽略。
- 此前未显式设置 `auto_learning.enabled`、因而依赖 `1.1.0` 默认启用的部署，升级后将变为关闭；如需继续生成 shadow 候选，请显式设置 `auto_learning.enabled=true`。
- mark_write 仅影响新门禁处置及显式带有该标记的记忆；默认检索和演化会排除它们，管理员可通过命令或 Page API 显式查看。
- 直接导入插件内部旧 `core/*` 技术分层路径的自定义扩展必须迁移；普通 AstrBot 安装、管理命令、Dashboard 与 Page API 使用方无需调整。

## [1.1.0] — 2026-07-31

Memora 1.1.0 聚焦召回准确性、LLM 首字响应关键路径、请求内证据覆盖和隐私安全观测，并同步加强提示词保护的配置一致性与并发隔离，适配新版 Dashboard 构建工具链。

### 新增

- 新增有界查询计划：规范化意图、实体、时间锚点和焦点词，在不扩大既有候选预算的前提下生成最多三条查询，并通过跨查询 RRF 合并 canonical 候选。
- 新增实体、角色、时间、事件、焦点和关系证据评分；评分只作用于请求局部候选副本，不修改 canonical memory、ID 或持久化 metadata。
- 新增文档、图和 Atom 三路并发检索协调器；单路普通失败可有限降级，调用取消会传播并收敛子任务。
- 新增带单调游标的召回性能样本 Page API，返回有界 allowlist 标量，用于读取阶段耗时、候选数量和注入结果。
- 新增 `recall_engine.pre_llm_soft_budget_ms` 软预算配置；默认从 LLM 请求钩子起算 `800ms` 绝对截止时间，设置为 `0` 可关闭预算。
- 新增请求局部召回计时上下文，记录 readiness、身份解析、查询分析、主检索、画像读取、候选整理和召回钩子总耗时，不依赖跨请求共享的最近一次搜索状态。
- 新增 LLM 请求前关键路径基准，覆盖冷/热路径、命中/未命中、私聊/群聊的 p50/p95/p99、部分降级率和底层 Embedding 调用次数，并同步执行 Recall@K、MRR 与 nDCG 质量门禁。

### 改进

- 注入候选选择会优先补齐查询计划要求但尚未覆盖的 facet，再按确定性效用、冗余度和字符预算选择，减少近重复证据挤占有限注入空间。
- 完整查询计划参与结果缓存和会话缓存键，避免不同实体、时间锚点、查询变体或必需 facet 共享不兼容结果。
- 图记忆重建改为在单次 `BEGIN IMMEDIATE` 事务内完成旧图删除、新节点/边/条目写入、FTS 同步与孤儿清理；SQLite 提交后按源记忆清理并重建图向量，使失败重放保持幂等。
- Dashboard 生产构建适配 Vite 8 与 Rolldown 的 IIFE 输出，使用兼容的 CSS 压缩路径，并保持 AstrBot classic-script 单包产物契约。
- 更新 Dashboard、Python、GitHub Actions 和测试工具链依赖，补充社区协作模板与自动依赖维护配置。
- 将 uv 缓存和测试临时目录隔离在本地工作区，避免开发缓存进入版本控制。
- LLM 请求前的 readiness 检查改为非阻塞快照读取；初始化或运行时组件尚未发布时直接跳过本次召回，不再等待 Provider 初始化或补装组件。
- 稳定身份解析改为同步、无 I/O 的关键路径操作；可信名称目录和会话名称同步转入受管理后台任务，并在关停阶段统一收敛。
- BM25 与图关键词等本地检索始终保留，文档和图向量检索只等待共享软预算的剩余时间；向量超时会取消并收敛任务后使用本地结果继续聊天主链路。
- 文档与图 FAISS 共享并发 Embedding single-flight，相同进行中请求只调用一次底层 Provider；顺序请求不缓存，一个等待者取消也不会影响其他等待者。
- 自发与前瞻辅助召回受主检索剩余预算约束，并使用独立计时容器；明确、低歧义且无图相关 facet 的简单查询不再创建图检索任务。
- 召回观测新增文档、图、Atom 三路固定布尔降级标记和整次路由中止标记；单路故障继续保留成功候选并标记部分降级，达到两路故障阈值时清空残余候选并安全中止本次路由。
- 将 BM25/FTS 的固定表名和静态 SQL 收敛到共享契约，供检索、批量管理、索引重建和持久化校验复用，减少重复语句并保持索引行为一致。

### 安全

- `security.prompt_protection_enabled=false` 时不再创建提示词保护服务，运行时行为与配置保持一致；默认值仍为启用。
- 提示词保护 scope 注册表改为串行化访问，避免并发请求登记、消费和清理时相互污染。
- 保护失败诊断统一使用 AstrBot logger，只记录阶段、异常类型、scope 是否存在和载荷字符数，不记录载荷、scope 值或原始异常消息。
- 召回性能样本不返回或持久化 query、Prompt、记忆正文、原始身份、内部 ID、Provider 信息或异常内容。
- 收紧 SQLite 查询边界：批量 ID 改为绑定 JSON 参数，分页排序改为绑定值驱动的固定 `CASE`，动态表名、列名、谓词和迁移语句改用静态 SQL 或严格 allowlist；社交关系存储只执行含固定 `social_relations` 标识符的静态语句，外部值始终作为参数处理。
- FAISS 运行时检查、全量门禁、Dashboard 打包、召回基准和 smoke 脚本的子进程调用显式使用 `shell=False`，避免参数被 shell 二次解释。
- GitHub Actions 中的 `setup-uv` 与 Release 发布动作改为固定不可变提交 SHA，降低第三方标签漂移带来的供应链风险。

### 修复

- 修复 Vite 8/Rolldown 动态导入辅助代码与 CSS 压缩在 classic-script 构建中的兼容问题。
- 修复提示词保护关闭配置未在共享服务创建阶段生效的问题。
- 修复并发提示词保护 scope 登记可能发生竞争的问题。
- 修复图记忆分段提交期间，并发孤儿节点清理可能删除尚未建立边引用的新节点并触发 SQLite 外键约束失败的问题。
- 修复部分召回降级状态在计时合并时被数值化为 `1.0` 的问题，超时和降级标记现在保持布尔类型。
- 修复前瞻 PLANNED 原子转换的候选缺少 RRF、BM25 和向量评分字段，导致候选不能稳定参与后续注入的问题。
- 修复 Dashboard 配置契约和 browser smoke 仍使用旧 schema 字段计数，导致新增软预算字段未被完整验收的问题。
- 修复下层检索器内部降级未被三路协调器计入失败阈值的问题；即使下层已吞掉普通异常，文档、图或 Atom 的整路失败仍会参与统一的部分降级与中止判定。

### 升级说明

- 从 `1.0.0` 升级不需要数据库迁移、canonical memory 重建或配置字段迁移。
- 本次没有移除命令、API、配置字段或稳定数据契约。
- 新增召回样本端点为向后兼容能力；性能跟踪器尚未初始化时返回空页。
- 显式关闭提示词保护的部署升级后会按配置跳过保护服务；未修改该配置的部署继续使用默认保护行为。
- 新增软预算配置无需手工迁移；未配置时使用 `800ms` 默认值，需要恢复无截止时间行为时可显式设置为 `0`。
- 软预算只限制 LLM 请求前的向量和辅助等待时间，不删除 canonical memory；超时时仍保留本地检索结果并记录部分降级状态。

## [1.0.0] — 2026-07-27

Memora 的首个正式版本，为 AstrBot 提供从对话采集、长期存储、混合检索到请求内安全注入的完整记忆生命周期，并配套可视化管理、诊断、备份和离线评测能力。

### 新增

#### 记忆生命周期与身份

- 从对话中抽取、分类并持久化记忆原子，支持话题分割、会话总结、群聊环境消息反思、衰减、归档与遗忘。
- 以 SQLite 中的 canonical memory 作为唯一权威记录；FTS5、FAISS、图关系和 Projection 均为可校验、可失效、可重建的派生数据。
- 为 OneBot 11、QQ 官方 WebSocket 与 Webhook 接入稳定协议身份。QQ 号与不同平台实例下的 OpenID 严格隔离，名称变更只更新作用域显示名和历史别名，不改变 canonical user ID。
- 新记忆保存受信任的参与者来源证据；历史别名仅在原会话作用域唯一匹配时用于只读召回增强，不回写原始记忆。
- 提供默认关闭的记忆演化管线，以有界后台任务生成带 source revision 证据的关系和 Projection，不建立第二套记忆主键。

#### 检索、评测与注入

- 组合 BM25 全文检索、FAISS 语义检索、图检索、RRF 融合、关系扩展、重排序和隐私过滤，并按会话、用户、群组、角色和有效期约束召回结果。
- 支持 `manual`、`auto`、`hybrid` 三种注入路由，以及 Tool First、Low Cost、Balanced、Quality 四档预设。
- 根据 Provider 能力选择当前请求内的临时交付方式；动态记忆不写入 System Prompt，并受条数、字符数和全局硬预算约束。
- 异步记录脱敏注入决策，提供路由预览、决策历史和可解释召回 trace；记录不包含查询、Prompt、记忆正文、记忆 ID 列表或原始身份。
- 提供 JSONL 检索数据集、Recall@K、MRR、nDCG@K、p95 延迟、消融对比和安全反馈排序 shadow，用于离线验证检索质量。

#### 知识与个性化

- 提供知识库、笔记、用户画像、社交关系、好感度、Bot 情绪、表达模式和群聊黑话等存储与管理能力。
- 为 AstrBot Agent 注册记忆搜索、主动记忆、笔记、知识库和用户画像工具。
- 支持中文、English、Русский 三种界面与命令文本。

#### Dashboard、API 与运维

- 提供 16 个 Dashboard 功能入口，覆盖概览、记忆、图谱、时间线、召回、注入、知识、笔记、画像、社交、好感度、黑话、学习、智能诊断、系统和配置。
- 统一数据表排序、筛选、分页、列视图和选择行为；统一实体查看与编辑流程，并支持移动端布局、键盘操作、脏状态保护、revision 冲突和批量操作。
- 提供与 AstrBot 配置联动的 Dashboard 配置页，支持字段校验、并发 revision、原子写回、运行时重载和失败回滚。
- 提供 Page API、标准错误 envelope 与 SSE 实时事件，覆盖记忆、图谱、知识、笔记、画像、关系、注入、评测、备份、维护、诊断和配置等管理域。
- 提供校验后发布的备份快照、定时备份、安全保留策略，以及带预检、维护锁、回滚和热重载的事务式恢复流程。
- 系统概览支持检查 GitHub Release、查看发布说明、忽略版本和下载经 SHA-256 校验的 runtime 包；镜像失败时自动回退 GitHub，并复用 AstrBot 的 HTTP、HTTPS 或 SOCKS5 代理。
- 宿主支持时，系统概览可在确认后自动安装 runtime、原子切换插件目录并请求 AstrBot 单插件重载；新版本重载失败会自动恢复旧目录，宿主不支持单插件重载时安全降级为仅下载。
- 提供 `/memora status`、`health`、`diagnostics`、`search`、`trace`、`forget`、`rebuild-index`、`rebuild-graph`、`webui`、`summarize`、`reset`、`cleanup`、`update` 和 `help` 管理命令。
- 提供隐私安全的问题报告模式、运行时健康评分、性能与质量摘要、后台任务状态和有界召回追踪。

#### 工程与发布

- 建立 Python 3.12 锁定开发环境、pytest/Vitest 测试体系、Ruff 与 pre-commit 门禁，以及 Python、Dashboard、smoke 和构建产物检查组成的 CI 流程。
- 提供经过校验的源码包与运行时包构建脚本，并检查 Dashboard 单文件构建产物的运行时兼容性。
- 统一 `metadata.yaml`、Python 项目元数据、运行时注册版本和 Dashboard 包版本为 `1.0.0`。

### 安全

- 所有写入、检索、编辑和派生流程均校验 scope、privacy、validity、role 与 revision；请求变更先完整构建，再原子应用。
- 身份、Projection 和注入观测采用模型可见字段 allowlist，内部 ID、来源映射、歧义过程、Provider 凭据和敏感请求内容不会进入 Prompt 或诊断记录。
- SQL 值使用参数绑定，动态排序与表名使用固定 allowlist；备份和恢复拒绝路径穿越、绝对路径及非法目标。
- 初始化、后台任务和关闭流程传播取消信号；普通可恢复故障安全降级，不中断 AstrBot 聊天主链路。

### 修复

- 修复初始化完成后运行期组件未及时创建、Provider 重试和后台调度器未完整关闭等生命周期问题。
- 修复 SQLite 连接池跨数据库路径复用、维护游标阻塞 `VACUUM`、WAL/FTS 维护结果不完整等存储问题。
- 修复 canonical 时间戳与 revision 丢失、结构化抽取字段被覆盖、派生数据并发写入和 Projection 来源映射校验问题。
- 修复注入预算预留、请求作用域隔离、异步决策批次归属、工具可用性回退和历史注入清理边界。
- 修复配置并发写回与回滚竞争、备份恢复失败状态、API 编辑冲突和敏感错误信息外泄风险。
- 修复 Dashboard 移动端滚动与操作遮挡、长标签溢出、编辑竞态、表格固定列和配置导航状态问题。

### 从预发布版本升级

- 运行环境要求 Python `>=3.12,<3.13`；AstrBot `>=4.24.2` 可获得完整的 Pages 与 WebUI 支持。
- 运行前需要在 AstrBot 中配置可用的 Embedding Provider 和 LLM Provider。
- 已移除 `recall_engine.injection_method`，且不提供旧字段迁移。请改用 `injection_routing_mode`、`injection_manual_preset` 和 `injection_delivery_override`；新安装默认使用 `manual + balanced + auto delivery`。
- 稳定身份目录通过独立表幂等创建，不执行 `ALTER TABLE`、历史扫描、canonical memory 重写或身份回填。
- `memory_evolution.enabled` 默认关闭；未显式启用时不会启动演化 worker，也不影响现有 canonical 记忆链路。
- `1.0.0` 建立首个公开稳定契约，此前开发快照中的配置与数据行为不视为稳定 API。
