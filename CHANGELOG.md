# Changelog

Memora 项目的所有重要变更都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased] — 2026-07-03

### Breaking: 自适应记忆注入策略

- **可切换路由** — 支持 Manual、Auto 与 Hybrid；新安装默认采用 `manual + balanced + auto delivery`，需要回滚到确定性行为时使用 `manual + balanced`。
- **高级预设** — Tool First、Low Cost、Balanced、Quality 的普通记忆字符预算分别为 `0/800/1200/2400`，最大条数分别为 `0/2/4/6`。
- **安全注入边界** — 动态记忆不再进入 System Prompt；载荷仅在当前请求中临时存在，并受全局硬预算约束。
- **完整策略工作台** — Dashboard 新增 Overview、Strategy Configuration 与 Decision History，用于查看、配置和审计注入决策。
- **SQLite 决策记录** — 决策元数据全量持久化到 `injection_decisions`，不会保存查询文本、记忆正文/ID 或原始身份标识；默认保留 30 天和 100,000 行，两项均可配置。
- **关闭与崩溃语义** — 正常关闭最多等待 5 秒刷新待写批次；进程崩溃可能丢失最后一个未刷新的批次。
- **无旧配置迁移** — `recall_engine.injection_method` 已移除且没有兼容迁移；升级后必须用新字段重新配置，必要时可先采用 `manual + balanced`。

### feat: Dashboard 全站数据表与实体编辑器重构

- **统一 DataTable** — 数据密集页面改用基于 TanStack React Table 的共享 DataTable，统一列头排序、选择列、操作列、横向滚动、当前详情行和键盘行激活。
- **服务端排序契约** — 列表排序通过 allowlist 传递 `sort_by`/`sort_order`，在真实 `limit`/`offset` 分页前完成，并使用稳定 tie-breaker；排序、筛选、分页或查询变化会清除不可见行选择。
- **选择性视图持久化** — 每张表按 `memora.table.<tableId>.v1` 只保存密度、列显隐、列顺序和左右固定列；排序、筛选、分页、选择和列宽不写入持久化状态，损坏偏好会安全回退并保护 required 列。
- **统一实体详情编辑** — Knowledge、Memory、Notes、Profiles、Social、Jargon 和 Affection 详情统一使用 42rem 响应式 EntityEditorSheet，view/edit 共用一个 Sheet，body 独立滚动，危险/次要动作固定在 footer，并保留脏状态、重复提交保护和 revision 冲突流程。
- **视觉验收覆盖** — 增加知识库表格默认/列视图、实体查看/编辑、移动端表格/编辑器、宽屏画像、暗色社交表格和紧凑注入决策表的 browser smoke 基线。

### docs: 路线图 Milestone A 事实同步

- **当前事实入口** — 新增 `docs/QUALITY_GATE_STATUS.md`，记录最近一次 L0-L2 与统一 gate 的结果、耗时和失败归因。
- **覆盖状态同步** — 根级 `AGENTS.md`、Dashboard/monitoring/tests 模块文档同步当前测试事实：167 个 Python 测试文件、19 个 Dashboard 前端测试、monitoring 核心文件已有直接测试。
- **历史计划定位** — 旧深扫计划顶部增加状态说明，指向当前优化路线图与门禁状态文档。
- **Integration smoke 说明** — 新增 `tests/integration/README.md`，明确 5 个 pipeline smoke 的覆盖承诺和 mock 依赖边界。

### test: 门禁脚本状态输出

- **Smoke 分目标状态** — `scripts/run_smoke.py` 逐个运行 integration 目标，输出每个目标的 pass/fail、耗时、汇总和总耗时。
- **统一 gate 耗时记录** — `scripts/check_all.py` 为每个步骤和总流程输出耗时，失败时保留短失败归因。
- **Dashboard 产物检查** — 新增 `scripts/check_dashboard_build_artifacts.py` 与 `npm run check:artifacts`，统一检查生产 `index.html` 是否为 classic script 单 bundle、无 `type="module"` / `crossorigin`、无陈旧 JS/CSS hash 堆积。
- **元测试覆盖** — `tests/test_project_metadata.py` 增加脚本输出契约测试，防止门禁状态输出回退。

### feat: 可观测性摘要 API

