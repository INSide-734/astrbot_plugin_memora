"""自主学习 feature 的应用服务。"""

from .feedback_signal_manager import (
    FeedbackIngestResult,
    FeedbackRevokeResult,
    FeedbackSignalManager,
    record_explicit_correction,
    revoke_explicit_correction,
)

__all__ = [
    "FeedbackIngestResult",
    "FeedbackRevokeResult",
    "FeedbackSignalManager",
    "record_explicit_correction",
    "revoke_explicit_correction",
]
