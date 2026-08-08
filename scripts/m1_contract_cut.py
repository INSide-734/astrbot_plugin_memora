"""M1 canonical contract 生成器与连续 cut 轮换验证器（AST-40）。

本工具是 M1 gate（`scripts/m1_gate_*`，AST-28 M1）的配套协议层，只读复用
verifier 既定接口（contract schema/锚点/facts 口径），不修改任何 verifier
判定逻辑。两个子命令：

- ``generate``：给定 base commit（可另给 PR head 或 expected-changes 输入），
  按 verifier 同口径生成 canonical contract（精确绑定 base_commit/base_tree/
  base_graph_facts_sha256，manifest 内部自洽），并输出不可变 rotation
  manifest 条目（确定性产物，无时间戳）。kind 由 base 是否已有 contract
  自动判定：无 contract 为 genesis（首个 cut），恰一份为 rotation。
- ``validate-rotation``：按 contract rotation grammar 校验 governance-only
  单文件 ``M`` 轮换 PR（base tree -> candidate tree）：

  - 恰一份 contract 文件被修改（status=M），base/candidate 各恰有一份
    contract 且路径相同，除该文件外无任何其他变更（contract-only）；
  - 新 contract 精确绑定当前 base 的 commit/tree/facts，锚点真实自洽；
  - 新 contract 与旧 contract 内容不同（拒绝自轮换）；
  - 若提供 ``--manifest``，必须与确定性重算结果严格一致（拒绝篡改）。

退出码：0 = 通过/生成成功；1 = 轮换被 grammar 拒绝（输出 reasons）；
2 = 无法形成可信裁决（工具/输入/契约/结构错误，fail-closed）。

文档见 `website/docs/development/m1-contract-rotation.md`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # 直接从仓库根执行脚本时，scripts 目录在 sys.path 首位。
    from architecture_core import load_config
    from m1_gate_analysis import _analyze_materialized, git_materialize_tree
    from m1_gate_core import (
        CONTRACT_DIR,
        CONTRACT_SUFFIX,
        SCHEMA_VERSION,
        M1GateError,
        _canonical_change,
        _changed_paths,
        _decode_utf8,
        _list_tree_files,
        _read_tree_file,
        _validate_change_modes,
        _validate_contract_anchors,
        _validate_contract_payload,
        change_manifest_sha256,
        git_commit_tree,
        git_diff_tree_raw,
    )
except ImportError:  # pytest 以包模块导入时使用完整路径。
    from scripts.architecture_core import (  # type: ignore[no-redef]
        load_config,
    )
    from scripts.m1_gate_analysis import (  # type: ignore[no-redef]
        _analyze_materialized,
        git_materialize_tree,
    )
    from scripts.m1_gate_core import (  # type: ignore[no-redef]
        CONTRACT_DIR,
        CONTRACT_SUFFIX,
        SCHEMA_VERSION,
        M1GateError,
        _canonical_change,
        _changed_paths,
        _decode_utf8,
        _list_tree_files,
        _read_tree_file,
        _validate_change_modes,
        _validate_contract_anchors,
        _validate_contract_payload,
        change_manifest_sha256,
        git_commit_tree,
        git_diff_tree_raw,
    )


def _full_sha(value: str, *, label: str) -> str:
    """把输入解析为完整 40 位提交 SHA；非法即 M1GateError。"""
    value = str(value).strip()
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise M1GateError(f"{label} 不是完整 40 位十六进制 SHA: {value}")
    return value


def _blob_oid(root: Path, content: bytes) -> str:
    """计算字节内容的 Git blob OID（与提交后 tree 中的 blob OID 一致）。"""
    try:
        completed = subprocess.run(
            ["git", "hash-object", "--stdin"],
            cwd=root,
            check=False,
            capture_output=True,
            input=content,
            timeout=60.0,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise M1GateError(f"git hash-object 失败: {exc}") from exc
    if completed.returncode != 0:
        raise M1GateError("git hash-object 非零退出")
    return _decode_utf8(completed.stdout, label="hash-object").strip()


def _contract_entries(root: Path, tree: str) -> list[dict[str, str]]:
    """列出 tree 中 contract 目录下的 .json 文件（与 verifier 同口径）。"""
    return [
        entry
        for entry in _list_tree_files(root, tree, CONTRACT_DIR)
        if entry["path"].endswith(CONTRACT_SUFFIX)
    ]


def _read_contract_payload(root: Path, tree: str, path: str) -> dict[str, Any]:
    """从 tree 读取 contract 并做 schema 校验；损坏失败闭合。"""
    try:
        payload = json.loads(
            _decode_utf8(_read_tree_file(root, tree, path), label="contract")
        )
    except (json.JSONDecodeError, M1GateError) as exc:
        raise M1GateError(f"contract 无法解析 {path}: {exc}") from exc
    _validate_contract_payload(payload, path=path)
    return payload


def _base_facts(root: Path, base_tree: str) -> dict[str, Any]:
    """按 verifier 同口径分析 base tree（物化后移除 contract 目录）的事实。"""
    with tempfile.TemporaryDirectory(prefix=".m1-contract-facts-") as text:
        base_root = Path(text)
        git_materialize_tree(root, base_tree, base_root)
        config = load_config(base_root / "architecture.toml")
        return _analyze_materialized(base_root, config)


def _canonical_changes(
    changes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """把 change 记录压缩为 manifest 逐字节比较的规范 7 键形态。"""
    return [_canonical_change(item) for item in changes]


def compute_contract(
    *,
    cut_id: str,
    base_commit: str,
    base_tree: str,
    base_graph_facts_sha256: str,
    expected_changes: Sequence[Mapping[str, Any]],
    expected_removed_edges: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """构造 canonical contract 载荷（字段顺序固定，输出字节确定）。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "cut_id": cut_id,
        "base_commit": base_commit,
        "base_tree": base_tree,
        "base_graph_facts_sha256": base_graph_facts_sha256,
        "change_manifest_sha256": change_manifest_sha256(expected_changes),
        "expected_changes": list(expected_changes),
        "expected_removed_edges": [list(edge) for edge in expected_removed_edges],
    }


