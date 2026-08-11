"""learning feature 的分层所有权与端口契约。"""

import core.features.learning as learning_feature
from core.features.learning.application import (
    AutoLearningManager,
    AutoLearningOperationsMixin,
    AutoLearningPersistenceMixin,
    AutoLearningReloadMixin,
    AutoLearningRetentionMixin,
    FeedbackIngestResult,
    FeedbackRevokeResult,
    FeedbackSignalManager,
    TombstoneRetentionResult,
    normalize_reload_operation,
    record_explicit_correction,
    revoke_explicit_correction,
)
from core.features.learning.contracts import (
    FeedbackSignalServicePort,
    FeedbackSignalStorePort,
    LearningConfigAdapterPort,
    LearningEvidenceProviderPort,
)
from core.features.learning.domain import (
    FEEDBACK_REASON_CODES,
    CandidateBinding,
    FeedbackAdapterKind,
    FeedbackOutcome,
    FeedbackSignalAggregate,
    FeedbackSignalPolicy,
    GlobalLearningCandidate,
    TrustedFeedbackEvent,
    aggregation_revision_for,
    build_trusted_feedback_event,
    stable_revision,
)
from core.features.learning.infrastructure import (
    STATE_SCHEMA_VERSION,
    AutoLearningStateError,
    AutoLearningStateLoadResult,
    AutoLearningStatePersistenceError,
    AutoLearningStateStore,
    AutoLearningStateValidationError,
    FeedbackLearningEvidenceInbox,
    FeedbackLearningEvidenceProvider,
    FeedbackSignalStore,
    LearningConfigAdapter,
    LearningConfigApplyResult,
    LearningConfigSnapshot,
    LearningEvidenceInboxError,
)


def test_learning_feature_exports_application_owner() -> None:
    """feature 包级入口应恒等导出自主学习与反馈应用服务。"""

    assert learning_feature.AutoLearningManager is AutoLearningManager
    assert learning_feature.FeedbackIngestResult is FeedbackIngestResult
    assert learning_feature.FeedbackRevokeResult is FeedbackRevokeResult
    assert learning_feature.FeedbackSignalManager is FeedbackSignalManager
    assert learning_feature.record_explicit_correction is record_explicit_correction
    assert learning_feature.revoke_explicit_correction is revoke_explicit_correction


def test_learning_application_assembles_feature_owned_mixins() -> None:
    """自主学习 Manager 应只组合 feature application 内的职责 mixin。"""

    assert AutoLearningOperationsMixin in AutoLearningManager.__mro__
    assert AutoLearningPersistenceMixin in AutoLearningManager.__mro__
    assert AutoLearningReloadMixin in AutoLearningManager.__mro__
    assert AutoLearningRetentionMixin in AutoLearningManager.__mro__
    assert callable(normalize_reload_operation)
    assert TombstoneRetentionResult.__module__.startswith(
        "core.features.learning.application"
    )


def test_learning_feature_exports_domain_owner() -> None:
    """feature 包级入口应恒等导出反馈领域模型的唯一实现。"""

    assert learning_feature.FEEDBACK_REASON_CODES is FEEDBACK_REASON_CODES
    assert learning_feature.FeedbackAdapterKind is FeedbackAdapterKind
    assert learning_feature.FeedbackOutcome is FeedbackOutcome
    assert learning_feature.FeedbackSignalAggregate is FeedbackSignalAggregate
    assert learning_feature.FeedbackSignalPolicy is FeedbackSignalPolicy
    assert learning_feature.TrustedFeedbackEvent is TrustedFeedbackEvent
    assert learning_feature.build_trusted_feedback_event is build_trusted_feedback_event


def test_learning_feature_exports_candidate_domain_owner() -> None:
    """feature 包级入口应恒等导出候选归并领域契约。"""

    assert learning_feature.CandidateBinding is CandidateBinding
    assert learning_feature.GlobalLearningCandidate is GlobalLearningCandidate
    assert learning_feature.aggregation_revision_for is aggregation_revision_for
    assert learning_feature.stable_revision is stable_revision


def test_learning_feature_exports_infrastructure_owner() -> None:
    """feature 包级入口应恒等导出状态、反馈与配置基础设施。"""

    assert learning_feature.STATE_SCHEMA_VERSION == STATE_SCHEMA_VERSION
    assert learning_feature.AutoLearningStateError is AutoLearningStateError
    assert learning_feature.AutoLearningStateLoadResult is AutoLearningStateLoadResult
    assert (
        learning_feature.AutoLearningStatePersistenceError
        is AutoLearningStatePersistenceError
    )
    assert learning_feature.AutoLearningStateStore is AutoLearningStateStore
    assert (
        learning_feature.AutoLearningStateValidationError
        is AutoLearningStateValidationError
    )
    assert learning_feature.FeedbackSignalStore is FeedbackSignalStore
    assert learning_feature.LearningConfigAdapter is LearningConfigAdapter
    assert learning_feature.LearningConfigApplyResult is LearningConfigApplyResult
    assert learning_feature.LearningConfigSnapshot is LearningConfigSnapshot


def test_learning_feature_exports_evidence_infrastructure_owner() -> None:
    """feature 包级入口应恒等导出私有证据 inbox 与读取 Provider。"""

    assert (
        learning_feature.FeedbackLearningEvidenceInbox is FeedbackLearningEvidenceInbox
    )
    assert (
        learning_feature.FeedbackLearningEvidenceProvider
        is FeedbackLearningEvidenceProvider
    )
    assert learning_feature.LearningEvidenceInboxError is LearningEvidenceInboxError


def test_learning_ports_accept_existing_implementations_structurally(tmp_path) -> None:
    """learning 端口应接收现有 Store、服务和受控适配器实现。"""

    store = FeedbackSignalStore(":memory:")
    manager = FeedbackSignalManager(store)
    evidence_provider = FeedbackLearningEvidenceProvider(
        FeedbackLearningEvidenceInbox(tmp_path),
        aggregation_revision_provider=lambda _items: "a" * 64,
        source_config_revision_provider=lambda: "b" * 64,
        quality_gate_version="quality-gate-v1",
    )
    try:
        assert isinstance(store, FeedbackSignalStorePort)
        assert isinstance(manager, FeedbackSignalServicePort)
        assert isinstance(LearningConfigAdapter(object()), LearningConfigAdapterPort)
        assert isinstance(evidence_provider, LearningEvidenceProviderPort)
    finally:
        store.close()