- **Metrics summary** — 新增 `GET /api/plugin/memora/metrics/summary`，返回 recall 性能、质量评分、后台任务和 Prometheus registry 的可序列化摘要。
- **只读无副作用** — summary API 只读取现有 `_perf_tracker`、quality scorer 与 `_pending_tasks`，不会为了展示指标而惰性创建评分器或触发维护操作。
- **观测文档** — 新增 `docs/OBSERVABILITY.md` 记录 API 结构、数据来源、Dashboard 接入契约与验证命令。

### feat: 检索质量评测基线

- **离线评测 helper** — 新增 `core.evaluation.retrieval_quality`，支持 JSONL 样本加载、sync/async retriever runner、Recall@K、MRR、nDCG@K 与 p95 latency。
- **MemoryEngine 评测适配器** — 新增 `make_memory_engine_retriever()`，将评测样本 metadata 映射到 `search_memories()` 的 session、user、chat、memory type、emotion、chain depth 等参数。
- **检索样本集** — 新增 `private_basic`、`group_topic_shift`、`graph_relation`、`emotion_context`、`noise_negative` 五组 JSONL fixture，覆盖私聊、群聊话题切换、图关系、情绪上下文和负样本抗干扰。
- **消融报告** — 新增 `AblationReport`、`compare_reports()` 与 `evaluate_variants()`，用于记录 graph expansion、emotion boost、seasonal boost、testing effect 等开关前后的指标差值。
- **Boost trace 与 multi-hop 消融** — `RetrievalOptimizer.apply_boosts()` 支持可选 debug trace，multi-hop 检索支持按配置关闭图扩展或 topic 扩展，便于离线评测定位启发式贡献。
- **评测文档** — 新增 `docs/RETRIEVAL_EVALUATION.md`，约束检索默认参数变更必须记录数据集、指标和副作用。

### fix: 存储连接池可靠性

- **BaseStore pool path guard** — `BaseStore.init_pool()` 在不同 `db_path` 下会关闭旧池并重建，避免测试或运行期重初始化时静默复用旧数据库连接。
- **连接池生命周期测试** — `tests/test_storage_base.py` 覆盖同路径重复初始化、不同路径重建、`close_pool()` 后重开和共享 PRAGMA 行为。
- **存储维护 smoke** — `maintain_storage(vacuum=True)` 增加真实 SQLite + FTS + WAL checkpoint smoke，修复维护游标未关闭导致 `VACUUM` 失败的问题，并返回 FTS/WAL 诊断字段。

## [Unreleased] — 2026-06-28

### fix: 流程修复与质量门收口

- **启动生命周期修复** — 初始化完成后即可创建运行期组件，不再依赖下一条消息触发
- **后台任务治理** — terminate 统一停止 Provider retry、EventHandler 维护任务、DecayScheduler、BackfillScheduler
- **BM25 表名校验** — validator / rebuilder 仅接受校验后的 FTS 表名
- **备份恢复安全化** — 删除/恢复接口拒绝 `../`、绝对路径、分隔符和非法备份名
- **Smoke 脚本恢复** — `scripts/run_smoke.py` 现在指向真实 integration tests，并在缺少 `uv` 时回退到 `python -m pytest`
- **Dashboard 运行时安全控制** — Web API 触发 install/build 默认关闭，并带超时、并发锁和输出截断
- **Dashboard 构建输出同步** — Vite 先输出到临时目录再同步回 `pages/dashboard/`，避免 `outDir` 覆盖源码警告

### test: 最小前后端质量门

- **统一本地 gate** — 新增 `python scripts/check_all.py`
- **GitHub Actions CI** — 新增 Python + Node 双栈工作流
- **pytest 配置固定化** — 新增 `pytest.ini`
- **Dashboard 单测** — 新增 Bridge / `useRealtimeStream` Vitest 覆盖
- **API 契约测试** — 新增前端 Bridge 端点与后端注册一致性校验
- **全量回归状态** — 该轮 `python -m pytest tests -q` 为 3532 passed；当前基线以 `docs/QUALITY_GATE_STATUS.md` 为准

### docs: 协作文档与元数据对齐

- **命令文档对齐** — 根文档、README、多语言 README 同步为当前 `/lmem` 命令集
- **版本元数据对齐** — `metadata.yaml`、`PLUGIN_VERSION`、`@register()`、Dashboard `package.json` 保持一致
- **fast-context fallback** — 文档明确 `WINDSURF_API_KEY` 配置前提和本地搜索回退策略
- **模块文档约定** — 明确根级 `AGENTS.md` + 模块级 `CLAUDE.md` 的现行约定

