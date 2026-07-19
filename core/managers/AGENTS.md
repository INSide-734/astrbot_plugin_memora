[根级 AGENTS.md](../../AGENTS.md) > **core/managers**

# Managers 模块上下文

**最后更新：** 2026-07-19
**源码范围：** `core/managers/*.py`（40 个 Python 文件）

## 职责与边界

`core/managers/` 是业务生命周期与编排层。它把 SQLite 文档表、BM25、FAISS、记忆原子和图记忆组合成统一的 `MemoryEngine`，并提供会话、画像、知识、笔记、备份、导入导出、衰减、写故障恢复及 Memory Evolution 后台演化服务。

本层负责“何时、按什么顺序、失败后如何补偿”；底层表 CRUD 属于 [`core/storage/AGENTS.md`](../storage/AGENTS.md)，候选召回和排序属于 [`core/retrieval/AGENTS.md`](../retrieval/AGENTS.md)，定时触发属于 [`core/schedulers/AGENTS.md`](../schedulers/AGENTS.md)。Memory Evolution 的关系/Projection 事务和 revision 校验由 manager 编排，具体 SQLite 表访问仍属于 storage。

```mermaid
graph TD
    Caller[插件/API/处理器] --> Engine[MemoryEngine]
    Engine --> Lifecycle[MemoryEngineLifecycleMixin]
    Engine --> CRUD[MemoryEngineCRUDMixin]
    Engine --> Batch[MemoryEngineBatchMixin]
    Engine --> Retrieval[RetrievalOptimizer]
    Engine --> Journal[WriteOpJournal]
    Engine --> Maintenance[MaintenanceOperations]
    Engine --> Schema[SchemaManager]
    CRUD --> Hybrid[retrieval.HybridRetriever]
    CRUD --> GraphMgr[GraphMemoryManager]
    CRUD --> AtomStore[storage.AtomStore]
    Maintenance --> SQLite[(SQLite documents)]
    Hybrid --> BM25[(SQLite FTS5)]
    Hybrid --> FAISS[(FAISS)]
    Journal --> Repair[WriteOpRepairMixin]
    Engine --> Evolution[MemoryEvolutionManager]
    Evolution --> Gate[MemoryEvolutionGate]
    Evolution --> Worker[单 worker / lease / retry]
    Evolution --> Derived[Relation / Projection 派生表]
```

## 关键入口与公开接口

### `MemoryEngine`

`memory_engine.py` 通过 `MemoryEngineLifecycleMixin`、`MemoryEngineCRUDMixin`、`MemoryEngineBatchMixin` 组装主入口；包级 `__init__.py` 还导出 `ConversationManager`、`GraphMemoryManager` 和 `create_conversation_manager`。

| 入口 | 语义 |
|---|---|
| `initialize()` / `close()` | 打开/关闭 SQLite 与图向量库，创建索引组件和可选子系统，追踪并取消后台任务 |
| `add_memory(...) -> int` | 写文档/向量、BM25、原子、图产物并记录可恢复写日志 |
| `search_memories(...)` | 经缓存、双路/混合检索、触发词、情绪/季节和链式扩展返回 `HybridResult` |
| `update_memory(...) -> bool` | 元数据原地更新；内容更新采用“新建后删除旧项”，删除失败则删除新项补偿 |
| `delete_memory(...) -> bool` | 先删文档索引，再清理图和原子；子资源失败进入修复队列 |
| `batch_delete_memories[_detailed]()` | 每 200 个 ID 分批删除并返回计数/失败明细 |
| `apply_daily_decay()` / `cleanup_old_memories()` | 重要性衰减和 `ACTIVE → DORMANT → ARCHIVED → 物理删除` |
| `maintain_storage()` / `rebuild_graph_index()` | 存储维护和图产物重建 |

### 生命周期初始化顺序

`memory_engine_lifecycle.py` 的实际顺序是：

