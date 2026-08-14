"""自主学习 feature 的隔离持久化基础设施。"""

from .auto_learning_state import (
    STATE_SCHEMA_VERSION,
    AutoLearningStateError,
    AutoLearningStateLoadResult,
    AutoLearningStatePersistenceError,
    AutoLearningStateStore,
    AutoLearningStateValidationError,
)
from .feedback_learning_evidence_store import (
    FeedbackLearningEvidenceInbox,
    FeedbackLearningEvidenceProvider,
    LearningEvidenceInboxError,
)
from .feedback_signal_store import FeedbackSignalStore
from .learning_config_adapter import (
    LearningConfigAdapter,
    LearningConfigApplyResult,
    LearningConfigSnapshot,
)

__all__ = [
    "STATE_SCHEMA_VERSION",
    "AutoLearningStateError",
    "AutoLearningStateLoadResult",
    "AutoLearningStatePersistenceError",
    "AutoLearningStateStore",
    "AutoLearningStateValidationError",
    "FeedbackLearningEvidenceInbox",
    "FeedbackLearningEvidenceProvider",
    "FeedbackSignalStore",
    "LearningConfigAdapter",
    "LearningConfigApplyResult",
    "LearningConfigSnapshot",
    "LearningEvidenceInboxError",
]
