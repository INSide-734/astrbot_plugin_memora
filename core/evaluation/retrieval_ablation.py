"""检索消融应用实现的旧路径兼容导出。"""

from ..features.evaluation.application import (
    retrieval_ablation as _feature_retrieval_ablation,
)

RETRIEVAL_VARIANT_NAMES = _feature_retrieval_ablation.RETRIEVAL_VARIANT_NAMES
PreparedVariant = _feature_retrieval_ablation.PreparedVariant
RetrievalAblationController = _feature_retrieval_ablation.RetrievalAblationController

__all__ = [
    "RETRIEVAL_VARIANT_NAMES",
    "PreparedVariant",
    "RetrievalAblationController",
]
