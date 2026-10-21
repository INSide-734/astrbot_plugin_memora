"""效果门报告写读 allowlist 与旧报告兼容测试。"""

from __future__ import annotations

import json

import pytest

from core.features.evaluation.infrastructure.effect_gate_payloads import (
    effect_gate_summary,
    safe_effect_gate_payload,
)
from core.features.evaluation.infrastructure.report_store import EvaluationReportStore
from tests.effect_gate_test_support import gate_payload


def _report(gate: dict | None = None) -> dict:
    report = {
        "summary": {
            "total_cases": 1,
            "recall_at_k": 1.0,
            "observed_p95_latency_ms": 10.0,
        },
        "datasets": ["runtime"],
        "variants": {},
        "cases": [
            {
                "case_id": "case-1",
                "query": "secret query",
                "recall_at_k": 1.0,
                "injection": {
                    "final_injected_hit": True,
                    "injection_precision": 1.0,
                },
            }
        ],
    }
    if gate is not None:
        report["effect_gate"] = gate
        report["summary"]["effect_gate_summary"] = effect_gate_summary(gate)
    return report


@pytest.mark.asyncio
async def test_effect_gate_round_trip_is_allowlisted(tmp_path) -> None:
    """写读后只保留 gate 闭集字段，且不透出 query/canonical ID。"""

    store = EvaluationReportStore(tmp_path / "reports.db")
    await store.initialize()
    gate = gate_payload()
    gate["query"] = "secret query"
    gate["canonical_id"] = "doc-1"

    report_id = await store.save_report(_report(gate))
    loaded = await store.get_report(report_id)

    assert loaded is not None
    assert loaded["effect_gate"] == safe_effect_gate_payload(gate_payload())
    assert "secret query" not in json.dumps(loaded, ensure_ascii=False)
    assert "doc-1" not in json.dumps(loaded, ensure_ascii=False)
    assert loaded["cases"][0]["case_id"] == "case-1"
    assert "query" not in loaded["cases"][0]
    assert loaded["summary"]["effect_gate_summary"]["factors"]["atom"]["status"] == (
        "pass_enable"
    )

    reports = await store.list_reports()
    assert (
        reports[0]["summary"]["effect_gate_summary"]["factors"]["graph_route"][
            "recommendation"
        ]
        == "degrade_to_document_only"
    )
    assert "effect_gate" not in reports[0]


@pytest.mark.asyncio
async def test_effect_gate_store_rejects_canary_violation(tmp_path) -> None:
    """canary 命中时拒绝写入，不留下半成品报告。"""

    store = EvaluationReportStore(tmp_path / "reports.db")
    await store.initialize()
    gate = gate_payload()
    gate["privacy_canary"]["violations"] = ["secret query"]

    with pytest.raises(ValueError, match="effect_gate payload failed validation"):
        await store.save_report(_report(gate))


@pytest.mark.asyncio
async def test_old_report_without_effect_gate_remains_readable(tmp_path) -> None:
    """旧报告没有 gate 字段时照常读取。"""

    store = EvaluationReportStore(tmp_path / "reports.db")
    await store.initialize()

    report_id = await store.save_report(_report())
    loaded = await store.get_report(report_id)

    assert loaded is not None
    assert "effect_gate" not in loaded
    assert "effect_gate_summary" not in loaded["summary"]
