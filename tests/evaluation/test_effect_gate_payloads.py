"""效果门报告 allowlist 与紧凑摘要测试。"""

from __future__ import annotations

import math
from copy import deepcopy

from core.features.evaluation.infrastructure.effect_gate_payloads import (
    effect_gate_summary,
    safe_effect_gate_payload,
)
from tests.effect_gate_test_support import gate_payload


def test_safe_effect_gate_payload_keeps_only_allowlisted_values() -> None:
    """未列入 allowlist 的字段、类别、reason code 与配置必须剔除。"""

    payload = gate_payload()
    payload["query"] = "用户隐私正文"
    payload["canonical_id"] = "doc-42"
    payload["factors"]["atom"]["reason_codes"].append("unknown_reason")
    payload["factors"]["atom"]["categories"]["secret_category"] = {
        "case_count": 1,
        "final_injected_hit_rate": {"baseline": 1.0, "variant": 1.0, "delta": 0.0},
    }
    payload["factors"]["atom"]["metrics"]["raw_metric"] = {
        "available": True,
        "pairs": 1,
    }
    payload["factors"]["atom"]["recommended_config"] = {"atom_route_weight": 0.25}
    payload["recommended_config"]["secret_key"] = "value"
    payload["manifest"]["raw_config"] = {"api_key": "secret"}
    payload["policy"]["raw_policy"] = "value"

    safe = safe_effect_gate_payload(payload)

    assert safe is not None
    assert "query" not in safe
    assert "canonical_id" not in safe
    assert "unknown_reason" not in safe["factors"]["atom"]["reason_codes"]
    assert "secret_category" not in safe["factors"]["atom"]["categories"]
    assert "raw_metric" not in safe["factors"]["atom"]["metrics"]
    assert "secret_key" not in safe["recommended_config"]
    assert "raw_config" not in safe["manifest"]
    assert "raw_policy" not in safe["policy"]
    assert safe["factors"]["atom"]["status"] == "pass_enable"
    assert safe["readiness"]["can_evaluate"] is True
    assert len(safe["factors"]) == 4


def test_safe_effect_gate_payload_drops_non_finite_values() -> None:
    """NaN/Inf 不能写入报告；保留字段但值为 null。"""

    payload = gate_payload()
    payload["factors"]["atom"]["metrics"]["final_injected_hit_rate"]["delta"] = float(
        "nan"
    )
    payload["factors"]["atom"]["metrics"]["injection_precision"]["ci_high"] = math.inf

    safe = safe_effect_gate_payload(payload)

    assert safe is not None
    atom = safe["factors"]["atom"]["metrics"]
    assert atom["final_injected_hit_rate"]["delta"] is None
    assert atom["injection_precision"]["ci_high"] is None


def test_safe_effect_gate_payload_rejects_canary_violation() -> None:
    """privacy canary 命中时整份 gate 载荷必须拒绝。"""

    payload = gate_payload()
    payload["privacy_canary"]["violations"] = ["secret query"]

    assert safe_effect_gate_payload(payload) is None


def test_safe_effect_gate_payload_requires_all_factors_and_valid_manifest() -> None:
    """缺因子或 manifest 哈希非法时拒绝，避免半份报告。"""

    missing_factor = gate_payload()
    del missing_factor["factors"]["rerank"]
    assert safe_effect_gate_payload(missing_factor) is None

    bad_manifest = deepcopy(gate_payload())
    bad_manifest["manifest"]["fixture_hmac"] = "not-a-hash"
    assert safe_effect_gate_payload(bad_manifest) is None


def test_effect_gate_summary_has_no_per_metric_payload() -> None:
    """列表/对比摘要只保留 readiness、状态与 manifest 绑定。"""

    summary = effect_gate_summary(gate_payload())

    assert summary is not None
    assert "truncated" not in summary
    assert summary["factors"]["atom"] == {
        "status": "pass_enable",
        "recommendation": "enable",
        "reason_codes": ["pareto_improvement"],
    }
    assert "metrics" not in summary["factors"]["atom"]
    assert "categories" not in summary["factors"]["atom"]
    assert summary["manifest"]["fixture_hmac"] == "a" * 64
    assert summary["manifest"]["seed"] == 0
