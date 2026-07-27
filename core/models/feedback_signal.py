"""反馈排序实验的固定事件、聚合和安全策略契约。"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

FEEDBACK_REASON_CODES = frozenset(
    {
        "accepted",
        "untrusted_event_source",
        "invalid_outcome",
        "invalid_event_time",
        "duplicate_event",
        "rate_limited",
        "scope_mismatch",
        "insufficient_evidence",
        "signal_expired",
        "weight_delta_capped",
        "baseline_retained",
        "evaluation_prerequisite_unmet",
    }
)


class FeedbackOutcome(str, Enum):
    """受控适配器允许的有限结果枚举。"""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"

    @property
    def reward(self) -> float:
        """把结果映射为固定 reward，不接受调用方浮点数。"""

        return {self.POSITIVE: 1.0, self.NEUTRAL: 0.5, self.NEGATIVE: 0.0}[self]


class FeedbackAdapterKind(str, Enum):
    """可注册的内部反馈适配器类别。"""

    RETRIEVAL_RESULT = "retrieval_result"
    TOOL_OUTCOME = "tool_outcome"
    REVIEW_DECISION = "review_decision"


@dataclass(frozen=True, slots=True)
class FeedbackSignalPolicy:
    """反馈事件时间、限流、衰减和权重变化的固定策略。"""

    policy_version: int = 1
    window_seconds: int = 3600
    max_event_age_seconds: int = 7 * 86400
    max_future_skew_seconds: int = 60
    max_events_per_window: int = 32
    max_events_per_domain: int = 256
    max_global_events: int = 2048
    min_independent_windows: int = 2
    half_life_seconds: int = 7 * 86400
    baseline_document_weight: float = 0.7
    baseline_graph_weight: float = 0.3
    max_weight_delta: float = 0.1

    def __post_init__(self) -> None:
        """校验有限正整数和权重边界，防止策略自身绕过安全门。"""

        for value in (
            self.policy_version,
            self.window_seconds,
            self.max_event_age_seconds,
            self.max_future_skew_seconds,
            self.max_events_per_window,
            self.max_events_per_domain,
            self.max_global_events,
            self.min_independent_windows,
            self.half_life_seconds,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("feedback_policy_integer_invalid")
        for value in (
            self.baseline_document_weight,
            self.baseline_graph_weight,
        ):
            if not math.isfinite(value) or not 0.1 <= value <= 0.9:
                raise ValueError("feedback_policy_weight_invalid")
        if (
            not math.isfinite(self.max_weight_delta)
            or not 0.0 <= self.max_weight_delta <= 0.4
        ):
            raise ValueError("feedback_policy_weight_invalid")
        if not math.isclose(
            self.baseline_document_weight + self.baseline_graph_weight,
            1.0,
        ):
            raise ValueError("feedback_policy_baseline_invalid")


@dataclass(frozen=True, slots=True)
class TrustedFeedbackEvent:
    """已由内部适配器生成的最小反馈事件。"""

    adapter_kind: FeedbackAdapterKind
    decision_key: str
    variant_key: str
    outcome: FeedbackOutcome
    scope_domain: str
    persona_domain: str | None
    observed_at: datetime
    window_key: str
    dedupe_key: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        """校验固定枚举、UTC 时间和内部不透明键。"""

        if not isinstance(self.adapter_kind, FeedbackAdapterKind):
            raise ValueError("feedback_adapter_invalid")
        if not isinstance(self.outcome, FeedbackOutcome):
            raise ValueError("feedback_outcome_invalid")
        for value, reason in (
            (self.decision_key, "feedback_decision_key_invalid"),
            (self.variant_key, "feedback_variant_key_invalid"),
            (self.scope_domain, "feedback_scope_invalid"),
            (self.window_key, "feedback_window_invalid"),
            (self.dedupe_key, "feedback_dedupe_invalid"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(reason)
        if not self.window_key.isdigit():
            raise ValueError("feedback_window_invalid")
        if len(self.dedupe_key) != 64 or any(
            char not in "0123456789abcdef" for char in self.dedupe_key
        ):
            raise ValueError("feedback_dedupe_invalid")
        if self.persona_domain is not None and (
            not isinstance(self.persona_domain, str) or not self.persona_domain.strip()
        ):
            raise ValueError("feedback_persona_invalid")
        if not isinstance(self.observed_at, datetime):
            raise ValueError("feedback_event_time_invalid")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(
            0
        ):
            raise ValueError("feedback_event_time_invalid")
        if isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise ValueError("feedback_schema_version_invalid")


def build_trusted_feedback_event(
    *,
    adapter_kind: FeedbackAdapterKind,
    decision_key: str,
    variant_key: str,
    outcome: FeedbackOutcome,
    scope_domain: str,
    persona_domain: str | None,
    observed_at: datetime,
    window_seconds: int,
) -> TrustedFeedbackEvent:
    """由受控内部上下文构造事件，派生 window 和 dedupe key。"""

    if not isinstance(outcome, FeedbackOutcome):
        raise ValueError("feedback_outcome_invalid")
    if not isinstance(adapter_kind, FeedbackAdapterKind):
        raise ValueError("feedback_adapter_invalid")
    if (
        isinstance(window_seconds, bool)
        or not isinstance(window_seconds, int)
        or window_seconds <= 0
    ):
        raise ValueError("feedback_window_invalid")
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise ValueError("feedback_event_time_invalid")
    normalized_time = observed_at.astimezone(timezone.utc)
    epoch = int(normalized_time.timestamp())
    window_key = str(epoch // window_seconds)
    raw_key = "|".join(
        (
            adapter_kind.value,
            decision_key,
            variant_key,
            scope_domain,
            persona_domain or "",
            window_key,
        )
    )
    dedupe_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return TrustedFeedbackEvent(
        adapter_kind=adapter_kind,
        decision_key=decision_key,
        variant_key=variant_key,
        outcome=outcome,
        scope_domain=scope_domain,
        persona_domain=persona_domain,
        observed_at=normalized_time,
        window_key=window_key,
        dedupe_key=dedupe_key,
    )


@dataclass(frozen=True, slots=True)
class FeedbackSignalAggregate:
    """一个 scope/persona/window 的有界派生排序建议。"""

    scope_domain: str
    persona_domain: str | None
    window_start: datetime
    window_end: datetime
    accepted_count: int
    independent_window_count: int
    decayed_support: float
    proposed_document_weight: float
    proposed_graph_weight: float
    delta_from_baseline: float
    status: str
    policy_version: int

    def __post_init__(self) -> None:
        """校验聚合计数、权重范围和固定状态。"""

        if not self.scope_domain.strip() or self.status not in {
            "shadow",
            "candidate",
            "rejected",
            "expired",
            "baseline_retained",
        }:
            raise ValueError("feedback_aggregate_status_invalid")
        if self.accepted_count < 0 or self.independent_window_count < 0:
            raise ValueError("feedback_aggregate_count_invalid")
        for value in (
            self.decayed_support,
            self.proposed_document_weight,
            self.proposed_graph_weight,
            self.delta_from_baseline,
        ):
            if not math.isfinite(value):
                raise ValueError("feedback_aggregate_number_invalid")
        if not 0.0 <= self.decayed_support <= 1.0:
            raise ValueError("feedback_aggregate_support_invalid")
        if not 0.1 <= self.proposed_document_weight <= 0.9:
            raise ValueError("feedback_aggregate_weight_invalid")
        if not 0.1 <= self.proposed_graph_weight <= 0.9:
            raise ValueError("feedback_aggregate_weight_invalid")
        if not math.isclose(
            self.proposed_document_weight + self.proposed_graph_weight,
            1.0,
        ):
            raise ValueError("feedback_aggregate_weight_invalid")


__all__ = [
    "FEEDBACK_REASON_CODES",
    "FeedbackAdapterKind",
    "FeedbackOutcome",
    "FeedbackSignalAggregate",
    "FeedbackSignalPolicy",
    "TrustedFeedbackEvent",
    "build_trusted_feedback_event",
]
