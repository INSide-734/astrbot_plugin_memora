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

    raw_intent = getattr(query_plan, "intent", None)
    if not raw_intent and query_intent is not None:
        raw_intent = getattr(query_intent, "intent", None)
    intent = str(raw_intent or "").strip().casefold()
    if intent not in _KNOWN_INTENTS or intent in _GRAPH_INTENTS:
        return True

    required_facets = {
        str(facet).strip().casefold()
        for facet in (getattr(query_plan, "required_facets", ()) or ())
    }
    return bool(required_facets & _GRAPH_FACETS)


__all__ = ["should_use_graph_route"]
