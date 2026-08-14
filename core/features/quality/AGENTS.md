# 记忆质量门与人工处置

**最后核对：** 2026-08-14  
**导航：** [项目根级](../../../AGENTS.md) / [`core`](../../AGENTS.md) / [`features`](../AGENTS.md) / `quality`

## 职责边界

`core/features/quality/` 管理两类明确分离的质量流程：

1. `MemoryQualityGate` 在 canonical 写入前把 grounding/质量未通过的候选持久化到独立 quarantine 状态机，人工批准后重新取证并走正常 `MemoryEngine.add_memory()`。
2. `ReviewDetector`/`ReviewStore` 扫描已有 canonical 记忆的低置信度、重复、陈旧、敏感、噪声或来源缺失迹象，维护独立人工复核队列。

它不执行结构化抽取、不拥有 canonical 表，也不把检测信号自动当作删除或授权决定。

## 数据流

```mermaid
flowchart LR
    A[Reflection 候选] --> B{quality_gate_action/reason codes}
    B -->|无原因| C[allow → MemoryEngine]
    B -->|需复核| D[MemoryQuarantineStore]
    D --> E[人工 approve/reject]
    E -->|approve| F[重读来源窗口 + grounding]
    F --> G[重建 Atom]
    G --> H[canonical add]
    H --> I[finalize approval]
    J[已有 canonical + 质量统计] --> K[ReviewDetector]
    K --> L[ReviewStore]
    L --> M[人工动作历史]
```

## 关键不变量

- `route_candidate()` 使用稳定 candidate key 幂等 stage；隔离候选不能进入 canonical、FTS、FAISS、图或 Evolution。
- quarantine 数据库保存候选正文、metadata、session/persona 与来源窗口，保密级别等同原对话；列表/API 必须做授权与字段投影。
- approve 强制 `expected_revision`。可选正文修正有非空和 2000 字符上限，并在写入前从 conversation Store 重读原窗口、重新验证 source evidence。
- 只有重验证通过后才重新分类 Atom 并调用正常 `MemoryEngine.add_memory()`；不能从隔离表直接写 canonical/索引。
- approval token 只持久化 SHA-256 摘要。canonical 可能已提交而 quarantine finalize 失败时抛 `QuarantineApprovalPendingError`，保持 `approving` 供显式 repair；不得自动重试造成重复写入。
- reject/blocked/approved 等状态迁移在 `BEGIN IMMEDIATE` 事务和 revision 条件下执行；取消或异常不得留下虚假终态。
- `ReviewDetector` 是确定性启发式，不是安全分类器。敏感 marker、重复度和陈旧阈值只生成复核项，不直接删改 canonical。
- Review payload 通过 JSON 安全副本隔离可变对象；队列动作和 actor 信息仍属敏感运营数据。

## 依赖方向

reflection → quality application → 注入的 conversation/memory/processor 能力与 quality infrastructure。Page API 可以构造 review Store/Detector，但 quality 不反向依赖 API。grounding 规则复用 [`recall/processors/AGENTS.md`](../recall/processors/AGENTS.md)，canonical 写契约见 [`memory/AGENTS.md`](../memory/AGENTS.md)。

## 修改联动

- 改 quarantine schema/status：同步迁移、row mapper、revision、repair、备份引用校验和 API。
- 改批准流程：同步来源重验证、Atom 重建、canonical 幂等证据和 durable recovery 测试。
- 改质量 reason code：同步 processor grounding 输出、reflection 计数、Page 文案和诊断 allowlist。
- 改 Review 模型/动作：同步 `ReviewStore` JSON、队列 API、检测器和兼容 `core/review` 导出。
- 改包根导出：更新应用/领域/基础设施 owner 契约，禁止在旧 `core/review` 增加新实现。

## 最窄验证入口

```bash
python -m pytest -q tests/test_memory_quarantine.py
python -m pytest -q tests/test_quarantine_durable_recovery.py tests/test_api_quarantine.py
python -m pytest -q tests/test_review_detector.py tests/test_api_quality.py
python -m pytest -q tests/test_memory_quality_pipeline.py
```
