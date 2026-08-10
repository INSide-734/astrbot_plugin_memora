"""evaluation feature 与旧路径的唯一实现契约。"""

from core.evaluation import dataset_repository as legacy_dataset_repository
from core.features.evaluation.infrastructure import (
    dataset_repository as feature_dataset_repository,
)


def test_legacy_dataset_repository_reuses_feature_implementation() -> None:
    """旧 evaluation 路径只能导出 feature infrastructure 的唯一实现。"""

    assert legacy_dataset_repository.__all__ == feature_dataset_repository.__all__
    assert (
        legacy_dataset_repository.EvaluationDatasetRepository
        is feature_dataset_repository.EvaluationDatasetRepository
    )
    assert (
        legacy_dataset_repository.EvaluationDatasetValidationError
        is feature_dataset_repository.EvaluationDatasetValidationError
    )
    assert (
        legacy_dataset_repository.PreparedEvaluationDataset
        is feature_dataset_repository.PreparedEvaluationDataset
    )
