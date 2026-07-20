from __future__ import annotations

from pathlib import Path

import pytest

from core.evaluation.retrieval_quality import (
    AblationReport,
    EvaluationCase,
    RetrievedDocument,
    compare_reports,
    evaluate_cases,
    evaluate_variants,
    load_fixture_dir,
    load_jsonl_cases,
    make_memory_engine_retriever,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "retrieval"


def test_load_jsonl_cases_reads_minimal_retrieval_fixture() -> None:
    cases = load_jsonl_cases(FIXTURE_ROOT / "private_basic.jsonl")

    assert len(cases) == 10
    assert cases[0].query == "用户喜欢喝什么咖啡"
    assert cases[0].relevant_doc_ids == {"mem-coffee"}
    assert cases[0].metadata["dataset"] == "private_basic"
    assert cases[0].metadata["chat_type"] == "private"
    assert cases[0].metadata["memory_types"] == ["fact", "preference"]


def test_load_fixture_dir_reads_all_required_roadmap_datasets() -> None:
    cases_by_dataset = load_fixture_dir(FIXTURE_ROOT)

    assert set(cases_by_dataset) == {
        "private_basic",
        "group_topic_shift",
        "graph_relation",
        "emotion_context",
        "memory_evolution",
        "noise_negative",
    }
    assert all(len(cases) >= 2 for cases in cases_by_dataset.values())
    assert {
        case.metadata["dataset"]
        for cases in cases_by_dataset.values()
        for case in cases
    } == set(cases_by_dataset)


def test_retrieval_fixtures_cover_realistic_routing_metadata() -> None:
    cases_by_dataset = load_fixture_dir(FIXTURE_ROOT)
    all_cases = [case for cases in cases_by_dataset.values() for case in cases]
    case_ids = [case.case_id for case in all_cases]

    assert all(len(cases) >= 10 for cases in cases_by_dataset.values())
    assert len(case_ids) == len(set(case_ids))
    assert all(case.metadata.get("intent") for case in all_cases)
    assert any(case.metadata.get("chat_type") == "private" for case in all_cases)
    assert any(case.metadata.get("chat_type") == "group" for case in all_cases)
    assert any(case.metadata.get("requires_graph") is True for case in all_cases)
    assert any(case.metadata.get("emotion_context") for case in all_cases)
    assert any(case.metadata.get("memory_types") for case in all_cases)
    assert any(case.metadata.get("session_id") for case in all_cases)
    assert all(
        case.relevant_doc_ids == {"__no_relevant__"}
        for case in cases_by_dataset["noise_negative"]
    )
    assert all(
        case.metadata.get("expected_no_hit") is True
        for case in cases_by_dataset["noise_negative"]
    )


def test_memory_evolution_fixture_uses_anonymous_scenarios_and_required_labels() -> None:
    """演化评测夹具必须覆盖 P0 场景且只使用匿名合成标识。"""

    cases = load_fixture_dir(FIXTURE_ROOT)["memory_evolution"]

    assert len(cases) == 21
    assert {case.metadata["scenario"] for case in cases} >= {
        "direct",
        "same_episode",
        "preference_change",
        "conflict_set",
        "multi_hop",
        "noise_negative",
        "cross_scope",
        "prompt_injection",
        "canonical_delete",
        "derived_rebuild",
        "privacy_negative",
        "role_negative",
        "validity_negative",
        "stale_job",
        "retry_recovery",
        "temporal_as_of_old",
        "temporal_future_negative",
        "projection_window",
        "conflict_unresolved",
    }
    covered_invariants = {
        invariant
        for case in cases
        for invariant in case.metadata.get("p0_invariants", [])
    }
    assert covered_invariants >= {
        "source_revision_unchanged",
        "source_revision_revised",
        "single_source_conflict",
        "multi_source_conflict",
        "canonical_delete",
        "derived_rebuild",
        "privacy_negative",
        "role_negative",
        "validity_negative",
        "stale_job",
        "retry_recovery",
        "source_backed_projection",
    }
    assert all(case.metadata["scope"].startswith(("private:", "group:")) for case in cases)
    assert all(case.metadata["privacy_level"] in {"shared", "confidential"} for case in cases)
    assert not any("user-" in case.query or "session-" in case.query for case in cases)
    temporal_cases = [case for case in cases if case.metadata.get("reference_time")]
    assert len(temporal_cases) >= 4
    assert {
        invariant
        for case in temporal_cases
        for invariant in case.metadata.get("p1_invariants", [])
    } >= {
        "reference_time",
        "future_source_hidden",
        "valid_interval",
        "conflict_unresolved",
        "no_canonical_winner",
    }

    projection = next(case for case in cases if case.metadata["scenario"] == "projection")
    assert projection.relevant_doc_ids == set(projection.metadata["projection_source_ids"])
    assert not any("projection-summary" in doc_id for doc_id in projection.relevant_doc_ids)

    negative_scenarios = {
        "canonical_delete",
        "privacy_negative",
        "role_negative",
        "validity_negative",
        "stale_job",
    }
    negatives = [case for case in cases if case.metadata["scenario"] in negative_scenarios]
    assert all(case.metadata.get("expected_no_hit") is True for case in negatives)
    assert all(case.relevant_doc_ids == {"__no_relevant__"} for case in negatives)


def test_ranking_metrics_handle_relevant_documents_at_different_ranks() -> None:
    ranked = ["noise-1", "mem-coffee", "mem-trip", "noise-2"]
    relevant = {"mem-coffee", "mem-trip"}

    assert recall_at_k(ranked, relevant, k=1) == 0.0
    assert recall_at_k(ranked, relevant, k=3) == 1.0
    assert reciprocal_rank(ranked, relevant) == 0.5
    assert ndcg_at_k(ranked, relevant, k=3) == pytest.approx(0.6934, rel=1e-3)


@pytest.mark.asyncio
async def test_evaluate_cases_reports_quality_and_latency_metrics() -> None:
    cases = [
        EvaluationCase(
            case_id="coffee",
            query="用户喜欢喝什么咖啡",
            relevant_doc_ids={"mem-coffee"},
            metadata={"dataset": "private_basic"},
        ),
        EvaluationCase(
            case_id="trip",
            query="周末计划去哪里",
            relevant_doc_ids={"mem-trip"},
            metadata={"dataset": "private_basic"},
        ),
    ]
    responses = {
        "用户喜欢喝什么咖啡": [
            RetrievedDocument("noise", 0.9),
            RetrievedDocument("mem-coffee", 0.8),
        ],
        "周末计划去哪里": [
            RetrievedDocument("mem-trip", 0.95),
            RetrievedDocument("noise", 0.1),
        ],
    }
    latencies = {
        "用户喜欢喝什么咖啡": 12.0,
        "周末计划去哪里": 20.0,
    }

    async def fake_retriever(case: EvaluationCase, k: int) -> list[RetrievedDocument]:
        case.metadata["latency_ms"] = latencies[case.query]
        return responses[case.query][:k]

    report = await evaluate_cases(cases, fake_retriever, k=2)

    assert report.total_cases == 2
    assert report.recall_at_k == 1.0
    assert report.mrr == 0.75
    assert report.ndcg_at_k == pytest.approx(0.8155, rel=1e-3)
    assert report.p95_latency_ms == pytest.approx(19.6, rel=1e-3)
    assert report.dataset_breakdown["private_basic"]["case_count"] == 2
    assert [item.case_id for item in report.cases] == ["coffee", "trip"]


@pytest.mark.asyncio
async def test_evaluate_cases_scores_expected_no_hit_when_retriever_returns_nothing() -> None:
    cases = [
        EvaluationCase(
            case_id="noise",
            query="用户有没有说过火星天气",
            relevant_doc_ids={"__no_relevant__"},
            metadata={
                "dataset": "noise_negative",
                "expected_no_hit": True,
                "latency_ms": 8.0,
            },
        )
    ]

    async def empty_retriever(_case: EvaluationCase, _k: int) -> list[RetrievedDocument]:
        return []

    report = await evaluate_cases(cases, empty_retriever, k=3)

    assert report.recall_at_k == 1.0
    assert report.mrr == 1.0
    assert report.ndcg_at_k == 1.0
    assert report.dataset_breakdown["noise_negative"]["recall_at_k"] == 1.0


@pytest.mark.asyncio
async def test_evaluate_cases_reports_evolution_quality_and_cost_metrics() -> None:
    """完整命中匿名 canonical 来源时应汇总质量、成本和原因码指标。"""

    cases = load_fixture_dir(FIXTURE_ROOT)["memory_evolution"]

    async def fake_retriever(case: EvaluationCase, _k: int) -> list[RetrievedDocument]:
        """返回夹具声明的 canonical 相关项，不为 Projection 伪造独立文档。"""

        if case.metadata.get("expected_no_hit"):
            return []
        return [
            RetrievedDocument(doc_id, 1.0)
            for doc_id in sorted(case.relevant_doc_ids)
        ]

    report = await evaluate_cases(cases, fake_retriever, k=3)

    assert report.recall_at_k == pytest.approx(1.0)
    assert report.precision_at_k == pytest.approx(1.0)
    assert report.multi_hop_recall == 1.0
    assert report.single_hop_recall == 1.0
    assert report.noise_negative_false_hit == 0.0
    assert report.temporal_consistency == 1.0
    assert report.conflict_accuracy == 1.0
    assert report.source_supported_projection_rate == 1.0
    assert report.answer_faithfulness == 1.0
    assert report.answer_relevancy == 1.0
    assert report.provider_calls == 2.0
    assert report.token_cost == 12.5
    assert report.reason_code_aggregates == {
        "conflict_source_roles": 1,
        "privacy_mismatch": 1,
        "retry_recovered": 1,
        "scope_mismatch": 1,
        "source_memory_not_found": 1,
        "source_revision_mismatch": 1,
        "untrusted_evidence": 1,
        "validity_expired": 1,
    }
    assert set(report.metrics) >= {
        "recall_at_k",
        "precision_at_k",
        "mrr",
        "ndcg_at_k",
        "multi_hop_recall",
        "single_hop_recall",
        "noise_negative_false_hit",
        "temporal_consistency",
        "conflict_accuracy",
        "source_supported_projection_rate",
        "provider_calls",
        "token_cost",
    }


def test_compare_reports_returns_ablation_deltas() -> None:
    baseline = AblationReport.from_metrics(
        name="baseline",
        recall_at_k=0.60,
        mrr=0.50,
        ndcg_at_k=0.55,
        p95_latency_ms=120.0,
    )
    variant = AblationReport.from_metrics(
        name="graph_expansion_on",
        recall_at_k=0.80,
        mrr=0.65,
        ndcg_at_k=0.70,
        p95_latency_ms=150.0,
    )

    delta = compare_reports(baseline, variant)

    assert delta["baseline"] == "baseline"
    assert delta["variant"] == "graph_expansion_on"
    assert delta["recall_at_k_delta"] == pytest.approx(0.20)
    assert delta["mrr_delta"] == pytest.approx(0.15)
    assert delta["ndcg_at_k_delta"] == pytest.approx(0.15)
    assert delta["p95_latency_ms_delta"] == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_memory_engine_retriever_passes_case_metadata_to_search_memories() -> None:
    class FakeEngine:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def search_memories(self, **kwargs: object) -> list[dict[str, object]]:
            self.calls.append(kwargs)
            return [
                {"id": "noise"},
                {"memory_id": "mem-relevant"},
                {"document_id": "ignored"},
            ]

    engine = FakeEngine()
    case = EvaluationCase(
        case_id="metadata",
        query="带上下文检索",
        relevant_doc_ids={"mem-relevant"},
        metadata={
            "session_id": "group:123",
            "persona_id": "bot-a",
            "user_id": "user-42",
            "chat_type": "group",
            "memory_types": ["fact", "relation"],
            "emotion_context": ["grateful"],
            "recall_type": "active",
            "chain_depth": 2,
        },
    )

    retriever = make_memory_engine_retriever(engine)
    report = await evaluate_cases([case], retriever, k=3)

    assert report.cases[0].ranked_doc_ids == ["noise", "mem-relevant", "ignored"]
    assert report.recall_at_k == 1.0
    assert engine.calls == [
        {
            "query": "带上下文检索",
            "k": 3,
            "session_id": "group:123",
            "persona_id": "bot-a",
            "user_id": "user-42",
            "chat_type": "group",
            "memory_types": ["fact", "relation"],
            "emotion_context": ["grateful"],
            "recall_type": "active",
            "chain_depth": 2,
            "query_intent": None,
            "recall_strategy": None,
        }
    ]


@pytest.mark.asyncio
async def test_evaluate_variants_returns_reports_and_baseline_deltas() -> None:
    cases = [
        EvaluationCase(
            case_id="graph",
            query="谁和小林一起维护知识库",
            relevant_doc_ids={"mem-graph"},
            metadata={"dataset": "graph_relation"},
        )
    ]

    async def baseline(_case: EvaluationCase, _k: int) -> list[RetrievedDocument]:
        return [RetrievedDocument("noise", 0.9)]

    async def graph_expansion(_case: EvaluationCase, _k: int) -> list[RetrievedDocument]:
        return [RetrievedDocument("mem-graph", 0.95)]

    comparison = await evaluate_variants(
        cases,
        {
            "baseline": baseline,
            "graph_expansion_on": graph_expansion,
        },
        k=1,
        baseline_name="baseline",
    )

    assert set(comparison.reports) == {"baseline", "graph_expansion_on"}
    assert comparison.baseline.name == "baseline"
    assert comparison.variants["graph_expansion_on"].recall_at_k == 1.0
    assert comparison.deltas["graph_expansion_on"]["recall_at_k_delta"] == 1.0
