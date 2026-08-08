"""M1 gate 分析编排与报告模块：tree 物化分析、受信 run_id/run_attempt
空目录 + 原子 current manifest 的五报告、deny-by-default governance
校验与 run_gate 主编排。探针编排见 check_m1_gate.run_probe。"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

try:  # 直接从仓库根执行脚本时，scripts 目录在 sys.path 首位。
    from architecture_analysis import analyze_repository
    from architecture_core import (
        ArchitectureConfigError,
        ArchitectureSourceError,
        load_config,
    )
    from architecture_snapshots import facts_sha256, stable_facts_payload
    from m1_gate_core import (
        ATTESTATION_ENV_KEYS,
        ATTESTATION_KEYS,
        CONTRACT_DIR,
        M1_PROBE_VERSION,
        PROBE_IMAGE,
        SCHEMA_VERSION,
        TRUSTED_PRODUCER_IDS,
        M1GateError,
        _canonical_change,
        _changed_paths,
        _check_edges,
        _decode_utf8,
        _is_contract_path,
        _is_governance_allowed,
        _is_protected,
        _list_tree_files,
        _load_contract,
        _read_tree_file,
        _scan_dynamic_import_escapes,
        _validate_change_modes,
        _validate_contract_anchors,
        _validate_contract_payload,
        _validate_provenance,
        attestation_signature_valid,
        change_manifest_sha256,
        evidence_sha256,
        git_diff_tree_raw,
    )
    from m1_gate_report import _emit, _ensure_empty_report_dir, _gate_fail
except ImportError:  # pytest 以包模块导入时使用完整路径。
    from scripts.architecture_analysis import (
        analyze_repository,  # type: ignore[no-redef]
    )
    from scripts.architecture_core import (  # type: ignore[no-redef]
        ArchitectureConfigError,
        ArchitectureSourceError,
        load_config,
    )
    from scripts.architecture_snapshots import (  # type: ignore[no-redef]
        facts_sha256,
        stable_facts_payload,
    )
    from scripts.m1_gate_core import (  # type: ignore[no-redef]
        ATTESTATION_ENV_KEYS,
        ATTESTATION_KEYS,
        CONTRACT_DIR,
        M1_PROBE_VERSION,
        PROBE_IMAGE,
        SCHEMA_VERSION,
        TRUSTED_PRODUCER_IDS,
        M1GateError,
        _canonical_change,
        _changed_paths,
        _check_edges,
        _decode_utf8,
        _is_contract_path,
        _is_governance_allowed,
        _is_protected,
        _list_tree_files,
        _load_contract,
        _read_tree_file,
        _scan_dynamic_import_escapes,
        _validate_change_modes,
        _validate_contract_anchors,
        _validate_contract_payload,
        _validate_provenance,
        attestation_signature_valid,
        change_manifest_sha256,
        evidence_sha256,
        git_diff_tree_raw,
    )
    from scripts.m1_gate_report import (  # type: ignore[no-redef]
        _emit,
        _ensure_empty_report_dir,
        _gate_fail,
    )


def _analyze_materialized(
    materialized_root: Path,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """对已物化 tree 执行全量分析并返回事实摘要（分析前移除
    scripts/m1_cuts，避免 contract 自指影响 facts）。"""

    contract_dir = materialized_root / CONTRACT_DIR
    if contract_dir.is_dir():
        shutil.rmtree(contract_dir)
    report = analyze_repository(materialized_root, config)
    facts = stable_facts_payload(report)
    return {
        "facts_sha256": facts_sha256(facts),
        "largest_scc": report["dependency_graph"]["largest_scc"],
        "scc_count": len(report["dependency_graph"]["sccs"]),
        "sccs": report["dependency_graph"]["sccs"],
        "edge_count": len(report["dependency_graph"]["edges"]),
        "edges": report["dependency_graph"]["edges"],
        "file_count": report["summary"]["file_count"],
        "total_lines": report["summary"]["total_lines"],
        "parse_errors": report["violations"]["parse_errors"],
        "forbidden_import_count": len(report["violations"]["forbidden_imports"]),
        "snapshot_facts_sha256": facts_sha256(
            {
                "snapshots": report["snapshots"],
                "public_contracts": report["public_contracts"],
            }
        ),
    }


def git_materialize_tree(root: Path, tree: str, destination: Path) -> None:
    """导出 tree 到独立目录（拒绝链接/穿越），archive/解包异常一律 exit 2。"""

    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise M1GateError("tree 导出目标必须为空目录")
    destination.mkdir(parents=True, exist_ok=True)
    try:
        archive = subprocess.run(
            ["git", "archive", "--format=tar", tree],
            cwd=root,
            check=False,
            capture_output=True,
            timeout=60.0,
        )
    except subprocess.TimeoutExpired as exc:
        raise M1GateError("Git archive 超时") from exc
    except OSError as exc:
        raise M1GateError("无法执行 Git archive") from exc
    if archive.returncode != 0:
        raise M1GateError("Git archive 失败")
    try:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as handle:
            for member in handle.getmembers():
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise M1GateError("tree 归档包含不安全路径")
                if member.issym() or member.islnk():
                    raise M1GateError("tree 归档包含链接成员")
            handle.extractall(destination, filter="data")
    except M1GateError:
        raise
    except (tarfile.TarError, OSError, UnicodeError) as exc:
        raise M1GateError(f"tree 归档无法解包: {exc}") from exc


def _validate_governance_contract(
    root: Path,
    base_commit: str,
    base_tree: str,
    candidate_tree: str,
    changes: Sequence[Mapping[str, Any]],
    contract_paths_changed: Sequence[str],
    base_facts: Mapping[str, Any],
    candidate_facts: Mapping[str, Any],
) -> list[str]:
    """校验 governance-only PR 新增的 contract（deny-by-default 配套）。

    contract-add PR 必须 contract-only（精确只含一份 contract 文件）；
    并按 production 同口径验证“移除 contract 后”的 candidate facts 与
    base 一致，保证 governance pass 后首个 production cut 可直接可用。
    结构/schema 歧义 → M1GateError（exit 2）；仅事实/范围不匹配 →
    返回阻断原因（exit 1）。
    """

    base_entries = _list_tree_files(root, base_tree, CONTRACT_DIR)
    if base_entries:
        raise M1GateError(
            f"governance-only 新增 contract 时 base 不得已有 contract（{len(base_entries)} 份）"
        )
    candidate_entries = _list_tree_files(root, candidate_tree, CONTRACT_DIR)
    candidate_contracts = [
        entry for entry in candidate_entries if entry["path"].endswith(".json")
    ]
    if len(candidate_contracts) != 1:
        raise M1GateError(
            f"candidate 必须恰好新增一份 contract，实际 {len(candidate_contracts)}"
        )
    entry = candidate_contracts[0]
    if entry["mode"] not in {"100644", "100755"}:
        raise M1GateError(
            f"contract 必须是 regular blob（mode {entry['mode']}）: {entry['path']}"
        )
    changed = set(contract_paths_changed)
    if changed != {entry["path"]}:
        raise M1GateError(
            f"contract 变更范围不符: {sorted(changed)} != [{entry['path']}]"
        )
    for item in changes:
        path = item.get("new_path") or item.get("old_path")
        if _is_contract_path(str(path)) and item["status"] not in {"A", "M"}:
            raise M1GateError(f"contract 只允许 A/M，实际 {item['status']}: {path}")
    try:
        payload = json.loads(
            _decode_utf8(
                _read_tree_file(root, candidate_tree, entry["path"]), label="contract"
            )
        )
    except (json.JSONDecodeError, M1GateError) as exc:
        raise M1GateError(f"candidate contract 无法解析: {exc}") from exc
    _validate_contract_payload(payload, path=entry["path"])
    reasons: list[str] = []
    all_changed = _changed_paths(changes)
    if all_changed != {entry["path"]}:
        reasons.append(
            f"governance contract 必须 contract-only（额外变更）: "
            f"{sorted(all_changed - {entry['path']})}"
        )
    if str(payload["base_commit"]) != base_commit:
        reasons.append(
            f"candidate contract.base_commit 与当前 base 不符: {payload['base_commit']}"
        )
    if str(payload["base_tree"]) != base_tree:
        reasons.append(
            f"candidate contract.base_tree 与当前 base tree 不符: {payload['base_tree']}"
        )
    if str(payload["base_graph_facts_sha256"]) != base_facts["facts_sha256"]:
        reasons.append(
            "candidate contract.base_graph_facts_sha256 与 base 全量分析事实不一致"
        )
    if candidate_facts["facts_sha256"] != base_facts["facts_sha256"]:
        reasons.append(
            "candidate 移除 contract 后的事实与 base 不一致（production 同口径验证）"
        )
    return reasons


def _validate_probe_evidence(
    evidence: Mapping[str, Any],
    *,
    provenance: Mapping[str, Any],
    contract: Mapping[str, Any],
    attestation_nonce: str,
) -> None:
    """base-owned verifier：认证 + 精确 schema + 完整绑定，逐项 fail-closed。

    认证（签名/身份/nonce/受保护传输）：
    - 精确键集（拒绝未知键/缺失键）；attestation 必须非空白字符串
    - identity 必须在受保护 attestor 受信名单（拒绝 reference_producer
      与任意字符串自证）
    - nonce 必须等于本裁决提供的 run nonce（且 nonce 非空）
    - signature 用仓库内 pinned RSA-2048 公钥验签（外部 attestor/HSM
      持有私钥；仓库与 runner 仅持有公钥，无法伪造签名）
    结构：environment 精确键集与隔离域契约一致、image=pin digest、
    version/base/head/tree/contract path+oid 精确一致、expected_edges 与
    contract 精确一致、edges_checked 逐位置相等、exit_code 必须 int 且
    与 verdicts 关系自洽、evidence_sha256 一致。任何不符 M1GateError。
    """

    if not isinstance(evidence, dict):
        raise M1GateError("probe-result 不是 JSON 对象")
    if set(evidence) != ATTESTATION_KEYS:
        raise M1GateError(
            f"probe-result 键集不精确: {sorted(set(evidence) ^ ATTESTATION_KEYS)}"
        )
    if (
        not isinstance(evidence.get("attestation"), str)
        or not evidence["attestation"].strip()
    ):
        raise M1GateError("probe-result attestation 必须是非空白字符串")
    if evidence.get("identity") not in TRUSTED_PRODUCER_IDS:
        raise M1GateError(
            f"probe-result identity 不在受信 producer 名单: {evidence.get('identity')}"
        )
    if not attestation_nonce:
        raise M1GateError("本裁决缺少 attestation nonce（无法认证）")
    if evidence.get("nonce") != attestation_nonce:
        raise M1GateError(
            f"probe-result nonce 与本次裁决不符: {evidence.get('nonce')} != {attestation_nonce}"
        )
    if not attestation_signature_valid(evidence):
        raise M1GateError("probe-result 签名认证失败（pinned 公钥验签不符）")
    environment = evidence.get("environment")
    if not isinstance(environment, dict):
        raise M1GateError("probe-result environment 缺失或类型无效")
    if set(environment) != ATTESTATION_ENV_KEYS:
        raise M1GateError(
            f"probe-result environment 键集不精确: "
            f"{sorted(set(environment) ^ ATTESTATION_ENV_KEYS)}"
        )
    expected_env = {
        "network": "none",
        "secrets": False,
        "read_only_inputs": True,
        "non_root": True,
        "no_new_privileges": True,
    }
    for key, expected in expected_env.items():
        if environment.get(key) != expected:
            raise M1GateError(f"probe-result environment.{key} 与隔离域契约不一致")
    if not isinstance(environment.get("isolated_domain"), str) or not environment.get(
        "isolated_domain"
    ):
        raise M1GateError("probe-result environment.isolated_domain 缺失")
    if evidence.get("image") != PROBE_IMAGE:
        raise M1GateError(
            f"probe-result image 与 pin digest 不一致: {evidence.get('image')}"
        )
    if evidence.get("probe_version") != M1_PROBE_VERSION:
        raise M1GateError(
            f"probe-result probe_version 不受支持: {evidence.get('probe_version')}"
        )
    for field, expected in (
        ("base_commit", provenance["base_commit"]),
        ("pr_head_commit", provenance["pr_head_commit"]),
        ("candidate_commit", provenance["candidate_commit"]),
        ("candidate_tree", provenance["candidate_tree"]),
    ):
        if str(evidence.get(field)) != str(expected):
            raise M1GateError(
                f"probe-result 绑定 {field} 与本次裁决不一致: "
                f"{evidence.get(field)} != {expected}"
            )
    if contract is None:
        if (
            evidence.get("contract_path") is not None
            or evidence.get("contract_oid") is not None
        ):
            raise M1GateError("probe-result 绑定 contract 但 base 无 contract")
        expected_edges: list[list[str]] = []
    else:
        if evidence.get("contract_path") != contract.get("_contract_path"):
            raise M1GateError(
                f"probe-result contract_path 与 base contract 不一致: "
                f"{evidence.get('contract_path')} != {contract.get('_contract_path')}"
            )
        if evidence.get("contract_oid") != contract.get("_contract_oid"):
            raise M1GateError("probe-result contract_oid 与 base contract 不一致")
        expected_edges = [
            list(edge) for edge in contract.get("expected_removed_edges", [])
        ]
    if [list(edge) for edge in evidence.get("expected_edges", [])] != expected_edges:
        raise M1GateError("probe-result expected_edges 与 contract 精确边集不一致")
    exit_code = evidence.get("exit_code")
    if type(exit_code) is not int or exit_code not in {0, 1, 2}:
        raise M1GateError(
            f"probe-result exit_code 必须是 int 且 ∈ {{0,1,2}}: {exit_code!r}"
        )
    edges_checked = evidence.get("edges_checked")
    if not isinstance(edges_checked, list) or not all(
        isinstance(item, dict) for item in edges_checked
    ):
        raise M1GateError("probe-result edges_checked 类型无效")
    if len(edges_checked) != len(expected_edges):
        raise M1GateError(
            f"probe-result edges_checked 数量与 expected_edges 不一致: "
            f"{len(edges_checked)} != {len(expected_edges)}"
        )
    edge_item_keys = {"edge", "verdict", "loaded_targets", "error"}
    for index, item in enumerate(edges_checked):
        if set(item) != edge_item_keys:
            raise M1GateError(
                f"probe-result edges_checked[{index}] 键集不精确: {sorted(set(item))}"
            )
        edge = item.get("edge")
        verdict = item.get("verdict")
        loaded = item.get("loaded_targets")
        error = item.get("error")
        if (
            not isinstance(edge, list)
            or len(edge) != 2
            or not all(isinstance(part, str) for part in edge)
            or edge != expected_edges[index]
        ):
            raise M1GateError(
                f"probe-result edges_checked[{index}].edge 与 expected_edges 不一致"
            )
        if verdict not in {"confirmed_removed", "edge_present", "inconclusive"}:
            raise M1GateError(f"probe-result edges_checked[{index}] verdict 非法")
        if not isinstance(loaded, list) or not all(
            isinstance(part, str) for part in loaded
        ):
            raise M1GateError(
                f"probe-result edges_checked[{index}] loaded_targets 类型非法"
            )
        if error is not None and not isinstance(error, str):
            raise M1GateError(f"probe-result edges_checked[{index}] error 类型非法")
    verdicts = {str(item.get("verdict")) for item in edges_checked}
    confirmed = (not verdicts) or verdicts == {"confirmed_removed"}
    if evidence.get("all_edges_confirmed") is not confirmed:
        raise M1GateError("probe-result all_edges_confirmed 与 verdicts 不自洽")
    if exit_code == 0 and not confirmed:
        raise M1GateError("probe-result exit_code=0 但存在非 confirmed 边")
    if exit_code == 1 and "edge_present" not in verdicts:
        raise M1GateError("probe-result exit_code=1 但无 edge_present 边")
    if exit_code == 2 and "inconclusive" not in verdicts:
        raise M1GateError("probe-result exit_code=2 但无 inconclusive 边")
    actual_hash = evidence_sha256(expected_edges, edges_checked, exit_code)
    if evidence.get("evidence_sha256") != actual_hash:
        raise M1GateError(
            f"probe-result evidence_sha256 不一致: "
            f"{evidence.get('evidence_sha256')} != {actual_hash}"
        )


def _load_probe_result(path: Path | None) -> dict[str, Any] | None:
    """读取并校验 probe-result.json；缺失返回 None，损坏失败闭合。"""
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise M1GateError(f"probe-result 不可读/不可解析: {exc}") from exc
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise M1GateError("probe-result schema_version 不受支持")
    return payload


def run_gate(
    root: Path,
    base_commit: str,
    pr_head_commit: str,
    candidate_commit: str,
    report_dir: Path,
    *,
    probe_result: Path | None = None,
    attestation_nonce: str = "",
    run_id: str | None = None,
    run_attempt: str | None = None,
) -> int:
    """执行受信双树 M1 gate 并返回退出码。

    production cut 在任何 policy 判断之前强制 attestation 认证：缺失或
    无法认证（签名/身份/nonce）一律 exit 2（fail-closed）。
    """

    root = root.resolve()
    report_dir = report_dir.resolve()
    meta = {
        "run_id": run_id,
        "run_attempt": run_attempt,
        "base_commit": base_commit,
        "pr_head_commit": pr_head_commit,
        "candidate_commit": candidate_commit,
    }
    try:
        _ensure_empty_report_dir(report_dir)
    except M1GateError as exc:
        print(f"M1 gate error: {exc}", file=sys.stderr)
        return 2
    try:
        provenance = _validate_provenance(
            root, base_commit, pr_head_commit, candidate_commit
        )
    except M1GateError as exc:
        return _gate_fail(report_dir, str(exc), run_id=run_id, run_attempt=run_attempt)
    candidate_tree = provenance["candidate_tree"]

    # 探针证据完整性：governance-only 无 removed edge，probe 非必需；
    # 生产 cut 的完整绑定（base/head/tree/contract/edges/version/exit/hash）
    # 在契约就绪后由 _validate_probe_evidence 复核。
    try:
        probe_evidence = _load_probe_result(probe_result)
    except M1GateError as exc:
        return _gate_fail(report_dir, str(exc), run_id=run_id, run_attempt=run_attempt)

    try:
        base_tree = provenance["base_tree"]
        changes = git_diff_tree_raw(root, base_tree, candidate_tree)
        _validate_change_modes(changes)
        changed_paths = _changed_paths(changes)
        protected_touched = sorted(
            path for path in changed_paths if _is_protected(path)
        )
        contract_paths_changed = sorted(
            path for path in changed_paths if _is_contract_path(path)
        )
        contract = None
        base_facts = None
        candidate_facts = None
        with tempfile.TemporaryDirectory(prefix=".m1-base-") as base_text:
            base_root = Path(base_text)
            git_materialize_tree(root, base_tree, base_root)
            base_config = load_config(base_root / "architecture.toml")
            base_facts = _analyze_materialized(base_root, base_config)
            contract = _load_contract(root, base_tree)
            with tempfile.TemporaryDirectory(prefix=".m1-candidate-") as candidate_text:
                candidate_root = Path(candidate_text)
                git_materialize_tree(root, candidate_tree, candidate_root)
                candidate_facts = _analyze_materialized(candidate_root, base_config)

        reasons: list[str] = []
        production_changes = sorted(
            path
            for path in changed_paths
            if path.startswith("core/") or path == "main.py"
        )
        if protected_touched:
            reasons.append(f"protected 路径被候选修改: {protected_touched}")

        # production cut：attestation 认证优先于任何 policy 判断
        if production_changes:
            if probe_evidence is None:
                raise M1GateError("生产 cut 缺少外部受信 attestation（--probe-result）")
            _validate_probe_evidence(
                probe_evidence,
                provenance=provenance,
                contract=contract,
                attestation_nonce=attestation_nonce,
            )

        # governance-only PR：无生产 cut（deny-by-default）
        if not production_changes:
            denied = sorted(
                path
                for path in changed_paths
                if not _is_governance_allowed(path) and not _is_contract_path(path)
            )
            if protected_touched:
                reasons.append(f"protected 路径被候选修改: {protected_touched}")
            if denied:
                reasons.append(
                    f"governance-only 禁止修改未授权对象（deny-by-default）: {denied}"
                )
            if protected_touched or denied:
                _emit(
                    report_dir,
                    provenance,
                    changes,
                    base_facts,
                    candidate_facts,
                    status="blocked",
                    exit_code=1,
                    reasons=reasons,
                    invariants={
                        "trusted_provenance": True,
                        "nul_safe_diff": True,
                        "scc_strict_decrease": "not_applicable",
                        "protected_paths_untouched": not protected_touched,
                        "governance_deny_by_default": True,
                        "dynamic_import_escapes": [],
                    },
                    meta=meta,
                )
                return 1
            governance_contract_valid = not contract_paths_changed
            if contract_paths_changed:
                contract_reasons = _validate_governance_contract(
                    root,
                    base_commit,
                    base_tree,
                    candidate_tree,
                    changes,
                    contract_paths_changed,
                    base_facts,
                    candidate_facts,
                )
                governance_contract_valid = not contract_reasons
                if contract_reasons:
                    reasons.extend(contract_reasons)
                    _emit(
                        report_dir,
                        provenance,
                        changes,
                        base_facts,
                        candidate_facts,
                        status="blocked",
                        exit_code=1,
                        reasons=reasons,
                        invariants={
                            "trusted_provenance": True,
                            "nul_safe_diff": True,
                            "scc_strict_decrease": "not_applicable",
                            "protected_paths_untouched": True,
                            "governance_contract_valid": False,
                            "dynamic_import_escapes": [],
                        },
                        meta=meta,
                    )
                    return 1
            _emit(
                report_dir,
                provenance,
                changes,
                base_facts,
                candidate_facts,
                status="governance_only",
                exit_code=0,
                reasons=reasons,
                invariants={
                    "trusted_provenance": True,
                    "nul_safe_diff": True,
                    "scc_strict_decrease": "not_applicable",
                    "protected_paths_untouched": True,
                    "contract_paths_changed": contract_paths_changed,
                    "governance_contract_valid": governance_contract_valid,
                    "governance_deny_by_default": True,
                    "dynamic_import_escapes": [],
                },
                meta=meta,
            )
            return 0

        # 生产 cut
        if protected_touched or contract_paths_changed:
            if contract_paths_changed:
                reasons.append(
                    f"生产 cut 不得修改 base-owned contract: {contract_paths_changed}"
                )
            _emit(
                report_dir,
                provenance,
                changes,
                base_facts,
                candidate_facts,
                status="blocked",
                exit_code=1,
                reasons=reasons,
                invariants={"trusted_provenance": True},
                meta=meta,
            )
            return 1

        if contract is None:
            reasons.append(
                f"生产 cut 需要 base-owned contract（{CONTRACT_DIR}/*.json 不存在于 base）"
            )
            _emit(
                report_dir,
                provenance,
                changes,
                base_facts,
                candidate_facts,
                status="blocked",
                exit_code=1,
                reasons=reasons,
                invariants={"trusted_provenance": True},
                meta=meta,
            )
            return 1

        invariants: dict[str, Any] = {"trusted_provenance": True}

        # production 必须校验 contract 锚点是真实 Git 对象且为 base 祖先
        try:
            _validate_contract_anchors(
                root,
                contract,
                base_commit=base_commit,
                path=str(contract.get("_contract_path")),
            )
        except M1GateError as exc:
            raise M1GateError(f"contract 锚点校验失败: {exc}") from exc
        if str(contract["base_graph_facts_sha256"]) != base_facts["facts_sha256"]:
            reasons.append(
                "contract.base_graph_facts_sha256 与 base 全量分析事实不一致"
            )
        invariants["contract_binds_current_base"] = not reasons

        actual_manifest = change_manifest_sha256(
            [_canonical_change(item) for item in changes]
        )
        expected_manifest = str(contract["change_manifest_sha256"])
        invariants["change_manifest_matches"] = actual_manifest == expected_manifest
        if actual_manifest != expected_manifest:
            reasons.append(
                f"change manifest 不一致: {actual_manifest} != {expected_manifest}"
            )
        actual_changes = [_canonical_change(item) for item in changes]
        expected_changes = [dict(item) for item in contract["expected_changes"]]
        invariants["exact_change_list"] = actual_changes == expected_changes
        if actual_changes != expected_changes:
            reasons.append(
                "diff 与 contract.expected_changes 不完全一致（额外/缺失/洗白）"
            )

        # 无关 legacy 夹带：contract 未覆盖的 production change 一律拒绝
        contracted_paths = {
            str(item.get("new_path") or item.get("old_path"))
            for item in contract["expected_changes"]
        }
        uncovered = sorted(
            path for path in production_changes if path not in contracted_paths
        )
        if uncovered:
            reasons.append(
                f"production change 未被 contract 覆盖（legacy 夹带）: {uncovered}"
            )
        invariants["no_uncontracted_legacy_changes"] = not uncovered

        base_edges = {(item["source"], item["target"]) for item in base_facts["edges"]}
        candidate_edges = {
            (item["source"], item["target"]) for item in candidate_facts["edges"]
        }
        edge_violations = _check_edges(base_edges, candidate_edges, contract)
        invariants["edges_removed_as_contracted"] = not edge_violations
        reasons.extend(edge_violations)

        base_scc = int(base_facts["largest_scc"])
        candidate_scc = int(candidate_facts["largest_scc"])
        invariants["scc_strict_decrease"] = candidate_scc < base_scc
        if candidate_scc >= base_scc:
            reasons.append(f"SCC 未严格下降: {base_scc} -> {candidate_scc}")

        escapes = _scan_dynamic_import_escapes(root, candidate_tree, changes)
        invariants["no_dynamic_import_escapes"] = not escapes
        if escapes:
            reasons.append(f"动态导入/间接加载逃逸: {escapes}")

        # removed-edge 运行探针证据：认证与绑定已在 production 分支顶部完成
        # （fail-closed exit 2）；此处只做 policy 确认。
        probe_confirmed: bool | str = "not_applicable"
        if not reasons:
            present_edges = [
                item
                for item in probe_evidence.get("edges_checked", [])
                if isinstance(item, dict) and item.get("verdict") == "edge_present"
            ]
            if present_edges:
                reasons.append(
                    f"运行探针确认 removed edge 仍在运行时存在: {present_edges}"
                )
                probe_confirmed = False
            elif not probe_evidence.get("all_edges_confirmed"):
                inconclusive = [
                    item
                    for item in probe_evidence.get("edges_checked", [])
                    if isinstance(item, dict) and item.get("verdict") == "inconclusive"
                ]
                raise M1GateError(
                    f"运行探针未能确认 removed edge（inconclusive）: {inconclusive}"
                )
            else:
                probe_confirmed = True
        invariants["removed_edges_runtime_probe_confirmed"] = probe_confirmed

        base_snapshot_hash = base_facts["snapshot_facts_sha256"]
        candidate_snapshot_hash = candidate_facts["snapshot_facts_sha256"]
        invariants["snapshot_public_contract_stable"] = (
            base_snapshot_hash == candidate_snapshot_hash
        )
        if base_snapshot_hash != candidate_snapshot_hash:
            reasons.append("public snapshot/contract 相对 base 漂移")

        invariants["production_scope_covered_by_contract"] = True
        _emit(
            report_dir,
            provenance,
            changes,
            base_facts,
            candidate_facts,
            status="blocked" if reasons else "pass",
            exit_code=1 if reasons else 0,
            reasons=reasons,
            invariants=invariants,
            meta=meta,
            contract={
                "path": contract.get("_contract_path"),
                "oid": contract.get("_contract_oid"),
                "base_commit": contract.get("base_commit"),
                "base_tree": contract.get("base_tree"),
            },
        )
        return 1 if reasons else 0
    except (ArchitectureSourceError, ArchitectureConfigError, M1GateError) as exc:
        return _gate_fail(report_dir, str(exc), run_id=run_id, run_attempt=run_attempt)
    except Exception as exc:  # 顶层兜底：任何未预期异常都 exit 2 + envelope
        return _gate_fail(
            report_dir,
            f"未预期错误: {type(exc).__name__}: {exc}",
            run_id=run_id,
            run_attempt=run_attempt,
        )
