"""架构门禁的公开符号、路由、命令、工具与 schema 静态快照。"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

if TYPE_CHECKING:
    try:
        from architecture_core import ImportRef
    except ImportError:
        from scripts.architecture_core import ImportRef


_FACTS_FIELDS = (
    "schema_version",
    "files",
    "summary",
    "imports",
    "dependency_graph",
    "violations",
    "snapshots",
    "public_contracts",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_json(value: Any) -> str:
    """返回用于证据摘要的规范 JSON。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_facts_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """提取报告稳定事实，并排除运行模式与 provenance 元数据。"""

    return {field: value.get(field) for field in _FACTS_FIELDS}


def facts_sha256(value: Mapping[str, Any]) -> str:
    """计算不受运行模式与 Git 元数据影响的稳定事实摘要。"""

    payload = canonical_json(stable_facts_payload(value)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def config_fingerprints(config: Mapping[str, Any]) -> dict[str, str]:
    """分别计算完整配置、策略和 C0-C6 契约指纹。"""

    policy_payload = {
        key: config.get(key, [] if key == "exceptions" else {})
        for key in (
            "project",
            "policy",
            "decisions",
            "roots",
            "thresholds",
            "signals",
            "dependencies",
            "exceptions",
        )
    }
    contract_payload = {
        "public_contracts": config.get("public_contracts", []),
        "feature_requirements": config.get("feature_requirements", {}),
    }

    def digest(value: Any) -> str:
        """计算单个规范 JSON 值的 SHA-256。"""

        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

    return {
        "architecture_config_sha256": digest(config),
        "policy_fingerprint": digest(policy_payload),
        "contract_fingerprint": digest(contract_payload),
    }


def analysis_manifest_sha256(files: Mapping[str, Any]) -> str:
    """计算架构事实中路径/内容摘要清单的 SHA-256。"""

    manifest: dict[str, str] = {}
    for path, record in sorted(files.items()):
        if (
            not isinstance(record, dict)
            or _SHA256_RE.fullmatch(str(record.get("sha256", ""))) is None
        ):
            raise ValueError(f"baseline.files.{path}.sha256 缺失或不是 SHA-256")
        manifest[str(path)] = str(record["sha256"])
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def _core_utils() -> Any:
    """延迟加载 core 工具，避免静态快照模块与 core 形成导入环。"""

    try:
        import architecture_core
    except ImportError:
        from scripts import architecture_core

    return architecture_core


def git_worktree_tree(root: Path, *, exclude_paths: Sequence[str] = ()) -> str:
    """通过独立临时索引生成稳定 worktree tree，不修改真实索引。"""

    core = _core_utils()
    root = root.resolve()
    handle = tempfile.NamedTemporaryFile(
        prefix=".architecture-index-", dir=root, delete=False
    )
    index_path = Path(handle.name)
    handle.close()
    index_path.unlink(missing_ok=True)
    exclusions = {".agent_artifacts", "reports", "scripts/baselines/architecture.json"}
    exclusions.update(core._normalise_rel(path) for path in exclude_paths)
    pathspecs = [
        ".",
        ":(exclude,glob).tmp-*",
        ":(exclude,glob).tmp-*/**",
        ":(exclude,glob).architecture-index-*",
    ]
    for path in sorted(exclusions):
        pathspecs.extend(
            (f":(exclude){path.rstrip('/')}", f":(exclude,glob){path.rstrip('/')}/**")
        )
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(index_path)
    try:
        core._run_git(root, "read-tree", "HEAD", env=env)
        core._run_git(root, "add", "-A", "--", *pathspecs, env=env)
        value = core._run_git(root, "write-tree", env=env).stdout.strip()
    finally:
        index_path.unlink(missing_ok=True)
        index_path.with_name(index_path.name + ".lock").unlink(missing_ok=True)
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise core.ArchitectureSourceError("Git worktree tree 不可解析")
    return value


def analysis_content_sha256(root: Path, files: Mapping[str, Any]) -> str:
    """计算报告文件路径与内容摘要，读取失败时保持失败闭合。"""

    try:
        manifest = {
            str(path): hashlib.sha256((root / path).read_bytes()).hexdigest()
            for path in sorted(files)
        }
    except OSError as exc:
        raise _core_utils().ArchitectureSourceError(
            f"无法读取 analysis content: {exc}"
        ) from exc
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def public_names(tree: ast.Module) -> list[str]:
    """提取模块顶层公开定义名，用于公开符号 fan-out 信号。"""

    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets: Iterable[ast.expr]
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.add(target.id)
    return sorted(names)


def _decorator_name(node: ast.AST) -> str:
    """获取 decorator 的可读限定名。"""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _decorator_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def literal_strings(node: ast.AST) -> list[str]:
    """收集节点子树中的字符串常量，保持出现顺序。"""

    return [
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]


def method_signals(
    tree: ast.Module,
    relative_path: str,
    limit: int,
) -> list[dict[str, Any]]:
    """报告超过方法软信号阈值的函数，不将其当作行数硬失败。"""

    findings: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.end_lineno is None:
            continue
        lines = node.end_lineno - node.lineno + 1
        if lines > limit:
            findings.append(
                {
                    "path": relative_path,
                    "name": node.name,
                    "line": node.lineno,
                    "lines": lines,
                }
            )
    return sorted(findings, key=lambda item: (item["path"], item["line"], item["name"]))


def _safe_value(node: ast.AST, environment: dict[str, Any]) -> Any:
    """求值 route catalog 使用的字面量子集，不执行任意 Python。"""

    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return environment.get(node.id)
    if isinstance(node, ast.Attribute):
        return _decorator_name(node)
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [_safe_value(item, environment) for item in node.elts]
        return values if all(value is not None for value in values) else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                resolved = _safe_value(value.value, environment)
                if resolved is None:
                    return None
                parts.append(str(resolved))
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _safe_value(node.left, environment)
        right = _safe_value(node.right, environment)
        return (
            left + right if isinstance(left, str) and isinstance(right, str) else None
        )
    return None


def _bind_target(target: ast.AST, value: Any, environment: dict[str, Any]) -> None:
    """把静态 for-loop 值绑定到名称或 tuple target。"""

    if isinstance(target, ast.Name):
        environment[target.id] = value
    elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, list):
        for child, child_value in zip(target.elts, value, strict=False):
            _bind_target(child, child_value, environment)


