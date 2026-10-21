"""compare_reports 的注入、成本与效果门扩展测试。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from core.features.evaluation.infrastructure.effect_gate_payloads import (
    effect_gate_summary,
)
from core.features.evaluation.infrastructure.report_comparison import (
    compare_evaluation_reports,
)
from core.features.evaluation.infrastructure.report_store import EvaluationReportStore
from tests.effect_gate_test_support import gate_payload


def _report(
    *,
    recall: float,
    injected_hit: float,
    provider_calls: float,
    gate: dict | None,
) -> dict:
    report = {
        "summary": {
            "total_cases": 1,
            "recall_at_k": recall,
            "mrr": 1.0,
            "ndcg_at_k": 1.0,
            "observed_p95_latency_ms": 10.0,
            "observed_provider_calls": provider_calls,
            "injection_summary": {
                "final_injected_hit_rate": injected_hit,
                "injection_precision": injected_hit,
                "required_fact_coverage": injected_hit,
                "negative_injection_rate": 0.0,
            },
        },
        "datasets": ["runtime"],
        "variants": {},
        "cases": [],
    }
    if gate is not None:
        report["effect_gate"] = gate
        report["summary"]["effect_gate_summary"] = effect_gate_summary(gate)
    return report


@pytest.mark.asyncio
async def test_compare_reports_extended_fields_and_changed_factor(tmp_path) -> None:
    """保留旧 deltas 四键，同时返回注入、成本与门禁变化。"""

    store = EvaluationReportStore(tmp_path / "reports.db")
    await store.initialize()
    gate_a = gate_payload()
    gate_b = deepcopy(gate_payload())
    gate_b["factors"]["atom"]["status"] = "reject"
    gate_b["factors"]["atom"]["recommendation"] = "keep_disabled"
    gate_b["factors"]["atom"]["reason_codes"] = ["primary_regression"]

    first = await store.save_report(
        _report(recall=0.5, injected_hit=0.5, provider_calls=1.0, gate=gate_a)
    )
    second = await store.save_report(
        _report(recall=0.7, injected_hit=0.7, provider_calls=2.0, gate=gate_b)
    )

    comparison = await compare_evaluation_reports(store, first, second)

    assert comparison is not None
    assert set(comparison["deltas"]) == {
        "recall_at_k",
        "mrr",
        "ndcg_at_k",
        "observed_p95_latency_ms",
    }
    assert comparison["deltas"]["recall_at_k"] == pytest.approx(0.2)
    assert comparison["injection_deltas"]["final_injected_hit_rate"] == pytest.approx(
        0.2
    )
    assert comparison["cost_deltas"]["observed_provider_calls"] == pytest.approx(1.0)
    assert comparison["effect_gate"]["changed_factors"] == ["atom"]
    assert comparison["effect_gate"]["a"]["factors"]["atom"]["status"] == "pass_enable"
    assert comparison["effect_gate"]["b"]["factors"]["atom"]["status"] == "reject"
    assert (
        comparison["reports"]["a"]["summary"]["effect_gate_summary"]["factors"]["atom"][
            "recommendation"
        ]
        == "enable"
    )


@pytest.mark.asyncio
async def test_compare_reports_missing_injection_and_gate_stay_unknown(
    tmp_path,
) -> None:
    """任一侧缺注入/门禁时对应字段为 null，不以 0 补齐。"""

    store = EvaluationReportStore(tmp_path / "reports.db")
    await store.initialize()
    first = await store.save_report(
        _report(recall=0.5, injected_hit=0.5, provider_calls=1.0, gate=None)
    )
    second = await store.save_report(
        _report(recall=0.7, injected_hit=0.7, provider_calls=2.0, gate=gate_payload())
    )

    comparison = await compare_evaluation_reports(store, first, second)

    assert comparison is not None
    assert comparison["effect_gate"] is None
    assert comparison["injection_deltas"]["final_injected_hit_rate"] == pytest.approx(
        0.2
    )

    no_injection = {
        "summary": {"total_cases": 1, "recall_at_k": 0.5},
        "datasets": ["runtime"],
        "variants": {},
        "cases": [],
    }
    first_id = await store.save_report(no_injection)
    second_id = await store.save_report(no_injection)
    comparison = await compare_evaluation_reports(store, first_id, second_id)
    assert comparison is not None
    assert comparison["injection_deltas"]["final_injected_hit_rate"] is None
    assert comparison["cost_deltas"]["observed_provider_calls"] is None
