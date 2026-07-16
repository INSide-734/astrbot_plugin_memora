[根目录](../../CLAUDE.md) > [core](../) > **base**

## 模块职责

`core/base/` 是 Memora 插件的基础设施层，提供异常体系、配置管理（三层合并策略）、配置校验（Pydantic 模型）、注入常量等跨模块共享的基础组件。共 6 个文件（含 `__init__.py`）。

## 入口与启动

- **公开导出**: `core/base/__init__.py` -- 导出 `ConfigManager`，以及 `MemoraException`、`ConfigurationError`、`DatabaseError`、`InitializationError`、`MemoryProcessingError`、`ProviderNotReadyError`、`RetrievalError`、`ValidationError` 8 个异常类。

## 核心组件

### ConfigManager (`config_manager.py`, 342 lines)

**三层配置合并策略**的核心实现：

```
第 1 层: AstrBot 用户配置 + Pydantic 默认值合并 (merge_config_with_defaults)
第 2 层: 持久化 JSON 覆盖 (由 Dashboard 写入，_deep_merge)
第 3 层: Pydantic MemoraConfig 最终校验 (_validate_with_branch_fallback)
```

关键特性：
- **分支级降级回退**: 校验失败时自动探测无效配置分支，逐分支回退到默认值，而非全量回退
- **运行时配置热更新**: `update_runtime_config(updates, persist=True)` 支持点号键（如 `topic_segmentation.strategy`），校验后写入内存 + 持久化
- **持久化读写**: `save_persisted_config()` 异步写入，`_load_persisted_config()` 同步读取（在 `__init__` 中调用）
- **配置访问**: `get("key.subkey", default)` 支持点号分隔的嵌套键，`get_section("section")` 获取整个配置节
- **便捷属性**: `provider_settings`、`session_manager`、`recall_engine`、`reflection_engine`、`filtering_settings`、`graph_memory`

### config_validator.py (512 lines)

基于 **Pydantic v2** 的完整配置模型定义：

| 配置模型 | 用途 | 默认关键字段 |
|---------|------|------------|
| `SessionManagerConfig` | 会话生命周期 | max_sessions=100, session_ttl=3600, context_window_size=50, enable_full_group_capture=True |
| `RecallEngineConfig` | 记忆检索注入 | top_k=5, max_k=10, injection_routing_mode="manual", injection_manual_preset="balanced", injection_delivery_override="auto", search_cache_ttl=45s |
| `ReflectionEngineConfig` | 反思触发 | summary_trigger_rounds=10 |
| `AgentToolsConfig` | Agent 工具开关 | 15 个 boolean 开关（note_read/write 拆分，已废弃 note_tools 总开关） |
| `DashboardConfig` | 运行时构建 | allow_runtime_build=False, build_timeout=120s |
| `SecurityConfig` | Prompt 防护 | prompt_protection_enabled=True, guardrails_enabled=True, strict_mode=False |
| `ForgettingAgentConfig` | 自动清理 | cleanup_days=30, cleanup_importance_threshold=0.3 |
| `ImportanceDecayConfig` | 重要性衰减 | decay_rate=0.01/day, access_decay_window=30d |
| `GraphMemoryConfig` | 图记忆检索 | 文档路权重0.65/图路0.35, atom_enabled=True, 权重自动归一化 |
| `ProviderConfig` | AI 提供器 | embedding_provider_id, llm_provider_id |
| `IndexRebuildSettings` | 索引重建 | batch_size=50, embedding_batch_size=8, max_failure_ratio=0.02 |
| `TopicSegmentationConfig` | 话题分割 | strategy="a_b_hybrid", 策略B/C/D参数 |
| `MigrationSettings` | 数据库迁移 | auto_migrate=True, create_backup=True |
| `FilteringConfig` | 过滤策略 | use_persona_filtering=True, use_session_filtering=True |
| `FusionStrategyConfig` | 融合策略 | rrf_k=60 |

顶层 `MemoraConfig` 组合所有子模型，`model_config = {"extra": "allow"}` 向前兼容。

关键函数：
- `get_default_config()` -- `MemoraConfig().model_dump()` 生成完整默认配置
- `merge_config_with_defaults(user_config)` -- 深度合并用户配置与默认值
- `validate_config(raw_config)` -- 验证并返回 MemoraConfig 实例
- `validate_runtime_config_changes(current_config, changes)` -- 运行时更新预校验

### exceptions.py (125 lines)

完整的异常层次结构，所有异常继承自 `MemoraException(Exception)`，带 `error_code` 属性：

