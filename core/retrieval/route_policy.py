"""根据查询计划决定是否需要创建图检索任务。"""

from __future__ import annotations

from typing import Any

_KNOWN_INTENTS = frozenset(
    {"factual", "default", "preference", "relationship", "temporal", "contextual"}
)
_GRAPH_INTENTS = frozenset({"relationship", "temporal", "contextual"})
_GRAPH_FACETS = frozenset({"entity", "relation", "time", "event"})


def should_use_graph_route(query_plan: Any | None, query_intent: Any | None) -> bool:
    """只在明确低歧义的简单查询中跳过图路，其余情况保守启用。"""

    if query_plan is None:
        return True
    if tuple(getattr(query_plan, "ambiguity_flags", ()) or ()):
        return True
    intent = _resolve_intent(query_plan, query_intent)
    if intent not in _KNOWN_INTENTS or intent in _GRAPH_INTENTS:
        return True
    return _has_graph_facet(query_plan)


def _resolve_intent(query_plan: Any, query_intent: Any | None) -> str:
    """优先从计划读取意图，并在字段缺失时回退到改写结果。"""

    raw_intent = getattr(query_plan, "intent", None)
    if not raw_intent and query_intent is not None:
        raw_intent = getattr(query_intent, "intent", None)
    return str(raw_intent or "").strip().casefold()


def _has_graph_facet(query_plan: Any) -> bool:
    """判断查询计划是否声明实体、关系、时间或事件维度。"""

    facets = {
        str(facet).strip().casefold()
        for facet in (getattr(query_plan, "required_facets", ()) or ())
    }
    return bool(facets & _GRAPH_FACETS)


__all__ = ["should_use_graph_route"]
