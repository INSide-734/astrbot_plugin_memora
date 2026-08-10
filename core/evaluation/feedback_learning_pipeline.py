"""反馈排序评测投递编排的旧路径兼容导出。"""

from ..features.evaluation.application import (
    feedback_learning_pipeline as _feature_feedback_pipeline,
)

run_feedback_ranking_evaluation_and_publish_evidence = (
    _feature_feedback_pipeline.run_feedback_ranking_evaluation_and_publish_evidence
)

__all__ = ["run_feedback_ranking_evaluation_and_publish_evidence"]
