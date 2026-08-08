# M1 外部 attestor/HSM 集成契约（AST-28 Stage 1）

本文档定义 M1 production 放行的**受保护基础设施前置**：外部 attestor/HSM 的
输入输出接口、密钥仪式、私钥保管、认证传输、失败/重放处理、回滚与 pinned
公钥轮换方案，以及 OS 级观察证据的验收要求。verifier 侧裁决语义见
[`m1-gate.md`](./m1-gate.md)；本文档只描述 attestor 契约，不改变 gate 的
fail-closed 判定。

## 1. 目标与现状

- M1 bootstrap（PR #33）已合并：verifier-only gate 在无有效外部 attestation
  时对 production cut 稳定 `exit 2`；仓库内只保存 pinned **公钥**，签发私钥
  只存在于外部受保护 attestor/HSM。
- 本阶段不产生生产 attestation、不执行真实密钥轮换、不部署 HSM；attestor
  设施不存在时本阶段**明确阻塞**，任何伪造 production 证据的行为都被
  verifier 拒绝且违反本契约。
- 契约以 `scripts/m1_gate_core.py` 中的常量为代码权威；本文档的键集清单由
  `tests/test_m1_attestor_contract.py` 与代码交叉校验，防止文档/代码漂移。

## 2. attestor 输入（verifier 侧提供）

attestor 在签发前必须从受信来源取得以下输入（与本裁决严格一致，任何
不一致的 attestation 都会被 verifier 拒绝）：

| 输入 | 来源 | 说明 |
|---|---|---|
| run nonce | workflow `openssl rand -hex 32` → `M1_NONCE` → gate argv | 64 位小写 hex，单次使用 |
| base/pr_head/candidate commit | GitHub 事件 SHA（40 hex，OID 核对） | attestation 必须逐字节回显 |
| candidate tree | base-owned gate 计算 | 绑定 candidate 内容快照 |
| contract path/oid | base-owned contract（`scripts/m1_cuts/`） | 缺失时 attestation 不得绑定 contract |
| expected_edges | contract `expected_removed_edges` 精确边集 | 边集逐位置绑定 |
| probe_version / image | `M1_PROBE_VERSION` / `PROBE_IMAGE` pin digest | 版本与镜像必须一致 |
| run_id / run_attempt | GitHub 受信来源 | 报告与 staging 绑定 |

## 3. attestor 输出（attestation JSON 契约）

attestation 是顶层 JSON 对象，**顶层键必须恰为**以下 19 键（拒绝未知键/
缺失键，`ATTESTATION_KEYS`）：

```text
ATTESTATION_KEYS 精确键集（19 键）：
schema_version, probe_version, attestation, identity, nonce, signature, image,
base_commit, pr_head_commit, candidate_commit, candidate_tree, contract_path,
contract_oid, expected_edges, edges_checked, all_edges_confirmed, exit_code,
environment, evidence_sha256
```

- **签名**：`signature` 为 RSA-2048 PKCS#1 v1.5 / SHA-256，对**除 signature
  外全部键**的规范 JSON（`ensure_ascii=False, sort_keys=True, separators=(",", ":")`）
  签名；verifier 用仓库内 pinned 公钥常量时间验签。
- **身份**：`identity` 必须 ∈ `TRUSTED_PRODUCER_IDS = {"os_level_observer"}`；
  拒绝 `reference_producer` 与任意字符串自证。
- **nonce**：`nonce` 必须等于本次裁决的 run nonce（attestor 经受保护传输
  获取，必须回显；空 nonce 直接拒绝）。
- **环境**：`environment` 键集必须恰为：

```text
ATTESTATION_ENV_KEYS 精确键集（6 键）：
network, secrets, read_only_inputs, non_root, no_new_privileges, isolated_domain
```

  且取值固定：`network="none"`、`secrets=False`、`read_only_inputs=True`、
  `non_root=True`、`no_new_privileges=True`、`isolated_domain` 为非空字符串。
