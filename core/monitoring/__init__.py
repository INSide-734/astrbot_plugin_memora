"""监控可观测性包 — Prometheus 指标、插桩装饰器、性能追踪与质量评分。

提供：
- metrics.py: 带优雅降级 stub 的 Prometheus CollectorRegistry
- instrumentation.py: @monitored 装饰器（禁用时仅保留轻量布尔门控）
- perf_tracker.py: 环形缓冲区召回管线时序追踪器
- quality_scorer.py: 5 维度记忆原子质量评分 + 4 级告警系统

所有重依赖均为懒加载 — 除非显式请求调试模式或可选模块，
导入此包的耗时小于 1ms。
"""

from __future__ import annotations

import asyncio
import functools
from pathlib import Path
from typing import Any, Callable, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# 轻量级运行时门控：禁用时直接调用原函数，启用后懒加载真实插桩
# ---------------------------------------------------------------------------


_runtime_debug_enabled = False


def monitored(func: _F) -> _F:
    """返回一个可在运行时切换真实插桩的轻量包装器。"""
    instrumented_func: _F | None = None

    def resolve_instrumented() -> _F:
        nonlocal instrumented_func
        if instrumented_func is None:
            from .instrumentation import monitored as instrument

            instrumented_func = instrument(func)
        return instrumented_func

    if asyncio.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _runtime_debug_enabled:
                return await func(*args, **kwargs)
            return await resolve_instrumented()(*args, **kwargs)

        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        if not _runtime_debug_enabled:
            return func(*args, **kwargs)
        return resolve_instrumented()(*args, **kwargs)

    return sync_wrapper  # type: ignore[return-value]


def reset_trace_context() -> None:
    """启用监控时清理真实追踪上下文，禁用时保持 no-op。"""
    if not _runtime_debug_enabled:
        return
    from .instrumentation import reset_trace_context as reset

    reset()


def set_debug_mode(enabled: bool, *, data_dir: str | Path | None = None) -> None:
    """全局启用调试级监控。

    首次启用时，会触发真实 ``instrumentation`` 模块的懒加载
    （包括 prometheus_client 指标）。
    禁用时，``@monitored`` 只执行一次轻量布尔判断。
    """
    global _runtime_debug_enabled

    from .debug_reporter import configure_debug_reporting

    # 问题报告记录器与 Prometheus 插桩共用同一开关，但保持实现独立。
    configure_debug_reporting(bool(enabled), data_dir)
    _runtime_debug_enabled = bool(enabled)

    if enabled:
        from .instrumentation import set_debug_mode as _set_debug

        _set_debug(True)  # 立即生效
        return

    try:
        from .instrumentation import set_debug_mode as _set_debug
    except Exception:
        _set_debug = None

    if _set_debug is not None:
        _set_debug(False)

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
    "close_debug_reporting",
    "configure_debug_reporting",
    "debug_operation",
    "is_debug_reporting_enabled",
    "report_debug_event",
    "report_debug_exception",
    "set_debug_mode",
]

_lazy: dict[str, Any] = {}


def __getattr__(name: str) -> Any:
    """首次访问时懒加载重依赖可选模块。"""
    global _lazy

    if name in _lazy:
        return _lazy[name]

    if name in {
        "close_debug_reporting",
        "configure_debug_reporting",
        "debug_operation",
        "is_debug_reporting_enabled",
        "report_debug_event",
        "report_debug_exception",
    }:
        from . import debug_reporter as _reporter

        _lazy.update(
            {
                "close_debug_reporting": _reporter.close_debug_reporting,
                "configure_debug_reporting": _reporter.configure_debug_reporting,
                "debug_operation": _reporter.debug_operation,
                "is_debug_reporting_enabled": _reporter.is_debug_reporting_enabled,
                "report_debug_event": _reporter.report_debug_event,
                "report_debug_exception": _reporter.report_debug_exception,
            }
        )
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
