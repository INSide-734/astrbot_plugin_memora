"""Explainable recall capture wrapper."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .trace_models import (
    FilteredCandidate,
    RecallTrace,
    TraceResult,
    TraceStage,
    json_safe,
)
from .trace_store import RecallTraceStore

_CONTENT_PREVIEW_CHARS = 160
_MAX_STRING_CHARS = 300
_MAX_LIST_ITEMS = 20
_MAX_DICT_ITEMS = 30
_MAX_DEPTH = 4

_ALLOWED_RESULT_METADATA_KEYS = {
    "memory_type",
    "importance",
    "status",
    "memory_status",
    "create_time",
    "canonical_summary",
    "source_type",
}

_SENSITIVE_METADATA_KEYS = {
    "content",
    "full_content",
    "raw_content",
    "text",
    "user_id",
    "session_id",
    "raw",
    "source",
    "private",
}

_SENSITIVE_KEY_FRAGMENTS = {
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "auth",
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


@dataclass(slots=True)
class _DebugContribution:
    source: str
    score: float
    weight: float = 1.0
    explanation: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "score": self.score,
            "weight": self.weight,
            "explanation": self.explanation,
            "metadata": json_safe(self.metadata),
        }


async def capture_explainable_recall(
    engine: Any,
    request_params: Mapping[str, Any],
    *,
    store: RecallTraceStore | None = None,
) -> dict[str, Any]:
    """Run a recall search, persist its trace, and return a JSON-safe DTO."""
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
    trace = RecallTrace(
        trace_id=str(uuid.uuid4()),
        query=query,
        total_ms=round(total_ms, 3),
        stages=[
            TraceStage(
                name="search_memories",
                duration_ms=round(total_ms, 3),
                candidate_count=len(result_list),
                metadata={"engine": engine.__class__.__name__},
            )
        ],
        results=_normalize_results(result_list, debug_trace),
        filtered=filtered,
        metadata={
            "request": json_safe(
                _bounded_value(
                    {
                        key: value
                        for key, value in request_params.items()
                        if key in _SEARCH_KEYS and key != "query"
                    }
                )
            ),
            "debug_trace_available": bool(debug_trace),
        },
    )
    payload = trace.to_dict()

    if store is not None:
        await store.save_trace(payload)

    return json_safe(payload)


def _as_list(value: Any) -> list[Any]:
    try:
        return list(value or [])
    except TypeError:
        return []


def _extract_debug_trace(engine: Any, results: Any) -> list[dict[str, Any]]:
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
    candidates: list[FilteredCandidate] = []
    for source in (
        getattr(engine, "filtered_candidates", None),
        getattr(engine, "last_filtered_candidates", None),
        getattr(engine, "_last_filtered_candidates", None),
        getattr(results, "filtered_candidates", None),
    ):
        for item in _mapping_list(source):
            doc_id = item.get("doc_id")
            reason = item.get("reason")
            if doc_id is None or not reason:
                continue
            candidates.append(
                FilteredCandidate(
                    doc_id=str(doc_id),
                    reason=str(reason),
                    stage=_optional_str(item.get("stage")),
                    score=_optional_float(item.get("score")),
                    metadata=_bounded_value(
                        {
                            key: value
                            for key, value in item.items()
                            if key not in {"doc_id", "reason", "stage", "score"}
                        }
                    ),
                )
            )
        if candidates:
            break
    return candidates


def _normalize_results(
    results: list[Any],
    debug_trace: list[dict[str, Any]],
) -> list[TraceResult]:
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


def _score_contributions(trace_entry: Mapping[str, Any]) -> list[_DebugContribution]:
    if not trace_entry:
        return []
    source = str(trace_entry.get("source") or "optimizer")
    final_score = _coerce_float(trace_entry.get("final_score"), 0.0)
    return [
        _DebugContribution(
            source=source,
            score=final_score,
            weight=1.0,
            explanation=_optional_str(trace_entry.get("explanation")),
            metadata=_bounded_value(
                {
                    key: value
                    for key, value in trace_entry.items()
                    if key not in {"source", "score", "weight", "explanation"}
                }
            ),
        )
    ]


def _sanitize_result_metadata(result: Any) -> dict[str, Any]:
    source_metadata = _result_metadata(result)
    sanitized: dict[str, Any] = {}
    for key in _ALLOWED_RESULT_METADATA_KEYS:
        if key in source_metadata:
            sanitized[key] = _bounded_value(source_metadata[key])
    content = _result_value(result, "content")
    if content is not None:
        sanitized["content_preview"] = str(content)[:_CONTENT_PREVIEW_CHARS]
    score_breakdown = _result_value(result, "score_breakdown")
    if score_breakdown:
        sanitized["score_breakdown"] = _bounded_value(score_breakdown)
    return sanitized


def _result_metadata(result: Any) -> dict[str, Any]:
    metadata = _result_value(result, "metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _bounded_value(value: Any, depth: int = 0) -> Any:
    if depth >= _MAX_DEPTH:
        if isinstance(value, Mapping):
            return {}
        if _is_sequence(value):
            return []
        if isinstance(value, str):
            return value[:_MAX_STRING_CHARS]
        return json_safe(value)

    if isinstance(value, str):
        return value[:_MAX_STRING_CHARS]
    if isinstance(value, int | float | bool) or value is None:
        return value
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:_MAX_DICT_ITEMS]:
            key = str(raw_key)
            if _is_sensitive_metadata_key(key):
                continue
            sanitized[key] = _bounded_value(raw_value, depth + 1)
        return sanitized
    if _is_sequence(value):
        return [
            _bounded_value(item, depth + 1)
            for item in list(value)[:_MAX_LIST_ITEMS]
        ]
    return json_safe(value)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, list | tuple | set | frozenset)


def _is_sensitive_metadata_key(key: str) -> bool:
    normalized = key.casefold().strip()
    if normalized in _SENSITIVE_METADATA_KEYS:
        return True
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _result_value(result: Any, key: str) -> Any:
    if isinstance(result, Mapping):
        return result.get(key)
    return getattr(result, key, None)


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    try:
        items = list(value)
    except TypeError:
        return []
    return [dict(item) for item in items if isinstance(item, Mapping)]


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


__all__ = ["capture_explainable_recall"]
