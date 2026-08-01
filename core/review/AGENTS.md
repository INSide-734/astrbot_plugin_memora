[根目录](../../AGENTS.md) > [core](../AGENTS.md) > **review**

# 记忆人工审查队列模块

**最后更新：** 2026-08-01

## 职责与边界

`core/review/` 提供两条明确分离的人工审查边界：既有 `ReviewStore` 分诊已经存在的 canonical memory；`MemoryQuarantineStore` 与 `MemoryQualityGate` 处理 canonical 写入前的低质量或来源未验证候选。Memory Evolution 的高影响 relation 复核由 `core/storage/memory_evolution_review.py` 和专用 Page API 持有，属于第三条独立队列。三者不得共用 memory/candidate/relation ID、状态机或持久化表，也不得让 quarantine 或 derived candidate ID 冒充 canonical `doc_id`。

## 架构与数据流

```mermaid
flowchart LR
    A[记忆记录与质量统计] --> B[ReviewDetector.detect]
    B --> C[ReviewItem 原因与严重度]
    C --> D[ReviewStore.upsert_item]
    D --> E[(review_items)]
    F[操作员 approve/edit/merge/archive/delete/mark_safe] --> G[Review API 执行业务动作]
    G --> H[ReviewStore.record_action]
    H --> I[(review_actions)]
    H --> J[更新 item status]
    K[MemoryProcessor 候选] --> L{MemoryQualityGate}
    L -->|allow| M[MemoryEngine.add_memory]
    L -->|low / ungrounded| N[(memory_quarantine_candidates)]
    O[管理员 approve / 修正 / reject] --> P[revision CAS]
    P --> Q[重新加载 ConversationStore 证据]
    Q --> R[重新验证来源并生成 Atom]
    R -->|通过| M
    R -->|失败| S[blocked]
    T[高影响 relation candidate] --> U[derived revision CAS]
    U -->|approve| V[active relation]
    U -->|reject / replay| W[(derived review actions)]
```

## 检测模型

- `ReviewReason`：`low_confidence`、`duplicate`、`stale`、`sensitive`、`noisy`、`provenance_missing`。
- `ReviewSeverity`：`low`、`medium`、`high`；敏感标记优先为高严重度。
- `ReviewStatus`：开放、批准、编辑、合并、归档、删除和安全状态。
- `ReviewItem`：`memory_id`、原因、严重度、状态、内容预览、元数据和时间戳。
- `ReviewAction` / `ReviewActionResult`：动作、操作者、载荷与执行结果。
- `json_safe` / `json_copy`：递归生成 JSON-safe 副本，避免可变内部状态泄漏。

`ReviewDetector.detect(memories, quality_stats=None)` 使用确定性启发式：质量统计中的低置信度 ID、token 重叠的近重复、长期未访问且低重要度、配置的敏感 marker、标点/噪声占比和来源元数据缺失。英文 marker 采用不区分大小写匹配。检测结果是分诊信号，不是内容净化或安全裁决。

## Store 契约与数据流

`ReviewStore` 不依赖 AstrBot，使用独立 `aiosqlite` 连接：

- `initialize()` 创建 `review_items`、`review_actions` 及排序/状态/动作索引。
- `upsert_item()` 在 `BEGIN IMMEDIATE` 中按相同 `memory_id` 且原因有交集去重开放项；保留最新 winner，并把其余重叠开放项标为 `safe`。
- `list_items(status, reason, severity, cursor, limit)` 支持稳定的 `(updated_at DESC, item_id DESC)` 游标分页；limit 必须为整数并裁剪到 `1..200`。
- `record_action()` 追加动作历史并把 item 状态更新为动作状态；找不到 item 时返回明确失败，不伪造记录。
- `list_actions()` 按创建时间与 action ID 升序返回完整历史。

`MemoryQuarantineStore` 使用独立的 `memory_quarantine.sqlite3`：

- `stage_candidate()` 按稳定 `candidate_key` 幂等插入；候选尚未拥有 canonical ID，也不进入任何召回或派生索引。
- 状态为 `pending -> approving -> approved`、`pending/blocked -> rejected` 或 `approving -> blocked`；所有动作使用 `expected_revision` CAS，并在同一事务追加低敏动作历史。
- `approved` 与 `rejected` 是终态。canonical 写入开始前取消会转为 `blocked` 后传播；写入开始后取消保留 `approving` 表示提交结果未知，禁止自动重试造成重复 canonical。
- 批准必须重新读取原会话窗口，按持久化消息指纹和 offset 复核；缺失、变化或越界证据一律 `blocked`。通过后重新生成 Atom，并且只调用一次正常 `MemoryEngine.add_memory()`。
- 拒绝只改变候选状态，不删除或改写 `ConversationStore` 原始消息。

## 依赖方向

- 上游：`core/api/review_api.py` 从实际记忆/质量数据刷新 canonical 队列；反思链和手动总结在写入前调用 `MemoryQualityGate`，`core/api/quarantine_api.py` 执行隔离候选处置。
- 本模块：`review_detector.py -> models.py`；`review_store.py -> models.py`。
- 下游：仅 `aiosqlite` 与标准库；包可在没有 AstrBot mock 的环境导入。
- 相关上下文：[存储模块](../storage/AGENTS.md)、[监控模块](../monitoring/AGENTS.md)、[API 模块](../api/AGENTS.md)。

## 隐私、安全与修改约束

- Detector 接收完整记忆内容；Store 保存 `content_preview` 和任意 JSON 元数据。敏感 marker 命中不表示数据已脱敏，写入前和 API 返回前仍需遵循上层隐私策略。
- 不要在日志、错误响应或动作审计中回显完整记忆、敏感 marker 周边文本或任意 payload。
- quarantine API 只允许返回候选 ID、revision、状态、原因码、正文/预览、重要性、匿名 offset、canonical ID 和时间；不得返回 candidate key、session/persona、消息指纹、数据库路径或异常正文。
- 动作状态不能替代真实记忆操作结果；API 必须先确认业务动作语义，再写动作历史，失败不得标成成功状态。
- 保持开放项去重的 `memory_id + overlapping reasons` 语义、事务原子性和游标稳定顺序。
- `json_safe` 只保证可序列化，不验证字段可信度；所有外部 action/payload 仍需在 API 边界限制。
- 新增原因、严重度或状态时必须同步枚举规范化、数据库过滤、API 映射和测试。

## 测试定位与验证

- `tests/test_review_detector.py`：重复、陈旧、低置信度、敏感 marker、噪声、来源缺失、JSON-safe 模型、开放项去重、动作历史、筛选、游标、limit 与非法枚举。
- `tests/test_api_review.py`：队列刷新、详情与操作 API、底层记忆动作协调和失败契约。
- `tests/test_memory_quarantine.py`：幂等 stage、终态、批准复核、取消语义、Atom 重建和原始证据保留。
- `tests/test_api_quarantine.py`：路由、revision 冲突、修正后批准和响应 allowlist。
- `tests/test_memory_evolution_review.py`、`tests/test_api_memory_evolution_review.py`：高影响 relation 的 source 二次校验、revision CAS、reject/replay、动作审计、后台 upsert 隔离和 API allowlist。

精确验证命令：

```bash
python -m pytest -q tests/test_review_detector.py tests/test_api_review.py tests/test_memory_quarantine.py tests/test_api_quarantine.py tests/test_memory_evolution_review.py tests/test_api_memory_evolution_review.py
```
