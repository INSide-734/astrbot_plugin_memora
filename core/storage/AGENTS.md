[根级 AGENTS.md](../../AGENTS.md) > **core/storage**

# Storage 模块上下文

**最后更新：** 2026-07-21
**源码范围：** `core/storage/*.py` 与各 feature 自有持久化模块

## 职责与边界

`core/storage/` 是本地持久化层：以 `aiosqlite`/SQLite WAL 保存会话消息和注入决策遥测，并维护 FTS5 派生索引及与 FAISS 向量 ID 的关联。原子与图存储已归属 memory feature，用户画像、知识与笔记存储分别归属 profiles、knowledge 和 notes feature。业务编排位于 [`core/managers/AGENTS.md`](../managers/AGENTS.md)，召回算法位于 [`core/retrieval/AGENTS.md`](../retrieval/AGENTS.md)。

canonical Schema、Atom、幂等映射、来源校验、写日志、graph store 及共享连接实现已迁至 `core/features/memory/`；对应 `core/storage/` 路径仅保留单实现兼容导出，表名、数据目录和连接生命周期不变。

`feedback_signal_store.py` 是显式隔离路径的同步 SQLite Store；它保存最小反馈事件和可重建聚合，使用 dedupe 唯一约束与事务，不得默认连接 `memora.db`，也不得把事件 key/domain/query/正文写入安全摘要。生产可信适配器写入前必须把决策、作用域和人格转换为稳定不可逆 token；Manager 在写入/重建时物理清理超期事件，并只允许受控适配器按同一匿名决策撤销后重建聚合。

`core/features/memory/infrastructure/sql_contract.py` 集中保存跨 retrieval、managers、validators 与 social 复用的固定表名和静态 FTS SQL；`core/storage/sql_contract.py` 仅保留兼容导出。这些常量不接受运行时输入，调用方仍必须对值参数绑定，并在支持可替换标识符的入口保留白名单校验。

SQLite 不支持把表标识符作为绑定参数。`RelationStore` 因此只用 `SOCIAL_RELATIONS_TABLE` 校验固定表契约，实际执行语句始终包含静态 `social_relations` 标识符，不把运行时标识符插入 SQL；群组 ID、用户 ID、关系类型和排序选择继续通过值参数或固定 allowlist 处理。

## Memory Evolution 派生解释平面

`memory_evolution_store.py` 与 canonical `memora.db` 共用连接生命周期，但只保存可失效、可重建的演化数据：job/lease 队列、`memory_relations`、`memory_projections`、projection source mapping，以及独立的 `memory_derived_review_actions` 低敏复核审计。relation/projection 写入保留 source revision、`reference_at`/`discovered_at`/`invalid_at` 和时间来源/精度；source mapping 可保存自己的 occurred/valid 窗口。旧库由初始化迁移补列，缺失时间保持 NULL，不从 `updated_at` 推断事实时间。canonical memory 的整数 ID、正文和 revision 仍由主记忆表权威维护，Projection 不创建第二套 canonical ID。

- Job 写入必须使用 idempotency key，并保存入队时 `source_revisions_json`；claim/renew/retry/reject/complete/dead 状态转换同时校验 worker token，过期 lease 可恢复为 pending。旧库初始化时必须补齐 revision 列，不能删除已有 job。
- `apply_derived_plan()` 在一个事务内写入 relation、projection、mapping 和 source revision；source revision 变化、非法 seed、scope 不一致、坏 mapping 或不支持 role 必须隔离/失效，不能污染其他 bundle。
- `load_candidate_sources()` 必须在 SQL 限流前按 canonical metadata 的 scope 过滤，再保持 primary 首项；不得先截取全库最近 ID 后在 Python 中过滤，避免其他租户记录挤掉真实候选。
- `memory_relations.revision` 是高影响候选动作的 CAS 值，不是 canonical source revision。approve/replay 在同一写事务内重新验证两侧 source；reject 保持终态，普通 upsert 不得无审计重开。动作表只保存 action、前后状态、候选 revision、reason code 和时间，不保存 source ID/revision、scope、identity 或正文。
- `active_projection_bundles_for_seeds()` 先去重 seed，再批量读取 active projection 与完整 source mapping；支持 `scope_key` 和 projection limit，禁止 N+1 source 查询。读取侧再核对 primary/supporting/conflict role、revision、privacy 和 validity。
- 普通非冲突 Projection 可在 supporting source 失效且 primary 仍有效时保留；`semantic_summary` 的正文合成自全部 mappings，因此任一来源 revision 变化、删除或 orphan cleanup 移除 mapping 时必须整条失效，等待从当前 canonical revision 幂等重建。
- SQL 动态片段仅来自固定 allowlist，值全部参数绑定；读取失败由上层回退 canonical/relation baseline，不把 query、prompt、正文、source ID 列表或身份写入日志/决策记录。