def _route_contract(
    path: str,
    methods: list[str],
    handler: str,
    source: str,
    *,
    aliases: bool,
) -> dict[str, Any]:
    """按 PluginPageApi 当前公开元数据规则生成静态契约项。"""

    normalized = [str(method).upper() for method in methods]
    lowered = path.lower()
    if "POST" not in normalized:
        risk = "read"
    elif "/dashboard/install" in lowered or "/dashboard/build" in lowered:
        risk = "runtime_exec"
    elif any(
        token in lowered
        for token in ("/delete", "batch-delete", "/purge", "/restore", "/reset")
    ):
        risk = "destructive"
    elif any(
        token in lowered
        for token in (
            "/maintenance/",
            "/backup/",
            "/backfill/start",
            "/config/",
            "/quality/reset",
            "/system/",
            "/update/",
        )
    ):
        risk = "maintenance"
    else:
        risk = "write"
    no_ready_suffixes = (
        "/delegation/status",
        "/delegation/provided-services",
        "/config/schema",
        "/config/state",
        "/config/apply",
        "/update/check",
        "/update/status",
    )
    return {
        "path": path,
        "methods": normalized,
        "handler_name": handler,
        "risk": risk,
        "auth": "admin" if "POST" in normalized else "host",
        "aliases": aliases,
        "requires_ready": not path.endswith(no_ready_suffixes),
        "write_guard": risk in {"write", "maintenance", "destructive", "runtime_exec"},
        "source": source,
    }


