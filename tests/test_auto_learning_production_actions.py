"""自主学习生产候选归并与动作状态机契约。"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from core.features.learning.application.auto_learning import (
    AutoLearningManager,
    AutoLearningStatePersistenceError,
)
from core.features.learning.domain.auto_learning_actions import (
    CandidateBinding,
    aggregation_revision_for,
    reduce_global_candidate,
    stable_revision,
    weight_snapshot_hash,
)
from core.features.learning.domain.feedback_learning_evidence import (
    LatencyEvidence,
    QualityMetricEvidence,
    build_learning_evidence,
)
from core.features.learning.domain.feedback_learning_evidence_contract import (
    REQUIRED_EVIDENCE_REGRESSION_CHECKS,
)
from core.features.learning.infrastructure.learning_config_adapter import (
    LearningConfigApplyResult,
    LearningConfigSnapshot,
)
from core.models.feedback_signal import FeedbackSignalAggregate

UTC = timezone.utc


def _aggregate(
    *,
    scope: str,
    persona: str | None,
    window: int,
    accepted: int,
    support: float,
    delta: float,
) -> FeedbackSignalAggregate:
    """构造一个可发布评估使用的窗口级可信聚合。"""

    start = datetime(2026, 8, 1, tzinfo=UTC) + timedelta(hours=window)
    return FeedbackSignalAggregate(
        scope_domain=scope,
        persona_domain=persona,
        window_start=start,
        window_end=start + timedelta(hours=1),
        accepted_count=accepted,
        independent_window_count=2,
        decayed_support=support,
        proposed_document_weight=round(0.7 + delta, 6),
        proposed_graph_weight=round(0.3 - delta, 6),
        delta_from_baseline=delta,
        status="candidate",
        policy_version=3,
    )


def _binding(*, passed: bool = True) -> CandidateBinding:
    """构造与当前配置及不可变评测产物绑定的测试证据。"""

    return CandidateBinding(
        source_config_revision="b" * 64,
        evidence_revision="evidence-revision-23",
        quality_gate_version="quality-gate-v2",
        evidence_passed=passed,
    )


def _artifact_for(
    aggregates: list[FeedbackSignalAggregate],
    binding: CandidateBinding,
):
    """按当前聚合与配置 revision 构造可复核的通过 artifact。"""

    return build_learning_evidence(
        aggregation_revision=aggregation_revision_for(aggregates),
        source_config_revision=binding.source_config_revision,
        quality_gate_version=binding.quality_gate_version,
        dataset_hash="dataset-hash",
        replay_window_hash="replay-window-hash",
        evaluator_version="feedback-ranking-v2",
        sample_count=8,
        independent_window_count=2,
        quality_metrics=(
            QualityMetricEvidence("Recall@K", 0.5, 0.56, 0.01, 0.11),
            QualityMetricEvidence("MRR", 0.5, 0.51, 0.0, 0.02),
            QualityMetricEvidence("nDCG", 0.5, 0.51, 0.0, 0.02),
        ),
        latency_metrics=(
            LatencyEvidence("retrieval_stage", 50.0, 100.0, 45.0, 95.0),
            LatencyEvidence("ttft", 100.0, 200.0, 95.0, 190.0),
        ),
        baseline_token_cost=100.0,
        candidate_token_cost=100.0,
        regression_checks=tuple(sorted(REQUIRED_EVIDENCE_REGRESSION_CHECKS)),
        regression_failures=(),
    )


def _candidate_signature(candidate: object) -> tuple[object, ...]:
    """忽略随机 ID 和创建时间，返回确定性候选字段。"""

    return (
        candidate.aggregation_revision,
        candidate.proposed_document_weight,
        candidate.proposed_graph_weight,
        candidate.accepted_count,
        candidate.independent_window_count,
        candidate.decayed_support,
        candidate.status,
        candidate.reason_code,
        candidate.window_set,
    )


def test_global_reduction_is_order_independent_across_domains() -> None:
    """跨 scope/persona 的输入顺序不得改变唯一全局候选。"""

    aggregates = [
        _aggregate(
            scope="scope-b",
            persona="persona-2",
            window=1,
            accepted=4,
            support=0.85,
            delta=0.07,
        ),
        _aggregate(
            scope="scope-a",
            persona=None,
            window=0,
            accepted=2,
            support=0.75,
            delta=0.05,
        ),
    ]

    first = reduce_global_candidate(
        aggregates,
        binding=_binding(),
        min_samples=3,
        min_independent_windows=2,
    )
    second = reduce_global_candidate(
        list(reversed(aggregates)),
        binding=_binding(),
        min_samples=3,
        min_independent_windows=2,
    )

    assert _candidate_signature(first) == _candidate_signature(second)
    assert first.candidate_scope == "global_aggregate"
    assert second.candidate_scope == "global_aggregate"


def test_global_reduction_accumulates_all_windows_instead_of_last_write_wins() -> None:
    """多窗口样本、支持度和窗口数必须整体归并。"""

    aggregates = [
        _aggregate(
            scope="scope-a",
            persona="persona-a",
            window=0,
            accepted=3,
            support=0.7,
            delta=0.04,
        ),
        _aggregate(
            scope="scope-a",
            persona="persona-a",
            window=1,
            accepted=5,
            support=0.9,
            delta=0.08,
        ),
        _aggregate(
            scope="scope-b",
            persona=None,
            window=1,
            accepted=7,
            support=0.8,
            delta=0.06,
        ),
    ]

    candidate = reduce_global_candidate(
        aggregates,
        binding=_binding(),
        min_samples=3,
        min_independent_windows=2,
    )

    assert candidate.accepted_count == 15
    assert candidate.independent_window_count == 2
    assert candidate.window_set == (
        "2026-08-01T00:00:00+00:00/2026-08-01T01:00:00+00:00",
        "2026-08-01T01:00:00+00:00/2026-08-01T02:00:00+00:00",
    )
    assert 0.74 < candidate.proposed_document_weight < 0.78
    assert candidate.status == "ready_for_review"


def test_opposite_window_directions_are_rejected_before_publish() -> None:
    """证据方向相反时不得通过平均值掩盖冲突。"""

    candidate = reduce_global_candidate(
        [
            _aggregate(
                scope="scope-a",
                persona=None,
                window=0,
                accepted=5,
                support=0.9,
                delta=0.08,
            ),
            _aggregate(
                scope="scope-b",
                persona=None,
                window=1,
                accepted=5,
                support=0.1,
                delta=-0.08,
            ),
        ],
        binding=_binding(),
        min_samples=3,
        min_independent_windows=2,
    )

    assert candidate.status == "rejected"
    assert candidate.reason_code == "conflicting_evidence"


def test_candidate_uses_high_entropy_opaque_id_and_revision_bindings() -> None:
    """生产候选只暴露随机 URL-safe ID，并固定全部发布前置版本。"""

    candidate = reduce_global_candidate(
        [
            _aggregate(
                scope="scope-CANARY",
                persona="persona-CANARY",
                window=0,
                accepted=3,
                support=0.8,
                delta=0.06,
            ),
            _aggregate(
                scope="scope-CANARY",
                persona="persona-CANARY",
                window=1,
                accepted=3,
                support=0.8,
                delta=0.06,
            ),
        ],
        binding=_binding(),
        min_samples=3,
        min_independent_windows=2,
    )

    assert 22 <= len(candidate.candidate_id) <= 128
    assert re.fullmatch(r"[A-Za-z0-9_-]+", candidate.candidate_id)
    assert "CANARY" not in candidate.candidate_id
    assert candidate.candidate_id not in candidate.candidate_key
    assert candidate.source_config_revision == "b" * 64
    assert candidate.evidence_revision == "evidence-revision-23"
    assert candidate.quality_gate_version == "quality-gate-v2"
    assert len(candidate.aggregation_revision) == 64
    assert len(candidate.baseline_snapshot_hash) == 64
    assert len(candidate.target_snapshot_hash) == 64


def test_failed_quality_gate_never_creates_publishable_candidate() -> None:
    """评测产物未通过 Gate 时只能保留为不可发布证据。"""

    candidate = reduce_global_candidate(
        [
            _aggregate(
                scope="scope-a",
                persona=None,
                window=0,
                accepted=4,
                support=0.8,
                delta=0.06,
            ),
            _aggregate(
                scope="scope-a",
                persona=None,
                window=1,
                accepted=4,
                support=0.8,
                delta=0.06,
            ),
        ],
        binding=_binding(passed=False),
        min_samples=3,
        min_independent_windows=2,
    )

    assert candidate.status == "rejected"
    assert candidate.reason_code == "quality_gate_failed"


class _FeedbackManager:
    """提供可替换窗口聚合的最小 FeedbackSignalManager 协议。"""

    def __init__(self, aggregates: list[FeedbackSignalAggregate]) -> None:
        """保存当前聚合和固定基线策略。"""

        self.aggregates = aggregates
        self.policy = SimpleNamespace(
            baseline_document_weight=0.7,
            baseline_graph_weight=0.3,
            policy_version=3,
        )

    def rebuild(self, *, reference_time: datetime) -> list[FeedbackSignalAggregate]:
        """返回隔离副本；reference time 仅用于匹配生产协议。"""

        assert reference_time.tzinfo is not None
        return list(self.aggregates)


class _RecordingAdapter:
    """模拟会推进权威 revision 的 typed 配置适配器。"""

    def __init__(
        self,
        *,
        revision: str,
        document_weight: float,
        graph_weight: float,
        blocked: bool = False,
    ) -> None:
        """初始化当前快照与可选阻塞屏障。"""

        self.snapshot = self._make_snapshot(
            revision,
            document_weight,
            graph_weight,
        )
        self.apply_calls: list[tuple[dict[str, float], str]] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()

    async def get_weight_snapshot(self) -> LearningConfigSnapshot:
        """返回当前权威快照。"""

        return self.snapshot

    async def apply_weights(
        self,
        target_weights: dict[str, float],
        *,
        expected_revision: str,
    ) -> LearningConfigApplyResult:
        """记录一次 CAS，等待屏障后生成下一权威 revision。"""

        before = self.snapshot
        self.apply_calls.append((dict(target_weights), expected_revision))
        self.entered.set()
        await self.release.wait()
        next_revision = stable_revision(
            "test-config-revision",
            {"previous": expected_revision, "ordinal": len(self.apply_calls)},
        )
        self.snapshot = self._make_snapshot(
            next_revision,
            target_weights["document_route_weight"],
            target_weights["graph_route_weight"],
        )
        return LearningConfigApplyResult(
            requested_revision=expected_revision,
            applied_revision=next_revision,
            changed_paths=(
                "graph_memory.document_route_weight",
                "graph_memory.graph_route_weight",
            ),
            before_hash=before.config_hash,
            after_hash=self.snapshot.config_hash,
            applied=True,
            no_op=False,
            reason_code="config_applied",
        )

    @staticmethod
    def _make_snapshot(
        revision: str,
        document_weight: float,
        graph_weight: float,
    ) -> LearningConfigSnapshot:
        """构造与 revision 和权重一致的低敏测试快照。"""

        weights = {
            "document_route_weight": document_weight,
            "graph_route_weight": graph_weight,
        }
        return LearningConfigSnapshot(
            revision=revision,
            document_route_weight=document_weight,
            graph_route_weight=graph_weight,
            config_hash=stable_revision(
                "test-config-snapshot",
                {"revision": revision, "weights": weights},
            ),
            weight_hash=weight_snapshot_hash(weights),
        )


def _ready_aggregates(*, delta: float = 0.06) -> list[FeedbackSignalAggregate]:
    """生成两个同向独立窗口，供 manager 动作测试使用。"""

    support = 0.8 if delta > 0 else 0.2
    return [
        _aggregate(
            scope="scope-a",
            persona=None,
            window=0,
            accepted=4,
            support=support,
            delta=delta,
        ),
        _aggregate(
            scope="scope-b",
            persona="persona-b",
            window=1,
            accepted=4,
            support=support,
            delta=delta,
        ),
    ]


async def _build_ready_manager(
    tmp_path: Any,
    *,
    binding: CandidateBinding,
) -> tuple[AutoLearningManager, _FeedbackManager, dict[str, Any]]:
    """构造已生成一个全局 ready candidate 的 manager。"""

    aggregates = _ready_aggregates()
    feedback = _FeedbackManager(aggregates)
    artifact = _artifact_for(aggregates, binding)
    manager = AutoLearningManager(
        feedback,
        data_dir=str(tmp_path),
        enabled=True,
        min_independent_windows=2,
        min_samples=3,
        evidence_provider=lambda _items: artifact,
        quality_gate_version=binding.quality_gate_version,
    )
    candidates = await manager.rebuild_candidates(
        reference_time=datetime(2026, 8, 3, tzinfo=UTC),
    )
    ready = next(item for item in candidates if item["status"] == "ready_for_review")
    return manager, feedback, ready


async def _publish(
    manager: AutoLearningManager,
    candidate: dict[str, Any],
    adapter: _RecordingAdapter,
) -> dict[str, Any]:
    """按候选绑定 revision 执行一次测试发布。"""

    return await manager.publish_candidate(
        candidate["candidate_id"],
        config_adapter=adapter,
        expected_revision=candidate["source_config_revision"],
    )


@pytest.mark.asyncio
async def test_prepared_intent_persistence_failure_skips_typed_writer(
    tmp_path: Any,
) -> None:
    """prepared intent 无法落盘时不得调用 typed 配置 writer。"""

    binding = _binding()
    manager, _, ready = await _build_ready_manager(tmp_path, binding=binding)
    adapter = _RecordingAdapter(
        revision=binding.source_config_revision,
        document_weight=0.7,
        graph_weight=0.3,
    )

    async def fail_save() -> None:
        """模拟 intent 写入安全状态文件失败。"""

        raise AutoLearningStatePersistenceError("simulated")

    manager._save_state = fail_save
    result = await _publish(manager, ready, adapter)
    status = await manager.get_status_snapshot()

    assert result == {
        "published": False,
        "reason_code": "learning_state_persistence_failed",
    }
    assert adapter.apply_calls == []
    assert manager.last_published_snapshot(ready["candidate_id"]) is None
    assert status["recovery"]["intent_count"] == 0
    assert status["recovery"]["operation"] is None


@pytest.mark.asyncio
async def test_final_state_persistence_failure_keeps_restart_rollback_intent(
    tmp_path: Any,
) -> None:
    """配置已提交而最终状态落盘失败时重启仍可按 intent 回滚。"""

    binding = _binding()
    manager, feedback, ready = await _build_ready_manager(tmp_path, binding=binding)
    adapter = _RecordingAdapter(
        revision=binding.source_config_revision,
        document_weight=0.7,
        graph_weight=0.3,
    )
    real_save = manager._save_state
    save_calls = 0

    async def fail_final_save() -> None:
        """仅阻断 writer 成功后的最终状态收口。"""

        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise AutoLearningStatePersistenceError("simulated")
        await real_save()

    manager._save_state = fail_final_save
    result = await _publish(manager, ready, adapter)

    assert result["published"] is False
    assert result["reason_code"] == "learning_publish_recovery_required"
    assert result["recovery_required"] is True
    assert result["config_applied"] is True
    assert result["applied_revision"] == adapter.snapshot.revision
    assert len(adapter.apply_calls) == 1

    restarted = AutoLearningManager(
        feedback,
        data_dir=str(tmp_path),
        enabled=True,
    )
    await restarted.load_state()
    recovered = restarted.last_published_snapshot(ready["candidate_id"])
    assert recovered is not None
    assert recovered["phase"] == "recovery_required"
    assert recovered["before_document_weight"] == 0.7
    assert recovered["before_graph_weight"] == 0.3

    rollback = await restarted.rollback_last_publish(
        ready["candidate_id"],
        config_adapter=adapter,
        expected_revision=adapter.snapshot.revision,
    )

    assert rollback["restored"] is True
    assert rollback["reason_code"] == "restored"
    assert rollback["changed_paths"] == [
        "graph_memory.document_route_weight",
        "graph_memory.graph_route_weight",
    ]
    assert adapter.apply_calls[-1][0] == {
        "document_route_weight": 0.7,
        "graph_route_weight": 0.3,
    }
    assert restarted.last_published_snapshot(ready["candidate_id"]) is None


@pytest.mark.asyncio
async def test_revision_drift_rejects_before_config_writer(tmp_path: Any) -> None:
    """配置、聚合、证据或 Gate 任一漂移都不得调用 writer。"""

    binding = _binding()
    manager, _, ready = await _build_ready_manager(tmp_path, binding=binding)
    adapter = _RecordingAdapter(
        revision="c" * 64,
        document_weight=0.7,
        graph_weight=0.3,
    )

    result = await manager.publish_candidate(
        ready["candidate_id"],
        config_adapter=adapter,
        expected_revision=binding.source_config_revision,
    )

    assert result["published"] is False
    assert result["reason_code"] == "learning_candidate_unavailable"
    assert adapter.apply_calls == []


@pytest.mark.asyncio
async def test_writer_runs_without_state_lock_and_second_publish_is_immediate(
    tmp_path: Any,
) -> None:
    """外部 writer 期间释放状态锁，相同动作立即返回 in-progress。"""

    binding = _binding()
    manager, _, ready = await _build_ready_manager(tmp_path, binding=binding)
    adapter = _RecordingAdapter(
        revision=binding.source_config_revision,
        document_weight=0.7,
        graph_weight=0.3,
        blocked=True,
    )
    first_task = asyncio.create_task(_publish(manager, ready, adapter))
    await adapter.entered.wait()

    assert manager._state_lock.locked() is False
    second = await _publish(manager, ready, adapter)

    assert second["published"] is False
    assert second["reason_code"] == "learning_publish_in_progress"
    assert len(adapter.apply_calls) == 1
    adapter.release.set()
    first = await first_task
    assert first["published"] is True


@pytest.mark.asyncio
async def test_publication_chain_only_rolls_back_active_child_then_parent(
    tmp_path: Any,
) -> None:
    """P1 -> P2 必须按当前 child、直接 parent 顺序恢复。"""

    binding_one = _binding()
    manager, feedback, candidate_one = await _build_ready_manager(
        tmp_path,
        binding=binding_one,
    )
    adapter = _RecordingAdapter(
        revision=binding_one.source_config_revision,
        document_weight=0.7,
        graph_weight=0.3,
    )
    first = await _publish(manager, candidate_one, adapter)
    assert first["published"] is True

    feedback.aggregates = _ready_aggregates(delta=-0.04)
    binding_two = CandidateBinding(
        source_config_revision=adapter.snapshot.revision,
        evidence_revision="evidence-revision-24",
        quality_gate_version="quality-gate-v2",
        evidence_passed=True,
    )
    artifact_two = _artifact_for(feedback.aggregates, binding_two)
    manager._evidence_provider = lambda _items: artifact_two
    candidates = await manager.rebuild_candidates(
        reference_time=datetime(2026, 8, 3, tzinfo=UTC),
    )
    candidate_two = next(
        item for item in candidates if item["status"] == "ready_for_review"
    )
    second = await _publish(manager, candidate_two, adapter)
    assert second["published"] is True

    blocked = await manager.rollback_last_publish(
        candidate_one["candidate_id"],
        config_adapter=adapter,
        expected_revision=adapter.snapshot.revision,
    )
    assert blocked["restored"] is False
    assert blocked["reason_code"] == "learning_candidate_unavailable"
    assert len(adapter.apply_calls) == 2

    child = await manager.rollback_last_publish(
        candidate_two["candidate_id"],
        config_adapter=adapter,
        expected_revision=adapter.snapshot.revision,
    )
    assert child["restored"] is True
    status_after_child = await manager.get_status_snapshot()
    assert (
        status_after_child["active_publication"]["candidate_id"]
        == candidate_one["candidate_id"]
    )

    parent = await manager.rollback_last_publish(
        candidate_one["candidate_id"],
        config_adapter=adapter,
        expected_revision=adapter.snapshot.revision,
    )
    assert parent["restored"] is True, parent
    status_after_parent = await manager.get_status_snapshot()
    assert status_after_parent["active_publication"] is None
    assert [call[0] for call in adapter.apply_calls[-2:]] == [
        {"document_route_weight": 0.76, "graph_route_weight": 0.24},
        {"document_route_weight": 0.7, "graph_route_weight": 0.3},
    ]


@pytest.mark.asyncio
async def test_reset_preserves_active_publication_and_rollback_evidence(
    tmp_path: Any,
) -> None:
    """reset 只能清除安全 shadow，不能删除 active/parent/intent 证据。"""

    binding = _binding()
    manager, _, ready = await _build_ready_manager(tmp_path, binding=binding)
    adapter = _RecordingAdapter(
        revision=binding.source_config_revision,
        document_weight=0.7,
        graph_weight=0.3,
    )
    assert (await _publish(manager, ready, adapter))["published"]

    result = await manager.reset()
    status = await manager.get_status_snapshot()

    assert result["reset"] is True
    assert status["active_publication"]["candidate_id"] == ready["candidate_id"]
    assert status["publication_count"] == 1
