"""Recall Trace 固定字段脱敏与旧数据读取边界。"""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections.abc import Mapping
from typing import Any

from ...shared.memory_status import effective_memory_status

_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_STAGE_NAMES = frozenset(
    {
        "bm25",
        "graph",
        "injection_decision",
        "personalize",
        "privacy_filter",
        "projection_attachment",
        "query_parse",
        "relation_expansion",
        "rerank",
        "search_memories",
        "vector",
    }
)
_ROUTING_MODES = frozenset({"auto", "manual", "hybrid"})
_PRESETS = frozenset({"tool_first", "low_cost", "balanced", "quality"})
_REASON_CODES = frozenset(
    {
        "AUTO_FALLBACK",
        "AUTO_HISTORY_INTENT",
        "AUTO_LOW_CONTEXT_HEADROOM",
        "AUTO_MEMORY_UNCERTAIN",
        "HYBRID_CLAMPED_MAX",
        "HYBRID_CLAMPED_MIN",
        "INVALID_CONFIG_FALLBACK",
        "MANUAL_SELECTED",
        "PROVIDER_TOOL_UNAVAILABLE",
    }
)
_CONTRIBUTION_SOURCES = frozenset(
    {
        "bm25",
        "document",
        "emotion_boost",
        "graph",
        "importance",
        "optimizer",
        "personalization",
        "projection",
        "recency",
        "relation",
        "rerank",
        "seasonal",
        "unknown",
        "vector",
    }
)
_MEMORY_TYPES = frozenset(
    {
        "episodic",
        "event",
        "fact",
        "factual",
        "general",
        "knowledge",
        "planned",
        "preference",
        "procedural",
        "reflection",
        "relational",
        "semantic",
    }
)
_MEMORY_STATUSES = frozenset(
    {
        "active",
        "archived",
        "candidate",
        "deleted",
        "dormant",
        "invalidated",
        "stale",
        "unknown",
    }
)
_SOURCE_TYPES = frozenset(
    {"chat", "document", "evolution", "graph", "import", "manual", "unknown"}
)
_FILTER_REASONS = frozenset(
    {
        "invalid_revision",
        "invalid_scope",
        "low_score",
        "missing_fields",
        "privacy",
        "stale",
    }
)
_STAGE_NUMERIC_METADATA = frozenset(
    {
        "budget_chars",
        "candidate_count",
        "effective_budget_chars",
        "filtered_count",
        "reason_count",
        "selected_count",
    }
)
_MAX_ITEMS = 100
_MAX_NUMBER = 10**12


def sanitize_trace_payload(value: Any) -> dict[str, Any]:
    """把任意新旧 trace 映射转换为固定安全 DTO。"""
    if not isinstance(value, Mapping):
        raise TypeError("trace_payload_not_mapping")
    trace_id = _safe_trace_id(value.get("trace_id"))
    stages = _sanitize_stages(value.get("stages"))
    results = _sanitize_results(value.get("results"))
    filtered = _sanitize_filtered(value.get("filtered"))
    return {
        "trace_id": trace_id,
        "total_ms": _safe_number(value.get("total_ms"), default=0.0),
        "stages": stages,
        "results": results,
        "filtered": filtered,
        "created_at": _safe_number(value.get("created_at"), default=time.time()),
        "metadata": _sanitize_trace_metadata(value.get("metadata")),
    }


def _sanitize_trace_metadata(value: Any) -> dict[str, bool]:
    """仅保留问题报告状态和候选评分轨迹状态两个安全布尔值。"""
    source = _mapping(value)
    sanitized = {
        "debug_trace_available": bool(source.get("debug_trace_available", False))
    }
    if "debug_reporting_enabled" in source:
        sanitized["debug_reporting_enabled"] = bool(
            source.get("debug_reporting_enabled", False)
        )
    return sanitized


def _sanitize_stages(value: Any) -> list[dict[str, Any]]:
    """仅保留已知阶段及其低基数标量。"""
    sanitized: list[dict[str, Any]] = []
    for item in _mapping_items(value):
        name = _safe_enum(item.get("name"), _STAGE_NAMES)
        if not name:
            continue
        sanitized.append(
            {
                "name": name,
                "duration_ms": _safe_number(item.get("duration_ms"), default=0.0),
                "candidate_count": _safe_int(item.get("candidate_count"), default=0),
                "metadata": _sanitize_stage_metadata(item.get("metadata")),
            }
        )
    return sanitized


