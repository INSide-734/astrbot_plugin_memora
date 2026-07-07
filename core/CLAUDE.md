[根目录](../CLAUDE.md) > **core**

## 模块职责

`core/` 是 Memora 插件的核心业务引擎，包含记忆管理、检索、存储、API、工具、安全等全部后端逻辑。共约 200+ Python 文件，分为 25+ 子模块。

## 入口与启动

- **插件入口**: `main.py` (项目根) 通过 `@register("Memora", ...)` 注册为 AstrBot Star 插件
- **核心导出**: `core/__init__.py` -- 提供懒加载的公共 API
- **初始化编排**: `core/plugin_initializer.py` -- `PluginInitializer` 类，编排 Provider 加载、数据库初始化、组件构建
- **命令端点**: `core/command_endpoints.py` -- 混入 `MemoraPlugin`，注册 `/lmem` 命令组

### 初始化流程

```
PluginInitializer.initialize()
  -> ProviderWaiter.wait_non_blocking() (LLM + Embedding)
  -> ComponentFactory.build_all()
      -> DatabaseSetup (SQLite + FAISS + Graph DB)
      -> MemoryEngine, MemoryProcessor, ConversationManager
      -> DecayScheduler, BackfillScheduler
      -> AffectionManager, ExpressionLearner, JargonMiner, RelationManager
  -> PromptProtectionService
```

若 Provider 不可用，后台重试最多 60 次，就绪后自动完成初始化。

## 对外接口

### REST API (`core/api/`)

`core/page_api.py` 组合 24 个 API mixin 类，统一注册到 AstrBot 页面接口：

