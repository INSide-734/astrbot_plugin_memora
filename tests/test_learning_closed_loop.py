"""LEARN-01~04 闭环：统一反馈事件、影子候选、CAS 发布与 MAB 删除。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.managers.auto_learning import AutoLearningManager
from core.managers.feedback_signal_manager import FeedbackSignalManager
from core.models.feedback_signal import (
    FeedbackAdapterKind,
    FeedbackOutcome,
    FeedbackSignalPolicy,
    build_trusted_feedback_event,
)
from core.storage.feedback_signal_store import FeedbackSignalStore

UTC = timezone.utc


async def _make_feedback_manager(
    tmp_path: Path,
    *,
    policy: FeedbackSignalPolicy | None = None,
) -> FeedbackSignalManager:
    """构造隔离反馈 Store 与已注册 REVIEW_DECISION 适配器的 Manager。"""

    store = FeedbackSignalStore(str(tmp_path / "feedback.db"))
    store.initialize()
    manager = FeedbackSignalManager(store, policy=policy)
    manager.register_adapter(FeedbackAdapterKind.REVIEW_DECISION)
    return manager


def _seed_event(
    manager: FeedbackSignalManager,
    *,
    decision_key: str,
    scope: str,
    reference_time: datetime,
    persona: str | None = None,
    outcome: FeedbackOutcome = FeedbackOutcome.NEGATIVE,
) -> object:
    """直接写入一条已注册适配器的可信事件。"""

    event = build_trusted_feedback_event(
        adapter_kind=FeedbackAdapterKind.REVIEW_DECISION,
        decision_key=decision_key,
        variant_key="doc_route",
        outcome=outcome,
        scope_domain=scope,
        persona_domain=persona,
        observed_at=reference_time,
        window_seconds=manager.policy.window_seconds,
    )
    return manager.ingest_event(
        event,
        trusted_scope=scope,
        trusted_persona=persona,
        reference_time=reference_time,
    )


@pytest.mark.asyncio
async def test_no_feedback_produces_no_candidates(tmp_path: Path) -> None:
    """无反馈事件时不产生参数候选，也不触碰生产配置。"""

    feedback = await _make_feedback_manager(tmp_path)
    manager = AutoLearningManager(feedback, data_dir=str(tmp_path), enabled=True)
    writer = AsyncMock()

    candidates = await manager.rebuild_candidates(reference_time=datetime.now(UTC))

    assert candidates == []
    assert manager.get_candidates() == []
    writer.assert_not_called()


@pytest.mark.asyncio
async def test_low_sample_reports_insufficient_evidence(tmp_path: Path) -> None:
    """独立窗口不足时候选被拒绝并报告 insufficient_evidence。"""

    feedback = await _make_feedback_manager(tmp_path)
    manager = AutoLearningManager(feedback, data_dir=str(tmp_path), enabled=True)
    now = datetime.now(UTC)
    _seed_event(
        feedback, decision_key="forget:7", scope="private:u", reference_time=now
    )

    candidates = await manager.rebuild_candidates(reference_time=now)

    assert len(candidates) == 1
    assert candidates[0]["status"] == "rejected"
    assert candidates[0]["reason_code"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_duplicate_event_deduped(tmp_path: Path) -> None:
    """同一窗口同一决策只接受一次，重复事件被去重。"""

    feedback = await _make_feedback_manager(tmp_path)
    now = datetime.now(UTC)

    first = _seed_event(
        feedback, decision_key="forget:7", scope="private:u", reference_time=now
    )
    second = _seed_event(
        feedback, decision_key="forget:7", scope="private:u", reference_time=now
    )

    assert first.reason_code == "accepted"
    assert second.reason_code == "duplicate_event"


@pytest.mark.asyncio
async def test_scope_mismatch_not_attributed(tmp_path: Path) -> None:
    """事件作用域与可信作用域不一致时拒绝归因。"""

    feedback = await _make_feedback_manager(tmp_path)
    event = build_trusted_feedback_event(
        adapter_kind=FeedbackAdapterKind.REVIEW_DECISION,
        decision_key="forget:7",
        variant_key="doc_route",
        outcome=FeedbackOutcome.NEGATIVE,
        scope_domain="private:u",
        persona_domain=None,
        observed_at=datetime.now(UTC),
        window_seconds=feedback.policy.window_seconds,
    )

    result = feedback.ingest_event(
        event,
        trusted_scope="private:other",
        trusted_persona=None,
        reference_time=datetime.now(UTC),
    )

    assert result.reason_code == "scope_mismatch"


@pytest.mark.asyncio
async def test_publish_failure_keeps_production_revision(tmp_path: Path) -> None:
    """候选发布失败时生产配置保持原 revision，且保留回滚快照。"""

    feedback = await _make_feedback_manager(tmp_path)
    manager = AutoLearningManager(
        feedback,
        data_dir=str(tmp_path),
        enabled=True,
        min_independent_windows=1,
        min_samples=1,
    )
    now = datetime.now(UTC)
    for offset in range(2):
        _seed_event(
            feedback,
            decision_key=f"forget:{offset}",
            scope="private:u",
            reference_time=now - timedelta(hours=offset),
        )
    candidates = await manager.rebuild_candidates(reference_time=now)
    ready = next(c for c in candidates if c["status"] == "ready_for_review")
    writer = AsyncMock(return_value=False)
    writer._last_write_reason_code = "source_revision_mismatch"

    result = await manager.publish_candidate(
        ready["candidate_key"],
        config_writer=writer,
        expected_revision="cfg-1",
        current_values={
            "document_route_weight": 0.61,
            "graph_route_weight": 0.39,
        },
    )

    assert result["published"] is False
    assert result["reason_code"] == "source_revision_mismatch"
    assert manager.last_published_snapshot(ready["candidate_key"]) is None


@pytest.mark.asyncio
async def test_rollback_restores_last_stable_baseline(tmp_path: Path) -> None:
    """成功发布后回滚必须写回发布前的稳定权重，而不是重复新权重。"""

    feedback = await _make_feedback_manager(tmp_path)
    manager = AutoLearningManager(
        feedback,
        data_dir=str(tmp_path),
        enabled=True,
        min_independent_windows=1,
        min_samples=1,
    )
    now = datetime.now(UTC)
    for offset in range(2):
        _seed_event(
            feedback,
            decision_key=f"rollback:{offset}",
            scope="private:u",
            reference_time=now - timedelta(hours=offset),
        )
    ready = next(
        candidate
        for candidate in await manager.rebuild_candidates(reference_time=now)
        if candidate["status"] == "ready_for_review"
    )
    calls: list[tuple[dict, str]] = []

    async def writer(changes: dict, *, expected_revision: str) -> bool:
        calls.append((changes, expected_revision))
        return True

    published = await manager.publish_candidate(
        ready["candidate_key"],
        config_writer=writer,
        expected_revision="cfg-1",
        current_values={
            "document_route_weight": 0.61,
            "graph_route_weight": 0.39,
        },
    )
    restored = await manager.rollback_last_publish(
        ready["candidate_key"],
        config_writer=writer,
        expected_revision="cfg-2",
    )

    assert published["published"] is True
    assert restored == {"restored": True, "reason_code": "restored"}
    assert calls[1] == (
        {
            "graph_memory.document_route_weight": 0.61,
            "graph_memory.graph_route_weight": 0.39,
        },
        "cfg-2",
    )


@pytest.mark.asyncio
async def test_persona_candidates_do_not_overwrite_same_scope(tmp_path: Path) -> None:
    """同一作用域的不同 persona 必须各自保留 shadow 候选。"""

    feedback = await _make_feedback_manager(tmp_path)
    manager = AutoLearningManager(
        feedback,
        data_dir=str(tmp_path),
        enabled=True,
        min_independent_windows=1,
        min_samples=1,
    )
    now = datetime.now(UTC)
    _seed_event(
        feedback,
        decision_key="persona-a",
        scope="shared-scope",
        persona="persona-a",
        reference_time=now,
    )
    _seed_event(
        feedback,
        decision_key="persona-b",
        scope="shared-scope",
        persona="persona-b",
        reference_time=now,
    )

    candidates = await manager.rebuild_candidates(reference_time=now)

    assert len(candidates) == 2
    assert {item["persona_domain"] for item in candidates} == {"persona-a", "persona-b"}
    assert len({item["candidate_key"] for item in candidates}) == 2


@pytest.mark.asyncio
async def test_explicit_correction_pseudonymizes_identifiers(tmp_path: Path) -> None:
    """显式反馈存储不得出现 canonical、复核或原始会话标识。"""

    from core.managers.feedback_signal_manager import record_explicit_correction

    feedback = await _make_feedback_manager(tmp_path)
    reference_time = datetime.now(UTC)
    result = record_explicit_correction(
        feedback,
        decision_key="canonical-memory-id-CANARY",
        scope_domain="onebot:group-CANARY",
        persona_domain="persona-CANARY",
        reference_time=reference_time,
    )

    assert result.accepted is True
    event = feedback.store.list_events()[0]
    serialized = json.dumps(
        event.__dict__ if hasattr(event, "__dict__") else repr(event)
    )
    assert "CANARY" not in serialized
    assert event.decision_key.startswith("decision:")
    assert event.scope_domain.startswith("scope:")
    assert event.persona_domain and event.persona_domain.startswith("persona:")


@pytest.mark.asyncio
async def test_explicit_feedback_can_be_revoked_and_rebuilt(tmp_path: Path) -> None:
    """可信入口必须能按同一匿名决策撤销反馈并重建派生聚合。"""

    from core.managers.feedback_signal_manager import (
        record_explicit_correction,
        revoke_explicit_correction,
    )

    feedback = await _make_feedback_manager(tmp_path)
    reference_time = datetime.now(UTC)
    recorded = record_explicit_correction(
        feedback,
        decision_key="review-id-CANARY",
        scope_domain="review-scope-CANARY",
        reference_time=reference_time,
    )
    assert recorded.accepted is True
    assert feedback.rebuild(reference_time=reference_time)

    revoked = revoke_explicit_correction(
        feedback,
        decision_key="review-id-CANARY",
        scope_domain="review-scope-CANARY",
        reference_time=reference_time,
    )

    assert revoked.revoked is True
    assert revoked.reason_code == "revoked"
    assert feedback.store.list_events() == []
    assert feedback.safe_summary()["aggregate_count"] == 0


@pytest.mark.asyncio
async def test_expired_feedback_is_pruned_before_global_rate_limit(
    tmp_path: Path,
) -> None:
    """过期事件必须释放全局容量，避免反馈管线永久进入限流状态。"""

    from core.managers.feedback_signal_manager import record_explicit_correction

    policy = FeedbackSignalPolicy(
        max_event_age_seconds=60,
        max_global_events=1,
    )
    feedback = await _make_feedback_manager(tmp_path, policy=policy)
    reference_time = datetime.now(UTC)
    expired_time = reference_time - timedelta(seconds=61)
    first = record_explicit_correction(
        feedback,
        decision_key="expired-decision-CANARY",
        scope_domain="scope-CANARY",
        reference_time=expired_time,
    )
    second = record_explicit_correction(
        feedback,
        decision_key="current-decision-CANARY",
        scope_domain="scope-CANARY",
        reference_time=reference_time,
    )

    assert first.accepted is True
    assert second.accepted is True
    events = feedback.store.list_events()
    assert len(events) == 1
    assert "CANARY" not in repr(events[0])


@pytest.mark.asyncio
async def test_state_file_values_are_constrained_before_summary(tmp_path: Path) -> None:
    """污染的 shadow 状态不能把任意 reason 或嵌套值带入 API 摘要。"""

    feedback = await _make_feedback_manager(tmp_path)
    manager = AutoLearningManager(feedback, data_dir=str(tmp_path), enabled=True)
    state = {
        "candidates": {
            "candidate-canary": {
                "scope_domain": "scope-canary",
                "persona_domain": None,
                "proposed_document_weight": {"leak": "BODY-CANARY"},
                "proposed_graph_weight": 0.3,
                "delta_from_baseline": 0.0,
                "accepted_count": True,
                "independent_window_count": 2,
                "decayed_support": 0.5,
                "status": "ready_for_review",
                "reason_code": {"leak": "BODY-CANARY"},
                "policy_version": 1,
            }
        },
        "published": {},
    }
    (tmp_path / "auto_learning.json").write_text(json.dumps(state), encoding="utf-8")

    await manager.load_state()
    summary = manager.safe_summary()

    assert "BODY-CANARY" not in json.dumps(summary, ensure_ascii=False)
    assert summary["reasons"] == ["invalid_state"]
    assert "BODY-CANARY" not in json.dumps(manager.get_candidates(), ensure_ascii=False)


def test_auto_learning_no_longer_exposes_online_feedback_collector() -> None:
    """旧 FeedbackCollector/ParamOptimizer 在线更新入口必须移除。"""

    from core.managers import auto_learning

    assert not hasattr(auto_learning, "FeedbackCollector")
    assert not hasattr(auto_learning, "ParamOptimizer")


def test_learning_summary_exposes_candidates_without_mutation() -> None:
    """指标摘要应暴露候选与反馈摘要，且不触发任何写入。"""

    from core.api.metrics_api import MetricsApiMixin

    auto = MagicMock()
    auto.safe_summary.return_value = {
        "available": True,
        "candidate_count": 1,
        "ready_count": 1,
        "rejected_count": 0,
        "published_count": 0,
        "reasons": ["candidate"],
    }
    feedback = MagicMock()
    feedback.safe_summary.return_value = {"aggregate_count": 1}
    initializer = SimpleNamespace(
        memory_engine=SimpleNamespace(
            auto_learning=auto,
            feedback_signal_manager=feedback,
        )
    )
    api = SimpleNamespace(plugin=SimpleNamespace(initializer=initializer))

    summary = MetricsApiMixin._build_learning_summary(api)

    assert summary["available"] is True
    assert summary["ready_count"] == 1
    assert summary["feedback"] == {"aggregate_count": 1}
    feedback.safe_summary.assert_called_once()


def test_learning_summary_isolates_feedback_store_failure() -> None:
    """反馈隔离 Store 读取失败时仍应返回安全的学习摘要。"""

    from core.api.metrics_api import MetricsApiMixin

    auto = MagicMock()
    auto.safe_summary.return_value = {
        "available": True,
        "candidate_count": 0,
        "ready_count": 0,
        "rejected_count": 0,
        "published_count": 0,
        "reasons": [],
    }
    feedback = MagicMock()
    feedback.safe_summary.side_effect = RuntimeError("隔离 Store 不可用")
    api = SimpleNamespace(
        plugin=SimpleNamespace(
            initializer=SimpleNamespace(
                memory_engine=SimpleNamespace(
                    auto_learning=auto,
                    feedback_signal_manager=feedback,
                )
            )
        )
    )

    summary = MetricsApiMixin._build_learning_summary(api)

    assert summary["available"] is True
    assert summary["feedback"] == {}


def test_review_feedback_records_trusted_event() -> None:
    """管理员复核动作应写入统一反馈管线，正负结果映射正确。"""

    from core.api.review_api import ReviewApiMixin

    manager = MagicMock()
    manager.policy = FeedbackSignalPolicy()
    engine = SimpleNamespace(feedback_signal_manager=manager)

    ReviewApiMixin._record_review_feedback(None, engine, "review-1", "approve")
    ReviewApiMixin._record_review_feedback(None, engine, "review-2", "reject")

    assert manager.ingest_event.call_count == 2
    approve_event = manager.ingest_event.call_args_list[0].args[0]
    reject_event = manager.ingest_event.call_args_list[1].args[0]
    assert approve_event.outcome is FeedbackOutcome.POSITIVE
    assert reject_event.outcome is FeedbackOutcome.NEGATIVE
    assert "review-1" not in repr(approve_event)
    assert "review-2" not in repr(reject_event)


@pytest.mark.asyncio
async def test_forget_feedback_records_trusted_event() -> None:
    """显式忘记命令应写入可信负向反馈；无管理器时安全跳过。"""

    from core.commands.query_commands import QueryCommandMixin

    manager = MagicMock()
    manager.policy = FeedbackSignalPolicy()
    engine = SimpleNamespace(feedback_signal_manager=manager)
    handler = SimpleNamespace(memory_engine=engine)
    event = SimpleNamespace(unified_msg_origin="private:u")

    await QueryCommandMixin._record_forget_correction(handler, 7, event)
    manager.ingest_event.assert_called_once()
    event_recorded = manager.ingest_event.call_args.args[0]
    assert event_recorded.outcome is FeedbackOutcome.NEGATIVE
    assert event_recorded.scope_domain.startswith("scope:")
    assert "private:u" not in repr(event_recorded)

    empty_handler = SimpleNamespace(
        memory_engine=SimpleNamespace(feedback_signal_manager=None)
    )
    await QueryCommandMixin._record_forget_correction(empty_handler, 7, event)
