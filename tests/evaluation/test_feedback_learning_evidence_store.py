"""自主学习离线证据 inbox 的不可变持久化与生产读取契约。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading

import pytest

from core.features.learning.domain.feedback_learning_evidence import (
    EvidenceEvaluatorConfig,
    LatencyEvidence,
    QualityMetricEvidence,
    build_learning_evidence,
)
from core.features.learning.domain.feedback_learning_evidence_contract import (
    REQUIRED_EVIDENCE_REGRESSION_CHECKS,
)
from core.features.learning.infrastructure import (
    feedback_learning_evidence_store as store_module,
)
from core.features.learning.infrastructure.feedback_learning_evidence_store import (
    FeedbackLearningEvidenceInbox,
    FeedbackLearningEvidenceProvider,
    LearningEvidenceInboxError,
)

_AGGREGATION_REVISION = "a" * 64
_SOURCE_CONFIG_REVISION = "b" * 64
_NEXT_SOURCE_CONFIG_REVISION = "c" * 64
_OTHER_SOURCE_CONFIG_REVISION = "d" * 64


def _artifact(**overrides):
    """构造通过固定生产 Gate 的匿名 artifact。"""

    values = {
        "aggregation_revision": _AGGREGATION_REVISION,
        "source_config_revision": _SOURCE_CONFIG_REVISION,
        "quality_gate_version": "quality-gate-v1",
        "dataset_hash": "dataset-hash",
        "replay_window_hash": "window-hash",
        "evaluator_version": "feedback-ranking-evidence-v1",
        "sample_count": 100,
        "independent_window_count": 3,
        "quality_metrics": (
            QualityMetricEvidence("Recall@K", 0.70, 0.75, 0.01, 0.09),
            QualityMetricEvidence("MRR", 0.60, 0.63, 0.00, 0.06),
            QualityMetricEvidence("nDCG", 0.65, 0.68, 0.00, 0.06),
        ),
        "latency_metrics": (
            LatencyEvidence("retrieval_stage", 100.0, 200.0, 90.0, 190.0),
            LatencyEvidence("ttft", 300.0, 400.0, 290.0, 390.0),
        ),
        "evaluation_k": 5,
        "evaluator_config": EvidenceEvaluatorConfig(),
        "baseline_snapshot_hash": "a" * 64,
        "target_snapshot_hash": "b" * 64,
        "baseline_provider_calls": 10.0,
        "candidate_provider_calls": 9.0,
        "baseline_token_cost": 10.0,
        "candidate_token_cost": 9.0,
        "regression_checks": tuple(sorted(REQUIRED_EVIDENCE_REGRESSION_CHECKS)),
        "regression_failures": (),
    }
    values.update(overrides)
    return build_learning_evidence(**values)


def _checksum(value: object) -> str:
    """按生产 canonical JSON 规则重算测试文档 checksum。"""

    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@pytest.mark.asyncio
async def test_publish_round_trip_requires_exact_binding(tmp_path) -> None:
    """只允许当前 aggregation、配置 revision 与 Gate 的精确 artifact。"""

    inbox = FeedbackLearningEvidenceInbox(tmp_path)
    artifact = _artifact()

    revision = await inbox.publish(artifact)

    assert revision == artifact.evidence_revision
    assert (
        await inbox.load_current(
            aggregation_revision=_AGGREGATION_REVISION,
            source_config_revision=_SOURCE_CONFIG_REVISION,
            quality_gate_version="quality-gate-v1",
        )
        == artifact
    )
    assert (
        await inbox.load_current(
            aggregation_revision=_AGGREGATION_REVISION,
            source_config_revision=_OTHER_SOURCE_CONFIG_REVISION,
            quality_gate_version="quality-gate-v1",
        )
        is None
    )


@pytest.mark.asyncio
async def test_artifact_file_is_immutable_while_current_pointer_rotates(
    tmp_path,
) -> None:
    """发布新证据只能切换指针，不能覆盖旧 revision 的 artifact。"""

    inbox = FeedbackLearningEvidenceInbox(tmp_path)
    first = _artifact()
    second = _artifact(source_config_revision=_NEXT_SOURCE_CONFIG_REVISION)
    await inbox.publish(first)
    first_path = inbox.artifact_path(first.evidence_revision)
    first_bytes = first_path.read_bytes()

    await inbox.publish(second)

    assert first_path.read_bytes() == first_bytes
    assert inbox.artifact_path(second.evidence_revision).is_file()
    assert (
        await inbox.load_current(
            aggregation_revision=_AGGREGATION_REVISION,
            source_config_revision=_SOURCE_CONFIG_REVISION,
            quality_gate_version="quality-gate-v1",
        )
        is None
    )
    assert (
        await inbox.load_current(
            aggregation_revision=_AGGREGATION_REVISION,
            source_config_revision=_NEXT_SOURCE_CONFIG_REVISION,
            quality_gate_version="quality-gate-v1",
        )
        == second
    )


@pytest.mark.asyncio
async def test_tampered_artifact_fails_closed_and_cannot_be_overwritten(
    tmp_path,
) -> None:
    """checksum 或 revision 被篡改时读取与同 revision 重写都必须拒绝。"""

    inbox = FeedbackLearningEvidenceInbox(tmp_path)
    artifact = _artifact()
    await inbox.publish(artifact)
    artifact_path = inbox.artifact_path(artifact.evidence_revision)
    document = json.loads(artifact_path.read_text(encoding="utf-8"))
    document["artifact"]["sample_count"] = 999
    artifact_path.write_text(json.dumps(document), encoding="utf-8")

    assert (
        await inbox.load_current(
            aggregation_revision=_AGGREGATION_REVISION,
            source_config_revision=_SOURCE_CONFIG_REVISION,
            quality_gate_version="quality-gate-v1",
        )
        is None
    )
    with pytest.raises(LearningEvidenceInboxError, match="artifact_collision"):
        await inbox.publish(artifact)


@pytest.mark.asyncio
async def test_duplicate_pointer_keys_and_oversized_artifacts_fail_closed(
    tmp_path,
) -> None:
    """重复 JSON 键和超出上限的文件不得被生产 provider 接受。"""

    inbox = FeedbackLearningEvidenceInbox(tmp_path)
    artifact = _artifact()
    await inbox.publish(artifact)
    inbox.current_path.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )

    assert (
        await inbox.load_current(
            aggregation_revision=_AGGREGATION_REVISION,
            source_config_revision=_SOURCE_CONFIG_REVISION,
            quality_gate_version="quality-gate-v1",
        )
        is None
    )

    tiny_inbox = FeedbackLearningEvidenceInbox(
        tmp_path / "tiny",
        max_artifact_bytes=128,
    )
    with pytest.raises(LearningEvidenceInboxError, match="artifact_too_large"):
        await tiny_inbox.publish(artifact)


@pytest.mark.asyncio
async def test_valid_checksum_with_invalid_metric_type_fails_closed(tmp_path) -> None:
    """合法 envelope checksum 也不能让畸形 metric 类型逃逸为异常。"""

    inbox = FeedbackLearningEvidenceInbox(tmp_path)
    artifact = _artifact()
    await inbox.publish(artifact)
    path = inbox.artifact_path(artifact.evidence_revision)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["artifact"]["quality_metrics"][0]["name"] = ["Recall@K"]
    document["artifact_checksum"] = _checksum(document["artifact"])
    path.write_text(json.dumps(document), encoding="utf-8")

    assert (
        await inbox.load_current(
            aggregation_revision=_AGGREGATION_REVISION,
            source_config_revision=_SOURCE_CONFIG_REVISION,
            quality_gate_version="quality-gate-v1",
        )
        is None
    )


@pytest.mark.asyncio
async def test_unsafe_evaluator_or_regression_text_is_never_persisted(tmp_path) -> None:
    """evaluator 与回归原因只允许固定低敏代码，禁止原文进入文件。"""

    inbox = FeedbackLearningEvidenceInbox(tmp_path)
    unsafe_evaluator = _artifact(evaluator_version="PROVIDER-KEY-CANARY")
    unsafe_reason = _artifact(regression_failures=("QUERY-SECRET-CANARY",))

    for artifact in (unsafe_evaluator, unsafe_reason):
        with pytest.raises(LearningEvidenceInboxError, match="artifact_invalid"):
            await inbox.publish(artifact)

    assert not inbox.current_path.exists()
    if inbox.directory.exists():
        serialized = "".join(
            path.read_text(encoding="utf-8") for path in inbox.directory.glob("*.json")
        )
        assert "PROVIDER-KEY-CANARY" not in serialized
        assert "QUERY-SECRET-CANARY" not in serialized


@pytest.mark.asyncio
async def test_pointer_failure_preserves_previous_current(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新 current 指针替换失败时仍能读取上一个完整 artifact。"""

    inbox = FeedbackLearningEvidenceInbox(tmp_path)
    first = _artifact()
    second = _artifact(source_config_revision=_NEXT_SOURCE_CONFIG_REVISION)
    await inbox.publish(first)
    original_replace = store_module._atomic_replace

    def fail_current(path, payload) -> None:
        """只故障注入 current 指针替换。"""

        if path.name == "current.json":
            raise LearningEvidenceInboxError("learning_evidence_persistence_failed")
        original_replace(path, payload)

    monkeypatch.setattr(store_module, "_atomic_replace", fail_current)
    with pytest.raises(LearningEvidenceInboxError, match="persistence_failed"):
        await inbox.publish(second)

    assert (
        await inbox.load_current(
            aggregation_revision=_AGGREGATION_REVISION,
            source_config_revision=_SOURCE_CONFIG_REVISION,
            quality_gate_version="quality-gate-v1",
        )
        == first
    )


