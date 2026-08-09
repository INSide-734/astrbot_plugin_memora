"""learning feature 的领域模型所有权与旧路径兼容契约。"""

from core.api.learning_config_adapter import LearningConfigAdapter
from core.evaluation.feedback_learning_evidence_store import (
    FeedbackLearningEvidenceInbox,
    FeedbackLearningEvidenceProvider,
)
from core.features.learning.contracts import (
    FeedbackSignalServicePort,
    FeedbackSignalStorePort,
    LearningConfigAdapterPort,
    LearningEvidenceProviderPort,
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
from core.managers.feedback_signal_manager import FeedbackSignalManager
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
