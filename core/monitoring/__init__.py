"""向后兼容导出可观测性 feature 的运行时门面。"""

from ..features.observability.application import runtime as _runtime

__all__ = _runtime.__all__
__getattr__ = _runtime.__getattr__
monitored = _runtime.monitored
reset_trace_context = _runtime.reset_trace_context
set_debug_mode = _runtime.set_debug_mode
