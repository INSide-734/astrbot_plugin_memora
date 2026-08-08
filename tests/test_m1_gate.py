"""M1 gate 受信双树裁决的定向测试（fixture 使用真实 Git 对象）。"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

import pytest

import scripts.check_m1_gate as m1_gate
import scripts.m1_gate_core as m1_gate_core
import scripts.m1_gate_probe as m1_gate_probe
from scripts.m1_gate_core import (
    change_manifest_sha256,
    git_diff_tree_raw,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_SOURCE = REPO_ROOT / "architecture.toml"
REPORT_FILES = (
    "provenance.json",
    "diff.json",
    "base-analysis.json",
    "candidate-analysis.json",
    "decision.json",
)
GATE_FILES = (
    "scripts/check_m1_gate.py",
    "scripts/m1_gate_core.py",
    "scripts/m1_gate_analysis.py",
    "scripts/m1_gate_probe.py",
    "scripts/m1_gate_report.py",
    "scripts/architecture_core.py",
    "scripts/architecture_analysis.py",
    "scripts/architecture_snapshots.py",
)
TEST_ATTESTATION_NONCE = "test-nonce-8baeafe4-ef69-4255"


def local_docker_runner(
    command: Sequence[str],
) -> subprocess.CompletedProcess[bytes]:
    """测试用本地 runner：解析隔离域命令的只读挂载，用宿主 python 直跑 harness。

    与真实 docker 相同的输入语义（candidate/harness 只读、环境仅 PATH、
    python -I -B），用于在无 docker 环境验证观察者与 harness 逻辑。
    """

    tokens = list(command)
    mounts: dict[str, str] = {}
    for i, token in enumerate(tokens):
        if token == "--volume":
            host, guest = tokens[i + 1].split(":", 2)[:2]
            mounts[guest] = host
    assert "/candidate" in mounts and "/harness" in mounts, "隔离域挂载缺失"
    harness_name = tokens[-4].rsplit("/", 1)[1]
    candidate_root = mounts["/candidate"]
    source, target = tokens[-2], tokens[-1]
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(Path(mounts["/harness"]) / harness_name),
            candidate_root,
            source,
            target,
        ],
        capture_output=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin"},
    )


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """在 fixture 仓库执行 git 并校验成功。"""

    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed


def _init_repo(repo: Path) -> None:
    """初始化 fixture 仓库并写入最小 architecture.toml。"""

    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "test")
    git(repo, "config", "user.email", "test@example.com")
    shutil.copy2(CONFIG_SOURCE, repo / "architecture.toml")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "config")


def _write(repo: Path, path: str, content: str) -> None:
    """写入仓库相对路径文件。"""

    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _commit_all(repo: Path, message: str) -> str:
    """提交全部变更并返回完整 SHA。"""

    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def _tree_of(repo: Path, commit: str) -> str:
    """返回 commit 的 tree SHA。"""

    return git(repo, "rev-parse", f"{commit}^{{tree}}").stdout.strip()


def _make_merge(repo: Path, base: str, head: str, message: str) -> str:
    """用 git commit-tree 构造父关系为 (base, head) 的 merge commit。"""

    return git(
        repo,
        "commit-tree",
        _tree_of(repo, head),
        "-p",
        base,
        "-p",
        head,
        "-m",
        message,
    ).stdout.strip()


class Fixture:
    """一个完整的 M1 cut 场景：base -> governance(contract) -> head -> merge。"""

    def __init__(self, repo: Path, work: Path) -> None:
        self.repo = repo
        self.work = work
        self.base_commit: str | None = None
        self.base_tree: str | None = None
        self.governance_commit: str | None = None
        self.head_commit: str | None = None
        self.merge_commit: str | None = None
        self.contract: dict[str, Any] | None = None
        self.head_ops: list[tuple[str, str | None]] = []

    def build_base(self) -> None:
        """建立带 2 节点 SCC（core.a <-> core.b）的 base。"""

        _write(self.repo, "core/__init__.py", "")
        _write(self.repo, "core/a.py", "from core import b\nVALUE_A = 1\n")
        _write(self.repo, "core/b.py", "from core import a\nVALUE_B = 1\n")
        _write(self.repo, "core/c.py", "VALUE_C = 1\n")
        self.base_commit = _commit_all(self.repo, "base with 2-SCC")
        self.base_tree = _tree_of(self.repo, self.base_commit)

    def set_head_ops(
        self,
        *,
        break_cycle: bool = True,
        extra: dict[str, str] | None = None,
    ) -> None:
        """记录 PR head 需要应用的文件操作。"""

        self.head_ops = []
        if break_cycle:
            self.head_ops.append(("core/b.py", "VALUE_B = 2\n"))
        self.head_ops.extend((path, content) for path, content in (extra or {}).items())

    def compute_contract(
        self,
        head_tree: str,
        removed_edges: list[list[str]],
        *,
        expected_changes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """按当前 base 事实生成与 head_tree diff 匹配的 contract。

        锚点绑定原始 base（governance 提交的父提交），是 gate base 的祖先。
        """

        assert self.base_commit is not None and self.base_tree is not None
        facts = self._tree_facts(self.base_tree)
        changes = (
            expected_changes
            if expected_changes is not None
            else _actual_changes(self.repo, self.base_tree, head_tree)
        )
        return {
            "schema_version": 1,
            "cut_id": "test-cut",
            "base_commit": self.base_commit,
            "base_tree": self.base_tree,
            "base_graph_facts_sha256": facts,
            "change_manifest_sha256": change_manifest_sha256(changes),
            "expected_changes": changes,
            "expected_removed_edges": removed_edges,
        }

    def add_contract(self, contract: dict[str, Any]) -> None:
        """把 contract 放进 base（独立 governance 提交，落在 main 分支）。"""

        git(self.repo, "checkout", "-q", "main")
        path = "scripts/m1_cuts/test-cut.json"
        _write(self.repo, path, json.dumps(contract, ensure_ascii=False))
        self.governance_commit = _commit_all(self.repo, "governance: add cut contract")
        self.contract = contract

    def build_head(self) -> str:
        """从 governance（若有）或 base 建立 PR head。"""

        start = self.governance_commit or self.base_commit
        assert start is not None
        if self.head_commit is not None:
            git(self.repo, "checkout", "-q", "pr-head")
            git(self.repo, "reset", "-q", "--hard", start)
        else:
            git(self.repo, "checkout", "-q", "-b", "pr-head", start)
        for path, content in self.head_ops:
            if content is None:
                (self.repo / path).unlink(missing_ok=True)
            else:
                _write(self.repo, path, content)
        self.head_commit = _commit_all(self.repo, "head breaks the cycle")
        return self.head_commit

    def make_merge(self) -> str:
        """构造父关系为 (base, head) 的 merge candidate。"""

        first_parent = self.governance_commit or self.base_commit
        assert first_parent is not None
        assert self.head_commit is not None
        self.merge_commit = _make_merge(
            self.repo, first_parent, self.head_commit, "merge candidate"
        )
        return self.merge_commit

    def _tree_facts(self, tree: str) -> str:
        """物化一棵 tree 并返回 facts SHA（与 gate 同口径）。"""

        import tempfile

        from scripts.architecture_analysis import analyze_repository
        from scripts.architecture_core import load_config
        from scripts.architecture_snapshots import facts_sha256, stable_facts_payload
        from scripts.m1_gate_analysis import git_materialize_tree

        with tempfile.TemporaryDirectory() as text:
            root = Path(text)
            git_materialize_tree(self.repo, tree, root)
            config = load_config(root / "architecture.toml")
            report = analyze_repository(root, config)
            return facts_sha256(stable_facts_payload(report))

    def run_probe(self, probe_dir: Path) -> int:
        """以参考 producer 直调运行 removed-edge 探针（verifier-only 下
        gate CLI 不再有 --probe；证据必须由外部受信方产生）。"""

        first_parent = self.governance_commit or self.base_commit
        assert first_parent is not None
        assert self.head_commit is not None
        assert self.merge_commit is not None
        return m1_gate_probe.run_probe(
            self.repo,
            first_parent,
            self.head_commit,
            self.merge_commit,
            probe_dir,
            attestation_nonce=TEST_ATTESTATION_NONCE,
        )

    def run_gate(
        self,
        report_dir: Path,
        *,
        run_id: str | None = None,
        run_attempt: str | None = None,
        probe_dir: Path | None = None,
    ) -> int:
        """以 CLI 形式运行 M1 gate（生产 cut 自动附 removed-edge 探针证据）。"""

        first_parent = self.governance_commit or self.base_commit
        assert first_parent is not None
        assert self.head_commit is not None
        assert self.merge_commit is not None
        args = [
            "--repository",
            str(self.repo),
            "--base-commit",
            first_parent,
            "--pr-head-commit",
            self.head_commit,
            "--candidate-commit",
            self.merge_commit,
            "--report-dir",
            str(report_dir),
        ]
        if probe_dir is None:
            probe_dir = self.work / "probe"
        # 生产 cut 的 attestation 由测试（模拟外部受保护 attestor）以测试
        # 私钥签发；参考 producer 无私钥，其输出永远无法通过验签。
        if self.contract is not None:
            from tests.test_m1_gate_probe import _bound_probe_result

            probe_dir.mkdir(parents=True, exist_ok=True)
            payload = _bound_probe_result(
                self, verdict="confirmed_removed", extra_fields={}, exit_code=0
            )
            (probe_dir / "probe-result.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            args += ["--probe-result", str(probe_dir / "probe-result.json")]
        args += ["--attestation-nonce", TEST_ATTESTATION_NONCE]
        if run_id is not None:
            args += ["--run-id", run_id]
        if run_attempt is not None:
            args += ["--run-attempt", run_attempt]
        return m1_gate.main(args)


@pytest.fixture()
def cut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    """标准 2-SCC -> 1-SCC 场景（contract 由测试按需添加）。"""

    from tests.test_m1_gate_probe import TEST_ATTESTOR_PUBLIC

    monkeypatch.setattr(m1_gate_probe, "_run_docker", local_docker_runner)
    monkeypatch.setattr(m1_gate_core, "M1_ATTESTOR_PUBLIC_KEY", TEST_ATTESTOR_PUBLIC)
    repo = tmp_path / "repo"
    _init_repo(repo)
    fixture = Fixture(repo, tmp_path)
    fixture.build_base()
    return fixture


def _actual_changes(repo: Path, base_tree: str, head_tree: str) -> list[dict[str, Any]]:
    """用 gate 自身的 NUL-safe diff 解析器取 change 清单。"""

    changes = git_diff_tree_raw(repo, base_tree, head_tree)
    return [
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


def _expected_b_change() -> dict[str, Any]:
    """contract.expected_changes 中 core/b.py 修改条目的占位形态。"""

    return {
        "status": "M",
        "old_path": "core/b.py",
        "new_path": "core/b.py",
        "old_mode": "100644",
        "new_mode": "100644",
        "old_blob": "a" * 40,
        "new_blob": "b" * 40,
    }


def _cli_args(
    cut: Fixture,
    report_dir: Path,
    *,
    base: str | None = None,
    head: str | None = None,
    merge: str | None = None,
    extra: list[str] | None = None,
) -> list[str]:
    first_parent = cut.governance_commit or cut.base_commit
    assert first_parent is not None
    assert cut.head_commit is not None
    assert cut.merge_commit is not None
    return [
        "--repository",
        str(cut.repo),
        "--base-commit",
        base or first_parent,
        "--pr-head-commit",
        head or cut.head_commit,
        "--candidate-commit",
        merge or cut.merge_commit,
        "--report-dir",
        str(report_dir),
        *(extra or []),
    ]


def _prepare_cut(
    cut: Fixture,
    *,
    extra: dict[str, str] | None = None,
    break_cycle: bool = True,
    expected_changes: list[dict[str, Any]] | None = None,
) -> None:
    """统一搭建 production cut：head（可夹带 extra）+ base-owned contract。"""

    cut.set_head_ops(break_cycle=break_cycle, extra=extra)
    head = cut.build_head()
    head_tree = _tree_of(cut.repo, head)
    cut.add_contract(
        cut.compute_contract(
            head_tree, [["core.b", "core.a"]], expected_changes=expected_changes
        )
    )
    cut.build_head()
    cut.make_merge()


def _read_report(report_dir: Path, name: str) -> dict[str, Any]:
    """经 current.json manifest 读取当前代报告 JSON。"""
    manifest = json.loads((report_dir / "current.json").read_text(encoding="utf-8"))
    assert name in manifest["files"], f"manifest 未指向 {name}"
    return json.loads((report_dir / name).read_text(encoding="utf-8"))


def _assert_five_reports(report_dir: Path) -> None:
    """断言目录含 manifest 指向的五份原子报告与 COMMITTED marker。"""
    manifest = json.loads((report_dir / "current.json").read_text(encoding="utf-8"))
    assert manifest.get("committed") is True
    for name in REPORT_FILES:
        assert name in manifest["files"], f"manifest 缺少 {name}"
        assert (report_dir / name).is_file(), f"缺少报告 {name}"
        _read_report(report_dir, name)
        assert (
            manifest["files"][name]
            == hashlib.sha256((report_dir / name).read_bytes()).hexdigest()
        )
    from scripts.m1_gate_core import COMMIT_MARKER

    assert (report_dir / COMMIT_MARKER).is_file(), "缺少 COMMITTED marker"


@pytest.mark.parametrize(
    "tampered,keyword",
    [
        ({"scripts/m1_gate_core.py": "# tampered\n"}, "protected"),
        ({"scripts/m1_gate_probe.py": "# tampered\n"}, "protected"),
        ({"scripts/m1_gate_report.py": "# tampered\n"}, "protected"),
        ({".github/workflows/ci.yml": "name: tampered\n"}, "protected"),
        ({"_conf_schema.json": '{"tampered": true}\n'}, "protected"),
        ({"pyproject.toml": "[tool.tampered]\n"}, "deny-by-default"),
        ({"runtime_asset.txt": "tampered\n"}, "deny-by-default"),
    ],
)
def test_governance_deny_by_default_blocks(
    cut: Fixture, tmp_path: Path, tampered: dict[str, str], keyword: str
) -> None:
    """governance-only 修改受保护/未授权对象一律 exit 1。"""

    cut.set_head_ops(break_cycle=False, extra=tampered)
    cut.build_head()
    cut.make_merge()
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 1
    decision = _read_report(report_dir, "decision.json")
    assert decision["status"] == "blocked"
    assert any(keyword in reason for reason in decision["reasons"])


def test_production_change_touching_any_workflow_blocks(
    cut: Fixture, tmp_path: Path
) -> None:
    """生产 cut 夹带 .github/workflows/ci.yml（未保护具体名）必须 exit 1。"""

    _prepare_cut(cut, extra={".github/workflows/ci.yml": "name: tampered\n"})
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 1
    decision = _read_report(report_dir, "decision.json")
    assert decision["status"] == "blocked"
    assert any("protected" in reason for reason in decision["reasons"])


@pytest.mark.parametrize(
    "with_contract,extra_files,expected,status_assert",
    [
        (False, {"docs/AGENTS.md": "# docs\n"}, 0, "governance_only"),
        (True, {}, 0, "governance_only"),
        (True, {"docs/AGENTS.md": "# docs\n"}, 1, "blocked"),
    ],
)
def test_governance_only_boundary(
    cut: Fixture,
    tmp_path: Path,
    with_contract: bool,
    extra_files: dict[str, str],
    expected: int,
    status_assert: str,
) -> None:
    """governance 边界：docs-only/contract-only 通过，contract 夹带 docs 阻断。"""

    contract = cut.compute_contract(
        _tree_of(cut.repo, cut.base_commit or ""),
        [["core.b", "core.a"]],
        expected_changes=[_expected_b_change()],
    )
    extra = dict(extra_files)
    if with_contract:
        extra["scripts/m1_cuts/test-cut.json"] = json.dumps(
            contract, ensure_ascii=False
        )
    cut.set_head_ops(break_cycle=False, extra=extra)
    cut.build_head()
    cut.make_merge()
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == expected
    decision = _read_report(report_dir, "decision.json")
    assert decision["status"] == status_assert
    if expected == 1:
        assert any("contract-only" in reason for reason in decision["reasons"])


def test_production_missing_attestation_exit2_before_policy(
    cut: Fixture, tmp_path: Path
) -> None:
    """Blocker 2 回归：production 缺 attestation 在任何 policy 判断前 exit 2。

    即使候选同时违反 SCC/edge policy（break_cycle=False + 夹带 core/c.py），
    无 attestation 也必须 exit 2（而非 policy blocked exit 1）。
    """

    _prepare_cut(cut, break_cycle=False, extra={"core/c.py": "VALUE_C = 9\n"})
    report_dir = tmp_path / "reports"
    code = m1_gate.main(
        _cli_args(cut, report_dir)  # 不传 --probe-result / --attestation-nonce
    )
    assert code == 2
    decision = _read_report(report_dir, "decision.json")
    assert "error" in decision
    assert "attestation" in decision["error"]


def test_production_unauthenticated_attestation_exit2(
    cut: Fixture, tmp_path: Path
) -> None:
    """Blocker 1 回归：未认证 attestation（任意字符串身份/错 nonce/错签名）
    在 policy 判断前 exit 2。"""

    from tests.test_m1_gate_probe import _bound_probe_result

    _prepare_cut(cut)
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir(parents=True)
    payload = _bound_probe_result(
        cut, verdict="confirmed_removed", extra_fields={}, exit_code=0
    )
    payload["identity"] = "arbitrary-unverified-string"
    (probe_dir / "probe-result.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    report_dir = tmp_path / "reports"
    code = m1_gate.main(
        _cli_args(
            cut,
            report_dir,
            extra=[
                "--probe-result",
                str(probe_dir / "probe-result.json"),
                "--attestation-nonce",
                TEST_ATTESTATION_NONCE,
            ],
        )
    )
    assert code == 2
    decision = _read_report(report_dir, "decision.json")
    assert "identity" in decision["error"]


def test_governance_pass_then_production_usable(cut: Fixture, tmp_path: Path) -> None:
    """两阶段回归：governance contract-only pass -> 首个 production cut 可用。"""

    cut.set_head_ops()
    head = cut.build_head()
    contract = cut.compute_contract(_tree_of(cut.repo, head), [["core.b", "core.a"]])
    cut.set_head_ops(
        break_cycle=False,
        extra={
            "scripts/m1_cuts/test-cut.json": json.dumps(contract, ensure_ascii=False)
        },
    )
    cut.build_head()
    cut.make_merge()
    gov_report = tmp_path / "gov-reports"
    assert cut.run_gate(gov_report) == 0
    assert _read_report(gov_report, "decision.json")["status"] == "governance_only"

    cut.add_contract(contract)
    cut.set_head_ops()
    cut.build_head()
    cut.make_merge()
    prod_report = tmp_path / "prod-reports"
    code = cut.run_gate(prod_report)
    assert code == 0, f"expected production usable after governance, got {code}"
    decision = _read_report(prod_report, "decision.json")
    assert decision["status"] == "pass"
    assert decision["invariants"]["removed_edges_runtime_probe_confirmed"] is True


@pytest.mark.parametrize(
    "mutate,expected,keyword",
    [
        (lambda c: c.update({"base_commit": "not-a-commit"}), 2, "base_commit"),
        (lambda c: c.update({"base_commit": "0" * 40}), 1, "blocked"),
        (lambda c: c.update({"change_manifest_sha256": "0" * 64}), 2, "哈希"),
    ],
)
def test_governance_contract_invalid_variants(
    cut: Fixture, tmp_path: Path, mutate: Any, expected: int, keyword: str
) -> None:
    """governance contract 坏锚点/锚点不符/hash 破坏按语义 exit 1/2。"""

    contract = cut.compute_contract(
        _tree_of(cut.repo, cut.base_commit or ""),
        [["core.b", "core.a"]],
        expected_changes=[_expected_b_change()],
    )
    mutate(contract)
    cut.set_head_ops(
        break_cycle=False,
        extra={
            "scripts/m1_cuts/test-cut.json": json.dumps(contract, ensure_ascii=False)
        },
    )
    cut.build_head()
    cut.make_merge()
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == expected
    decision = _read_report(report_dir, "decision.json")
    if keyword == "blocked":
        assert decision["status"] == "blocked"
    else:
        assert keyword in decision["error"]


def test_scc_strict_decrease_passes_with_contract(cut: Fixture, tmp_path: Path) -> None:
    """SCC 2->1 且 contract 匹配 exit 0。"""
    _prepare_cut(cut)
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 0, f"expected pass, got {code}"
    _assert_five_reports(report_dir)
    decision = _read_report(report_dir, "decision.json")
    assert decision["status"] == "pass"
    assert decision["invariants"]["scc_strict_decrease"] is True
    assert decision["invariants"]["change_manifest_matches"] is True
    base_analysis = _read_report(report_dir, "base-analysis.json")
    candidate_analysis = _read_report(report_dir, "candidate-analysis.json")
    assert base_analysis["largest_scc"] == 2
    assert candidate_analysis["largest_scc"] == 1


def test_current_manifest_binds_run_meta(cut: Fixture, tmp_path: Path) -> None:
    """current.json 绑定 run_id/run_attempt、provenance、exit code 与五文件 hash。"""

    cut.set_head_ops(break_cycle=False, extra={"docs/AGENTS.md": "# docs\n"})
    cut.build_head()
    cut.make_merge()
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir, run_id="123", run_attempt="2")
    assert code == 0
    manifest = json.loads((report_dir / "current.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "123"
    assert manifest["run_attempt"] == "2"
    assert manifest["generation"] == report_dir.name
    assert manifest["exit_code"] == 0
    assert manifest["committed"] is True
    assert manifest["candidate_commit"] == cut.merge_commit
    assert set(manifest["files"]) == set(REPORT_FILES)
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((report_dir / name).read_bytes()).hexdigest() == digest


def test_scc_not_decreased_blocks(cut: Fixture, tmp_path: Path) -> None:
    """SCC 不降 exit 1。"""

    _prepare_cut(cut, break_cycle=False, extra={"core/c.py": "VALUE_C = 9\n"})
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 1
    decision = _read_report(report_dir, "decision.json")
    assert any("SCC 未严格下降" in reason for reason in decision["reasons"])


def test_contract_mismatch_blocks(cut: Fixture, tmp_path: Path) -> None:
    """额外路径洗白 exit 1。"""

    _prepare_cut(
        cut,
        extra={"core/c.py": "VALUE_C = 2\n"},
        expected_changes=[_expected_b_change()],
    )
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 1
    decision = _read_report(report_dir, "decision.json")
    assert decision["status"] == "blocked"


def test_protected_path_change_blocks(cut: Fixture, tmp_path: Path) -> None:
    """候选改 architecture.toml exit 1。"""

    _prepare_cut(cut, extra={"architecture.toml": "version = 1\n"})
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 1
    decision = _read_report(report_dir, "decision.json")
    assert decision["status"] == "blocked"


def test_contract_path_change_with_production_blocks(
    cut: Fixture, tmp_path: Path
) -> None:
    """生产 cut 改 contract exit 1。"""

    _prepare_cut(cut, extra={"scripts/m1_cuts/test-cut.json": '{"schema_version": 1}'})
    report_dir = tmp_path / "reports"
    code = cut.run_gate(report_dir)
    assert code == 1
    decision = _read_report(report_dir, "decision.json")
    assert decision["status"] == "blocked"


def test_invalid_sha_exit2(cut: Fixture, tmp_path: Path) -> None:
    """非法 SHA exit 2 且五报告。"""

    cut.set_head_ops()
    cut.build_head()
    cut.make_merge()
    report_dir = tmp_path / "reports"
    code = m1_gate.main(_cli_args(cut, report_dir, base="abc"))
    assert code == 2
    _assert_five_reports(report_dir)
    decision = _read_report(report_dir, "decision.json")
    assert "error" in decision


def test_m1_gate_source_files_untouched() -> None:
    """M1 gate 不修改任何 baseline 跟踪文件（frozen baseline 契约）。"""

    baseline = json.loads(
        (REPO_ROOT / "scripts/baselines/architecture.json").read_text(encoding="utf-8")
    )
    for path, record in baseline["files"].items():
        actual = REPO_ROOT / path
        if actual.is_file():
            lines = len(actual.read_text(encoding="utf-8").splitlines())
            assert lines <= int(record["lines"]), f"{path} 相对 baseline 增长"


def test_feature_gate_default_semantics_unchanged() -> None:
    """M1 gate 不得改变 feature gate 的 M2 缺省语义。"""

    source = (REPO_ROOT / "scripts/check_feature_gate.py").read_text(encoding="utf-8")
    assert "--stage" not in source
    assert "--m1" not in source
    assert "m1_cuts" not in source
