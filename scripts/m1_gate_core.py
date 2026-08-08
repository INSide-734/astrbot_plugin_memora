"""M1 gate 核心库：Git/字节安全、scope/manifest/contract/动态依赖扫描/attestation 契约。"""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # 直接从仓库根执行脚本时，scripts 目录在 sys.path 首位。
    from architecture_core import _normalise_rel
except ImportError:  # pytest 以包模块导入时使用完整路径。
    from scripts.architecture_core import (  # type: ignore[no-redef]
        _normalise_rel,
    )


SCHEMA_VERSION = 1
CONTRACT_DIR = "scripts/m1_cuts"
CONTRACT_SUFFIX = ".json"
# 完整信任根：整个 .github/workflows/、gate/checker 全部模块、schema、
# 包/锁定与运行资产。候选修改其中任一项都被 protected 阻断。
PROTECTED_PATHS = (
    "architecture.toml",
    "_conf_schema.json",
    "metadata.yaml",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "scripts/baselines/architecture.json",
    "scripts/baselines/",
    "scripts/architecture_analysis.py",
    "scripts/architecture_core.py",
    "scripts/architecture_snapshots.py",
    "scripts/check_architecture.py",
    "scripts/check_feature_gate.py",
    "scripts/check_m1_gate.py",
    "scripts/m1_gate_analysis.py",
    "scripts/m1_gate_core.py",
    "scripts/m1_gate_probe.py",
    "scripts/m1_gate_report.py",
    ".github/workflows/",
)
# governance-only 精确 allowlist（deny-by-default）：contract 与文档站
# 明确范围；其余任何对象变化都必须进入 production contract 或 exit 1。
GOVERNANCE_ALLOWED_PREFIXES = (
    CONTRACT_DIR + "/",
    "docs/",
    "website/docs/",
)
REPORT_NAMES = (
    "provenance.json",
    "diff.json",
    "base-analysis.json",
    "candidate-analysis.json",
    "decision.json",
)
COMMIT_MARKER = "COMMITTED"
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RAW_STATUS_RE = re.compile(
    r"^:(?P<old_mode>[0-7]{6}) (?P<new_mode>[0-7]{6}) "
    r"(?P<old_sha>[0-9a-f]{40}|0{40}) (?P<new_sha>[0-9a-f]{40}|0{40}) "
    r"(?P<status>[AMDTRC][0-9]{0,3})$"
)
_REGULAR_BLOB_MODES = {"100644", "100755"}
# 动态加载逃逸不做名称黑名单：只在 AST 上下文可准确限定时判定
# （builtins/__dict__/别名/组合表达式/未知动态取值 fail-closed）。
_BUILTIN_NAMESPACE_NAMES = {"__builtins__", "builtins"}
_NAMESPACE_LOOKUP_CALLS = {"globals", "locals", "vars"}
_MODULE_ALIAS_KINDS = {
    "builtins": "builtins_like",
    "sys": "sys_module",
    "importlib": "importlib_module",
    "runpy": "runpy_module",
    "imp": "importlib_module",
    "pkgutil": "importlib_module",
    "zipimport": "importlib_module",
}
_LOADER_NAMES = {
    "__import__",
    "import_module",
    "reload",
    "load_module",
    "exec_module",
    "create_module",
    "find_spec",
    "find_loader",
    "run_module",
    "run_path",
    "eval",
    "exec",
    "execfile",
    "compile",
}
_DIRECT_LOADER_CALLS = {"__import__", "eval", "exec", "execfile", "compile"}
_GETATTR_LOADER_CALLS = {"getattr", "hasattr"}
_GIT_SUBPROCESS_TIMEOUT = 60.0


class M1GateError(RuntimeError):
    """表示无法形成可信 M1 裁决的工具级错误。"""


def _run_git_bytes(
    root: Path, *args: str, timeout: float = _GIT_SUBPROCESS_TIMEOUT
) -> bytes:
    """按字节执行 Git 子命令；timeout/OSError/非零退出统一 M1GateError。"""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise M1GateError(f"Git 命令超时: {args[0]}") from exc
    except OSError as exc:
        raise M1GateError(f"无法执行 Git 命令: {args[0]}") from exc
    if completed.returncode != 0:
        raise M1GateError(f"Git 命令失败: {args[0]} (exit {completed.returncode})")
    return completed.stdout


