"""将架构基元编排为完整、可序列化的仓库报告。"""

from __future__ import annotations

import fnmatch
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from architecture_core import (
        ArchitectureSourceError,
        ImportRef,
        _category_for_path,
        _collect_imports,
        _feature_for_module,
        _matches_module,
        _module_for_path,
        _normalise_rel,
        _package_for_module,
        _parse_python,
        _read_text,
        _tarjan_scc,
        discover_files,
    )
    from architecture_snapshots import (
        method_signals,
        public_names,
        reexport_snapshot,
        schema_snapshot,
        snapshot_commands,
        snapshot_routes,
        snapshot_tools,
    )
except ImportError:
    from scripts.architecture_core import (  # type: ignore[no-redef]
        ArchitectureSourceError,
        ImportRef,
        _category_for_path,
        _collect_imports,
        _feature_for_module,
        _matches_module,
        _module_for_path,
        _normalise_rel,
        _package_for_module,
        _parse_python,
        _read_text,
        _tarjan_scc,
        discover_files,
    )
    from scripts.architecture_snapshots import (  # type: ignore[no-redef]
        method_signals,
        public_names,
        reexport_snapshot,
        schema_snapshot,
        snapshot_commands,
        snapshot_routes,
        snapshot_tools,
    )


def _config_edges(config: Mapping[str, Any]) -> list[dict[str, str]]:
    """返回规范化的 forbidden edge 规则。"""

    values = config.get("dependencies", {}).get("forbidden_edges", [])
    return [
        {
            "source": str(item["source"]),
            "target": str(item["target"]),
            "reason": str(item.get("reason", "forbidden dependency")),
        }
        for item in values
    ]


def _edge_matches(source: str, target: str, rule: Mapping[str, str]) -> bool:
    """判断模块边是否命中配置规则。"""

    return _matches_module(source, rule["source"]) and _matches_module(
        target, rule["target"]
    )


def _private_import(import_ref: ImportRef, config: Mapping[str, Any]) -> bool:
    """判断 import 是否属于 AstrBot 私有模块边界。"""

    prefixes = config["dependencies"].get("private_import_prefixes", [])
    return any(
        import_ref.target == prefix.rstrip(".") or import_ref.target.startswith(prefix)
        for prefix in prefixes
    )


def _public_import(import_ref: ImportRef, config: Mapping[str, Any]) -> bool:
    """判断 import 是否来自 AstrBot 公共入口。"""

    prefixes = config["dependencies"].get("public_import_prefixes", [])
    return any(
        import_ref.target == prefix.rstrip(".") or import_ref.target.startswith(prefix)
        for prefix in prefixes
    )


def _contract_for_import(
    import_ref: ImportRef,
    contracts: Sequence[Mapping[str, Any]],
) -> str | None:
    """按符号或调用方路径关联 C0-C6 台账项。"""

    source_path = import_ref.source.replace(".", "/") + ".py"
    for contract in contracts:
        symbol = str(contract.get("symbol", ""))
        if symbol and (symbol in import_ref.target or symbol in import_ref.symbol):
            return str(contract["id"])
        for path in contract.get("current_imports", []):
            path_text = str(path)
            if path_text and fnmatch.fnmatch(source_path, path_text):
                if import_ref.target.startswith("astrbot.core."):
                    return str(contract["id"])
    return None


def _changed_set(selected_files: Iterable[str] | None) -> set[str] | None:
    """规范化调用方传入的 changed-files；None 表示全量。"""

    if selected_files is None:
        return None
    return {_normalise_rel(path) for path in selected_files}


def _feature_layer(module: str, config: Mapping[str, Any]) -> str | None:
    """返回复杂 feature 模块所属四层或 contracts。"""

    parts = module.split(".")
    if len(parts) < 4 or parts[:2] != ["core", "features"]:
        return None
    layer = parts[3]
    known = set(config["dependencies"].get("feature_layers", {})) | {"contracts"}
    return layer if layer in known else None


