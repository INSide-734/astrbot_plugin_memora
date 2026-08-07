"""M0 package-by-feature 单切片 gate 的独立定向测试。"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

import scripts.check_feature_gate as feature_gate
from scripts.architecture_core import (
    ArchitectureConfigError,
    _read_baseline,
    load_config,
)
from scripts.architecture_snapshots import (
    analysis_content_sha256,
    analysis_manifest_sha256,
    facts_sha256,
    git_worktree_tree,
)
from scripts.check_feature_gate import (
    _changed_scope,
    _check_contract_prerequisites,
    _is_common_change,
    check_feature,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = load_config(REPO_ROOT / "architecture.toml")
BASELINE = _read_baseline(REPO_ROOT / "scripts" / "baselines" / "architecture.json")


def test_feature_contract_prerequisites_preserve_blockers() -> None:
    """jargon 在 C1 未公开前只能准备台账，不能取得迁移放行。"""

    details, blocked = _check_contract_prerequisites(CONFIG, "jargon")
    assert [item["id"] for item in details] == ["C1"]
    assert [item["id"] for item in blocked] == ["C1"]
    assert blocked[0]["status"] == "blocked"
    assert blocked[0]["no_wrapper"] is True


def test_feature_scope_rejects_cross_feature_and_legacy_ownership() -> None:
    """一个 feature PR 不能交叉修改其他 feature 或冻结的旧生产目录。"""

    scope = _changed_scope(
        [
            "core/features/jargon/domain.py",
            "core/features/identity/domain.py",
            "core/api/jargon_api.py",
            "tests/test_jargon_gate.py",
        ],
        "jargon",
        "core/features/jargon",
    )
    assert scope["foreign_features"] == ["identity"]
    assert scope["frozen_legacy_files"] == ["core/api/jargon_api.py"]
    assert len(scope["reasons"]) == 3


def test_no_feature_change_is_reported_as_not_started() -> None:
    """M0 公共门禁文件本身不应伪装成已启动的 feature 迁移。"""

    report = check_feature(
        REPO_ROOT,
        CONFIG,
        BASELINE,
        "jargon",
        ["architecture.toml", "scripts/check_feature_gate.py"],
        blocking=True,
    )
    assert report["status"] == "not_started"
    assert report["gate"]["blocking"] is False
    assert report["scope"]["feature_files"] == []


def test_feature_change_is_blocked_until_public_contract_is_stable() -> None:
    """存在 feature 文件变更时，C1 blocker 必须转为 blocking 退出语义。"""

    report = check_feature(
        REPO_ROOT,
        CONFIG,
        BASELINE,
        "jargon",
        ["core/features/jargon/domain.py"],
        blocking=True,
    )
    assert report["status"] == "blocked"
    assert report["gate"]["blocking"] is True
    assert any(
        item.get("kind") == "contract" and item.get("id") == "C1"
        for item in report["gate"]["violations"]
    )
    assert all("wrapper" in risk or "harness" in risk for risk in report["risks"])


def test_legacy_only_change_blocks_even_without_feature_file() -> None:
    """冻结旧生产目录的单独改动也必须阻断 feature gate。"""

    report = check_feature(
        REPO_ROOT,
        CONFIG,
        BASELINE,
        "jargon",
        ["core/api/jargon_api.py"],
        blocking=True,
    )
    assert report["status"] == "blocked"
    assert report["scope"]["feature_files"] == []
    assert report["gate"]["blocking"] is True
    assert any(item["kind"] == "scope" for item in report["gate"]["violations"])


def test_common_file_matching_does_not_use_broad_prefixes() -> None:
    """同名后缀文件不能借公共台账路径绕过所有权检查。"""

    assert _is_common_change("architecture.toml") is True
    assert _is_common_change("scripts/check_feature_gate.py") is True
    assert _is_common_change("architecture.toml.bak") is False
    assert _is_common_change("scripts-not-a-directory/file.py") is False


def test_real_ten_file_candidate_is_common_scope_not_unsupported() -> None:
    """既定 10 文件 M0 候选不是 feature 迁移，必须可报告。"""

    owned = [
        ".github/workflows/architecture-report.yml",
        "architecture.toml",
        "scripts/architecture_analysis.py",
        "scripts/architecture_core.py",
        "scripts/architecture_snapshots.py",
        "scripts/baselines/architecture.json",
        "scripts/check_architecture.py",
        "scripts/check_feature_gate.py",
        "tests/test_architecture_gate.py",
        "tests/test_feature_gate.py",
    ]
    scope = _changed_scope(owned, "jargon", "core/features/jargon")
    assert scope["unsupported_files"] == []
    assert scope["frozen_legacy_files"] == []
    assert scope["reasons"] == []
    report = check_feature(REPO_ROOT, CONFIG, BASELINE, "jargon", owned, blocking=True)
    assert report["status"] == "not_started"
    assert report["gate"]["blocking"] is False


def test_feature_report_binds_analysis_content_provenance() -> None:
    """Feature 报告必须绑定实际分析树、内容清单与稳定事实。"""

    report = check_feature(REPO_ROOT, CONFIG, BASELINE, "jargon", [], blocking=False)
    files = report["architecture"]["files"]
    assert report["analysis_tree"] == git_worktree_tree(REPO_ROOT)
    assert report["analysis_content_sha256"] == analysis_content_sha256(
        REPO_ROOT, files
    )
    assert report["analysis_content_sha256"] == analysis_manifest_sha256(files)
    assert report["facts_sha256"] == facts_sha256(report["architecture"])


def test_feature_report_rejects_tampered_facts() -> None:
    """Feature 报告事实被修改后必须在写出前失败闭合。"""

    report = check_feature(REPO_ROOT, CONFIG, BASELINE, "jargon", [], blocking=False)
    tampered = copy.deepcopy(report)
    tampered["architecture"]["summary"]["total_lines"] += 1
    with pytest.raises(ArchitectureConfigError, match="facts_sha256"):
        feature_gate.validate_feature_report_provenance(REPO_ROOT, tampered)


def test_workflow_and_cli_contract_are_pinned() -> None:
    """Fresh runner 必须先安装锁定环境，再实际执行两份定向测试。"""

    workflow = (REPO_ROOT / ".github/workflows/architecture-report.yml").read_text(
        encoding="utf-8"
    )
    for flag in (
        "--mode report",
        "--baseline scripts/baselines/architecture.json",
        "--report reports/architecture.json",
    ):
        assert flag in workflow
    assert "fetch-depth: 0" in workflow
    assert "tests/test_architecture_gate.py" in workflow
    assert "--collect-only" not in workflow
    assert "scripts/check_feature_gate.py" in workflow
    assert "reports/jargon-feature.json" in workflow

    setup_uv = "astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78"
    pytest_command = (
        "uv run --locked pytest -q tests/test_architecture_gate.py "
        "tests/test_feature_gate.py"
    )
    assert setup_uv in workflow
    assert 'UV_VERSION: "0.11.x"' in workflow
    assert "version: ${{ env.UV_VERSION }}" in workflow
    assert "cache-dependency-glob: uv.lock" in workflow
    assert "uv sync --locked --dev" in workflow
    assert pytest_command in " ".join(workflow.split())
    assert "python -m pytest" not in workflow

    checkout_pos = workflow.index("actions/checkout@v7")
    python_pos = workflow.index("actions/setup-python@v7")
    uv_pos = workflow.index(setup_uv)
    sync_pos = workflow.index("uv sync --locked --dev")
    report_pos = workflow.index("uv run --locked python scripts/check_architecture.py")
    pytest_pos = workflow.index(
        pytest_command.split(" tests/test_architecture_gate.py")[0]
    )
    feature_pos = workflow.index("uv run --locked python scripts/check_feature_gate.py")
    assert (
        checkout_pos
        < python_pos
        < uv_pos
        < sync_pos
        < report_pos
        < pytest_pos
        < feature_pos
    )
