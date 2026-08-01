# Memora Architecture and Design Contract

## Purpose

This document is the repository-level architecture contract for Memora. It consolidates
stable boundaries that are already implemented and links implementation work back to the
module documentation, tests, and feature-specific design records. It is not a replacement
for detailed feature specifications and does not introduce a second adaptive-injection spec.

Memora turns chat events into durable `MemoryAtom` records and retrieves the useful subset
for later requests while keeping storage, prompt cost, privacy, and runtime failure isolated.

## System boundaries

```text
AstrBot event
  -> MemoraPlugin / PluginInitializer
  -> EventHandler
       -> ConversationManager
       -> MemoryProcessor
       -> RecallHandler
       -> ReflectionHandler
  -> MemoryEngine
       -> SQLite stores
       -> BM25 + vector retrieval
       -> FAISS / graph indexes
  -> PluginPageApi
       -> Dashboard bridge
```

`main.py` registers the plugin and hooks. `PluginInitializer` constructs components in
dependency order and owns partial-initialization cleanup. `EventHandler` coordinates message
events but delegates storage, recall, extraction, reflection, and scheduled work to bounded
components. Shutdown is idempotent and closes producers before their stores.

## Memory model and lifecycle

`MemoryAtom` is the durable unit shared by extraction, retrieval, lifecycle management, and
diagnostics. An atom carries typed memory content and allowlisted metadata; indexes are
derived data and can be rebuilt from durable storage.

The normal data path is:

1. Extract normalized message content.
2. Maintain the conversation/session lifecycle.
3. Generate or update MemoryAtom data through the configured processor.
4. Persist through SQLite-backed stores and the coordinated write boundary.
5. Update full-text, vector, and graph-derived indexes.
6. Apply decay, archive, cleanup, and reconstruction through explicit lifecycle services.

SQLite is authoritative for structured durable state. FAISS and graph indexes accelerate
retrieval but must not become the only copy of a memory. Multi-step writes use the shared
write coordinator or a store-local transaction following the same serialization contract.

### 自动画像 proposal 闭环

canonical memory 成功提交后，`MemoryEngine` 通过受生命周期跟踪的写后任务调用
`ProfileProposalPipeline`。管线重新读取 canonical memory 和 source revision，只接受
`stable-identity-v1`、唯一可信 `subject_id` 与匹配的身份来源；匿名、多主体、非法身份或
缺失 source 均直接跳过。标签和偏好写入 `ProfileManager` 时携带不含正文的
`DomainProvenance`，`ProfileStore` 在事务内再次校验 revision/scope/privacy，并在读取时
过滤失效 derived 对象，因此个性化排序只消费当前有效来源。

画像 LLM 调用使用请求级 `profile_extraction` 额外预算；没有额度时只运行保守关键词
fallback，不裸调用 Provider。普通派生失败不回滚 canonical，取消继续向上传播并释放
reservation。偏好目前以整份快照记录 provenance；已有人工来源时整份自动 proposal 让位，
避免值被覆盖而来源仍伪装为 manual。Dashboard 详情同时展示标签和偏好的 manual/derived
来源，旧数据兼容既有 `source` 字段。

### 自动知识 proposal 闭环

canonical memory 成功提交后，`MemoryEngine` 将任务交给受生命周期跟踪的
`KnowledgeProposalPipeline`。管线先按重要性、置信度、稳定状态筛选 source，再在
`knowledge_extraction` 请求级额外预算允许时调用 `KnowledgeExtractor`；没有预算时不调用
Provider。抽取结果必须通过 category、title、content、confidence 和 tags 的结构/长度校验，
随后重新读取 source 并比较 revision、scope、privacy，才以不含正文的
`DomainProvenance` 调用 `KnowledgeManager.add_derived_entry()`。

