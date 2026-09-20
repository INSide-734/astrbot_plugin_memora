[根级 AGENTS.md](../../../AGENTS.md) > **core/features/memory**

# Memory 模块上下文

**最后更新：** 2026-09-20
**源码范围：** `core/features/memory/`（MemoryEngine 门面、canonical/graph 基础设施与验证器）

## 职责与边界

`core/features/memory/` 是记忆门面的业务生命周期与编排层。它把 SQLite 文档表、BM25、FAISS、记忆原子和图记忆组合成统一的 `MemoryEngine`，并提供会话、备份、导入导出、衰减与写故障恢复。

用户画像的领域服务与 proposal 管线唯一归属 `core/features/profiles/application/`；`core/features/memory/` 只保留 `MemoryEngine` 的画像写后钩子，不再转发画像应用类型。
知识领域服务与 proposal 管线唯一归属 `core/features/knowledge/application/`；`core/features/memory/` 只保留 `MemoryEngine` 的知识写后钩子，不再转发知识应用类型。
笔记领域服务与 proposal 管线唯一归属 `core/features/notes/application/`；`core/features/memory/` 只保留 `MemoryEngine` 的笔记写后钩子，不再转发笔记应用类型。
自主学习与反馈聚合唯一归属 `core/features/learning/`：application 管理可信反馈聚合、shadow 候选和单一 CAS 发布，domain 保存候选与反馈模型，infrastructure 保存隔离事件、状态和配置适配；`core/features/memory/` 只在 `MemoryEngine` 生命周期中装配和持有这些组件，不再转发 Learning 类型。自主学习不得直接修改生产检索权重或调用 `update_memory()`。
Memory Evolution 的 Gate、候选生成、LLM proposal、worker、Projection 应用与语义压缩唯一归属 `core/features/evolution/application/`，Store 唯一归属 `core/features/evolution/infrastructure/`；`core/features/memory/` 只保留 MemoryEngine 写后钩子，不再转发 Evolution 应用类型。

本层负责“何时、按什么顺序、失败后如何补偿”；canonical/graph/索引表 CRUD 位于本 feature infrastructure，候选召回和排序属于 [`features/retrieval`](../retrieval/AGENTS.md)，定时维护由 [`features/decay`](../decay/AGENTS.md) 与 [`features/backfill`](../backfill/AGENTS.md) 触发。Memory Evolution 的关系/Projection 事务与 revision 校验由 evolution feature 编排。

### 跨窗口近重复合并

`application/canonical_merge.py` 把反思候选并入同 scope 的既有 canonical（B5）：命中由 [`quality`](../quality/AGENTS.md) 的 `near_duplicate_detector` 判定，合并只做 reinforce——`importance` 取 max、`source_refs`/`topics` 并集去重（限 32/5）、逐事实 `fact_source_evidence` 按规范化事实键合并（每条事实限 32 条来源）、`merge_count`+1、`last_merged_at`、`merged_idempotency_keys`（限 16，重放短路），**正文永不改写**；候选的摘要级 `source_evidence` 不并入既有记录，避免用未合并正文的证据扩大摘要归属，revision 推进沿用 `update_memory` 的既有语义（含乐观校验、派生失效...