| Mixin | 端点 | 职责 |
|-------|------|------|
| MemoryReadApi | GET /api/plugin/memora/memories | 记忆读取 |
| MemoryWriteApi | POST /api/plugin/memora/memories | 记忆写入 |
| MemoryBatchApi | POST /api/plugin/memora/memories/batch | 批量操作 |
| MemoryStatsRecallApi | GET /api/plugin/memora/stats/recall | 召回统计 |
| GraphApi | GET /api/plugin/memora/graph/** | 图记忆查询 |
| KnowledgeApi | GET /api/plugin/memora/knowledge/** | 知识库 |
| NoteApi | GET/POST/PUT /api/plugin/memora/notes/** | 笔记系统 |
| ProfileApi | GET /api/plugin/memora/profiles/** | 用户画像 |
| AffectionApi | GET /api/plugin/memora/affection/** | 好感度 |
| ExpressionApi | GET /api/plugin/memora/expression/** | 表达模式 |
| JargonApi | GET /api/plugin/memora/jargon/** | 黑话查询 |
| SocialApi | GET /api/plugin/memora/social/** | 社交关系 |
| BackupApi | POST /api/plugin/memora/backup/** | 备份操作 |
| MaintenanceApi | POST /api/plugin/memora/maintenance/** | 维护操作 |
| DiagnosticsApi | GET /api/plugin/memora/diagnostics/** | 诊断 |
| EvaluationApi | GET/POST /api/plugin/memora/evaluation/** | 评测 |
| QualityApi | GET /api/plugin/memora/quality/** | 质量监控 |
| MetricsApi | GET /api/plugin/memora/metrics/** | 可观测性 |
| RecallTraceApi | GET /api/plugin/memora/recall-trace/** | 召回追踪 |
| ReviewApi | GET /api/plugin/memora/review/** | 审核队列 |
| LearningApi | GET /api/plugin/memora/learning/** | 自主学习状态 |
| DelegationApi | GET /api/plugin/memora/delegation/** | 功能委托状态 |
| TopicSegmentationApi | POST /api/plugin/memora/topic-segmentation/** | 话题分割 |
| HistoryTracker | -- | 历史追踪 |

### Agent 工具 (`core/tools/`)

15+ 个 LLM Tool，注册到 AstrBot Agent 系统供 LLM 主动调用：

| 工具类 | 工具名 | 职责 |
|--------|--------|------|
| MemorySearchTool | `memory_search` | 搜索长期记忆 |
| MemoryMemorizeTool | `memory_memorize` | 写入记忆 (默认关闭) |
| NoteSearchTool | `note_search` | 搜索笔记 |
| NoteReadTool | `note_read` | 读取笔记 |
| NoteWriteTool | `note_write` | 写入笔记 (默认关闭) |
| KnowledgeSearchTool | `knowledge_search` | 搜索知识库 |
| KnowledgeReadTool | `knowledge_read` | 读取知识条目 |
| ProfileLookupTool | `profile_lookup` | 查询用户画像 |
| JargonExplainTool | `jargon_explain` | 解释黑话 |
| JargonListTool | `jargon_list` | 列出已知黑话 |
| AffectionCheckTool | `affection_check` | 查询好感度 |
| BotMoodTool | `bot_mood` | 查询 Bot 心情 |
| ExpressionRecallTool | `expression_recall` | 召回表达模式 |
| RelationLookupTool | `relation_lookup` | 查询社交关系 |
| RelationGraphTool | `relation_graph` | 关系图查询 |

### 配置 Schema

`_conf_schema.json` -- 1100+ 行的完整配置定义，覆盖: 提供器、会话管理、召回引擎、衰减策略、融合策略、混合评分、记忆隔离、反思触发、图记忆、类人记忆增强、数据库迁移、索引重建、备份、写入可靠性、提示词模板、用户画像、重排序、自主学习、知识库、笔记、异常检测、关系追踪、连续性追踪、语义压缩、情景聚类、索引管理、Agent 工具、Dashboard、安全防护、权重学习、再巩固、导出、话题分割、原子分类器、闪光灯记忆、自动清理、质量过滤等 40+ 配置区块。

## 关键依赖与配置

- **AstrBot API**: `astrbot.api.Star`, `astrbot.api.star.Context`, 插件生命周期框架
- **AI 提供器**: `astrbot.core.provider.Provider` (LLM), `astrbot.core.provider.EmbeddingProvider` (向量嵌入)
- **数据库**: SQLite (aiosqlite + WAL 模式), FAISS (向量索引)
- **分词**: jieba 中文分词 (BM25 检索)
- **图引擎**: networkx (知识图谱)
- **Pydantic**: 数据模型校验 (guardrails)
- **数据格式**: JSONL (评测样本、导出), Markdown (导出)

## 数据模型

核心数据模型定义在 `core/models/`:

- `memory_atom.py` -- MemoryAtom: 记忆原子，核心存储单元
- `graph_models.py` -- GraphNode, GraphEdge, ExtractedGraph: 知识图谱
- `conversation_models.py` -- Session, Message: 会话与消息
- `knowledge_models.py` -- KnowledgeEntry: 知识条目
- `note_models.py` -- Note: 笔记
- `user_profile.py` -- UserProfile: 用户画像
- `recall_strategy.py` -- RecallStrategy: 召回策略

存储层在 `core/storage/`:
- `atom_store.py`, `atom_fts.py` -- 记忆原子 CRUD + 全文搜索
- `graph_store.py`, `graph_crud.py`, `graph_query.py`, `graph_delete.py`, `graph_subgraph.py` -- 图存储完整套件
- `conversation_store.py`, `message_store.py`, `message_queries.py` -- 会话/消息持久化
- `knowledge_store.py`, `note_store.py`, `profile_store.py` -- 其他实体存储
- `base.py`, `base_store.py` -- 共享基类 (连接池、性能 PRAGMA)
- `hierarchy_store.py` -- 层级记忆存储

## 检索系统

`core/retrieval/` 实现了完整的多路检索流水线:

```
查询 -> QueryRewriter(语义改写)
    -> DualRouteRetriever(双路并行)
        -> 文档路: BM25 + Vector -> RRF -> Hybrid -> Reranker
        -> 图路:   GraphKeyword + GraphVector -> Mixed -> MMR
    -> 融合排序 -> PersonalizedRanker
    -> HumanLikeFormatter -> 注入 LLM 上下文
```

| 组件 | 文件 | 职责 |
|------|------|------|
| BM25 全文 | `bm25_retriever.py` | 基于 jieba 的中文全文搜索 |
| 向量检索 | `vector_retriever.py` | FAISS 语义相似度 |
| RRF 融合 | `rrf_fusion.py` | BM25 + 向量融合 |
| 混合检索 | `hybrid_retriever.py` | 统一检索入口 |
| 图关键词 | `graph_keyword_retriever.py` | 图路关键词匹配 |
| 图向量 | `graph_vector_retriever.py` | 图路向量检索 |
| 图检索 | `graph_retriever.py` | 图路融合入口 |
| 双路路由 | `dual_route_retriever.py` | 文档路+图路并行 |
| 原子检索 | `atom_retriever.py` | MemoryAtom 级别检索 |
| Cross-Encoder | `cross_encoder_reranker.py` | CrossEncoder 重排序 |
| LLM 重排序 | `llm_reranker.py` | LLM 驱动重排序 |
| MMR 重排序 | `mmr_reranker.py` | 最大边际相关性 |
| 重排序工厂 | `reranker_factory.py` | 重排序策略工厂 |
| 查询改写 | `query_rewriter.py` | R1 语义查询展开 |
| 评分权重 | `score_weighting.py` | 混合评分计算 |
| 个性化排序 | `personalized_ranker.py` | 基于画像的结果排序 |
| 情感评分 | `emotion_scorer.py` | 情感强度加权 |
| 季节性召回 | `seasonal_recall.py` | 时间敏感周期召回 |
| 意图关键词 | `intent_keywords.py` | 意图驱动检索增强 |
| 知识检索 | `knowledge_retriever.py` | 知识库检索 |
| 可解释召回 | `explainable_recall.py` | 召回溯源 |
| 追踪模型 | `trace_models.py`, `trace_store.py` | 召回追踪与存储 |
| 生命周期 | `memory_lifecycle.py` | 记忆生命周期状态机 |

## 测试与质量

- **测试文件**: `tests/` 下 167+ 文件，覆盖所有管理器、处理器、检索器、存储、API、工具、安全
- **测试基础设施**: `tests/conftest.py` 提供统一 mock (AI 提供器、数据库、AstrBot 框架)
- **质量门禁**: `scripts/check_all.py` 统一运行 pytest + smoke + dashboard build + 前端测试
- **代码质量**: black + isort + ruff (配置: `.pre-commit-config.yaml`, `.codex/config.toml`)

## 常见问题 (FAQ)

**Q: 插件初始化失败怎么办？**
A: 检查 AstrBot 是否正确配置了 LLM Provider 和 Embedding Provider。插件会自动后台重试最多 60 次。使用 `/lmem status` 查看状态。

**Q: 如何启用图记忆？**
A: 在配置中启用 `graph_memory.enabled = true` 和 `atom_enabled = true`。

**Q: 如何控制记忆召回策略？**
A: 配置 `recall_engine` 区块中的 `top_k`、`injection_method`、`reranker.strategy` 等参数。

**Q: 伴侣插件如何协作？**
A: `core/feature_delegation.py` 自动检测 `self_learning` 和 `GroupChatPlus` 插件，跳过重复处理。

## 相关文件清单

- `main.py` -- 插件注册入口
- `core/__init__.py` -- 核心公共 API
- `core/plugin_initializer.py` -- 初始化编排
- `core/event_handler.py` -- 消息事件处理
- `core/command_handler.py` -- 命令处理
- `core/page_api.py` -- REST API 路由
- `core/feature_delegation.py` -- 伴侣插件委托
- `_conf_schema.json` -- 完整配置定义
- `metadata.yaml` -- 插件元数据

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 初始文档 | 生成 core 模块级 CLAUDE.md |
