"""提供反思生成阶段的隐私安全诊断事件。"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...observability.infrastructure.debug_reporter import report_debug_event

if TYPE_CHECKING:
    from ...reflection.domain.summary_models import TopicCandidateSelection

_MODES = frozenset({"off", "observe", "full", "top_k"})
_STATUSES = frozenset({"ready", "degraded", "unavailable",
                      "backfilling", "empty", "unknown"})
_BUCKETS = frozenset(
    {
        "0",
        "1-8",
        "9-16",
        "17-32",
        "33-64",
        "65-128",
        "129-256",
        "257+",
        "unknown",
    }
)
_BUDGET_REASONS = frozenset(
    {"none", "count_exceeded", "token_exceeded", "token_truncated", "unknown"}
)
_REASONS = frozenset(
    {
        "mode_off",
        "scope_unavailable",
        "catalog_ready",
        "catalog_degraded",
        "catalog_unavailable",
        "no_full_candidates",
        "no_fill_candidates",
        "count_budget_exceeded",
        "token_budget_exceeded",
        "token_truncated",
        "full_success",
        "top_k_success",
        "fill_only",
        "observe_shadow",
        "selector_failed",
        "candidate_shortfall",
        "unknown_mode",
        "unknown",
    }
)


@dataclass(frozen=True, slots=True)
class TopicCandidateObservation:
    """候选观测的固定低基数字段；不允许携带正文、scope 或 ID。"""

    mode: str
    effective_mode: str
    catalog_status: str
    topic_count_bucket: str
    reason_code: str
    budget_reason: str = "none"
    token_source_available: bool = False
    candidate_count: int = 0
    bm25_hit_count: int = 0
    recent_fill_count: int = 0
    identity_drop_count: int = 0
    selector_duration_ms: float = 0.0
    prompt_chars: int = 0
    prompt_tokens: int | None = None
    exact_reuse_count: int = 0
    exact_topic_count: int = 0
    duplicate_topic_count: int = 0
    status: str = "completed"

    def __post_init__(self) -> None:
        """校验闭集枚举、非负标量和 token 缺失语义。"""

        for field, allowed in (
            ("mode", _MODES),
            ("effective_mode", _MODES),
            ("catalog_status", _STATUSES),
            ("topic_count_bucket", _BUCKETS),
            ("reason_code", _REASONS),
            ("budget_reason", _BUDGET_REASONS),
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or value not in allowed:
                raise ValueError(f"{field}_invalid")
        if self.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("status_invalid")
        if not isinstance(self.token_source_available, bool):
            raise TypeError("token_source_available_invalid")
        for field in (
            "candidate_count",
            "bm25_hit_count",
            "recent_fill_count",
            "identity_drop_count",
            "prompt_chars",
            "exact_reuse_count",
            "exact_topic_count",
            "duplicate_topic_count",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field}_invalid")
        if (
            isinstance(self.selector_duration_ms, bool)
            or not isinstance(self.selector_duration_ms, (int, float))
            or not math.isfinite(float(self.selector_duration_ms))
            or self.selector_duration_ms < 0
        ):
            raise ValueError("selector_duration_ms_invalid")
        if self.token_source_available != (self.prompt_tokens is not None):
            raise ValueError("token_source_available_mismatch")
        if self.prompt_tokens is not None and (
            isinstance(self.prompt_tokens, bool)
            or not isinstance(self.prompt_tokens, int)
            or self.prompt_tokens < 0
        ):
            raise ValueError("prompt_tokens_invalid")


def report_topic_candidate_event(
    observation: TopicCandidateObservation | None = None, **fields: Any
) -> bool:
    """只发射通过类型和 allowlist 校验的候选观测。"""

    try:
        if observation is None:
            observation = TopicCandidateObservation(**fields)
        elif fields or not isinstance(observation, TopicCandidateObservation):
            return False
    except (TypeError, ValueError):
        return False
    payload: dict[str, object] = {
        "component": "reflection",
        "stage": "candidate_selection",
        "status": observation.status,
        "mode": observation.mode,
        "effective_mode": observation.effective_mode,
        "catalog_status": observation.catalog_status,
        "catalog_topic_count_bucket": observation.topic_count_bucket,
        "reason_code": observation.reason_code,
        "budget_reason": observation.budget_reason,
        "token_source_available": (
            "available" if observation.token_source_available else "unavailable"
        ),
        "candidate_count": observation.candidate_count,
        "bm25_hit_count": observation.bm25_hit_count,
        "recent_fill_count": observation.recent_fill_count,
        "identity_drop_count": observation.identity_drop_count,
        "selector_duration_ms": observation.selector_duration_ms,
        "prompt_chars": observation.prompt_chars,
        "exact_reuse_count": observation.exact_reuse_count,
        "exact_topic_count": observation.exact_topic_count,
        "duplicate_topic_count": observation.duplicate_topic_count,
    }
    if observation.prompt_tokens is not None:
        payload["prompt_tokens"] = observation.prompt_tokens
    report_debug_event("reflection_state", **payload)
    return True


def report_topic_candidate_selection(selection: TopicCandidateSelection) -> bool:
    """将已完成选择的安全标量交给 typed 事件，不传递标签或来源字段。"""
    reason = selection.reason_code
    if reason.startswith("observe_shadow_"):
        reason = "observe_shadow"
    elif reason not in _REASONS:
        reason = "selector_failed"
    return report_topic_candidate_event(
        mode=selection.mode,
        effective_mode=selection.effective_mode,
        catalog_status=selection.catalog_status,
        topic_count_bucket=selection.topic_count_bucket,
        reason_code=reason,
        budget_reason=selection.budget_reason or "none",
        candidate_count=selection.candidate_count,
        bm25_hit_count=selection.bm25_hit_count,
        recent_fill_count=selection.recent_fill_count,
        identity_drop_count=selection.identity_drop_count,
        selector_duration_ms=selection.selector_duration_ms,
    )


def report_generation_stage(
    stage: str,
    status: str,
    reason_code: str,
    started: float,
    **numeric_fields: int | float | None,
) -> None:
    """发射固定阶段和非空数值字段，不记录生成输入或输出正文。"""

    fields = {key: value for key, value in numeric_fields.items()
              if value is not None}
    report_debug_event(
        "storage_task",
        component="reflection",
        stage=stage,
        status=status,
        reason_code=reason_code,
        task_type="storage",
        duration_ms=max(0.0, (time.perf_counter() - started) * 1000.0),
        **fields,
    )


__all__ = [
    "TopicCandidateObservation",
    "report_generation_stage",
    "report_topic_candidate_event",
    "report_topic_candidate_selection",
]
