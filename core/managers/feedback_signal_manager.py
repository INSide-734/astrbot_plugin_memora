"""反馈排序实验的可信适配、限流、衰减和聚合 Manager。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..features.learning.domain.models import (
    FeedbackAdapterKind,
    FeedbackOutcome,
    FeedbackSignalAggregate,
    FeedbackSignalPolicy,
    TrustedFeedbackEvent,
    build_trusted_feedback_event,
)
from ..storage.feedback_signal_store import FeedbackSignalStore


@dataclass(frozen=True, slots=True)
class FeedbackIngestResult:
    """单事件处理的安全结果，不返回事件 key 或原始 payload。"""

    accepted: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class FeedbackRevokeResult:
    """反馈撤销的安全结果，不返回决策键或作用域。"""

    revoked: bool
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

    def close(self) -> None:
        """关闭隔离反馈 Store；重复关闭保持安全。"""

        self.store.close()

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
        if (
            event.scope_domain != trusted_scope
            or event.persona_domain != trusted_persona
        ):
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
            retention_cutoff = now - timedelta(
                seconds=self.policy.max_event_age_seconds
            )
            if self.store.delete_events_before(retention_cutoff):
                self.store.clear_aggregates()
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

    def revoke_event(
        self,
        *,
        adapter_kind: FeedbackAdapterKind,
        decision_key: str,
        variant_key: str,
        scope_domain: str,
        persona_domain: str | None,
        trusted_scope: str,
        trusted_persona: str | None,
        reference_time: datetime,
    ) -> FeedbackRevokeResult:
        """验证来源和归因后撤销指定匿名决策，并同步重建聚合。"""

        if adapter_kind not in self._registered_adapters:
            return FeedbackRevokeResult(False, "untrusted_event_source")
        if scope_domain != trusted_scope or persona_domain != trusted_persona:
            return FeedbackRevokeResult(False, "scope_mismatch")
        try:
            now = _utc(reference_time)
            retention_cutoff = now - timedelta(
                seconds=self.policy.max_event_age_seconds
            )
            deleted = self.store.revoke_and_replace_aggregates(
                adapter_kind=adapter_kind,
                decision_key=decision_key,
                variant_key=variant_key,
                scope_domain=scope_domain,
                persona_domain=persona_domain,
                retention_cutoff=retention_cutoff,
                aggregate_builder=lambda events: self._build_aggregates(events, now),
            )
            if not deleted:
                return FeedbackRevokeResult(False, "feedback_not_found")
        except (RuntimeError, ValueError):
            return FeedbackRevokeResult(False, "evaluation_prerequisite_unmet")
        return FeedbackRevokeResult(True, "revoked")

    def rebuild(self, *, reference_time: datetime) -> list[FeedbackSignalAggregate]:
        """从已提交事件按固定 reference time 完整重建聚合。"""

        now = _utc(reference_time)
        retention_cutoff = now - timedelta(seconds=self.policy.max_event_age_seconds)
        self.store.delete_events_before(retention_cutoff)
        aggregates = self._build_aggregates(self.store.list_events(), now)
        self.store.replace_aggregates(aggregates)
        return aggregates

    def _build_aggregates(
        self,
        events: list[TrustedFeedbackEvent],
        reference_time: datetime,
    ) -> list[FeedbackSignalAggregate]:
        """从事务提供的事件快照纯计算 aggregate，不执行 Store I/O。"""

        now = _utc(reference_time)
        groups: dict[tuple[str, str | None, str], list[TrustedFeedbackEvent]] = (
            defaultdict(list)
        )
        for event in events:
            age = (now - _utc(event.observed_at)).total_seconds()
            if (
                age < -self.policy.max_future_skew_seconds
                or age > self.policy.max_event_age_seconds
            ):
                continue
            groups[(event.scope_domain, event.persona_domain, event.window_key)].append(
                event
            )
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
            status = (
                "candidate"
                if independent >= self.policy.min_independent_windows
                else "baseline_retained"
            )
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
                max(
                    (abs(float(row["delta_from_baseline"])) for row in rows),
                    default=0.0,
                ),
                6,
            ),
        }


def record_explicit_correction(
    manager: FeedbackSignalManager,
    *,
    decision_key: str,
    scope_domain: str,
    persona_domain: str | None = None,
    outcome: FeedbackOutcome = FeedbackOutcome.NEGATIVE,
    reference_time: datetime | None = None,
) -> FeedbackIngestResult:
    """由受控生产入口记录一条显式纠正反馈（如忘记/复核拒绝）。"""

    observed_at = reference_time or datetime.now(timezone.utc)
    opaque_decision = manager.store.opaque_token("decision", decision_key)
    opaque_scope = manager.store.opaque_token("scope", scope_domain)
    opaque_persona = (
        manager.store.opaque_token("persona", persona_domain)
        if persona_domain is not None
        else None
    )
    event = build_trusted_feedback_event(
        adapter_kind=FeedbackAdapterKind.REVIEW_DECISION,
        decision_key=opaque_decision,
        variant_key="doc_route",
        outcome=outcome,
        scope_domain=opaque_scope,
        persona_domain=opaque_persona,
        observed_at=observed_at,
        window_seconds=manager.policy.window_seconds,
    )
    return manager.ingest_event(
        event,
        trusted_scope=opaque_scope,
        trusted_persona=opaque_persona,
        reference_time=observed_at,
    )


def revoke_explicit_correction(
    manager: FeedbackSignalManager,
    *,
    decision_key: str,
    scope_domain: str,
    persona_domain: str | None = None,
    reference_time: datetime | None = None,
) -> FeedbackRevokeResult:
    """按与写入相同的匿名键撤销显式纠正，并重建当前聚合。"""

    observed_at = reference_time or datetime.now(timezone.utc)
    opaque_decision = manager.store.opaque_token("decision", decision_key)
    opaque_scope = manager.store.opaque_token("scope", scope_domain)
    opaque_persona = (
        manager.store.opaque_token("persona", persona_domain)
        if persona_domain is not None
        else None
    )
    return manager.revoke_event(
        adapter_kind=FeedbackAdapterKind.REVIEW_DECISION,
        decision_key=opaque_decision,
        variant_key="doc_route",
        scope_domain=opaque_scope,
        persona_domain=opaque_persona,
        trusted_scope=opaque_scope,
        trusted_persona=opaque_persona,
        reference_time=observed_at,
    )


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


__all__ = [
    "FeedbackIngestResult",
    "FeedbackRevokeResult",
    "FeedbackSignalManager",
    "record_explicit_correction",
    "revoke_explicit_correction",
]