`knowledge_base.dedup_threshold` 与 `expire_days` 由运行时配置投影到 Manager；合并要求相同
scope/privacy，人工知识始终优先，重复 derived proposal 保持幂等。Knowledge Store 的读取
边界继续过滤失效 source revision；自动知识只通过既有 Agent Tool/API/Dashboard 显式读取，
不进入被动召回。派生失败隔离 canonical 主写，取消继续传播。

### 自动笔记 proposal 闭环

达到 `notes.auto_create_min_length` 的 canonical memory 成功提交后，`MemoryEngine` 通过统一领域
写后钩子调度 `NoteProposalPipeline`。请求级 `note_generation` 预算允许时调用 `NoteGenerator`；
balanced/low-cost、缺少请求预算或生成结构不可用时使用确定性的 canonical 来源标题与正文，
重建路径始终禁用 Provider。标题和正文执行固定领域长度限制，tags 按 `notes.max_tags` 过滤、
去重和截断；`notes.max_versions` 由同一运行时配置注入 `NoteManager`。

自动入口必须携带不含正文的 `DomainProvenance`，并在生成后重新校验 source revision、scope 和
privacy。`NoteStore` 在 `BEGIN IMMEDIATE` 事务中按完整 provenance 幂等命中当前 derived note，
不会更新人工笔记或其版本；source 更新/删除后旧自动笔记在读取面失效，但 `note_versions`
继续保留审计历史。`DerivedRebuildCoordinator` 在 canonical、索引、graph、evolution 后运行独立
notes 阶段，以 Provider-free 方式从当前 source 重建。自动笔记不新增 Agent 写入口，也不进入
被动召回。

### 话题分段闭环

`TopicBatchPreparer` 只在结构化抽取前执行策略 C/D：C 使用相邻消息 Embedding 边界，
D 使用请求级额外 LLM 预算识别话题范围。`MemoryProcessor` 在结构化解析和
`StorageBuilder` 之间执行 A/B/Hybrid：A 直接消费 `memories[]`；Hybrid 仅在 A 返回
单条且事实数达到门槛时运行 B；纯 B 和 Hybrid 的 B 回退都复用初始化器注入的共享
Embedding Provider。

B 只能在每条原始 memory 边界内聚类，分段继承原 participant 和 source refs；稳定身份
provenance 继续由可信 `Message` 元数据在分段后统一锚定，session/scope 继续由反思存储
边界附加。Router 只记录 strategy、稳定 fallback reason、输入/输出计数，不记录对话正文、
身份、scope、source ID 或 Provider 配置。C/D 在 `MemoryProcessor` 内保持透传，避免重复分割。

### 对话连续性与关系所有权

`ContinuityTracker` 只接收通过质量门且 canonical 写入成功的 topics，按稳定 session 隔离并在启动/关闭同步恢复和保存；召回读取结果复用既有 cognitive budget、Prompt 保护与 `InjectionExecutor` 临时请求路径，不写 System Prompt 或 canonical。`core/affection/` 是 Bot 与用户好感度（warmth、层级、情绪）的唯一权威，`core/social/` 是用户间显式关系的唯一权威；遗留 `RelationshipTracker`（JSON 状态、无生产消费者）已删除，不得再引入第三套关系状态，好感度或关系分值不改变 privacy、scope 与注入权限。

### Pre-canonical quality gate

LLM extraction output is not canonical merely because its JSON structure is valid. Every
candidate carries anonymous `S<n>` source offsets and passes deterministic grounding checks
for range, numeric anchors, negation polarity, and group-chat subjects. An uncertain claim may
use the request-scoped extra-LLM budget for a Judge; the Judge receives only the current claim
and its referenced snippets. Ordinary Judge failure conservatively quarantines the candidate,
while cancellation propagates.

`MemoryQualityGate` is the only production boundary between extracted candidates and
`MemoryEngine.add_memory()`. Low-quality or ungrounded candidates are stored in the independent
`memory_quarantine.sqlite3` queue and never enter canonical SQLite documents, FTS, FAISS, Atom,
graph, or Memory Evolution. The quarantine candidate ID is not a canonical memory ID.

