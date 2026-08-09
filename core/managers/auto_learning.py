"""自主学习应用服务的兼容导出。"""

from ..features.learning.application.auto_learning import (
    AutoLearningManager,
    AutoLearningStatePersistenceError,
)

__all__ = [
    "AutoLearningManager",
    "AutoLearningStatePersistenceError",
]
