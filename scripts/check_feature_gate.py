"""检查单个 package-by-feature 迁移单元的 M0/M1 前置条件。

feature gate 只做静态契约检查：它不会导入生产模块、移动文件或为 AstrBot 私有
类型创建 wrapper。当前 M0 仍是 report-only；``--blocking`` 为后续 changed-files
门禁提供同一份可重复的退出码语义。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # 直接从仓库根执行脚本时，scripts 目录在 sys.path 首位。
    from architecture_analysis import analyze_repository
    from architecture_core import (
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
    from check_architecture import (
        _configuration_drift,
        _contract_transition_violations,
        evaluate_blocking,
        validate_baseline_provenance,
    )
except ImportError:  # pytest 以包模块导入时使用完整路径。
    from scripts.architecture_analysis import (
        analyze_repository,  # type: ignore[no-redef]
    )
    from scripts.architecture_core import (  # type: ignore[no-redef]
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
    from scripts.check_architecture import (
        _configuration_drift,
        _contract_transition_violations,
        evaluate_blocking,
        validate_baseline_provenance,
    )

try:
    from architecture_snapshots import (
        analysis_content_sha256,
        analysis_manifest_sha256,
        config_fingerprints,
        facts_sha256,
        git_worktree_tree,
    )
except ImportError:
    from scripts.architecture_snapshots import (
        analysis_content_sha256,
        analysis_manifest_sha256,
        config_fingerprints,  # type: ignore[no-redef]
        facts_sha256,
        git_worktree_tree,
    )


DEFAULT_CONFIG = "architecture.toml"
DEFAULT_BASELINE = "scripts/baselines/architecture.json"
ANALYSIS_TREE_EXCLUDES = (
    ".agent_artifacts",
    ".architecture-index-*",
    ".tmp-*",
    "reports",
    "scripts/baselines/architecture.json",
)
COMMON_CHANGE_ROOTS = (
    ".github/",
    "architecture.toml",
    "scripts/",
    "tests/",
    "docs/",
    "website/docs/",
    "AGENTS.md",
    "DESIGN.md",
    "README.md",
    "README_EN.md",
    "README_RU.md",
    "_conf_schema.json",
)


def _feature_requirements(config: Mapping[str, Any], feature: str) -> dict[str, Any]:
    """读取一个 feature 的 contracts/lifecycle 前置表。"""

    values = config.get("feature_requirements", {})
    if not isinstance(values, dict):
        return {}
    result = values.get(feature, {})
    return dict(result) if isinstance(result, dict) else {}


def _feature_root(config: Mapping[str, Any], feature: str) -> str:
    """解析 feature 的仓库目录并统一分隔符。"""

    values = config.get("features", {})
    if not isinstance(values, dict) or feature not in values:
        raise ArchitectureConfigError(f"未在 architecture.toml 注册 feature: {feature}")
    value = values[feature]
    if not isinstance(value, str) or not value.strip():
        raise ArchitectureConfigError(f"feature {feature} 的目录无效")
    return _normalise_rel(value).rstrip("/")


def _is_common_change(path: str) -> bool:
    """判断 changed file 是否属于不占用 feature 迁移所有权的公共文件。"""

    normalized = _normalise_rel(path)
    return any(
        (
            normalized == root.rstrip("/")
            or (root.endswith("/") and normalized.startswith(root))
        )
        for root in COMMON_CHANGE_ROOTS
    )


def _feature_name_from_path(path: str) -> str | None:
    """从 ``core/features/<name>/...`` 路径提取 feature 名。"""

    prefix = "core/features/"
    normalized = _normalise_rel(path)
    if not normalized.startswith(prefix):
        return None
    remainder = normalized[len(prefix) :]
    return remainder.split("/", 1)[0] if remainder else None


def _contract_statuses(config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """建立 C0-C6 公共契约状态索引。"""

    return {
        str(item["id"]): dict(item)
        for item in config.get("public_contracts", [])
        if isinstance(item, dict) and "id" in item
    }


def _check_contract_prerequisites(
    config: Mapping[str, Any],
    feature: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回契约前置详情和未满足项。"""

    requirements = _feature_requirements(config, feature)
    required = requirements.get("contracts", [])
    if not isinstance(required, list):
        raise ArchitectureConfigError(
            f"feature_requirements.{feature}.contracts 必须是数组"
        )
    statuses = _contract_statuses(config)
    details: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for contract_id in required:
        key = str(contract_id)
        item = statuses.get(key)
        if item is None:
            detail = {"id": key, "status": "missing", "reason": "台账缺少契约"}
            details.append(detail)
            blocked.append(detail)
            continue
        status = str(item.get("status", "blocked"))
        detail = {
            "id": key,
            "status": status,
            "stable": bool(item.get("stable", False)),
            "no_wrapper": bool(item.get("no_wrapper", False)),
            "stage": item.get("stage"),
            "evidence": item.get("evidence", ""),
        }
        details.append(detail)
        promoted = status in {"public", "verified"}
        evidence = str(item.get("evidence", "")).lower()
        evidence_ok = any(
            token in evidence for token in ("passed", "verified", "通过", "green")
        )
        evidence_blocked = any(
            token in evidence
            for token in (
                "blocked",
                "pending",
                "remaining",
                "no stable",
                "未形成",
                "等待",
            )
        )
        tests_ok = isinstance(item.get("contract_tests"), list) and bool(
            item.get("contract_tests")
        )
        if (
            not promoted
            or not item.get("stable", False)
            or item.get("no_wrapper") is not True
            or not tests_ok
            or not evidence_ok
            or evidence_blocked
        ):
            blocked.append(
                dict(detail, reason="No stable public contract；不得用 wrapper 放行")
            )
    return details, blocked


