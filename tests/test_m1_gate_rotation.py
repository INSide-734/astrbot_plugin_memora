"""M1 verifier 对 contract 轮换的放行回归（AST-40 授权的最小接口变更）。

base-owned verifier 的 governance 路径现放行“base 恰有一份 contract、契约
绑定/范围校验通过的 contract-only 单文件 M”轮换；本文件用真实 rotation
merge candidate 经 `m1_gate.run_gate` 复核，与
`test_m1_contract_cut.py` 的 validator 复验互为独立复核。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.m1_gate_core as m1_gate_core
from tests.test_m1_contract_cut import (
    CONTRACT_PATH,
    RotFixture,
    _init_repo,
    _setup_contract,
    _write,
)
from tests.test_m1_gate import _make_merge, git
from tests.test_m1_gate_probe import TEST_ATTESTOR_PUBLIC


@pytest.fixture()
def rot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RotFixture:
    """连续 cut fixture（测试私钥 pinned 到 verifier）。"""
    monkeypatch.setattr(m1_gate_core, "M1_ATTESTOR_PUBLIC_KEY", TEST_ATTESTOR_PUBLIC)
    repo = tmp_path / "repo"
    _init_repo(repo)
    fixture = RotFixture(repo, tmp_path)
    fixture.build_base()
    return fixture


def _rotation_merge_candidate(
    rot: RotFixture,
    contract_bytes: bytes,
    *,
    branch: str = "rot-real",
    message: str = "rotation merge candidate",
) -> tuple[str, str, str]:
    """构造真实 rotation merge candidate，返回 (base, head, merge)。"""
    assert rot.main_head is not None
    base = rot.main_head
    git(rot.repo, "checkout", "-q", "-b", branch, base)
    _write(rot.repo, CONTRACT_PATH, contract_bytes.decode("utf-8"))
    git(rot.repo, "add", "-A")
    git(rot.repo, "commit", "-q", "--allow-empty", "-m", message)
    head = git(rot.repo, "rev-parse", "HEAD").stdout.strip()
    merge = _make_merge(rot.repo, base, head, "rotation merge")
    return base, head, merge


def test_verifier_passes_authorized_rotation(rot: RotFixture) -> None:
    """验收见证：真实 rotation merge candidate 经 base-owned verifier
    复核通过（授权的最小 verifier 接口变更已落地）。

    此前该场景 exit 2（governance 路径要求 base 无 contract）；授权变更
    后 verifier 放行“base 恰有一份 contract、契约绑定/范围校验通过的
    contract-only 单文件 M”轮换。本测试与 validator 复验互为独立复核。
    """

    current_bytes, contract = _setup_contract(rot)
    base, head, merge = _rotation_merge_candidate(
        rot, json.dumps(contract, ensure_ascii=False).encode("utf-8")
    )
    report_dir = rot.work / "rot-gate-reports"
    code = rot.run_gate(report_dir, base=base, head=head, candidate=merge, probe=None)
    assert code == 0, "授权后 verifier 必须放行合法轮换"
    decision = json.loads((report_dir / "decision.json").read_text(encoding="utf-8"))
    assert decision["status"] == "governance_only"
    assert decision["invariants"]["governance_contract_valid"] is True
    # 反证：现役 contract 原样提交（自轮换）不得被 verifier 放行
    base2, head2, merge2 = _rotation_merge_candidate(
        rot, current_bytes, branch="rot-self"
    )
    report2 = rot.work / "rot-gate-reports-2"
    code2 = rot.run_gate(report2, base=base2, head=head2, candidate=merge2, probe=None)
    assert code2 == 2


def test_verifier_rejects_rotation_wrong_binding(rot: RotFixture) -> None:
    """verifier 侧轮换绑定复核：新 contract 不绑定当前 base 时 exit 1。"""

    _, contract = _setup_contract(rot)
    mutated = dict(contract)
    mutated["base_commit"] = "0" * 40
    base, head, merge = _rotation_merge_candidate(
        rot, json.dumps(mutated, ensure_ascii=False).encode("utf-8")
    )
    report_dir = rot.work / "rot-gate-reports-bad"
    code = rot.run_gate(report_dir, base=base, head=head, candidate=merge, probe=None)
    assert code == 1
    decision = json.loads((report_dir / "decision.json").read_text(encoding="utf-8"))
    assert decision["status"] == "blocked"
    assert any("base_commit" in reason for reason in decision["reasons"])
