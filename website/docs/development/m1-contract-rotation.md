# M1 canonical contract 生成、验证与连续 cut 轮换协议（AST-40）

本文定义 M1 阶段 base-owned contract 的 canonical 生成、验证与连续 cut
轮换协议，闭合 AST-37 审计发现的 High：首个 M1 cut 之后必须存在合法的
contract 轮换路径，否则后续 production cut 无法取得绑定当前 base 的
contract，门禁流程停滞。

协议由独立工具 `scripts/m1_contract_cut.py`（generator + validator）承载，
**只读复用** M1 verifier 既定接口（contract schema/锚点/facts 口径），
不修改任何 verifier 判定逻辑（`scripts/m1_gate_*`、baseline、AstrBot API
与数据库 schema 均不在本协议改动范围内）。

## 连续 cut 协议总览

每个 production cut 都由一份 **base-owned contract** 声明：contract 精确
绑定其 base（commit/tree/facts），并声明该 production cut 的精确 change
manifest 与被移除边。cut 序列如下循环：

```text
genesis contract（首个 cut，绑定初始 base）
  -> production 1（verifier 复核 contract 绑定并放行）
  -> rotation（新 contract 单文件替换，绑定合并后的新 base）
  -> production 2
  -> rotation -> production 3 -> ...
```

- **genesis**：base 尚无 contract 时，governance-only PR 以 contract-only
  方式新增首份 contract（现有 verifier 的 governance contract-add 路径）。
- **rotation**：base 已有 contract 时，governance-only PR 以 **单文件 `M`**
  方式替换现役 contract。每次 production cut 合并后 base 必然推进，因此
  每个新 production cut 都要求一次 rotation。
- **production cut**：不得修改 contract；未绑定当前 base 的 contract 无法
  通过 verifier 的锚点/facts 复核（fail-closed）。

## contract rotation grammar

轮换 PR（base tree -> candidate tree）必须同时满足以下全部条件：

1. **contract-only 单文件变更**：变更集恰好为一份 contract 文件（`M` 状态）；
   base 与 candidate 各恰好有一份 `.json` contract（`scripts/m1_cuts/` 下
   递归计数，与 verifier 同口径），且路径相同；除该文件外不得有任何其他
   变更（不得夹带 docs、tests、scripts、core/ 或受保护路径）。
2. **精确绑定当前 base**：新 contract 的 `base_commit` 必须等于 PR base
   提交、`base_tree` 必须等于 PR base tree（语义不符即拒绝，exit 1）。
3. **锚点真实自洽**：新 contract 的 `base_commit`/`base_tree` 必须是真实
   Git 对象且 `base_commit^{tree} == base_tree`（结构不符 exit 2）。
4. **facts 一致**：新 contract 的 `base_graph_facts_sha256` 必须等于对
   base tree 按 verifier 同口径（物化并移除 contract 目录后全量分析）的
   facts 摘要。
5. **真实轮换**：新 contract 与现役 contract 内容必须不同（拒绝自轮换；
   candidate tree 与 base tree 相同即拒绝）。
6. **schema 有效**：新 contract 通过 verifier 的 `_validate_contract_payload`
   校验（含 `change_manifest_sha256` 内部自洽）。

以下情形一律**拒绝**（validator 输出 reasons 并 exit 1；结构歧义 exit 2）：

- `base_commit`/`base_tree`/`base_graph_facts_sha256` 与当前 base 不符；
- 变更集包含 contract 以外的任何路径（非 contract-only）；
- candidate 无 contract（删除）或出现第二份 contract（结构歧义，exit 2）；
- 新 contract 与现役 contract 相同（自轮换）；
- 提供的 manifest 与确定性重算不一致（篡改或过期）。

## 工具：scripts/m1_contract_cut.py

从仓库根目录执行（stdlib-only，仅读复用 verifier 模块）：

```text
# 生成 canonical contract（genesis：base 无 contract；rotation：恰一份）
python scripts/m1_contract_cut.py generate \
  --repository . \
  --base-commit <40-hex> \
  --pr-head <40-hex> \
  --expected-removed-edges-file tests/fixtures/m1_contract/removed-edges.json \
  --cut-id <cut-id> \
  --output scripts/m1_cuts/<cut-id>.json \
  --manifest-output <manifest-path>

# 校验轮换 PR（base/candidate 各为完整提交 SHA）
python scripts/m1_contract_cut.py validate-rotation \
  --repository . \
  --base-commit <PR-base> \
  --candidate-commit <merge-candidate> \
  --output <manifest-path>

# 复验既有 manifest（独立 reviewer 连续复验入口）
python scripts/m1_contract_cut.py validate-rotation \
  --repository . \
  --base-commit <PR-base> \
  --candidate-commit <merge-candidate> \
  --manifest <manifest-path>
```