Approval uses revision CAS, reloads the original `ConversationStore` window, revalidates the
stored message fingerprints and offsets, rebuilds Atom data, and then performs one normal
canonical write. Rejection preserves the original messages. Cancellation before canonical
submission returns the candidate to a blocked state; cancellation after submission begins
leaves it `approving` to represent an unknown commit result and prevent automatic duplicates.
Page API responses expose only allowlisted candidate fields and anonymous offsets.

## 派生记忆演化闭环

canonical memory 成功提交后，`MemoryEvolutionManager` 从同 scope 的近期记录中有界选择
最多 6 条 source，并把创建时 revision 固化到 job。`MemoryEvolutionCandidateGenerator`
先运行不调用 Provider 的确定性候选链：`EpisodeClusterer` 产生带 topic overlap、时间窗口和
revision 的 `same_episode` relation proposal；`ContradictionDetector` 只产生同匿名主体的
`updates`/`contradicts` 候选。只有本地候选为空时才回退 `MemoryConsolidator`，避免为已经
确定的 episode/conflict 再付出一次 LLM 调用。

处理器不写 canonical metadata、正文或状态。Manager 在应用 proposal 前重新读取全部 source，
校验 revision、scope、privacy、主体、role、自关系、重复边和环，再把低影响高置信 relation
写为 `active`，把高影响 relation 写为 `candidate`。relation 的稳定键绑定 canonical ID、
source revision 和类型；Projection 仍只是有 source mapping 的派生解释，不形成第二套
canonical ID。

可选 `SemanticCompressor` 只读取达到年龄门槛的 canonical source，并在完全相同的 scope、
privacy 与 role 分区内按 topic Jaccard 聚类。它通过 Manager 的外部 Projection proposal 边界
二次核对全部 source revision，再写入 `semantic_summary`；不会调用 canonical add/delete。
任何摘要来源更新、删除或 orphan cleanup 都使整条摘要失效，统一派生重建和每日维护可幂等
重建当前 revision。关闭语义压缩后读取器屏蔽已有 `semantic_summary`，canonical 仍正常召回。记忆再巩固默认关闭：召回只生成 pending 候选（记录 source revision、旧正文与 LLM 提案），人工确认后按 `expected_revision` CAS 应用，回滚恢复旧正文与派生索引，热路径永不直接写 canonical。

高影响 relation 使用独立复核状态机和动作审计表，不复用 canonical review 或 pre-canonical
quarantine 的 ID/状态。`approve`、`reject`、`replay` 都要求候选 revision CAS；approve/replay
还会再次验证 source revision、scope 与 privacy。人工拒绝是持久终态，后台重复 proposal 不得
无审计重开，只有显式 replay 能重新进入 `candidate`。Page API 只返回 relation candidate ID、
候选 revision、类型、状态、置信度和动作标量，不返回 source ID/revision、scope、privacy、
正文、身份或 origin job。

## Retrieval and adaptive injection

`RecallHandler` remains the request-event orchestrator. It performs content extraction,
query rewriting, persona/session filtering, retrieval, optional auxiliary context, final
routing, execution, and sanitized observability.

Adaptive injection has one strategy path:

- `InjectionStrategyRouter` resolves Manual, Auto, or Hybrid routing deterministically.
- `core/injection/selection.py` owns pure candidate normalization, utility ranking, and
  stable budget selection; `InjectionExecutor` remains the sole orchestration and request
  mutation boundary.
- Built-in presets are Tool First, Low Cost, Balanced, and Quality.
- Preflight may skip passive retrieval only when the current Provider request really exposes
  an active memory tool.
- Final routing uses normalized candidate signals and does not make an extra LLM call.
- `InjectionExecutor` owns utility selection, layered formatting, prompt protection, the
  global hard character budget, Provider delivery adaptation, and atomic request mutation.