- **绑定**：base/head/candidate commit、candidate tree、contract path/oid、
  `expected_edges` 与 verifier 本次裁决精确一致；`edges_checked` 逐位置等于
  `expected_edges`，verdict ∈ `{confirmed_removed, edge_present, inconclusive}`。
- **自洽**：`exit_code ∈ {0,1,2}` 且与 verdicts 关系自洽（0 ⟺ 全 confirmed；
  1 ⟹ 存在 edge_present；2 ⟹ 存在 inconclusive）；`evidence_sha256` 与规范
  哈希一致；`image` 必须等于 pin digest。

## 4. 密钥仪式（key ceremony）

1. **生成**：在受保护 HSM（或等效隔离设施）内生成 RSA-2048（`e=65537`）
   密钥对；私钥生成后**立即标记为不可导出**。
2. **见证**：生成、备份与销毁步骤必须有两名以上见证人（或可审计的自动化
   仪式日志），记录时间、操作者、密钥指纹。
3. **pinning**：将公钥以 `{"n": <hex>, "e": "10001"}` 形式写入
   `scripts/m1_gate_core.py` 的 `M1_ATTESTOR_PUBLIC_KEY`；该文件是受保护
   路径，变更必须走 bootstrap 式人工独立评审（与 PR #33 同流程），不得
   由 attestor 或 PR runner 自行写入。
4. **审计**：每次轮换在仪式日志与 PR 描述中记录新公钥 SHA-256 指纹。

## 5. 私钥保管（custody）

- 私钥只存在于外部受保护 attestor/HSM；**绝不进入**仓库、PR runner、
  GitHub secret、CI 变量或任何日志（本仓库的
  `tests/test_m1_attestor_contract.py::test_no_private_key_material` 对
  仓库内全部受版本控制文件做 PEM 标记扫描）。
- HSM 需具备：非导出私钥、访问控制（quorum/双人控制）、审计日志、
  备份与恢复仪式、销毁流程。
- 私钥副本（如有备份）必须使用与 HSM 同等保护的加密存储。

## 6. 认证传输（nonce/evidence 传输）

- **nonce 交付**：workflow 每次运行生成 `M1_NONCE`（`openssl rand -hex 32`），
  经受保护通道交给 attestor（如 mTLS/OIDC 认证的 attestor API，或隔离
  agent runner 的受控执行域）；attestation 必须回显该 nonce。
- **evidence 返回**：attestation JSON 经受保护通道写入 gate 读取的
  `attestation_dir/attestation.json`（workflow 固定于 `RUNNER_TEMP`）；
  attestor 必须先完成非对称签名再交付，verifier 只认 pinned 公钥验签结果。
- **通道认证**：attestor 必须认证调用方为受信 GitHub 运行（mTLS/OIDC/
  受控 runner 身份）；不得使用任何会泄露私钥或 nonce 的明文通道。
- bootstrap 阶段不存在该设施，`attestation.json` 缺失 → production 稳定
  `exit 2`（fail-closed），不会伪造或降级。

## 7. 失败与重放处理

- **缺失/未认证**：无 attestation 或签名/身份/nonce/绑定任一不符 →
  verifier 在 policy 判断**之前**稳定 `exit 2`。
- **重放**：nonce 单次使用；attestor 必须记录已用 nonce 并拒绝重复签发
  （拒绝历史 attestation 重放进新 run）；verifier 侧每次运行生成新 nonce。
- **attestor 不可用**：production cut 保持 `exit 2`（阻塞），不允许降级
  为“无证据放行”；阻塞状态与所需设施见第 10 节。

## 8. pinned 公钥轮换方案

当前 verifier 只支持单一 pinned 公钥，轮换必须**不改变 verifier 契约**
（`ATTESTATION_KEYS`、签名算法、身份名单、环境键集都不变）：

1. **准备**：在 HSM 生成新密钥对（RSA-2048，`e=65537`），完成第 4 节仪式。
2. **新钥先行**：attestor 用新私钥试签一份测试 attestation，本地用新公钥
   验签通过。
