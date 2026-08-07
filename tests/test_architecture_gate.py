"""M0 架构报告、baseline-relative 门禁与公共契约台账测试。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

import scripts.architecture_core as architecture_core
import scripts.check_architecture as check_architecture
from scripts.architecture_analysis import analyze_repository
from scripts.architecture_core import (
    ArchitectureConfigError,
    ArchitectureSourceError,
    _normalise_rel,
    _read_baseline,
    git_changed_files,
    load_config,
)
from scripts.check_architecture import (
    _configuration_drift,
    _contract_transition_violations,
    build_parser,
    evaluate_blocking,
    git_worktree_tree,
)
from scripts.check_architecture import (
    main as architecture_main,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "architecture.toml"
BASELINE_PATH = REPO_ROOT / "scripts" / "baselines" / "architecture.json"


@pytest.fixture(scope="module")
def architecture_report() -> dict[str, Any]:
    """只扫描一次真实 checkout，供基线契约测试复用。"""

    config = load_config(CONFIG_PATH)
    return analyze_repository(REPO_ROOT, config)


def test_report_reproduces_accepted_m0_baseline(
    architecture_report: dict[str, Any],
) -> None:
    """fresh 分析相对 checked-in baseline 只允许合法 only-decrease / SCC reduction。"""

    baseline = _read_baseline(BASELINE_PATH)
    assert baseline is not None
    report = architecture_report
    assert report["violations"]["parse_errors"] == []

    baseline_files = baseline["files"]
    grown = sorted(
        path
        for path, record in report["files"].items()
        if path in baseline_files
        and record["lines"] > int(baseline_files[path]["lines"])
    )
    assert grown == [], f"相对 baseline 增长的文件需 --write-baseline: {grown}"
    assert report["dependency_graph"]["largest_scc"] <= int(
        baseline["dependency_graph"]["largest_scc"]
    )

    baseline_private = {
        check_architecture._signature(item)
        for item in baseline["violations"]["forbidden_imports"]
    }
    fresh_private = {
        check_architecture._signature(item)
        for item in report["violations"]["forbidden_imports"]
    }
    assert fresh_private <= baseline_private

    gate = evaluate_blocking(
        report,
        baseline,
        selected_files=None,
        blocking=True,
    )
    assert gate["blocking"] is False


def test_page_route_snapshot_freezes_primary_and_alias_contracts(
    architecture_report: dict[str, Any],
) -> None:
    """Page 快照必须覆盖主前缀/别名前缀及关键风险元数据。"""

    routes = {
        (item["path"], tuple(item["methods"])): item
        for item in architecture_report["snapshots"]["routes"]
    }
    assert len(routes) == 282
    for prefix, aliases in (
        ("/astrbot_plugin_memora/page", False),
        ("/Memora/page", True),
    ):
        apply_route = routes[(f"{prefix}/update/apply", ("POST",))]
        assert apply_route["auth"] == "admin"
        assert apply_route["risk"] == "maintenance"
        assert apply_route["write_guard"] is True
        assert apply_route["aliases"] is aliases

        status_route = routes[(f"{prefix}/update/status", ("GET",))]
        assert status_route["auth"] == "host"
        assert status_route["risk"] == "read"
        assert status_route["requires_ready"] is False


def test_c0_c6_are_ledger_only_blockers(
    architecture_report: dict[str, Any],
) -> None:
    """C0-C6 未有完整公共契约时必须保持 blocker 且禁止 wrapper。"""

    contracts = architecture_report["public_contracts"]
    assert [item["id"] for item in contracts] == [f"C{index}" for index in range(7)]
    assert all(item["status"] == "blocked" for item in contracts)
    assert all(item["stable"] is False for item in contracts)
    assert all(item["no_wrapper"] is True for item in contracts)
    assert all(item["versions"] == ["4.24.2", "4.26.7", "4.27.1"] for item in contracts)
    assert not any(
        path.startswith("core/platform/astrbot")
        for item in contracts
        for path in item["current_imports"]
    )


def test_checked_in_baseline_matches_fresh_contract_snapshots(
    architecture_report: dict[str, Any],
) -> None:
    """受版本控制 baseline 的非波动契约事实必须匹配 fresh report。"""

    baseline = _read_baseline(BASELINE_PATH)
    assert baseline is not None
    assert baseline["public_contracts"] == architecture_report["public_contracts"]
    assert baseline["snapshots"] == architecture_report["snapshots"]
    assert (
        baseline["imports"]["summary"]["private_unmapped_records"]
        == architecture_report["imports"]["summary"]["private_unmapped_records"]
    )

    baseline_files = baseline["files"]
    grown = sorted(
        path
        for path, record in architecture_report["files"].items()
        if path in baseline_files
        and record["lines"] > int(baseline_files[path]["lines"])
    )
    assert grown == [], f"相对 baseline 增长的文件需 --write-baseline: {grown}"


def test_changed_gate_blocks_new_private_import_and_legacy_feature_edge(
    tmp_path: Path,
) -> None:
    """新 feature 引入私有 AstrBot API 或旧技术层依赖必须非零阻断。"""

    (tmp_path / "core" / "features" / "jargon").mkdir(parents=True)
    (tmp_path / "core" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "core" / "api.py").write_text("VALUE = 1\n", encoding="utf-8")
    target = tmp_path / "core" / "features" / "jargon" / "domain.py"
    target.write_text(
        "from astrbot.core.agent.tool import FunctionTool\nfrom core import api\n",
        encoding="utf-8",
    )
    config = load_config(CONFIG_PATH)
    changed = ["core/features/jargon/domain.py"]
    report = analyze_repository(tmp_path, config, selected_files=changed)
    gate = evaluate_blocking(
        report,
        None,
        selected_files=changed,
        blocking=True,
    )

    reasons = {item["reason"] for item in gate["blocking_violations"]}
    assert "new_forbidden_imports" in reasons
    assert "new_forbidden_edges" in reasons
    assert gate["blocking"] is True


def test_domain_layer_cannot_import_feature_infrastructure(tmp_path: Path) -> None:
    """复杂 feature 的 domain 只能依赖 domain/contracts，不得反向依赖 infrastructure。"""

    domain = tmp_path / "core" / "features" / "jargon" / "domain"
    infrastructure = tmp_path / "core" / "features" / "jargon" / "infrastructure"
    domain.mkdir(parents=True)
    infrastructure.mkdir(parents=True)
    for package in (
        tmp_path / "core",
        tmp_path / "core" / "features",
        tmp_path / "core" / "features" / "jargon",
        domain,
        infrastructure,
    ):
        (package / "__init__.py").write_text("", encoding="utf-8")
    (infrastructure / "store.py").write_text("VALUE = 1\n", encoding="utf-8")
    source = domain / "service.py"
    source.write_text(
        "from core.features.jargon.infrastructure import store\n",
        encoding="utf-8",
    )

    report = analyze_repository(tmp_path, load_config(CONFIG_PATH))
    assert any(
        item["reason"] == "feature 四层依赖方向违规"
        and item["source"] == "core.features.jargon.domain.service"
        for item in report["violations"]["forbidden_edges"]
    )


def _line_report(lines: int) -> dict[str, Any]:
    """构造只包含历史超限文件的最小门禁报告。"""

    return {
        "files": {"core/legacy.py": {"category": "production", "lines": lines}},
        "violations": {
            "line_budget": [
                {
                    "path": "core/legacy.py",
                    "category": "production",
                    "lines": lines,
                    "soft": 450,
                    "hard": 700,
                    "legacy_hard": 800,
                    "kind": "hard",
                }
            ],
            "parse_errors": [],
            "forbidden_imports": [],
            "forbidden_edges": [],
            "method_length": [],
            "public_symbols": [],
            "import_fanout": [],
        },
        "dependency_graph": {"edges": [], "largest_scc": 1},
        "_config_exceptions": [],
    }


def test_historical_over_limit_file_is_only_decrease() -> None:
    """历史超硬上限文件缩短可通过，增长一行必须阻断。"""

    baseline = {
        "files": {"core/legacy.py": {"category": "production", "lines": 800}},
        "violations": {"forbidden_imports": [], "forbidden_edges": []},
        "dependency_graph": {"edges": [], "largest_scc": 1},
    }
    decreased = evaluate_blocking(
        _line_report(799),
        baseline,
        selected_files=["core/legacy.py"],
        blocking=True,
    )
    increased = evaluate_blocking(
        _line_report(801),
        baseline,
        selected_files=["core/legacy.py"],
        blocking=True,
    )
    assert decreased["blocking"] is False
    assert any(
        item["reason"] == "historical_only_decrease" for item in decreased["warnings"]
    )
    assert increased["blocking"] is True
    assert any(
        item["reason"] == "hard_budget" for item in increased["blocking_violations"]
    )


def _scc_report(largest_scc: int, sccs: list[list[str]]) -> dict[str, Any]:
    """构造只含依赖图 SCC 形状的最小门禁报告。"""

    return {
        "files": {},
        "violations": {
            "line_budget": [],
            "parse_errors": [],
            "forbidden_imports": [],
            "forbidden_edges": [],
            "method_length": [],
            "public_symbols": [],
            "import_fanout": [],
        },
        "dependency_graph": {"edges": [], "largest_scc": largest_scc, "sccs": sccs},
        "_config_exceptions": [],
    }


def test_scc_reduction_contract_matches_cli_semantics() -> None:
    """最大 SCC 严格缩小即合法；SCC 数量拆分不构成回归，未缩小/增长必须阻断。"""

    baseline = {
        "files": {},
        "violations": {"forbidden_imports": [], "forbidden_edges": []},
        "dependency_graph": {
            "edges": [],
            "largest_scc": 16,
            "sccs": [[f"core.m{i}" for i in range(16)]],
        },
    }
    changed = ["core/legacy.py"]

    split = evaluate_blocking(
        _scc_report(
            9,
            [[f"core.m{i}" for i in range(9)], ["core.extra", "core.extra2"]],
        ),
        baseline,
        selected_files=changed,
        blocking=True,
        require_scc_reduction=True,
    )
    assert split["blocking"] is False
    assert not any(
        item["reason"] in {"scc_growth", "scc_not_reduced"}
        for item in split["blocking_violations"]
    )

    stagnant = evaluate_blocking(
        _scc_report(16, [[f"core.m{i}" for i in range(16)]]),
        baseline,
        selected_files=changed,
        blocking=True,
        require_scc_reduction=True,
    )
    assert stagnant["blocking"] is True
    assert any(
        item["reason"] == "scc_not_reduced" for item in stagnant["blocking_violations"]
    )

    grown = evaluate_blocking(
        _scc_report(17, [[f"core.m{i}" for i in range(17)]]),
        baseline,
        selected_files=changed,
        blocking=True,
        require_scc_reduction=True,
    )
    assert grown["blocking"] is True
    reasons = {item["reason"] for item in grown["blocking_violations"]}
    assert "scc_growth" in reasons
    assert "scc_not_reduced" in reasons


def test_baseline_contains_no_absolute_paths_or_sensitive_values() -> None:
    """baseline 只允许仓库相对路径和脱敏结构摘要。"""

    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert str(REPO_ROOT) not in serialized
    for marker in (
        "secret-value-canary",
        "authorization: bearer",
        "sqlite:///",
        "api-key-",
    ):
        assert marker not in serialized.lower()


def test_pending_candidate_thresholds_keep_legacy_hard_limit(tmp_path: Path) -> None:
    """未决阈值不能把现行 800 行规则静默放宽。"""

    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    target = tests_root / "new.py"
    target.write_text("VALUE = 1\n" * 801, encoding="utf-8")
    report = analyze_repository(tmp_path, load_config(CONFIG_PATH))
    violation = next(
        item
        for item in report["violations"]["line_budget"]
        if item["path"] == "tests/new.py"
    )
    assert violation["candidate_kind"] == "soft"
    assert violation["effective_hard"] == 800
    assert violation["kind"] == "hard"

    gate = evaluate_blocking(
        report,
        None,
        selected_files=["tests/new.py"],
        blocking=True,
    )
    assert gate["blocking"] is True
    assert gate["blocking_violations"][0]["reason"] == "hard_budget"


def test_baseline_requires_gate_structures(tmp_path: Path) -> None:
    """损坏的 baseline 必须变成可定位的配置错误。"""

    path = tmp_path / "baseline.json"
    path.write_text('{"schema_version": 1}', encoding="utf-8")
    with pytest.raises(ArchitectureConfigError, match="baseline.files"):
        _read_baseline(path)


def test_git_changed_files_failure_is_not_silently_ignored(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Git 失败不能伪装成空 changed set。"""

    def failed_run(*args: Any, **kwargs: Any) -> Any:
        """模拟 Git 子命令返回失败。"""

        del args, kwargs
        return type("Completed", (), {"returncode": 128, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(architecture_core.subprocess, "run", failed_run)
    with pytest.raises(ArchitectureSourceError, match="Git changed-files"):
        git_changed_files(tmp_path, None)


@pytest.mark.parametrize("value", ("/outside/file.py", "../outside.py"))
def test_repository_paths_reject_escape(value: str) -> None:
    """架构配置路径必须留在仓库内。"""

    with pytest.raises(ArchitectureConfigError):
        architecture_core._validate_repo_path(value, field="test.path")
    assert _normalise_rel("./core/example.py") == "core/example.py"


def test_policy_fingerprint_drift_is_rejected_even_in_report_mode(
    tmp_path: Path,
) -> None:
    """阈值篡改不能把阻断报告改成绿色 report。"""

    config = copy.deepcopy(load_config(CONFIG_PATH))
    config["thresholds"]["production"]["legacy_hard"] = 10_000
    drift = _configuration_drift(config, _read_baseline(BASELINE_PATH))
    assert any(item["reason"] == "policy_fingerprint_drift" for item in drift)

    tampered = tmp_path / "architecture.toml"
    text = CONFIG_PATH.read_text(encoding="utf-8")
    tampered.write_text(
        text.replace("legacy_hard = 800", "legacy_hard = 10000", 1), encoding="utf-8"
    )
    assert (
        architecture_main(
            [
                "--root",
                str(REPO_ROOT),
                "--config",
                str(tampered),
                "--baseline",
                str(BASELINE_PATH),
                "--mode",
                "report",
            ]
        )
        == 2
    )


def test_contract_relaxation_requires_tests_and_consistent_evidence() -> None:
    """C0-C6 状态提升不能仅靠修改 TOML 自行授权。"""

    config = copy.deepcopy(load_config(CONFIG_PATH))
    config["public_contracts"][1].update(
        status="verified",
        stable=True,
        no_wrapper=False,
        contract_tests=[],
        evidence="verified",
    )
    baseline = _read_baseline(BASELINE_PATH)
    violations = _contract_transition_violations(REPO_ROOT, config, baseline)
    reasons = {item["reason"] for item in violations}
    assert "contract_fingerprint_drift" in reasons
    assert "contract_relaxation" in reasons
    assert "contract_tests_required" in reasons
    assert "contract_no_wrapper_required" in reasons


def test_forbidden_wrapper_filename_is_an_ast_violation(tmp_path: Path) -> None:
    """配置禁止的兼容 wrapper 必须成为可执行 AST 违规。"""

    wrapper = tmp_path / "core" / "features" / "jargon" / "compat.py"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("VALUE = 1\n", encoding="utf-8")
    report = analyze_repository(tmp_path, load_config(CONFIG_PATH))
    assert report["violations"]["forbidden_wrappers"] == [
        {
            "path": "core/features/jargon/compat.py",
            "name": "compat.py",
            "reason": "禁止的 wrapper 文件名",
        }
    ]


def test_private_import_variants_are_detected_only_in_architecture_files(
    tmp_path: Path,
) -> None:
    """from-import 与动态导入变体必须共用私有导入门禁。"""

    source = tmp_path / "core" / "variant.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from astrbot import core\n"
        "import importlib\n"
        "importlib.import_module('astrbot.core.agent.tool')\n"
        "importlib.import_module(name='astrbot.core.keyword_only')\n"
        "from importlib import import_module as load_module\n"
        "load_module(name='astrbot.core.keyword_alias')\n"
        "__import__(name='astrbot.core.keyword_builtin')\n",
        encoding="utf-8",
    )
    tests_stub = tmp_path / "tests" / "conftest.py"
    tests_stub.parent.mkdir(parents=True)
    tests_stub.write_text(
        "import importlib\nimportlib.import_module('astrbot.core.test_stub')\n",
        encoding="utf-8",
    )
    report = analyze_repository(tmp_path, load_config(CONFIG_PATH))
    targets = {item["target"] for item in report["violations"]["forbidden_imports"]}
    assert "astrbot.core" in targets
    assert "astrbot.core.agent.tool" in targets
    assert "astrbot.core.keyword_only" in targets
    assert "astrbot.core.keyword_alias" in targets
    assert "astrbot.core.keyword_builtin" in targets
    assert not any(
        item["source"].startswith("tests.")
        for item in report["violations"]["forbidden_imports"]
    )


def test_report_mode_fails_closed_when_baseline_is_missing(tmp_path: Path) -> None:
    """report-only 仍生产证据，缺少 baseline 时必须失败闭合。"""

    assert (
        architecture_main(
            [
                "--root",
                str(REPO_ROOT),
                "--baseline",
                str(tmp_path / "missing.json"),
                "--mode",
                "report",
            ]
        )
        == 2
    )


def test_baseline_facts_hash_detects_provenance_tampering(tmp_path: Path) -> None:
    """修改报告事实后必须使 baseline 证据摘要失效。"""

    baseline = copy.deepcopy(_read_baseline(BASELINE_PATH))
    assert baseline is not None
    baseline["summary"]["total_lines"] += 1
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    with pytest.raises(ArchitectureConfigError, match="facts_sha256"):
        _read_baseline(path)


def test_cli_contract_exposes_explicit_baseline_update_only() -> None:
    """CLI 只能通过单一显式参数更新 baseline。"""

    args = build_parser().parse_args(["--mode", "report", "--write-baseline"])
    assert args.write_baseline is True
    assert args.mode == "report"
    review = build_parser().parse_args(
        ["--mode", "changed", "--blocking", "--policy-update"]
    )
    assert review.policy_update is True


def test_git_identity_failure_is_not_reported_as_unknown(tmp_path: Path) -> None:
    """非 Git report 根目录必须失败闭合，不能输出 unknown。"""

    with pytest.raises(ArchitectureSourceError, match="Git"):
        architecture_core._git_value(tmp_path, "rev-parse", "HEAD")


def test_policy_file_drift_is_hard_in_changed_gate() -> None:
    """changed gate 不能把 policy 文件漂移静默并入 feature report。"""

    report = _line_report(1)
    report["_configuration_drift"] = [
        {"reason": "policy_fingerprint_drift", "field": "policy_fingerprint"}
    ]
    gate = evaluate_blocking(
        report,
        {"files": {}, "violations": {}, "dependency_graph": {"edges": []}},
        selected_files=["architecture.toml"],
        blocking=True,
    )
    assert gate["blocking"] is True
    assert any(
        item["reason"] == "policy_or_baseline_changed"
        for item in gate["blocking_violations"]
    )
    explicit = evaluate_blocking(
        report,
        {"files": {}, "violations": {}, "dependency_graph": {"edges": []}},
        selected_files=["architecture.toml"],
        blocking=True,
        allow_baseline_update=True,
    )
    assert not any(
        item["reason"] == "policy_or_baseline_changed"
        for item in explicit["blocking_violations"]
    )


def test_git_identity_error_from_runtime_metadata_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """report 不能降级为 ``unknown`` source commit。"""

    def fail_git(*args: Any, **kwargs: Any) -> str:
        """模拟无法解析 Git identity。"""

        del args, kwargs
        raise ArchitectureSourceError("Git identity 不可解析")

    monkeypatch.setattr(check_architecture, "_git_value", fail_git)
    assert (
        check_architecture.main(
            [
                "--root",
                str(REPO_ROOT),
                "--baseline",
                str(BASELINE_PATH),
                "--mode",
                "report",
            ]
        )
        == 2
    )


def test_analysis_tree_is_stable_and_does_not_touch_the_real_index() -> None:
    """临时索引 provenance 必须可重复且不修改真实索引。"""

    index_before = architecture_core._git_value(REPO_ROOT, "write-tree")
    first = git_worktree_tree(REPO_ROOT)
    second = git_worktree_tree(REPO_ROOT)
    index_after = architecture_core._git_value(REPO_ROOT, "write-tree")
    assert first == second
    assert index_before == index_after


def test_promoted_contract_tests_must_actually_pass(tmp_path: Path) -> None:
    """可解析但失败的 contract test 不能提升 C0-C6 状态。"""

    test_path = tmp_path / "tests" / "test_contract.py"
    test_path.parent.mkdir(parents=True)
    config = copy.deepcopy(load_config(CONFIG_PATH))
    config["public_contracts"][1].update(
        status="verified",
        stable=True,
        no_wrapper=True,
        contract_tests=["tests/test_contract.py"],
        evidence="verified and passed",
    )
    baseline = _read_baseline(BASELINE_PATH)

    test_path.write_text("def test_contract():\n    assert False\n", encoding="utf-8")
    failed = _contract_transition_violations(tmp_path, config, baseline)
    assert any(item["reason"] == "contract_tests_failed" for item in failed)

    test_path.write_text("def test_contract():\n    assert True\n", encoding="utf-8")
    passed = _contract_transition_violations(tmp_path, config, baseline)
    assert not any(item["reason"] == "contract_tests_failed" for item in passed)
