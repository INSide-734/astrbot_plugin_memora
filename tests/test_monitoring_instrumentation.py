"""测试 monitoring.instrumentation 行为。"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import core.monitoring.instrumentation as inst


class _FakeLabeledMetric:
    def __init__(self) -> None:
        self.observations: list[float] = []
        self.increments: list[float] = []

    def observe(self, amount: float) -> None:
        self.observations.append(amount)

    def inc(self, amount: float = 1) -> None:
        self.increments.append(amount)


class _FakeHistogram:
    def __init__(self) -> None:
        self.labels_calls: list[dict[str, str]] = []
        self.children: list[_FakeLabeledMetric] = []

    def labels(self, **kwargs: str) -> _FakeLabeledMetric:
        self.labels_calls.append(kwargs)
        child = _FakeLabeledMetric()
        self.children.append(child)
        return child


class _FakeCounter:
    def __init__(self) -> None:
        self.increments: list[float] = []

    def inc(self, amount: float = 1) -> None:
        self.increments.append(amount)


@pytest.fixture(autouse=True)
def _reset_instrumentation_state():
    inst.set_debug_mode(False)
    inst.set_trace_enabled(False)
    inst.reset_trace_context()
    inst._histogram_cache.clear()
    inst._counter_cache.clear()
    inst._error_counter_cache.clear()
    yield
    inst.set_debug_mode(False)
    inst.set_trace_enabled(False)
    inst.reset_trace_context()
    inst._histogram_cache.clear()
    inst._counter_cache.clear()
    inst._error_counter_cache.clear()


def test_sanitize_fqn_replaces_dots() -> None:
    """函数全名中的点号应替换为下划线。"""
    assert inst._sanitize_fqn("core.mod.Class.method") == "core_mod_Class_method"


def test_sync_monitored_records_metrics_when_debug_and_trace_disabled(
    monkeypatch,
) -> None:
    """关闭调试与追踪时同步装饰器仍应记录基础指标。"""
    histogram = _FakeHistogram()
    counter = _FakeCounter()
    error_counter = _FakeCounter()
    monkeypatch.setattr(
        inst, "_get_or_create_histogram", lambda *args, **kwargs: histogram
    )
    monkeypatch.setattr(inst, "_get_or_create_counter", lambda *args, **kwargs: counter)
    monkeypatch.setattr(
        inst, "_get_or_create_error_counter", lambda *args, **kwargs: error_counter
    )

    calls: list[int] = []

    @inst.monitored
    def add_one(value: int) -> int:
        calls.append(value)
        return value + 1

    assert add_one(4) == 5
    assert calls == [4]
    assert counter.increments == [1]
    assert error_counter.increments == []
    expected_fqn = inst._sanitize_fqn(f"{add_one.__module__}.{add_one.__qualname__}")
    assert histogram.labels_calls == [{"function": expected_fqn}]
    assert len(histogram.children) == 1
    assert len(histogram.children[0].observations) == 1


def test_dynamic_metrics_register_on_plugin_registry() -> None:
    """动态指标应注册到插件独立 Registry。"""
    from core.features.observability.infrastructure import metrics

    counter = inst._get_or_create_counter(
        "memora_test_instrumented_registry_total",
        "Test counter registered on plugin registry.",
    )
    counter.inc()

    if metrics.is_prometheus_available():
        names = {metric.name for metric in metrics.REGISTRY.collect()}
        assert "memora_test_instrumented_registry" in names


def test_sync_monitored_records_call_and_latency_when_debug_enabled(
    monkeypatch,
) -> None:
    """开启调试时同步装饰器应记录调用次数与延迟。"""
    histogram = _FakeHistogram()
    counter = _FakeCounter()
    error_counter = _FakeCounter()
    monkeypatch.setattr(
        inst, "_get_or_create_histogram", lambda *args, **kwargs: histogram
    )
    monkeypatch.setattr(inst, "_get_or_create_counter", lambda *args, **kwargs: counter)
    monkeypatch.setattr(
        inst, "_get_or_create_error_counter", lambda *args, **kwargs: error_counter
    )
    inst.set_debug_mode(True)

    @inst.monitored
    def double(value: int) -> int:
        return value * 2

    assert double(3) == 6
    assert counter.increments == [1]
    assert error_counter.increments == []
    expected_fqn = inst._sanitize_fqn(f"{double.__module__}.{double.__qualname__}")
    assert histogram.labels_calls == [{"function": expected_fqn}]
    assert len(histogram.children) == 1
    assert len(histogram.children[0].observations) == 1
    assert histogram.children[0].observations[0] >= 0.0


def test_monitored_reports_safe_function_timing_without_arguments(monkeypatch) -> None:
    """函数级诊断只记录安全函数名、状态和耗时，不记录调用参数。"""
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "core.monitoring.debug_reporter.report_debug_event",
        lambda event_name, **fields: events.append((event_name, fields)),
    )

    @inst.monitored
    def inspect_value(value: str) -> str:
        return value

    sentinel = "PRIVATE_FUNCTION_ARGUMENT_SENTINEL"
    assert inspect_value(sentinel) == sentinel

    event_name, fields = events[-1]
    assert event_name == "instrumented_call"
    assert fields["component"] == "instrumentation"
    assert fields["status"] == "completed"
    assert fields["function"].endswith("inspect_value")
    assert isinstance(fields["duration_ms"], float)
    assert isinstance(fields["call_depth"], int)
    assert sentinel not in repr(fields)


def test_sync_monitored_records_errors_and_restores_trace_depth(monkeypatch) -> None:
    """同步调用失败时应记录错误并恢复追踪深度。"""
    histogram = _FakeHistogram()
    counter = _FakeCounter()
    error_counter = _FakeCounter()
    debug_log = MagicMock()
    monkeypatch.setattr(
        inst, "_get_or_create_histogram", lambda *args, **kwargs: histogram
    )
    monkeypatch.setattr(inst, "_get_or_create_counter", lambda *args, **kwargs: counter)
    monkeypatch.setattr(
        inst, "_get_or_create_error_counter", lambda *args, **kwargs: error_counter
    )
    monkeypatch.setattr(inst.logger, "debug", debug_log)
    inst.set_trace_enabled(True)

    @inst.monitored
    def explode() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        explode()

    assert counter.increments == [1]
    assert error_counter.increments == [1]
    assert inst._trace_depth.get() == 0
    assert debug_log.call_count == 2
    assert ">>>" in debug_log.call_args_list[0].args[0]
    assert "ERROR" in debug_log.call_args_list[1].args[0]


@pytest.mark.asyncio
async def test_async_monitored_records_metrics_when_debug_enabled(monkeypatch) -> None:
    """开启调试时异步装饰器应记录基础指标。"""
    histogram = _FakeHistogram()
    counter = _FakeCounter()
    error_counter = _FakeCounter()
    monkeypatch.setattr(
        inst, "_get_or_create_histogram", lambda *args, **kwargs: histogram
    )
    monkeypatch.setattr(inst, "_get_or_create_counter", lambda *args, **kwargs: counter)
    monkeypatch.setattr(
        inst, "_get_or_create_error_counter", lambda *args, **kwargs: error_counter
    )
    inst.set_debug_mode(True)

    @inst.monitored
    async def triple(value: int) -> int:
        return value * 3

    assert await triple(2) == 6
    assert counter.increments == [1]
    assert error_counter.increments == []
    assert len(histogram.children) == 1
    assert len(histogram.children[0].observations) == 1


@pytest.mark.asyncio
async def test_async_trace_mode_logs_nested_call_hierarchy_and_resets_depth(
    monkeypatch,
) -> None:
    """异步追踪应记录嵌套层级并恢复追踪深度。"""
    histogram = _FakeHistogram()
    counter = _FakeCounter()
    error_counter = _FakeCounter()
    debug_log = MagicMock()
    monkeypatch.setattr(
        inst, "_get_or_create_histogram", lambda *args, **kwargs: histogram
    )
    monkeypatch.setattr(inst, "_get_or_create_counter", lambda *args, **kwargs: counter)
    monkeypatch.setattr(
        inst, "_get_or_create_error_counter", lambda *args, **kwargs: error_counter
    )
    monkeypatch.setattr(inst.logger, "debug", debug_log)
    inst.set_trace_enabled(True)

    @inst.monitored
    async def inner() -> str:
        return "ok"

    @inst.monitored
    async def outer() -> str:
        return await inner()

    assert await outer() == "ok"
    assert inst._trace_depth.get() == 0
    log_lines = [call.args[0] for call in debug_log.call_args_list]
    assert any(line.startswith(">>>") for line in log_lines)
    assert any(line.startswith("  >>>") for line in log_lines)
    assert any(line.startswith("  <<<") for line in log_lines)
    assert any(line.startswith("<<<") for line in log_lines)


@pytest.mark.asyncio
async def test_async_monitored_records_nested_errors_and_restores_trace_depth(
    monkeypatch,
) -> None:
    """异步嵌套调用失败时应记录错误并恢复追踪深度。"""
    histogram = _FakeHistogram()
    counter = _FakeCounter()
    error_counter = _FakeCounter()
    debug_log = MagicMock()
    monkeypatch.setattr(
        inst, "_get_or_create_histogram", lambda *args, **kwargs: histogram
    )
    monkeypatch.setattr(inst, "_get_or_create_counter", lambda *args, **kwargs: counter)
    monkeypatch.setattr(
        inst, "_get_or_create_error_counter", lambda *args, **kwargs: error_counter
    )
    monkeypatch.setattr(inst.logger, "debug", debug_log)
    inst.set_trace_enabled(True)

    @inst.monitored
    async def inner() -> None:
        raise RuntimeError("boom")

    @inst.monitored
    async def outer() -> None:
        await inner()

    with pytest.raises(RuntimeError, match="boom"):
        await outer()

    assert counter.increments == [1, 1]
    assert error_counter.increments == [1, 1]
    assert inst._trace_depth.get() == 0
    log_lines = [call.args[0] for call in debug_log.call_args_list]
    assert any(line.startswith(">>>") for line in log_lines)
    assert any(line.startswith("  >>>") for line in log_lines)
    assert any(line.startswith("  <<<") and "ERROR" in line for line in log_lines)
    assert any(line.startswith("<<<") and "ERROR" in line for line in log_lines)


@pytest.mark.asyncio
async def test_async_monitored_reports_cancellation_without_swallowing_it(
    monkeypatch,
) -> None:
    """函数级诊断记录取消状态后必须继续传播取消信号。"""
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "core.monitoring.debug_reporter.report_debug_event",
        lambda event_name, **fields: events.append((event_name, fields)),
    )

    @inst.monitored
    async def cancelled() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await cancelled()

    assert events[-1][0] == "instrumented_call"
    assert events[-1][1]["status"] == "cancelled"
    assert events[-1][1]["reason_code"] == "call_cancelled"


def test_trace_mode_logs_nested_call_hierarchy_and_resets_depth(monkeypatch) -> None:
    """同步追踪应记录嵌套层级并恢复追踪深度。"""
    histogram = _FakeHistogram()
    counter = _FakeCounter()
    error_counter = _FakeCounter()
    debug_log = MagicMock()
    monkeypatch.setattr(
        inst, "_get_or_create_histogram", lambda *args, **kwargs: histogram
    )
    monkeypatch.setattr(inst, "_get_or_create_counter", lambda *args, **kwargs: counter)
    monkeypatch.setattr(
        inst, "_get_or_create_error_counter", lambda *args, **kwargs: error_counter
    )
    monkeypatch.setattr(inst.logger, "debug", debug_log)
    inst.set_trace_enabled(True)

    @inst.monitored
    def inner() -> str:
        return "ok"

    @inst.monitored
    def outer() -> str:
        return inner()

    assert outer() == "ok"
    assert inst._trace_depth.get() == 0
    log_lines = [call.args[0] for call in debug_log.call_args_list]
    assert any(line.startswith(">>>") for line in log_lines)
    assert any(line.startswith("  >>>") for line in log_lines)
    assert any(line.startswith("  <<<") for line in log_lines)
    assert any(line.startswith("<<<") for line in log_lines)