def analyze_repository(
    root: Path,
    config: Mapping[str, Any],
    *,
    selected_files: Iterable[str] | None = None,
) -> dict[str, Any]:
    """执行纯静态分析并返回可序列化报告。"""

    all_files = discover_files(root, config)
    selected = _changed_set(selected_files)
    architecture_files = {
        path for path in all_files if path == "main.py" or path.startswith("core/")
    }
    modules = {
        _module_for_path(path) for path in architecture_files if path.endswith(".py")
    }
    file_records: dict[str, dict[str, Any]] = {}
    imports: list[ImportRef] = []
    parse_errors: list[dict[str, str]] = []
    method_signal_items: list[dict[str, Any]] = []
    public_symbols: dict[str, list[str]] = {}
    routes: list[dict[str, Any]] = []
    commands: set[str] = set()
    tools: set[str] = set()
    reexports: list[str] = []
    forbidden_wrappers: list[dict[str, str]] = []

    forbidden_wrapper_names = {
        str(value)
        for value in config.get("dependencies", {}).get("forbidden_wrapper_names", [])
    }
    for relative_path in sorted(architecture_files):
        if Path(relative_path).name in forbidden_wrapper_names:
            forbidden_wrappers.append(
                {
                    "path": relative_path,
                    "name": Path(relative_path).name,
                    "reason": "禁止的 wrapper 文件名",
                }
            )

    for relative_path in all_files:
        text = _read_text(root, relative_path)
        category = _category_for_path(relative_path, config)
        if category is None:
            continue
        file_records[relative_path] = {
            "category": category,
            "lines": len(text.splitlines()),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        if not relative_path.endswith(".py"):
            continue
        try:
            tree = _parse_python(root, relative_path)
        except ArchitectureSourceError as exc:
            parse_errors.append({"path": relative_path, "error": str(exc)})
            continue
        if relative_path not in architecture_files:
            continue
        module_imports = _collect_imports(relative_path, tree, modules)
        imports.extend(module_imports)
        public_symbols[relative_path] = public_names(tree)
        method_signal_items.extend(
            method_signals(
                tree, relative_path, int(config["signals"]["max_method_lines"])
            )
        )
        routes.extend(snapshot_routes(relative_path, tree, text))
        commands.update(snapshot_commands(relative_path, text))
        tools.update(snapshot_tools(relative_path, tree, text))
        reexports.extend(reexport_snapshot(relative_path, module_imports, tree))

    imports.sort(key=lambda item: (item.source, item.line, item.target, item.symbol))
    local_edges = sorted(
        {
            (item.source, item.local_target)
            for item in imports
            if item.local_target is not None
        }
    )
    package_edges = sorted(
        {
            (_package_for_module(source), _package_for_module(target))
            for source, target in local_edges
            if _package_for_module(source) != _package_for_module(target)
            and "core"
            not in {
                _package_for_module(source),
                _package_for_module(target),
            }
        }
    )
    package_nodes = sorted(
        {
            _package_for_module(module)
            for module in modules
            if _package_for_module(module) != "core"
        }
        | {source for source, _ in package_edges}
        | {target for _, target in package_edges}
    )
    sccs = _tarjan_scc(package_nodes, package_edges)

    private_imports: list[dict[str, Any]] = []
    public_imports: list[dict[str, Any]] = []
    forbidden_edges: list[dict[str, Any]] = []
    contracts = config["public_contracts"]
    for import_ref in imports:
        if _private_import(import_ref, config):
            item = import_ref.as_dict()
            item["contract_id"] = _contract_for_import(import_ref, contracts)
            private_imports.append(item)
        if _public_import(import_ref, config):
            public_imports.append(import_ref.as_dict())
        local_target = import_ref.local_target
        if local_target is not None:
            for rule in _config_edges(config):
                if _edge_matches(import_ref.source, local_target, rule):
                    forbidden_edges.append(
                        {
                            "source": import_ref.source,
                            "target": local_target,
                            "line": import_ref.line,
                            "reason": rule["reason"],
                        }
                    )
        source_feature = _feature_for_module(import_ref.source)
        target_feature = _feature_for_module(local_target or "")
        if source_feature and local_target:
            legacy = config["dependencies"].get("legacy_layer_prefixes", [])
            if any(_matches_module(local_target, str(prefix)) for prefix in legacy):
                forbidden_edges.append(
                    {
                        "source": import_ref.source,
                        "target": local_target,
                        "line": import_ref.line,
                        "reason": "迁移 feature 不得依赖旧技术层",
                    }
                )
            if (
                target_feature
                and target_feature != source_feature
                and not local_target.endswith(".contracts")
            ):
                forbidden_edges.append(
                    {
                        "source": import_ref.source,
                        "target": local_target,
                        "line": import_ref.line,
                        "reason": "feature 间只能依赖显式 contracts.py",
                    }
                )
            source_layer = _feature_layer(import_ref.source, config)
            target_layer = _feature_layer(local_target, config)
            if target_feature == source_feature and source_layer and target_layer:
                if source_layer == "contracts":
                    allowed_layers = {"contracts", "domain"}
                else:
                    allowed_layers = set(
                        config["dependencies"]["feature_layers"][source_layer]
                    )
                if target_layer not in allowed_layers:
                    forbidden_edges.append(
                        {
                            "source": import_ref.source,
                            "target": local_target,
                            "line": import_ref.line,
                            "reason": "feature 四层依赖方向违规",
                        }
                    )
        source_layer = _feature_layer(import_ref.source, config)
        if source_layer in {
            "domain",
            "application",
            "contracts",
        } and import_ref.target.startswith(
            ("astrbot", "quart", "sqlite3", "aiosqlite", "faiss", "sqlalchemy")
        ):
            forbidden_edges.append(
                {
                    "source": import_ref.source,
                    "target": import_ref.target,
                    "line": import_ref.line,
                    "reason": "domain/application/contracts 禁止平台与基础设施依赖",
                }
            )
        if import_ref.source.startswith("core.shared") and (
            _private_import(import_ref, config)
            or import_ref.target.startswith(
                ("astrbot", "quart", "sqlite", "aiosqlite", "faiss")
            )
        ):
            forbidden_edges.append(
                {
                    "source": import_ref.source,
                    "target": import_ref.target,
                    "line": import_ref.line,
                    "reason": "shared 禁止平台/基础设施依赖",
                }
            )
        if import_ref.source.startswith("core.platform.astrbot") and _private_import(
            import_ref, config
        ):
            forbidden_edges.append(
                {
                    "source": import_ref.source,
                    "target": import_ref.target,
                    "line": import_ref.line,
                    "reason": "AstrBot 适配层不得新增或包装私有导入",
                }
            )
    forbidden_edges = sorted(
        {
            (item["source"], item["target"], item["line"], item["reason"]): item
            for item in forbidden_edges
        }.values(),
        key=lambda item: (item["source"], item["line"], item["target"], item["reason"]),
    )

    line_violations: list[dict[str, Any]] = []
    candidate_thresholds_effective = bool(
        config["policy"]["candidate_thresholds_effective"]
    )
    for path, record in sorted(file_records.items()):
        values = config["thresholds"][record["category"]]
        lines = int(record["lines"])
        if lines > int(values["soft"]):
            candidate_kind = "hard" if lines > int(values["hard"]) else "soft"
            effective_hard = int(
                values["hard"]
                if candidate_thresholds_effective
                else values["legacy_hard"]
            )
            line_violations.append(
                {
                    "path": path,
                    "category": record["category"],
                    "lines": lines,
                    "soft": int(values["soft"]),
                    "hard": int(values["hard"]),
                    "legacy_hard": int(values["legacy_hard"]),
                    "effective_hard": effective_hard,
                    "candidate_kind": candidate_kind,
                    "kind": "hard" if lines > effective_hard else "soft",
                }
            )

    imports_by_source: dict[str, set[str]] = defaultdict(set)
    for item in imports:
        imports_by_source[item.source].add(item.target)
    fanout_limit = int(config["signals"]["max_import_fanout"])
    fanout = [
        {"module": source, "count": len(targets), "limit": fanout_limit}
        for source, targets in sorted(imports_by_source.items())
        if len(targets) > fanout_limit
    ]
    public_limit = int(config["signals"]["max_public_symbols"])
    public_symbol_signals = [
        {"path": path, "count": len(names), "limit": public_limit, "names": names}
        for path, names in sorted(public_symbols.items())
        if len(names) > public_limit
    ]
    snapshots = {
        "routes": sorted(
            routes,
            key=lambda item: (
                item["path"],
                item["methods"],
                item["source"],
                item["handler_name"],
            ),
        ),
        "commands": sorted(commands),
        "tools": sorted(tools),
        "reexports": sorted(set(reexports)),
        "public_symbols": dict(sorted(public_symbols.items())),
        "config_schema": schema_snapshot(root),
        "public_imports": sorted(
            {
                (item["source"], item["target"], item["symbol"], item["line"]): item
                for item in public_imports
            }.values(),
            key=lambda item: (
                item["source"],
                item["line"],
                item["target"],
                item["symbol"],
            ),
        ),
    }
    by_category: dict[str, dict[str, int]] = defaultdict(
        lambda: {"files": 0, "lines": 0}
    )
    for item in file_records.values():
        by_category[item["category"]]["files"] += 1
        by_category[item["category"]]["lines"] += int(item["lines"])
    private_statements = {
        (item["source"], item["target"], item["line"]) for item in private_imports
    }
    return {
        "schema_version": 1,
        "files": dict(sorted(file_records.items())),
        "summary": {
            "file_count": len(file_records),
            "python_file_count": sum(
                1 for path in file_records if path.endswith(".py")
            ),
            "total_lines": sum(int(item["lines"]) for item in file_records.values()),
            "by_category": {key: by_category[key] for key in sorted(by_category)},
        },
        "imports": {
            "all": [item.as_dict() for item in imports],
            "private": private_imports,
            "public": public_imports,
            "summary": {
                "private_records": len(private_imports),
                "private_statements": len(private_statements),
                "private_files": len({item["source"] for item in private_imports}),
                "private_unmapped_records": sum(
                    1 for item in private_imports if item["contract_id"] is None
                ),
                "public_records": len(public_imports),
            },
        },
        "dependency_graph": {
            "nodes": package_nodes,
            "edges": [
                {"source": source, "target": target} for source, target in package_edges
            ],
            "module_edges": [
                {"source": source, "target": target} for source, target in local_edges
            ],
            "sccs": sccs,
            "largest_scc": len(sccs[0]) if sccs else 1,
        },
        "violations": {
            "line_budget": line_violations,
            "method_length": method_signal_items,
            "public_symbols": public_symbol_signals,
            "import_fanout": fanout,
            "forbidden_imports": private_imports,
            "forbidden_edges": forbidden_edges,
            "forbidden_wrappers": forbidden_wrappers,
            "parse_errors": parse_errors,
        },
        "snapshots": snapshots,
        "public_contracts": [
            dict(item) for item in sorted(contracts, key=lambda value: value["id"])
        ],
        "selected_files": sorted(selected) if selected is not None else None,
    }