def snapshot_routes(
    relative_path: str,
    tree: ast.Module,
    text: str,
) -> list[dict[str, Any]]:
    """静态解释 register_routes 的字面量注册，冻结完整 Page 元数据。"""

    del text
    environment: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            value = _safe_value(node.value, environment)
            if value is not None:
                _bind_target(node.targets[0], value, environment)
    prefix = environment.get("PAGE_API_PREFIX")
    alias_names = environment.get("PAGE_API_ALIASES", [])
    alias_prefixes = (
        [f"/{name}/page" for name in alias_names]
        if isinstance(alias_names, list)
        else []
    )
    routes: list[dict[str, Any]] = []

    def record(call: ast.Call, local: dict[str, Any]) -> None:
        """记录一个可静态求值的 register 调用。"""

        if _decorator_name(call.func) != "register" or len(call.args) < 3:
            return
        path = _safe_value(call.args[0], local)
        methods = _safe_value(call.args[2], local)
        if not isinstance(path, str) or not isinstance(methods, list):
            return
        handler_value = _safe_value(call.args[1], local)
        handler = (
            handler_value
            if isinstance(handler_value, str)
            else _decorator_name(call.args[1]) or "<handler>"
        )
        routes.append(
            _route_contract(path, methods, handler, relative_path, aliases=False)
        )
        if isinstance(prefix, str) and path.startswith(prefix):
            suffix = path[len(prefix) :]
            routes.extend(
                _route_contract(
                    alias_prefix + suffix,
                    methods,
                    handler,
                    relative_path,
                    aliases=True,
                )
                for alias_prefix in alias_prefixes
            )

    def walk(statements: list[ast.stmt], local: dict[str, Any]) -> None:
        """遍历 register_routes 语句并展开有限字面量 for-loop。"""

        for statement in statements:
            if isinstance(statement, ast.Expr) and isinstance(
                statement.value, ast.Call
            ):
                record(statement.value, local)
            elif isinstance(statement, ast.For):
                values = _safe_value(statement.iter, local)
                if isinstance(values, list):
                    for value in values:
                        branch = dict(local)
                        _bind_target(statement.target, value, branch)
                        walk(statement.body, branch)
            elif isinstance(statement, ast.If):
                walk(statement.body, dict(local))
                walk(statement.orelse, dict(local))
            elif isinstance(statement, ast.Try):
                walk(statement.body, dict(local))
                for handler_node in statement.handlers:
                    walk(handler_node.body, dict(local))
                walk(statement.orelse, dict(local))
                walk(statement.finalbody, dict(local))

    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "register_routes"
        ):
            walk(node.body, dict(environment))
    return sorted(
        {
            (item["path"], tuple(item["methods"]), item["handler_name"]): item
            for item in routes
        }.values(),
        key=lambda item: (item["path"], item["methods"], item["handler_name"]),
    )


def snapshot_commands(relative_path: str, text: str) -> list[str]:
    """提取命令 decorator 的稳定字符串参数。"""

    del relative_path
    values = {
        match.group(1)
        for match in re.finditer(
            r"(?:command|command_group)\s*\(\s*[\"']([^\"']+)", text
        )
    }
    values.update(
        match.group(1)
        for match in re.finditer(r"[\"'](/memora(?:\s[^\"']*)?)[\"']", text)
    )
    return sorted(values)


def snapshot_tools(relative_path: str, tree: ast.Module, text: str) -> list[str]:
    """提取 Agent tool 名称和类名，作为注册协议的静态快照。"""

    if "/tools/" not in f"/{relative_path}" and not relative_path.endswith("_tool.py"):
        return []
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name.endswith("Tool"):
            values.add(f"class:{node.name}")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(
                isinstance(target, ast.Name) and target.id in {"name", "tool_name"}
                for target in targets
            ):
                continue
            values.update(
                value
                for value in literal_strings(node.value)
                if value and len(value) <= 120
            )
    values.update(
        match.group(1)
        for match in re.finditer(
            r"(?:self\.)?(?:name|tool_name)\s*=\s*[\"']([^\"']+)", text
        )
    )
    return sorted(values)


def schema_snapshot(root: Path) -> dict[str, Any]:
    """读取配置 schema 的键集合和内容摘要，不记录配置值或路径。"""

    path = root / "_conf_schema.json"
    if not path.exists():
        return {"present": False, "sha256": None, "leaf_keys": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        try:
            from architecture_core import ArchitectureSourceError
        except ImportError:
            from scripts.architecture_core import ArchitectureSourceError

        raise ArchitectureSourceError(f"配置 schema 无法解析: {exc}") from exc
    leaf_keys: list[str] = []

    def walk(value: Any, prefix: str) -> None:
        """递归收集 schema 的叶字段名。"""

        if isinstance(value, dict):
            properties = value.get("properties") if "properties" in value else value
            if isinstance(properties, dict):
                for key, child in properties.items():
                    walk(child, f"{prefix}.{key}" if prefix else str(key))
                return
        leaf_keys.append(prefix)

    walk(payload, "")
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "present": True,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "leaf_keys": sorted(key for key in leaf_keys if key),
    }


def reexport_snapshot(
    relative_path: str,
    imports: Sequence[ImportRef],
    tree: ast.Module,
) -> list[str]:
    """提取 package ``__init__`` 中的显式 re-export。"""

    if Path(relative_path).name != "__init__.py":
        return []
    values = {f"{item.target}:{item.symbol}" for item in imports if item.symbol != "*"}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            values.update(value for value in literal_strings(node.value) if value)
    return sorted(values)
