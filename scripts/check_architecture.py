"""生成 Memora 的 M0 架构基线并检查增量依赖。

该入口只读取源码、配置和 Git 元数据，不导入插件生产模块。M0 默认是
report-only；后续阶段可以通过 ``--mode changed --blocking`` 对 changed-files
启用非零门禁。所有报告使用仓库相对路径和稳定排序，便于作为可审计产物归档。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # 直接从仓库根执行脚本时，scripts 目录在 sys.path 首位。
    from architecture_analysis import analyze_repository
    from architecture_core import (
        SCHEMA_VERSION,
        ArchitectureConfigError,
        ArchitectureSourceError,
        _git_value,
        _normalise_rel,
        _read_baseline,
        _write_json,
        git_changed_files,
        git_object,
        load_config,
    )
except ImportError:  # pytest 以包模块导入时使用完整路径。
    from scripts.architecture_analysis import (
        analyze_repository,  # type: ignore[no-redef]
    )
    from scripts.architecture_core import (  # type: ignore[no-redef]
        SCHEMA_VERSION,
        ArchitectureConfigError,
        ArchitectureSourceError,
        _git_value,
        _normalise_rel,
        _read_baseline,
        _write_json,
        git_changed_files,
        git_object,
        load_config,
    )

try:
    from architecture_snapshots import (
        analysis_content_sha256,
        config_fingerprints,
        facts_sha256,
        git_worktree_tree,
    )
except ImportError:
    from scripts.architecture_snapshots import (  # type: ignore[no-redef]
        analysis_content_sha256,
        config_fingerprints,
        facts_sha256,
        git_worktree_tree,
    )


DEFAULT_CONFIG = "architecture.toml"
DEFAULT_BASELINE = "scripts/baselines/architecture.json"
_PROMOTED_CONTRACT_STATUSES = {"public", "verified"}
_CONTRACT_BLOCKER_WORDS = (
    "blocked",
    "pending",
    "remaining",
    "no stable",
    "未形成",
    "等待",
    "未确认",
    "不支持",
    "缺少",
    "不得",
)


def validate_baseline_provenance(
    root: Path,
    baseline: Mapping[str, Any],
) -> None:
    """核对 baseline 声明的 source commit 与 source tree 一致。"""

    source_commit = str(baseline.get("source_commit", ""))
    source_tree = str(baseline.get("source_tree", ""))
    actual_tree = git_object(root, f"{source_commit}^{{tree}}")
    if actual_tree != source_tree:
        raise ArchitectureConfigError(
            "baseline.source_tree 与 source_commit 对应 tree 不一致"
        )


def _configuration_drift(
    config: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """返回相对 baseline 的配置、策略与契约指纹漂移。"""

    if baseline is None:
        return [{"reason": "baseline_missing", "scope": "fingerprints"}]
    current = config_fingerprints(config)
    drift: list[dict[str, Any]] = []
    for field, reason in (
        ("architecture_config_sha256", "architecture_config_drift"),
        ("policy_fingerprint", "policy_fingerprint_drift"),
        ("contract_fingerprint", "contract_fingerprint_drift"),
    ):
        expected = baseline.get(field)
        if not isinstance(expected, str) or expected != current[field]:
            drift.append(
                {
                    "field": field,
                    "baseline": expected,
                    "current": current[field],
                    "reason": reason,
                }
            )
    return drift


def _contract_test_path(root: Path, value: str) -> tuple[Path, str]:
    """解析具体 contract test 路径，并拒绝越界路径与 glob。"""

    path_text = _normalise_rel(value.split("::", 1)[0])
    if not path_text.endswith(".py") or any(
        token in path_text for token in ("*", "?", "[")
    ):
        raise ValueError("contract test 必须是具体 Python 路径")
    path = (root / path_text).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("contract test 路径越界或不存在")
    return path, path_text


def _contract_transition_violations(
    root: Path,
    config: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """校验 C0-C6 状态单调性、测试执行结果与 evidence 一致性。"""

    violations = [
        item
        for item in _configuration_drift(config, baseline)
        if item.get("reason") in {"contract_fingerprint_drift", "baseline_missing"}
    ]
    old_items = {
        str(item.get("id")): item
        for item in (baseline or {}).get("public_contracts", [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    current_items = {
        str(item.get("id")): item
        for item in config.get("public_contracts", [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    for contract_id in sorted(set(old_items) | set(current_items)):
        current = current_items.get(contract_id)
        previous = old_items.get(contract_id)
        if current is None:
            violations.append(
                {"id": contract_id, "reason": "contract_ledger_shape_drift"}
            )
            continue
        if previous is None and baseline is not None:
            violations.append(
                {"id": contract_id, "reason": "contract_ledger_shape_drift"}
            )
        previous_blocked = (
            previous is not None
            and previous.get("status") == "blocked"
            and previous.get("stable") is False
            and previous.get("no_wrapper") is True
        )
        relaxed = previous_blocked and (
            current.get("status") != "blocked"
            or current.get("stable") is not False
            or current.get("no_wrapper") is not True
        )
        if relaxed:
            violations.append(
                {
                    "id": contract_id,
                    "previous": {
                        "status": previous.get("status"),
                        "stable": previous.get("stable"),
                        "no_wrapper": previous.get("no_wrapper"),
                    },
                    "current": {
                        "status": current.get("status"),
                        "stable": current.get("stable"),
                        "no_wrapper": current.get("no_wrapper"),
                    },
                    "reason": "contract_relaxation",
                }
            )
        promoted = (
            bool(current.get("stable"))
            or current.get("status") in _PROMOTED_CONTRACT_STATUSES
        )
        if not promoted:
            continue
        if (
            current.get("status") not in _PROMOTED_CONTRACT_STATUSES
            or current.get("stable") is not True
        ):
            violations.append(
                {"id": contract_id, "reason": "contract_stability_required"}
            )
        if current.get("no_wrapper") is not True:
            violations.append(
                {"id": contract_id, "reason": "contract_no_wrapper_required"}
            )
        tests = current.get("contract_tests")
        runnable_tests: list[str] = []
        if not isinstance(tests, list) or not tests:
            violations.append({"id": contract_id, "reason": "contract_tests_required"})
        else:
            for test_path in tests:
                try:
                    path, normalized = _contract_test_path(root, str(test_path))
                    tree = ast.parse(
                        path.read_text(encoding="utf-8"), filename=normalized
                    )
                except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
                    violations.append(
                        {
                            "id": contract_id,
                            "path": str(test_path),
                            "error": str(exc),
                            "reason": "contract_tests_invalid",
                        }
                    )
                    continue
                has_test = any(
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("test_")
                    for node in ast.walk(tree)
                )
                if not has_test:
                    violations.append(
                        {
                            "id": contract_id,
                            "path": normalized,
                            "reason": "contract_tests_not_collectable",
                        }
                    )
                else:
                    runnable_tests.append(str(test_path))
        if isinstance(tests, list) and tests and len(runnable_tests) == len(tests):
            try:
                completed = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", *runnable_tests],
                    cwd=root,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                violations.append(
                    {
                        "id": contract_id,
                        "error": type(exc).__name__,
                        "reason": "contract_tests_execution_error",
                    }
                )
            else:
                if completed.returncode != 0:
                    violations.append(
                        {
                            "id": contract_id,
                            "exit_code": completed.returncode,
                            "reason": "contract_tests_failed",
                        }
                    )
        evidence = str(current.get("evidence", ""))
        lowered = evidence.lower()
        if not any(
            token in lowered or token in evidence
            for token in ("passed", "verified", "通过", "green")
        ):
            violations.append(
                {"id": contract_id, "reason": "contract_evidence_missing_pass"}
            )
        if any(
            token in lowered or token in evidence.lower()
            for token in _CONTRACT_BLOCKER_WORDS
        ):
            violations.append(
                {"id": contract_id, "reason": "contract_evidence_blocker"}
            )
    return violations


def _changed_set(selected_files: Sequence[str] | None) -> set[str] | None:
    """规范化 changed-files；None 表示全量报告。"""

    if selected_files is None:
        return None
    return {_normalise_rel(path) for path in selected_files}


def _signature(item: Mapping[str, Any]) -> tuple[Any, ...]:
    """生成违规项的稳定签名，供 baseline-relative 比较。"""

    return tuple(
        item.get(key)
        for key in ("path", "source", "target", "symbol", "name", "reason", "kind")
    )


def _baseline_violation_keys(
    baseline: Mapping[str, Any],
    name: str,
) -> set[tuple[Any, ...]]:
    """读取旧 baseline 中指定违规集合的签名。"""

    violations = baseline.get("violations", {})
    values = violations.get(name, []) if isinstance(violations, dict) else []
    return {_signature(item) for item in values if isinstance(item, dict)}


def _is_selected(item: Mapping[str, Any], selected: set[str] | None) -> bool:
    """判断违规项是否由本轮 changed file 触发。"""

    if selected is None:
        return True
    path = item.get("path") or item.get("source")
    if not isinstance(path, str):
        return True
    source_path = path.replace(".", "/") + ".py" if "/" not in path else path
    return path in selected or source_path in selected


def evaluate_blocking(
    report: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
    *,
    selected_files: Sequence[str] | None,
    blocking: bool,
    require_scc_reduction: bool = False,
    allow_baseline_update: bool = False,
) -> dict[str, Any]:
    """依据 baseline 和 changed-files 计算实际门禁结果。"""

    selected = _changed_set(selected_files)
    violations = report["violations"]
    blocking_items: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    baseline_files = baseline.get("files", {}) if isinstance(baseline, dict) else {}

    def add(item: Mapping[str, Any], reason: str, *, hard: bool = True) -> None:
        """按当前 blocking 模式收集失败或报告项。"""

        value = dict(item, reason=reason)
        if blocking and hard:
            blocking_items.append(value)
        else:
            warnings.append(value)

    for item in violations.get("parse_errors", []):
        if _is_selected(item, selected):
            add(item, "parse_error")

    for item in violations.get("forbidden_wrappers", []):
        if _is_selected(item, selected):
            add(item, "forbidden_wrapper")

    for item in violations.get("line_budget", []):
        if not _is_selected(item, selected):
            continue
        if item.get("kind") == "soft":
            add(item, "soft_budget", hard=False)
            continue
        path = str(item["path"])
        baseline_record = (
            baseline_files.get(path) if isinstance(baseline_files, dict) else None
        )
        exception = next(
            (
                value
                for value in report.get("_config_exceptions", [])
                if isinstance(value, dict) and value.get("path") == path
            ),
            None,
        )
        if exception and int(item["lines"]) <= int(exception.get("max_lines", 0)):
            add(item, "declared_exception", hard=False)
        elif isinstance(baseline_record, dict) and int(item["lines"]) <= int(
            baseline_record.get("lines", 0)
        ):
            add(item, "historical_only_decrease", hard=False)
        else:
            add(item, "hard_budget")

    baseline_private = _baseline_violation_keys(baseline or {}, "forbidden_imports")
    baseline_edges = _baseline_violation_keys(baseline or {}, "forbidden_edges")
    for name, values, old_keys in (
        (
            "forbidden_imports",
            violations.get("forbidden_imports", []),
            baseline_private,
        ),
        ("forbidden_edges", violations.get("forbidden_edges", []), baseline_edges),
    ):
        for item in values:
            if not _is_selected(item, selected):
                continue
            if _signature(item) in old_keys:
                add(item, "baseline_existing", hard=False)
            else:
                add(item, f"new_{name}")

    for item in report.get("_configuration_drift", []):
        if isinstance(item, dict):
            add(item, str(item.get("reason", "configuration_drift")))
    for item in report.get("_contract_transition_violations", []):
        if isinstance(item, dict):
            add(item, str(item.get("reason", "contract_state_drift")))

    selected_policy_files = {
        "architecture.toml",
        "scripts/baselines/architecture.json",
    }
    if (
        not allow_baseline_update
        and selected is not None
        and selected & selected_policy_files
    ):
        add(
            {"files": sorted(selected & selected_policy_files)},
            "policy_or_baseline_changed",
        )

    # 方法、公开符号和 fan-out 是 M0 信号；changed 模式只报告，不误当硬阻断。
    for name in ("method_length", "public_symbols", "import_fanout"):
        for item in violations.get(name, []):
            if _is_selected(item, selected):
                add(item, f"signal_{name}", hard=False)

    current_edges = {
        (item["source"], item["target"])
        for item in report["dependency_graph"].get("edges", [])
    }
    old_edges = {
        (item["source"], item["target"])
        for item in (baseline or {}).get("dependency_graph", {}).get("edges", [])
        if isinstance(item, dict)
    }
    new_edges = sorted(current_edges - old_edges)
    if selected is None:
        new_edges = []
    else:
        selected_modules = {
            path[:-3].replace("/", ".").removesuffix(".__init__")
            for path in selected
            if path.endswith(".py")
        }
        new_edges = [
            edge
            for edge in new_edges
            if any(
                module == edge[0] or module.startswith(edge[0] + ".")
                for module in selected_modules
            )
        ]
    if new_edges:
        add(
            {
                "edges": [
                    {"source": source, "target": target} for source, target in new_edges
                ]
            },
            "new_dependency_edges",
        )

    old_largest = int(
        (baseline or {}).get("dependency_graph", {}).get("largest_scc", 1)
    )
    current_largest = int(report["dependency_graph"].get("largest_scc", 1))
    if selected is not None and current_largest > old_largest:
        add({"baseline": old_largest, "current": current_largest}, "scc_growth")
    if require_scc_reduction and current_largest >= old_largest:
        add({"baseline": old_largest, "current": current_largest}, "scc_not_reduced")

    for exception in report.get("_config_exceptions", []):
        if not isinstance(exception, dict):
            add({"exception": exception}, "invalid_architecture_exception")
            continue
        required = {"path", "owner", "reason", "max_lines", "issue", "expires"}
        missing = sorted(required - set(exception))
        if "*" in str(exception.get("path", "")) or missing:
            add(
                {"path": exception.get("path"), "missing": missing},
                "invalid_architecture_exception",
            )

    return {
        "blocking": bool(blocking and blocking_items),
        "blocking_violations": sorted(
            blocking_items,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        ),
        "warnings": sorted(
            warnings,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        ),
        "new_dependency_edges": [
            {"source": source, "target": target} for source, target in new_edges
        ],
    }


def _with_runtime_metadata(
    report: dict[str, Any],
    root: Path,
    config: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
    *,
    selected_files: Sequence[str] | None,
    blocking: bool,
    require_scc_reduction: bool,
    allow_baseline_update: bool = False,
) -> dict[str, Any]:
    """补充提交、策略和门禁结果，并移除内部配置字段。"""

    report["source_commit"] = _git_value(root, "rev-parse", "HEAD")
    report["source_tree"] = git_object(root, f"{report['source_commit']}^{{tree}}")
    report["analysis_tree"] = git_worktree_tree(root)
    report["analysis_tree_excludes"] = [
        ".agent_artifacts",
        ".architecture-index-*",
        ".tmp-*",
        "reports",
        "scripts/baselines/architecture.json",
    ]
    report["analysis_content_sha256"] = analysis_content_sha256(
        root, report.get("files", {})
    )
    report["analysis_content_scope"] = "report.files path/content SHA-256 manifest"
    report["statistics_method"] = {
        "line_count": "UTF-8 physical lines via str.splitlines()",
        "imports": "Python 3.12 ast.Import/ast.ImportFrom",
        "scc": "deterministic Tarjan over package graph",
        "paths": "repository-relative POSIX paths",
    }
    report.update(config_fingerprints(config))
    report["mode"] = "changed" if selected_files is not None else "report"
    report["policy"] = {
        "decision": "blocking" if blocking else "report-only",
        "baseline_policy": config["project"].get("baseline_policy", "only_decrease"),
        "candidate_thresholds_effective": bool(
            config["policy"].get("candidate_thresholds_effective", False)
        ),
    }
    report["public_contract_summary"] = {
        "ids": [item["id"] for item in report["public_contracts"]],
        "blocked": [
            item["id"]
            for item in report["public_contracts"]
            if item["status"] == "blocked"
        ],
        "stable": [
            item["id"] for item in report["public_contracts"] if item.get("stable")
        ],
        "no_wrapper_required": [
            item["id"] for item in report["public_contracts"] if item.get("no_wrapper")
        ],
    }
    report["_config_exceptions"] = list(config.get("exceptions", []))
    report["_configuration_drift"] = _configuration_drift(config, baseline)
    report["_contract_transition_violations"] = _contract_transition_violations(
        root, config, baseline
    )
    report["facts_sha256"] = facts_sha256(report)
    report["gate"] = evaluate_blocking(
        report,
        baseline,
        selected_files=selected_files,
        blocking=blocking,
        require_scc_reduction=require_scc_reduction,
        allow_baseline_update=allow_baseline_update,
    )
    report.pop("_config_exceptions", None)
    report.pop("_configuration_drift", None)
    report.pop("_contract_transition_violations", None)
    return report


def _baseline_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    """从报告中提取不含运行模式的可版本化 baseline。"""

    payload = {
        "schema_version": SCHEMA_VERSION,
        "baseline_update_flow": "explicit --write-baseline",
        "source_commit": report.get("source_commit"),
        "source_tree": report.get("source_tree"),
        "analysis_tree": report.get("analysis_tree"),
        "analysis_tree_excludes": report.get("analysis_tree_excludes"),
        "analysis_content_sha256": report.get("analysis_content_sha256"),
        "analysis_content_scope": report.get("analysis_content_scope"),
        "statistics_method": report.get("statistics_method", {}),
        "architecture_config_sha256": report.get("architecture_config_sha256"),
        "policy_fingerprint": report.get("policy_fingerprint"),
        "contract_fingerprint": report.get("contract_fingerprint"),
        "files": report.get("files", {}),
        "summary": report.get("summary", {}),
        "imports": {
            "private": report.get("imports", {}).get("private", []),
            "public": report.get("imports", {}).get("public", []),
            "summary": report.get("imports", {}).get("summary", {}),
        },
        "dependency_graph": report.get("dependency_graph", {}),
        "violations": report.get("violations", {}),
        "snapshots": report.get("snapshots", {}),
        "public_contracts": report.get("public_contracts", []),
    }
    payload["facts_sha256"] = facts_sha256(payload)
    payload["baseline_integrity_sha256"] = hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return payload


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="仓库根目录")
    parser.add_argument(
        "--config", type=Path, default=None, help="architecture.toml 路径"
    )
    parser.add_argument(
        "--baseline", type=Path, default=None, help="baseline JSON 路径"
    )
    parser.add_argument("--report", type=Path, default=None, help="报告 JSON 输出路径")
    parser.add_argument("--mode", choices=("report", "changed"), default="report")
    parser.add_argument("--base", default=None, help="changed 模式的基准提交")
    parser.add_argument("--head", default="HEAD", help="changed 模式的目标提交")
    parser.add_argument(
        "--files", nargs="*", default=None, help="显式 changed-files（测试用）"
    )
    parser.add_argument(
        "--blocking", action="store_true", help="将新增违规转换为非零退出"
    )
    parser.add_argument(
        "--require-scc-reduction", action="store_true", help="要求最大 SCC 严格缩小"
    )
    parser.add_argument(
        "--write-baseline",
        "--record-baseline",
        dest="write_baseline",
        action="store_true",
        help="用当前全量 report 更新 baseline（显式操作）",
    )
    parser.add_argument(
        "--policy-update",
        action="store_true",
        help="显式复验已由 --write-baseline 生成的 policy/baseline 更新",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """执行架构报告或 changed-files 门禁。"""

    args = build_parser().parse_args(argv)
    root = (args.root or Path(__file__).resolve().parents[1]).resolve()
    config_path = (args.config or root / DEFAULT_CONFIG).resolve()
    baseline_path = (args.baseline or root / DEFAULT_BASELINE).resolve()
    try:
        if args.write_baseline and (
            args.mode != "report"
            or args.files is not None
            or args.blocking
            or args.require_scc_reduction
        ):
            raise ArchitectureConfigError(
                "--write-baseline 只能作为独立的全量 report 非阻断流程执行"
            )
        if args.write_baseline and not baseline_path.is_relative_to(root):
            raise ArchitectureConfigError("baseline 更新路径必须位于仓库内")
        if args.policy_update and (
            args.write_baseline or args.mode != "changed" or not args.blocking
        ):
            raise ArchitectureConfigError(
                "--policy-update 仅用于独立的 changed --blocking 复验"
            )
        config = load_config(config_path)
        if args.write_baseline:
            promotion_errors = [
                item
                for item in _contract_transition_violations(root, config, None)
                if item.get("reason") != "baseline_missing"
            ]
            if promotion_errors:
                reasons = ", ".join(
                    str(item.get("reason")) for item in promotion_errors
                )
                raise ArchitectureConfigError(
                    f"baseline 更新前公共契约证据未闭合: {reasons}"
                )
        try:
            baseline = _read_baseline(baseline_path)
        except ArchitectureConfigError:
            if not args.write_baseline:
                raise
            baseline = None
        if baseline is None and not args.write_baseline:
            raise ArchitectureConfigError(
                "architecture gate 的 report/changed 模式都需要有效 baseline；"
                "仅 --write-baseline 可显式创建"
            )
        if baseline is not None and not args.write_baseline:
            validate_baseline_provenance(root, baseline)
            drift = _configuration_drift(config, baseline)
            if drift:
                reasons = ", ".join(str(item.get("reason")) for item in drift)
                raise ArchitectureConfigError(
                    f"配置/policy 指纹相对 baseline 漂移: {reasons}; "
                    "请使用独立的 --write-baseline 流程"
                )
        if args.files is not None:
            selected_files: list[str] | None = args.files
        elif args.mode == "changed":
            selected_files = git_changed_files(root, args.base, args.head)
        else:
            selected_files = None
        if args.policy_update:
            selected = {_normalise_rel(path) for path in selected_files or []}
            if not {DEFAULT_CONFIG, DEFAULT_BASELINE} <= selected:
                raise ArchitectureConfigError(
                    "--policy-update 必须同时显式包含 architecture.toml 与 baseline"
                )
        report = analyze_repository(root, config, selected_files=selected_files)
        if report["violations"]["parse_errors"]:
            first = report["violations"]["parse_errors"][0]
            raise ArchitectureSourceError(
                f"{len(report['violations']['parse_errors'])} 个 Python 文件无法解析；"
                f"首项为 {first['path']}"
            )
        report = _with_runtime_metadata(
            report,
            root,
            config,
            baseline,
            selected_files=selected_files,
            blocking=bool(args.blocking),
            require_scc_reduction=bool(args.require_scc_reduction),
            allow_baseline_update=bool(args.write_baseline or args.policy_update),
        )
        if args.write_baseline:
            if selected_files is not None:
                raise ArchitectureConfigError("更新 baseline 必须使用全量 report 模式")
            _write_json(baseline_path, _baseline_payload(report))
            report["baseline_written"] = _normalise_rel(baseline_path.relative_to(root))
        if args.report:
            report_path = (
                args.report if args.report.is_absolute() else root / args.report
            )
            _write_json(report_path, report)
            label = (
                _normalise_rel(report_path.relative_to(root))
                if report_path.is_relative_to(root)
                else str(report_path)
            )
            print(f"architecture report: {label}")
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if report["gate"]["blocking"] else 0
    except (ArchitectureConfigError, ArchitectureSourceError) as exc:
        print(f"architecture gate error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
