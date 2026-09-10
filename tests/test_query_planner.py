"""查询计划规范化与固定边界测试。"""

import json
from dataclasses import replace
from datetime import datetime, timezone

from core.features.memory.application.retrieval_optimizer import RetrievalOptimizer
from core.features.retrieval.multi_query_fusion import (
    fuse_query_results,
    split_candidate_budget,
)
from core.features.retrieval.query_planner import QueryPlanner
from core.features.retrieval.query_rewriter import QueryIntent, QueryRewriter
from core.features.retrieval.rrf_fusion import HybridResult


def _candidate(doc_id: int, score: float) -> HybridResult:
    """构造多查询融合与缓存测试使用的最小候选。"""

    return HybridResult(
        doc_id=doc_id,
        final_score=score,
        rrf_score=score,
        bm25_score=None,
        vector_score=None,
        content=f"候选{doc_id}",
        metadata={"privacy_level": "shared"},
        score_breakdown={"source_score": score},
    )


def test_query_plan_normalizes_and_exposes_cache_compatibility() -> None:
    """查询计划必须有限、去重，并兼容既有缓存键。"""

    intent = QueryIntent(
        intent="relational",
        extracted_entities=[" 参与者甲 ", "参与者甲", "对象乙"],
        time_reference="2025-05",
        reference_time=datetime(2025, 6, 1, tzinfo=timezone.utc),
        rewritten_queries=["原始问题", "对象乙 的关系", "对象乙 的关系"],
        memory_types=["RELATIONAL"],
    )

    plan = QueryPlanner.build("原始问题", intent)

    assert plan.intent == "relationship"
    assert plan.queries == ("原始问题", "对象乙 的关系")
    assert plan.rewritten_queries == plan.queries
    assert plan.entities == ("参与者甲", "参与者甲", "对象乙")
    assert set(plan.required_facets) <= {
        "entity",
        "role",
        "time",
        "event",
        "focus",
        "relation",
    }


def test_query_plan_caps_entities_and_queries() -> None:
    """模型输出不能生成超过固定上限的实体或查询。"""

    intent = QueryIntent(
        extracted_entities=[f"实体{index}" for index in range(20)],
        rewritten_queries=[f"查询{index}" for index in range(20)],
    )

    plan = QueryPlanner.build("原始查询", intent)

    assert len(plan.entities) == 8
    assert len(plan.queries) == 3


def test_llm_query_intent_parser_enforces_fixed_boundaries() -> None:
    """模型输出必须经过列表类型、长度与固定枚举边界。"""

    parsed = QueryRewriter._parse_llm_response(
        json.dumps(
            {
                "intent": "越界意图",
                "extracted_entities": "不是列表",
                "time_reference": "任意未来某天",
                "rewritten_queries": "不是列表",
                "memory_types": ["factual", "SECRET", 3],
            },
            ensure_ascii=False,
        ),
        "  降级查询  ",
    )

    assert parsed is not None
    assert parsed.intent == "default"
    assert parsed.extracted_entities == []
    assert parsed.time_reference is None
    assert parsed.rewritten_queries == ["降级查询"]
    assert parsed.memory_types == ["FACTUAL"]


def test_temporal_ambiguity_depends_on_missing_time_anchor() -> None:
    """时间歧义应由时间锚点缺失触发，而不是由实体缺失触发。"""

    without_anchor = QueryPlanner.build(
        "参与者甲那次做了什么",
        QueryIntent(intent="temporal", extracted_entities=["参与者甲"]),
    )
    with_anchor = QueryPlanner.build(
        "昨天做了什么",
        QueryIntent(intent="temporal", time_reference="yesterday"),
    )

    assert "temporal_competition" in without_anchor.ambiguity_flags
    assert "temporal_competition" not in with_anchor.ambiguity_flags


def test_split_budget_never_exceeds_existing_route_budget() -> None:
    """多查询只能切分现有候选预算，不能按查询数量放大。"""

    assert split_candidate_budget(total=24, query_count=3) == (8, 8, 8)
    assert split_candidate_budget(total=10, query_count=3) == (4, 3, 3)
    assert sum(split_candidate_budget(total=2, query_count=3)) == 2