3. **pin PR**：将 `M1_ATTESTOR_PUBLIC_KEY` 替换为新公钥的 PR，走
   bootstrap 式人工独立评审（受保护路径）；合并前 attestion 仍用旧钥签发
   （verifier 只认旧 pin，行为不中断）。
4. **切换**：pin PR 合并后，attestor 切换到新私钥签发；下一个 run 即用
   新 pin 验签。
5. **观察与退役**：观察窗口（建议 ≥ 2 周）内保留旧钥于 HSM（仅验签审计
   用），窗口期满后按 HSM 销毁流程退役。
6. **回滚**：若切换后出现异常，回滚 = 还原 pin PR（旧公钥重新 pin，同样
   走 bootstrap 式评审）并令 attestor 切回旧私钥；回滚不改变契约键集。

## 9. OS 级观察证据（attestor 侧要求）

- attestor 必须是**独立父进程/OS 级观察者**：在候选执行域之外收集证据，
  候选代码不得参与证据生成或签名（参考实现 `scripts/m1_gate_probe.py`
  只作参考 producer，其解释器内证据**不得**用于生产裁决）。
- 观察者必须实际验证隔离域属性并写入 `environment`（network 断开、无
  secrets、只读输入、非 root、no-new-privileges、隔离域标识），任何一项
  无法确认即签发 `exit_code=2`（inconclusive），不得伪造为通过。
- 证据必须完整绑定 candidate（commit/tree/contract/边集）并经 pinned 公钥
  对应私钥签名；OS 级审计痕迹（进程树、容器 ID、资源边界、输出上限）由
  attestor 留存备查，不进入 attestation JSON。

## 10. 阻塞项与 owner/DevOps 交接

本阶段（Stage 1）**不落地**生产 attestor，以下均为阻塞项，需外部授权/设施
后才能推进：

1. **HSM/attestor 设施**：无受保护 HSM、无 attestor 服务、无密钥仪式执行
   ——需要 owner/DevOps 提供或授权采购与部署（FIPS 140-2 L2+ 或等效）。
2. **密钥仪式**：`M1_ATTESTOR_PUBLIC_KEY` 目前为 bootstrap 占位 pin；真实
   密钥仪式（第 4 节）未执行，生产 pinning 需要人工见证与 bootstrap 式评审。
3. **受保护传输**：nonce/evidence 的 mTLS/OIDC 通道与 attestor API 未部署。
4. **workflow 集成**：`m1-gate.yml` 目前只消费 `attestation_dir` 路径，
   没有调用 attestor 的步骤；attestor 设施落地后需在 workflow 增加受保护
   的 attestation 获取步骤（该变更属受保护路径，需人工评审）。
5. **测试落库**：`tests/` 不在 governance-only allowlist 内；契约测试合并
   需要随 production cut（需 attestation）或治理评审放行。

交接条件：只有外部受保护 attestor 能产出经 pinned 公钥验证的签名
attestation，且由**独立复验方**（非 attestor 自身）完成复验后，才可推进
Stage 2；否则本子任务保持阻塞。

## 11. 验收与验证（本阶段可执行部分）

- 契约键集/格式/身份名单与代码常量交叉校验：
  `uv run --locked python -m pytest tests/test_m1_attestor_contract.py -q`
- verifier 行为回归（签名/身份/nonce/绑定/fail-closed）：
  `uv run --locked python -m pytest tests/test_m1_gate.py tests/test_m1_gate_probe.py tests/test_m1_gate_safety.py tests/test_m1_gate_report.py -q`
- 静态检查：`uv run --locked ruff check <新增文件>`、pre-commit。
- 无密钥 fail-closed smoke：生产 cut 不带 attestation 运行 gate 必须
  `exit 2`（见 `tests/test_m1_gate.py::test_production_missing_attestation_exit2_before_policy`）。
