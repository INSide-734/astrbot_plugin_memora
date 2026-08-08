# M1 阶段门禁（verifier-only bootstrap，受信双树 M1 gate）

M1 是旧技术层断环阶段。每个生产 cut 的 PR 由 `.github/workflows/m1-gate.yml`
在 CI 上执行只读受信双树裁决（`scripts/check_m1_gate.py`），不对工作树做任何假设：

```text
PYTHONPATH="$CHECKER_DIR/scripts" python3 "$CHECKER_DIR/scripts/check_m1_gate.py" \
  --repository "$CANDIDATE_REPOSITORY" \
  --base-commit "$BASE_SHA" \
  --pr-head-commit "$PR_HEAD_SHA" \
  --candidate-commit "$MERGE_SHA" \
  --report-dir "$REPORT_DIR" \
  --probe-result "$ATTESTATION" \
  --run-id "$GITHUB_RUN_ID" \
  --run-attempt "$GITHUB_RUN_ATTEMPT"
```

模块划分（全部 stdlib）：`check_m1_gate.py`（CLI 入口与最外层收口）、
`m1_gate_core.py`（Git/contract/scope/动态依赖扫描/受保护信任根）、
`m1_gate_analysis.py`（双树分析/governance/attestation 严格复核）、
`m1_gate_probe.py`（**受保护参考 producer**，不参与 bootstrap 裁决）、
`m1_gate_report.py`（原子报告发布与受信 consumer）。

## verifier-only bootstrap（本阶段核心语义）

- **workflow 不执行候选、不自生成探针证据**：没有 probe step、没有 docker、
  没有候选执行域；`--probe-result` 指向外部受信 attestation 路径。
- **production cut 必须携带外部受信、可认证且绑定当前 candidate 的
  attestation**：bootstrap 阶段该基础设施尚未存在，attestation 文件缺失
  → production 一律 **exit 2**（fail-closed）；governance-only PR 不受影响。
- **OS 级观察者/producer 是受保护基础设施前置**（production 放行前必须
  落地）：`m1_gate_probe.py` 只是参考实现（含隔离域与资源边界：
  `--network none`、`--cap-drop ALL`、pids/memory/CPU/FD/tmpfs 上限、
  非 root、no-new-privileges、只读挂载、输出上限），其解释器内证据
  **不得**用于生产裁决；生产 attestation 必须由独立父进程/OS 级观察者
  生成并经受保护基础设施签名/认证。

## 信任根（base-owned）

- workflow 使用 `pull_request_target`：checkout 仅 `ref: base.sha`，候选
  只作为 Git 对象数据被解析。
- 先校验 PR number 为正整数、三个事件 SHA 为完整 40 位十六进制；再从
  base repo 定向 fetch `refs/pull/<n>/{head,merge}` 到固定 local ref 并
  逐一核对 OID（完整对象历史，非浅克隆）；不符即 exit 2。
- 并发按 PR number 分组；全部 action 固定到完整 commit SHA。
- 从真实 `BASE_SHA` 导出完整 checker（CLI + core + analysis + report +
  architecture 依赖；probe 模块是受保护参考 producer，不导出执行）后
  执行；导出缺失任一文件即 exit 2，无候选 fallback。
- **受保护信任根**：整个 `.github/workflows/`、checker 全部模块（含
  `m1_gate_probe.py`、`m1_gate_report.py`）、schema/锁定/运行资产；
  候选修改任一均 exit 1（防止后续 cut 洗白裁决/发布逻辑）。
- bootstrap PR（base 尚无 checker）在 workflow 下明确 exit 2，由现有保护
  与人工独立复审合并。

## attestation 认证与严格复核（base-owned verifier）

`m1_gate_analysis._validate_probe_evidence` 认证 + 精确 schema + 完整
绑定，逐项 fail-closed（任何缺失/不一致 exit 2）：

- **签名（非对称）**：`signature` 为 RSA-2048 PKCS#1 v1.5 / SHA-256，
  用**仓库内 pinned 公钥**（`M1_ATTESTOR_PUBLIC_KEY`）验签（常量时间
  比较）；**签发私钥只存在于外部受保护 attestor，绝不进入仓库/PR
  runner/`reference_producer`** → 无人能伪造签名，production 在
  attestor 落地前稳定 exit 2。**拒绝 `reference_producer` 等自证**。
- **身份**：`identity` 必须在受保护 attestor 受信名单
  （`{"os_level_observer"}`），任意字符串身份一律拒绝。
- **nonce**：`nonce` 必须等于本裁决 run nonce（workflow 每次运行生成，
  经受保护传输交给 attestor；attestation 必须回显；GITHUB_ENV 写入名与
  gate argv 引用名一致——`M1_NONCE`）。
- **受保护传输**：attestation 只能由持有私钥的外部受保护 attestor 产生；
  workflow 不执行候选、不自生成证据。
- **精确键集（每层）**：顶层键必须恰为 `ATTESTATION_KEYS`（拒绝未知键/
  缺失键）；`edges_checked` 每项键集恰为 `{edge, verdict, loaded_targets,
  error}` 且类型精确（edge=2 元素字符串列表、verdict 合法、loaded_targets
  字符串列表、error str|None）；`attestation` 非空白字符串；
  `type(exit_code) is int` 且 ∈ {0,1,2}；`environment` 键集精确且与隔离域
  契约一致。
