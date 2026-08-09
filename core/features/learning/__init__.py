"""自主学习 feature 的公开领域边界。"""

from .contracts import (
    FeedbackSignalServicePort,
    FeedbackSignalStorePort,
    LearningConfigAdapterPort,
    LearningEvidenceProviderPort,
)
from .domain import (
    FEEDBACK_REASON_CODES,
    FeedbackAdapterKind,
    FeedbackOutcome,
    FeedbackSignalAggregate,
    FeedbackSignalPolicy,
    TrustedFeedbackEvent,
    build_trusted_feedback_event,
)
from .infrastructure import FeedbackSignalStore

__all__ = [
    "FEEDBACK_REASON_CODES",
    "FeedbackAdapterKind",
    "FeedbackOutcome",
    "FeedbackSignalAggregate",
    "FeedbackSignalPolicy",
    "FeedbackSignalServicePort",
    "FeedbackSignalStore",
    "FeedbackSignalStorePort",
    "LearningConfigAdapterPort",
    "LearningEvidenceProviderPort",
    "TrustedFeedbackEvent",
    "build_trusted_feedback_event",
]
