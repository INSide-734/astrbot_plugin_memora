"""诊断健康评分器的旧路径兼容导出。"""

from ..features.diagnostics.application import health_scorer as _feature_health_scorer

HealthScorer = _feature_health_scorer.HealthScorer

__all__ = ["HealthScorer"]
