"""报告存储对逐用例注入观察的写读 allowlist 契约。"""

from __future__ import annotations

import pytest

from core.features.evaluation.infrastructure.report_store import EvaluationReportStore


@pytest.mark.asyncio
async def test_report_store_sanitizes_injection_payload(tmp_path) -> None:
    store = EvaluationReportStore(tmp_path / "reports.db")
    await store.initialize()
    report_id = await store.save_report(
        {
            "summary": {"total_cases": 1, "k": 5},
            "datasets": ["synthetic"],
            "cases": [
                {
                    "case_id": "case-1",
                    "query": "secret query",
                    "content": "secret content",
                    "doc_id": 42,
                    "injection": {
                        "candidate_count": 2,
                        "selected_count": 1,
                        "rejected_count": 1,
                        "source_rejected_count": 0,
                        "selection_dropped_count": 1,
                        "candidate_hit": True,
                        "final_injected_hit": False,
                        "negative_injection": True,
                        "injection_precision": 0.5,
                        "required_fact_coverage": None,
                        "dropped_relevant": 1,
                        "budget_utilization": 0.25,
                        "resolved_preset": "balanced",
                        "decision_reason_codes": ["MANUAL_SELECTED", "secret"],
                        "injection_simulation_ms": 0.2,
                        "route_signals": {
                            "query_intent": "relationship",
                            "document_route_weight": 0.65,
                            "graph_route_score_max": 0.4,
                            "content": "secret content",
                        },
                    },
                }
            ],
        }
    )

    report = await store.get_report(report_id)
    assert report is not None
    injection = report["cases"][0]["injection"]

    assert injection["candidate_count"] == 2
    assert injection["selected_count"] == 1
    assert injection["candidate_hit"] is True
    assert injection["final_injected_hit"] is False
    assert injection["negative_injection"] is True
    assert injection["injection_precision"] == 0.5
    assert "required_fact_coverage" not in injection
    assert injection["resolved_preset"] == "balanced"
    assert injection["decision_reason_codes"] == ["MANUAL_SELECTED"]
    assert injection["route_signals"]["query_intent"] == "relationship"
    assert injection["route_signals"]["document_route_weight"] == 0.65
    assert "content" not in injection["route_signals"]
    assert "query" not in injection
    assert "content" not in injection
    assert "doc_id" not in injection