- Dynamic memory never enters System Prompt; the normal path uses temporary user content.

The configured budget includes prospective, ordinary-memory, and optional cognitive layers.
The effective budget is additionally clamped by conservative context headroom. Protection
wrappers count toward the same cap. A failed build or delivery leaves the request unchanged.

Decision metadata is persisted in the `injection_decisions` SQLite table through a bounded,
non-blocking recorder. The schema and API response allowlists exclude query text, prompt
text, memory bodies or memory-ID lists, raw user/session/group/persona identities, Provider
credentials/headers/endpoints, and stack traces. Retention applies time expiry first and a
stable newest-row cap second.

The detailed design and execution record remain in the existing adaptive-memory-injection
specification and implementation plan under `docs/superpowers/`; this repository document
only states the stable ownership and safety boundaries.

## Configuration contract

Runtime configuration is a three-layer merge:

```text
AstrBot configuration -> persisted Memora configuration -> code defaults
```

Every public configuration leaf must agree across `_conf_schema.json`, Pydantic models,
runtime readers, Dashboard types/defaults, and contract tests. Load-time tolerance may fall
back from invalid external data, but save APIs must reject invalid candidate configurations.

Configuration writes use revision-protected compare-and-apply semantics. The Dashboard sends
only changed leaves with `base_revision`; conflicts preserve the local draft until the
administrator explicitly accepts or rebases the remote state.

## Schema migration contract

Canonical SQLite startup separates fresh-schema creation from existing-schema migration.
Existing databases are inspected into a stable migration plan before any mutation. When
`migration_settings.auto_migrate` is false, a required migration blocks `MemoryEngine`
startup with a stable reason code. When migration backup is enabled, a verified
`pre_migration` snapshot must complete before the first transaction or mutation statement.

Migration steps are idempotent and run in an explicit transaction. Completion requires both
the target schema version and the original canonical row count to validate. A failed
migration restores the verified pre-migration snapshot before startup can retry; a failed
restore persists an explicit blocked state. Migration records expose only the migration ID,
from/to versions, stage, reason code, canonical count, and bounded change counts. Database
paths, memory bodies, canonical ID lists, and raw exception details are not observable fields.

`recall_engine.injection_method` is a deliberate breaking removal. There is no compatibility
migration or dual strategy system; rollback uses the supported Manual + Balanced settings or
a version rollback.

## Page API and Dashboard

`PluginPageApi` composes focused API mixins. Handlers validate a fixed request envelope,
reject unknown fields where required, bind all query values, and return explicit response
allowlists. Internal exceptions may be logged, but raw exception data and sensitive request
content are not returned to the browser.

The Dashboard is a React application embedded through the AstrBot plugin bridge. It preserves
the classic-script single-bundle artifact contract. Shared layout primitives, semantic theme
tokens, accessible Dialog/Sheet controls, true server pagination, stale-response suppression,
and three-language key parity are cross-page requirements.

The Injection Strategy workbench contains Overview, Strategy Configuration, and Decision
History. Decision and trace identifiers may be displayed in a controlled detail surface and
used for one-shot in-memory navigation, but they are not written to the URL, browser storage,
bridge-call logs, or persistent debug state.

## Security model

Memora treats chat content, memory bodies, identities, Provider configuration, and prompt
material as sensitive. The main controls are:

- input normalization and fixed-field validation;
- SQL value binding and allowlisted identifiers;
- prompt-protection wrappers for dynamic injected content;
- output and decision-record allowlists;
- no raw decision payload logging;
- bounded queues, budgets, pagination, and retention;
- cancellation propagation and failure isolation at asynchronous boundaries.

Accepted residual risks are explicit. Prompt wrappers and response cleaning are
defense-in-depth controls; untrusted memory remains untrusted and is never promoted to a
trusted instruction. A process crash can lose the last decision batch that has not reached
SQLite, and sustained recorder failure can make the bounded queue discard its oldest pending
records to protect chat latency and memory use. Decision telemetry remains inside the AstrBot
host-authenticated administration boundary and must not be exposed to ordinary chat users.