1. `aiosqlite.connect`，设置 `Row` 与共享 PRAGMA，注册 `ConnectionRegistry`。
2. `SchemaManager.create_tables()`，同时创建 `memory_write_ops`。
3. 构建 `TextProcessor → BM25Retriever → VectorRetriever → HybridRetriever`。
4. 仅在 `graph_enabled` 且存在 `graph_vector_db` 时构建 `GraphStore`、`AtomStore`、层级存储、图双路检索和 `GraphMemoryManager`。
5. 可选执行 `WriteOpJournal.repair_incomplete()`。
6. 按配置构建画像、知识、笔记、自动学习、性格追踪、重排序器等；高成本 `llm`/`hybrid` 重排可能由成本控制降级为 `mmr`。
7. 图路可用时构建 `DualRouteRetriever`，最后创建 `RealtimeSSE`。
8. 若注入了 `projection_reader`，`MemoryEngine` 只把它作为召回阶段的派生注解读取器；它不改变 canonical 写入和整数 `doc_id` 语义。

`close()` 先停原子维护，再保存部分子系统状态、取消 `_pending_tasks`，最后关闭 SQLite 与图向量库。新增后台协程必须走 `_create_tracked_task()`，不得裸建后失去生命周期控制。

## 写入数据链与恢复语义

```mermaid
sequenceDiagram
    participant C as Caller
    participant E as MemoryEngine
    participant J as WriteOpJournal
    participant H as HybridRetriever
    participant A as AtomStore
    participant G as GraphMemoryManager
    C->>E: add_memory(content, metadata, atoms)
    E->>J: start_op(add, repair payload)
    E->>H: add_memory
    H-->>E: doc_id
    E->>J: document_indexed
    E->>A: insert_many (可选)
    E->>G: index_memory (可选)
    alt 所有阶段成功
        E->>J: completed
    else 原子或图阶段部分失败
        E->>J: needs_repair + failed payload
    end
    E-->>C: doc_id
```

- 这不是跨 SQLite/FAISS 的单一 ACID 事务。`memory_write_ops` 是跨存储 saga 日志；`repair_incomplete()` 尽力重放 `pending`/`needs_repair` 的 add、delete、batch delete 和 graph reindex。
- `add_memory()` 在日志中保存最多 500 字符的 `content_preview`、完整 metadata 和可修复原子载荷；它们都可能包含用户数据。
- 原子批量失败后逐条补写，仅仍失败的原子进入修复载荷。图失败不撤销已建文档，而是标记修复。
- 删除先调用 `HybridRetriever.delete_memory()`；随后图或原子清理失败不会把主删除改成失败，但日志保留 `needs_repair`。
- `WriteOpJournal.start_op()` 失败时可能返回 `None`；业务路径仍继续，因此不能把日志存在等同于事务已保证。

## Memory Evolution 生命周期与安全边界

`memory_evolution_gate.py`、`memory_evolution_manager.py` 负责 canonical 写入后的派生演化，不替代 `MemoryEngine` 的主写路径：

- `MemoryEvolutionGate` 仅基于 source revision、scope、topic/entity 信号、阈值、去抖桶和待处理上限生成稳定 idempotency key；`enabled=false` 或非法 mode 必须返回 `mode_disabled`。
- `MemoryEvolutionManager.schedule_consider()` 只在 canonical 写入成功后入队。worker 以单任务循环领取 job，持有可续租 lease；取消会恢复 pending，普通异常按指数退避重试，超过 `max_attempts` 进入 dead，proposal 规则拒绝进入 rejected。
- 处理 proposal 时必须先读取 source，再在应用前重新读取并比较每个 source 的 revision；source 缺失、scope 不一致、alias 未知、自关系、重复/成环边、冲突 Projection 少于三类角色均拒绝，不能污染派生表。
- 关系按低/高影响分类：低影响且达到阈值的允许按配置自动 `active`，高影响默认 `candidate` 并要求复核；Projection 共享 scope，privacy 取所有 source 中最严格值，状态由置信度和冲突类型决定。
- Relation/Projection 是 SQLite 中的派生解释平面；稳定 ID 由 source memory ID、revision 和类型计算，但不创建第二套 canonical memory 或向量索引。更新/删除 canonical 后由 Store 的 revision invalidation 隔离旧派生结果。
- `get_status_snapshot()` 只能返回模式、计数、reason code 和延迟桶等 allowlist 标量；不得把 query、prompt、正文、原始身份、source ID 列表或 provider 信息写入日志/指标。

