"""反馈排序 Manager 的来源、限流、衰减和聚合契约。"""

from __future__ import annotations

import json
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
    outcome: FeedbackOutcome = FeedbackOutcome.POSITIVE,
    scope: str = "scope-synthetic",
    persona: str | None = None,
):
    """构造匿名可信反馈事件。"""

    return build_trusted_feedback_event(
        adapter_kind=FeedbackAdapterKind.RETRIEVAL_RESULT,
        decision_key=decision,
        variant_key="document_route",
        outcome=outcome,
        scope_domain=scope,
        persona_domain=persona,
        observed_at=observed_at,
        window_seconds=3600,
    )


def _manager(tmp_path, **policy_overrides):
    """构造已初始化隔离 Store 和 Manager。"""

    store = FeedbackSignalStore(
        tmp_path / f"feedback-{len(list(tmp_path.iterdir()))}.db"
    )
    store.initialize()
    manager = FeedbackSignalManager(store, FeedbackSignalPolicy(**policy_overrides))
    manager.register_adapter(FeedbackAdapterKind.RETRIEVAL_RESULT)
    return store, manager


def test_manager_rejects_untrusted_scope_time_duplicate_and_rate_limit(
    tmp_path,
) -> None:
    """来源、作用域、时间、去重和窗口限流必须使用稳定 reason。"""

    store, manager = _manager(tmp_path, max_events_per_window=1)
    event = _event("decision-a", REFERENCE_TIME - timedelta(minutes=5))

    accepted = manager.ingest_event(
        event,
        trusted_scope="scope-synthetic",
        trusted_persona=None,
        reference_time=REFERENCE_TIME,
    )
    duplicate = manager.ingest_event(
        event,
        trusted_scope="scope-synthetic",
        trusted_persona=None,
        reference_time=REFERENCE_TIME,
    )
    cross_scope = manager.ingest_event(
        _event("decision-b", REFERENCE_TIME - timedelta(minutes=4)),
        trusted_scope="scope-other",
        trusted_persona=None,
        reference_time=REFERENCE_TIME,
    )
    future = manager.ingest_event(
        _event("decision-future", REFERENCE_TIME + timedelta(minutes=5)),
        trusted_scope="scope-synthetic",
        trusted_persona=None,
        reference_time=REFERENCE_TIME,
    )
    naive_reference = manager.ingest_event(
        _event("decision-naive", REFERENCE_TIME - timedelta(hours=2)),
        trusted_scope="scope-synthetic",
        trusted_persona=None,
        reference_time=datetime(2026, 7, 21, 12),
    )

    try:
        assert accepted.reason_code == "accepted"
        assert duplicate.reason_code == "duplicate_event"
        assert cross_scope.reason_code == "scope_mismatch"
        assert future.reason_code == "invalid_event_time"
        assert naive_reference.reason_code == "invalid_event_time"
    finally:
        store.close()


def test_manager_requires_registered_adapter(tmp_path) -> None:
    """未注册 adapter 不能写入隔离事件 Store。"""

    store = FeedbackSignalStore(tmp_path / "untrusted.db")
    store.initialize()
    manager = FeedbackSignalManager(store)
    result = manager.ingest_event(
        _event("decision", REFERENCE_TIME),
        trusted_scope="scope-synthetic",
        trusted_persona=None,
        reference_time=REFERENCE_TIME,
    )

    try:
        assert result.reason_code == "untrusted_event_source"
        assert store.safe_summary()["event_count"] == 0
    finally:
        store.close()


def test_rebuild_is_order_independent_bounded_and_domain_isolated(tmp_path) -> None:
    """事件顺序不影响聚合，scope/persona 不共享信号且 delta 有上限。"""

    policy = {
        "min_independent_windows": 2,
        "max_weight_delta": 0.05,
    }
    events = [
        _event("a", REFERENCE_TIME - timedelta(minutes=10)),
        _event("b", REFERENCE_TIME - timedelta(hours=1, minutes=10)),
        _event(
            "c",
            REFERENCE_TIME - timedelta(minutes=8),
            outcome=FeedbackOutcome.NEGATIVE,
            scope="scope-other",
        ),
    ]
    snapshots = []
    for index, ordered in enumerate((events, list(reversed(events)))):
        store = FeedbackSignalStore(tmp_path / f"order-{index}.db")
        store.initialize()
        manager = FeedbackSignalManager(store, FeedbackSignalPolicy(**policy))
        manager.register_adapter(FeedbackAdapterKind.RETRIEVAL_RESULT)
        for event in ordered:
            manager.ingest_event(
                event,
                trusted_scope=event.scope_domain,
                trusted_persona=event.persona_domain,
                reference_time=REFERENCE_TIME,
            )
        aggregates = manager.rebuild(reference_time=REFERENCE_TIME)
        snapshots.append(
            [
                (
                    item.scope_domain,
                    item.status,
                    item.proposed_document_weight,
                    item.proposed_graph_weight,
                    item.delta_from_baseline,
                )
                for item in aggregates
            ]
        )
        assert manager.safe_summary()["max_abs_delta"] <= 0.05
        store.close()

    assert snapshots[0] == snapshots[1]
    scope_candidates = [item for item in snapshots[0] if item[0] == "scope-synthetic"]
    other_candidates = [item for item in snapshots[0] if item[0] == "scope-other"]
    assert all(item[1] == "candidate" for item in scope_candidates)
    assert all(item[1] == "baseline_retained" for item in other_candidates)


def test_reset_rebuild_is_stable_and_summary_omits_canaries(tmp_path) -> None:
    """删除聚合后重建结果一致，安全摘要不包含 domain 或 decision。"""

    store, manager = _manager(tmp_path, min_independent_windows=1)
    event = _event(
        "DECISION-SECRET-CANARY",
        REFERENCE_TIME - timedelta(minutes=5),
        scope="SCOPE-SECRET-CANARY",
        persona="PERSONA-SECRET-CANARY",
    )
    manager.ingest_event(
        event,
        trusted_scope=event.scope_domain,
        trusted_persona=event.persona_domain,
        reference_time=REFERENCE_TIME,
    )
    first = manager.rebuild(reference_time=REFERENCE_TIME)
    second = manager.reset_and_rebuild(reference_time=REFERENCE_TIME)
    serialized = json.dumps(manager.safe_summary())

    try:
        assert first == second
        for canary in (
            "DECISION-SECRET-CANARY",
            "SCOPE-SECRET-CANARY",
            "PERSONA-SECRET-CANARY",
            event.dedupe_key,
        ):
            assert canary not in serialized
    finally:
        store.close()
