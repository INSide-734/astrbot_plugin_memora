"""有界时间、实体角色与焦点证据评分测试。"""

from datetime import datetime, timezone

from core.retrieval.evidence_scorer import RetrievalEvidenceScorer
from core.retrieval.query_planner import QueryPlan
from core.retrieval.rrf_fusion import HybridResult


def _result(
    doc_id: int,
    content: str,
    timestamp: str,
    *,
    score: float = 0.5,
) -> HybridResult:
    """构造最小检索候选。"""

    return HybridResult(
        doc_id=doc_id,
        final_score=score,
        rrf_score=score,
        bm25_score=None,
        vector_score=None,
        content=content,
        metadata={"timestamp": timestamp},
        score_breakdown={},
    )


def _plan() -> QueryPlan:
    """构造带时间、实体和焦点的固定查询计划。"""

    return QueryPlan(
        original_query="参与者甲在 2025 年 5 月设计了什么物品",
        intent="temporal",
        entities=("参与者甲",),
        focus_terms=("设计", "物品"),
        temporal_anchor="2025-05",
        reference_time=datetime(2025, 6, 1, tzinfo=timezone.utc),
        queries=("参与者甲 2025年5月 设计 物品",),
        required_facets=("entity", "time", "focus"),
        ambiguity_flags=(),
        memory_types=("EPISODIC",),
    )


def test_temporal_entity_and_focus_evidence_prefers_matching_candidate() -> None:
    """目标月份、本人角色和对象焦点应共同提升正确候选。"""

    wrong = _result(1, "家属甲在 2025 年 4 月设计了网页", "2025-04-10T00:00:00Z")
    right = _result(2, "参与者甲在 2025 年 5 月设计了物品", "2025-05-10T00:00:00Z")

    ranked = RetrievalEvidenceScorer().score([wrong, right], _plan())

    assert ranked[0].doc_id == 2
    assert ranked[0].score_breakdown["time"] == 1.0
    assert ranked[0].score_breakdown["entity"] == 1.0
    assert ranked[0].score_breakdown["focus"] > 0.0
    assert ranked[1].score_breakdown["evidence_penalty"] > 0.0


def test_scorer_does_not_mutate_input_candidates() -> None:
    """证据评分必须返回副本，避免污染缓存或其他请求。"""

    candidate = _result(
        2,
        "参与者甲在 2025 年 5 月设计了物品",
        "2025-05-10T00:00:00Z",
    )

    ranked = RetrievalEvidenceScorer().score([candidate], _plan())

    assert ranked[0] is not candidate
    assert candidate.final_score == 0.5
    assert candidate.score_breakdown == {}


def test_explicit_focus_terms_contribute_without_query_tokens() -> None:
    """查询变体没有可用词时，显式焦点词仍应提升匹配候选。"""

    plan = QueryPlan(
        original_query="问",
        intent="default",
        entities=(),
        focus_terms=("设计", "物品"),
        temporal_anchor=None,
        reference_time=datetime(2025, 6, 1, tzinfo=timezone.utc),
        queries=("问",),
        required_facets=("focus",),
        ambiguity_flags=(),
        memory_types=(),
    )
    unrelated = _result(1, "无关内容", "2025-05-10T00:00:00Z")
    focused = _result(2, "设计物品", "2025-05-10T00:00:00Z")

    ranked = RetrievalEvidenceScorer().score([unrelated, focused], plan)

    assert ranked[0].doc_id == 2
    assert ranked[0].score_breakdown["focus"] == 1.0


def test_event_time_is_valid_temporal_evidence() -> None:
    """event_time 应同时参与时间匹配并避免无时间证据惩罚。"""

    candidate = _result(1, "五月事件", "2025-05-10T00:00:00Z")
    candidate.metadata = {"event_time": "2025-05-10T00:00:00Z"}

    ranked = RetrievalEvidenceScorer().score([candidate], _plan())

    assert ranked[0].score_breakdown["time"] == 1.0
    assert ranked[0].score_breakdown["evidence_penalty"] == 0.02