| 异常类 | error_code | 语义 |
|--------|-----------|------|
| `MemoraException` | `UNKNOWN_ERROR` | 基础异常 |
| `InitializationError` | `INIT_ERROR` | 组件初始化失败 |
| `ProviderNotReadyError` | `PROVIDER_NOT_READY` | LLM/Embedding Provider 不可用 |
| `DatabaseError` | `DATABASE_ERROR` | 数据库操作失败 |
| `RetrievalError` | `RETRIEVAL_ERROR` | 记忆检索失败 |
| `MemoryProcessingError` | `MEMORY_PROCESSING_ERROR` | 记忆抽取/处理失败 |
| `ConfigurationError` | `CONFIG_ERROR` | 配置加载/合并失败 |
| `ValidationError` | `VALIDATION_ERROR` | 数据校验失败 |
| `StorageError` | `STORAGE_ERROR` | 向量库/SQLite 读写失败 |
| `EmbeddingError` | `EMBEDDING_ERROR` | 嵌入生成失败 |
| `DecayError` | `DECAY_ERROR` | 衰减/调度失败 |
| `BackupError` | `BACKUP_ERROR` | 备份恢复失败 |
| `GraphError` | `GRAPH_ERROR` | 图数据库操作失败 |
| `TopicSplitError` | `TOPIC_SPLIT_ERROR` | 话题分割失败 |
| `IndexCorruptionError` | `INDEX_ERROR` | FAISS 索引损坏/重建失败 |
| `RecallInjectionError` | `RECALL_INJECTION_ERROR` | 记忆注入上下文失败 |
| `FeatureDelegationError` | `FEATURE_DELEGATION_ERROR` | 跨插件委托失败 |

### constants.py (12 lines)

记忆注入相关常量：

| 常量 | 值 | 用途 |
|------|----|------|
| `MEMORY_INJECTION_HEADER` | `<RAG-Faiss-Memory>` | 注入到 System Prompt 的记忆开头标记 |
| `MEMORY_INJECTION_FOOTER` | `</RAG-Faiss-Memory>` | 注入到 System Prompt 的记忆结尾标记 |
| `FAKE_TOOL_CALL_NAME` | `recall_long_term_memory` | 伪造工具调用名（复用已注册工具名） |
| `FAKE_TOOL_CALL_ID_PREFIX` | `fake_recall_` | 用于清理时识别的伪造消息 ID 前缀 |

### config_defaults.py (17 lines)

纯文档文件，说明配置默认值的**唯一运行时来源**是 `config_validator.py` 中的 Pydantic `Field(default=...)`。提醒更新配置键时同步修改三处：本文件（文档）、`config_validator.py`（Pydantic 默认值）、`_conf_schema.json`（Schema UI）。

## 关键依赖与配置

- **pydantic**: 配置模型定义与校验（`BaseModel`, `Field`, `model_validator`）
- **astrbot.api**: `logger` 日志接口
- 无外部持久化依赖；`ConfigManager` 的持久化路径通过 `persisted_config_path` 参数注入

## 数据模型

配置模型在 `config_validator.py` 中以 Pydantic `BaseModel` 形式定义（见上方表格）。异常模型在 `exceptions.py` 中为简单的异常类继承树。

## 测试与质量

相关测试文件（未在本模块目录内，属于 `tests/` 下）：
- `tests/test_base.py` -- 配置加载、异常测试
- `tests/test_config_manager.py` -- 三层合并、运行时更新、降级回退

## 常见问题 (FAQ)

**Q: 配置更新后不生效？**
A: 运行时更新应调用 `ConfigManager.update_runtime_config()`，该方法会经过 Pydantic 校验后同时更新内存配置和持久化 JSON。直接修改 `_conf_schema.json` 不会自动生效。

**Q: 三层合并的优先级？**
A: AstrBot 用户配置 < Pydantic 默认值 < 持久化 JSON (Dashboard)。即 Dashboard 写入的持久化配置优先级最高。

**Q: 配置校验失败会发生什么？**
A: 首先尝试分支级降级（自动探测无效分支并回退到默认值）。如果降级后仍失败，全部回退到默认值。降级详情记录在 `validation_errors` 属性中。

**Q: 如何添加新的配置键？**
A: 参见 `config_defaults.py` 中的说明：在 `config_validator.py` 中添加 Pydantic Field 定义（含 `default=...`），同步更新 `_conf_schema.json`（AstrBot UI Schema），并更新 `config_defaults.py` 文档。

## 相关文件清单

- `core/base/__init__.py` -- 公开导出
- `core/base/config_manager.py` -- 三层配置合并引擎
- `core/base/config_validator.py` -- Pydantic 配置模型 + 校验函数
- `core/base/config_defaults.py` -- 默认值参考文档
- `core/base/exceptions.py` -- 异常层次结构
- `core/base/constants.py` -- 注入常量

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 初始文档 | 完整读取 6 文件，生成 core/base/CLAUDE.md |