def test_multi_query_fusion_rewards_shared_candidate_without_duplicate() -> None:
    """多条查询共同支持的 canonical 候选只出现一次并获得有限奖励。"""

    fused = fuse_query_results(
        [
            [_candidate(1, 0.9), _candidate(2, 0.8)],
            [_candidate(2, 0.9), _candidate(3, 0.8)],
        ],
        limit=3,
    )

    assert [item.doc_id for item in fused].count(2) == 1
    shared = next(item for item in fused if item.doc_id == 2)
    assert 0.0 < shared.score_breakdown["cross_query_support"] <= 0.08
    assert shared.score_breakdown["source_score"] == 0.9


def test_multi_query_fusion_uses_stable_tie_breaking() -> None:
    """同分且最佳来源排名相同的候选应按 canonical 整数 ID 排序。"""

    fused = fuse_query_results(
        [[_candidate(2, 0.8)], [_candidate(1, 0.8)]],
        limit=2,
    )

    assert [item.doc_id for item in fused] == [1, 2]


def test_complete_query_plan_participates_in_both_cache_keys() -> None:
    """任一规范计划字段变化都必须让结果缓存和会话缓存失配。"""

    base = QueryPlanner.build(
        "参与者甲昨天设计了什么",
        QueryIntent(
            intent="temporal",
            extracted_entities=["参与者甲"],
            time_reference="yesterday",
            reference_time=datetime(2025, 6, 1, tzinfo=timezone.utc),
            rewritten_queries=["参与者甲 昨天 设计"],
            memory_types=["EPISODIC"],
        ),
    )
    variations = (
        replace(base, original_query="另一原始查询"),
        replace(base, entities=("参与者乙",)),
        replace(base, focus_terms=("对象甲",)),
        replace(base, temporal_anchor="today"),
        replace(base, queries=(base.original_query, "另一查询")),
        replace(base, required_facets=("entity",)),
        replace(base, ambiguity_flags=("focus_missing",)),
        replace(base, reference_time=datetime(2025, 6, 2, tzinfo=timezone.utc)),
    )
    optimizer = RetrievalOptimizer(config={})
    base_key = optimizer.cache_key(
        "相同查询",
        5,
        "会话",
        "人格",
        query_intent=base,
    )
    base_session_key = optimizer._session_cache_key(
        "相同查询",
        5,
        "会话",
        "人格",
        query_intent=base,
    )

    for variation in variations:
        assert (
            optimizer.cache_key(
                "相同查询",
                5,
                "会话",
                "人格",
                query_intent=variation,
            )
            != base_key
        )
        assert (
            optimizer._session_cache_key(
                "相同查询",
                5,
                "会话",
                "人格",
                query_intent=variation,
            )
            != base_session_key
        )


def test_legacy_query_intent_time_reference_participates_in_cache_key() -> None:
    """未构建 QueryPlan 的兼容调用仍必须按时间引用隔离缓存。"""

    optimizer = RetrievalOptimizer(config={})
    today = optimizer.cache_key(
        "相同查询",
        5,
        "会话",
        "人格",
        query_intent=QueryIntent(time_reference="today"),
    )
    recent = optimizer.cache_key(
        "相同查询",
        5,
        "会话",
        "人格",
        query_intent=QueryIntent(time_reference="recent"),
    )

    assert today != recent


def test_session_cache_returns_deep_copy_for_query_plan() -> None:
    """会话缓存按计划隔离，并返回不会污染缓存快照的深副本。"""

    plan = QueryPlanner.build("原始查询", QueryIntent())
    optimizer = RetrievalOptimizer(config={})
    original = [_candidate(1, 0.9)]
    optimizer.set_session_cached(
        "原始查询",
        5,
        "会话",
        "人格",
        original,
        query_intent=plan,
    )

    cached = optimizer.get_session_cached(
        "原始查询",
        5,
        "会话",
        "人格",
        query_intent=plan,
    )

    assert cached is not None
    assert cached is not original
    assert cached[0] is not original[0]
    cached[0].metadata["mutated"] = True
    second = optimizer.get_session_cached(
        "原始查询",
        5,
        "会话",
        "人格",
        query_intent=plan,
    )
    assert second is not None
    assert "mutated" not in second[0].metadata
