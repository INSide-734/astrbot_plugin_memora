[根目录](../CLAUDE.md) > **tests**

## 模块职责

`tests/` 目录包含 Memora 插件的全部自动化测试套件: 单元测试、集成测试、检索评测和压力测试，共 167+ 文件。使用 pytest 框架，通过 `tests/conftest.py` 提供统一的 mock 基础设施。

## 入口与启动

- **测试配置**: `tests/conftest.py` -- pytest fixtures (mock AI 提供器、数据库、AstrBot 框架)
- **集成测试配置**: `tests/integration/conftest.py`
- **运行全部测试**: `python -m pytest tests -q`
- **运行特定模块**: `python -m pytest tests/test_memory_engine.py -q`

### Mock 基础设施

`tests/conftest.py` 在导入 `core.*` 之前 mock AstrBot 运行时依赖，使得测试可以在没有真实 AstrBot 环境下运行:
- Mock `astrbot.api.*` (logger, Star, Context 等)
- Mock `astrbot.core.provider.*` (LLM, Embedding)

## 测试文件清单

### API 测试 (23 文件)

| 测试文件 | 目标模块 |
|---------|---------|
| `test_api_memory.py` | memory_read/write/batch API |
| `test_api_graph.py` | graph API |
| `test_api_knowledge.py` | knowledge API |
| `test_api_note.py` | note API |
| `test_api_profile.py` | profile API |
| `test_api_affection.py` | affection API |
| `test_api_diagnostics.py` | diagnostics API |
| `test_api_evaluation.py` | evaluation API |
| `test_api_expression.py` | expression API |
| `test_api_jargon.py` | jargon API |
| `test_api_learning.py` | learning API |
| `test_api_metrics.py` | metrics API |
| `test_api_quality.py` | quality API |
| `test_api_realtime.py` | realtime API |
| `test_api_recall_trace.py` | recall trace API |
| `test_api_response_utils.py` | response utilities |
| `test_api_review.py` | review API |
| `test_api_social.py` | social API |
| `test_api_topic_segmentation.py` | topic segmentation API |
| `test_api_delegation.py` | delegation API |
| `test_api_backup.py` | backup API |
| `test_api_history_tracker.py` | history tracker |
| `test_maintenance_api.py` | maintenance API |

### 管理器测试

| 测试文件 | 目标模块 |
|---------|---------|
| `test_managers_anomaly.py` | `core/managers/anomaly_detector.py` |
| `test_managers_atom_lifecycle.py` | `core/managers/atom_lifecycle_manager.py` |
| `test_managers_autolearn.py` | `core/managers/auto_learning.py` |
| `test_managers_backup.py` | `core/managers/backup_manager.py` |
| `test_managers_compressor.py` | `core/managers/semantic_compressor.py` |
| `test_managers_continuity.py` | `core/managers/continuity_tracker.py` |
| `test_managers_conversation.py` | `core/managers/conversation_manager.py` |
| `test_managers_decay.py` | `core/managers/decay_operations.py` |

### 存储测试

| 测试文件 | 目标模块 |
|---------|---------|
| `test_atom_store.py` | `core/storage/atom_store.py` |
| `test_atom_fts.py` | `core/storage/atom_fts.py` |
| `test_graph_store.py` | `core/storage/graph_store.py` |
| `test_graph_crud.py` | `core/storage/graph_crud.py` |
| `test_graph_delete.py` | `core/storage/graph_delete.py` |
| `test_graph_query.py` | `core/storage/graph_query.py` |
| `test_graph_subgraph.py` | `core/storage/graph_subgraph.py` |
| `test_graph_extractor.py` | `core/processors/graph_extractor.py` |
| `test_conversation_store.py` | `core/storage/conversation_store.py` |
| `test_hierarchy_store.py` | `core/storage/hierarchy_store.py` |
| `test_knowledge_store.py` | `core/storage/knowledge_store.py` |

### 检索测试

| 测试文件 | 目标模块 |
|---------|---------|
| `test_atom_retriever.py` | `core/retrieval/atom_retriever.py` |
| `test_bm25_retriever.py` | `core/retrieval/bm25_retriever.py` |
| `test_vector_retriever.py` | (向量检索) |
| `test_hybrid_retriever.py` | `core/retrieval/hybrid_retriever.py` |
| `test_dual_route_retriever.py` | `core/retrieval/dual_route_retriever.py` |
| `test_graph_retriever.py` | `core/retrieval/graph_retriever.py` |
| `test_graph_keyword_retriever.py` | `core/retrieval/graph_keyword_retriever.py` |
| `test_graph_vector_retriever.py` | `core/retrieval/graph_vector_retriever.py` |
| `test_cross_encoder_reranker.py` | `core/retrieval/cross_encoder_reranker.py` |
| `test_llm_reranker.py` | `core/retrieval/llm_reranker.py` |
| `test_knowledge_retriever.py` | `core/retrieval/knowledge_retriever.py` |
| `test_diversity_manager.py` | `core/utils/diversity_manager.py` |

