"""记忆衰减应用操作的旧路径兼容导出。"""

from ..features.decay.application import operations as _feature_operations

DecayOperationsMixin = _feature_operations.DecayOperationsMixin
_normalize_batch_metadata = _feature_operations._normalize_batch_metadata

__all__ = ["DecayOperationsMixin"]
