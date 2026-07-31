"""召回性能样本的安全字段与归一化函数。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Final

TIMING_KEYS: Final[tuple[str, ...]] = (
    "plugin_ready_ms",
    "identity_resolve_ms",
    # 新粒度键
    "query_analysis_ms",
    "conditional_llm_ms",
    "llm_wait_ms",
    "cache_lookup_ms",
    "document_fusion_ms",
    "document_weighting_ms",
    "document_mmr_ms",
    "document_total_ms",
    "graph_keyword_ms",
    "graph_vector_ms",
    "graph_fusion_ms",
    "graph_total_ms",
    "atom_ms",
    "profile_lookup_ms",
    "merge_ms",
    "relation_ms",
    "projection_ms",
    "boost_ms",
    "privacy_ms",
    "candidate_finalize_ms",
    "format_ms",
    "injection_ms",
    # 旧有兼容键（Dashboard 与关停摘要尚未迁移）
    "total_ms",
    "recall_hook_total_ms",
    "retrieval_total_ms",
    "bm25_ms",
    "vector_ms",
    "graph_ms",
    "rerank_ms",
)

COUNT_KEYS: Final[tuple[str, ...]] = (
    "query_count",
    "candidate_count",
    "selected_count",
    "injection_chars",
    "conditional_llm_calls",
)
BOOL_KEYS: Final[tuple[str, ...]] = (
    "cache_hit",
    "conditional_llm_triggered",
    "conditional_llm_used",
    "conditional_llm_timed_out",
    "document_vector_timed_out",
    "graph_vector_timed_out",
    "deadline_exhausted",
    "partial_fallback",
    "graph_route_skipped",
    "document_route_degraded",
    "graph_route_degraded",
    "atom_route_degraded",
    "route_aborted",
)
STATUS_VALUES: Final[frozenset[str]] = frozenset(
    {
        "disabled",
        "not_triggered",
        "rate_limited",
        "shadow",
        "used",
        "timeout",
        "invalid",
        "failed",
    }
)


def sanitize_recall_sample(
    sample: Mapping[str, object],
) -> dict[str, float | int | bool | str]:
    """把任意内部样本收敛为有限标量 allowlist。"""
    safe: dict[str, float | int | bool | str] = {}
    for key in TIMING_KEYS:
        value = sample.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            safe[key] = max(0.0, float(value))
    for key in COUNT_KEYS:
        value = sample.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            safe[key] = max(0, value)
    for key in BOOL_KEYS:
        value = sample.get(key)
        if isinstance(value, bool):
            safe[key] = value
    status = sample.get("conditional_llm_status")
    if isinstance(status, str) and status in STATUS_VALUES:
        safe["conditional_llm_status"] = status
    return safe
