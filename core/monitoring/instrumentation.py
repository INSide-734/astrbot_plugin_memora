"""向后兼容导出可观测性 feature 的监控插桩对象。"""

from ..features.observability.infrastructure.instrumentation import (
    is_debug_mode,
    is_trace_enabled,
    monitored,
    reset_trace_context,
    set_debug_mode,
    set_trace_enabled,
)

__all__ = [
    "is_debug_mode",
    "is_trace_enabled",
    "monitored",
    "reset_trace_context",
    "set_debug_mode",
    "set_trace_enabled",
]