```mermaid
graph TD
    Managers[Managers / API] --> PoolBase[base.BaseStore + ConnectionPool]
    Managers --> InstanceBase[base_store.BaseStore]
    PoolBase --> Atom[AtomStore]
    PoolBase --> Graph[GraphStore]
    PoolBase --> Knowledge[features.knowledge.KnowledgeStore]
    PoolBase --> Note[features.notes.NoteStore]
    PoolBase --> Profile[features.profiles.ProfileStore]
    InstanceBase --> Decision[InjectionDecisionStore]
    Conversation[ConversationStore] --> Messages[(sessions + messages)]
    Atom --> AtomFTS[(memory_atoms_fts)]
    Graph --> GraphFTS[(memora_graph_entries_fts)]
    Graph --> VectorLink[vector_doc_id → graph FAISS]
    Decision --> Telemetry[(injection_decisions)]
```

## 连接模型与并发

### `core/features/memory/infrastructure/base.py`

- `apply_perf_pragmas()` 是共享 SQLite 设置的单一事实来源：`foreign_keys=ON`、`journal_mode=WAL`、`synchronous=NORMAL`、`busy_timeout=30000`、64 MB page cache、内存临时表、256 MB mmap。
- `ConnectionPool` 用 `asyncio.Queue` 管理固定数量连接；`base.BaseStore._connect()` 优先借池连接，否则创建一次性连接。
- 所有 JSON 解析必须经安全助手并验证容器类型；数据库里的 JSON 字符串不天然可信。

### `base_store.py`

- 这是另一种 per-instance 持久连接基类，供 `InjectionDecisionStore` 使用；`initialize()` 建连接并建表，`close()` 显式释放。
- `_insert_many()`、`_delete_where()` 等会动态拼接表/列名，只能传内部常量。不要把 API 输入直接传给这些助手。
- 两个同名 `BaseStore` 不可互换；导入时必须明确来源。

`ConversationStore` 维护自己的持久连接和 `_write_lock`。其他领域 Store 多数通过短连接上下文执行事务。WAL 提升并发读写，但不消除 SQLite 单写者约束；跨多条相关写入必须在同一连接和一次 commit 内完成。

## Schema 与 Store

### 记忆原子：`core/features/memory/infrastructure/AtomStore`

`memory_atoms` 保存 `parent_memory_id`、创建时 `parent_revision`/`parent_scope_key`/`parent_privacy_level`、类型、正文、实体 JSON、重要性/置信度、时间、TTL、状态、强化次数、衰减类型、session/persona 和 metadata；`memory_atoms_fts` 是 `content, atom_id UNINDEXED` 的 FTS5 表。三项父来源列缺失的旧行只可维护，读取时不主动召回。

```mermaid
stateDiagram-v2
    [*] --> active
    active --> expired: expires_at 经过
    expired --> forgotten: 超过遗忘阈值并移出 FTS
    forgotten --> [*]: 物理清理
    active --> cold: 低重要性且长期未访问
```

- 单条 `insert()` 在一个事务内写主表和 FTS；`insert_many()` 每 500 条形成独立事务，单批失败 rollback 并重置已准备对象的 `atom_id`。
- `forget_expired_atoms()`、`cleanup_forgotten()`、按父 ID 删除必须同步主表与 FTS。
- 当同一数据库存在 `documents` canonical 表时，`insert()`/`insert_many()` 会在事务内拒绝缺失、孤儿、revision、scope 或 privacy 不匹配的父来源；批量任一失败整体回滚。无 canonical 表的纯 Store 测试库保留旧兼容路径。
- `filter_current_sources()` 与 `query_upcoming_planned()` 在 FTS/前瞻读取后重新核对父 source；普通校验失败只丢弃 Atom，取消异常继续传播。前瞻群聊额外排除 `confidential` 父来源。
- FTS 搜索使用参数绑定；查询 token 中的引号会转义，session/persona/status 作为参数化过滤。普通搜索只返回 active；cold 不参加常规 FTS。

### 图记忆：`core/features/memory/graph/infrastructure/GraphStore` 与四个 mixin

| 表 | 作用 |
|---|---|
| `graph_nodes` | 规范化实体节点，`node_key` 唯一 |
| `graph_edges` | 节点关系、源记忆、权重、置信度与状态 |
| `graph_entries` | 可搜索图条目、源记忆、scope、`vector_doc_id` |
| `graph_entry_nodes` | 条目与节点多对多关系 |
| `memora_graph_entries_fts` | 图条目全文索引 |

- `GraphCRUDMixin` 在单连接事务内批量 upsert 节点/边/条目；跨记忆同语义边按实现规则合并。
- `GraphDeleteMixin` 删除 entries、FTS、entry-node、edges，并清孤儿节点；返回 `vector_doc_id` 给上层删除图 FAISS。SQLite commit 与 FAISS 删除不是同一事务，必须由 `GraphMemoryManager`/写日志补偿。
- `GraphQueryMixin` 的 scope 值参数化；节点 token 查询和批量 `IN` 占位符由内部数量生成。
- `GraphSubgraphMixin` 构建前端快照并限制 memories/entries/nodes/edges；其输出仍可能暴露实体关系和正文片段，不是公开匿名数据。

### 会话与消息：`ConversationStore`

- `sessions` 保存 `session_id`、platform、活跃时间、消息计数、participants JSON 和 metadata JSON。
- `messages` 保存 role、content、sender/group/platform、timestamp 和 metadata，并关联 session。
- `add_message()` 在 `_write_lock` 内写消息、upsert session、更新计数和参与者后一次 commit。
- `trim_session_messages()` 只能删除已经总结的最旧消息；`sync_message_counts()` 修复 sessions 计数。任何范围查询都应以实际消息数为准。

### 用户画像：`core/features/profiles/infrastructure/ProfileStore`

`user_profiles.user_id` 唯一；`user_tags` 对 `(user_id, category, value)` 唯一并以外键关联画像。

- 严格创建、管理员字段替换、带 revision 删除、偏好合并、标签 upsert、消息计数和标签衰减都使用 `BEGIN IMMEDIATE`。
- `create_profile_strict()`、`replace_editable_fields()`、`update_profile_fields_atomic()` 与 `update_profile()` 是人工维护入口，必须拒绝带 `derived` provenance 的 `UserPreferences`，避免绕过 ProfileManager 的 source 校验；自动偏好只能通过带当前 canonical source 的受控合并入口写入。
- 管理员编辑以 `compute_entity_revision()` 做乐观并发检查，冲突抛 `EditConflictError`；不要把旧画像快照整行写回。
- `_rollback_safely()` 保证回滚清理错误不覆盖原异常。

### 知识：`core/features/knowledge/infrastructure/KnowledgeStore`；笔记：`core/features/notes/infrastructure/NoteStore`

- `knowledge_entries`：title/content/category/confidence/source_ids/tags、时间、过期和访问计数，并以 `origin`/`provenance_json` 区分人工与派生；搜索是参数化 `LIKE`，不是 FTS。派生写入和读取会重新校验 source revision/scope/privacy。
- `notes` + `note_versions`：创建时同事务写 v1；更新以 `WHERE id=? AND version=?` 乐观锁，成功后插入下一版本；notes 以 `origin`/`provenance_json` 保存派生来源，primary 仍有效时 supporting source 可在读取投影中裁剪。自动派生创建在 `BEGIN IMMEDIATE` 中按完整 provenance 幂等命中当前记录，不更新人工笔记或已有版本。
- `idx_note_versions_note_version` 对 `(note_id, version)` 唯一；健康检查仍检测旧数据或损坏造成的重复版本。
- `soft_delete()` 仅改状态；`delete()` 同事务删版本和主笔记；`prune_versions()` 按创建时间保留最新 N 个。

### 实体层级：`EntityHierarchyStore`

`entity_hierarchy(child,parent)` 唯一；添加前从 parent 向上搜索，拒绝自环和会形成的环。它复用 `MemoryEngine` 主连接，不自持连接。

### 注入决策遥测：`InjectionDecisionStore`

该 Store 使用显式安全 schema，只保存决策 ID、时间、trace ID、路由/预设/交付结果、reason code、provider 类型/模型、计数、字符预算与耗时；**不保存提示词、候选正文、最终注入正文、session/user/persona ID 或 provider 凭据**。

- `DecisionQuery` 限制 `offset >= 0`、`1 <= limit <= 100` 和时间范围顺序；所有过滤参数绑定。
- `insert_many()` 在全局 `write_transaction` 中原子批量插入，重复 `decision_id` 忽略，异常 rollback。
- 列表故意排除 `reason_codes_json`，详情按 opaque `decision_id` 获取并解码。
- `cleanup()` 先按 retention 删除，再按 `(created_at_ms DESC, decision_id DESC)` 稳定保留 newest `max_rows`，同一事务提交。
- provider 型号和错误码仍可能泄露部署信息，API 层必须授权；“无正文”不代表可公开。

### 再巩固候选：`ReconsolidationStore`

`reconsolidation_candidates` 保存 source revision、旧正文与 proposed 正文，`reconsolidation_actions` 记录 stage/apply/reject/rollback 低敏审计；状态迁移使用 CAS，应用与回滚必须携带 `expected_revision`。`reconsolidation_apply_ops` 在 canonical apply 前保存唯一 intent，成功、明确失败和重启恢复都必须在同一事务内收口候选与 intent；普通 reject 不得越过进行中的 apply。`reconsolidation_rollback_ops` 在 canonical 更新前保存回滚意图；完成时必须在同一事务内迁移候选、追加动作并删除操作。启动恢复只重放 revision 未变或正文已经等于目标正文/旧正文的 pending 操作，冲突操作保持 blocked/failed，不能覆盖后续编辑。

