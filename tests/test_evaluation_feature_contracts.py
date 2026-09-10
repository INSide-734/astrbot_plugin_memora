"""evaluation feature 的唯一所有权契约。"""

from core.features.evaluation.application.feedback_learning_pipeline import (
    run_feedback_ranking_evaluation_and_publish_evidence,
)


def test_feedback_learning_pipeline_is_owned_by_evaluation_feature() -> None:
    """反馈评测投递编排应由 evaluation feature application 唯一拥有。"""

    assert run_feedback_ranking_evaluation_and_publish_evidence.__module__ == (
        "core.features.evaluation.application.feedback_learning_pipeline"
    )
