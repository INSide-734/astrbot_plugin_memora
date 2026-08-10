"""评测指标来源领域契约的旧路径兼容导出。"""

from ..features.evaluation.domain import metric_provenance as _feature_metric_provenance

RetrievalObservation = _feature_metric_provenance.RetrievalObservation
aggregate_case_metrics = _feature_metric_provenance.aggregate_case_metrics
annotated_latency = _feature_metric_provenance.annotated_latency
build_case_metrics = _feature_metric_provenance.build_case_metrics
optional_nonnegative_number = _feature_metric_provenance.optional_nonnegative_number
reason_code_aggregates = _feature_metric_provenance.reason_code_aggregates
reported_latency = _feature_metric_provenance.reported_latency
split_retrieval_observation = _feature_metric_provenance.split_retrieval_observation

__all__ = [
    "RetrievalObservation",
    "aggregate_case_metrics",
    "annotated_latency",
    "build_case_metrics",
    "optional_nonnegative_number",
    "reason_code_aggregates",
    "reported_latency",
    "split_retrieval_observation",
]