- `generate`：`--pr-head` 与 `--expected-changes-file` 二选一；`--pr-head`
  时以 `diff(base_tree -> head_tree)` 计算 `expected_changes`（规范 7 键
  形态，与 verifier 生产 cut 逐项比较口径一致）。kind 自动判定：
  base 无 contract 为 `genesis`，恰一份为 `rotation`。
- 退出码：`0` 通过/生成成功；`1` 轮换被 grammar 拒绝；`2` 无法形成可信
  裁决（工具/输入/契约/结构错误，fail-closed）。
- 生成与验证均**确定性**：同输入产出逐字节相同的 contract 与 manifest
  （固定字段顺序、sort_keys、无时间戳）；同一次轮换由 `generate` 产出的
  manifest 与 `validate-rotation` 重算的 manifest 完全一致，可互相复验。

## manifest 规格

manifest 是 validator/generator 的**确定性产物**（证据 artifact），不得
提交进 `scripts/m1_cuts/`（verifier 要求该目录恰好一份 `.json` contract）。
建议随轮换 PR 或 issue 附件保存，独立 reviewer 用 `--manifest` 复验。

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 固定为 1 |
| `kind` | `genesis`（首个 cut）或 `rotation` |
| `contract_path` | contract 的 canonical 仓库相对路径 |
| `old_contract_oid` | 现役 contract 的 Git blob OID（genesis 为 `null`） |
| `new_contract_oid` | 新 contract 的 Git blob OID |
| `new_contract_sha256` | 新 contract 字节的 SHA-256 |
| `base_commit` / `base_tree` | 新 contract 绑定的 base 锚点 |
| `base_graph_facts_sha256` | base 全量分析 facts 摘要 |
| `change_manifest_sha256` | `expected_changes` 的规范哈希 |
| `expected_removed_edges` | 声明的被移除边集 |

校验器按同一输入重算 manifest，与提供值任何字段不一致即拒绝（篡改检测）。

## 首个 contract：m1-genesis（不可变）

`scripts/m1_cuts/m1-genesis.json` 是绑定本仓库当前 base 的首个（genesis）
contract，作为轮换链的**不可变起点**：

- `base_commit`/`base_tree`/`base_graph_facts_sha256` 精确绑定生成时的
  base，可通过 `test_committed_genesis_is_valid_and_reproducible` 随时复现
  与复核（输入 fixture 见 `tests/fixtures/m1_contract/`）。
- 其 `expected_changes` 为**显式占位 manifest**（与 M1 verifier 测试
  fixtures 同形态，指向 `scripts/m1_cuts/__placeholder__.json`，blob 为
  占位哈希）：genesis 只锚定 base，**任何真实 production cut 都不可能
  直接匹配该占位 manifest**——production 放行前必须先执行一次 rotation，
  用绑定当前 base、覆盖真实 diff 的 cut-specific contract 替换它
  （fail-closed by design，杜绝“跳过轮换”的静默路径）。

## 回滚规则

- 回滚是**指向旧状态的轮换**：以当前 base 重新生成（或从 Git 历史取回
  内容）绑定**当前 base**、声明回滚后 production 状态对应 diff 的
  contract，并走同一 rotation grammar 单文件 `M` 替换；validator 与
  manifest 复验口径不变。
- **禁止**直接复用历史 contract 文件：历史 contract 绑定的 base 已过期，
  无法通过“精确绑定当前 base”检查（fail-closed），必须重新生成。
- contract 本身从不“删除”：Git 对象历史保留全部版本，回滚只改变现役
  指针（base tree 中的单份 contract）。

## 非法轮换反例

`tests/test_m1_contract_cut.py` 固化以下反例：错 `base_commit`、错
`base_tree`、错 `base_graph_facts_sha256`、内部 `change_manifest_sha256`
破坏（exit 2）、夹带非 contract 路径、自轮换（candidate tree 相同）、
删除 contract、candidate 出现第二份 contract（exit 2）、篡改 manifest。

## 与 M1 verifier 的关系与残余风险

- verifier 的 governance contract-add 路径（`scripts/m1_gate_analysis.py`
  `_validate_governance_contract`）要求 base 无 contract，因此**当前
  verifier 本身对轮换 PR 仍返回 exit 2**——这是 AST-37 记录的已知缺口。
  本协议的工具、fixtures 与测试已让轮换可被确定性复验；让 verifier 直接
  放行轮换 PR 需要一处最小接口变更（放行“base 已有恰一份 contract 时、
  契约绑定/范围校验通过的 contract-only 单文件 M”），该变更属于 verifier
  判定逻辑，超出 AST-40 文件所有权，留给后续阶段评估。
- production cut 的 attestation（受信身份/签名/nonce/currentness）仍由
  受保护 attestor 前置（见 `m1-gate.md` production 前置），本协议不替代。