def compute_manifest_entry(
    *,
    kind: str,
    contract_path: str,
    old_contract_oid: str | None,
    new_contract_oid: str,
    new_contract_sha256: str,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """构造确定性 rotation manifest 条目（字段顺序固定，无时间戳）。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "contract_path": contract_path,
        "old_contract_oid": old_contract_oid,
        "new_contract_oid": new_contract_oid,
        "new_contract_sha256": new_contract_sha256,
        "base_commit": contract["base_commit"],
        "base_tree": contract["base_tree"],
        "base_graph_facts_sha256": contract["base_graph_facts_sha256"],
        "change_manifest_sha256": contract["change_manifest_sha256"],
        "expected_removed_edges": [
            list(edge) for edge in contract["expected_removed_edges"]
        ],
    }


def _write_json(payload: Mapping[str, Any], output: Path | None) -> None:
    """写 JSON（sort_keys 保证字节确定）；未指定输出时打印到 stdout。"""
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(text)
    else:
        output.write_text(text, encoding="utf-8")


def cmd_generate(args: argparse.Namespace) -> int:
    """``generate`` 子命令：生成 canonical contract 与 manifest 条目。"""
    root = (args.repository or Path.cwd()).resolve()
    base_commit = _full_sha(args.base_commit, label="base_commit")
    if not args.pr_head and not args.expected_changes_file:
        print(
            "generate 必须提供 --pr-head 或 --expected-changes-file 之一",
            file=sys.stderr,
        )
        return 2
    try:
        base_tree = git_commit_tree(root, base_commit)
        entries = _contract_entries(root, base_tree)
        if len(entries) > 1:
            raise M1GateError(f"base 已有 {len(entries)} 份 contract，无法裁决")
        kind = "rotation" if entries else "genesis"
        old_oid = entries[0]["oid"] if entries else None
        if args.pr_head:
            head_commit = _full_sha(args.pr_head, label="pr_head")
            head_tree = git_commit_tree(root, head_commit)
            changes = _canonical_changes(git_diff_tree_raw(root, base_tree, head_tree))
        else:
            raw_changes = json.loads(
                Path(args.expected_changes_file).read_text(encoding="utf-8")
            )
            if not isinstance(raw_changes, list):
                raise M1GateError("expected_changes 必须是 JSON 数组")
            changes = _canonical_changes(raw_changes)
        if not changes:
            raise M1GateError("expected_changes 必须是非空数组")
        removed_edges: list[list[str]] = []
        if args.expected_removed_edges_file:
            removed_edges = json.loads(
                Path(args.expected_removed_edges_file).read_text(encoding="utf-8")
            )
            if not isinstance(removed_edges, list) or not all(
                isinstance(edge, list)
                and len(edge) == 2
                and all(isinstance(part, str) for part in edge)
                for edge in removed_edges
            ):
                raise M1GateError("expected_removed_edges 必须是 [['a','b'], ...] 数组")
        facts = _base_facts(root, base_tree)
        contract = compute_contract(
            cut_id=args.cut_id,
            base_commit=base_commit,
            base_tree=base_tree,
            base_graph_facts_sha256=facts["facts_sha256"],
            expected_changes=changes,
            expected_removed_edges=removed_edges,
        )
        _validate_contract_payload(contract, path=CONTRACT_DIR)
        _validate_contract_anchors(
            root, contract, base_commit=base_commit, path=CONTRACT_DIR
        )
        # manifest 记录 canonical 提交路径（与 validate-rotation 校验口径一致）。
        contract_path = f"{CONTRACT_DIR}/{args.cut_id}.json"
        contract_bytes = (
            json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        manifest = compute_manifest_entry(
            kind=kind,
            contract_path=contract_path,
            old_contract_oid=old_oid,
            new_contract_oid=_blob_oid(root, contract_bytes),
            new_contract_sha256=hashlib.sha256(contract_bytes).hexdigest(),
            contract=contract,
        )
        if args.output is not None:
            _write_json(contract, args.output)
        _write_json(manifest, args.manifest_output)
        return 0
    except M1GateError as exc:
        print(f"M1 contract cut error: {exc}", file=sys.stderr)
        return 2


def _reject(reasons: Sequence[str]) -> int:
    """grammar 拒绝：打印 reasons 并返回 exit 1。"""
    for reason in reasons:
        print(f"contract rotation 拒绝: {reason}")
    return 1


def cmd_validate_rotation(args: argparse.Namespace) -> int:
    """``validate-rotation`` 子命令：校验轮换 grammar 并输出/核验 manifest。"""
    root = (args.repository or Path.cwd()).resolve()
    base_commit = _full_sha(args.base_commit, label="base_commit")
    candidate_commit = _full_sha(args.candidate_commit, label="candidate_commit")
    try:
        base_tree = git_commit_tree(root, base_commit)
        candidate_tree = git_commit_tree(root, candidate_commit)
    except M1GateError as exc:
        print(f"M1 contract cut error: {exc}", file=sys.stderr)
        return 2
    try:
        base_entries = _contract_entries(root, base_tree)
        candidate_entries = _contract_entries(root, candidate_tree)
        if len(base_entries) > 1:
            raise M1GateError(
                f"base contract 不唯一（{len(base_entries)} 份），无法裁决"
            )
        if len(candidate_entries) > 1:
            raise M1GateError(
                f"candidate contract 不唯一（{len(candidate_entries)} 份），无法裁决"
            )
        reasons: list[str] = []
        if base_tree == candidate_tree:
            return _reject(["自轮换：候选 tree 与 base tree 相同（无任何变更）"])
        if not base_entries:
            reasons.append(
                "base 无 contract：genesis 应走 governance contract-add 路径，不是轮换"
            )
        if not candidate_entries:
            reasons.append("candidate 无 contract（轮换不得删除 contract）")
        if base_entries and candidate_entries:
            old_entry, new_entry = base_entries[0], candidate_entries[0]
            contract_path = old_entry["path"]
            changed_paths = sorted(
                _changed_paths(git_diff_tree_raw(root, base_tree, candidate_tree))
            )
            _validate_change_modes(git_diff_tree_raw(root, base_tree, candidate_tree))
            if set(changed_paths) != {contract_path}:
                reasons.append(
                    "轮换必须 contract-only 单文件变更（额外变更）: "
                    f"{sorted(set(changed_paths) - {contract_path})}"
                )
            if new_entry["path"] != contract_path:
                reasons.append(
                    f"轮换必须同路径单文件 M: {contract_path} -> {new_entry['path']}"
                )
            statuses = {
                item["status"]
                for item in git_diff_tree_raw(root, base_tree, candidate_tree)
                if (item.get("old_path") or item.get("new_path")) == contract_path
            }
            if statuses != {"M"}:
                reasons.append(f"contract 变更必须为单文件 M，实际 {sorted(statuses)}")
        if reasons:
            return _reject(reasons)
        old_entry, new_entry = base_entries[0], candidate_entries[0]
        # 校验 base 已有 contract 可解析（损坏即 fail-closed exit 2）。
        _read_contract_payload(root, base_tree, old_entry["path"])
        new_payload = _read_contract_payload(root, candidate_tree, new_entry["path"])
        # 语义绑定优先于锚点结构检查（与 governance 判定同口径：绑定不符 exit 1）
        if str(new_payload["base_commit"]) != base_commit:
            reasons.append(
                f"新 contract.base_commit 与当前 base 不符: {new_payload['base_commit']}"
            )
        if str(new_payload["base_tree"]) != base_tree:
            reasons.append(
                f"新 contract.base_tree 与当前 base tree 不符: {new_payload['base_tree']}"
            )
        if reasons:
            return _reject(reasons)
        _validate_contract_anchors(
            root, new_payload, base_commit=base_commit, path=new_entry["path"]
        )
        if (
            str(new_payload["base_graph_facts_sha256"])
            != _base_facts(root, base_tree)["facts_sha256"]
        ):
            reasons.append(
                "新 contract.base_graph_facts_sha256 与 base 全量分析事实不一致"
            )
        old_bytes = _read_tree_file(root, base_tree, old_entry["path"])
        new_bytes = _read_tree_file(root, candidate_tree, new_entry["path"])
        if old_entry["oid"] == new_entry["oid"] or old_bytes == new_bytes:
            reasons.append("自轮换：新 contract 与旧 contract 内容相同")
        if reasons:
            return _reject(reasons)
        manifest = compute_manifest_entry(
            kind="rotation",
            contract_path=new_entry["path"],
            old_contract_oid=old_entry["oid"],
            new_contract_oid=new_entry["oid"],
            new_contract_sha256=hashlib.sha256(new_bytes).hexdigest(),
            contract=new_payload,
        )
        if args.manifest is not None:
            try:
                provided = json.loads(args.manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise M1GateError(f"manifest 不可读/不可解析: {exc}") from exc
            if provided != manifest:
                return _reject(["manifest 与确定性重算不一致（篡改或过期）"])
        _write_json(manifest, args.output)
        return 0
    except M1GateError as exc:
        print(f"M1 contract cut error: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 解析器（generate / validate-rotation 两个子命令）。"""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen = subparsers.add_parser("generate", help="生成 canonical contract")
    gen.add_argument("--repository", type=Path, default=None)
    gen.add_argument("--base-commit", required=True, help="完整 40 位 base 提交 SHA")
    gen.add_argument(
        "--pr-head", default=None, help="可选 PR head；据此计算 expected_changes"
    )
    gen.add_argument(
        "--expected-changes-file",
        type=Path,
        default=None,
        help="可选 expected_changes JSON 数组文件（与 --pr-head 二选一）",
    )
    gen.add_argument(
        "--expected-removed-edges-file",
        type=Path,
        default=None,
        help="可选 expected_removed_edges JSON 数组文件",
    )
    gen.add_argument("--cut-id", default="m1-cut", help="cut 标识（写入 contract）")
    gen.add_argument("--output", type=Path, default=None, help="contract 输出路径")
    gen.add_argument(
        "--manifest-output",
        type=Path,
        default=None,
        help="manifest 条目输出路径（缺省打印到 stdout）",
    )
    gen.set_defaults(handler=cmd_generate)

    rot = subparsers.add_parser(
        "validate-rotation", help="按 grammar 校验 contract 轮换 PR"
    )
    rot.add_argument("--repository", type=Path, default=None)
    rot.add_argument("--base-commit", required=True, help="PR base 完整提交 SHA")
    rot.add_argument(
        "--candidate-commit", required=True, help="merge candidate 提交 SHA"
    )
    rot.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="待核验 manifest 文件（与确定性重算不一致即拒绝）",
    )
    rot.add_argument("--output", type=Path, default=None, help="重算 manifest 输出路径")
    rot.set_defaults(handler=cmd_validate_rotation)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主入口（任何未预期异常稳定 exit 2）。"""
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except M1GateError as exc:
        print(f"M1 contract cut error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            f"M1 contract cut error: 未预期错误: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