## SQLite、事务与并发约束

- `write_coordinator.py` 的模块级 `asyncio.Lock` 串行化协调写入；锁冲突可指数退避并加随机抖动，连接坏死由 `ConnectionRegistry` 重连。
- `coordinated_transaction()` 使用 `BEGIN IMMEDIATE`，异常必须 rollback，取消也必须继续上抛。
- `SchemaManager` 只对白名单 `doc_id`、`created_at`、`updated_at` 做动态列迁移，并安全引用标识符；动态 SQL 不得接收未白名单化的外部表/列名。
- `documents` 是校验与重建的源数据表；BM25、FAISS、图和原子都是需要同步或可修复的派生产物。
- 维护批次和批量删除是有意分块的；不要改成超大事务，也不要在持锁区执行 LLM/Embedding 网络调用。

## 子系统索引

| 区域 | 文件 | 事实边界 |
|---|---|---|
| 会话 | `conversation_manager.py` 及 6 个 mixin | `ConversationStore` 上层 LRU、上下文窗口、事件适配和元数据；缓存由 `_cache_lock` 保护 |
| 图同步 | `graph_memory_manager.py` | 删除旧图产物后重建节点/边/条目与图向量；向量 ID 最终回写 SQLite |
| 原子生命周期 | `atom_lifecycle_manager.py` | 周期过期/遗忘/冷迁移，同批原子 Jaccard 去重；后台任务由 `start/stop` 管理 |
| 维护 | `decay_operations.py`、`lifecycle_operations.py`、`stats_operations.py` | 衰减、分层遗忘、统计、存储与图索引维护 |
| 画像 | `profile_manager.py` | 管理员编辑使用修订值冲突检测；自动标签与偏好走存储层原子事务 |
| 知识/笔记 | `knowledge_manager.py`、`note_manager.py` | 知识去重与过期；笔记 CRUD、软删和版本裁剪 |
| 可靠性 | `write_coordinator.py`、`write_op_*` | SQLite 写串行化、重试、跨存储操作日志和崩溃修复 |
| 记忆演化 | `memory_evolution_gate.py`、`memory_evolution_manager.py` | canonical 写后门控、单 worker、lease/retry/dead/cancel、关系与 Projection 计划校验及原子应用 |
| 文件状态 | `auto_learning.py`、`anomaly_detector.py`、`continuity_tracker.py`、`relationship_tracker.py`、`trait_evolution.py`、`weight_learner.py` | JSON 状态属于运行数据，不是配置；加载失败通常降级为空状态 |
| 备份 | `backup_manager.py`、`backup_models.py`、`backup_snapshot.py` | SQLite 使用 Online Backup API；manifest 保存角色、大小、SHA-256 和 quick check；新恢复使用 `.restore/<operation_id>/restore_plan.json`、`payload/`、`previous/` 事务目录 |
| 导入导出 | `memory_exporter.py` | JSONL/Markdown 包含正文与 metadata；导入按内容 SHA-256 短哈希去重后重新走 `add_memory` |

## 安全与不可泄露数据边界