def _changed_scope(
    changed_files: Sequence[str],
    feature: str,
    feature_root: str,
) -> dict[str, Any]:
    """核对单 feature PR 的文件所有权和冻结目录。"""

    feature_files = [
        _normalise_rel(path)
        for path in changed_files
        if _normalise_rel(path) == feature_root
        or _normalise_rel(path).startswith(feature_root + "/")
    ]
    touched_features = sorted(
        {
            name
            for path in changed_files
            if (name := _feature_name_from_path(path)) is not None
        }
    )
    foreign_features = [name for name in touched_features if name != feature]
    unsupported = [
        _normalise_rel(path)
        for path in changed_files
        if not _is_common_change(path)
        and _normalise_rel(path) not in feature_files
        and _feature_name_from_path(path) != feature
    ]
    # M1/M2 与生产旧技术层目录迁移冻结；feature gate 对它们给出明确证据。
    frozen_legacy = [
        _normalise_rel(path)
        for path in changed_files
        if _normalise_rel(path).startswith("core/")
        and _feature_name_from_path(path) is None
        and not _normalise_rel(path).startswith("core/features/")
        and not _is_common_change(path)
    ]
    reasons: list[str] = []
    if foreign_features:
        reasons.append("单个迁移单元不得同时修改其他 feature")
    if unsupported:
        reasons.append("changed-files 含未声明的生产路径")
    if frozen_legacy:
        reasons.append("M1/M2 与生产旧技术层目录迁移仍冻结")
    return {
        "feature": feature,
        "feature_root": feature_root,
        "feature_files": sorted(feature_files),
        "touched_features": touched_features,
        "foreign_features": foreign_features,
        "unsupported_files": sorted(unsupported),
        "frozen_legacy_files": sorted(frozen_legacy),
        "reasons": reasons,
    }


def validate_feature_report_provenance(root: Path, report: Mapping[str, Any]) -> None:
    """重算并核对 feature 报告的 Git、内容清单与稳定事实。"""

    architecture = report.get("architecture")
    if not isinstance(architecture, dict) or not isinstance(
        architecture.get("files"), dict
    ):
        raise ArchitectureConfigError("feature.architecture.files 缺失或类型无效")
    source_commit = _git_value(root, "rev-parse", "HEAD")
    expected = {
        "source_commit": source_commit,
        "source_tree": git_object(root, f"{source_commit}^{{tree}}"),
        "analysis_tree": git_worktree_tree(root),
    }
    for field, value in expected.items():
        if report.get(field) != value:
            raise ArchitectureConfigError(f"feature.{field} 与当前工作树不一致")
    if list(report.get("analysis_tree_excludes", [])) != list(ANALYSIS_TREE_EXCLUDES):
        raise ArchitectureConfigError("feature.analysis_tree_excludes 不一致")
    files = architecture["files"]
    try:
        manifest_hash = analysis_manifest_sha256(files)
    except ValueError as exc:
        raise ArchitectureConfigError(str(exc)) from exc
    content_hash = analysis_content_sha256(root, files)
    if (
        manifest_hash != content_hash
        or report.get("analysis_content_sha256") != content_hash
    ):
        raise ArchitectureConfigError(
            "feature.analysis_content_sha256 与当前内容或文件清单不一致"
        )
    if report.get("facts_sha256") != facts_sha256(architecture):
        raise ArchitectureConfigError("feature.facts_sha256 与稳定事实内容不一致")


