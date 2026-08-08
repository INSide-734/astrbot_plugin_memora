"""M1 removed-edge 探针定向测试：隔离域构造、观察者证据绑定与 gate 复核。"""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
from pathlib import Path
from typing import Any, Mapping

import pytest

import scripts.check_m1_gate as m1_gate
import scripts.m1_gate_core as m1_gate_core
import scripts.m1_gate_probe as m1_gate_probe
from scripts.m1_gate_core import evidence_sha256
from tests.test_m1_gate import (
    TEST_ATTESTATION_NONCE,
    Fixture,
    _cli_args,
    _init_repo,
    _prepare_cut,
    _read_report,
    _tree_of,
    git,
    local_docker_runner,
)

PROBE_IMAGE = m1_gate_probe.PROBE_IMAGE


def _miller_rabin(n: int, rounds: int, rng: Any) -> bool:
    """Miller-Rabin 素性测试（测试用密钥生成）。"""
    if n < 2:
        return False
    for small in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % small == 0:
            return n == small
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        a = rng.randrange(2, n - 1)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits: int, rng: Any) -> int:
    while True:
        candidate = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _miller_rabin(candidate, 16, rng):
            return candidate


def _generate_rsa_keypair(bits: int = 2048, seed: int = 42) -> dict[str, str]:
    """测试进程内生成 RSA 密钥对（纯 stdlib，固定种子可复现）。"""
    rng = random.Random(seed)
    p = _gen_prime(bits // 2, rng)
    q = _gen_prime(bits // 2, rng)
    while q == p:
        q = _gen_prime(bits // 2, rng)
    n = p * q
    e = 65537
    d = pow(e, -1, (p - 1) * (q - 1))
    return {"n": hex(n)[2:], "e": hex(e)[2:], "d": hex(d)[2:]}


def _rsa_sign(payload: Mapping[str, Any]) -> str:
    """测试私钥签名（与 verifier 的 pinned 公钥验签配套）。"""
    from scripts.m1_gate_core import _SHA256_DIGEST_INFO, attestation_signed_payload

    n = int(TEST_ATTESTOR_KEY["n"], 16)
    d = int(TEST_ATTESTOR_KEY["d"], 16)
    size = (n.bit_length() + 7) // 8
    digest = hashlib.sha256(attestation_signed_payload(payload)).digest()
    em = (
        b"\x00"
        + b"\xff" * (size - 3 - len(_SHA256_DIGEST_INFO) - len(digest))
        + b"\x00"
        + _SHA256_DIGEST_INFO
        + digest
    )
    signature = pow(int.from_bytes(em, "big"), d, n)
    return signature.to_bytes(size, "big").hex()


TEST_ATTESTOR_KEY = _generate_rsa_keypair()
TEST_ATTESTOR_PUBLIC = {"n": TEST_ATTESTOR_KEY["n"], "e": TEST_ATTESTOR_KEY["e"]}


@pytest.fixture()
def cut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    """标准 2-SCC -> 1-SCC 场景（探针子进程用本地 runner 替代 docker）。"""

    monkeypatch.setattr(m1_gate_probe, "_run_docker", local_docker_runner)
    monkeypatch.setattr(m1_gate_core, "M1_ATTESTOR_PUBLIC_KEY", TEST_ATTESTOR_PUBLIC)
    repo = tmp_path / "repo"
    _init_repo(repo)
    fixture = Fixture(repo, tmp_path)
    fixture.build_base()
    return fixture


def _docker_available() -> bool:
    """检测本机是否可执行 docker（真实隔离域集成测试用）。"""

    try:
        completed = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=15,
        )
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def test_docker_command_isolates_candidate_execution(tmp_path: Path) -> None:
    """隔离域命令必须含 network none/只读/非 root/no-new-privileges/无 env。"""

    candidate = tmp_path / "candidate"
    candidate.mkdir()
    harness = tmp_path / "harness"
    harness.mkdir()
    command = m1_gate_probe.docker_probe_command(
        candidate, harness / "probe_harness.py", "core.a", "core.b"
    )
    assert command[0] == "docker" and command[1] == "run"
    assert "--network" in command and command[command.index("--network") + 1] == "none"
    assert "--user" in command and command[command.index("--user") + 1] == "65534:65534"
    assert "--security-opt" in command
    assert command[command.index("--security-opt") + 1] == "no-new-privileges:true"
    assert "--read-only" in command
    assert (
        "--tmpfs" in command
        and command[command.index("--tmpfs") + 1] == "/tmp:size=16m"
    )
    assert f"{candidate}:/candidate:ro" in command
    assert f"{harness}:/harness:ro" in command
    assert PROBE_IMAGE in command
    assert "-I" in command and "-B" in command
    assert "--env" not in command and "--env-file" not in command
    assert "GITHUB_TOKEN" not in command and "SECRET" not in command


def test_docker_command_enforces_resource_limits(tmp_path: Path) -> None:
    """隔离域命令必须含 pids/memory/CPU/FD/tmpfs 上限与 cap-drop。"""

    candidate = tmp_path / "candidate"
    candidate.mkdir()
    harness = tmp_path / "harness"
    harness.mkdir()
    command = m1_gate_probe.docker_probe_command(
        candidate, harness / "probe_harness.py", "core.a", "core.b"
    )
    assert "--cap-drop" in command and command[command.index("--cap-drop") + 1] == "ALL"
    assert "--pids-limit" in command
    assert "--memory" in command and command[command.index("--memory") + 1] == "256m"
    assert "--memory-swap" in command and "--cpus" in command
    assert "--ulimit" in command and "/tmp:size=16m" in command
    assert m1_gate_probe._PROBE_OUTPUT_CAP == 64 * 1024


def test_probe_uses_full_object_history(cut: Fixture, tmp_path: Path) -> None:
    """Blocker 1 回归：探针要求完整对象历史，浅克隆必须 exit 2。"""

    cut.set_head_ops()
    head = cut.build_head()
    head_tree = _tree_of(cut.repo, head)
    cut.add_contract(cut.compute_contract(head_tree, [["core.b", "core.a"]]))
    cut.build_head()
    cut.make_merge()
    # 构造浅克隆副本（file:// 强制真实浅克隆），探针必须在 provenance 拒绝
    shallow = tmp_path / "shallow"
    git(
        cut.repo,
        "clone",
        "--depth=1",
        "file://" + str(cut.repo.resolve()),
        str(shallow),
    )
    shallow_cut = Fixture(shallow, tmp_path)
    shallow_cut.base_commit = cut.governance_commit or cut.base_commit
    shallow_cut.head_commit = cut.head_commit
    shallow_cut.merge_commit = cut.merge_commit
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    code = m1_gate_probe.run_probe(
        shallow,
        shallow_cut.base_commit or "",
        shallow_cut.head_commit or "",
        shallow_cut.merge_commit or "",
        probe_dir,
    )
    assert code == 2
    result = json.loads((probe_dir / "probe-result.json").read_text(encoding="utf-8"))
    assert "浅克隆" in result["error"]


def test_probe_observer_binds_all_evidence_fields(cut: Fixture, tmp_path: Path) -> None:
    """观察者证据必须完整绑定 base/head/tree/contract/version/精确 edges/exit/hash。"""

    _prepare_cut(cut)
    probe_dir = tmp_path / "probe"
    assert cut.run_probe(probe_dir) == 0
    result = json.loads((probe_dir / "probe-result.json").read_text(encoding="utf-8"))
    first_parent = cut.governance_commit or cut.base_commit
    assert result["attestation"] == "reference_producer"
    assert result["nonce"] == TEST_ATTESTATION_NONCE
    assert "signature" not in result  # 参考 producer 无私钥，无法签名
    assert result["probe_version"] == m1_gate_probe.M1_PROBE_VERSION
    assert result["base_commit"] == first_parent
    assert result["pr_head_commit"] == cut.head_commit
    assert result["candidate_commit"] == cut.merge_commit
    assert result["candidate_tree"] == _tree_of(cut.repo, cut.merge_commit or "")
    assert result["contract_path"] == "scripts/m1_cuts/test-cut.json"
    assert isinstance(result["contract_oid"], str) and len(result["contract_oid"]) == 40
    assert result["expected_edges"] == cut.contract["expected_removed_edges"]
    assert result["exit_code"] == 0
    assert result["all_edges_confirmed"] is True
    assert result["environment"]["network"] == "none"
    assert result["environment"]["secrets"] is False
    expected_hash = evidence_sha256(
        result["expected_edges"], result["edges_checked"], result["exit_code"]
    )
    assert result["evidence_sha256"] == expected_hash


def test_production_change_requires_contract(cut: Fixture, tmp_path: Path) -> None:
    """生产 cut 无 contract（带认证 attestation）exit 1（policy 阻断）。"""

    cut.set_head_ops()
    cut.build_head()
    cut.make_merge()
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "probe_version": m1_gate_probe.M1_PROBE_VERSION,
        "attestation": "os_level_observer_attestation",
        "identity": "os_level_observer",
        "nonce": TEST_ATTESTATION_NONCE,
        "image": m1_gate_probe.PROBE_IMAGE,
        "base_commit": cut.base_commit,
        "pr_head_commit": cut.head_commit,
        "candidate_commit": cut.merge_commit,
        "candidate_tree": _tree_of(cut.repo, cut.merge_commit or ""),
        "contract_path": None,
        "contract_oid": None,
        "expected_edges": [],
        "edges_checked": [],
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
    payload["evidence_sha256"] = evidence_sha256([], [], 0)
    payload["signature"] = _rsa_sign(payload)
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
    assert code == 1
    decision = _read_report(report_dir, "decision.json")
    assert decision["status"] == "blocked"
    assert any("base-owned contract" in reason for reason in decision["reasons"])


def test_probe_detects_edge_still_present(cut: Fixture, tmp_path: Path) -> None:
    """运行探针能确认 removed edge 在候选运行时仍存在。"""

    _prepare_cut(cut, break_cycle=False, extra={"core/c.py": "VALUE_C = 9\n"})
    probe_dir = tmp_path / "probe"
    code = cut.run_probe(probe_dir)
    assert code == 1
    result = json.loads((probe_dir / "probe-result.json").read_text(encoding="utf-8"))
    edge = result["edges_checked"][0]
    assert result["all_edges_confirmed"] is False
    assert edge["verdict"] == "edge_present" and "core.a" in edge["loaded_targets"]


def test_harness_swallows_candidate_output(cut: Fixture, tmp_path: Path) -> None:
    """候选导入期 stdout 不得污染证据通道（harness 报告是唯一输出）。"""

    _prepare_cut(cut, extra={"core/b.py": "import sys\nVALUE_B = 1\n"})
    probe_dir = tmp_path / "probe"
    assert cut.run_probe(probe_dir) == 0
    result = json.loads((probe_dir / "probe-result.json").read_text(encoding="utf-8"))
    assert result["edges_checked"][0]["verdict"] == "confirmed_removed"


def test_production_cut_missing_probe_exit2(cut: Fixture, tmp_path: Path) -> None:
    """生产 cut 无 removed-edge 探针证据 exit 2（无法形成可信裁决）。"""

    _prepare_cut(cut)
    report_dir = tmp_path / "reports"
    code = m1_gate.main(
        _cli_args(
            cut, report_dir, extra=["--attestation-nonce", TEST_ATTESTATION_NONCE]
        )
    )
    assert code == 2
    decision = _read_report(report_dir, "decision.json")
    assert "error" in decision
    assert "attestation" in decision["error"]


def test_production_cut_probe_binding_mismatch_exit2(
    cut: Fixture, tmp_path: Path
) -> None:
    """探针结果绑定其他 candidate 时 exit 2（证据与裁决对象不一致）。"""

    _prepare_cut(cut)
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir(parents=True)
    payload = _bound_probe_result(
        cut, verdict="confirmed_removed", extra_fields={}, exit_code=0
    )
    (probe_dir / "probe-result.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    # 第二个 candidate（额外改动 core/c.py）与探针绑定的 candidate 不同
    _prepare_cut(cut, extra={"core/c.py": "VALUE_C = 9\n"})
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
    assert "error" in decision
    assert "绑定" in decision["error"]


def _bound_probe_result(
    cut: Fixture,
    *,
    verdict: str,
    extra_fields: dict[str, Any],
    exit_code: int,
) -> dict[str, Any]:
    """构造完整认证证据（identity/nonce/signature + 全部绑定字段）。"""

    first_parent = cut.governance_commit or cut.base_commit
    assert first_parent is not None and cut.contract is not None
    contract_oid = git(
        cut.repo, "rev-parse", f"{first_parent}:scripts/m1_cuts/test-cut.json"
    ).stdout.strip()
    edges_checked = [
        {
            "edge": ["core.b", "core.a"],
            "verdict": verdict,
            "loaded_targets": [],
            "error": None,
            **extra_fields,
        }
    ]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "probe_version": m1_gate_probe.M1_PROBE_VERSION,
        "attestation": "os_level_observer_attestation",
        "identity": "os_level_observer",
        "nonce": TEST_ATTESTATION_NONCE,
        "image": m1_gate_probe.PROBE_IMAGE,
        "base_commit": first_parent,
        "pr_head_commit": cut.head_commit,
        "candidate_commit": cut.merge_commit,
        "candidate_tree": _tree_of(cut.repo, cut.merge_commit or ""),
        "contract_path": "scripts/m1_cuts/test-cut.json",
        "contract_oid": contract_oid,
        "expected_edges": cut.contract["expected_removed_edges"],
        "edges_checked": edges_checked,
        "all_edges_confirmed": verdict == "confirmed_removed",
        "exit_code": exit_code,
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
        payload["expected_edges"], edges_checked, exit_code
    )
    payload["signature"] = _rsa_sign(payload)
    return payload


@pytest.mark.parametrize(
    "verdict,extra_fields,expected,keyword",
    [
        ("edge_present", {"loaded_targets": ["core.a"]}, 1, "运行探针"),
        ("inconclusive", {"error": "导入失败"}, 2, "inconclusive"),
    ],
)
def test_probe_evidence_policy(
    cut: Fixture,
    tmp_path: Path,
    verdict: str,
    extra_fields: dict[str, Any],
    expected: int,
    keyword: str,
) -> None:
    """合法 cut 的探针证据：edge_present -> exit 1，inconclusive -> exit 2。"""

    _prepare_cut(cut)
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir(parents=True)
    payload = _bound_probe_result(
        cut,
        verdict=verdict,
        extra_fields=extra_fields,
        exit_code=2 if verdict == "inconclusive" else 1,
    )
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
    assert code == expected
    decision = _read_report(report_dir, "decision.json")
    if expected == 1:
        assert decision["status"] == "blocked"
        assert any(keyword in reason for reason in decision["reasons"])
    else:
        assert keyword in decision["error"]


@pytest.mark.parametrize(
    "mutate,keyword,resign",
    [
        (lambda p: p.pop("attestation"), "attestation", False),
        (lambda p: p.update({"identity": "reference_producer"}), "identity", False),
        (lambda p: p.update({"nonce": "wrong-nonce"}), "nonce", False),
        (lambda p: p.update({"signature": "0" * 64}), "签名", False),
        (lambda p: p.update({"environment": {}}), "environment", True),
        (lambda p: p.update({"image": "evil:latest"}), "image", True),
        (lambda p: p.update({"probe_version": 0}), "probe_version", True),
        (lambda p: p.update({"base_commit": "0" * 40}), "base_commit", True),
        (
            lambda p: p.update({"expected_edges": [["core.x", "core.y"]]}),
            "expected_edges",
            True,
        ),
        (lambda p: p.update({"edges_checked": []}), "edges_checked", True),
        (lambda p: p.update({"exit_code": 1}), "exit_code", True),
        (lambda p: p.update({"evidence_sha256": "0" * 64}), "evidence_sha256", True),
    ],
)
def test_probe_evidence_forgery_exit2(
    cut: Fixture, tmp_path: Path, mutate: Any, keyword: str, resign: bool
) -> None:
    """伪造/篡改 attestation 一律 exit 2（认证先行；结构篡改须重签后仍被拒）。"""

    _prepare_cut(cut)
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir(parents=True)
    payload = _bound_probe_result(
        cut, verdict="confirmed_removed", extra_fields={}, exit_code=0
    )
    mutate(payload)
    if resign:
        payload["signature"] = _rsa_sign(payload)
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
    assert keyword in decision["error"]


@pytest.mark.skipif(
    not _docker_available(), reason="本机无 docker，跳过真实隔离域集成测试"
)
def test_probe_real_docker_isolation(tmp_path: Path) -> None:
    """真实 docker 隔离域：候选不可写挂载输入、非 root、证据由观察者生成。"""

    repo = tmp_path / "repo"
    _init_repo(repo)
    cut = Fixture(repo, tmp_path)
    cut.build_base()
    _prepare_cut(cut)
    probe_dir = tmp_path / "probe"
    code = m1_gate_probe.run_probe(
        cut.repo,
        cut.governance_commit or "",
        cut.head_commit or "",
        cut.merge_commit or "",
        probe_dir,
    )
    assert code == 0
    result = json.loads((probe_dir / "probe-result.json").read_text(encoding="utf-8"))
    assert result["all_edges_confirmed"] is True
    assert result["environment"]["isolated_domain"] == "docker"
