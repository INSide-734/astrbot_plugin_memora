# 更新日志

Memora 的所有重要变更都记录在此文件中。

本文档遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.1.0] — 2026-07-31

Memora 1.1.0 聚焦召回准确性、请求内证据覆盖和隐私安全观测，并同步加强提示词保护的配置一致性与并发隔离，适配新版 Dashboard 构建工具链。

### 新增

- 新增有界查询计划：规范化意图、实体、时间锚点和焦点词，在不扩大既有候选预算的前提下生成最多三条查询，并通过跨查询 RRF 合并 canonical 候选。
- 新增实体、角色、时间、事件、焦点和关系证据评分；评分只作用于请求局部候选副本，不修改 canonical memory、ID 或持久化 metadata。
- 新增文档、图和 Atom 三路并发检索协调器；单路普通失败可有限降级，调用取消会传播并收敛子任务。
- 新增带单调游标的召回性能样本 Page API，返回有界 allowlist 标量，用于读取阶段耗时、候选数量和注入结果。

### 改进

- 注入候选选择会优先补齐查询计划要求但尚未覆盖的 facet，再按确定性效用、冗余度和字符预算选择，减少近重复证据挤占有限注入空间。
- 完整查询计划参与结果缓存和会话缓存键，避免不同实体、时间锚点、查询变体或必需 facet 共享不兼容结果。
- Dashboard 生产构建适配 Vite 8 与 Rolldown 的 IIFE 输出，使用兼容的 CSS 压缩路径，并保持 AstrBot classic-script 单包产物契约。
- 更新 Dashboard、Python、GitHub Actions 和测试工具链依赖，补充社区协作模板与自动依赖维护配置。
- 将 uv 缓存和测试临时目录隔离在本地工作区，避免开发缓存进入版本控制。

### 安全

- `security.prompt_protection_enabled=false` 时不再创建提示词保护服务，运行时行为与配置保持一致；默认值仍为启用。
- 提示词保护 scope 注册表改为串行化访问，避免并发请求登记、消费和清理时相互污染。
- 保护失败诊断统一使用 AstrBot logger，只记录阶段、异常类型、scope 是否存在和载荷字符数，不记录载荷、scope 值或原始异常消息。
- 召回性能样本不返回或持久化 query、Prompt、记忆正文、原始身份、内部 ID、Provider 信息或异常内容。

### 修复

- 修复 Vite 8/Rolldown 动态导入辅助代码与 CSS 压缩在 classic-script 构建中的兼容问题。
- 修复提示词保护关闭配置未在共享服务创建阶段生效的问题。
- 修复并发提示词保护 scope 登记可能发生竞争的问题。

### 升级说明

- 从 `1.0.0` 升级不需要数据库迁移、canonical memory 重建或配置字段迁移。
- 本次没有移除命令、API、配置字段或稳定数据契约。
- 新增召回样本端点为向后兼容能力；性能跟踪器尚未初始化时返回空页。
- 显式关闭提示词保护的部署升级后会按配置跳过保护服务；未修改该配置的部署继续使用默认保护行为。

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
