"""监控可观测性包 — Prometheus 指标、插桩装饰器、性能追踪与质量评分。

提供：
- metrics.py: 带优雅降级 stub 的 Prometheus CollectorRegistry
- instrumentation.py: @monitored 装饰器（禁用时零开销）
- perf_tracker.py: 环形缓冲区召回管线时序追踪器
- quality_scorer.py: 5 维度记忆原子质量评分 + 4 级告警系统

所有重依赖均为懒加载 — 除非显式请求调试模式或可选模块，
导入此包的耗时小于 1ms。
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# 轻量级 stub：monitored（零开销，无需额外导入）
# ---------------------------------------------------------------------------


def monitored(func: _F) -> _F:
    """默认 no-op 装饰器 — 原样返回 func。

    当调用 ``set_debug_mode(True)`` 时，此 stub 会被替换为
    来自 ``.instrumentation`` 的真实插桩装饰器。
    """
    return func


def reset_trace_context() -> None:
    """监控禁用时为 no-op — 调用 ``set_debug_mode(True)`` 后替换。"""
    pass


_STUB_MONITORED = monitored
_STUB_RESET_TRACE_CONTEXT = reset_trace_context


def set_debug_mode(enabled: bool) -> None:
    """全局启用调试级监控。

    首次启用时，会触发真实 ``instrumentation`` 模块的懒加载
    （包括 prometheus_client 指标）。
    禁用时，``@monitored`` 为零开销。
    """
    global monitored, reset_trace_context

    if enabled:
        from .instrumentation import monitored as _monitored
        from .instrumentation import reset_trace_context as _reset_trace
        from .instrumentation import set_debug_mode as _set_debug

        monitored = _monitored
        reset_trace_context = _reset_trace
        _set_debug(True)  # 立即生效
        return

    try:
        from .instrumentation import set_debug_mode as _set_debug
    except Exception:
        _set_debug = None

    if _set_debug is not None:
        _set_debug(False)

    monitored = _STUB_MONITORED
    reset_trace_context = _STUB_RESET_TRACE_CONTEXT


# ---------------------------------------------------------------------------
# 重依赖可选模块的懒加载属性访问
# ---------------------------------------------------------------------------

__all__ = [
    "AlertLevel",
    "MemoryQualityScorer",
    "monitored",
    "PerfTracker",
    "QualityAlert",
    "QualityScore",
    "reset_trace_context",
    "set_debug_mode",
]

_lazy: dict[str, Any] = {}


def __getattr__(name: str) -> Any:
    """首次访问时懒加载重依赖可选模块。"""
    global _lazy

    if name in _lazy:
        return _lazy[name]

    if name == "PerfTracker":
        from .perf_tracker import PerfTracker as _cls

        _lazy["PerfTracker"] = _cls
        return _cls

    if name in ("MemoryQualityScorer", "QualityScore", "QualityAlert", "AlertLevel"):
        from . import quality_scorer as _mod

        for _attr in ("MemoryQualityScorer", "QualityScore", "QualityAlert", "AlertLevel"):
            if hasattr(_mod, _attr):
                _lazy[_attr] = getattr(_mod, _attr)
        return _lazy[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