def _sanitize_stage_metadata(value: Any) -> dict[str, Any]:
    """过滤阶段 metadata，只留下路由枚举、reason code 和计数。"""
    source = _mapping(value)
    sanitized: dict[str, Any] = {}
    for key in ("routing_mode",):
        selected = _safe_enum(source.get(key), _ROUTING_MODES)
        if selected:
            sanitized[key] = selected
    for key in ("configured_preset", "recommended_preset", "resolved_preset"):
        selected = _safe_enum(source.get(key), _PRESETS)
        if selected:
            sanitized[key] = selected
    for key in _STAGE_NUMERIC_METADATA:
        if key in source:
            sanitized[key] = _safe_int(source[key], default=0)

    reason_code = _safe_enum(source.get("reason_code"), _REASON_CODES)
    reason_codes = source.get("reason_codes")
    if not reason_code and isinstance(reason_codes, (list, tuple)):
        reason_code = next(
            (
                selected
                for item in reason_codes
                if (selected := _safe_enum(item, _REASON_CODES))
            ),
            "",
        )
        sanitized["reason_count"] = min(len(reason_codes), _MAX_ITEMS)
    if reason_code:
        sanitized["reason_code"] = reason_code
    return sanitized


def _sanitize_results(value: Any) -> list[dict[str, Any]]:
    """保留无 canonical ID 的排名、分数、贡献与安全枚举。"""
    sanitized: list[dict[str, Any]] = []
    for index, item in enumerate(_mapping_items(value), start=1):
        sanitized.append(
            {
                "rank": _safe_int(item.get("rank"), default=index, minimum=1),
                "initial_score": _safe_number(item.get("initial_score"), default=0.0),
                "final_score": _safe_number(item.get("final_score"), default=0.0),
                "score_contributions": _sanitize_contributions(
                    item.get("score_contributions")
                ),
                "metadata": _sanitize_result_metadata(item.get("metadata")),
            }
        )
    return sanitized


def _sanitize_contributions(value: Any) -> list[dict[str, Any]]:
    """删除 explanation 和任意 metadata，仅保留贡献标量。"""
    sanitized: list[dict[str, Any]] = []
    for item in _mapping_items(value):
        source = _safe_enum(item.get("source"), _CONTRIBUTION_SOURCES) or "unknown"
        sanitized.append(
            {
                "source": source,
                "score": _safe_number(item.get("score"), default=0.0),
                "weight": _safe_number(item.get("weight"), default=1.0),
            }
        )
    return sanitized


def _sanitize_result_metadata(value: Any) -> dict[str, Any]:
    """仅保留有限的记忆类型、有效状态、来源类型和重要性标量。"""
    source = _mapping(value)
    sanitized: dict[str, Any] = {}
    memory_type = _safe_enum(source.get("memory_type"), _MEMORY_TYPES)
    status = _safe_enum(effective_memory_status(source), _MEMORY_STATUSES)
    source_type = _safe_enum(source.get("source_type"), _SOURCE_TYPES)
    if memory_type:
        sanitized["memory_type"] = memory_type
    if status:
        sanitized["status"] = status
    if source_type:
        sanitized["source_type"] = source_type
    if "importance" in source:
        sanitized["importance"] = _safe_number(
            source["importance"], default=0.0, maximum=1.0
        )
    return sanitized


def _sanitize_filtered(value: Any) -> list[dict[str, Any]]:
    """删除候选 ID，仅保留过滤原因、阶段和分数。"""
    sanitized: list[dict[str, Any]] = []
    for item in _mapping_items(value):
        reason = _safe_enum(item.get("reason"), _FILTER_REASONS)
        if not reason:
            continue
        stage = _safe_enum(item.get("stage"), _STAGE_NAMES)
        entry: dict[str, Any] = {"reason": reason}
        if stage:
            entry["stage"] = stage
        if item.get("score") is not None:
            entry["score"] = _safe_number(item.get("score"), default=0.0)
        sanitized.append(entry)
    return sanitized


def _mapping(value: Any) -> Mapping[str, Any]:
    """把非映射输入安全退化为空映射。"""
    return value if isinstance(value, Mapping) else {}


def _mapping_items(value: Any) -> list[Mapping[str, Any]]:
    """提取有界的映射列表，忽略任意其他元素。"""
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value[:_MAX_ITEMS] if isinstance(item, Mapping)]


def _safe_trace_id(value: Any) -> str:
    """保留合法观测关联码；非法旧值转换为不可逆短摘要。"""
    text = str(value or "").strip()
    if _TRACE_ID_PATTERN.fullmatch(text):
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"invalid-{digest}"


def _safe_enum(value: Any, choices: frozenset[str]) -> str:
    """从固定枚举中选择大小写规范化后的字符串。"""
    text = str(value or "").strip()
    if text in choices:
        return text
    lowered = text.lower()
    return lowered if lowered in choices else ""


def _safe_number(
    value: Any,
    *,
    default: float,
    maximum: float = _MAX_NUMBER,
) -> float:
    """把观测数值规范化为非负有限浮点数。"""
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(0.0, min(maximum, parsed))


def _safe_int(value: Any, *, default: int, minimum: int = 0) -> int:
    """把观测计数规范化为有界整数。"""
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(int(_MAX_NUMBER), parsed))


__all__ = ["sanitize_trace_payload"]
