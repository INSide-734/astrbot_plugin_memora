"""运行时诊断能力的旧路径兼容导出。"""

from ..features.diagnostics import DiagnosticEventStore
from .health_scorer import HealthScorer

__all__ = ["DiagnosticEventStore", "HealthScorer"]