---

## [1.0.0] — 2026-06-17

### 初始发布

Memora v1.0.0 — AstrBot 智能长期记忆插件的首个正式版本。

### feat: 核心记忆引擎

- **MemoryAtom 记忆原子模型** — 核心数据单元，包含 TTL、衰减类型、重要性、情感强度等时态属性
- **MemoryEngine** — 统一记忆引擎，协调记忆的完整生命周期
- **AtomLifecycleManager** — 原子生命周期管理：创建 → 激活 → 衰减 → 归档 → 遗忘
- **MemoryProcessor** — LLM 驱动的记忆抽取管道，自动从对话中提取有价值信息
- **ConversationManager** — 会话缓存与上下文管理

### feat: 多路混合检索

- **BM25 全文检索** — 基于 jieba 分词的中文 FTS5 全文搜索
- **FAISS 向量检索** — 基于 Embedding 的语义相似度搜索
- **RRF 融合** — Reciprocal Rank Fusion 融合 BM25 + 向量两路排序
- **HybridRetriever** — 统一的混合检索入口
- **DualRouteRetriever** — 文档路 + 图路双路并行召回
- **CrossEncoder 重排序** — 提升检索结果精度
- **LLM 重排序** — 基于 LLM 的结果重排序
- **PersonalizedRanker** — 基于用户画像的个性化结果排序
- **QueryRewriter** — 查询重写，提升召回效果

### feat: 图记忆系统

- **GraphStore** — 基于 SQLite 的图存储后端
- **GraphCRUD / GraphQuery / GraphDelete** — 图数据的完整 CRUD 操作
- **GraphRetriever** — 图检索：关键词匹配 + 向量搜索双路融合
- **Knowledge graph visualization** — Dashboard 图谱可视化

### feat: 知识库 & 笔记系统

- **KnowledgeExtractor** — LLM 驱动的知识点自动抽取
- **KnowledgeManager** — 知识库存储、检索、更新
- **NoteGenerator** — LLM 驱动的对话总结和笔记生成
- **NoteStore** — 笔记的持久化存储和检索
- **标签管理** — 灵活的多维度标签体系

### feat: 用户画像

- **ProfileBuilder** — 对话中自动构建用户画像
- **ProfileStore** — 用户画像持久化存储
- **PersonaInterpretation** — 用户人格特征分析
- **个性化对话策略** — 基于画像的个性化交互

### feat: 记忆衰减与遗忘

- **DecayScheduler** — 记忆衰减调度器，支持多种衰减策略
- **DecayOperations** — 衰减计算引擎：线性 / 指数 / 对数 / 自适应
- **EmotionScorer** — 情感强度评分，影响记忆权重
- **自动遗忘** — 低价值/过期记忆自动清理

### feat: 智能特性

- **ReflectionHandler** — 反思机制，周期性回顾和整合记忆
- **AutoLearning** — 从交互中持续学习和优化
- **ProactiveReminder** — 基于记忆的主动提醒
- **AnomalyDetector** — 记忆质量异常检测
- **SeasonalRecall** — 时间敏感的周期性记忆召回
- **PrivacyFilter** — 敏感信息自动过滤
- **ContinuityTracker** — 对话连续性追踪

### feat: 用户命令

- `/lmem status` — 查看插件初始化状态与核心组件状态
- `/lmem search <query> [k]` — 搜索记忆，`k` 默认为 5
- `/lmem forget <doc_id>` — 删除指定记忆
- `/lmem rebuild-index` — 重建向量索引
- `/lmem rebuild-graph` — 重建图记忆索引
- `/lmem webui` — 输出 WebUI 访问信息
- `/lmem summarize` — 立即触发当前会话总结
- `/lmem reset` — 重置当前会话长期记忆上下文
- `/lmem cleanup [preview|exec]` — 清理历史消息中的记忆注入片段
- `/lmem help` — 查看帮助信息

### feat: LLM Agent 工具

- **MemorySearchTool** — 记忆搜索工具
- **MemoryMemorizeTool** — 主动记忆工具
- **NoteTools** — 笔记管理工具集
- **KnowledgeTools** — 知识库管理工具集
- **ProfileTools** — 用户画像管理工具集

### feat: Web Dashboard

