"""生产评测数据集仓库的旧路径兼容导出。"""

from ..features.evaluation.infrastructure import (
    dataset_repository as _feature_dataset_repository,
)

EvaluationDatasetRepository = _feature_dataset_repository.EvaluationDatasetRepository
EvaluationDatasetValidationError = (
    _feature_dataset_repository.EvaluationDatasetValidationError
)
PreparedEvaluationDataset = _feature_dataset_repository.PreparedEvaluationDataset

__all__ = [
    "EvaluationDatasetRepository",
    "EvaluationDatasetValidationError",
    "PreparedEvaluationDataset",
]