- `image` 必须等于 pin digest（`python:3.12-slim@sha256:d657ab0a…`）
- `probe_version`、base/head/candidate commit、candidate tree、
  contract path/oid 与本次裁决精确一致
- `expected_edges` 与 base contract 精确一致；`edges_checked` **逐位置**
  与 expected_edges 一一相等，verdict 合法
- exit 关系：`exit_code=0` ⟺ `all_edges_confirmed` ⟺ 全部
  `confirmed_removed`（空边集视为 confirmed）；`1` ⟹ 存在 `edge_present`；
  `2` ⟹ 存在 `inconclusive`
- `evidence_sha256` 与规范哈希一致
- **production 任何 policy 判断之前**：缺失或未认证 attestation 一律
  稳定 exit 2（即使候选同时违反 SCC/edge policy，也不会先给出 exit 1）。

## 不变量

- 候选必须是真实 merge（父关系 + merge-base）；浅克隆/对象缺失 exit 2。
- diff 由 gate 从两棵 tree 自行取得（NUL-safe、按字节、含 R/C 双侧 mode）；
  不接受 `--files`/baseline 写入/policy update/候选 scope 白名单。
- symlink/gitlink/T/mode 漂移一律 exit 2；fresh 双树分析断言
  `candidate.largest_scc < base.largest_scc`。
- 生产 cut 必须引用 base-owned contract（锚点真实/自洽/祖先；facts 一致；
  精确 manifest 逐项匹配；候选不得改 contract；未覆盖 production 变更拒绝）。
- 动态依赖不做名称黑名单，AST 上下文 fail-closed：直接调用加载内置；
  命名空间下标（builtins/`builtins.__dict__`/globals()/vars()/sys.modules，
  组合表达式折叠，不可折叠 fail-closed）；`builtins.__dict__` 的
  get/`__getitem__`；getattr/hasattr 加载键；**保守 alias/dataflow**
  （`d = builtins.__dict__`、`b = __builtins__; d = b.__dict__`、
  `import builtins as b`、`from builtins import __import__ as imp` 等，
  不动点迭代）；未知动态取值 fail-closed；`re.compile` 豁免。
- governance-only PR deny-by-default；contract-add PR 必须 contract-only，
  并按 production 同口径验证 candidate facts（两阶段回归覆盖）。

## 退出码与报告契约

- `0` = 全部不变量成立（或 governance-only）；`1` = policy 阻断；
  `2` = 无法形成可信裁决（工具/对象/契约/超时/路径解析/attestation
  缺失或绑定不符）。
- 报告目录由受信 `run_id/run_attempt` 构造，必须为空目录；五份原子 JSON
  逐份 fsync，O_EXCL `COMMITTED`，最后原子写 `current.json`（generation/
  provenance/exit code/五文件 SHA-256）；CLI 最外层收口路径解析与
  envelope 失败，任何异常稳定 exit 2、无 traceback。
- **受信 consumer**（`m1_gate_report.py --verify/--publish`）：**持有目录
  FD（`O_DIRECTORY|O_NOFOLLOW`）**，全部验证与发布经 `openat(dir_fd)/
  fstat/fd 列表`（目录替换竞态闭合）；目录路径任一组件为 symlink 即拒绝；
  目录项经 lstat 拒绝一切 symlink/非 regular 项；`current.json` 与五份
  报告经 openat/fstat/同 fd 读取校验并返回**已验证字节快照**；**manifest
  严格绑定受信 `run_id/run_attempt` 与 generation**；**`COMMITTED` 校验
  固定内容 `"committed\n"`**；发布从快照写入（绝不重新读取 source 路径），
  staging 以 run-id/attempt 命名并 fsync，**对 staging 全量复验后才原子
  rename**；artifact 只在 consumer `rc == 0` 时上传。

## 范围约束

新增/修改文件均 ≤800 行（docs ≤400）：CLI 114、core 800、analysis 706、
probe 305、report 413、workflow 203、tests 722/799/…/181、docs 127。
不修改 baseline、`architecture.toml`、`check_feature_gate.py` 或生产代码。

## production 前置（不在 bootstrap 范围内，放行前必须落地）

- **currentness**：订阅 base retarget/编辑事件，并在最终裁决前重新读取
  当前 PR base/head/merge SHA；bootstrap 落到受保护默认分支，配置
  strict up-to-date required check/merge queue、禁止 bypass/直推。
- **fork 演练**：真实 fork、force-push、retarget、base 更新与 merge-SHA
  绑定场景实测。
- **OS 级 attestation producer**：独立父进程/OS 级观察者；**签发私钥由
  外部受保护 attestor/HSM 持有，仓库与 runner 只保留 pinned 公钥**（不得
  把私钥放入任何仓库 secret）；run nonce 经受保护传输 + canonical
  contract generator/validator + 连续 cut 轮换协议；生产 attestation
  必须以受信身份签名并完整绑定 candidate，由受保护基础设施提供
  （bootstrap 的 verifier 已就绪，attestor 落地前 production 一律
  exit 2）。attestor/HSM 的输入输出、密钥仪式、私钥保管、认证传输、
  失败/重放处理、回滚与 pinned 公钥轮换方案见
  [`m1-attestor-contract.md`](./m1-attestor-contract.md)。
