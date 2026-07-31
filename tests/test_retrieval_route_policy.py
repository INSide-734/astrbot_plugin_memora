"""低风险图路跳过策略与任务创建边界测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.retrieval.query_planner import QueryPlan, QueryPlanner
from core.retrieval.query_rewriter import QueryIntent
from core.retrieval.retrieval_execution import RouteExecutionCoordinator
from core.retrieval.route_policy import should_use_graph_route


def _plan(
    *,
    intent: str = "factual",
    facets: tuple[str, ...] = (),
    ambiguity: tuple[str, ...] = (),
) -> QueryPlan:
    """创建只包含路由决策所需信号的查询计划。"""

    return QueryPlan(
        original_query="query",
        intent=intent,
        entities=(),
        focus_terms=(),
        temporal_anchor=None,
        reference_time=datetime.now(timezone.utc),
        queries=("query",),
        required_facets=facets,
        ambiguity_flags=ambiguity,
        memory_types=(),
    )


@pytest.mark.parametrize("intent", ["factual", "default", "preference"])
def test_low_ambiguity_simple_intents_skip_graph(intent: str) -> None:
    """无图相关维度的简单已知意图可以跳过图路。"""

    assert should_use_graph_route(_plan(intent=intent), None) is False


@pytest.mark.parametrize("intent", ["relationship", "temporal", "contextual"])
def test_graph_sensitive_intents_keep_graph(intent: str) -> None:
    """关系、时间和上下文意图始终保留图路。"""

    assert should_use_graph_route(_plan(intent=intent), None) is True


@pytest.mark.parametrize("facet", ["entity", "relation", "time", "event"])
def test_graph_sensitive_facets_keep_graph(facet: str) -> None:
    """实体、关系、时间和事件维度任一存在时保留图路。"""

    assert should_use_graph_route(_plan(facets=(facet,)), None) is True


def test_missing_unknown_or_ambiguous_plan_keeps_graph() -> None:
    """计划缺失、意图未知或存在歧义时采用保守图路。"""

    assert should_use_graph_route(None, None) is True
    assert should_use_graph_route(_plan(intent="unknown"), None) is True
    assert should_use_graph_route(_plan(ambiguity=("pronoun",)), None) is True


@pytest.mark.parametrize(
    "query",
    [
        "知识库部署清单来自哪次讨论",
        "召回缓存依赖哪个配置开关",
    ],
)
def test_relation_source_and_dependency_queries_keep_graph(query: str) -> None:
    """来源与依赖查询在关键词降级路径中仍必须保留图路。"""

    intent = QueryIntent.from_keywords(query)
    plan = QueryPlanner.build(query=query, intent=intent)

    assert should_use_graph_route(plan, intent) is True


@pytest.mark.asyncio
async def test_skipped_graph_route_does_not_create_graph_task() -> None:
    """策略跳过图路时协调器不调用图检索器，并记录安全布尔状态。"""

    document = MagicMock()
    document.search = AsyncMock(return_value=[])
    graph = MagicMock()
    graph.search = AsyncMock(side_effect=AssertionError("不应创建图任务"))
    coordinator = RouteExecutionCoordinator(document, graph)

    outcome = await coordinator.execute(
        query="query",
        k=5,
        use_graph_route=False,
    )

    graph.search.assert_not_awaited()
    assert outcome.graph_results == []
    assert outcome.timing["graph_route_skipped"] is True
