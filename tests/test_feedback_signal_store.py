"""隔离反馈事件 Store 的事务、幂等和重建契约。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from core.models.feedback_signal import (
    FeedbackAdapterKind,
    FeedbackOutcome,
    FeedbackSignalAggregate,
    build_trusted_feedback_event,
)
from core.storage.feedback_signal_store import FeedbackSignalStore


def _event(decision: str = "decision-1"):
    """构造匿名可信事件。"""

    return build_trusted_feedback_event(
        adapter_kind=FeedbackAdapterKind.RETRIEVAL_RESULT,
        decision_key=decision,
        variant_key="document_route",
        outcome=FeedbackOutcome.POSITIVE,
        scope_domain="scope-synthetic",
        persona_domain=None,
        observed_at=datetime(2026, 7, 21, 10, tzinfo=timezone.utc),
        window_seconds=3600,
    )


def test_store_deduplicates_and_persists_committed_events(tmp_path) -> None:
    """相同 dedupe key 重放只产生一条已提交事件。"""

    path = tmp_path / "feedback.db"
    store = FeedbackSignalStore(path)
    store.initialize()

    first = store.insert_events([_event()])
    replay = store.insert_events([_event()])
    summary = store.safe_summary()
    store.close()

    reopened = FeedbackSignalStore(path)
    reopened.initialize()
    try:
        assert first == {"accepted": 1, "duplicate_event": 0}
        assert replay == {"accepted": 0, "duplicate_event": 1}
        assert summary["event_count"] == 1
        assert len(reopened.list_events()) == 1
    finally:
        reopened.close()


def test_store_rolls_back_partial_batch_on_non_sql_error(tmp_path) -> None:
    """批次中途异常时事务必须回滚，不保留半批事件。"""

    store = FeedbackSignalStore(tmp_path / "rollback.db")
    store.initialize()

    with pytest.raises(AttributeError):
        store.insert_events([_event("decision-a"), object()])

    try:
        assert store.safe_summary()["event_count"] == 0
    finally:
        store.close()


def test_store_replaces_and_rebuilds_aggregates_without_event_loss(tmp_path) -> None:
    """删除 aggregate 后可从保留事件重建，事件计数保持不变。"""

    store = FeedbackSignalStore(tmp_path / "aggregate.db")
    store.initialize()
    store.insert_events([_event()])
    aggregate = FeedbackSignalAggregate(
        scope_domain="scope-synthetic",
        persona_domain=None,
        window_start=datetime(2026, 7, 21, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 21, 11, tzinfo=timezone.utc),
        accepted_count=1,
        independent_window_count=1,
        decayed_support=1.0,
        proposed_document_weight=0.7,
        proposed_graph_weight=0.3,
        delta_from_baseline=0.0,
        status="baseline_retained",
        policy_version=1,
    )
    store.replace_aggregates([aggregate])
    before = store.safe_summary()
    store.replace_aggregates([])
    after = store.safe_summary()

    try:
        assert before == {"event_count": 1, "aggregate_count": 1}
        assert after == {"event_count": 1, "aggregate_count": 0}
    finally:
        store.close()


def test_safe_summary_omits_event_and_domain_canaries(tmp_path) -> None:
    """Store 状态不得暴露 decision、domain 或 dedupe 原值。"""

    store = FeedbackSignalStore(tmp_path / "safe.db")
    store.initialize()
    event = build_trusted_feedback_event(
        adapter_kind=FeedbackAdapterKind.RETRIEVAL_RESULT,
        decision_key="DECISION-SECRET-CANARY",
        variant_key="document_route",
        outcome=FeedbackOutcome.POSITIVE,
        scope_domain="SCOPE-SECRET-CANARY",
        persona_domain="PERSONA-SECRET-CANARY",
        observed_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        window_seconds=3600,
    )
    store.insert_events([event])
    serialized = json.dumps(store.safe_summary())

    try:
        for canary in (
            "DECISION-SECRET-CANARY",
            "SCOPE-SECRET-CANARY",
            "PERSONA-SECRET-CANARY",
            event.dedupe_key,
        ):
            assert canary not in serialized
    finally:
        store.close()
