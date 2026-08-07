"""架构门禁的配置、路径、AST import 图与 Git 基础设施。"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import re
import subprocess
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
try:
    import architecture_snapshots as snapshot_utils
except ImportError:
    from scripts import (
        architecture_snapshots as snapshot_utils,  # type: ignore[no-redef]
    )


class ArchitectureConfigError(ValueError):
    """表示 architecture.toml 缺少必要字段或字段类型错误。"""


class ArchitectureSourceError(RuntimeError):
    """表示源码无法被安全地解析。"""


@dataclass(frozen=True)
class ImportRef:
    """保存一个静态 import 的稳定、脱敏信息。"""

    source: str
    target: str
    symbol: str
    line: int
    local_target: str | None

    def as_dict(self) -> dict[str, Any]:
        """将 import 转为可排序的 JSON 对象。"""

        return {
            "source": self.source,
            "target": self.target,
            "symbol": self.symbol,
            "line": self.line,
            "local_target": self.local_target,
        }


def _as_list(value: Any, *, field: str) -> list[Any]:
    """将 TOML 数组验证为列表并复制，避免修改解析结果。"""

    if value is None:
        return []
    if not isinstance(value, list):
        raise ArchitectureConfigError(f"{field} 必须是数组")
    return list(value)


def _as_int(value: Any, *, field: str) -> int:
    """将阈值验证为正整数。"""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ArchitectureConfigError(f"{field} 必须是正整数")
    return value


def _normalise_rel(path: str | Path) -> str:
    """把路径规范化为使用正斜杠的仓库相对字符串。"""

    value = Path(path).as_posix()
    while value.startswith("./"):
        value = value[2:]
    return value


def _validate_repo_path(value: Any, *, field: str, allow_glob: bool = False) -> str:
    """拒绝绝对路径和父目录穿越，避免配置把扫描带出仓库。"""

    if not isinstance(value, str) or not value:
        raise ArchitectureConfigError(f"{field} 必须是非空字符串")
    if "\x00" in value or "\\" in value:
        raise ArchitectureConfigError(f"{field} 必须使用 POSIX 仓库路径")
    path = Path(value)
    windows_path = PureWindowsPath(value)
    if path.is_absolute() or windows_path.is_absolute() or ".." in path.parts:
        raise ArchitectureConfigError(f"{field} 必须是仓库内相对路径")
    if not allow_glob and any(token in value for token in ("*", "?", "[")):
        raise ArchitectureConfigError(f"{field} 不允许 glob")
    return _normalise_rel(value)


def load_config(path: Path) -> dict[str, Any]:
    """读取并验证架构配置，拒绝会导致静默放行的缺省字段。"""

    try:
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ArchitectureConfigError(f"无法读取架构配置 {path}: {exc}") from exc
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ArchitectureConfigError("architecture.toml 必须声明 version = 1")

    project = config.get("project")
    if (
        not isinstance(project, dict)
        or project.get("baseline_policy") != "only_decrease"
    ):
        raise ArchitectureConfigError("project.baseline_policy 必须是 only_decrease")
    policy = config.get("policy")
    if not isinstance(policy, dict):
        raise ArchitectureConfigError("缺少 [policy] 配置")
    if policy.get("default_mode") != "report":
        raise ArchitectureConfigError("M0 policy.default_mode 必须是 report")
    if not isinstance(policy.get("candidate_thresholds_effective"), bool):
        raise ArchitectureConfigError(
            "policy.candidate_thresholds_effective 必须是布尔值"
        )
    if policy.get("baseline_update_requires_explicit") is not True:
        raise ArchitectureConfigError(
            "policy.baseline_update_requires_explicit 必须为 true"
        )

    decisions = config.get("decisions")
    expected_decisions = {
        "TH-01",
        "TH-02",
        "TH-03",
        "TH-04",
        "TH-05",
        "AB-01",
        "AB-02",
    }
    if not isinstance(decisions, dict) or set(decisions) != expected_decisions:
        raise ArchitectureConfigError("[decisions] 必须完整声明 TH-01..05 与 AB-01..02")
    if not all(
        value in {"pending", "fixed", "blocked", "closed"}
        for value in decisions.values()
    ):
        raise ArchitectureConfigError("[decisions] 含无效状态")

    roots = config.get("roots")
    if not isinstance(roots, dict):
        raise ArchitectureConfigError("缺少 [roots] 配置")
    for field in (
        "production",
        "tests",
        "scripts",
        "markdown",
        "entry_files",
        "exclude",
    ):
        for index, value in enumerate(
            _as_list(roots.get(field), field=f"roots.{field}")
        ):
            _validate_repo_path(value, field=f"roots.{field}[{index}]")
    contract_names = _as_list(roots.get("contract_names"), field="roots.contract_names")
    if not all(
        isinstance(value, str) and value and Path(value).name == value
        for value in contract_names
    ):
        raise ArchitectureConfigError("roots.contract_names 只能包含文件名")

    thresholds = config.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ArchitectureConfigError("缺少 [thresholds] 配置")
    categories = ("production", "entry", "contract", "test", "script", "markdown")
    for category in categories:
        values = thresholds.get(category)
        if not isinstance(values, dict):
            raise ArchitectureConfigError(f"缺少 [thresholds.{category}]")
        for field in ("soft", "hard", "legacy_hard"):
            _as_int(values.get(field), field=f"thresholds.{category}.{field}")
        if values["soft"] > values["hard"]:
            raise ArchitectureConfigError(f"{category} 的 soft 不能大于 hard")

    signals = config.get("signals")
    if not isinstance(signals, dict):
        raise ArchitectureConfigError("缺少 [signals] 配置")
    for field in ("max_method_lines", "max_public_symbols", "max_import_fanout"):
        _as_int(signals.get(field), field=f"signals.{field}")

    dependencies = config.get("dependencies")
    if not isinstance(dependencies, dict):
        raise ArchitectureConfigError("缺少 [dependencies] 配置")
    for field in (
        "private_import_prefixes",
        "public_import_prefixes",
        "forbidden_wrapper_names",
        "feature_roots",
        "legacy_layer_prefixes",
    ):
        values = _as_list(dependencies.get(field), field=f"dependencies.{field}")
        if not all(isinstance(item, str) and item for item in values):
            raise ArchitectureConfigError(f"dependencies.{field} 只能包含非空字符串")
    feature_layers = dependencies.get("feature_layers")
    expected_layers = {"domain", "application", "infrastructure", "presentation"}
    if not isinstance(feature_layers, dict) or set(feature_layers) != expected_layers:
        raise ArchitectureConfigError(
            "dependencies.feature_layers 必须完整声明四层依赖"
        )
    for layer, allowed in feature_layers.items():
        values = _as_list(
            allowed,
            field=f"dependencies.feature_layers.{layer}",
        )
        if not all(isinstance(item, str) and item for item in values):
            raise ArchitectureConfigError(
                f"dependencies.feature_layers.{layer} 只能包含非空字符串"
            )
    edges = _as_list(
        dependencies.get("forbidden_edges"),
        field="dependencies.forbidden_edges",
    )
    for index, edge in enumerate(edges):
        if (
            not isinstance(edge, dict)
            or not edge.get("source")
            or not edge.get("target")
        ):
            raise ArchitectureConfigError(
                f"forbidden_edges[{index}] 缺少 source/target"
            )

    contracts = config.get("public_contracts")
    if not isinstance(contracts, list):
        raise ArchitectureConfigError("必须提供 public_contracts 台账")
    ids = [item.get("id") for item in contracts if isinstance(item, dict)]
    if sorted(ids) != [f"C{index}" for index in range(7)]:
        raise ArchitectureConfigError("public_contracts 必须恰好包含 C0-C6")
    required_fields = (
        "symbol",
        "current_imports",
        "public_candidates",
        "status",
        "stable",
        "no_wrapper",
        "owner",
        "stage",
        "versions",
        "contract_tests",
        "evidence",
    )
    for item in contracts:
        if not isinstance(item, dict):
            raise ArchitectureConfigError("public_contracts 项必须是表")
        missing = [field for field in required_fields if field not in item]
        if missing:
            raise ArchitectureConfigError(
                f"public_contracts[{item.get('id')}] 缺少字段: {', '.join(missing)}"
            )
        if item["status"] not in {"blocked", "public", "verified", "deprecated"}:
            raise ArchitectureConfigError(
                f"public_contracts[{item['id']}] status 无效: {item['status']}"
            )
        if item["status"] == "blocked" and item["no_wrapper"] is not True:
            raise ArchitectureConfigError(
                f"{item['id']} 为 blocked 时 no_wrapper 必须为 true"
            )
        if item["status"] == "blocked" and item["stable"] is not False:
            raise ArchitectureConfigError(
                f"{item['id']} 为 blocked 时 stable 必须为 false"
            )
        if not isinstance(item["stable"], bool) or not isinstance(
            item["no_wrapper"], bool
        ):
            raise ArchitectureConfigError(
                f"{item['id']} stable/no_wrapper 必须是布尔值"
            )
        for field in (
            "current_imports",
            "public_candidates",
            "versions",
            "contract_tests",
        ):
            values = _as_list(
                item[field], field=f"public_contracts.{item['id']}.{field}"
            )
            if not all(isinstance(value, str) and value for value in values):
                raise ArchitectureConfigError(
                    f"public_contracts.{item['id']}.{field} 只能包含非空字符串"
                )
        for field in ("symbol", "owner", "stage", "evidence"):
            if not isinstance(item[field], str) or not item[field].strip():
                raise ArchitectureConfigError(
                    f"public_contracts.{item['id']}.{field} 必须是非空字符串"
                )
        for index, value in enumerate(item["current_imports"]):
            _validate_repo_path(
                value,
                field=f"public_contracts.{item['id']}.current_imports[{index}]",
                allow_glob=True,
            )
        for index, value in enumerate(item["contract_tests"]):
            _validate_repo_path(
                value,
                field=f"public_contracts.{item['id']}.contract_tests[{index}]",
            )

    features = config.get("features")
    if not isinstance(features, dict) or not features:
        raise ArchitectureConfigError("缺少 [features] 配置")
    for feature, value in features.items():
        _validate_repo_path(value, field=f"features.{feature}")
    return config


def _path_matches_prefix(path: str, prefix: str) -> bool:
    """判断模块是否等于给定前缀或位于其子树。"""

    return path == prefix or path.startswith(prefix + ".")


def _module_for_path(relative_path: str) -> str:
    """把 Python 文件路径转换为稳定模块名。"""

    path = relative_path[:-3] if relative_path.endswith(".py") else relative_path
    if path.endswith("/__init__"):
        path = path[: -len("/__init__")]
    return path.replace("/", ".").replace("-", "_") or "__root__"


def _category_for_path(relative_path: str, config: Mapping[str, Any]) -> str | None:
    """按配置将文件归入生产、入口、契约、测试、脚本或 Markdown。"""

    path = _normalise_rel(relative_path)
    roots = config["roots"]
    excludes = tuple(_normalise_rel(item) for item in roots.get("exclude", []))
    if any(
        path == item or path.startswith(item.rstrip("/") + "/") for item in excludes
    ):
        return None
    if path.endswith(".md"):
        markdown_roots = tuple(
            _normalise_rel(item) for item in roots.get("markdown", [])
        )
        if path in markdown_roots or any(
            path == item or path.startswith(item.rstrip("/") + "/")
            for item in markdown_roots
            if "/" not in item or not item.endswith(".md")
        ):
            return "markdown"
        return None
    if not path.endswith(".py"):
        return None
    if path == "main.py" or path in {
        _normalise_rel(item) for item in roots.get("entry_files", [])
    }:
        return "entry"
    if path.startswith("tests/") or path == "tests":
        return "test"
    if path.startswith("scripts/") or path == "scripts":
        return "script"
    if Path(path).name in set(roots.get("contract_names", [])):
        return "contract"
    if path == "core" or path.startswith("core/"):
        return "production"
    return None


def discover_files(root: Path, config: Mapping[str, Any]) -> list[str]:
    """发现架构事实范围内的文件，并按路径排序去重。"""

    roots = config["roots"]
    candidates: set[str] = set()
    for key in ("production", "tests", "scripts", "markdown"):
        for item in roots.get(key, []):
            relative = _normalise_rel(item)
            path = root / relative
            if path.is_file():
                candidates.add(relative)
            elif path.is_dir():
                candidates.update(
                    _normalise_rel(child.relative_to(root))
                    for child in path.rglob("*")
                    if child.is_file()
                )
    return sorted(
        path for path in candidates if _category_for_path(path, config) is not None
    )


def _read_text(root: Path, relative_path: str) -> str:
    """以 UTF-8 读取源码；错误会转换为可定位的架构异常。"""

    try:
        return (root / relative_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ArchitectureSourceError(f"无法读取 {relative_path}: {exc}") from exc


def _parse_python(root: Path, relative_path: str) -> ast.Module:
    """解析一个 Python 文件并保留精确语法错误位置。"""

    try:
        return ast.parse(_read_text(root, relative_path), filename=relative_path)
    except SyntaxError as exc:
        location = f"{relative_path}:{exc.lineno or 0}:{exc.offset or 0}"
        raise ArchitectureSourceError(f"AST 解析失败 {location}: {exc.msg}") from exc


def _resolve_relative_import(
    source_module: str,
    level: int,
    imported_module: str | None,
    modules: set[str],
) -> str | None:
    """将相对 import 解析为已发现的本地模块名。"""

    if level <= 0:
        candidate = imported_module or ""
    else:
        if source_module.endswith(".__init__"):
            package = source_module.removesuffix(".__init__")
        else:
            package = (
                source_module.rsplit(".", 1)[0]
                if "." in source_module
                else source_module
            )
        for _ in range(level - 1):
            package = package.rsplit(".", 1)[0] if "." in package else ""
        candidate = ".".join(part for part in (package, imported_module or "") if part)
    if candidate in modules:
        return candidate
    prefixes = [name for name in modules if _path_matches_prefix(name, candidate)]
    return (
        sorted(prefixes, key=lambda value: (value.count("."), value))[0]
        if prefixes
        else None
    )


def _collect_imports(
    relative_path: str,
    tree: ast.Module,
    modules: set[str],
) -> list[ImportRef]:
    """从 AST 提取静态及字面量动态 import，并标注本地目标。"""

    source = _module_for_path(relative_path)
    resolution_source = (
        source + ".__init__" if relative_path.endswith("/__init__.py") else source
    )
    imports: list[ImportRef] = []
    importlib_aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "importlib"
    }
    import_module_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "importlib"
        for alias in node.names
        if alias.name == "import_module"
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(
                    ImportRef(
                        source,
                        alias.name,
                        alias.asname or alias.name.split(".")[-1],
                        node.lineno,
                        _resolve_relative_import(
                            resolution_source, 0, alias.name, modules
                        ),
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            target = "." * node.level + (node.module or "")
            local_target = _resolve_relative_import(
                resolution_source, node.level, node.module, modules
            )
            for alias in node.names:
                alias_target_name = target
                if node.level == 0 and node.module == "astrbot" and alias.name != "*":
                    alias_target_name = f"astrbot.{alias.name}"
                alias_target = local_target
                if local_target and f"{local_target}.{alias.name}" in modules:
                    alias_target = f"{local_target}.{alias.name}"
                imports.append(
                    ImportRef(
                        source,
                        alias_target_name,
                        alias.name,
                        node.lineno,
                        alias_target,
                    )
                )
        elif isinstance(node, ast.Call):
            function = node.func
            is_dynamic_import = (
                isinstance(function, ast.Name)
                and function.id in ({"__import__"} | import_module_names)
            ) or (
                isinstance(function, ast.Attribute)
                and function.attr == "import_module"
                and isinstance(function.value, ast.Name)
                and function.value.id in importlib_aliases
            )
            argument = (
                node.args[0]
                if node.args
                else next(
                    (item.value for item in node.keywords if item.arg == "name"), None
                )
            )
            if (
                is_dynamic_import
                and isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
                and argument.value
            ):
                target = argument.value
                imports.append(
                    ImportRef(
                        source,
                        target,
                        "dynamic_import",
                        node.lineno,
                        _resolve_relative_import(resolution_source, 0, target, modules),
                    )
                )
    return imports


def _package_for_module(module: str) -> str:
    """将模块归一到一级技术包，用于 SCC 和依赖方向分析。"""

    parts = module.split(".")
    return module if len(parts) <= 1 else ".".join(parts[:2])


def _feature_for_module(module: str) -> str | None:
    """返回 ``core.features.<name>`` 模块所属的 feature 名。"""

    parts = module.split(".")
    return parts[2] if len(parts) >= 3 and parts[:2] == ["core", "features"] else None


def _matches_module(module: str, pattern: str) -> bool:
    """同时支持前缀和 TOML glob 的模块匹配。"""

    return fnmatch.fnmatch(module, pattern) or _path_matches_prefix(module, pattern)


def _tarjan_scc(
    nodes: Iterable[str], edges: Iterable[tuple[str, str]]
) -> list[list[str]]:
    """用标准库实现确定性 Tarjan SCC，避免门禁新增运行时依赖。"""

    adjacency: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        adjacency[source].append(target)
    for node in nodes:
        adjacency.setdefault(node, [])
    for values in adjacency.values():
        values.sort()
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def strong_connect(node: str) -> None:
        """递归访问一个节点并收敛其强连通分量。"""

        nonlocal index
        indices[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in adjacency[node]:
            if target not in indices:
                strong_connect(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            components.append(sorted(component))

    for node in sorted(adjacency):
        if node not in indices:
            strong_connect(node)
    return sorted(
        (component for component in components if len(component) > 1),
        key=lambda component: (len(component), component),
        reverse=True,
    )


def _run_git(
    root: Path,
    *args: str,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """执行 Git 子命令，并将不可审计的失败统一转换为源码错误。"""

    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            env=dict(env) if env is not None else None,
        )
    except OSError as exc:
        raise ArchitectureSourceError(f"无法执行 Git 命令: {args[0]}") from exc
    if completed.returncode != 0:
        raise ArchitectureSourceError(
            f"Git 命令失败: {args[0]} (exit {completed.returncode})"
        )
    return completed


def _git_value(root: Path, *args: str) -> str:
    """读取必需的 Git 标识，空输出或命令失败时拒绝继续。"""

    value = _run_git(root, *args).stdout.strip()
    if not value:
        raise ArchitectureSourceError(f"Git identity 为空: {args[0]}")
    return value


def git_object(root: Path, revision: str) -> str:
    """解析并验证完整的 Git commit/tree object 标识。"""

    value = _git_value(root, "rev-parse", revision)
    if _GIT_OBJECT_RE.fullmatch(value) is None:
        raise ArchitectureSourceError(f"Git identity 不可解析: {revision}")
    return value


def git_changed_files(root: Path, base: str | None, head: str = "HEAD") -> list[str]:
    """读取 changed-files，并纳入暂存区与未跟踪文件。"""

    commands = (
        [["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base}..{head}"]]
        if base
        else [
            ["git", "diff", "--name-only", "--diff-filter=ACMR", head],
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            ["git", "ls-files", "--others", "--exclude-standard"],
        ]
    )
    values: set[str] = set()
    for command in commands:
        try:
            completed = subprocess.run(
                command, cwd=root, check=False, capture_output=True, text=True
            )
        except OSError as exc:
            raise ArchitectureSourceError(
                f"无法执行 Git changed-files 命令: {command[1]}"
            ) from exc
        if completed.returncode != 0:
            raise ArchitectureSourceError(
                f"Git changed-files 命令失败: {command[1]} (exit {completed.returncode})"
            )
        values.update(
            _normalise_rel(line)
            for line in completed.stdout.splitlines()
            if line.strip()
        )
    return sorted(values)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """以原子替换写入 JSON，避免中断留下半份 baseline。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_baseline(path: Path) -> dict[str, Any] | None:
    """读取 baseline；不存在时返回 None，格式错误则抛出配置异常。"""

    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchitectureConfigError(f"baseline 无法解析 {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ArchitectureConfigError("baseline schema_version 不受支持")
    required_types: dict[str, type[Any]] = {
        "files": dict,
        "summary": dict,
        "imports": dict,
        "dependency_graph": dict,
        "violations": dict,
        "snapshots": dict,
        "public_contracts": list,
        "analysis_tree_excludes": list,
    }
    for field, expected_type in required_types.items():
        if not isinstance(value.get(field), expected_type):
            raise ArchitectureConfigError(f"baseline.{field} 类型无效或缺失")
    if value.get("baseline_update_flow") != "explicit --write-baseline":
        raise ArchitectureConfigError(
            "baseline.baseline_update_flow 必须来自显式 --write-baseline 流程"
        )
    if (
        value.get("analysis_content_scope")
        != "report.files path/content SHA-256 manifest"
    ):
        raise ArchitectureConfigError("baseline.analysis_content_scope 无效")

    imports = value["imports"]
    for field, expected_type in {
        "private": list,
        "public": list,
        "summary": dict,
    }.items():
        if not isinstance(imports.get(field), expected_type):
            raise ArchitectureConfigError(f"baseline.imports.{field} 类型无效或缺失")

    graph = value["dependency_graph"]
    if (
        not isinstance(graph.get("edges"), list)
        or isinstance(graph.get("largest_scc"), bool)
        or not isinstance(graph.get("largest_scc"), int)
    ):
        raise ArchitectureConfigError(
            "baseline.dependency_graph 缺少 edges/largest_scc"
        )
    violations = value["violations"]
    for field in ("forbidden_imports", "forbidden_edges", "forbidden_wrappers"):
        if not isinstance(violations.get(field), list):
            raise ArchitectureConfigError(f"baseline.violations.{field} 类型无效或缺失")
    for field in ("source_commit", "source_tree", "analysis_tree"):
        if (
            not isinstance(value.get(field), str)
            or _GIT_OBJECT_RE.fullmatch(value[field]) is None
        ):
            raise ArchitectureConfigError(f"baseline.{field} 缺失或不是完整 Git object")
    for field in (
        "architecture_config_sha256",
        "policy_fingerprint",
        "contract_fingerprint",
        "analysis_content_sha256",
        "facts_sha256",
        "baseline_integrity_sha256",
    ):
        if (
            not isinstance(value.get(field), str)
            or _SHA256_RE.fullmatch(value[field]) is None
        ):
            raise ArchitectureConfigError(f"baseline.{field} 缺失或不是 SHA-256")
    try:
        expected_content_hash = snapshot_utils.analysis_manifest_sha256(value["files"])
    except ValueError as exc:
        raise ArchitectureConfigError(str(exc)) from exc
    if value["analysis_content_sha256"] != expected_content_hash:
        raise ArchitectureConfigError(
            "baseline.analysis_content_sha256 与 files 内容不一致"
        )
    expected_facts_hash = snapshot_utils.facts_sha256(value)
    if value["facts_sha256"] != expected_facts_hash:
        raise ArchitectureConfigError("baseline.facts_sha256 与稳定事实内容不一致")
    integrity_payload = dict(value)
    integrity_payload.pop("baseline_integrity_sha256", None)
    expected_integrity = hashlib.sha256(
        snapshot_utils.canonical_json(integrity_payload).encode("utf-8")
    ).hexdigest()
    if value["baseline_integrity_sha256"] != expected_integrity:
        raise ArchitectureConfigError(
            "baseline_integrity_sha256 与 baseline 内容不一致"
        )
    return value
