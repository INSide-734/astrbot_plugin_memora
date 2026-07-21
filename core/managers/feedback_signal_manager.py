"""反馈排序实验的可信适配、限流、衰减和聚合 Manager。"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from ..models.feedback_signal import (
    FeedbackAdapterKind,
    FeedbackSignalAggregate,
    FeedbackSignalPolicy,
    TrustedFeedbackEvent,
)
from ..storage.feedback_signal_store import FeedbackSignalStore


@dataclass(frozen=True, slots=True)
class FeedbackIngestResult:
    """单事件处理的安全结果，不返回事件 key 或原始 payload。"""

    accepted: bool
    reason_code: str


class FeedbackSignalManager:
    """只管理隔离 Store 中的反馈派生信号，不触碰 live engine。"""

    def __init__(
        self,
        store: FeedbackSignalStore,
        policy: FeedbackSignalPolicy | None = None,
    ) -> None:
        """保存显式隔离 Store 和不可变策略。"""

        self.store = store
        self.policy = policy or FeedbackSignalPolicy()
        self._registered_adapters: set[FeedbackAdapterKind] = set()

    def register_adapter(self, adapter_kind: FeedbackAdapterKind) -> None:
        """注册受控内部适配器；外部 payload 不能自行注册。"""

        if not isinstance(adapter_kind, FeedbackAdapterKind):
            raise ValueError("feedback_adapter_invalid")
        self._registered_adapters.add(adapter_kind)

    def ingest_event(
        self,
        event: TrustedFeedbackEvent,
        *,
        trusted_scope: str,
        trusted_persona: str | None,
        reference_time: datetime,
    ) -> FeedbackIngestResult:
        """验证来源、时间、作用域、去重和限流后事务写入事件。"""

        if event.adapter_kind not in self._registered_adapters:
            return FeedbackIngestResult(False, "untrusted_event_source")
        if event.scope_domain != trusted_scope or event.persona_domain != trusted_persona:
            return FeedbackIngestResult(False, "scope_mismatch")
        try:
            now = _utc(reference_time)
            observed = _utc(event.observed_at)
        except ValueError:
            return FeedbackIngestResult(False, "invalid_event_time")
        age = (now - observed).total_seconds()
        if age < -self.policy.max_future_skew_seconds:
            return FeedbackIngestResult(False, "invalid_event_time")
        if age > self.policy.max_event_age_seconds:
            return FeedbackIngestResult(False, "signal_expired")
        try:
            existing = self.store.list_events(
                scope_domain=event.scope_domain,
                persona_domain=event.persona_domain,
            )
            if any(item.dedupe_key == event.dedupe_key for item in existing):
                return FeedbackIngestResult(False, "duplicate_event")
            same_window = sum(item.window_key == event.window_key for item in existing)
            if same_window >= self.policy.max_events_per_window:
                return FeedbackIngestResult(False, "rate_limited")
            if len(existing) >= self.policy.max_events_per_domain:
                return FeedbackIngestResult(False, "rate_limited")
            if len(self.store.list_events()) >= self.policy.max_global_events:
                return FeedbackIngestResult(False, "rate_limited")
            counts = self.store.insert_events([event])
        except RuntimeError:
            return FeedbackIngestResult(False, "evaluation_prerequisite_unmet")
        if counts["duplicate_event"]:
            return FeedbackIngestResult(False, "duplicate_event")
        return FeedbackIngestResult(True, "accepted")

    def rebuild(self, *, reference_time: datetime) -> list[FeedbackSignalAggregate]:
        """从已提交事件按固定 reference time 完整重建聚合。"""

        now = _utc(reference_time)
        groups: dict[tuple[str, str | None, str], list[TrustedFeedbackEvent]] = defaultdict(list)
        events = self.store.list_events()
        for event in events:
            age = (now - _utc(event.observed_at)).total_seconds()
            if age < -self.policy.max_future_skew_seconds or age > self.policy.max_event_age_seconds:
                continue
            groups[(event.scope_domain, event.persona_domain, event.window_key)].append(event)
        domain_windows: dict[tuple[str, str | None], set[str]] = defaultdict(set)
        for scope, persona, window_key in groups:
            domain_windows[(scope, persona)].add(window_key)
        aggregates: list[FeedbackSignalAggregate] = []
        ordered_groups = sorted(
            groups.items(),
            key=lambda item: (item[0][0], item[0][1] or "", item[0][2]),
        )
        for (scope, persona, window_key), group in ordered_groups:
            support = _decayed_support(group, now, self.policy)
            independent = len(domain_windows[(scope, persona)])
            status = "candidate" if independent >= self.policy.min_independent_windows else "baseline_retained"
            delta = 0.0
            if status == "candidate":
                delta = _bounded_delta(support, self.policy.max_weight_delta)
            window_start = datetime.fromtimestamp(
                int(window_key) * self.policy.window_seconds,
                tz=timezone.utc,
            )
            window_end = window_start + timedelta(seconds=self.policy.window_seconds)
            aggregates.append(
                FeedbackSignalAggregate(
                    scope_domain=scope,
                    persona_domain=persona,
                    window_start=window_start,
                    window_end=window_end,
                    accepted_count=len(group),
                    independent_window_count=independent,
                    decayed_support=support,
                    proposed_document_weight=round(
                        self.policy.baseline_document_weight + delta,
                        6,
                    ),
                    proposed_graph_weight=round(
                        self.policy.baseline_graph_weight - delta,
                        6,
                    ),
                    delta_from_baseline=round(delta, 6),
                    status=status,
                    policy_version=self.policy.policy_version,
                )
            )
        self.store.replace_aggregates(aggregates)
        return aggregates

    def reset_and_rebuild(
        self,
        *,
        reference_time: datetime,
    ) -> list[FeedbackSignalAggregate]:
        """删除聚合后从保留事件重建，供崩溃恢复和幂等验证。"""

        self.store.clear_aggregates()
        return self.rebuild(reference_time=reference_time)

    def safe_summary(self) -> dict[str, int | float]:
        """返回不含 domain、decision、dedupe 或事件正文的聚合摘要。"""

        rows = self.store.list_aggregates(policy_version=self.policy.policy_version)
        return {
            "aggregate_count": len(rows),
            "candidate_count": sum(row["status"] == "candidate" for row in rows),
            "baseline_retained_count": sum(
                row["status"] == "baseline_retained" for row in rows
            ),
            "max_abs_delta": round(
                max((abs(float(row["delta_from_baseline"])) for row in rows), default=0.0),
                6,
            ),
        }


def _utc(value: datetime) -> datetime:
    """规范化带时区时间为 UTC。"""

    if value.tzinfo is None:
        raise ValueError("feedback_event_time_invalid")
    return value.astimezone(timezone.utc)


def _decayed_support(
    events: list[TrustedFeedbackEvent],
    reference_time: datetime,
    policy: FeedbackSignalPolicy,
) -> float:
    """按时间衰减计算有限支持度，事件顺序不影响结果。"""

    weighted = 0.0
    denominator = 0.0
    for event in events:
        age = max(0.0, (reference_time - _utc(event.observed_at)).total_seconds())
        decay = 0.5 ** (age / policy.half_life_seconds)
        weighted += event.outcome.reward * decay
        denominator += decay
    return max(0.0, min(1.0, weighted / denominator if denominator else 0.0))


def _bounded_delta(support: float, maximum: float) -> float:
    """把支持度映射为带绝对上限的文档路 delta。"""

    raw = (support - 0.5) * 0.2
    return max(-maximum, min(maximum, raw))


__all__ = ["FeedbackIngestResult", "FeedbackSignalManager"]
