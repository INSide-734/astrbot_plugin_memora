"""learning feature 的领域模型所有权与旧路径兼容契约。"""

from core.api.learning_config_adapter import LearningConfigAdapter
from core.evaluation import feedback_learning_evidence as legacy_learning_evidence
from core.evaluation import (
    feedback_learning_evidence_contract as legacy_learning_evidence_contract,
)
from core.evaluation.feedback_learning_evidence_store import (
    FeedbackLearningEvidenceInbox,
    FeedbackLearningEvidenceProvider,
)
from core.features.learning.application import (
    auto_learning as learning_application,
)
from core.features.learning.application import (
    auto_learning_operations as learning_operations,
)
from core.features.learning.application import (
    auto_learning_persistence as learning_persistence,
)
from core.features.learning.application import auto_learning_reload as learning_reload
from core.features.learning.application import (
    auto_learning_retention as learning_retention,
)
from core.features.learning.application.feedback_signal_manager import (
    FeedbackIngestResult,
    FeedbackRevokeResult,
    FeedbackSignalManager,
    record_explicit_correction,
    revoke_explicit_correction,
)
from core.features.learning.contracts import (
    FeedbackSignalServicePort,
    FeedbackSignalStorePort,
    LearningConfigAdapterPort,
    LearningEvidenceProviderPort,
)
from core.features.learning.domain import auto_learning_actions as learning_actions
from core.features.learning.domain import auto_learning_records as learning_records
from core.features.learning.domain import (
    feedback_learning_evidence as learning_evidence,
)
from core.features.learning.domain import (
    feedback_learning_evidence_contract as learning_evidence_contract,
)
from core.features.learning.domain.models import (
    FEEDBACK_REASON_CODES,
    FeedbackAdapterKind,
    FeedbackOutcome,
    FeedbackSignalAggregate,
    FeedbackSignalPolicy,
    TrustedFeedbackEvent,
    build_trusted_feedback_event,
)
from core.features.learning.infrastructure import FeedbackSignalStore
from core.features.learning.infrastructure import auto_learning_state as learning_state
from core.managers import auto_learning as legacy_learning_application
from core.managers import auto_learning_actions as legacy_learning_actions
from core.managers import auto_learning_operations as legacy_learning_operations
from core.managers import (
    auto_learning_persistence as legacy_learning_persistence,
)
from core.managers import auto_learning_records as legacy_learning_records
from core.managers import auto_learning_reload as legacy_learning_reload
from core.managers import auto_learning_retention as legacy_learning_retention
from core.managers import auto_learning_state as legacy_learning_state
from core.managers.feedback_signal_manager import (
    FeedbackIngestResult as LegacyFeedbackIngestResult,
)
from core.managers.feedback_signal_manager import (
    FeedbackRevokeResult as LegacyFeedbackRevokeResult,
)
from core.managers.feedback_signal_manager import (
    FeedbackSignalManager as LegacyFeedbackSignalManager,
)
from core.managers.feedback_signal_manager import (
    record_explicit_correction as legacy_record_explicit_correction,
)
from core.managers.feedback_signal_manager import (
    revoke_explicit_correction as legacy_revoke_explicit_correction,
)
from core.models.feedback_signal import (
    FEEDBACK_REASON_CODES as LEGACY_FEEDBACK_REASON_CODES,
)
from core.models.feedback_signal import (
    FeedbackAdapterKind as LegacyFeedbackAdapterKind,
)
from core.models.feedback_signal import FeedbackOutcome as LegacyFeedbackOutcome
from core.models.feedback_signal import (
    FeedbackSignalAggregate as LegacyFeedbackSignalAggregate,
)
from core.models.feedback_signal import (
    FeedbackSignalPolicy as LegacyFeedbackSignalPolicy,
)
from core.models.feedback_signal import (
    TrustedFeedbackEvent as LegacyTrustedFeedbackEvent,
)
from core.models.feedback_signal import (
    build_trusted_feedback_event as legacy_build_trusted_feedback_event,
)
from core.storage.feedback_signal_store import FeedbackSignalStore as LegacyStore


def test_legacy_feedback_model_imports_reuse_learning_types() -> None:
    """旧反馈模型路径只能导出 learning feature 的唯一实现。"""

    assert LEGACY_FEEDBACK_REASON_CODES is FEEDBACK_REASON_CODES
    assert LegacyFeedbackAdapterKind is FeedbackAdapterKind
    assert LegacyFeedbackOutcome is FeedbackOutcome
    assert LegacyFeedbackSignalAggregate is FeedbackSignalAggregate
    assert LegacyFeedbackSignalPolicy is FeedbackSignalPolicy
    assert LegacyTrustedFeedbackEvent is TrustedFeedbackEvent
    assert legacy_build_trusted_feedback_event is build_trusted_feedback_event


def test_legacy_feedback_store_import_reuses_learning_implementation() -> None:
    """旧反馈 Store 路径只能导出 learning infrastructure 的唯一实现。"""

    assert LegacyStore is FeedbackSignalStore


def test_legacy_feedback_service_imports_reuse_learning_implementation() -> None:
    """旧反馈服务路径只能导出 learning application 的唯一实现。"""

    assert LegacyFeedbackIngestResult is FeedbackIngestResult
    assert LegacyFeedbackRevokeResult is FeedbackRevokeResult
    assert LegacyFeedbackSignalManager is FeedbackSignalManager
    assert legacy_record_explicit_correction is record_explicit_correction
    assert legacy_revoke_explicit_correction is revoke_explicit_correction


def test_legacy_auto_learning_domain_imports_reuse_learning_implementation() -> None:
    """旧自主学习领域路径只能导出 learning domain 的唯一实现。"""

    assert legacy_learning_actions.__all__ == learning_actions.__all__
    assert legacy_learning_records.__all__ == learning_records.__all__
    for name in learning_actions.__all__:
        assert getattr(legacy_learning_actions, name) is getattr(learning_actions, name)
    for name in learning_records.__all__:
        assert getattr(legacy_learning_records, name) is getattr(learning_records, name)


def test_legacy_learning_evidence_imports_reuse_learning_implementation() -> None:
    """旧评测证据路径只能导出 learning domain 的唯一实现。"""

    for name in legacy_learning_evidence.__all__:
        assert getattr(legacy_learning_evidence, name) is getattr(
            learning_evidence, name
        )
    assert legacy_learning_evidence_contract.__all__ == (
        learning_evidence_contract.__all__
    )
    for name in learning_evidence_contract.__all__:
        assert getattr(legacy_learning_evidence_contract, name) is getattr(
            learning_evidence_contract, name
        )


def test_legacy_auto_learning_state_imports_reuse_learning_implementation() -> None:
    """旧状态路径只能导出 learning infrastructure 的唯一实现。"""

    for name in legacy_learning_state.__all__:
        assert getattr(legacy_learning_state, name) is getattr(learning_state, name)


def test_legacy_auto_learning_application_imports_reuse_feature_implementation() -> (
    None
):
    """旧自主学习应用路径只能导出 learning application 的唯一实现。"""

    module_pairs = (
        (legacy_learning_application, learning_application),
        (legacy_learning_operations, learning_operations),
        (legacy_learning_persistence, learning_persistence),
        (legacy_learning_reload, learning_reload),
        (legacy_learning_retention, learning_retention),
    )
    for legacy_module, feature_module in module_pairs:
        assert legacy_module.__all__ == feature_module.__all__
        for name in feature_module.__all__:
            assert getattr(legacy_module, name) is getattr(feature_module, name)


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