def _decode_utf8(payload: bytes, *, label: str) -> str:
    """严格 UTF-8 解码；不可无损表示的路径/内容显式 M1GateError。"""
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise M1GateError(f"{label} 无法以 UTF-8 无损表示") from exc


def _full_sha(value: str, *, label: str) -> str:
    """校验并规范化提交 SHA，拒绝短写/歧义。"""
    if _GIT_OBJECT_RE.fullmatch(value) is None:
        raise M1GateError(f"{label} 必须是完整 40 位十六进制提交 SHA")
    return value


def git_commit_tree(root: Path, commit: str) -> str:
    """解析 commit 的 tree，拒绝无法解析的对象。"""
    value = _decode_utf8(
        _run_git_bytes(root, "rev-parse", f"{commit}^{{tree}}"), label="commit tree"
    ).strip()
    if _GIT_OBJECT_RE.fullmatch(value) is None:
        raise M1GateError(f"commit tree 不可解析: {commit}")
    return value


def git_parents(root: Path, commit: str) -> list[str]:
    """返回 commit 的父提交（保持顺序），缺失对象时失败闭合。"""
    output = _decode_utf8(
        _run_git_bytes(root, "rev-list", "--parents", "-n", "1", commit),
        label="parents",
    )
    fields = output.strip().split()
    if not fields:
        raise M1GateError(f"Git commit 无父信息: {commit}")
    return fields[1:]


def git_merge_base(root: Path, left: str, right: str) -> str:
    """返回两个提交的 merge-base；无共同祖先时失败闭合。"""
    value = _decode_utf8(
        _run_git_bytes(root, "merge-base", left, right), label="merge-base"
    ).strip()
    if _GIT_OBJECT_RE.fullmatch(value) is None:
        raise M1GateError(f"Git merge-base 不可解析: {left} {right}")
    return value


def git_is_shallow(root: Path) -> bool:
    """检测仓库是否为浅克隆。"""
    value = _decode_utf8(
        _run_git_bytes(root, "rev-parse", "--is-shallow-repository"),
        label="shallow",
    ).strip()
    return value == "true"


def git_object_exists(root: Path, spec: str) -> bool:
    """检测 Git 对象是否存在（不抛错，供锚点校验）。"""
    try:
        _run_git_bytes(root, "cat-file", "-e", spec)
        return True
    except M1GateError:
        return False


