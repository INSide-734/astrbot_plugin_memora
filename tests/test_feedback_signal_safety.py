"""反馈排序信号的重放、persona 隔离和长期投毒安全门。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.managers.feedback_signal_manager import FeedbackSignalManager
from core.models.feedback_signal import (
    FeedbackAdapterKind,
    FeedbackOutcome,
    FeedbackSignalPolicy,
    build_trusted_feedback_event,
)
from core.storage.feedback_signal_store import FeedbackSignalStore

REFERENCE_TIME = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def _event(
    decision: str,
    observed_at: datetime,
    *,
    outcome: FeedbackOutcome,
    persona: str | None,
):
    """构造匿名内部反馈事件。"""

    return build_trusted_feedback_event(
        adapter_kind=FeedbackAdapterKind.RETRIEVAL_RESULT,
        decision_key=decision,
        variant_key="document_route",
        outcome=outcome,
        scope_domain="scope-synthetic",
        persona_domain=persona,
        observed_at=observed_at,
        window_seconds=3600,
    )


def _manager(tmp_path, policy: FeedbackSignalPolicy):
    """构造隔离 Store 和已注册 Manager。"""

    store = FeedbackSignalStore(tmp_path / "feedback-safety.db")
    store.initialize()
    manager = FeedbackSignalManager(store, policy)
    manager.register_adapter(FeedbackAdapterKind.RETRIEVAL_RESULT)
    return store, manager


def test_opposite_outcome_cannot_bypass_decision_dedupe(tmp_path) -> None:
    """同一 decision/window 的相反 outcome 必须命中相同 dedupe key。"""

    store, manager = _manager(tmp_path, FeedbackSignalPolicy())
    positive = _event(
        "decision-synthetic",
        REFERENCE_TIME - timedelta(minutes=5),
        outcome=FeedbackOutcome.POSITIVE,
        persona=None,
    )
    negative = _event(
        "decision-synthetic",
        REFERENCE_TIME - timedelta(minutes=5),
        outcome=FeedbackOutcome.NEGATIVE,
        persona=None,
    )

    first = manager.ingest_event(
        positive,
        trusted_scope="scope-synthetic",
        trusted_persona=None,
        reference_time=REFERENCE_TIME,
    )
    replay = manager.ingest_event(
        negative,
        trusted_scope="scope-synthetic",
        trusted_persona=None,
        reference_time=REFERENCE_TIME,
    )

    try:
        assert positive.dedupe_key == negative.dedupe_key
        assert first.reason_code == "accepted"
        assert replay.reason_code == "duplicate_event"
        assert store.safe_summary()["event_count"] == 1
    finally:
        store.close()


def test_none_persona_is_exact_domain_not_wildcard(tmp_path) -> None:
    """匿名 persona 域不能读取或限流具名 persona 的事件。"""

    policy = FeedbackSignalPolicy(max_events_per_domain=1)
    store, manager = _manager(tmp_path, policy)
    named = _event(
        "named",
        REFERENCE_TIME - timedelta(minutes=5),
        outcome=FeedbackOutcome.POSITIVE,
        persona="persona-synthetic",
    )
    anonymous = _event(
        "anonymous",
        REFERENCE_TIME - timedelta(minutes=4),
        outcome=FeedbackOutcome.POSITIVE,
        persona=None,
    )

    first = manager.ingest_event(
        named,
        trusted_scope="scope-synthetic",
        trusted_persona="persona-synthetic",
        reference_time=REFERENCE_TIME,
    )
    second = manager.ingest_event(
        anonymous,
        trusted_scope="scope-synthetic",
        trusted_persona=None,
        reference_time=REFERENCE_TIME,
    )
    aggregates = manager.rebuild(reference_time=REFERENCE_TIME)

    try:
        assert first.accepted is True
        assert second.accepted is True
        assert (
            len(store.list_events(scope_domain="scope-synthetic", persona_domain=None))
            == 1
        )
        assert (
            len(
                store.list_events(
                    scope_domain="scope-synthetic",
                    persona_domain="persona-synthetic",
                )
            )
            == 1
        )
        assert len(aggregates) == 2
    finally:
        store.close()


def test_many_independent_windows_still_obey_maximum_delta(tmp_path) -> None:
    """长期连续正反馈也不能突破固定权重 delta。"""

    policy = FeedbackSignalPolicy(
        min_independent_windows=2,
        max_weight_delta=0.02,
        max_event_age_seconds=30 * 86400,
    )
    store, manager = _manager(tmp_path, policy)
    for hour in range(10):
        event = _event(
            f"decision-{hour}",
            REFERENCE_TIME - timedelta(hours=hour),
            outcome=FeedbackOutcome.POSITIVE,
            persona=None,
        )
        manager.ingest_event(
            event,
            trusted_scope="scope-synthetic",
            trusted_persona=None,
            reference_time=REFERENCE_TIME,
        )
    aggregates = manager.rebuild(reference_time=REFERENCE_TIME)

    try:
        assert aggregates
        assert all(abs(item.delta_from_baseline) <= 0.02 for item in aggregates)
        assert manager.safe_summary()["max_abs_delta"] <= 0.02
    finally:
        store.close()