Security scanners are evidence, not automatic truth. Findings in fixed SQL identifiers,
test DOM setup, local CLI paths, or non-security randomness require manual data-flow review;
actual user-controlled Critical/High findings block delivery.

## Observability and performance

Metrics use bounded labels and sanitized scalar counts. Recall observability records stage
latency, cache behavior, candidate/selection counts, budget use, fallback outcomes, and
recorder health without logging the source query or selected memory identifiers. 异常检测按 UTC 日聚合 canonical `created_at` 创建量并幂等投喂滚动窗口；告警只写脱敏诊断事件（固定 reason code 与标量）并进入健康快照。

Performance gates include deterministic routing/execution metrics, a real
`RecallHandler.handle_memory_recall` total-path p95 comparison against a recorded baseline,
and a file-backed 100,000-row SQLite decision benchmark. Baselines are versioned artifacts;
they may only be refreshed through an explicit measurement command and reviewed diff.

## Quality and release contract

The unified local gate is:

```powershell
python scripts/check_all.py
```

It runs configuration validation, the backend regression suite, integration smoke tests,
Dashboard production build and artifact validation, frontend tests, runtime smoke, and a real
browser smoke. Browser screenshot contents are inspected manually after the automated run.

Behavior changes follow RED -> GREEN -> REFACTOR. Larger changes additionally run the
repository change and quality checks; persistence, API, prompt, and sensitive-data changes run
the security check. Completion requires fresh outputs for the full scope, a clean Git diff,
and a requirement-by-requirement audit rather than extrapolation from narrow tests.

The quality gate may report parameter-count design warnings on the fixed utility formula,
preset override resolver, recorder dependency-injection constructor, and internal routing or
payload assembly helpers. These signatures intentionally keep the approved scalar formula,
Schema leaves, clock/sleep test seams, and immutable routing inputs explicit. Replacing them
with single-use parameter-holder objects would add coupling without reducing behavior or
complexity; complexity, naming, duplication, and oversized-function findings remain blocking.

Release notes must identify breaking configuration changes, operational loss windows, default
retention, and rollback settings. Feature work is committed by concern and never stages
unrelated local artifacts.

## Authoritative references

- Root and module `CLAUDE.md` files: current ownership and implementation notes.
- `website/docs/development/`: environment and gate commands.
- `docs/superpowers/specs/`: approved feature designs.
- `docs/superpowers/plans/`: executable implementation plans.
- `tests/` and `scripts/check_all.py`: executable repository contract.

## 变更历史

### 2026-08-01 - 接通 Topic B 与 Hybrid 生产分段

**变更内容**：在 `MemoryProcessor` 的结构化解析与存储构建之间接入 A/B/Hybrid Router，
并由初始化器注入共享 Embedding Provider；C/D 继续由反思预切分层负责。

**变更理由**：原 Router 和 B 策略只有孤立单测，默认 Hybrid 的 B fallback 在真实写入链中
从未执行，配置与实现没有形成闭环。

**影响范围**：处理器话题分段、组件装配、低敏决策元数据、模块说明和定向回归测试。

**决策依据**：B 按原始 memory/participant 边界分别聚类并继承 source refs；稳定身份与 scope
仍由可信消息和存储边界决定，不能从事实正文猜测。

### 2026-08-01 - 接通 Episode 与 Conflict 派生闭环

**变更内容**：把 Episode/Contradiction 确定性候选接入 Memory Evolution worker，并新增高影响
relation 的 CAS 复核、拒绝、重放和低敏审计链。

**变更理由**：原实现只有孤立处理器或 candidate 状态，没有生产 source 选择、持久化与人工处置
入口，仍属于“功能已写、闭环未接上”。

**影响范围**：Memory Evolution 组件装配、candidate source 读取、relation schema、Page API、
模块文档和定向回归测试。

