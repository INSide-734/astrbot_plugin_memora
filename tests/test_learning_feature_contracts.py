"""learning feature 的领域模型所有权与旧路径兼容契约。"""

from core.features.learning.domain.models import (
    FEEDBACK_REASON_CODES,
    FeedbackAdapterKind,
    FeedbackOutcome,
    FeedbackSignalAggregate,
    FeedbackSignalPolicy,
    TrustedFeedbackEvent,
    build_trusted_feedback_event,
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


def test_legacy_feedback_model_imports_reuse_learning_types() -> None:
    """旧反馈模型路径只能导出 learning feature 的唯一实现。"""

    assert LEGACY_FEEDBACK_REASON_CODES is FEEDBACK_REASON_CODES
    assert LegacyFeedbackAdapterKind is FeedbackAdapterKind
    assert LegacyFeedbackOutcome is FeedbackOutcome
    assert LegacyFeedbackSignalAggregate is FeedbackSignalAggregate
    assert LegacyFeedbackSignalPolicy is FeedbackSignalPolicy
    assert LegacyTrustedFeedbackEvent is TrustedFeedbackEvent
    assert legacy_build_trusted_feedback_event is build_trusted_feedback_event
