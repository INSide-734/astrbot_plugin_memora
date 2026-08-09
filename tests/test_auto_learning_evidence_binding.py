"""自主学习候选与不可变质量证据的生产绑定契约。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from core.api.learning_config_adapter import (
    LearningConfigApplyResult,
    LearningConfigSnapshot,
)
from core.evaluation.feedback_learning_evidence_store import (
    FeedbackLearningEvidenceInbox,
    FeedbackLearningEvidenceProvider,
)
from core.features.learning.application.auto_learning import AutoLearningManager
from core.features.learning.domain.auto_learning_actions import (
    aggregation_revision_for,
    stable_revision,
    weight_snapshot_hash,
)
from core.features.learning.domain.feedback_learning_evidence import (
    LatencyEvidence,
    LearningEvidenceArtifact,
    QualityMetricEvidence,
    build_learning_evidence,
)
from core.features.learning.domain.feedback_learning_evidence_contract import (
    REQUIRED_EVIDENCE_REGRESSION_CHECKS,
)
from core.models.feedback_signal import FeedbackSignalAggregate


class _FeedbackManager:
    """提供固定全局归并输入和基线策略。"""

    def __init__(self, aggregates: list[FeedbackSignalAggregate]) -> None:
        """保存测试聚合与固定基线。"""

        self.aggregates = aggregates
        self.policy = SimpleNamespace(
            baseline_document_weight=0.7,
            baseline_graph_weight=0.3,
        )

    def rebuild(self, *, reference_time: datetime) -> list[FeedbackSignalAggregate]:
        """返回聚合副本，避免 manager 修改测试输入。"""

        del reference_time
        return list(self.aggregates)


class _ConfigAdapter:
    """记录生产写调用并维护权威配置快照。"""

    def __init__(self, revision: str) -> None:
        """用基线权重和指定 revision 初始化 adapter。"""

        self.snapshot = self._snapshot(revision, 0.7, 0.3)
        self.apply_calls = 0

    async def get_weight_snapshot(self) -> LearningConfigSnapshot:
        """返回当前权威快照。"""

        return self.snapshot

    async def apply_weights(
        self,
        target_weights: dict[str, float],
        *,
        expected_revision: str,
    ) -> LearningConfigApplyResult:
        """执行一次确定性测试 CAS 并返回 typed 结果。"""

        self.apply_calls += 1
        before = self.snapshot
        applied_revision = stable_revision(
            "test-config-revision",
            {"previous": expected_revision, "ordinal": self.apply_calls},
        )
        self.snapshot = self._snapshot(
            applied_revision,
            target_weights["document_route_weight"],
            target_weights["graph_route_weight"],
        )
        return LearningConfigApplyResult(
            requested_revision=expected_revision,
            applied_revision=applied_revision,
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
    def _snapshot(
        revision: str,
        document_weight: float,
        graph_weight: float,
    ) -> LearningConfigSnapshot:
        """构造 revision、完整配置 hash 与权重 hash 一致的快照。"""

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


def _aggregates() -> list[FeedbackSignalAggregate]:
    """生成两个同向且来自独立窗口的可信聚合。"""

    start = datetime(2026, 8, 1, tzinfo=UTC)
    return [
        FeedbackSignalAggregate(
            scope_domain=f"scope-{index}",
            persona_domain=None,
            window_start=start + timedelta(hours=index),
            window_end=start + timedelta(hours=index + 1),
            accepted_count=4,
            independent_window_count=2,
            decayed_support=0.8,
            proposed_document_weight=0.76,
            proposed_graph_weight=0.24,
            delta_from_baseline=0.06,
            status="candidate",
            policy_version=3,
        )
        for index in range(2)
    ]


def _artifact(
    aggregates: list[FeedbackSignalAggregate],
    *,
    source_revision: str = "b" * 64,
    aggregation_revision: str | None = None,
    dataset_hash: str = "dataset-hash-1",
) -> LearningEvidenceArtifact:
    """构造通过质量、p95 延迟与成本门的不可变 artifact。"""

    return build_learning_evidence(
        aggregation_revision=aggregation_revision
        or aggregation_revision_for(aggregates),
        source_config_revision=source_revision,
        quality_gate_version="quality-gate-v2",
        dataset_hash=dataset_hash,
        replay_window_hash="replay-window-hash-1",
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


async def _ready_manager(
    tmp_path: Any,
) -> tuple[AutoLearningManager, dict[str, Any], LearningEvidenceArtifact]:
    """构造通过 provider 接入真实 artifact 的 ready manager。"""

    aggregates = _aggregates()
    artifact = _artifact(aggregates)
    manager = AutoLearningManager(
        _FeedbackManager(aggregates),
        data_dir=str(tmp_path),
        enabled=True,
        min_samples=3,
        min_independent_windows=2,
        evidence_provider=lambda _items: artifact,
        quality_gate_version="quality-gate-v2",
    )
    candidates = await manager.rebuild_candidates(
        reference_time=datetime(2026, 8, 3, tzinfo=UTC)
    )
    ready = next(item for item in candidates if item["status"] == "ready_for_review")
    return manager, ready, artifact


@pytest.mark.asyncio
async def test_inbox_provider_drives_ready_candidate_and_rejects_revision_drift(
    tmp_path: Any,
) -> None:
    """真实 Inbox 只为当前聚合与配置 revision 生成可审阅候选。"""

    aggregates = _aggregates()
    artifact = _artifact(aggregates)
    inbox = FeedbackLearningEvidenceInbox(tmp_path)
    await inbox.publish(artifact)

    provider = FeedbackLearningEvidenceProvider(
        inbox,
        aggregation_revision_provider=aggregation_revision_for,
        source_config_revision_provider=lambda: artifact.source_config_revision,
        quality_gate_version="quality-gate-v2",
    )
    manager = AutoLearningManager(
        _FeedbackManager(aggregates),
        data_dir=str(tmp_path / "ready"),
        enabled=True,
        evidence_provider=provider,
        quality_gate_version="quality-gate-v2",
    )

    candidates = await manager.rebuild_candidates(
        reference_time=datetime(2026, 8, 3, tzinfo=UTC)
    )

    assert len(candidates) == 1
    assert candidates[0]["status"] == "ready_for_review"

    stale_provider = FeedbackLearningEvidenceProvider(
        inbox,
        aggregation_revision_provider=aggregation_revision_for,
        source_config_revision_provider=lambda: "c" * 64,
        quality_gate_version="quality-gate-v2",
    )
    stale_manager = AutoLearningManager(
        _FeedbackManager(aggregates),
        data_dir=str(tmp_path / "stale"),
        enabled=True,
        evidence_provider=stale_provider,
        quality_gate_version="quality-gate-v2",
    )

    stale_candidates = await stale_manager.rebuild_candidates(
        reference_time=datetime(2026, 8, 3, tzinfo=UTC)
    )

    assert len(stale_candidates) == 1
    assert stale_candidates[0]["status"] == "rejected"
    assert stale_candidates[0]["reason_code"] == "quality_gate_failed"


@pytest.mark.asyncio
async def test_publish_uses_manager_owned_artifact_without_caller_binding(
    tmp_path: Any,
) -> None:
    """API 只提供 opaque ID 与 revision，manager 自行复核 artifact。"""

    manager, ready, artifact = await _ready_manager(tmp_path)
    adapter = _ConfigAdapter(artifact.source_config_revision)

    result = await manager.publish_candidate(
        ready["candidate_id"],
        config_adapter=adapter,
        expected_revision=artifact.source_config_revision,
    )

    assert result["published"] is True
    assert adapter.apply_calls == 1


@pytest.mark.asyncio
async def test_publish_rejects_candidate_after_current_evidence_rotates(
    tmp_path: Any,
) -> None:
    """Inbox 当前指针轮换后，旧候选不得继续获得生产 writer 权限。"""

    aggregates = _aggregates()
    original = _artifact(aggregates)
    inbox = FeedbackLearningEvidenceInbox(tmp_path / "inbox")
    await inbox.publish(original)
    provider = FeedbackLearningEvidenceProvider(
        inbox,
        aggregation_revision_provider=aggregation_revision_for,
        source_config_revision_provider=lambda: original.source_config_revision,
        quality_gate_version="quality-gate-v2",
    )
    manager = AutoLearningManager(
        _FeedbackManager(aggregates),
        data_dir=str(tmp_path / "state"),
        enabled=True,
        min_samples=3,
        min_independent_windows=2,
        evidence_provider=provider,
        quality_gate_version="quality-gate-v2",
    )
    ready = next(
        item
        for item in await manager.rebuild_candidates(
            reference_time=datetime(2026, 8, 3, tzinfo=UTC)
        )
        if item["status"] == "ready_for_review"
    )
    rotated = _artifact(aggregates, dataset_hash="dataset-hash-2")
    assert rotated.evidence_revision != original.evidence_revision
    await inbox.publish(rotated)
    adapter = _ConfigAdapter(original.source_config_revision)

    result = await manager.publish_candidate(
        ready["candidate_id"],
        config_adapter=adapter,
        expected_revision=original.source_config_revision,
    )

    assert result["published"] is False
    assert result["reason_code"] == "learning_candidate_unavailable"
    assert adapter.apply_calls == 0


@pytest.mark.asyncio
async def test_tampered_artifact_is_rejected_before_config_writer(
    tmp_path: Any,
) -> None:
    """artifact 内容遭改动时必须在 ConfigManager 调用前 fail-closed。"""

    manager, ready, artifact = await _ready_manager(tmp_path)
    manager._evidence_artifacts[artifact.evidence_revision]["sample_count"] = 0
    adapter = _ConfigAdapter(artifact.source_config_revision)

    result = await manager.publish_candidate(
        ready["candidate_id"],
        config_adapter=adapter,
        expected_revision=artifact.source_config_revision,
    )

    assert result["published"] is False
    assert result["reason_code"] == "learning_candidate_unavailable"
    assert adapter.apply_calls == 0


@pytest.mark.asyncio
async def test_persisted_artifact_survives_restart_and_remains_publishable(
    tmp_path: Any,
) -> None:
    """重启后只能使用状态 checksum 内保存且仍可复核的 artifact。"""

    manager, ready, artifact = await _ready_manager(tmp_path)
    del manager
    restarted = AutoLearningManager(
        _FeedbackManager(_aggregates()),
        data_dir=str(tmp_path),
        enabled=True,
        quality_gate_version="quality-gate-v2",
    )
    await restarted.load_state()
    adapter = _ConfigAdapter(artifact.source_config_revision)

    result = await restarted.publish_candidate(
        ready["candidate_id"],
        config_adapter=adapter,
        expected_revision=artifact.source_config_revision,
    )

    assert result["published"] is True
    assert adapter.apply_calls == 1


@pytest.mark.asyncio
async def test_mismatched_aggregation_artifact_never_creates_ready_candidate(
    tmp_path: Any,
) -> None:
    """artifact 的聚合 revision 与当前窗口不一致时不得获得生产权限。"""

    aggregates = _aggregates()
    artifact = _artifact(aggregates, aggregation_revision="0" * 64)
    manager = AutoLearningManager(
        _FeedbackManager(aggregates),
        data_dir=str(tmp_path),
        enabled=True,
        evidence_provider=lambda _items: artifact,
        quality_gate_version="quality-gate-v2",
    )

    candidates = await manager.rebuild_candidates(
        reference_time=datetime(2026, 8, 3, tzinfo=UTC)
    )

    assert len(candidates) == 1
    assert candidates[0]["status"] == "rejected"
    assert candidates[0]["reason_code"] == "quality_gate_failed"