def git_diff_tree_raw(
    root: Path,
    old_tree: str,
    new_tree: str,
    *,
    find_renames: bool = True,
    find_copies: bool = True,
) -> list[dict[str, Any]]:
    """按字节解析两棵 tree 的原始 diff（NUL-safe 含 mode；R/C 双侧
    路径参与；任何记录无法解析即失败闭合）。"""

    command = ["diff-tree", "--raw", "-r", "-z"]
    if find_renames:
        command.append("-M")
    if find_copies:
        command.append("-C")
    command.extend([old_tree, new_tree])
    payload = _run_git_bytes(root, *command)
    if b"\x00" not in payload:
        raise M1GateError("Git diff-tree 未返回 NUL 分隔输出")
    tokens = payload.split(b"\x00")
    records: list[dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        header = tokens[index]
        index += 1
        if not header:
            continue
        header_text = _decode_utf8(header, label="diff-tree 记录")
        match = _RAW_STATUS_RE.fullmatch(header_text)
        if match is None:
            raise M1GateError(f"Git diff-tree 记录无法解析: {header_text!r}")
        status = match.group("status")
        old_mode, new_mode = match.group("old_mode"), match.group("new_mode")
        old_sha, new_sha = match.group("old_sha"), match.group("new_sha")
        if status[0] in {"R", "C"}:
            if index + 1 >= len(tokens):
                raise M1GateError(f"Git diff-tree rename 记录不完整: {header_text!r}")
            old_path = _decode_utf8(tokens[index], label="old path")
            new_path = _decode_utf8(tokens[index + 1], label="new path")
            index += 2
        else:
            if index >= len(tokens):
                raise M1GateError(f"Git diff-tree 路径缺失: {header_text!r}")
            new_path = _decode_utf8(tokens[index], label="path")
            old_path = new_path
            index += 1
        records.append(
            {
                "status": status[0],
                "score": int(status[1:]) if status[1:] else None,
                "old_mode": old_mode,
                "new_mode": new_mode,
                "old_blob": None if old_sha == "0" * 40 else old_sha,
                "new_blob": None if new_sha == "0" * 40 else new_sha,
                "old_path": _normalise_rel(old_path) if old_path else None,
                "new_path": _normalise_rel(new_path) if new_path else None,
            }
        )
    return records


def change_manifest_sha256(changes: Sequence[Mapping[str, Any]]) -> str:
    """对 change 记录生成规范 JSON 的 SHA-256 摘要（含 mode）。"""
    canonical = json.dumps(
        list(changes), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _canonical_change(item: Mapping[str, Any]) -> dict[str, Any]:
    """压缩为 manifest 逐字节比较的规范字段（绑定 old/new mode）。"""
    return {
        "status": item["status"],
        "old_path": item.get("old_path"),
        "new_path": item.get("new_path"),
        "old_mode": item.get("old_mode"),
        "new_mode": item.get("new_mode"),
        "old_blob": item.get("old_blob"),
        "new_blob": item.get("new_blob"),
    }


def _validate_provenance(
    root: Path,
    base_commit: str,
    pr_head_commit: str,
    candidate_commit: str,
) -> dict[str, Any]:
    """验证 merge candidate 的父关系与 merge-base，形成可信裁决前提。"""
    if git_is_shallow(root):
        raise M1GateError("浅克隆无法形成可信裁决，必须使用完整克隆")
    for value in (base_commit, pr_head_commit, candidate_commit):
        _run_git_bytes(root, "cat-file", "-e", f"{value}^{{commit}}")
    base_tree = git_commit_tree(root, base_commit)
    pr_tree = git_commit_tree(root, pr_head_commit)
    candidate_tree = git_commit_tree(root, candidate_commit)
    parents = git_parents(root, candidate_commit)
    if parents != [base_commit, pr_head_commit]:
        raise M1GateError(
            f"candidate 父关系不符: {parents} != [{base_commit}, {pr_head_commit}]"
        )
    merge_base = git_merge_base(root, base_commit, candidate_commit)
    if merge_base != base_commit:
        raise M1GateError(f"merge-base 不符（不得降级为三点 diff）: {base_commit}")
    return {
        "schema_version": SCHEMA_VERSION,
        "verified": True,
        "repository": str(root),
        "base_commit": base_commit,
        "base_tree": base_tree,
        "pr_head_commit": pr_head_commit,
        "pr_head_tree": pr_tree,
        "candidate_commit": candidate_commit,
        "candidate_tree": candidate_tree,
        "candidate_parents": parents,
        "merge_base": merge_base,
        "shallow": False,
    }


def _is_protected(path: str) -> bool:
    """判断是否属于完整信任根（整个 workflows/、gate/schema/锁定资产）。"""
    normalized = _normalise_rel(path)
    return any(
        normalized == prefix.rstrip("/") or normalized.startswith(prefix)
        for prefix in PROTECTED_PATHS
    )


def _is_contract_path(path: str) -> bool:
    """判断是否位于 base-owned contract 目录。"""
    return _normalise_rel(path).startswith(CONTRACT_DIR + "/")


def _is_governance_allowed(path: str) -> bool:
    """deny-by-default：仅 contract 与明确文档范围放行。"""
    normalized = _normalise_rel(path)
    return any(normalized.startswith(prefix) for prefix in GOVERNANCE_ALLOWED_PREFIXES)


def _changed_paths(changes: Sequence[Mapping[str, Any]]) -> set[str]:
    """收集 change 记录的全部路径（R/C 双侧都计入）。"""
    paths: set[str] = set()
    for item in changes:
        if item.get("old_path"):
            paths.add(str(item["old_path"]))
        if item.get("new_path"):
            paths.add(str(item["new_path"]))
    return paths


def _validate_change_modes(changes: Sequence[Mapping[str, Any]]) -> None:
    """拒绝 symlink/gitlink/typechange 与一切双侧 regular-blob mode 漂移
    （含 R/C 伴随 mode 变化，一律 exit 2）。"""
    for item in changes:
        old_mode = item.get("old_mode")
        new_mode = item.get("new_mode")
        for field, label in (("old_mode", "old"), ("new_mode", "new")):
            if item.get(field) in {"120000", "160000"}:
                raise M1GateError(
                    f"{label} side 是 symlink/gitlink（mode {item.get(field)}）: "
                    f"{item.get('old_path') or item.get('new_path')}"
                )
        if item["status"] == "T":
            raise M1GateError(
                f"typechange 无法形成可信裁决: {item.get('old_path') or item.get('new_path')}"
            )
        if (
            old_mode in _REGULAR_BLOB_MODES
            and new_mode in _REGULAR_BLOB_MODES
            and old_mode != new_mode
        ):
            raise M1GateError(
                f"regular-blob mode 漂移未授权: "
                f"{item.get('old_path') or item.get('new_path')} {old_mode} -> {new_mode}"
            )


def _list_tree_files(root: Path, tree: str, prefix: str) -> list[dict[str, str]]:
    """按字节列出 tree 前缀文件的 mode/OID（NUL-safe）。"""
    payload = _run_git_bytes(root, "ls-tree", "-r", "-z", tree, "--", prefix)
    records: list[dict[str, str]] = []
    for token in payload.split(b"\x00"):
        if not token:
            continue
        token_text = _decode_utf8(token, label="ls-tree 记录")
        metadata, _, path = token_text.partition("\t")
        parts = metadata.split()
        if not path or len(parts) != 3 or parts[1] != "blob":
            raise M1GateError(f"ls-tree 记录无法解析: {token_text!r}")
        records.append(
            {"mode": parts[0], "type": parts[1], "oid": parts[2], "path": path}
        )
    return records


def _read_tree_file(root: Path, tree: str, path: str) -> bytes:
    """按字节读取 tree 文件 blob；timeout/OSError 统一 M1GateError。"""
    try:
        completed = subprocess.run(
            ["git", "cat-file", "blob", f"{tree}:{path}"],
            cwd=root,
            check=False,
            capture_output=True,
            timeout=_GIT_SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise M1GateError(f"cat-file 超时: {tree}:{path}") from exc
    except OSError as exc:
        raise M1GateError(f"cat-file 无法执行: {tree}:{path}") from exc
    if completed.returncode != 0:
        raise M1GateError(f"无法读取 tree 文件: {tree}:{path}")
    return completed.stdout


def _validate_contract_payload(
    payload: Mapping[str, Any],
    *,
    path: str,
) -> None:
    """校验 contract schema、锚点格式与内部 canonical hash 自洽性。"""
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise M1GateError(f"contract schema_version 不受支持: {path}")
    for field in (
        "base_commit",
        "base_tree",
        "base_graph_facts_sha256",
        "change_manifest_sha256",
    ):
        if not isinstance(payload.get(field), str):
            raise M1GateError(f"contract.{field} 缺失或类型无效: {path}")
    if _GIT_OBJECT_RE.fullmatch(str(payload["base_commit"])) is None:
        raise M1GateError(f"contract.base_commit 不是完整提交 SHA: {path}")
    if _GIT_OBJECT_RE.fullmatch(str(payload["base_tree"])) is None:
        raise M1GateError(f"contract.base_tree 不是完整 tree SHA: {path}")
    for field in ("base_graph_facts_sha256", "change_manifest_sha256"):
        if _SHA256_RE.fullmatch(str(payload[field])) is None:
            raise M1GateError(f"contract.{field} 不是 SHA-256: {path}")
    expected_changes = payload.get("expected_changes")
    if not isinstance(expected_changes, list) or not expected_changes:
        raise M1GateError(f"contract.expected_changes 必须是非空数组: {path}")
    for item in expected_changes:
        if not isinstance(item, dict) or "status" not in item or "new_path" not in item:
            raise M1GateError(f"contract.expected_changes 条目格式无效: {path}")
    if not isinstance(payload.get("expected_removed_edges"), list):
        raise M1GateError("contract.expected_removed_edges 必须是数组")
    canonical = change_manifest_sha256(expected_changes)
    if canonical != str(payload["change_manifest_sha256"]):
        raise M1GateError(
            f"contract.change_manifest_sha256 与 expected_changes 规范哈希不一致: {path}"
        )


def _validate_contract_anchors(
    root: Path,
    payload: Mapping[str, Any],
    *,
    base_commit: str,
    path: str,
) -> None:
    """校验 contract 锚点真实、自洽（base_commit^{tree}==base_tree）且为
    base 祖先；不存在/自相矛盾/非祖先一律 M1GateError（exit 2）。"""

    declared_commit = str(payload["base_commit"])
    declared_tree = str(payload["base_tree"])
    if not git_object_exists(root, f"{declared_commit}^{{commit}}"):
        raise M1GateError(f"contract.base_commit 不是真实对象: {declared_commit}")
    if not git_object_exists(root, f"{declared_tree}^{{tree}}"):
        raise M1GateError(f"contract.base_tree 不是真实对象: {declared_tree}")
    resolved_tree = git_commit_tree(root, declared_commit)
    if resolved_tree != declared_tree:
        raise M1GateError(
            f"contract.base_commit^{{tree}} != contract.base_tree: "
            f"{resolved_tree} != {declared_tree}"
        )
    if declared_commit != base_commit:
        if git_merge_base(root, declared_commit, base_commit) != declared_commit:
            raise M1GateError(
                f"contract.base_commit 不是当前 base 的祖先: {declared_commit}"
            )


def _load_contract(root: Path, base_tree: str) -> dict[str, Any] | None:
    """从 base tree 读取 cut contract；不存在返回 None，损坏则失败闭合。
    （零个匹配由调用方按 exit 1 处理；多个/malformed/不可读 exit 2。）"""

    entries = _list_tree_files(root, base_tree, CONTRACT_DIR)
    contract_entries = [
        entry for entry in entries if entry["path"].endswith(CONTRACT_SUFFIX)
    ]
    if not contract_entries:
        return None
    if len(contract_entries) != 1:
        raise M1GateError(f"contract 必须恰好一份，实际 {len(contract_entries)}")
    entry = contract_entries[0]
    if entry["mode"] not in _REGULAR_BLOB_MODES:
        raise M1GateError(
            f"contract 必须是 regular blob（mode {entry['mode']}）: {entry['path']}"
        )
    try:
        payload = json.loads(
            _decode_utf8(
                _read_tree_file(root, base_tree, entry["path"]), label="contract"
            )
        )
    except (json.JSONDecodeError, M1GateError) as exc:
        raise M1GateError(f"contract 无法解析 {entry['path']}: {exc}") from exc
    _validate_contract_payload(payload, path=entry["path"])
    payload["_contract_path"] = entry["path"]
    payload["_contract_oid"] = entry["oid"]
    return payload


def _fold_constant_str(node: Any) -> str | None:
    """折叠字符串常量与 ``"a" + "b"`` 组合表达式；不可折叠返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_constant_str(node.left)
        right = _fold_constant_str(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _expr_alias_kind(node: Any, kinds: Mapping[str, str]) -> str | None:
    """保守别名种类（builtins_like/builtins_dict/sys_modules/loader_fn）。"""
    if isinstance(node, ast.Name):
        if node.id in _BUILTIN_NAMESPACE_NAMES:
            return "builtins_like"
        return kinds.get(node.id)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in _NAMESPACE_LOOKUP_CALLS:
            return "builtins_like"
        if node.func.id in _GETATTR_LOADER_CALLS and len(node.args) >= 2:
            key = _fold_constant_str(node.args[1])
            if key is not None and key in _LOADER_NAMES:
                base = _expr_alias_kind(node.args[0], kinds)
                if base in {"builtins_like", "builtins_dict", "importlib_module"}:
                    return "loader_fn"
        return None
    if isinstance(node, ast.Attribute):
        base = _expr_alias_kind(node.value, kinds)
        if base is None and isinstance(node.value, ast.Name):
            base = _MODULE_ALIAS_KINDS.get(node.value.id)
        if node.attr == "__dict__" and base == "builtins_like":
            return "builtins_dict"
        if node.attr == "modules" and base == "sys_module":
            return "sys_modules"
    return None


def _collect_alias_kinds(tree: ast.AST) -> dict[str, str]:
    """保守 alias/dataflow：import/赋值别名的种类映射（不动点迭代）。"""
    kinds: dict[str, str] = {}

    def record(name: str, kind: str) -> None:
        if name not in kinds:
            kinds[name] = kind
        elif kinds[name] not in {"unknown", kind}:
            kinds[name] = "unknown"

    def scan(node: ast.AST) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    record(
                        alias.asname,
                        _MODULE_ALIAS_KINDS.get(alias.name.split(".")[0], "unknown"),
                    )
        elif isinstance(node, ast.ImportFrom) and node.module in {
            "builtins",
            "importlib",
            "runpy",
            "imp",
        }:
            for alias in node.names:
                if alias.asname and alias.name in _LOADER_NAMES:
                    record(alias.asname, "loader_fn")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            target = targets[0]
            if isinstance(target, ast.Name) and node.value is not None:
                record(target.id, _expr_alias_kind(node.value, kinds) or "unknown")

    for _ in range(4):
        for node in ast.walk(tree):
            scan(node)
    return kinds


def _subscript_kind(node: Any, kinds: Mapping[str, str]) -> str | None:
    """下标载体种类（builtins_like/builtins_dict/sys_modules/unknown_name）。"""
    value = node.value
    if isinstance(value, ast.Name):
        if value.id in _BUILTIN_NAMESPACE_NAMES:
            return "builtins_like"
        kind = kinds.get(value.id)
        if kind in {"builtins_like", "builtins_dict", "sys_modules"}:
            return kind
        return "unknown_name"
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        if value.func.id in _NAMESPACE_LOOKUP_CALLS:
            return "builtins_like"
        return None
    return _expr_alias_kind(value, kinds)


def _dynamic_escape_findings(path: str, tree: ast.AST) -> list[str]:
    """上下文感知扫描单个 AST：返回动态加载逃逸（空表即无逃逸）；覆盖
    直接调用、限定属性、命名空间下标与 __dict__ 的 get/__getitem__。"""
    kinds = _collect_alias_kinds(tree)
    findings: list[str] = []

    def flag(message: str) -> None:
        findings.append(f"{path}: {message}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            kind = _subscript_kind(node, kinds)
            if kind == "sys_modules":
                flag("sys.modules 注册表下标（fail-closed）")
                continue
            if kind in {"builtins_like", "builtins_dict", "unknown_name"}:
                folded = _fold_constant_str(node.slice)
                if folded is None:
                    flag("命名空间动态下标无法折叠（fail-closed）")
                elif folded in _LOADER_NAMES:
                    flag(f"命名空间下标加载 {folded}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in _DIRECT_LOADER_CALLS:
                    flag(f"直接调用 {func.id}")
                elif func.id in _GETATTR_LOADER_CALLS and len(node.args) >= 2:
                    folded = _fold_constant_str(node.args[1])
                    if folded is not None and folded in _LOADER_NAMES:
                        flag(f"{func.id} 加载 API {folded}")
                elif kinds.get(func.id) == "loader_fn":
                    flag(f"加载 API 别名调用 {func.id}")
            elif isinstance(func, ast.Attribute):
                if func.attr in {"get", "__getitem__"}:
                    base = _expr_alias_kind(func.value, kinds)
                    if base == "builtins_dict":
                        folded = _fold_constant_str(node.args[0]) if node.args else None
                        if folded is not None and folded in _LOADER_NAMES:
                            flag(f"builtins.__dict__.{func.attr} 加载 {folded}")
                elif func.attr in _LOADER_NAMES and not (
                    func.attr == "compile"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "re"
                ):
                    flag(f"限定属性调用 {func.attr}")
    return findings


def _scan_dynamic_import_escapes(
    root: Path,
    candidate_tree: str,
    changes: Sequence[Mapping[str, Any]],
) -> list[str]:
    """扫描候选 tree 中被修改的 .py 文件的动态加载逃逸（fail-closed）。"""

    findings: list[str] = []
    for item in changes:
        path = item.get("new_path")
        if not path or not path.endswith(".py") or item["status"] == "D":
            continue
        try:
            text = _decode_utf8(
                _read_tree_file(root, candidate_tree, path), label=f"候选文件 {path}"
            )
            tree = ast.parse(text, filename=path)
        except (M1GateError, SyntaxError) as exc:
            raise M1GateError(f"候选文件无法解析 {path}: {exc}") from exc
        findings.extend(_dynamic_escape_findings(str(path), tree))
    return findings


def _check_edges(
    base_edges: set[tuple[str, str]],
    candidate_edges: set[tuple[str, str]],
    contract: Mapping[str, Any],
) -> list[str]:
    violations: list[str] = []
    for edge in contract.get("expected_removed_edges", []):
        if not isinstance(edge, list) or len(edge) != 2:
            violations.append(f"contract 边格式无效: {edge!r}")
            continue
        source, target = str(edge[0]), str(edge[1])
        if (source, target) not in base_edges:
            violations.append(f"期望移除边在 base 中不存在: {source} -> {target}")
        if (source, target) in candidate_edges:
            violations.append(f"期望移除边仍在 candidate 中: {source} -> {target}")
    new_edges = sorted(candidate_edges - base_edges)
    if new_edges:
        violations.append(f"candidate 新增依赖边: {new_edges}")
    return violations


# removed-edge 探针契约（隔离域执行细节见 m1_gate_probe）
M1_PROBE_VERSION = 2
PROBE_IMAGE = "python:3.12-slim@sha256:d657ab0ade19f404a6ccc883ab399540de667aff751748ce23c07330c5a89e64"
# 受保护 OS 级 attestor 受信身份（base-owned trust root，拒绝自证）。
TRUSTED_PRODUCER_IDS = {"os_level_observer"}
# 仓库内 pinned attestor 公钥（RSA-2048 PKCS#1 v1.5 / SHA-256）；签发
# 私钥只存在于外部受保护 attestor，绝不进入仓库/PR runner/producer。
M1_ATTESTOR_PUBLIC_KEY = {
    "n": "aa3d65e4eb60cd389eab861615cfd541efaa9ad5b97c49c9d80c733103742531a7f2d2afb491f9755eb273b22b89003372e1f819b0ece1c645d0cb09e9dfb2595bd2512d33e25bd8a3ef0284768524e100af272a4af15f8649ca0e16b50c8a691d80e8aa9ca0fd3db919adbc8fea69c24e8ed0253f01ae53cfdddbb6e531a6b75f926a861394ab5121b44b67a5e13e47fae64e1019ed21f9ae29c9060b267ff43486812c4505fe51c887981906483dfabd46375972eee43c4fb33363540099b6e57fa45d75e8912f889b613f3efcf16e9c353239cf79c773c57599964d2642cc62e821febb2325678999a3bde784ca928e7cad8c017d0304d5142b00cdf7299d",
    "e": "10001",
}
# attestation 精确键集（拒绝未知键/缺失键）；signature 键不参与签名。
ATTESTATION_KEYS = {
    "schema_version",
    "probe_version",
    "attestation",
    "identity",
    "nonce",
    "signature",
    "image",
    "base_commit",
    "pr_head_commit",
    "candidate_commit",
    "candidate_tree",
    "contract_path",
    "contract_oid",
    "expected_edges",
    "edges_checked",
    "all_edges_confirmed",
    "exit_code",
    "environment",
    "evidence_sha256",
}
ATTESTATION_SIGNED_KEYS = tuple(sorted(ATTESTATION_KEYS - {"signature"}))
ATTESTATION_ENV_KEYS = {
    "network",
    "secrets",
    "read_only_inputs",
    "non_root",
    "no_new_privileges",
    "isolated_domain",
}
# EMSA-PKCS1-v1_5 SHA-256 DigestInfo 前缀（DER）。
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def attestation_signed_payload(payload: Mapping[str, Any]) -> bytes:
    """签名/验签共用的规范负载（精确键集除 signature 的规范 JSON）。"""
    signed = {name: payload[name] for name in ATTESTATION_SIGNED_KEYS}
    return json.dumps(
        signed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def attestation_signature_valid(payload: Mapping[str, Any]) -> bool:
    """pinned 公钥验签（RSA-2048 PKCS#1 v1.5 / SHA-256），签名绑定全部
    非 signature 字段（issuer/nonce/base/head/tree/contract/边集）。"""
    signature_hex = payload.get("signature")
    if not isinstance(signature_hex, str):
        return False
    try:
        n = int(M1_ATTESTOR_PUBLIC_KEY["n"], 16)
        e = int(M1_ATTESTOR_PUBLIC_KEY["e"], 16)
        signature = bytes.fromhex(signature_hex)
    except ValueError:
        return False
    size = (n.bit_length() + 7) // 8
    if n.bit_length() < 2048 or len(signature) != size:
        return False
    digest = hashlib.sha256(attestation_signed_payload(payload)).digest()
    expected = (
        b"\x00\x01"
        + b"\xff" * (size - 3 - len(_SHA256_DIGEST_INFO) - len(digest))
        + b"\x00"
        + _SHA256_DIGEST_INFO
        + digest
    )
    if len(expected) != size:
        return False
    encoded = pow(int.from_bytes(signature, "big"), e, n).to_bytes(size, "big")
    return hmac.compare_digest(encoded, expected)


def evidence_sha256(
    expected_edges: Sequence[Any],
    edges_checked: Sequence[Mapping[str, Any]],
    exit_code: int,
) -> str:
    """探针证据规范哈希（观察者写入、gate 复核）。"""
    payload = {
        "expected_edges": [list(edge) for edge in expected_edges],
        "edges_checked": list(edges_checked),
        "exit_code": exit_code,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
