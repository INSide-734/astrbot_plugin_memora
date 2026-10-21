"""把 MemoryEngine 适配为离线评测检索协议。

本模块必须保持无 AstrBot 导入：评测包导入契约会在阻塞 AstrBot 的子进程中
导入 ``core.features.evaluation``。因此 query intent/plan 使用轻量本地对象，
GraphQueryScope 只在真正执行检索时延迟导入。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from ..domain.metric_provenance import RetrievalObservation

__all__ = ["make_memory_engine_retriever"]


@dataclass(frozen=True, slots=True)
class _EvaluationQueryIntent:
    """评测用最小查询意图，避免导入 AstrBot 运行时模型。"""

    intent: str = "default"
    reference_time: datetime | None = None
    time_reference: str | None = None
    extracted_entities: tuple[str, ...] = ()
    rewritten_queries: tuple[str, ...] = ()
    memory_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _EvaluationQueryPlan:
    """评测用确定性多查询计划，字段与 QueryPlan 的可读接口一致。"""

    original_query: str
    intent: str = "default"
    entities: tuple[str, ...] = ()
    focus_terms: tuple[str, ...] = ()
    temporal_anchor: str | None = None
    reference_time: datetime | None = None
    queries: tuple[str, ...] = ()
    required_facets: tuple[str, ...] = ()
    ambiguity_flags: tuple[str, ...] = ()
    memory_types: tuple[str, ...] = ()

    @property
    def rewritten_queries(self) -> tuple[str, ...]:
        """兼容缓存键读取。"""

        return self.queries


@dataclass(slots=True)
class EvaluationScopeStats:
    """记录 query scope 解析结果，供报告解释图路跳过。"""

    resolved: int = 0
    unavailable: int = 0
    invalid: int = 0


RetrieverFn = Callable[[Any, int], Any | Awaitable[Any]]


def make_memory_engine_retriever(engine: Any) -> RetrieverFn:
    """将 MemoryEngine 类对象适配为评测器检索协议。

    样本元数据会映射到 ``MemoryEngine.search_memories`` 的关键字参数；默认
    使用生产模型可见路径的 ``require_user_evidence=True``，并支持确定性的
    ``query_plan`` 以真实执行多跳路径。存在 ``_evaluation_cost_counter`` 时，
    返回 ``RetrievalObservation`` 并附带 Provider 调用次数。
    """

    scope_stats = EvaluationScopeStats()

    async def _retriever(case: Any, k: int) -> Any:
        metadata = getattr(case, "metadata", None) or {}
        query_scope = await _resolve_query_scope(engine, case, scope_stats)
        query_plan = _coerce_query_plan(metadata.get("query_plan"), case.query)
        query_intent = _coerce_query_intent(metadata.get("query_intent"))
        kwargs = dict(
            query=case.query,
            k=k,
            session_id=_optional_string(metadata.get("session_id")),
            persona_id=_optional_string(metadata.get("persona_id")),
            user_id=_optional_string(metadata.get("user_id")),
            chat_type=_optional_string(metadata.get("chat_type")) or "private",
            memory_types=_optional_string_list(metadata.get("memory_types")),
            emotion_context=_optional_string_list(metadata.get("emotion_context")),
            recall_type=_optional_string(metadata.get("recall_type")) or "passive",
            chain_depth=_optional_int(metadata.get("chain_depth"), default=0),
            query_intent=query_intent,
            recall_strategy=metadata.get("recall_strategy"),
            query_scope=query_scope,
            require_user_evidence=_require_user_evidence(metadata),
        )
        if query_plan is not None:
            kwargs["query_plan"] = query_plan
        if "reference_time" in metadata:
            kwargs["reference_time"] = _parse_reference_time(
                metadata.get("reference_time")
            )

        counter = getattr(engine, "_evaluation_cost_counter", None)
        if counter is not None:
            counter.reset()
        try:
            results = await engine.search_memories(**kwargs)
        finally:
            observed_calls = counter.value() if counter is not None else None
        if counter is None:
            return results
        return RetrievalObservation(
            documents=list(results or []),
            observed_provider_calls=observed_calls,
            observed_token_cost=None,
        )

    # 私有诊断挂载；不进入公开 retriever 协议，报告可按需读取。
    _retriever.scope_stats = scope_stats  # type: ignore[attr-defined]
    return _retriever


async def _resolve_query_scope(
    engine: Any,
    case: Any,
    stats: EvaluationScopeStats,
) -> Any | None:
    """按 case metadata → 首条相关 canonical 文档 metadata 解析 scope。"""

    metadata = getattr(case, "metadata", None) or {}
    scope_key = _optional_string(metadata.get("scope_key"))
    privacy_level = _optional_string(metadata.get("privacy_level"))
    if scope_key and privacy_level:
        scope = _build_query_scope(scope_key, privacy_level, stats)
        if scope is not None:
            return scope

    memory_id = _first_relevant_memory_id(getattr(case, "relevant_doc_ids", ()))
    if memory_id is None:
        stats.unavailable += 1
        return None
    get_memory = getattr(engine, "get_memory", None)
    if not callable(get_memory):
        stats.unavailable += 1
        return None
    try:
        memory = await cast(Awaitable[Any], get_memory(memory_id))
    except Exception:
        stats.invalid += 1
        return None
    if not isinstance(memory, dict):
        stats.unavailable += 1
        return None
    memory_metadata = memory.get("metadata")
    if not isinstance(memory_metadata, dict):
        stats.unavailable += 1
        return None
    scope_key = _optional_string(memory_metadata.get("scope_key"))
    privacy_level = _optional_string(memory_metadata.get("privacy_level"))
    if not scope_key or not privacy_level:
        stats.unavailable += 1
        return None
    return _build_query_scope(scope_key, privacy_level, stats)


def _build_query_scope(
    scope_key: str,
    privacy_level: str,
    stats: EvaluationScopeStats,
) -> Any | None:
    """延迟导入 GraphQueryScope，保持评测包导入无 AstrBot 依赖。"""

    try:
        from ...memory.graph.domain.models import GraphQueryScope

        scope = GraphQueryScope(scope_key, privacy_level)
    except (ImportError, TypeError, ValueError):
        stats.invalid += 1
        return None
    stats.resolved += 1
    return scope


def _coerce_query_intent(value: Any) -> Any | None:
    """把 metadata dict 转为轻量 intent；已有对象保持鸭子类型。"""

    if value is None:
        return None
    if not isinstance(value, dict):
        return value
    return _EvaluationQueryIntent(
        intent=str(value.get("intent") or "default"),
        reference_time=_parse_reference_time(value.get("reference_time")),
        time_reference=_optional_string(value.get("time_reference")),
        extracted_entities=tuple(
            _optional_string_list(value.get("extracted_entities"))
            or value.get("entities")
            or ()
        ),
        rewritten_queries=tuple(
            _optional_string_list(value.get("rewritten_queries"))
            or value.get("queries")
            or ()
        ),
        memory_types=tuple(_optional_string_list(value.get("memory_types")) or ()),
    )


def _coerce_query_plan(value: Any, query: str) -> Any | None:
    """把 metadata dict 转为确定性 QueryPlan；空 queries 不启用计划路径。"""

    if value is None:
        return None
    if not isinstance(value, dict):
        return value
    queries = tuple(_optional_string_list(value.get("queries")) or ())
    if not queries:
        return None
    return _EvaluationQueryPlan(
        original_query=query,
        intent=str(value.get("intent") or "default"),
        entities=tuple(_optional_string_list(value.get("entities")) or ()),
        focus_terms=tuple(_optional_string_list(value.get("focus_terms")) or ()),
        temporal_anchor=_optional_string(value.get("temporal_anchor")),
        reference_time=_parse_reference_time(value.get("reference_time")),
        queries=queries,
        required_facets=tuple(
            _optional_string_list(value.get("required_facets")) or ()
        ),
        ambiguity_flags=tuple(
            _optional_string_list(value.get("ambiguity_flags")) or ()
        ),
        memory_types=tuple(_optional_string_list(value.get("memory_types")) or ()),
    )


def _require_user_evidence(metadata: dict[str, Any]) -> bool:
    """生产模型可见路径默认要求用户来源证据；仅显式 false 可覆盖。"""

    value = metadata.get("require_user_evidence", True)
    return value is not False


def _first_relevant_memory_id(values: Iterable[Any]) -> int | None:
    """返回首个 positive canonical 整数 ID；负向标记和字符串 ID 跳过。"""

    for value in values:
        text = str(value or "").strip()
        if not text or text == "__no_relevant__":
            continue
        try:
            memory_id = int(text)
        except (TypeError, ValueError):
            continue
        if memory_id > 0 and str(memory_id) == text:
            return memory_id
    return None


def _optional_string(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _optional_string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, Iterable):
        items = list(value)
    else:
        return None
    normalized = [_optional_string(item) for item in items]
    result = [item for item in normalized if item]
    return result or None


def _optional_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_reference_time(value: Any) -> datetime | None:
    """在不导入 AstrBot/运行时模型的评测包中解析 UTC as-of 时间。"""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )
