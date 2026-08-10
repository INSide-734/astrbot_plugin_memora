"""检索评测报告存储的旧路径兼容导出。"""

from ..features.evaluation.infrastructure import report_store as _feature_report_store

EvaluationReportStore = _feature_report_store.EvaluationReportStore

__all__ = ["EvaluationReportStore"]
