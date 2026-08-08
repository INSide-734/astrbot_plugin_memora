"""M1 canonical contract 生成/验证与连续 cut 轮换的聚焦测试（AST-40）。

覆盖连续 cut 主 fixture（genesis contract -> production 1 -> rotation ->
production 2，全部经真实 Git 对象与 verifier 复核）、rotation grammar 的
非法反例（错 base/tree/facts、非 contract-only、自轮换、删除/双 contract、
篡改 manifest）以及生成器确定性与已提交 genesis contract 的可复现性。

不修改任何 M1 verifier 判定逻辑；production cut 的 attestation 与
tests/test_m1_gate_probe 同款测试私钥签发。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

import scripts.check_m1_gate as m1_gate
import scripts.m1_contract_cut as cut_tool
import scripts.m1_gate_core as m1_gate_core
import scripts.m1_gate_probe as m1_gate_probe
from scripts.m1_contract_cut import _base_facts, _blob_oid, compute_manifest_entry
from scripts.m1_gate_core import (
    evidence_sha256,
    git_commit_tree,
)
from tests.test_m1_gate import (
    REPO_ROOT,
    TEST_ATTESTATION_NONCE,
    _make_merge,
    _tree_of,
    git,
)
from tests.test_m1_gate_probe import TEST_ATTESTOR_PUBLIC, _rsa_sign

CONTRACT_PATH = "scripts/m1_cuts/test-cut.json"
GENESIS_PATH = "scripts/m1_cuts/m1-genesis.json"
GENESIS_INPUT = "tests/fixtures/m1_contract/ast-28-genesis.changes.json"
GENESIS_MANIFEST = "tests/fixtures/m1_contract/ast-28-genesis.manifest.json"
REMOVED_EDGE_1 = ["core.b", "core.c"]
REMOVED_EDGE_2 = ["core.c", "core.a"]


def _init_repo(repo: Path) -> None:
    """初始化 fixture 仓库并写入最小 architecture.toml（与 M1 gate 测试同款）。"""
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "test")
    git(repo, "config", "user.email", "test@example.com")
    shutil.copy2(REPO_ROOT / "architecture.toml", repo / "architecture.toml")
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


def _probe_payload(
    repo: Path,
    *,
    base_commit: str,
    head_commit: str,
    candidate_commit: str,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """构造完整认证证据（测试私钥签发，与 M1 gate 测试同款口径）。"""
    contract_oid = git(
        repo, "rev-parse", f"{base_commit}:{CONTRACT_PATH}"
    ).stdout.strip()
    edges_checked = [
        {
            "edge": list(edge),
            "verdict": "confirmed_removed",
            "loaded_targets": [],
            "error": None,
        }
        for edge in contract["expected_removed_edges"]
    ]
    expected_edges = [list(edge) for edge in contract["expected_removed_edges"]]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "probe_version": m1_gate_probe.M1_PROBE_VERSION,
        "attestation": "os_level_observer_attestation",
        "identity": "os_level_observer",
        "nonce": TEST_ATTESTATION_NONCE,
        "image": m1_gate_probe.PROBE_IMAGE,
        "base_commit": base_commit,
        "pr_head_commit": head_commit,
        "candidate_commit": candidate_commit,
        "candidate_tree": _tree_of(repo, candidate_commit),
        "contract_path": CONTRACT_PATH,
        "contract_oid": contract_oid,
        "expected_edges": expected_edges,
        "edges_checked": edges_checked,
        "all_edges_confirmed": True,
        "exit_code": 0,
        "environment": {
            "isolated_domain": "docker",
            "network": "none",
            "read_only_inputs": True,
            "non_root": True,
            "no_new_privileges": True,
            "secrets": False,
        },
    }
    payload["evidence_sha256"] = evidence_sha256(
        payload["expected_edges"], edges_checked, 0
    )
    payload["signature"] = _rsa_sign(payload)
    return payload


class RotFixture:
    """连续 cut 场景：3-SCC base -> governance(contract) -> production 1
    -> rotation(contract) -> production 2，全部经真实 Git 对象。"""

    def __init__(self, repo: Path, work: Path) -> None:
        self.repo = repo
        self.work = work
        self.base_commit: str | None = None
        self.base_tree: str | None = None
        self.main_head: str | None = None

    def build_base(self) -> None:
        """建立 3-SCC（a->b, a->c, b->c, c->a）的 base。"""
        _write(self.repo, "core/__init__.py", "")
        _write(self.repo, "core/a.py", "from core import b, c\nVALUE_A = 1\n")
        _write(self.repo, "core/b.py", "from core import c\nVALUE_B = 1\n")
        _write(self.repo, "core/c.py", "from core import a\nVALUE_C = 1\n")
        self.base_commit = _commit_all(self.repo, "base with 3-SCC")
        self.base_tree = _tree_of(self.repo, self.base_commit)
        self.main_head = self.base_commit

    def generate_contract(
        self,
        base_commit: str,
        pr_head: str,
        *,
        cut_id: str,
        output: Path,
        manifest_output: Path,
        removed_edges: list[list[str]] | None = None,
    ) -> int:
        """经 CLI 调用 generator（与 reviewer 复验同一入口）。"""
        args = [
            "generate",
            "--repository",
            str(self.repo),
            "--base-commit",
            base_commit,
            "--pr-head",
            pr_head,
            "--cut-id",
            cut_id,
            "--output",
            str(output),
            "--manifest-output",
            str(manifest_output),
        ]
        if removed_edges is not None:
            edges_file = self.work / f"{cut_id}-removed-edges.json"
            edges_file.write_text(
                json.dumps(removed_edges, ensure_ascii=False), encoding="utf-8"
            )
            args += ["--expected-removed-edges-file", str(edges_file)]
        return cut_tool.main(args)

    def add_contract_on_main(self, contract_bytes: bytes) -> str:
        """把 contract 落进 main（模拟 governance/rotation merge 落库）。"""
        git(self.repo, "checkout", "-q", "main")
        _write(self.repo, CONTRACT_PATH, contract_bytes.decode("utf-8"))
        self.main_head = _commit_all(self.repo, "governance: cut contract")
        return self.main_head

    def head_from_main(self, ops: dict[str, str], branch: str) -> str:
        """从当前 main 建 PR head 并应用文件操作。"""
        assert self.main_head is not None
        git(self.repo, "checkout", "-q", "-b", branch, self.main_head)
        for path, content in ops.items():
            _write(self.repo, path, content)
        return _commit_all(self.repo, f"head: {branch}")

    def merge(self, base: str, head: str, message: str) -> str:
        """构造父关系为 (base, head) 的 merge candidate 并落到 main。"""
        merge = _make_merge(self.repo, base, head, message)
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "merge", "-q", "--ff-only", merge)
        self.main_head = merge
        return merge

    def run_gate(
        self,
        report_dir: Path,
        *,
        base: str,
        head: str,
        candidate: str,
        probe: dict[str, Any] | None,
    ) -> int:
        """以 CLI 形式运行 M1 gate（production cut 附测试私钥证据）。"""
        probe_dir = self.work / "probe"
        probe_dir.mkdir(parents=True, exist_ok=True)
        args = [
            "--repository",
            str(self.repo),
            "--base-commit",
            base,
            "--pr-head-commit",
            head,
            "--candidate-commit",
            candidate,
            "--report-dir",
            str(report_dir),
            "--attestation-nonce",
            TEST_ATTESTATION_NONCE,
        ]
        if probe is not None:
            probe_path = probe_dir / "probe-result.json"
            probe_path.write_text(
                json.dumps(probe, ensure_ascii=False), encoding="utf-8"
            )
            args += ["--probe-result", str(probe_path)]
        return m1_gate.main(args)

    def validate_rotation(
        self,
        base: str,
        candidate: str,
        *,
        manifest: Path | None = None,
        output: Path | None = None,
    ) -> int:
        """经 CLI 调用 rotation validator。"""
        args = [
            "validate-rotation",
            "--repository",
            str(self.repo),
            "--base-commit",
            base,
            "--candidate-commit",
            candidate,
        ]
        if manifest is not None:
            args += ["--manifest", str(manifest)]
        if output is not None:
            args += ["--output", str(output)]
        return cut_tool.main(args)


@pytest.fixture()
def rot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RotFixture:
    """连续 cut fixture（测试私钥 pinned 到 verifier）。"""
    monkeypatch.setattr(m1_gate_core, "M1_ATTESTOR_PUBLIC_KEY", TEST_ATTESTOR_PUBLIC)
    repo = tmp_path / "repo"
    _init_repo(repo)
    fixture = RotFixture(repo, tmp_path)
    fixture.build_base()
    return fixture


def test_generate_is_deterministic_and_binds_base(rot: RotFixture) -> None:
    """同输入两次生成字节一致，且精确绑定 base commit/tree/facts。"""
    assert rot.base_commit is not None
    head = rot.head_from_main({"core/b.py": "VALUE_B = 2\n"}, "pr-1")
    first = rot.work / "c1.json"
    first_manifest = rot.work / "m1.json"
    second = rot.work / "c1b.json"
    assert (
        rot.generate_contract(
            rot.base_commit,
            head,
            cut_id="test-cut",
            output=first,
            manifest_output=first_manifest,
        )
        == 0
    )
    assert (
        rot.generate_contract(
            rot.base_commit,
            head,
            cut_id="test-cut",
            output=second,
            manifest_output=rot.work / "m1b.json",
        )
        == 0
    )
    assert first.read_bytes() == second.read_bytes()
    contract = json.loads(first.read_text(encoding="utf-8"))
    assert contract["base_commit"] == rot.base_commit
    assert contract["base_tree"] == rot.base_tree
    assert (
        contract["base_graph_facts_sha256"]
        == _base_facts(rot.repo, rot.base_tree or "")["facts_sha256"]
    )
    assert contract["schema_version"] == m1_gate_core.SCHEMA_VERSION
    manifest = json.loads(first_manifest.read_text(encoding="utf-8"))
    assert manifest["kind"] == "genesis"
    assert manifest["old_contract_oid"] is None
    assert len(manifest["new_contract_oid"]) == 40
    assert manifest["contract_path"] == CONTRACT_PATH
    assert (
        manifest["new_contract_sha256"]
        == hashlib.sha256(first.read_bytes()).hexdigest()
    )


def test_committed_genesis_is_valid_and_reproducible(tmp_path: Path) -> None:
    """仓库内已提交 genesis contract：schema/锚点有效、可复现、manifest 一致。

    CI 的 backend checkout 是默认浅克隆（`.github/workflows/ci.yml` 未配
    fetch-depth: 0），缺少历史 base 对象时本测试明确跳过；完整验证在
    full-history checkout 上执行（本地与任何完整克隆环境）。
    """
    from scripts.m1_gate_core import (
        _validate_contract_anchors,
        _validate_contract_payload,
        git_object_exists,
    )

    repo_root = REPO_ROOT
    committed = (repo_root / GENESIS_PATH).read_bytes()
    payload = json.loads(committed.decode("utf-8"))
    _validate_contract_payload(payload, path=GENESIS_PATH)
    base_commit = payload["base_commit"]
    if not git_object_exists(repo_root, f"{base_commit}^{{commit}}"):
        pytest.skip(
            "当前 checkout 缺少 genesis 历史 base 对象（浅克隆）；"
            "完整复现需 full-history checkout"
        )
    base_tree = git_commit_tree(repo_root, base_commit)
    _validate_contract_anchors(
        repo_root, payload, base_commit=base_commit, path=GENESIS_PATH
    )
    assert payload["base_tree"] == base_tree
    assert (
        payload["base_graph_facts_sha256"]
        == _base_facts(repo_root, base_tree)["facts_sha256"]
    )
    regenerated = tmp_path / "regenerated.json"
    regen_manifest = tmp_path / "regen-manifest.json"
    assert (
        cut_tool.main(
            [
                "generate",
                "--repository",
                str(repo_root),
                "--base-commit",
                base_commit,
                "--expected-changes-file",
                str(repo_root / GENESIS_INPUT),
                "--cut-id",
                "m1-genesis",
                "--output",
                str(regenerated),
                "--manifest-output",
                str(regen_manifest),
            ]
        )
        == 0
    )
    assert regenerated.read_bytes() == committed, "genesis 必须可从 fixture 输入复现"
    expected_manifest = compute_manifest_entry(
        kind="genesis",
        contract_path=GENESIS_PATH,
        old_contract_oid=None,
        new_contract_oid=_blob_oid(repo_root, committed),
        new_contract_sha256=hashlib.sha256(committed).hexdigest(),
        contract=payload,
    )
    assert json.loads(regen_manifest.read_text(encoding="utf-8")) == expected_manifest
    committed_manifest = json.loads(
        (repo_root / GENESIS_MANIFEST).read_text(encoding="utf-8")
    )
    assert committed_manifest == expected_manifest


def test_full_sequence_genesis_prod1_rotation_prod2(rot: RotFixture) -> None:
    """连续 cut 主 fixture：genesis -> production 1 -> rotation -> production 2。"""
    assert rot.base_commit is not None
    head1 = rot.head_from_main({"core/b.py": "VALUE_B = 2\n"}, "pr-1")
    c1_file = rot.work / "c1.json"
    m1_file = rot.work / "m1.json"
    assert (
        rot.generate_contract(
            rot.base_commit,
            head1,
            cut_id="test-cut",
            output=c1_file,
            manifest_output=m1_file,
            removed_edges=[REMOVED_EDGE_1],
        )
        == 0
    )
    contract1 = json.loads(c1_file.read_text(encoding="utf-8"))
    assert contract1["expected_removed_edges"] == [REMOVED_EDGE_1]

    # genesis governance PR：contract-only 新增，verifier 通过
    git(rot.repo, "checkout", "-q", "-b", "gov-1", rot.base_commit)
    _write(rot.repo, CONTRACT_PATH, c1_file.read_text(encoding="utf-8"))
    gov_head = _commit_all(rot.repo, "governance: genesis contract")
    gov_merge = _make_merge(rot.repo, rot.base_commit, gov_head, "gov merge")
    gov_report = rot.work / "gov-reports"
    assert (
        rot.run_gate(
            gov_report,
            base=rot.base_commit,
            head=gov_head,
            candidate=gov_merge,
            probe=None,
        )
        == 0
    )
    gov_main = rot.add_contract_on_main(c1_file.read_bytes())

    # production 1：base 为 governance 落库提交，verifier + probe 通过
    head1b = rot.head_from_main({"core/b.py": "VALUE_B = 2\n"}, "pr-1b")
    merge1 = rot.merge(gov_main, head1b, "prod 1 merge")
    probe1 = _probe_payload(
        rot.repo,
        base_commit=gov_main,
        head_commit=head1b,
        candidate_commit=merge1,
        contract=contract1,
    )
    prod1_report = rot.work / "prod1-reports"
    assert (
        rot.run_gate(
            prod1_report, base=gov_main, head=head1b, candidate=merge1, probe=probe1
        )
        == 0
    )
    base_after_prod1 = rot.main_head
    assert base_after_prod1 is not None

    # rotation：绑定新 base 的 contract2（生成器自动判定 kind=rotation）
    head2 = rot.head_from_main({"core/c.py": "VALUE_C = 2\n"}, "pr-2")
    c2_file = rot.work / "c2.json"
    m2_file = rot.work / "m2.json"
    assert (
        rot.generate_contract(
            base_after_prod1,
            head2,
            cut_id="test-cut",
            output=c2_file,
            manifest_output=m2_file,
            removed_edges=[REMOVED_EDGE_2],
        )
        == 0
    )
    contract2 = json.loads(c2_file.read_text(encoding="utf-8"))
    assert contract2["base_commit"] == base_after_prod1
    assert contract2["expected_removed_edges"] == [REMOVED_EDGE_2]
    gen_manifest2 = json.loads(m2_file.read_text(encoding="utf-8"))
    assert gen_manifest2["kind"] == "rotation"
    assert gen_manifest2["old_contract_oid"]

    # 轮换 PR：单文件 M 替换，validator 通过并输出 manifest
    git(rot.repo, "checkout", "-q", "-b", "rot", base_after_prod1)
    _write(rot.repo, CONTRACT_PATH, c2_file.read_text(encoding="utf-8"))
    rot_head = _commit_all(rot.repo, "rotation: contract 2")
    validated_manifest = rot.work / "rot-manifest.json"
    assert (
        rot.validate_rotation(base_after_prod1, rot_head, output=validated_manifest)
        == 0
    )
    rot_manifest = json.loads(validated_manifest.read_text(encoding="utf-8"))
    assert rot_manifest["kind"] == "rotation"
    assert rot_manifest["contract_path"] == CONTRACT_PATH
    assert rot_manifest["new_contract_oid"] != rot_manifest["old_contract_oid"]
    assert rot_manifest == gen_manifest2, "generate 与 validate 的 manifest 必须一致"

    # 复验：同一 manifest 再核验通过；篡改后拒绝
    assert (
        rot.validate_rotation(base_after_prod1, rot_head, manifest=validated_manifest)
        == 0
    )
    tampered = rot.work / "tampered-manifest.json"
    tampered.write_text(
        json.dumps(
            {**rot_manifest, "new_contract_sha256": "0" * 64}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    assert rot.validate_rotation(base_after_prod1, rot_head, manifest=tampered) == 1

    # rotation 落库后 production 2：verifier + probe 通过
    rot_main = rot.add_contract_on_main(c2_file.read_bytes())
    head2b = rot.head_from_main({"core/c.py": "VALUE_C = 2\n"}, "pr-2b")
    merge2 = rot.merge(rot_main, head2b, "prod 2 merge")
    probe2 = _probe_payload(
        rot.repo,
        base_commit=rot_main,
        head_commit=head2b,
        candidate_commit=merge2,
        contract=contract2,
    )
    prod2_report = rot.work / "prod2-reports"
    assert (
        rot.run_gate(
            prod2_report, base=rot_main, head=head2b, candidate=merge2, probe=probe2
        )
        == 0
    )
    decision2 = json.loads((prod2_report / "decision.json").read_text(encoding="utf-8"))
    assert decision2["status"] == "pass"
    assert decision2["invariants"]["contract_binds_current_base"] is True


def _setup_contract(rot: RotFixture) -> tuple[bytes, dict[str, Any]]:
    """先在 main 落一份绑定原 base 的 contract，再生成绑定当前 main 的
    轮换目标 contract（返回 main 现役 contract 字节与轮换目标载荷）。"""
    assert rot.base_commit is not None
    head0 = rot.head_from_main({"core/b.py": "VALUE_B = 2\n"}, "pr-setup0")
    out0 = rot.work / "setup0.json"
    assert (
        rot.generate_contract(
            rot.base_commit,
            head0,
            cut_id="test-cut",
            output=out0,
            manifest_output=rot.work / "setup0-m.json",
            removed_edges=[REMOVED_EDGE_1],
        )
        == 0
    )
    rot.add_contract_on_main(out0.read_bytes())
    current_bytes = out0.read_bytes()
    assert rot.main_head is not None
    head1 = rot.head_from_main({"core/c.py": "VALUE_C = 2\n"}, "pr-setup1")
    out1 = rot.work / "setup1.json"
    assert (
        rot.generate_contract(
            rot.main_head,
            head1,
            cut_id="test-cut",
            output=out1,
            manifest_output=rot.work / "setup1-m.json",
            removed_edges=[REMOVED_EDGE_2],
        )
        == 0
    )
    return current_bytes, json.loads(out1.read_text(encoding="utf-8"))


def _rotation_candidate(
    rot: RotFixture,
    contract_bytes: bytes,
    *,
    extra: dict[str, str] | None = None,
    delete: bool = False,
) -> str:
    """从当前 main 构造轮换候选提交（默认单文件 M 替换）。"""
    assert rot.main_head is not None
    git(rot.repo, "checkout", "-q", "-b", "rot-bad", rot.main_head)
    if delete:
        (rot.repo / CONTRACT_PATH).unlink(missing_ok=True)
    else:
        _write(rot.repo, CONTRACT_PATH, contract_bytes.decode("utf-8"))
    for path, content in (extra or {}).items():
        _write(rot.repo, path, content)
    git(rot.repo, "add", "-A")
    git(rot.repo, "commit", "-q", "--allow-empty", "-m", "rotation candidate")
    return git(rot.repo, "rev-parse", "HEAD").stdout.strip()


@pytest.mark.parametrize(
    "mutate,keyword,expected",
    [
        (lambda c: c.update({"base_commit": "0" * 40}), "base_commit", 1),
        (lambda c: c.update({"base_tree": "1" * 40}), "base_tree", 1),
        (
            lambda c: c.update({"base_graph_facts_sha256": "0" * 64}),
            "base_graph_facts_sha256",
            1,
        ),
        (
            lambda c: c.update({"change_manifest_sha256": "0" * 64}),
            "哈希",
            2,
        ),
    ],
)
def test_rotation_rejects_wrong_binding(
    rot: RotFixture,
    capsys: pytest.CaptureFixture[str],
    mutate: Any,
    keyword: str,
    expected: int,
) -> None:
    """错 base/tree/facts 拒绝（exit 1）；内部 canonical hash 破坏 exit 2。"""
    _, contract = _setup_contract(rot)
    mutated = dict(contract)
    mutate(mutated)
    candidate = _rotation_candidate(
        rot, json.dumps(mutated, ensure_ascii=False).encode("utf-8")
    )
    assert rot.main_head is not None
    code = rot.validate_rotation(rot.main_head, candidate)
    assert code == expected
    captured = capsys.readouterr().out
    assert keyword in captured or captured == ""


def test_rotation_rejects_extra_paths(rot: RotFixture) -> None:
    """非 contract-only（夹带 docs）拒绝。"""
    original, _ = _setup_contract(rot)
    candidate = _rotation_candidate(rot, original, extra={"docs/AGENTS.md": "# t\n"})
    assert rot.main_head is not None
    assert rot.validate_rotation(rot.main_head, candidate) == 1


def test_rotation_rejects_self_rotation(rot: RotFixture) -> None:
    """自轮换（新 contract 与旧相同）拒绝。"""
    original, _ = _setup_contract(rot)
    candidate = _rotation_candidate(rot, original)
    assert rot.main_head is not None
    assert rot.validate_rotation(rot.main_head, candidate) == 1


def test_rotation_rejects_delete(rot: RotFixture) -> None:
    """删除 contract（candidate 无 contract）拒绝。"""
    _setup_contract(rot)
    candidate = _rotation_candidate(rot, b"", delete=True)
    assert rot.main_head is not None
    assert rot.validate_rotation(rot.main_head, candidate) == 1


def test_rotation_rejects_two_contracts(rot: RotFixture) -> None:
    """candidate 出现第二份 contract 属结构错误（fail-closed exit 2）。"""
    original, _ = _setup_contract(rot)
    candidate = _rotation_candidate(
        rot, original, extra={"scripts/m1_cuts/second.json": original.decode("utf-8")}
    )
    assert rot.main_head is not None
    assert rot.validate_rotation(rot.main_head, candidate) == 2


def test_rotation_rejects_tampered_manifest(rot: RotFixture) -> None:
    """篡改 manifest（与确定性重算不一致）拒绝。"""
    original, contract = _setup_contract(rot)
    candidate = _rotation_candidate(
        rot, json.dumps(contract, ensure_ascii=False).encode("utf-8")
    )
    assert rot.main_head is not None
    fake = rot.work / "fake-manifest.json"
    fake.write_text(
        json.dumps({"kind": "rotation", "tampered": True}), encoding="utf-8"
    )
    assert rot.validate_rotation(rot.main_head, candidate, manifest=fake) == 1


def test_verifier_rejects_rotation_until_authorized_change(
    rot: RotFixture,
) -> None:
    """High 证据回归：真实 rotation merge candidate 经 base-owned verifier
    复核仍 exit 2（governance 路径要求 base 无 contract）。

    本测试固化 AST-40 的已知缺口：在获得 verifier 最小接口变更授权并落地
    后，此测试应从 exit 2 翻转为 exit 0，成为“rotation 被 verifier 连续
    复验”的验收见证。
    """

    _, contract = _setup_contract(rot)
    assert rot.main_head is not None
    git(rot.repo, "checkout", "-q", "-b", "rot-real", rot.main_head)
    _write(rot.repo, CONTRACT_PATH, json.dumps(contract, ensure_ascii=False))
    rot_head = _commit_all(rot.repo, "rotation merge candidate")
    merge = _make_merge(rot.repo, rot.main_head, rot_head, "rotation merge")
    report_dir = rot.work / "rot-gate-reports"
    code = rot.run_gate(
        report_dir,
        base=rot.main_head,
        head=rot_head,
        candidate=merge,
        probe=None,
    )
    assert code == 2, "授权变更落地前 verifier 必须 fail-closed（exit 2）"
    decision = json.loads((report_dir / "decision.json").read_text(encoding="utf-8"))
    assert "base 不得已有 contract" in decision["error"]


def test_generate_requires_input_and_valid_sha(rot: RotFixture) -> None:
    """generate 缺输入/坏 SHA 一律 exit 2。"""
    assert rot.base_commit is not None
    out = rot.work / "x.json"
    assert (
        cut_tool.main(
            [
                "generate",
                "--repository",
                str(rot.repo),
                "--base-commit",
                rot.base_commit,
                "--output",
                str(out),
            ]
        )
        == 2
    )
    assert (
        cut_tool.main(
            [
                "generate",
                "--repository",
                str(rot.repo),
                "--base-commit",
                "abc",
                "--pr-head",
                rot.base_commit,
                "--output",
                str(out),
            ]
        )
        == 2
    )


def test_generate_rejects_bad_removed_edges(rot: RotFixture) -> None:
    """expected_removed_edges 形状非法 exit 2。"""
    assert rot.base_commit is not None
    bad = rot.work / "bad-edges.json"
    bad.write_text('["not", "a", "list", "of", "edges"]', encoding="utf-8")
    out = rot.work / "x.json"
    assert (
        cut_tool.main(
            [
                "generate",
                "--repository",
                str(rot.repo),
                "--base-commit",
                rot.base_commit,
                "--expected-changes-file",
                str(REPO_ROOT / GENESIS_INPUT),
                "--expected-removed-edges-file",
                str(bad),
                "--output",
                str(out),
            ]
        )
        == 2
    )
