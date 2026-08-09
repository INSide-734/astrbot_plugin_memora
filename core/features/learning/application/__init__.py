"""自主学习 feature 的应用服务。"""

from .auto_learning_persistence import AutoLearningPersistenceMixin
from .auto_learning_reload import (
    AutoLearningReloadMixin,
    normalize_reload_operation,
)
from .auto_learning_retention import (
    AutoLearningRetentionMixin,
    TombstoneRetentionResult,
)
from .feedback_signal_manager import (
    FeedbackIngestResult,
    FeedbackRevokeResult,
    FeedbackSignalManager,
    record_explicit_correction,
    revoke_explicit_correction,
)

__all__ = [
    "AutoLearningPersistenceMixin",
    "AutoLearningReloadMixin",
    "AutoLearningRetentionMixin",
    "FeedbackIngestResult",
    "FeedbackRevokeResult",
    "FeedbackSignalManager",
    "TombstoneRetentionResult",
    "normalize_reload_operation",
    "record_explicit_correction",
    "revoke_explicit_correction",
]