### 处理器测试

| 测试文件 | 目标模块 |
|---------|---------|
| `test_atom_classifier.py` | `core/processors/atom_classifier.py` |
| `test_chatroom_parser.py` | `core/processors/chatroom_parser.py` |
| `test_contradiction_detector.py` | `core/processors/contradiction_detector.py` |
| `test_conversation_formatter.py` | `core/processors/conversation_formatter.py` |
| `test_entity_resolver.py` | `core/processors/entity_resolver.py` |
| `test_episode_clusterer.py` | `core/processors/episode_clusterer.py` |
| `test_human_like_formatter.py` | `core/processors/human_like_formatter.py` |
| `test_emotion_scorer.py` | `core/retrieval/emotion_scorer.py` |
| `test_json_parser.py` | `core/processors/json_parser.py` |
| `test_json_utils.py` | `core/utils/json_utils.py` |
| `test_llm_client.py` | `core/processors/llm_client.py` |
| `test_knowledge_extractor.py` | `core/processors/knowledge_extractor.py` |
| `test_knowledge_manager.py` | `core/managers/knowledge_manager.py` |
| `test_expression_pattern_learner.py` | `core/expression/pattern_learner.py` |
| `test_jargon_miner.py` | `core/jargon/jargon_miner.py` |
| `test_jargon_statistical_filter.py` | `core/jargon/statistical_filter.py` |
| `test_intent_keywords.py` | `core/retrieval/intent_keywords.py` |

### 安全与基础设施测试

| 测试文件 | 目标模块 |
|---------|---------|
| `test_guardrails.py` | `core/security/guardrails.py` |
| `test_cleaners.py` | `core/cleaners/` |
| `test_extractors.py` | `core/extractors/` |
| `test_dedup.py` | `core/dedup/` |
| `test_handlers.py` | `core/handlers/` |
| `test_base.py` | `core/base/` |
| `test_commands.py` | `core/commands/` |
| `test_command_handler.py` | `core/command_handler.py` |
| `test_command_endpoints.py` | `core/command_endpoints.py` |
| `test_event_handler.py` | `core/event_handler.py` |
| `test_feature_delegation.py` | `core/feature_delegation.py` |
| `test_i18n.py` | `core/i18n_backend.py` |
| `test_diagnostics_health_scorer.py` | `core/diagnostics/health_scorer.py` |
| `test_affection_manager.py` | `core/affection/` |
| `test_cache_manager.py` | `core/utils/cache_manager.py` |
| `test_decay_scheduler.py` | `core/schedulers/decay_scheduler.py` |
| `test_backfill_scheduler.py` | `core/schedulers/backfill_scheduler.py` |
| `test_integration_topic_segmentation.py` | 话题分割集成测试 |

### 集成测试 (`tests/integration/`)

| 测试文件 | 职责 |
|---------|------|
| `test_pipeline_event.py` | 事件处理流水线 smoke |
| `test_pipeline_graph.py` | 图记忆流水线 smoke |
| `test_pipeline_ingest.py` | 消息摄取流水线 smoke |
| `test_pipeline_lifecycle.py` | 记忆生命周期 smoke |
| `test_pipeline_retrieval.py` | 检索流水线 smoke |

### 检索评测 (`tests/evaluation/`)

| 测试文件 | 职责 |
|---------|------|
| `test_evaluation_service.py` | 评测服务 |
| `test_retrieval_quality.py` | 检索质量离线评测 (Recall@K, MRR, nDCG, latency) |

### 压力测试 (`tests/stress/`)

| 测试文件 | 职责 |
|---------|------|
| `test_concurrent_writes.py` | 并发写入压测 |

## 关键依赖与配置

- **pytest**: 主测试框架
- **pytest-asyncio**: 异步测试支持
- **unittest.mock**: AstrBot 框架 mock
- **pytest fixtures**: `conftest.py` 提供 test_db (内存 SQLite), test_config, mock_llm_caller

## 常见问题 (FAQ)

**Q: 添加新测试后如何运行？**
A: `python -m pytest tests/test_new_module.py -q`

**Q: 如何只运行特定标记的测试？**
A: 使用 `pytest -m "unit"` 或 `pytest -m "integration"` (需要 pytest.ini 中定义 markers)

**Q: 测试依赖真实 AI 服务吗？**
A: 不依赖。`conftest.py` mock 了 LLM 和 Embedding Provider，所有测试可在无网络环境下运行。

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 初始文档 | 生成 tests 模块级 CLAUDE.md |
