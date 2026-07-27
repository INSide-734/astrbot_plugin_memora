"""Explainable recall capture wrapper."""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Mapping
from typing import Any

from ..injection.models import RequestSignals
from ..injection.router import InjectionRoutingConfig, InjectionStrategyRouter
from .trace_models import (
    FilteredCandidate,
    RecallTrace,
    ScoreContribution,
    TraceResult,
    TraceStage,
    json_safe,
)
from .trace_store import RecallTraceStore

_ALLOWED_RESULT_METADATA_KEYS = {
    "memory_type",
    "importance",
    "status",
    "memory_status",
    "source_type",
}

_SEARCH_KEYS = {
    "query",
    "k",
    "session_id",
    "persona_id",
    "user_id",
    "chat_type",
    "memory_types",
    "emotion_context",
    "recall_type",
    "chain_depth",
    "query_intent",
    "recall_strategy",
}


async def capture_explainable_recall(
    engine: Any,
    request_params: Mapping[str, Any],
    *,
    store: RecallTraceStore | None = None,
    routing_config: InjectionRoutingConfig | None = None,
    debug_reporting_enabled: bool | None = None,
) -> dict[str, Any]:
    """执行召回并只持久化固定安全标量组成的 trace DTO。"""
    query = str(request_params.get("query", "") or "")
    search_params = {
        key: value
        for key, value in request_params.items()
        if key in _SEARCH_KEYS and value is not None
    }
    search_params["query"] = query
    search_params["trace_debug"] = True

    started = time.perf_counter()
    results = await engine.search_memories(**search_params)
    total_ms = (time.perf_counter() - started) * 1000

    result_list = _as_list(results)
    debug_trace = _extract_debug_trace(engine, results)
    filtered = _extract_filtered_candidates(engine, results)
    normalized_results = _normalize_results(result_list, debug_trace)
    decision_started = time.perf_counter()
    decision = InjectionStrategyRouter().route_final(
        routing_config or InjectionRoutingConfig(),
        _candidate_signals(result_list, normalized_results, request_params),
    )
    decision_ms = (time.perf_counter() - decision_started) * 1000
    trace_metadata = {"debug_trace_available": bool(debug_trace)}
    if debug_reporting_enabled is not None:
        trace_metadata["debug_reporting_enabled"] = bool(debug_reporting_enabled)
    trace = RecallTrace(
        trace_id=str(uuid.uuid4()),
        query="",
        total_ms=round(total_ms, 3),
        stages=[
            TraceStage(
                name="search_memories",
                duration_ms=round(total_ms, 3),
                candidate_count=len(result_list),
                metadata={},
            ),
            TraceStage(
                name="injection_decision",
                duration_ms=round(decision_ms, 3),
                candidate_count=len(normalized_results),
                metadata={
                    "routing_mode": decision.routing_mode.value,
                    "configured_preset": decision.configured_preset.value,
                    "recommended_preset": decision.recommended_preset.value,
                    "resolved_preset": decision.resolved_preset.value,
                    "effective_budget_chars": decision.memory_budget_chars,
                    "reason_codes": list(decision.reason_codes),
                },
            ),
        ],
        results=normalized_results,
        filtered=filtered,
        metadata=trace_metadata,
    )
    payload = trace.to_dict()

    if store is not None:
        await store.save_trace(payload)

    return json_safe(payload)


def _candidate_signals(
    raw_results: list[Any],
    results: list[TraceResult],
    request_params: Mapping[str, Any],
) -> RequestSignals:
    """从现有搜索结果计算路由信号，不复制正文到 trace。"""
    normalized_scores = []
    for item in results:
        try:
            score = float(item.final_score)
        except (TypeError, ValueError):
            score = 0.0
        if not math.isfinite(score):
            score = 0.0
        normalized_scores.append(max(0.0, min(1.0, score)))
    scores = sorted(normalized_scores, reverse=True)
    top_confidence = scores[0] if scores else 0.0
    score_gap = top_confidence - scores[1] if len(scores) > 1 else top_confidence
    token_sets = [
        set(str(_result_value(item, "content") or "").casefold().split())
        for item in raw_results
    ]
    similarities: list[float] = []
    for index, first in enumerate(token_sets):
        for second in token_sets[index + 1 :]:
            union = first | second
            similarities.append(len(first & second) / len(union) if union else 0.0)
    redundancy = sum(similarities) / len(similarities) if similarities else 0.0
    estimated_chars = sum(
        len(str(_result_value(item, "content") or ""))
        + len(str(_result_metadata(item).get("canonical_summary", "")))
        for item in raw_results
    )
    intent = str(request_params.get("query_intent") or "default")
    return RequestSignals(
        query_intent=intent,
        explicit_history_request=intent
        in {
            "relationship",
            "relational",
            "temporal",
            "preference",
            "contextual",
        },
        tools_supported=False,
        memory_tool_available=False,
        context_headroom_chars=10_000,
        candidate_count=len(results),
        top_confidence=top_confidence,
        score_gap=max(0.0, min(1.0, score_gap)),
        candidate_redundancy=max(0.0, min(1.0, redundancy)),
        temporal_conflict=False,
        estimated_payload_chars=estimated_chars,
        chat_type=str(request_params.get("chat_type") or "private"),
    )