- 生产端口：`build_recent_document_search` 用 `documents` 表已有的 `json_extract(metadata, '$.session_id')` 索引做 `ORDER BY id DESC LIMIT N` 有界查询，scope/privacy/主体过滤留在 Python 侧，不新增索引；`load_memory`/`update_memory` 复用 `MemoryEngine` 既有方法。
- 并发：进程内按 `session + scope_key` 的 `asyncio.Lock` 串行化「检测 → 合并」，覆盖总结窗口的候选并发；跨进程并发不在支持范围（单实例单 DB）。
- 失败语义：检测异常、目标正文在检测后被改写、CAS 冲突、写回异常一律 fail-open，由调用方回落普通 canonical 写入；写回返回 False 时以回读 `merged_idempotency_keys`/`merge_count` 判定是否已提交，避免在已合并的情况下插入重复 canonical。既有 owner 的 `key_facts`/`fact_source_evidence` 与自身正文不对齐（`facts_aligned` 非 `aligned`）时不允许强化合并，按冲突记为 `dedup_merge_conflict` 并回落新增，避免用错误事实强化旧记录。
- 默认关闭：`memory_dedup.mode=off` 不发起任何近重复查询；`observe` 只记录 `dedup_observed`；`enforce` 才写回。
- 语义扩展（默认关闭）：`memory_dedup.semantic_mode=off|observe|enforce` 与 `semantic_threshold`（默认 0.9）只在 lexical MISS/FACT_OVERLAP 之后调用 `quality` 的 `semantic_duplicate_detector` 窄端口，窗口键取候选的 `source_digest`（缺省退化为幂等键），`SemanticRequestBudget` 按窗口固定 8 次；候选必须回读 canonical 并通过作用域/可召回/长度/事实/用户来源证据护栏，`observe` 只记录、`enforce` 复用同一 owner CAS 写回。端口缺失、预算耗尽与 provider 异常都 fail-open 回落普通写入，语义 outcome 只记入 `semantic_observe`/`semantic_enforce` 模式。
- 模块结构：纯 metadata 规范化/并集/幂等键助手在 `application/canonical_merge_metadata.py`（`canonical_merge.py` 继续 re-export `MAX_SOURCE_EVIDENCE`/`MAX_MERGED_IDEMPOTENCY_KEYS`），协调器只保留检测、终态分派与写回。
- 观测：`infrastructure/dedup_metrics_store.py` 是独立 SQLite 小时桶聚合 `dedup_metrics(bucket_ms, mode, outcome, count)`，**只保存计数与时间桶/模式/outcome**，不含 scope、会话、正文、ID 或 reason 明细，因此不需要 HMAC 摘要键；七类终态由协调器的可选 `metrics_recorder` 端口（缺省 no-op）UPSERT 增量写入，`off` 不产生任何行。其中 `fact_overlap` 是 `quality` 检测器在「整段无命中但候选事实被既有 canonical 覆盖到阈值」时给出的附加信号：`observe`/`enforce` 都记录 `checked` + `fact_overlap`，返回 OBSERVED 等价终态且**永不写回**；`checked` 始终是分母，派生 `overlap_rate = fact_overlap/checked`（`checked=0` → 0.0），`hit_rate`/`guard_rate`/`failure_rate` 语义不变。记录异常只降级 debug 日志且不影响合并结果，`asyncio.CancelledError` 继续传播；保留期由 `memory_dedup.metrics_retention_days`（默认 30，范围 1-3650）控制，在初始化后与写入节流（每 64 次写入或每小时至多一次）清理过期桶。只读消费方是 Page API `GET /memory-dedup/metrics`。

