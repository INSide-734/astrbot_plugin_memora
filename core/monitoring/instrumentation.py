"""带可选追踪日志的零额外开销 ``@monitored`` 装饰器。"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import re
import time
from typing import Any, Callable, TypeVar

from astrbot.api import logger

from ..features.observability.infrastructure.metrics import REGISTRY, Counter, Histogram

# ---------------------------------------------------------------------------
# 全局开关
# ---------------------------------------------------------------------------

_debug_mode: bool = False
_trace_enabled: bool = False

# 单条消息级追踪上下文，由 ``reset_trace_context()`` 清理
_trace_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "monitored_trace_depth", default=0
)
_trace_call_tree: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "monitored_trace_call_tree", default=[]
)


def set_debug_mode(enabled: bool) -> None:
    """全局启用或禁用调试级监控。"""
    global _debug_mode
    _debug_mode = enabled


def set_trace_enabled(enabled: bool) -> None:
    """启用或禁用调用树追踪日志。"""
    global _trace_enabled
    _trace_enabled = enabled


def is_debug_mode() -> bool:
    """返回当前调试模式状态。"""
    return _debug_mode


def is_trace_enabled() -> bool:
    """返回当前追踪模式状态。"""
    return _trace_enabled


def reset_trace_context() -> None:
    """重置单条消息的追踪状态（深度计数与调用树）。"""
    _trace_depth.set(0)
    _trace_call_tree.set([])


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

_F = TypeVar("_F", bound=Callable[..., Any])

# 惰性创建的指标容器（确保导入时即可获得占位实现）
_histogram_cache: dict[str, Histogram] = {}
_counter_cache: dict[str, Counter] = {}
_error_counter_cache: dict[str, Counter] = {}


def _get_or_create_histogram(
    name: str, description: str, labelnames: list[str]
) -> Histogram:
    """返回缓存的 Histogram；首次访问时自动创建。"""
    if name not in _histogram_cache:
        _histogram_cache[name] = Histogram(
            name,
            description,
            labelnames=labelnames,
            registry=REGISTRY,
        )
    return _histogram_cache[name]


def _get_or_create_counter(name: str, description: str) -> Counter:
    """返回缓存的 Counter；首次访问时自动创建。"""
    if name not in _counter_cache:
        _counter_cache[name] = Counter(name, description, registry=REGISTRY)
    return _counter_cache[name]


def _get_or_create_error_counter(name: str, description: str) -> Counter:
    """返回缓存的错误计数器；首次访问时自动创建。"""
    if name not in _error_counter_cache:
        _error_counter_cache[name] = Counter(name, description, registry=REGISTRY)
    return _error_counter_cache[name]


def _sanitize_fqn(fqn: str) -> str:
    """将点号替换为下划线，使全限定名适合作为 Prometheus 标签值。"""
    sanitized = re.sub(r"[^A-Za-z0-9_.:+-]", "_", fqn.replace(".", "_"))
    return sanitized[:128]


def _report_instrumented_call(
    *,
    function: str,
    status: str,
    duration_ms: float,
    call_depth: int,
    exception_type: str | None = None,
) -> None:
    """记录不含参数和返回值的函数级安全耗时摘要。"""
    try:
        from .debug_reporter import report_debug_event

        fields: dict[str, Any] = {
            "component": "instrumentation",
            "stage": "call",
            "status": status,
            "reason_code": {
                "completed": "call_completed",
                "failed": "call_failed",
                "cancelled": "call_cancelled",
            }.get(status, "call_failed"),
            "function": function,
            "duration_ms": max(0.0, float(duration_ms)),
            "call_depth": max(0, int(call_depth)),
        }
        if exception_type:
            fields["exception_type"] = exception_type
        report_debug_event("instrumented_call", **fields)
    except Exception:
        # 诊断记录器不得改变业务函数的成功、失败或取消语义。
        pass


# ---------------------------------------------------------------------------
# 核心装饰器
# ---------------------------------------------------------------------------


def monitored(func: _F) -> _F:
    """为函数或协程添加可选的监控埋点。"""
    fqn: str = f"{func.__module__}.{func.__qualname__}"
    safe_fqn: str = _sanitize_fqn(fqn)

    # 预缓存指标对象，避免热点路径重复字典查找
    latency_hist = _get_or_create_histogram(
        "memora_instrumented_latency_seconds",
        "Per-function call latency in seconds.",
        labelnames=["function"],
    )
    call_counter = _get_or_create_counter(
        "memora_instrumented_calls_total",
        "Total number of instrumented function calls.",
    )
    error_counter = _get_or_create_error_counter(
        "memora_instrumented_errors_total",
        "Total number of exceptions raised by instrumented functions.",
    )

    is_coro = asyncio.iscoroutinefunction(func)

    # ------------------------------------------------------------------
    # 同步包装器
    # ------------------------------------------------------------------
    if not is_coro:

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            depth = _trace_depth.get()
            start = time.perf_counter()
            if _trace_enabled:
                indent = "  " * depth
                logger.debug(f"{indent}>>> {fqn}")
                _trace_depth.set(depth + 1)

            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start
                call_counter.inc()
                latency_hist.labels(function=safe_fqn).observe(elapsed)
                if _trace_enabled:
                    _trace_depth.set(depth)
                    logger.debug(f"{indent}<<< {fqn} ({elapsed * 1000:.2f} ms)")
                _report_instrumented_call(
                    function=safe_fqn,
                    status="completed",
                    duration_ms=elapsed * 1000.0,
                    call_depth=depth,
                )
                return result
            except Exception as exception:
                elapsed = time.perf_counter() - start
                error_counter.inc()
                call_counter.inc()
                latency_hist.labels(function=safe_fqn).observe(elapsed)
                if _trace_enabled:
                    _trace_depth.set(depth)
                    logger.debug(f"{indent}<<< {fqn} ERROR ({elapsed * 1000:.2f} ms)")
                _report_instrumented_call(
                    function=safe_fqn,
                    status="failed",
                    duration_ms=elapsed * 1000.0,
                    call_depth=depth,
                    exception_type=exception.__class__.__name__,
                )
                raise

        return sync_wrapper  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # 异步包装器
    # ------------------------------------------------------------------
    @functools.wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        depth = _trace_depth.get()
        start = time.perf_counter()
        if _trace_enabled:
            indent = "  " * depth
            logger.debug(f"{indent}>>> {fqn}")
            _trace_depth.set(depth + 1)

        try:
            result = await func(*args, **kwargs)
            elapsed = time.perf_counter() - start
            call_counter.inc()
            latency_hist.labels(function=safe_fqn).observe(elapsed)
            if _trace_enabled:
                _trace_depth.set(depth)
                logger.debug(f"{indent}<<< {fqn} ({elapsed * 1000:.2f} ms)")
            _report_instrumented_call(
                function=safe_fqn,
                status="completed",
                duration_ms=elapsed * 1000.0,
                call_depth=depth,
            )
            return result
        except asyncio.CancelledError:
            elapsed = time.perf_counter() - start
            if _trace_enabled:
                _trace_depth.set(depth)
            _report_instrumented_call(
                function=safe_fqn,
                status="cancelled",
                duration_ms=elapsed * 1000.0,
                call_depth=depth,
            )
            raise
        except Exception as exception:
            elapsed = time.perf_counter() - start
            error_counter.inc()
            call_counter.inc()
            latency_hist.labels(function=safe_fqn).observe(elapsed)
            if _trace_enabled:
                _trace_depth.set(depth)
                logger.debug(f"{indent}<<< {fqn} ERROR ({elapsed * 1000:.2f} ms)")
            _report_instrumented_call(
                function=safe_fqn,
                status="failed",
                duration_ms=elapsed * 1000.0,
                call_depth=depth,
                exception_type=exception.__class__.__name__,
            )
            raise

    return async_wrapper  # type: ignore[return-value]