def _as_list(value: Any) -> list[Any]:
    """把可迭代搜索结果规范化为列表。"""
    try:
        return list(value or [])
    except TypeError:
        return []


def _extract_debug_trace(engine: Any, results: Any) -> list[dict[str, Any]]:
    """读取已有的内部 debug 计分轨迹，不创建新的内容副本。"""
    for source in (
        getattr(engine, "debug_trace", None),
        getattr(engine, "last_debug_trace", None),
        getattr(engine, "_last_debug_trace", None),
        getattr(results, "debug_trace", None),
    ):
        items = _mapping_list(source)
        if items:
            return items

    retrieval = getattr(engine, "_retrieval", None)
    if retrieval is not None:
        for source in (
            getattr(retrieval, "debug_trace", None),
            getattr(retrieval, "last_debug_trace", None),
            getattr(retrieval, "_last_debug_trace", None),
        ):
            items = _mapping_list(source)
            if items:
                return items
    return []


def _extract_filtered_candidates(engine: Any, results: Any) -> list[FilteredCandidate]:
    """只提取过滤原因、阶段和分数，不复制候选 ID 或 metadata。"""
    candidates: list[FilteredCandidate] = []
    for source in (
        getattr(engine, "filtered_candidates", None),
        getattr(engine, "last_filtered_candidates", None),
        getattr(engine, "_last_filtered_candidates", None),
        getattr(results, "filtered_candidates", None),
    ):
        for item in _mapping_list(source):
            reason = item.get("reason")
            if not reason:
                continue
            candidates.append(
                FilteredCandidate(
                    doc_id="",
                    reason=str(reason),
                    stage=_optional_str(item.get("stage")),
                    score=_optional_float(item.get("score")),
                    metadata={},
                )
            )
        if candidates:
            break
    return candidates


def _normalize_results(
    results: list[Any],
    debug_trace: list[dict[str, Any]],
) -> list[TraceResult]:
    """把搜索结果转换成不含正文副本的内部计分模型。"""
    trace_by_doc_id = {
        str(item.get("doc_id")): item
        for item in debug_trace
        if item.get("doc_id") is not None
    }
    normalized: list[TraceResult] = []
    for rank, result in enumerate(results, start=1):
        doc_id = _result_value(result, "doc_id")
        if doc_id is None:
            continue
        doc_id_text = str(doc_id)
        trace_entry = trace_by_doc_id.get(doc_id_text, {})
        final_score = _coerce_float(
            _first_present(
                trace_entry.get("final_score"),
                _result_value(result, "final_score"),
                _result_value(result, "score"),
            ),
            0.0,
        )
        initial_score = _coerce_float(
            _first_present(
                trace_entry.get("initial_score"),
                _result_value(result, "initial_score"),
                _result_value(result, "score"),
                final_score,
            ),
            final_score,
        )
        metadata = _sanitize_result_metadata(result)
        normalized.append(
            TraceResult(
                doc_id=doc_id_text,
                rank=rank,
                initial_score=initial_score,
                final_score=final_score,
                score_contributions=_score_contributions(trace_entry),
                graph_paths=[],
                metadata=metadata,
            )
        )
    return normalized


def _score_contributions(trace_entry: Mapping[str, Any]) -> list[ScoreContribution]:
    """只保留 debug 贡献的来源和分数标量。"""
    if not trace_entry:
        return []
    source = str(trace_entry.get("source") or "optimizer")
    final_score = _coerce_float(trace_entry.get("final_score"), 0.0)
    return [
        ScoreContribution(
            source=source,
            score=final_score,
            weight=1.0,
        )
    ]


def _sanitize_result_metadata(result: Any) -> dict[str, Any]:
    """在内部模型阶段只复制明确允许的标量 metadata。"""
    source_metadata = _result_metadata(result)
    sanitized: dict[str, Any] = {}
    for key in _ALLOWED_RESULT_METADATA_KEYS:
        value = source_metadata.get(key)
        if isinstance(value, (str, int, float, bool)):
            sanitized[key] = value
    return sanitized


def _result_metadata(result: Any) -> dict[str, Any]:
    """读取搜索结果 metadata 的浅副本。"""
    metadata = _result_value(result, "metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _result_value(result: Any, key: str) -> Any:
    """兼容映射与对象形式读取搜索结果字段。"""
    if isinstance(result, Mapping):
        return result.get(key)
    return getattr(result, key, None)


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    """只保留列表中的映射项。"""
    if value is None:
        return []
    try:
        items = list(value)
    except TypeError:
        return []
    return [dict(item) for item in items if isinstance(item, Mapping)]


def _first_present(*values: Any) -> Any:
    """返回第一个非 None 值。"""
    for value in values:
        if value is not None:
            return value
    return None


def _coerce_float(value: Any, default: float) -> float:
    """把计分值转换为浮点数。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> float | None:
    """把可选计分值转换为浮点数。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    """把可选阶段值转换为字符串。"""
    return None if value is None else str(value)


__all__ = ["capture_explainable_recall"]