1. **记忆正文、会话 ID、人设 ID、用户画像、消息、情绪标签和 metadata 均为敏感数据。** 不得写入普通日志、指标标签、异常字符串或对外追踪；当前少数日志含内容前 60 字符，新增代码不得扩大泄露面。
2. `MemoryExporter` 会明文写出完整正文和 metadata，且接受调用方给定路径；调用方必须完成授权、路径约束和文件权限控制。导出文件不可当作无敏感数据的调试附件。
3. `MemoryImporter` 输入不可信：JSON 结构、metadata、importance 和目标 session/persona 必须在调用边界验证；导入失败不得回显完整正文。
4. `BackupManager.validate_backup_name()`、备案目录集合和 `relative_to(backups_root)` 共同阻止路径穿越；备份源、manifest 和恢复 payload 还必须拒绝符号链接、绝对路径、分隔符及白名单外文件。不可绕过这些 API 直接拼接路径。
5. 写日志中的修复载荷和备份目录具有与原始记忆相同的保密级别。不得通过 SSE、诊断 API 或导出默认暴露。
6. LLM 再巩固和高成本重排会把记忆内容送给配置的 provider；只有在用户授权且 provider 数据策略允许时启用。
7. Memory Evolution 的 proposal 输入是受长度限制的不可信 evidence；模型输出先由 processor 结构校验，manager 再做 source revision、scope/privacy、role 和影响级别校验。任何 source 证据不得进入模型可见的 Projection metadata。

## 异常规则

- `asyncio.CancelledError` 必须重新抛出；普通检索增强、画像排序、可选维护可降级，但持久化主写失败必须显式失败或进入 `needs_repair`。
- 内容为空：`add_memory()` 抛 `ValueError`；未初始化核心检索器：抛/返回失败，不能静默写半套数据。
- 内容更新是新 ID 替换旧 ID，调用方不得假定 `memory_id` 永久不变。
- `cleanup_old_memories()`、可选管理器和状态文件通常采用尽力而为语义；返回 0/空结果不等于数据一致性已验证。
- `BackupManager` 只在 canonical SQLite 快照、manifest 和 quick check 全部成功后发布 `ready` 备份；失败不得发布半成品。`scheduled` 与 `pre_restore` 才允许自动 prune，`manual` 和 `version_change` 必须显式删除。

## 测试定位与精确验证

按修改范围选择最小命令；本模块文档初始化不执行测试。

```bash
python -m pytest -q tests/test_managers_memory_engine.py tests/test_managers_memory_lifecycle.py tests/test_managers_memory_crud.py tests/test_managers_memory_batch.py
python -m pytest -q tests/test_managers_write_coordinator.py tests/test_managers_write_journal.py tests/test_managers_write_serial.py
python -m pytest -q tests/test_managers_decay.py tests/test_managers_lifecycle.py tests/test_managers_stats.py tests/test_managers_schema.py
python -m pytest -q tests/test_managers_conversation.py tests/test_managers_message.py tests/test_managers_session.py tests/test_managers_range.py tests/test_managers_event.py tests/test_managers_sender.py
python -m pytest -q tests/test_managers_backup.py tests/test_managers_export.py tests/test_managers_profile.py tests/test_managers_retrieval.py
python -m pytest -q tests/test_memory_evolution_gate.py tests/test_memory_evolution_manager.py
python -m pytest -q tests/integration/test_pipeline_lifecycle.py tests/stress/test_concurrent_writes.py
```

画像、知识、笔记、图、自动学习等子系统各有对应 `tests/test_managers_*.py`；修改单个文件时先运行同名测试，再运行上方主引擎与写可靠性组合。

## 依赖方向与改动守则

- 允许：`managers → models/processors/retrieval/storage/utils/base/api`。
- 禁止让 `storage` 反向依赖领域管理器；当前 `InjectionDecisionStore` 仅在方法内延迟导入通用 `write_transaction`，不要扩展为领域回调。
- `schedulers` 调用 managers，managers 不应反向持有 scheduler。
- 新增存储阶段必须同时更新写日志步骤、修复重放、删除/批删和相关测试；只改 happy path 会留下孤儿数据。
- 新增用户可编辑实体字段时，必须同步修订值计算、验证、原子替换和冲突响应，不能用旧快照覆盖并发更新。
- 不要把可选子系统失败升级成插件启动失败，除非该子系统已成为主数据正确性的必要条件。
- 新增或修改 Memory Evolution 阶段时，必须同时更新 gate reason、lease/retry/dead/cancel 计数、revision invalidation 和 manager 关闭顺序；不能只覆盖 worker 的 happy path。