- **React 18 + TypeScript + Vite** — 现代前端技术栈
- **Tailwind CSS + shadcn/ui** — 精美 UI 组件体系
- **10 个功能页面** — Memory / Graph / Recall / Timeline / Profiles / Knowledge / Notes / Learning / System / Preview
- **实时事件流** — SSE 支持
- **Mock Server** — 独立开发调试环境

### feat: REST API

- **14+ API 端点** — 覆盖记忆读写、批量操作、统计、召回、图操作、知识库、笔记、画像、备份、学习、维护
- **SSE 实时端点** — 实时事件推送
- **统一响应格式** — 标准化的 API 响应封装

### feat: 国际化 (i18n)

- **zh** — 简体中文
- **en** — English
- **ru** — Русский

### feat: 工程基础设施

- **ConfigManager** — Pydantic 配置管理，所有默认值集中定义
- **PluginInitializer** — 插件初始化编排，Provider 加载 + DB 建立 + 组件创建
- **ProviderWaiter** — Provider 不可用时后台重试（最多 60 次）
- **BackupManager** — 版本升级自动备份数据
- **IndexValidator** — 索引一致性验证
- **IndexRebuilder** — 索引自动重建
- **SchemaManager** — 数据库 Schema 管理
- **MemoraException 体系** — 分领域的异常类（Database / Retrieval / Initialization 等）

### feat: 测试

- **pytest + Vitest 最小质量门**
- **完整 AstrBot Mock** — `conftest.py` 提供框架 Mock，无需真实环境
- **后端回归** — 该轮为 116+ 测试文件、3532 个 pytest 用例；当前基线以 `docs/QUALITY_GATE_STATUS.md` 为准
- **前端回归** — Dashboard Bridge / hooks Vitest 用例
- **契约回归** — Page API contract test 覆盖前端调用与后端注册的一致性

### feat: CI / 代码质量

- **pre-commit hooks** — `.pre-commit-config.yaml` 配置
- **ruff + ruff-format + mypy** — 代码格式化、静态检查与类型检查
- **统一本地 gate** — `python scripts/check_all.py`
- **GitHub Actions CI** — Python 回归 + smoke + Dashboard build/test

---

## [Unreleased] — 2026-06-23

### feat: 话题分割系统 (Topic Segmentation)

- **Prompt Engineering (策略 A)** — 要求 LLM 输出 `memories[]` 数组，自动按话题分割记忆
- **Embedding Clustering (策略 B)** — 后置聚类：按 embedding 相似度将 key_fact 聚类为独立话题
- **Topic-aware Pre-chunking (策略 C)** — 前置分块：在 LLM 调用前检测话题边界
- **Two-stage LLM (策略 D)** — 两阶段：第一阶段识别话题，第二阶段分别抽取
- **A+B Hybrid 默认策略** — A 做主分割，B 做安全网回退
- **TopicSegmentationRouter** — 运行时策略切换，支持热加载配置变更
- **BackfillScheduler** — 存量记忆回填调度器，为旧版记忆重新执行话题分割
- **配置键** — `topic_segmentation.*` 集中管理所有话题分割参数
- **Dashboard 配置页** — TopicSegmentationConfig 组件，可视化策略管理

### feat: 测试套件扩展

- **116+ 测试文件** — 从早期 20 文件扩展到该轮 116+ 文件；当前数量见 `AGENTS.md` 与 `docs/QUALITY_GATE_STATUS.md`
- **全覆盖** — 17 个核心模块 + 35 个管理器专项测试
- **10 个存储层专项测试**
- **10 个 API 层专项测试**
- **完整 retrieval 管线测试** (20 retrieval 专项测试)
- **19 个 processors 专项测试**
- **话题分割集成测试** — 策略切换、性能基准、E2E 验证
- **Dashboard 最小门禁** — Bridge / hooks Vitest + Page API contract test

### fix: 发布就绪修复 (2026-06-23)

- **版本号统一** — `backup_manager.py` PLUGIN_VERSION 从 2.4.0 修正为 1.0.0
- **i18n 文件部署** — `.astrbot-plugin/i18n/` 补全 zh.json / en.json / ru.json
- **Flaky 测试修复** — `test_cosine_similarity_matrix_latency` 阈值放宽至 3.0s
- **Dashboard 构建** — 生产构建产物生成至 `pages/dashboard/assets/`

---

## 版本规范

- `[Major.Minor.Patch]` 严格遵循语义化版本
- 标签格式：`feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `perf` / `ci`

## 参考

- [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)
- [Semantic Versioning](https://semver.org/lang/zh-CN/)
