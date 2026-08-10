"""evaluation feature 与旧路径的唯一实现契约。"""

from core.evaluation import dataset_repository as legacy_dataset_repository
from core.evaluation import (
    feedback_learning_pipeline as legacy_feedback_learning_pipeline,
)
from core.evaluation import (
    feedback_ranking_ablation as legacy_feedback_ranking_ablation,
)
from core.evaluation import metric_provenance as legacy_metric_provenance
from core.evaluation import report_store as legacy_report_store
from core.evaluation import retrieval_quality as legacy_retrieval_quality
from core.features.evaluation.application import (
    feedback_learning_pipeline as feature_feedback_learning_pipeline,
)
from core.features.evaluation.application import (
    feedback_ranking_ablation as feature_feedback_ranking_ablation,
)
from core.features.evaluation.application import (
    retrieval_quality as feature_retrieval_quality,
)
from core.features.evaluation.domain import (
    metric_provenance as feature_metric_provenance,
)
from core.features.evaluation.infrastructure import (
    dataset_repository as feature_dataset_repository,
)
from core.features.evaluation.infrastructure import report_store as feature_report_store


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


def test_legacy_report_store_reuses_feature_implementation() -> None:
    """旧 evaluation 路径只能导出 feature infrastructure 的报告存储实现。"""

    assert legacy_report_store.__all__ == feature_report_store.__all__
    assert (
        legacy_report_store.EvaluationReportStore
        is feature_report_store.EvaluationReportStore
    )


def test_legacy_metric_provenance_reuses_feature_domain() -> None:
    """旧 evaluation 路径只能导出 feature domain 的指标来源实现。"""

    assert legacy_metric_provenance.__all__ == feature_metric_provenance.__all__
    for name in feature_metric_provenance.__all__:
        assert getattr(legacy_metric_provenance, name) is getattr(
            feature_metric_provenance,
            name,
        )


def test_legacy_retrieval_quality_reuses_feature_application() -> None:
    """旧 evaluation 路径只能导出 feature application 的评测实现。"""

    assert legacy_retrieval_quality.__all__ == feature_retrieval_quality.__all__
    for name in feature_retrieval_quality.__all__:
        assert getattr(legacy_retrieval_quality, name) is getattr(
            feature_retrieval_quality,
            name,
        )


def test_legacy_feedback_ranking_reuses_feature_application() -> None:
    """旧 evaluation 路径只能导出 feature application 的反馈排序实现。"""

    assert (
        legacy_feedback_ranking_ablation.__all__
        == feature_feedback_ranking_ablation.__all__
    )
    for name in feature_feedback_ranking_ablation.__all__:
        assert getattr(legacy_feedback_ranking_ablation, name) is getattr(
            feature_feedback_ranking_ablation,
            name,
        )


def test_legacy_feedback_learning_pipeline_reuses_feature_application() -> None:
    """旧 evaluation 路径只能导出 feature application 的反馈投递编排。"""

    assert (
        legacy_feedback_learning_pipeline.__all__
        == feature_feedback_learning_pipeline.__all__
    )
    assert (
        legacy_feedback_learning_pipeline.run_feedback_ranking_evaluation_and_publish_evidence
        is feature_feedback_learning_pipeline.run_feedback_ranking_evaluation_and_publish_evidence
    )
