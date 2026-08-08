"""M1 gate 安全反例定向测试（Git 对象安全 / timeout / 原子报告 / 导出启动）。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import scripts.check_m1_gate as m1_gate
from scripts.m1_gate_core import COMMIT_MARKER, git_diff_tree_raw
from tests.test_m1_gate import (
    GATE_FILES,
    REPO_ROOT,
    Fixture,
    _assert_five_reports,
    _commit_all,
    _expected_b_change,
    _init_repo,
    _make_merge,
    _read_report,
    _tree_of,
    _write,
    git,
)


@pytest.fixture()
def cut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    """标准 2-SCC -> 1-SCC 场景（contract 由测试按需添加）。"""

    import scripts.m1_gate_core as m1_gate_core
    from tests.test_m1_gate_probe import TEST_ATTESTOR_PUBLIC

    monkeypatch.setattr(m1_gate_core, "M1_ATTESTOR_PUBLIC_KEY", TEST_ATTESTOR_PUBLIC)
    repo = tmp_path / "repo"
    _init_repo(repo)
    fixture = Fixture(repo, tmp_path)
    fixture.build_base()
    return fixture


def test_mode_change_laundering_exit2(cut: Fixture, tmp_path: Path) -> None:
    """regular-blob mode 漂移（100644 -> 100755）必须 exit 2。"""

    cut.set_head_ops()
    head = cut.build_head()
    head_tree = _tree_of(cut.repo, head)
    cut.add_contract(cut.compute_contract(head_tree, [["core.b", "core.a"]]))
    cut.build_head()
    os.chmod(cut.repo / "core" / "b.py", 0o755)
    head2 = _commit_all(cut.repo, "head chmods b.py")
    merge2 = _make_merge(
        cut.repo, cut.governance_commit or "", head2, "merge with mode change"
    )
    report_dir = tmp_path / "reports"
    code = _run_cli_with_merge(cut, report_dir, head2, merge2)
    assert code == 2
    _assert_five_reports(report_dir)
    decision = _read_report(report_dir, "decision.json")
    assert "error" in decision and "mode 漂移" in decision["error"]


def test_rename_with_mode_change_exit2(cut: Fixture, tmp_path: Path) -> None:
    """R/C 伴随 mode 漂移（R100 100644 -> 100755）必须 exit 2。"""

    cut.set_head_ops()
    head = cut.build_head()
    head_tree = _tree_of(cut.repo, head)
    cut.add_contract(cut.compute_contract(head_tree, [["core.b", "core.a"]]))
    cut.build_head()
    git(cut.repo, "mv", "core/c.py", "core/renamed_c.py")
    os.chmod(cut.repo / "core" / "renamed_c.py", 0o755)
    head2 = _commit_all(cut.repo, "head renames + chmods")
    merge2 = _make_merge(
        cut.repo, cut.governance_commit or "", head2, "merge with rename mode"
    )
    report_dir = tmp_path / "reports"
    code = _run_cli_with_merge(cut, report_dir, head2, merge2)
    assert code == 2
    _assert_five_reports(report_dir)
    decision = _read_report(report_dir, "decision.json")
    assert "error" in decision and "mode 漂移" in decision["error"]


def test_gitlink_change_exit2(cut: Fixture, tmp_path: Path) -> None:
    """候选引入 gitlink（mode 160000）必须 exit 2。"""

    cut.set_head_ops()
    head = cut.build_head()
    head_tree = _tree_of(cut.repo, head)
    cut.add_contract(cut.compute_contract(head_tree, [["core.b", "core.a"]]))
    cut.build_head()
    (cut.repo / "core" / "gitlink_entry").write_text("", encoding="utf-8")
    git(cut.repo, "add", "-A")
    subprocess.run(
        [
            "git",
            "-C",
            str(cut.repo),
            "update-index",
            "--index-info",
        ],
        input=b"160000 e463e4af71f841c50c745a108e3e24cb493303f8\tcore/gitlink_entry\n",
        check=True,
    )
    (cut.repo / "core" / "gitlink_entry").unlink()
    git(cut.repo, "commit", "-qm", "head adds gitlink")
    head2 = git(cut.repo, "rev-parse", "HEAD").stdout.strip()
    merge2 = _make_merge(
        cut.repo, cut.governance_commit or "", head2, "merge with gitlink"
    )
    report_dir = tmp_path / "reports"
    code = _run_cli_with_merge(cut, report_dir, head2, merge2)
    assert code == 2
    decision = _read_report(report_dir, "decision.json")
    assert "error" in decision and (
        "gitlink" in decision["error"] or "symlink" in decision["error"]
    )


def test_symlink_change_exit2(cut: Fixture, tmp_path: Path) -> None:
    """候选引入 symlink（mode 120000）必须 exit 2。"""

    cut.set_head_ops()
    head = cut.build_head()
    head_tree = _tree_of(cut.repo, head)
    cut.add_contract(cut.compute_contract(head_tree, [["core.b", "core.a"]]))
    cut.build_head()
    os.symlink("core/a.py", cut.repo / "core" / "link.py")
    head2 = _commit_all(cut.repo, "head adds symlink")
    merge2 = _make_merge(
        cut.repo, cut.governance_commit or "", head2, "merge with symlink"
    )
    report_dir = tmp_path / "reports"
    code = _run_cli_with_merge(cut, report_dir, head2, merge2)
    assert code == 2
    decision = _read_report(report_dir, "decision.json")
    assert "error" in decision and (
        "symlink" in decision["error"] or "gitlink" in decision["error"]
    )


def test_long_path_archive_exit2(cut: Fixture, tmp_path: Path) -> None:
    """超长合法 UTF-8 路径导致 tar 解包 OSError → exit 2 + 五 error envelope。"""

    cut.set_head_ops()
    head = cut.build_head()
    head_tree = _tree_of(cut.repo, head)
    cut.add_contract(cut.compute_contract(head_tree, [["core.b", "core.a"]]))
    cut.build_head()
    long_path = "core/" + ("a" * 300) + ".py"
    # 绕过文件系统长度限制，直接把 blob 写入 Git 索引
    blob_sha = (
        subprocess.run(
            ["git", "-C", str(cut.repo), "hash-object", "-w", "--stdin"],
            input=b"VALUE = 1\n",
            check=True,
            capture_output=True,
        )
        .stdout.decode("utf-8")
        .strip()
    )
    subprocess.run(
        ["git", "-C", str(cut.repo), "update-index", "--index-info"],
        input=("100644 %s\t%s\n" % (blob_sha, long_path)).encode("utf-8"),
        check=True,
    )
    git(cut.repo, "commit", "-qm", "head adds long path")
    head2 = git(cut.repo, "rev-parse", "HEAD").stdout.strip()
    merge2 = _make_merge(
        cut.repo, cut.governance_commit or "", head2, "merge with long path"
    )
    report_dir = tmp_path / "reports"
    code = _run_cli_with_merge(cut, report_dir, head2, merge2)
    assert code == 2
    _assert_five_reports(report_dir)
    decision = _read_report(report_dir, "decision.json")
    assert "error" in decision


def test_non_utf8_path_exit2(cut: Fixture, tmp_path: Path) -> None:
    """非 UTF-8 路径必须 exit 2 并写出五份 error envelope。"""

    cut.set_head_ops()
    head = cut.build_head()
    head_tree = _tree_of(cut.repo, head)
    cut.add_contract(cut.compute_contract(head_tree, [["core.b", "core.a"]]))
    cut.build_head()
    raw_name = b"core/bad_\xff\xfe_name.py"
    target = cut.repo / os.fsdecode(raw_name)
    target.write_bytes(b"VALUE = 1\n")
    head2 = _commit_all(cut.repo, "head adds non-utf8 path")
    merge2 = _make_merge(
        cut.repo, cut.governance_commit or "", head2, "merge with non-utf8 path"
    )
    report_dir = tmp_path / "reports"
    code = _run_cli_with_merge(cut, report_dir, head2, merge2)
    assert code == 2
    _assert_five_reports(report_dir)
    decision = _read_report(report_dir, "decision.json")
    assert "error" in decision


def test_timeout_exit2(
    cut: Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git 子命令超时必须 exit 2 并写出 error envelope。"""

    import subprocess as sp

    import scripts.m1_gate_core as m1_core

    cut.set_head_ops()
    cut.build_head()
    cut.make_merge()

    def slow_run(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise sp.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(m1_core.subprocess, "run", slow_run)
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 2
    _assert_five_reports(report_dir)
    decision = _read_report(report_dir, "decision.json")
    assert "error" in decision and "超时" in decision["error"]


def test_cat_file_timeout_exit2(
    cut: Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cat-file 读取 blob 超时必须 exit 2 并写出 error envelope。"""

    import scripts.m1_gate_core as m1_core

    cut.set_head_ops(break_cycle=False, extra={"docs/AGENTS.md": "# docs\n"})
    cut.build_head()
    cut.make_merge()

    original_run = m1_core.subprocess.run
    calls = {"count": 0}

    def selective_run(*args: Any, **kwargs: Any) -> Any:
        command = args[0] if args else []
        if command and command[0] == "git" and "cat-file" in command:
            calls["count"] += 1
            if calls["count"] == 1:
                raise m1_core.subprocess.TimeoutExpired(cmd="git cat-file", timeout=1)
        return original_run(*args, **kwargs)

    monkeypatch.setattr(m1_core.subprocess, "run", selective_run)
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 2
    _assert_five_reports(report_dir)
    decision = _read_report(report_dir, "decision.json")
    assert "error" in decision


def test_archive_oserror_exit2(
    cut: Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git archive 抛 OSError → exit 2 + 五 error envelope。"""

    import scripts.m1_gate_analysis as m1_analysis

    cut.set_head_ops(break_cycle=False, extra={"docs/AGENTS.md": "# docs\n"})
    cut.build_head()
    cut.make_merge()

    original_run = subprocess.run

    def fail_archive(*args: Any, **kwargs: Any) -> Any:
        command = args[0] if args else []
        if command and command[0] == "git" and "archive" in command:
            raise OSError("simulated archive failure")
        return original_run(*args, **kwargs)

    monkeypatch.setattr(m1_analysis.subprocess, "run", fail_archive)
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 2
    _assert_five_reports(report_dir)
    decision = _read_report(report_dir, "decision.json")
    assert "error" in decision


def _prep_nonempty(r: Path) -> Path:
    r.mkdir(parents=True)
    (r / "decision.json").write_text('{"exit_code": 0}\n', encoding="utf-8")
    return r


def _prep_file(r: Path) -> Path:
    r.write_text("not a dir\n", encoding="utf-8")
    return r


def _prep_symlink_loop(r: Path) -> Path:
    r.mkdir(parents=True)
    os.symlink(str(r), str(r / "loop"))
    return r / "loop"


@pytest.mark.parametrize("prepare", [_prep_nonempty, _prep_file, _prep_symlink_loop])
def test_report_dir_unusable_exit2(cut: Fixture, tmp_path: Path, prepare: Any) -> None:
    """非空/文件/symlink loop 等不可用 report 目录：顶层收口 exit 2。"""
    cut.set_head_ops()
    cut.build_head()
    cut.make_merge()
    target = prepare(tmp_path / "reports")
    code = m1_gate.main(
        [
            "--repository",
            str(cut.repo),
            "--base-commit",
            "abc",
            "--pr-head-commit",
            cut.head_commit or "",
            "--candidate-commit",
            cut.merge_commit or "",
            "--report-dir",
            str(target),
        ]
    )
    assert code == 2
    # 旧内容原样保留（未被消费），本次运行没有产出新 manifest
    stale = tmp_path / "reports" / "decision.json"
    if stale.is_file():
        assert "exit_code" in stale.read_text(encoding="utf-8")


def test_manifest_and_commit_marker_present(cut: Fixture, tmp_path: Path) -> None:
    """报告目录根必须含 manifest 指向的五份文件与 COMMITTED marker，无杂散子目录。"""

    cut.set_head_ops(break_cycle=False, extra={"docs/AGENTS.md": "# docs\n"})
    cut.build_head()
    cut.make_merge()
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 0
    assert (report_dir / "current.json").is_file()
    for name in (
        "provenance.json",
        "diff.json",
        "base-analysis.json",
        "candidate-analysis.json",
        "decision.json",
    ):
        assert (report_dir / name).is_file()
    assert (report_dir / COMMIT_MARKER).is_file()
    assert not [p for p in report_dir.iterdir() if p.is_dir()]


def test_exported_checker_launches(tmp_path: Path) -> None:
    """按 workflow 精确导出清单实跑导出副本：真实可启动、不依赖候选 checkout。

    在合成仓库中把 gate 文件提交进 base，再按 workflow 的
    ``git archive BASE_SHA <files>`` 命令导出并实跑。
    """

    repo = tmp_path / "export-repo"
    _init_repo(repo)
    _write(repo, "core/__init__.py", "")
    _write(repo, "core/a.py", "VALUE = 1\n")
    for f in GATE_FILES:
        destination = repo / f
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / f, destination)
    base = _commit_all(repo, "base with gate files")

    export = tmp_path / "export"
    export.mkdir()
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", base] + list(GATE_FILES),
        check=True,
        capture_output=True,
    )
    subprocess.run(["tar", "-x", "-C", str(export)], input=archive.stdout, check=True)
    for f in GATE_FILES:
        assert (export / f).is_file(), f"导出缺少 {f}"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(export / "scripts")
    completed = subprocess.run(
        ["python3", str(export / "scripts/check_m1_gate.py"), "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--base-commit" in completed.stdout
    report_dir = tmp_path / "reports"
    completed = subprocess.run(
        [
            "python3",
            str(export / "scripts/check_m1_gate.py"),
            "--repository",
            str(repo),
            "--base-commit",
            "abc",
            "--pr-head-commit",
            base,
            "--candidate-commit",
            base,
            "--report-dir",
            str(report_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 2
    _assert_five_reports(report_dir)


def test_workflow_no_candidate_dependency() -> None:
    """workflow 不得在候选 checkout 上执行 uv sync（避免候选控制安装）。"""

    workflow = (REPO_ROOT / ".github/workflows/m1-gate.yml").read_text(encoding="utf-8")
    assert "uv sync" not in workflow
    assert "uv run" not in workflow
    assert "python3" in workflow
    assert 'PYTHONPATH="${CHECKER_DIR}/scripts"' in workflow


def test_workflow_trigger_is_base_owned() -> None:
    """workflow 必须用 pull_request_target 且 checkout 仅 base ref、action 固定 SHA。"""
    workflow = (REPO_ROOT / ".github/workflows/m1-gate.yml").read_text(encoding="utf-8")
    assert "pull_request_target" in workflow
    assert "pull_request:" not in workflow
    assert "ref: ${{ github.event.pull_request.base.sha }}" in workflow
    assert "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in workflow
    assert "fetch-depth: 0" in workflow and "persist-credentials: false" in workflow


def test_workflow_actions_pinned_to_full_sha() -> None:
    """全部 action 必须固定到完整 40 位 commit SHA，不得使用可移动 @vN。"""
    workflow = (REPO_ROOT / ".github/workflows/m1-gate.yml").read_text(encoding="utf-8")
    used = [
        line.strip().split()[1]
        for line in workflow.splitlines()
        if line.strip().startswith("uses:")
    ]
    assert used, "workflow 未使用任何 action"
    for action in used:
        assert action.startswith(("actions/",)), f"未预期 action: {action}"
        ref = action.split("@")[1]
        assert len(ref) == 40 and set(ref) <= set("0123456789abcdef"), ref
    assert "@v4" not in workflow and "@v5" not in workflow and "@v7" not in workflow


def test_workflow_validation_fetch_and_concurrency() -> None:
    """PR number/SHA 校验、定向 fetch refs/pull/N 到固定 ref + OID 核对、按 PR 并发。"""
    workflow = (REPO_ROOT / ".github/workflows/m1-gate.yml").read_text(encoding="utf-8")
    assert "^[1-9][0-9]*$" in workflow and "^[0-9a-f]{40}$" in workflow
    assert "refs/pull/${PR_NUMBER}/head:refs/m1/pr-head" in workflow
    assert "refs/pull/${PR_NUMBER}/merge:refs/m1/pr-merge" in workflow
    assert "rev-parse refs/m1/pr-head" in workflow
    assert "rev-parse refs/m1/pr-merge" in workflow
    assert "m1-gate-${{ github.event.pull_request.number }}" in workflow
    assert "cancel-in-progress: true" in workflow


def test_workflow_verifier_only_no_candidate_execution() -> None:
    """verifier-only：无候选执行/无 docker/无自生成证据；密钥+nonce 注入。"""
    workflow = (REPO_ROOT / ".github/workflows/m1-gate.yml").read_text(encoding="utf-8")
    assert "--probe-result" in workflow
    assert not any(
        line.strip().startswith("--probe ")
        for line in workflow.splitlines()
        if line.strip().startswith("--")
    )
    assert "docker" not in workflow and "m1-removed-edge-probe" not in workflow
    assert "attestation.json" in workflow
    assert "--attestation-nonce" in workflow and "openssl rand -hex 32" in workflow
    assert "secrets.M1_ATTESTATION_KEY" not in workflow
    assert "GITHUB_TOKEN" not in workflow


def test_workflow_fetch_step_env_dataflow_consistent() -> None:
    """Blocker 回归：GITHUB_ENV 写入名 -> fetch step env -> OID 比较全链一致。"""
    workflow = (REPO_ROOT / ".github/workflows/m1-gate.yml").read_text(encoding="utf-8")
    lines = workflow.splitlines()

    def section(marker: str, stop: str) -> list[str]:
        start = next(i for i, line in enumerate(lines) if marker in line)
        end = next(
            (i for i in range(start + 1, len(lines)) if stop in lines[i]), len(lines)
        )
        return lines[start:end]

    validate = section("校验 PR number 与三个提交 SHA", "检出 base 分支")
    env_written = {
        re.search(r'echo "([A-Za-z0-9_]+)=', line).group(1)
        for line in validate
        if '>> "${GITHUB_ENV}"' in line
    }
    assert {"base", "pr_head", "merge", "M1_NONCE"} <= env_written, env_written

    fetch = section("定向 fetch PR refs 到固定 ref", "配置 Python")
    fetch_env: dict[str, str] = {}
    run_lines: list[str] = []
    mode = ""
    for line in fetch:
        stripped = line.strip()
        if stripped == "env:":
            mode = "env"
        elif stripped == "run: |":
            mode = "run"
        elif mode == "env" and stripped and "${{" in stripped:
            name, source = stripped.split(":", 1)
            fetch_env[name.strip()] = source.strip()
        elif mode == "run":
            run_lines.append(stripped)
    assert fetch_env.get("PR_HEAD_SHA") == "${{ env.pr_head }}", fetch_env
    assert fetch_env.get("MERGE_SHA") == "${{ env.merge }}", fetch_env
    referenced = {
        match.group(1)
        for line in run_lines
        for match in re.finditer(r"\$\{([A-Za-z_]+)\}", line)
    }
    for var in referenced:
        assert var in fetch_env or var in env_written, f"fetch 引用未定义变量: {var}"
    assert any(
        "rev-parse refs/m1/pr-head" in line and "${PR_HEAD_SHA}" in line
        for line in run_lines
    )
    assert any(
        "rev-parse refs/m1/pr-merge" in line and "${MERGE_SHA}" in line
        for line in run_lines
    )


def test_workflow_nonce_dataflow_name_consistent() -> None:
    """Blocker 回归：GITHUB_ENV 写入的 nonce 名与 gate argv 引用必须同名。"""

    workflow = (REPO_ROOT / ".github/workflows/m1-gate.yml").read_text(encoding="utf-8")
    write_match = re.search(r'echo "(\w+)=\$\(openssl rand -hex 32\)" >>', workflow)
    assert write_match, "缺少 GITHUB_ENV nonce 写入"
    env_name = write_match.group(1)
    assert env_name == "M1_NONCE", f"GITHUB_ENV nonce 名: {env_name}"
    assert f"${{{env_name}}}" in workflow, "gate argv 引用的 nonce 名不一致"


def test_workflow_upload_only_when_consumer_rc_zero() -> None:
    """artifact 只在 consumer rc==0 时上传（staging run-id/attempt 绑定）。"""
    workflow = (REPO_ROOT / ".github/workflows/m1-gate.yml").read_text(encoding="utf-8")
    assert "steps.cons.outputs.rc == '0'" in workflow
    assert "m1-gate-verified/${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}" in workflow
    assert "--run-id" in workflow and "--run-attempt" in workflow
    assert "if-no-files-found: error" in workflow


def test_workflow_report_dir_run_id_attempt() -> None:
    """报告目录以受信 run_id/run_attempt 构造并传给 CLI。"""
    workflow = (REPO_ROOT / ".github/workflows/m1-gate.yml").read_text(encoding="utf-8")
    assert "${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}" in workflow
    assert "--run-id" in workflow
    assert "--run-attempt" in workflow


def test_workflow_trusted_consumer_uploads_only_verified_set() -> None:
    """受信 consumer 验证后只上传 staging 集合（m1_gate_report --publish）。"""
    workflow = (REPO_ROOT / ".github/workflows/m1-gate.yml").read_text(encoding="utf-8")
    assert "m1_gate_report.py" in workflow
    assert "--publish" in workflow
    assert "staging_dir" in workflow
    assert "if-no-files-found: error" in workflow
    assert "m1_gate_probe.py" in workflow


def test_workflow_report_dir_fixed_before_export() -> None:
    """安全 report 目录必须在任何失败前固定（独立于 base 导出步骤）。"""
    workflow = (REPO_ROOT / ".github/workflows/m1-gate.yml").read_text(encoding="utf-8")
    paths_step = workflow.index("固定安全目录")
    export_step = workflow.index(
        "从 base 导出完整 checker（失败不回退到候选 checkout）"
    )
    assert paths_step < export_step
    assert "report_dir=${RUNNER_TEMP}/m1-gate-report" in workflow
    assert "RUNNER_TEMP" in workflow and "if: ${{ always() }}" in workflow
    assert "upload-artifact" in workflow and "|| true" not in workflow


def test_workflow_protects_all_workflows() -> None:
    """workflow 内不得有会覆盖整个 .github/workflows/ 保护的旁路。"""
    wf = (REPO_ROOT / ".github/workflows/m1-gate.yml").read_text(encoding="utf-8")
    assert "persist-credentials: false" in wf and "contents: read" in wf


def test_nul_safe_diff_parses_all_statuses(cut: Fixture, tmp_path: Path) -> None:
    """A/M/D/R 状态与特殊路径必须被 NUL-safe 解析。"""

    repo = cut.repo
    cut.set_head_ops(
        extra={
            "core/new_file.py": "VALUE=1\n",
            "core/space name.py": "VALUE=1\n",
        }
    )
    head = cut.build_head()
    git(repo, "rm", "-q", "core/c.py")
    git(repo, "mv", "core/a.py", "core/renamed_a.py")
    head = _commit_all(repo, "head with A/M/D/R")
    head_tree = _tree_of(repo, head)
    changes = git_diff_tree_raw(repo, cut.base_tree or "", head_tree)
    statuses = {item["status"] for item in changes}
    assert "A" in statuses
    assert "M" in statuses
    assert "D" in statuses
    assert "R" in statuses
    paths = {str(item.get("old_path") or item.get("new_path")) for item in changes}
    assert "core/space name.py" in paths
    rename = next(item for item in changes if item["status"] == "R")
    assert rename["old_path"] == "core/a.py"
    assert rename["new_path"] == "core/renamed_a.py"
    canonical = [
        {
            "status": item["status"],
            "old_path": item.get("old_path"),
            "new_path": item.get("new_path"),
            "old_mode": item.get("old_mode"),
            "new_mode": item.get("new_mode"),
            "old_blob": item.get("old_blob"),
            "new_blob": item.get("new_blob"),
        }
        for item in changes
    ]
    assert all(item["old_mode"] is not None for item in canonical)


def test_production_contract_anchor_not_real_exit2(
    cut: Fixture, tmp_path: Path
) -> None:
    """production contract 锚点格式合法但不存在 → exit 2。"""

    cut.set_head_ops()
    head = cut.build_head()
    head_tree = _tree_of(cut.repo, head)
    contract = cut.compute_contract(head_tree, [["core.b", "core.a"]])
    contract["base_commit"] = "1" * 40  # 不存在但格式合法
    contract["base_tree"] = "2" * 40
    cut.add_contract(contract)
    cut.build_head()
    cut.make_merge()
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 2
    decision = _read_report(report_dir, "decision.json")
    assert "error" in decision
    assert "锚点" in decision["error"] or "真实对象" in decision["error"]


def test_production_contract_anchor_not_ancestor_exit2(
    cut: Fixture, tmp_path: Path
) -> None:
    """production contract 锚点真实但非 base 祖先 → exit 2。"""

    cut.set_head_ops()
    head = cut.build_head()
    head_tree = _tree_of(cut.repo, head)
    contract = cut.compute_contract(head_tree, [["core.b", "core.a"]])
    # 用另一个无关分支的提交作为锚点（真实但不相关）
    unrelated = tmp_path / "unrelated"
    _init_repo(unrelated)
    _write(unrelated, "core/x.py", "VALUE=1\n")
    unrelated_commit = _commit_all(unrelated, "unrelated")
    contract["base_commit"] = unrelated_commit
    contract["base_tree"] = _tree_of(unrelated, unrelated_commit)
    cut.add_contract(contract)
    cut.build_head()
    cut.make_merge()
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 2
    decision = _read_report(report_dir, "decision.json")
    assert "error" in decision


def test_uncontracted_legacy_change_blocks(cut: Fixture, tmp_path: Path) -> None:
    """contract 未覆盖的无关 legacy core/* 改动必须 exit 1。"""

    cut.set_head_ops(extra={"core/c.py": "VALUE_C = 9\n"})
    head = cut.build_head()
    head_tree = _tree_of(cut.repo, head)
    # contract 只覆盖 core/b.py，不含 core/c.py 的修改
    cut.add_contract(
        cut.compute_contract(
            head_tree,
            [["core.b", "core.a"]],
            expected_changes=[_expected_b_change()],
        )
    )
    cut.build_head()
    cut.make_merge()
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 1
    decision = _read_report(report_dir, "decision.json")
    assert decision["status"] == "blocked"
    assert any("未被 contract 覆盖" in reason for reason in decision["reasons"])


def _run_cli_with_merge(cut: Fixture, report_dir: Path, head: str, merge: str) -> int:
    """以给定 head/merge 直接运行 gate CLI（mode/特殊路径反例共用）。"""

    return m1_gate.main(
        [
            "--repository",
            str(cut.repo),
            "--base-commit",
            cut.governance_commit or "",
            "--pr-head-commit",
            head,
            "--candidate-commit",
            merge,
            "--report-dir",
            str(report_dir),
        ]
    )


def _build_escaped_cut(cut: Fixture, content: str) -> None:
    """构造改了 core/b.py 的 production cut（带 base contract）。"""
    cut.set_head_ops(extra={"core/b.py": content})
    head = cut.build_head()
    head_tree = _tree_of(cut.repo, head)
    cut.add_contract(cut.compute_contract(head_tree, [["core.b", "core.a"]]))
    cut.build_head()
    cut.make_merge()


@pytest.mark.parametrize(
    "content",
    [
        "__builtins__['__import__']('core.a', fromlist=['*'])\n",
        "__builtins__['__im' + 'port__']('core.a', fromlist=['*'])\n",
        "import importlib\nimportlib.import_module('core.a')\n",
        "import importlib\ngetattr(importlib, 'import_module')\n",
        "globals()['__import__']('core.a')\n",
        "sys.modules['core.a'] = None\n",
        "eval('import core.a')\n",
        "builtins.__dict__['__import__']('core.a')\n",
        "builtins.__dict__.get('__import__')('core.a')\n",
        "d = builtins.__dict__\nd['__import__']('core.a')\n",
        "d = builtins.__dict__\nd.get('__import__')('core.a')\n",
        "bi = __builtins__\nbi['__import__']('core.a')\n",
        "b = __builtins__\nd = b.__dict__\nd['__import__']('core.a')\n",
        "import builtins as b\nb.__dict__['__import__']('core.a')\n",
        "from builtins import __import__ as imp\nimp('core.a')\n",
        "cfg = {}\ncfg['__import__']('core.a')\n",
    ],
)
def test_context_aware_loading_escapes_block(
    cut: Fixture, tmp_path: Path, content: str
) -> None:
    """可准确限定的动态加载逃逸必须 exit 1（builtins/__dict__/alias/未知值）。"""

    _build_escaped_cut(cut, content)
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 1, f"expected blocked for {content!r}, got {code}"
    decision = _read_report(report_dir, "decision.json")
    assert decision["status"] == "blocked"
    assert any("逃逸" in reason for reason in decision["reasons"])


@pytest.mark.parametrize(
    "content",
    [
        "globals()['x'] = 1\nVALUE_B = 1\n",
        "import re\nVALUE_B = re.compile('x')\n",
        "getattr('', 'join')\nVALUE_B = 1\n",
        "import sys\n_v = sys.modules\nVALUE_B = 1\n",
    ],
)
def test_context_aware_benign_usage_not_flagged(
    cut: Fixture, tmp_path: Path, content: str
) -> None:
    """上下文感知：非加载用途（re.compile/普通下标/引用）不得误杀。"""
    _build_escaped_cut(cut, content)
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 0, f"expected pass for {content!r}, got {code}"
    decision = _read_report(report_dir, "decision.json")
    assert decision["status"] == "pass"
    assert decision["invariants"]["no_dynamic_import_escapes"] is True