### Memory Evolution 能力快照

`MemoryEvolutionStore` 显式声明原生 filtering、batch write、update 和 delete；取消传播由调用层保证。该快照只描述当前 Store 的稳定入口，不改变 canonical SQLite 权威、source revision 校验或派生对象可重建边界，也不得暴露 source mapping、revision、scope、内部 ID 或 job 信息。

## 数据与依赖方向

```mermaid
flowchart LR
    Documents[(documents 主数据)] --> BM25[(memora_memories_fts)]
    Documents --> MainFAISS[(主 FAISS)]
    Documents --> Atoms[(memory_atoms + FTS)]
    Documents --> Graph[(graph_* + graph FTS)]
    Graph --> GraphFAISS[(图 FAISS)]
    Sessions[(sessions)] --> Messages[(messages)]
    Notes[(notes)] --> Versions[(note_versions)]
    Profiles[(user_profiles)] --> Tags[(user_tags)]
```

`documents` 和主 BM25 表由管理/检索初始化路径拥有，但它们是持久化一致性图的一部分。`core/storage` 不应导入 handler、scheduler 或页面 API；上层可以依赖 Store，Store 不回调领域编排。

## 安全与不可泄露数据边界

1. `content`、message、atom、graph entry、profile、tag、session/persona/user ID、participants 与全部 metadata 都按敏感用户数据处理。
2. SQL 值一律参数绑定；动态表名/列名必须来自封闭白名单。FTS 查询语法不是普通字符串，必须保留 token 转义。
3. 不在日志中记录 SQL 参数、正文、完整 metadata、用户标识或数据库绝对路径。异常对外只给稳定错误码，内部 traceback 也要避免拼接敏感值。
4. SQLite 文件、WAL/SHM、FAISS 文件、备份和 trace DB 具有相同保密级别；文件权限和下载授权由调用层强制。
5. 删除主数据时必须同步 FTS 与向量引用；不能仅删 SQLite 行而留下可召回的 FAISS/FTS 内容。
6. JSON 列读取失败应返回安全空容器或显式错误；禁止反序列化任意 Python 对象。

## 异常与一致性约束

- 未初始化持久连接的写操作应抛 `RuntimeError`；不要把它转换为“写入 0 条”的成功。
- 批量事务失败必须 rollback；`CancelledError` 不得吞掉，否则会留下未知提交状态。
- `NoteStore.update()` 返回 `False` 表示版本冲突或不存在；调用方必须重新读取而不是重试旧对象。
- 图/原子/FTS 是派生索引，发现孤儿或缺失时交给 [`core/validators/AGENTS.md`](../validators/AGENTS.md) 检测与重建，不在查询时静默伪造数据。
- `foreign_keys=ON` 只约束声明了外键的表；`memory_atoms.parent_memory_id`、部分图源引用仍需健康检查维护。

## 测试定位与精确验证

```bash
python -m pytest -q tests/test_storage_base.py tests/test_storage_builder.py
python -m pytest -q tests/test_atom_store.py tests/test_atom_fts.py
python -m pytest -q tests/test_graph_store.py tests/test_graph_crud.py tests/test_graph_query.py tests/test_graph_delete.py tests/test_graph_subgraph.py
python -m pytest -q tests/test_conversation_store.py tests/test_message_store.py tests/test_message_queries.py
python -m pytest -q tests/test_profile_store.py tests/test_knowledge_store.py tests/test_note_store.py tests/test_hierarchy_store.py
python -m pytest -q tests/test_injection_decision_store.py
python -m pytest -q tests/test_memory_evolution_store.py tests/test_projection_reader.py
python -m pytest -q tests/test_adapter_capabilities.py
python -m pytest -q tests/test_temporal_semantics.py
python -m pytest -q tests/integration/test_pipeline_graph.py tests/stress/test_concurrent_writes.py
```

本次文档迁移不运行测试。Schema、事务或删除语义变化时，除同名 Store 测试外还必须运行 `tests/test_validators.py`，因为健康检查编码了跨表不变量。

## 改动守则

- Schema 新字段必须同步建表、旧库迁移、row mapper、写入参数、测试和健康检查；仅改 dataclass 不算完成。
- 新增派生索引必须同时设计增删改同步、崩溃修复和重建路径。
- 保持批量上限（原子/图常为 500）；不要为减少 commit 次数制造超大锁定事务。
- 包级 `__init__.py` 只暴露稳定公共 Store/DTO；新增导出前检查调用方是否应依赖具体实现。
- 不要把两个 `BaseStore` 合并或互换，除非完整迁移连接生命周期与所有调用方。