def check_feature(
    root: Path,
    config: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
    feature: str,
    changed_files: Sequence[str],
    *,
    blocking: bool,
    require_scc_reduction: bool = False,
) -> dict[str, Any]:
    """运行单 feature 静态 gate，返回不含机器路径的 JSON 报告。"""

    feature_root = _feature_root(config, feature)
    scope = _changed_scope(changed_files, feature, feature_root)
    architecture = analyze_repository(root, config, selected_files=changed_files)
    if architecture["violations"]["parse_errors"]:
        first = architecture["violations"]["parse_errors"][0]
        raise ArchitectureSourceError(
            f"{len(architecture['violations']['parse_errors'])} 个 Python 文件无法解析；"
            f"首项为 {first['path']}"
        )
    architecture["_configuration_drift"] = _configuration_drift(config, baseline)
    architecture["_contract_transition_violations"] = _contract_transition_violations(
        root, config, baseline
    )
    architecture_gate = evaluate_blocking(
        architecture,
        baseline,
        selected_files=changed_files,
        blocking=blocking,
        require_scc_reduction=require_scc_reduction,
        allow_baseline_update=not scope["feature_files"] and not scope["reasons"],
    )
    architecture.pop("_configuration_drift", None)
    architecture.pop("_contract_transition_violations", None)
    architecture["gate"] = architecture_gate
    contract_details, blocked_contracts = _check_contract_prerequisites(config, feature)
    requirements = _feature_requirements(config, feature)
    has_feature_change = bool(scope["feature_files"])
    gate_items: list[dict[str, Any]] = []
    gate_items.extend(
        {"kind": "scope", "reason": reason} for reason in scope["reasons"]
    )
    gate_items.extend(
        {"kind": "architecture", **item}
        for item in architecture_gate["blocking_violations"]
    )
    if has_feature_change:
        gate_items.extend({"kind": "contract", **item} for item in blocked_contracts)

    has_scope_violation = bool(scope["reasons"])
    if not has_feature_change and not has_scope_violation:
        status = "not_started"
    elif gate_items:
        status = "blocked"
    else:
        status = "ready"
    should_block = bool(
        blocking and (has_feature_change or has_scope_violation) and gate_items
    )
    source_commit = _git_value(root, "rev-parse", "HEAD")
    return {
        "schema_version": 2,
        "source_commit": source_commit,
        "source_tree": git_object(root, f"{source_commit}^{{tree}}"),
        "analysis_tree": git_worktree_tree(root),
        "analysis_tree_excludes": list(ANALYSIS_TREE_EXCLUDES),
        "analysis_content_sha256": analysis_content_sha256(root, architecture["files"]),
        "analysis_content_scope": "architecture.files path/content SHA-256 manifest",
        "facts_sha256": facts_sha256(architecture),
        "config_fingerprints": config_fingerprints(config),
        "feature": feature,
        "feature_root": feature_root,
        "mode": "blocking" if blocking else "report-only",
        "status": status,
        "changed_files": sorted(_normalise_rel(path) for path in changed_files),
        "scope": scope,
        "requirements": requirements,
        "contracts": {
            "required": contract_details,
            "blocked": blocked_contracts,
        },
        "architecture": architecture,
        "gate": {
            "blocking": should_block,
            "violations": sorted(
                gate_items,
                key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
            ),
        },
        "risks": [
            "C0-C6 仍按 No stable public contract 处理；本 gate 不移动或包装（wrapper）私有导入。",
            "生命周期与 same-data rollback harness 由独立 owner 负责，未在此 gate 重复实现。",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    """构造 feature gate 命令行解析器。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="仓库根目录")
    parser.add_argument(
        "--config", type=Path, default=None, help="architecture.toml 路径"
    )
    parser.add_argument(
        "--baseline", type=Path, default=None, help="architecture baseline JSON"
    )
    parser.add_argument("--feature", required=True, help="feature 名称")
    parser.add_argument("--mode", choices=("report", "changed"), default="report")
    parser.add_argument("--base", default=None, help="changed 模式的基准提交")
    parser.add_argument("--head", default="HEAD", help="changed 模式的目标提交")
    parser.add_argument(
        "--files", nargs="*", default=None, help="显式 changed-files（测试用）"
    )
    parser.add_argument("--report", type=Path, default=None, help="报告 JSON 输出路径")
    parser.add_argument(
        "--blocking", action="store_true", help="将前置缺口转换为非零退出"
    )
    parser.add_argument(
        "--require-scc-reduction", action="store_true", help="要求最大 SCC 严格缩小"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """执行 feature gate 并输出稳定证据产物。"""

    args = build_parser().parse_args(argv)
    root = (args.root or Path(__file__).resolve().parents[1]).resolve()
    config_path = (args.config or root / DEFAULT_CONFIG).resolve()
    baseline_path = (args.baseline or root / DEFAULT_BASELINE).resolve()
    try:
        config = load_config(config_path)
        baseline = _read_baseline(baseline_path)
        if baseline is None:
            raise ArchitectureConfigError(
                "feature gate 的 report/changed 模式都需要有效 baseline"
            )
        validate_baseline_provenance(root, baseline)
        drift = _configuration_drift(config, baseline)
        if drift:
            reasons = ", ".join(str(item.get("reason")) for item in drift)
            raise ArchitectureConfigError(
                f"配置/policy 指纹相对 baseline 漂移: {reasons}; "
                "请使用独立的 architecture --write-baseline 流程"
            )
        if args.files is not None:
            changed_files = args.files
        else:
            changed_files = (
                git_changed_files(root, args.base, args.head)
                if args.mode == "changed"
                else []
            )
        report = check_feature(
            root,
            config,
            baseline,
            args.feature,
            changed_files,
            blocking=bool(args.blocking),
            require_scc_reduction=bool(args.require_scc_reduction),
        )
        validate_feature_report_provenance(root, report)
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
            print(f"feature gate report: {label}")
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if report["gate"]["blocking"] else 0
    except (ArchitectureConfigError, ArchitectureSourceError) as exc:
        print(f"feature gate error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