@pytest.mark.asyncio
async def test_provider_uses_current_config_revision_and_exact_aggregation(
    tmp_path,
) -> None:
    """运行时 provider 必须从受信回调取得当前配置 revision。"""

    inbox = FeedbackLearningEvidenceInbox(tmp_path)
    artifact = _artifact()
    await inbox.publish(artifact)
    calls: list[str] = []

    async def current_config_revision() -> str:
        """返回测试中的权威配置 revision。"""

        calls.append("config")
        return _SOURCE_CONFIG_REVISION

    def aggregation_revision(_aggregates) -> str:
        """返回测试中的确定性聚合 revision。"""

        calls.append("aggregation")
        return _AGGREGATION_REVISION

    provider = FeedbackLearningEvidenceProvider(
        inbox,
        aggregation_revision_provider=aggregation_revision,
        source_config_revision_provider=current_config_revision,
        quality_gate_version="quality-gate-v1",
    )

    assert await provider(()) == artifact
    assert calls == ["aggregation", "config", "config"]


@pytest.mark.asyncio
async def test_provider_rejects_config_revision_drift_during_read(tmp_path) -> None:
    """Inbox 读取期间配置 revision 漂移时不得返回旧配置的 ready 证据。"""

    inbox = FeedbackLearningEvidenceInbox(tmp_path)
    await inbox.publish(_artifact())
    revisions = iter((_SOURCE_CONFIG_REVISION, _NEXT_SOURCE_CONFIG_REVISION))
    provider = FeedbackLearningEvidenceProvider(
        inbox,
        aggregation_revision_provider=lambda _aggregates: _AGGREGATION_REVISION,
        source_config_revision_provider=lambda: next(revisions),
        quality_gate_version="quality-gate-v1",
    )

    assert await provider(()) is None


