"""M1 外部 attestor/HSM 契约的可执行验收测试。

把 `website/docs/development/m1-attestor-contract.md` 中定义的契约
（精确键集、身份名单、pinned 公钥格式、nonce 格式、私钥禁入仓库、
轮换不变量）与 `scripts/m1_gate_core.py` 的代码常量交叉校验；文档、
代码与测试三处不一致立即失败。全部用例 stdlib，不需要任何外部设施
（attestor/HSM 落地前即可运行）。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from scripts.m1_gate_core import (
    ATTESTATION_ENV_KEYS,
    ATTESTATION_KEYS,
    ATTESTATION_SIGNED_KEYS,
    M1_ATTESTOR_PUBLIC_KEY,
    SCHEMA_VERSION,
    TRUSTED_PRODUCER_IDS,
    attestation_signed_payload,
    evidence_sha256,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DOC = REPO_ROOT / "website/docs/development/m1-attestor-contract.md"
M1_GATE_WORKFLOW = REPO_ROOT / ".github/workflows/m1-gate.yml"

# 文档中列出的精确键集（契约权威副本，必须与代码常量一致）
EXPECTED_ATTESTATION_KEYS = {
    "schema_version",
    "probe_version",
    "attestation",
    "identity",
    "nonce",
    "signature",
    "image",
    "base_commit",
    "pr_head_commit",
    "candidate_commit",
    "candidate_tree",
    "contract_path",
    "contract_oid",
    "expected_edges",
    "edges_checked",
    "all_edges_confirmed",
    "exit_code",
    "environment",
    "evidence_sha256",
}
EXPECTED_ENV_KEYS = {
    "network",
    "secrets",
    "read_only_inputs",
    "non_root",
    "no_new_privileges",
    "isolated_domain",
}


def _extract_doc_keyset(marker: str) -> set[str]:
    """从契约文档提取 `````text```` 代码块中指定标记后的键集。"""
    text = CONTRACT_DOC.read_text(encoding="utf-8")
    block_pattern = re.compile(
        r"```text\n" + re.escape(marker) + r"\n(.*?)\n```",
        re.DOTALL,
    )
    match = block_pattern.search(text)
    assert match is not None, f"契约文档缺少键集标记: {marker}"
    body = match.group(1)
    return {token.strip() for token in re.split(r"[,\s]+", body) if token.strip()}


def test_attestation_keys_exact_schema() -> None:
    """attestation 顶层键集必须与契约文档、代码常量三方一致。"""
    assert ATTESTATION_KEYS == EXPECTED_ATTESTATION_KEYS
    assert len(ATTESTATION_KEYS) == 19
    assert SCHEMA_VERSION == 1
    assert (
        _extract_doc_keyset("ATTESTATION_KEYS 精确键集（19 键）：") == ATTESTATION_KEYS
    )


def test_environment_keys_exact_schema() -> None:
    """environment 键集必须与契约文档、代码常量三方一致。"""
    assert ATTESTATION_ENV_KEYS == EXPECTED_ENV_KEYS
    assert len(ATTESTATION_ENV_KEYS) == 6
    assert _extract_doc_keyset("ATTESTATION_ENV_KEYS 精确键集（6 键）：") == (
        ATTESTATION_ENV_KEYS
    )


def test_trusted_producer_identity_allowlist() -> None:
    """签发身份只允许受保护 OS 级观察者，拒绝自证/reference producer。"""
    assert TRUSTED_PRODUCER_IDS == {"os_level_observer"}
    assert "reference_producer" not in TRUSTED_PRODUCER_IDS


def test_signed_keys_cover_all_except_signature() -> None:
    """签名负载必须覆盖除 signature 外的全部键（无未签名字段）。"""
    assert set(ATTESTATION_SIGNED_KEYS) == ATTESTATION_KEYS - {"signature"}
    assert len(ATTESTATION_SIGNED_KEYS) == len(ATTESTATION_KEYS) - 1


def test_pinned_public_key_format() -> None:
    """pinned 公钥必须是 2048 位 RSA、e=65537，且不携带私钥材料。"""
    assert set(M1_ATTESTOR_PUBLIC_KEY) == {"n", "e"}
    assert re.fullmatch(r"[0-9a-f]+", M1_ATTESTOR_PUBLIC_KEY["n"]) is not None
    assert re.fullmatch(r"[0-9a-f]+", M1_ATTESTOR_PUBLIC_KEY["e"]) is not None
    n = int(M1_ATTESTOR_PUBLIC_KEY["n"], 16)
    assert n.bit_length() == 2048, f"pinned 公钥模数必须 2048 位: {n.bit_length()}"
    assert int(M1_ATTESTOR_PUBLIC_KEY["e"], 16) == 65537
    for field in ("d", "p", "q", "dp", "dq", "qinv"):
        assert field not in M1_ATTESTOR_PUBLIC_KEY


def test_attestation_payload_canonical_form() -> None:
    """签名负载规范 JSON 必须是确定性的（键排序、紧凑分隔符、UTF-8）。"""
    payload: dict[str, Any] = {
        name: "x" for name in ATTESTATION_KEYS if name != "signature"
    }
    encoded = attestation_signed_payload(payload)
    assert encoded == attestation_signed_payload(dict(payload))  # 幂等
    assert set(json.loads(encoded)) == ATTESTATION_KEYS - {"signature"}
    assert ", " not in encoded.decode("utf-8")  # 紧凑分隔符


def test_nonce_requirements() -> None:
    """nonce 必须非空且由 workflow 以 openssl rand -hex 32 生成（64 hex）。"""
    workflow = M1_GATE_WORKFLOW.read_text(encoding="utf-8")
    assert "openssl rand -hex 32" in workflow
    assert "--attestation-nonce" in workflow
    # GITHUB_ENV 写入名与 gate argv 引用名必须同名（回归 Blocker）
    write_match = re.search(
        r'echo "([A-Za-z0-9_]+)=\$\(openssl rand -hex 32\)"', workflow
    )
    assert write_match is not None
    env_name = write_match.group(1)
    assert f"${{{env_name}}}" in workflow


def test_evidence_sha256_canonical() -> None:
    """evidence_sha256 必须是规范哈希（与探针参考实现同口径）。"""
    edges = [["core.b", "core.a"]]
    checked = [
        {
            "edge": ["core.b", "core.a"],
            "verdict": "confirmed_removed",
            "loaded_targets": [],
            "error": None,
        }
    ]
    first = evidence_sha256(edges, checked, 0)
    assert first == evidence_sha256(list(edges), list(checked), 0)
    assert re.fullmatch(r"[0-9a-f]{64}", first) is not None
    # 内容变化必须改变哈希
    checked[0]["verdict"] = "edge_present"
    assert evidence_sha256(edges, checked, 1) != first


def test_contract_doc_rotation_invariants_present() -> None:
    """契约文档必须定义轮换/回滚/仪式/保管/传输/失败处理与交接（可执行清单）。"""
    text = CONTRACT_DOC.read_text(encoding="utf-8")
    for keyword in (
        "密钥仪式",
        "私钥保管",
        "认证传输",
        "失败与重放处理",
        "轮换方案",
        "回滚",
        "阻塞项与 owner/DevOps 交接",
        "OS 级观察证据",
    ):
        assert keyword in text, f"契约文档缺少必需章节: {keyword}"
    assert "不得伪造" in text or "不伪造" in text


def test_workflow_attestation_path_contract() -> None:
    """workflow 必须把 --probe-result 指向受信固定路径的 attestation.json。"""
    workflow = M1_GATE_WORKFLOW.read_text(encoding="utf-8")
    assert "attestation_dir=" in workflow
    assert "${RUNNER_TEMP}/m1-attestation" in workflow
    assert "attestation.json" in workflow
    assert "secrets.M1_ATTESTOR" not in workflow  # 私钥绝不进入 workflow secret


def test_no_private_key_material_in_tracked_files() -> None:
    """仓库受版本控制文件不得包含任何私钥/PEM 标记（私钥禁入仓库）。"""
    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout.split(b"\x00")
    for raw in tracked:
        if not raw:
            continue
        path = REPO_ROOT / raw.decode("utf-8")
        if not path.is_file():
            continue
        content = path.read_bytes()
        if b"-----BEGIN" in content:
            raise AssertionError(f"受版本控制文件包含 PEM 私钥标记: {path}")