**决策依据**：canonical SQLite 继续保持唯一权威；episode/conflict 只进入可失效派生平面；
高影响结果默认不可见并必须经 source 二次校验和 revision CAS 后激活。

### 2026-08-01 - 接通自动画像 proposal 闭环

**变更内容**：在 canonical memory 写后接入来源约束的画像 proposal 管线、额外 LLM 预算
与关键词降级，并在 Dashboard 展示画像对象的 manual/derived 来源。

**变更理由**：`ProfileExtractor` 和 `ProfileManager` 原本各自可用但没有生产触发；自动结果
需要稳定身份、source revision、隐私边界和人工权威保护才能进入画像读取与个性化排序。

**影响范围**：MemoryEngine 写后任务、画像 Store/Manager、ProfileExtractor、组件工厂、
Dashboard 三语言资源和定向回归测试。

**决策依据**：canonical 仍是唯一权威；派生失败隔离主写，人工偏好整份快照优先，取消和
预算生命周期保持请求级约束。

### 2026-08-01 - 接通自动知识 proposal 闭环

**变更内容**：将 `KnowledgeExtractor` 接入 canonical 写后任务，增加质量门、额外 LLM 预算、
source revision 二次校验和 derived provenance；同时让知识去重与过期策略消费统一运行时配置。

**变更理由**：原知识抽取器与 `KnowledgeManager.add_derived_entry()` 仅能被测试或手工调用，
没有从 canonical memory 到知识 Store 的生产闭环，且旧合并逻辑可能改变人工条目或混合不同
scope/privacy 的来源。

**影响范围**：MemoryEngine 写后 hook、组件工厂、知识 Manager、额外预算 allowlist、知识
proposal 测试和模块文档。自动知识保持显式 Agent Tool/API/Dashboard 读取，不接入被动召回。

**决策依据**：canonical SQLite 仍是唯一权威；派生对象只保存不含正文的 source revision 证据，
人工知识优先，source 变化或隐私/作用域不兼容时拒绝合并。

### 2026-08-01 - 接通自动笔记 proposal 闭环

**变更内容**：将 `NoteGenerator` 与 `NoteManager.auto_create_from_memory()` 接入 canonical 写后任务，
增加请求预算、确定性 fallback、source 二次校验、配置限制、事务幂等和统一派生重建阶段。

**变更理由**：原自动笔记处理器与 Manager 只有测试调用，长度、tag 和版本配置没有完整消费；
自动结果还必须与人工笔记分权，并在 canonical source 变化后失效而不是悬空可见。

**影响范围**：MemoryEngine 领域写后 hook、组件工厂、Note Manager/Store、额外预算 allowlist、
DerivedRebuildCoordinator、自动笔记定向测试和模块文档；不新增 Agent 写权限或被动召回路径。

**决策依据**：canonical SQLite 继续保持唯一来源，自动笔记只保存 derived provenance；重建不调用
Provider，同 provenance 重放不覆盖人工或派生版本历史。

### 2026-08-01 - 接通对话连续性/异常检测与删除冗余关系追踪

**变更内容**：接通 canonical 写后话题标记、同 session 召回与同步状态生命周期；异常检测按 UTC 日聚合 canonical 创建量并写脱敏诊断事件；删除 `RelationshipTracker` 及其 `relationship_tracking.*` 配置、装配与测试。
**变更理由**：原 Tracker 无生产调用且构造参数错误；旧关系追踪与已接线的 `AffectionManager` 重复且无消费者。
**影响范围**：MemoryEngine 生命周期、反思写入、召回临时上下文、异常检测装配/调度/诊断快照、配置契约（破坏性变更，旧配置被安全忽略）、模块文档与契约测试。
**决策依据**：不新建状态或注入路径；按计划 16.1 决策 3，affection=Bot↔用户、social=用户间关系，JSON 死代码直接删除。