@pytest.mark.asyncio
async def test_provider_propagates_config_revision_cancellation(tmp_path) -> None:
    """宿主取消配置快照读取时不得降级成证据缺失。"""

    inbox = FeedbackLearningEvidenceInbox(tmp_path)

    async def cancelled_revision() -> str:
        """模拟宿主取消当前配置读取。"""

        raise asyncio.CancelledError

    provider = FeedbackLearningEvidenceProvider(
        inbox,
        aggregation_revision_provider=lambda _aggregates: _AGGREGATION_REVISION,
        source_config_revision_provider=cancelled_revision,
        quality_gate_version="quality-gate-v1",
    )

    with pytest.raises(asyncio.CancelledError):
        await provider(())


@pytest.mark.asyncio
async def test_publish_finishes_atomic_commit_before_propagating_cancel(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消不得让后台线程在调用方返回后继续未知地切换 current。"""

    inbox = FeedbackLearningEvidenceInbox(tmp_path)
    artifact = _artifact()
    started = threading.Event()
    release = threading.Event()
    original_publish = inbox._publish_sync

    def blocked_publish(value) -> str:
        """把本地提交固定在可控取消窗口。"""

        started.set()
        release.wait(timeout=2.0)
        return original_publish(value)

    monkeypatch.setattr(inbox, "_publish_sync", blocked_publish)
    task = asyncio.create_task(inbox.publish(artifact))
    assert await asyncio.to_thread(started.wait, 1.0)
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert inbox.current_path.is_file()
    assert inbox.artifact_path(artifact.evidence_revision).is_file()


@pytest.mark.asyncio
async def test_publish_survives_repeated_cancellation_until_commit_finishes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续取消也必须等待本地原子提交终态后再传播一次取消。"""

    inbox = FeedbackLearningEvidenceInbox(tmp_path)
    artifact = _artifact()
    started = threading.Event()
    release = threading.Event()
    original_publish = inbox._publish_sync

    def blocked_publish(value) -> str:
        """把本地提交固定在可重复取消的窗口。"""

        started.set()
        release.wait(timeout=2.0)
        return original_publish(value)

    monkeypatch.setattr(inbox, "_publish_sync", blocked_publish)
    task = asyncio.create_task(inbox.publish(artifact))
    assert await asyncio.to_thread(started.wait, 1.0)
    try:
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert task.done() is False
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert inbox.current_path.is_file()
    assert inbox.artifact_path(artifact.evidence_revision).is_file()
