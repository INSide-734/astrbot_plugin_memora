"""检索评测服务的旧路径兼容导出。"""

from ..features.evaluation.infrastructure import (
    evaluation_service as _feature_evaluation_service,
)

EvaluationService = _feature_evaluation_service.EvaluationService

__all__ = ["EvaluationService"]