```mermaid
graph TD
    Caller[插件/API/处理器] --> Engine[MemoryEngine]
    Engine --> Lifecycle[MemoryEngineLifecycleMixin]
    Engine --> CRUD[MemoryEngineCRUDMixin]
    Engine --> Batch[MemoryEngineBatchMixin]
    Engine --> Retrieval[RetrievalOptimizer]
    Engine --> Journal[features.memory.WriteOpJournal]
    Engine --> Maintenance[MaintenanceOperations]
    Engine --> Schema[features.memory.SchemaManager]
    CRUD --> Hybrid[retrieval.HybridRetriever]
    CRUD --> GraphMgr[GraphMemoryManager]
    CRUD --> AtomStore[features.memory.AtomStore]
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

`memory_engine.py` 通过 `MemoryEngineLifecycleMixin`、`MemoryEngineEvolutionHooksMixin`、`MemoryEngineCRUDMixin`、`MemoryEngineBatchMixin` 组装主入口；包级 `__init__.py` 还导出 `ConversationManager`、`GraphMemoryManager` 和 `create_conversation_manager`。

| 入口 | 语义 |
|---|---|
| `initialize()` / `close()` | 打开/关闭 SQLite 与图向量库，创建索引组件和可选子系统；`initialize()` 不执行持久化操作恢复，追踪并取消 `_pending_tasks` |
| `recover_persisted_operations()` | 在 canonical 与图文档存储就绪后，按既有重试与取消语义恢复 WriteOpJournal 和 reconsolidation；未就绪时静态失败且不产生副作用 |
| `search_memories(...)` | 经缓存、双路/混合检索、触发词、情绪/季节和链式扩展返回 `HybridResult` |
| `update_memory(...) -> bool` | 元数据原地更新；无 `expected_revision` 的内容更新走两阶段替换（暂存不可召回新行 → 单事务切换可见性 → 删除旧行），失败按账本收敛并以 `content_replace_failed` 报告 |
| `find_replacement_memory_id(old_id)` | 替换新 ID 的唯一查询端口；只在替换账本已提交且新行非暂存时返回，未收敛/已删/无法证明都返回 `None` |
| `delete_memory(...) -> bool` | 先删文档索引，再清理图和原子；子资源失败进入修复队列；任意返回路径都先失效检索缓存 |
| `batch_delete_memories[_detailed]()` | 每 200 个 ID 分批删除并返回计数/失败明细 |
| `apply_daily_decay()` / `cleanup_old_memories()` | 重要性衰减和 `ACTIVE → DORMANT → ARCHIVED → 物理删除` |
| `maintain_storage()` / `rebuild_graph_index()` | 存储维护和图产物重建 |

### 生命周期初始化顺序

`memory_engine_lifecycle.py` 的实际顺序是：

1. `aiosqlite.connect`，设置 `Row` 与共享 PRAGMA；此时尚不注册可重连连接。
2. `SchemaMigrationCoordinator` 只读检查版本；fresh install 直接建当前结构，旧库按 `migration_settings` 决定阻断或先创建 `pre_migration` 快照再迁移，同时创建 `memory_write_ops`。迁移成功后才注册可重连连接。
3. 构建 `TextProcessor → BM25Retriever → VectorRetriever → HybridRetriever`。
4. 仅在 `graph_enabled` 且存在 `graph_vector_db` 时构建 `GraphStore`、`AtomStore`、层级存储、图双路检索和 `GraphMemoryManager`。
5. `initialize()` 只构建上述组件；组合根在 canonical/图文档存储各自 `initialize()` 成功后调用 `recover_persisted_operations()`，再继续发布后续子系统与 worker。
6. 按配置构建画像、知识、笔记、自动学习、性格追踪、重排序器等；复用工厂注入的 typed `CostControl`，高成本 `llm`/`hybrid` 重排未通过功能门时降级为 `mmr`，成功创建的实例写回 `MemoryEngine.reranker` 并传给图双路检索器。
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
- 无 `expected_revision` 的正文更新是两阶段替换，保证任一时刻最多一条可召回 owner：新行先以 `replacement_pending`（`status=deleted`，不可召回、不建图、不强化）写入 → 账本 `replacement_created` → 单事务切换可见性（赢家恢复原状态、输家隐藏）→ 删除旧行后 `replacement_committed`；删除未完成记 `replacement_cleanup_pending`，补偿成功记 `replacement_rolled_back`，补偿失败记 `replacement_rollback_failed`（`needs_repair`），异常中断记 `replacement_aborted`。repair 只在两行并存时按账本 `content_digest` 判定赢家（匹配 → 删旧行；不匹配 → 删新行，旧行保持权威），单边存在按事实收敛，绝不复活已删除内容；`find_replacement_memory_id(old_id)` 是替换新 ID 的唯一查询端口。并发对同一行再次发起替换时以 `content_replace_pending` 拒绝。
- canonical 插入成功后立即登记 `documents_committed`（含 `content_digest`/预览）；后续 FTS/FAISS 阶段失败时 `add_memory` 仍返回已提交的整数 `doc_id`，并把该操作标为 `needs_repair`（`index_stage_degraded`），调用方不得把派生失败当成写入失败重试；只有 canonical 插入本身失败才向上报错。
- `add_memory()` 在 canonical 成功后重新读取 source revision，并为 Atom 绑定 parent revision/scope/privacy；来源读取失败时只进入可修复派生失败，不把未绑定 Atom 写入生产 canonical 库。
- 总结来源 fence 写入是两阶段的：`add_memory(source_fence=...)` 先落不可召回的暂存行（`summary_source_orphan/pending`、账本 step=`source_staged`，不建图、不强化既有 Atom、不触发干扰/触发词/演化/SSE），来源 owner 校验通过后在单个 canonical 事务内激活并同事务登记账本 `derived_pending`，再由 `finalize_add_derivation` 复用修复路径补图并收口同一 add 操作；接受与拒绝都按 `source_fence` token + 暂存状态做 CAS，拒绝仅在本轮 CAS 成功时收口账本，未接受来源既不派生也不推进 summary cursor。
- canonical metadata 更新默认携带入口读到的 revision 做 CAS（失败原因码 `source_revision_mismatch`），无语义变化时不重建图；测试效应与自动干扰属于运行态维护，只经 `reinforce_recall_state`/`apply_interference_decay` 白名单入口写入，不推进 revision。
- 事实文本单 owner：`documents.text` 是唯一权威正文，`key_facts`/`fact_source_evidence` 只是同一声明的准入元数据，判据是 `application/fact_text_alignment.py` 的三态（`aligned`/`misaligned`/`undeterminable`）。CAS 正文更新未提供事实字段时在同一事务清除与新正文矛盾的旧事实字段；调用方提供任一事实字段时只按本次提供的那对值判定（不与旧表示混合拼出「新事实 + 旧证据」），不构成对齐表示即以 `fact_evidence_mismatch` 拒绝且不改 canonical；正文已变化时 `canonical_summary` 必须与正文一致（提供值与新正文不一致时同样同步为新正文，绝不提交「新正文 + 旧摘要」）；读取侧（图 fact 抽取含结构化 `graph_extraction` 的 fact 实体、前瞻注入、Atom 事实集合）在消费前校验事实仍属当前正文，不满足按「无事实元数据」回落 canonical 正文，不整条拒绝。
- 检索缓存命中不再盲信缓存：按结果 `doc_id` 批量回读 canonical（批量端口优先取 SQLite 原始 revision，与候选/派生快照同源），剔除已删除、正文已改写、不可召回、mark_write、缺少用户证据、被本次请求可见性排除（群会话机密行、请求 `query_scope` 与行当前 scope/privacy 不一致），或 revision 已过期的条目（缓存建立时先记录读取窗口起始的缓存代际，发布前代际已前进则整次不发布并剥离未证明的 `derived_projections`；发布时按 `doc_id` 一次批量回读 canonical，仅当候选正文与当前 canonical 正文一致才把权威 revision 快照写进缓存副本，命中时条目携带 `revision_token` 即与行当前 revision 同源比较，无 token 时按同一 `metadata.updated_at` 快照比较；条目完全没有可比快照而 canonical 行已有快照时 fail-closed 视为失效，两侧都无快照时保持现状）并计入 `dropped_stale_count`——失效条目连同其携带的旧派生投影一并剔除；任何推进 revision 的 canonical 写入（正文/语义 metadata 更新、替换、删除、状态维护）都会经 `invalidate_cache` 使缓存整代失效；canonical 回读不可用时按未命中回落实时检索，不返回无法证明仍属当前 canonical 的正文。
- `memory_write_ops` 的 failed atom payload 保留父来源快照；repair 只接受仍匹配当前 revision 的现代载荷，旧载荷最多恢复为不可主动召回的兼容行。add 修复仅在正文可证明未变时才允许按当前 revision 收敛：add 账本载荷同时记录 `content_preview`（正文前 500 字符）与 `content_digest`（`CanonicalMemoryCommitted.digest_content(content)[:32]`）；有摘要时按摘要比对，没有摘要时只有「预览短于 500 字符且等于当前全文」才能收敛（预览达到 500 字符说明它是被截断的前缀，无法证明第 500 字符之后未变），摘要不匹配或前缀被截断一律保持 `needs_repair`/`source_stale` 且不建图。
- 原子批量失败后逐条补写，仅仍失败的原子进入修复载荷。图失败不撤销已建文档，而是标记修复。
- 删除先调用 `HybridRetriever.delete_memory()`；随后图或原子清理失败不会把主删除改成失败，但日志保留 `needs_repair`。delete 修复在清理图/原子前必须在写账本自身的 canonical 连接上按 `documents.id` 严格确认文档已不存在（明确查无行才清理；仍存在或无法确认时写 `needs_repair`/`source_alive` 并原样保留派生数据）。展示型 `get_memory` 会把读取异常吞成 `None`，不得当作“已删除”的证明。
- `WriteOpJournal.start_op()` 失败时可能返回 `None`；业务路径仍继续，因此不能把日志存在等同于事务已保证。
- `MemoryEngineProfileHooksMixin` 只在 canonical add 成功后创建受跟踪画像任务；
  `ProfileProposalPipeline` 重新读取 source 并校验稳定身份、revision、scope 和 privacy。
  自动标签/偏好携带 derived provenance，普通失败隔离主写，取消必须传播。画像 Store
  读取时过滤失效来源；偏好是整份 provenance 快照，已有 manual 来源时自动 proposal
整体让位，不能覆盖人工字段。
- `MemoryEngineKnowledgeHooksMixin` 只在 canonical add 成功后创建受跟踪知识任务；
  `KnowledgeProposalPipeline` 先执行重要性/置信度/稳定状态门和 `knowledge_extraction`
  额外预算门，再二次校验 source revision、scope、privacy。知识条目携带不含正文的
  derived provenance，人工条目优先，普通失败隔离主写，取消必须传播；Knowledge Store
  读取时过滤失效来源，自动知识不进入被动召回。
- `MemoryEngineDomainHooksMixin` 统一调度画像、知识和笔记写后任务；其中自动笔记只消费达到
  `notes.auto_create_min_length` 的 canonical source。`NoteProposalPipeline` 在预算允许时调用
  `NoteGenerator`，否则使用确定性 fallback，并在写前二次校验 revision/scope/privacy。
  `NoteStore` 按完整 provenance 事务幂等，人工笔记与版本不被自动重建覆盖；source 失效后
  derived note 不可见但版本历史保留，统一重建的 notes 阶段不调用 Provider。

## Memory Evolution 生命周期与安全边界

`core/features/evolution/application/` 负责 canonical 写入后的派生演化，不替代 `MemoryEngine` 的主写路径：

- `MemoryEvolutionGate` 仅基于 source revision、scope、topic/entity 信号、阈值、去抖桶和待处理上限生成稳定 idempotency key；`enabled=false` 或非法 mode 必须返回 `mode_disabled`。
- `MemoryConsolidator` 只把有界 canonical evidence 转为经过 JSON/Pydantic 校验的 `EvolutionProposal`；临时 alias、输入/输出预算和 Projection 字符上限保持强制约束，解析或预算失败交由 worker 重试/死信，取消继续传播。
- `MemoryEvolutionManager.schedule_consider()` 只在 canonical 写入成功后入队；Store 在 SQL 限流前按同 scope 选择最多 6 条近期 source，并把创建时全部 revision 写入 job provenance，其他 scope 的新记录不能挤掉同 scope 证据。worker 以单任务循环领取 job，持有可续租 lease；领取后先核对 job revision，stale job 进入 invalidated；取消会恢复 pending，普通异常按指数退避重试，超过 `max_attempts` 进入 dead，proposal 规则拒绝进入 rejected。
- `MemoryEngine` 在 canonical add/语义 metadata update 提交后统一重载 source 并调度；`ReflectionHandler` 仍覆盖反思链兼容调度，重复触发由稳定 idempotency key 去重。派生计划写入 `origin_job_id`，启动时先做 orphan/stale cleanup；回滚 job 只能失效自身派生对象，不能删除 canonical。
- 处理 proposal 时先运行本地 `MemoryEvolutionCandidateGenerator`：episode/conflict 候选非空时不调用 LLM，只有确定性候选为空才回退 Consolidator。随后必须再次读取 source 并比较每个 revision；source 缺失、scope 不一致、alias 未知、自关系、重复/成环边、冲突 Projection 少于三类角色均拒绝，不能污染派生表。
- 关系按低/高影响分类：低影响且达到阈值的允许按配置自动 `active`，`updates`/`contradicts`/`preference_change`/`supersedes` 始终是 `candidate`。高影响 relation 的 approve/reject/replay 使用候选 revision CAS；approve/replay 再次验证 canonical source，后台重复 proposal 不得覆盖人工 rejected 状态。Projection 共享 scope，privacy 取所有 source 中最严格值，状态由置信度和冲突类型决定。
- `SemanticCompressor` 只读取达到年龄门槛的 canonical source，按完全相同的 scope/privacy/role 分区并以 topic Jaccard 聚类；摘要通过 `apply_projection_proposal()` 二次核对全部 source revision 后写入 `semantic_summary`，不得调用 canonical add/delete。扫描普通失败只降级当前维护项，取消必须传播。
- Relation/Projection 是 SQLite 中的派生解释平面；稳定 ID 由 source memory ID、revision 和类型计算，但不创建第二套 canonical memory 或向量索引。更新/删除 canonical 后由 Store 的 revision invalidation 隔离旧派生结果。
- `get_status_snapshot()` 只能返回模式、计数、reason code 和延迟桶等 allowlist 标量；不得把 query、prompt、正文、原始身份、source ID 列表或 provider 信息写入日志/指标。

## SQLite、事务与并发约束

- `write_coordinator.py` 的模块级 `asyncio.Lock` 串行化协调写入；锁冲突可指数退避并加随机抖动，连接坏死由 `ConnectionRegistry` 重连。
- `coordinated_transaction()` 使用 `BEGIN IMMEDIATE`，异常必须 rollback，取消也必须继续上抛。
- `infrastructure/base.py` 的 `ConnectionPool.acquire()` 在归还连接前回滚借用方未收束的事务（借用方不得依赖事务跨 `_connect()` 块存续）；`close()` 覆盖队列中与已借出的全部连接并置为已关闭，同时唤醒已在 `acquire()` 排队等待的调用方（它们收到 `RuntimeError` 而不是永久挂起），关闭后的新 `acquire()` 也直接报错。`base_store.BaseStore.initialize()` 幂等：重复调用先关闭旧连接，建表失败时关闭新连接并清空 `connection` 后原样抛出。
- `SchemaManager` 只对白名单 `doc_id`、`created_at`、`updated_at` 做动态列迁移，并安全引用标识符；动态 SQL 不得接收未白名单化的外部表/列名。
- `SchemaManager` 分离 `inspect_schema()`、`create_fresh_schema()`、`build_migration_plan()`、`migrate_existing_schema()` 与 `validate_schema()`；生产启动只由 `SchemaMigrationCoordinator` 编排。`auto_migrate=false` 遇到旧结构必须以 `schema_migration_required` 停止引擎启动，不能调用兼容 `create_tables()` 偷偷升级。
- 迁移计划使用稳定 `migration_id`，只记录 from/to version、阶段、reason code 和变更计数。启用迁移备份时，`pre_migration` 快照必须先于 `BEGIN`/DDL/DML；失败时关闭启动连接并从已校验快照原子恢复 canonical，恢复失败持久化为 `blocked`，不得继续发布运行时。
- `documents` 是校验与重建的源数据表；BM25、FAISS、图和原子都是需要同步或可修复的派生产物。
- 维护批次和批量删除是有意分块的；不要改成超大事务，也不要在持锁区执行 LLM/Embedding 网络调用。

## 子系统索引

| 区域 | 文件 | 事实边界 |
|---|---|---|
| 会话 | `features/conversation/application/`（`conversation_manager.py` 及 6 个 mixin） | `ConversationStore` 上层 LRU、上下文窗口、事件适配和元数据；缓存由 `_cache_lock` 保护 |
| 图同步 | `graph_memory_manager.py`、`features/memory/graph/infrastructure/` | 删除旧图产物后重建节点/边/条目与图向量；向量 ID 最终回写 SQLite |
| 原子生命周期（派生信号） | `atom_lifecycle_manager.py`、`features/memory/application/atom_source_binding.py` | 周期过期/遗忘/冷迁移，同批原子 Jaccard 去重；canonical add 后绑定 parent source，后台任务由 `start/stop` 管理。Atom 状态/TTL 只决定该信号是否参与排序/前瞻，不改变 canonical 事实的存在、可见性与召回；canonical 变更后由 `rederive_for_sources(ids, reason)` 按当前事实替换该父的 Atom 行与 FTS（运行态历史随之重置），统计端口是 `count_current_atoms()`（父存在、revision/scope/privacy 匹配且可召回），原始 `search_fts`/`search_fts_by_type` 只服务维护与强化 |
| 维护 | `decay_operations.py`、`lifecycle_operations.py`、`stats_operations.py` | 衰减、分层遗忘、统计、存储与图索引维护；状态批更新提交后统一失效派生面（relation/projection 失效、图源级残留回收、Atom 重派生），失败只降级计数 |
| 派生重建 | `core/platform/composition/derived_rebuild_coordinator.py` | 固定顺序 canonical → indexes → catalog → atoms → graph → evolution → semantic_compression → notes；命令与 Page API 经 `rebuild_stages` 单阶段入口（端口缺失记 `rebuild_coordinator_unavailable`）；atoms 阶段按 200 分页重派生并回收无父残留，失败降级为 `atoms_rebuild_partial_failed`；owner 表、读取门矩阵、替换收敛与原因码见本地契约 `.trellis/spec/core/features/memory/backend/canonical-fact-ownership.md`（项目本地 spec 存储，不随仓库分发） |
| 画像 | `features/profiles/application/`、`memory_engine_profile_hooks.py` | 管理员编辑使用修订值冲突检测；canonical 写后自动 proposal 仅绑定唯一可信主体，标签与偏好携带 derived provenance 并走存储层原子事务 |
| 知识/笔记 | `features/knowledge/application/`、`features/notes/application/`、`memory_engine_domain_hooks.py` | 知识与笔记 canonical 写后 proposal、来源约束幂等与失效；自动笔记可无 Provider 重建，人工 CRUD、软删和版本历史保持领域权威 |
| 异常检测 | `anomaly_detector.py`、`stats_operations.py` | 按 UTC 日聚合 canonical 创建量；只用当前日之前的完整窗口计算 3-sigma 基线，待投递告警随状态恢复，同一天只写一条脱敏诊断事件 |
| 记忆再巩固 | `reconsolidation.py`、`reconsolidation_store.py` | 默认关闭；召回只生成 pending 候选；apply 先持久化唯一 intent，再按 source revision CAS 写 canonical 并恢复/失败收口；回滚同样持久化跨 Store 意图并刷新当前 source 的 graph 派生，状态、动作审计与操作清理原子收口；启动恢复不得覆盖后续编辑 |
| 自主学习 | `features/learning/application/`、`features/learning/domain/`、`features/learning/infrastructure/` | 统一 FeedbackSignal 事件只进入隔离 Store；shadow 候选经单一 CAS 写入口发布；生产写入前持久化真实旧权重 intent，最终状态保存失败时保留可重启回滚快照；rebuild/publish/rollback/reset 共用状态锁，不直接修改生产权重 |
| 可靠性 | `write_coordinator.py`、`features/memory/infrastructure/write_op_*`、`memory_engine_write_observability.py` | SQLite 写串行化、重试、跨存储操作日志和崩溃修复；canonical 写入指标与质量采样由独立 mixin 承担 |
| 来源可重放（只读） | `features/memory/application/source_replayability.py` | 按当前 ConversationStore 消息事实逐项对账 canonical 已持久化的 `source_evidence`/`fact_source_evidence`（epoch、窗口边界、message_id/seq、session、role、区间、指纹），只输出 `replayable/partial/unavailable/unknown` 聚合状态、计数与固定原因码；不写库、不改清理语义、不返回正文/身份/映射 |
| 记忆演化 | `features/evolution/application/`、`features/evolution/infrastructure/` | canonical 写后门控、确定性/LLM proposal、单 worker、lease/retry/dead/cancel、关系与 Projection 计划校验及语义摘要生成 |
| canonical 派生钩子 | `memory_engine_evolution_hooks.py` | source revision 提取、post-commit 调度、relation/projection 失效；不承载 canonical 正文写入 |
| 连续性 | `continuity_tracker.py`、`memory_engine_lifecycle.py` | 使用 `data_dir` 同步恢复/保存，按配置 TTL 和单 session 上限保留话题；关闭时不创建或读写 |
| 文件状态 | `features/learning/infrastructure/auto_learning_state.py` | JSON 状态属于运行数据，不是配置；加载失败通常降级为空状态；状态写入失败必须显式返回/抛出，不能把生产发布报告为成功 |
| 备份 | `features/backup/{domain,application,infrastructure}/` | SQLite 使用 Online Backup API；manifest 保存角色、大小、SHA-256 和 quick check；`pre_migration` 供启动迁移失败恢复，新恢复使用 `.restore/<operation_id>/restore_plan.json`、`payload/`、`previous/` 事务目录；旧 `managers/backup_*` 路径已删除 |
| 插件更新 | `update_manager.py`、`update_installer.py` | `update_manager.py` 检查 GitHub Release，按镜像到官方顺序下载 runtime 与校验清单，并只在 SHA-256 校验通过后写入暂存区；`update_installer.py` 严格校验 ZIP、在 AstrBot 插件目录同卷切换 runtime，安排单插件重载，失败时恢复旧目录并记录安全状态 |
| 导入导出 | `memory_exporter.py` | JSONL/Markdown 包含正文与 metadata；导入按内容 SHA-256 短哈希去重后重新走 `add_memory` |

## 安全与不可泄露数据边界

1. **记忆正文、会话 ID、人设 ID、用户画像、消息、情绪标签和 metadata 均为敏感数据。** 不得写入普通日志、指标标签、异常字符串或对外追踪；当前少数日志含内容前 60 字符，新增代码不得扩大泄露面。
2. `MemoryExporter` 会明文写出完整正文和 metadata，且接受调用方给定路径；调用方必须完成授权、路径约束和文件权限控制。导出文件不可当作无敏感数据的调试附件。
3. `MemoryImporter` 输入不可信：JSON 结构、metadata、importance 和目标 session/persona 必须在调用边界验证；导入失败不得回显完整正文。
4. `BackupManager.validate_backup_name()`、备案目录集合和 `relative_to(backups_root)` 共同阻止路径穿越；备份源、manifest 和恢复 payload 还必须拒绝符号链接、绝对路径、分隔符及白名单外文件。不可绕过这些 API 直接拼接路径。
5. 写日志中的修复载荷和备份目录具有与原始记忆相同的保密级别。不得通过 SSE、诊断 API 或导出默认暴露。
6. LLM 再巩固和高成本重排会把记忆内容送给配置的 provider；只有在用户授权且 provider 数据策略允许时启用。
7. Memory Evolution 的 proposal 输入是受长度限制的不可信 evidence；模型输出先由 feature application 的 Consolidator 结构校验，Manager 再拒绝非 `EvolutionProposal`、超出 `candidate_limit` 或重复 source，并做 source revision、scope/privacy、role 和影响级别校验。任何 source 证据不得进入模型可见的 Projection metadata。

## 异常规则

- `asyncio.CancelledError` 必须重新抛出；普通检索增强、画像排序、可选维护可降级，但持久化主写失败必须显式失败或进入 `needs_repair`。
- 内容为空：`add_memory()` 抛 `ValueError`；未初始化核心检索器：抛/返回失败，不能静默写半套数据。
- 内容更新是新 ID 替换旧 ID，调用方不得假定 `memory_id` 永久不变；需要定位替换后的当前 owner 时只用 `find_replacement_memory_id(old_id)`，不得按时间或相似度猜测。
- `cleanup_old_memories()`、可选管理器和状态文件通常采用尽力而为语义；返回 0/空结果不等于数据一致性已验证。
- `BackupManager` 只在 canonical SQLite 快照、manifest 和 quick check 全部成功后发布 `ready` 备份；失败不得发布半成品。`scheduled`、`pre_migration` 与 `pre_restore` 允许按保留期自动 prune，`manual` 和 `version_change` 必须显式删除。


## Topic catalog 存储契约

`TopicCatalogStore` 只拥有 canonical SQLite 中的派生 topic mapping、scope aggregate、generation state、dirty queue 与 metric-window 去重表。`documents` 的 INSERT/UPDATE/DELETE 触发器在同一 canonical 事务内递增 watermark 并登记只含 `memory_id`、操作和固定 reason 的 dirty 行；不得把正文、旧 metadata、scope 或 topic 快照写入 dirty/journal payload。

目录 mapping 必须按 memory ID 重读当前 `documents`，并同时通过 `is_memory_recallable`、`mark_write`、orphan、scope provenance、privacy、chat type、revision 和 topic label 规范化校验；失效 source 只删除 mapping，不影响 canonical。BM25、FAISS、图和 catalog 回填均是可修复派生阶段，不能把 catalog 失败升级为 canonical 回滚。

## 测试定位与精确验证

按修改范围选择最小命令；本模块文档初始化不执行测试。

```bash
python -m pytest -q tests/test_managers_memory_engine.py tests/test_managers_memory_lifecycle.py tests/test_managers_memory_crud.py tests/test_managers_memory_batch.py
python -m pytest -q tests/test_canonical_write_recovery.py tests/test_canonical_read_gates_cache.py tests/test_fact_text_alignment_crud.py tests/test_derived_rebuild_coordinator.py
python -m pytest -q tests/test_managers_write_coordinator.py tests/test_managers_write_journal.py tests/test_managers_write_serial.py
python -m pytest -q tests/test_managers_decay.py tests/test_managers_stats.py tests/test_managers_schema.py
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
