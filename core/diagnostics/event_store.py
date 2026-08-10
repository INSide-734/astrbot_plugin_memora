"""诊断事件存储的旧路径兼容导出。"""

from ..features.diagnostics.infrastructure import event_store as _feature_event_store

DiagnosticEventStore = _feature_event_store.DiagnosticEventStore

__all__ = ["DiagnosticEventStore"]
