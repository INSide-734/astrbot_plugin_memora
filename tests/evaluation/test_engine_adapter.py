"""评测引擎适配器的 scope、query plan 与成本观测契约。"""

from __future__ import annotations

from typing import Any

import pytest

from core.features.evaluation.application.engine_adapter import (
    make_memory_engine_retriever,
)
from core.features.evaluation.application.retrieval_quality import EvaluationCase
from core.features.evaluation.domain.metric_provenance import RetrievalObservation


class _CountingEngine:
    """记录 search_memories 入参并模拟一次 Provider 调用。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._evaluation_cost_counter = _Counter()

    async def search_memories(self, **kwargs: Any) -> list[object]:
        self.calls.append(kwargs)
        self._evaluation_cost_counter.calls += 1
        return [object()]

    async def get_memory(self, memory_id: int) -> dict[str, object]:
        return {
            "metadata": {
                "scope_key": f"private:test:{memory_id}",
                "privacy_level": "confidential",
            }
        }


class _Counter:
    def __init__(self) -> None:
        self.calls = 0

    def reset(self) -> None:
        self.calls = 0

    def value(self) -> float:
        return float(self.calls)


@pytest.mark.asyncio
async def test_adapter_passes_scope_plan_and_cost_observation() -> None:
    engine = _CountingEngine()
    retriever = make_memory_engine_retriever(engine)
    case = EvaluationCase(
        case_id="case-1",
        query="q",
        relevant_doc_ids={"1"},
        metadata={
            "scope_key": "private:test:u1",
            "privacy_level": "confidential",
            "query_intent": {"intent": "relationship"},
            "query_plan": {
                "queries": ["q", "q2"],
                "required_facets": ["relation"],
                "intent": "relationship",
            },
        },
    )

    result = await retriever(case, 5)

    assert isinstance(result, RetrievalObservation)
    assert result.observed_provider_calls == 1.0
    assert result.observed_token_cost is None
    call = engine.calls[0]
    assert call["require_user_evidence"] is True
    assert call["query_scope"].scope_key == "private:test:u1"
    assert call["query_plan"].queries == ("q", "q2")
    assert call["query_plan"].required_facets == ("relation",)
    assert call["query_intent"].intent == "relationship"


@pytest.mark.asyncio
async def test_adapter_falls_back_to_canonical_scope_and_allows_opt_out() -> None:
    engine = _CountingEngine()
    retriever = make_memory_engine_retriever(engine)
    case = EvaluationCase(
        case_id="case-2",
        query="q",
        relevant_doc_ids={"7"},
        metadata={"require_user_evidence": False},
    )

    await retriever(case, 3)

    call = engine.calls[0]
    assert call["require_user_evidence"] is False
    assert call["query_scope"].scope_key == "private:test:7"
    assert "query_plan" not in call


@pytest.mark.asyncio
async def test_adapter_negative_case_keeps_scope_absent() -> None:
    engine = _CountingEngine()
    retriever = make_memory_engine_retriever(engine)
    case = EvaluationCase(
        case_id="case-3",
        query="q",
        relevant_doc_ids={"__no_relevant__"},
    )

    await retriever(case, 3)

    call = engine.calls[0]
    assert call["query_scope"] is None
    assert call["require_user_evidence"] is True
