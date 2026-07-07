"""测试 monitoring.instrumentation behavior."""

from __future__ import annotations

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
    assert inst._sanitize_fqn("core.mod.Class.method") == "core_mod_Class_method"


def test_sync_monitored_records_metrics_when_debug_and_trace_disabled(monkeypatch) -> None:
    histogram = _FakeHistogram()
    counter = _FakeCounter()
    error_counter = _FakeCounter()
    monkeypatch.setattr(inst, "_get_or_create_histogram", lambda *args, **kwargs: histogram)
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
    from core.monitoring import metrics

    counter = inst._get_or_create_counter(
        "memora_test_instrumented_registry_total",
        "Test counter registered on plugin registry.",
    )
    counter.inc()

    if metrics.is_prometheus_available():
        names = {metric.name for metric in metrics.REGISTRY.collect()}
        assert "memora_test_instrumented_registry" in names


def test_sync_monitored_records_call_and_latency_when_debug_enabled(monkeypatch) -> None:
    histogram = _FakeHistogram()
    counter = _FakeCounter()
    error_counter = _FakeCounter()
    monkeypatch.setattr(inst, "_get_or_create_histogram", lambda *args, **kwargs: histogram)
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


def test_sync_monitored_records_errors_and_restores_trace_depth(monkeypatch) -> None:
    histogram = _FakeHistogram()
    counter = _FakeCounter()
    error_counter = _FakeCounter()
    debug_log = MagicMock()
    monkeypatch.setattr(inst, "_get_or_create_histogram", lambda *args, **kwargs: histogram)
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
    histogram = _FakeHistogram()
    counter = _FakeCounter()
    error_counter = _FakeCounter()
    monkeypatch.setattr(inst, "_get_or_create_histogram", lambda *args, **kwargs: histogram)
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
    histogram = _FakeHistogram()
    counter = _FakeCounter()
    error_counter = _FakeCounter()
    debug_log = MagicMock()
    monkeypatch.setattr(inst, "_get_or_create_histogram", lambda *args, **kwargs: histogram)
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
    histogram = _FakeHistogram()
    counter = _FakeCounter()
    error_counter = _FakeCounter()
    debug_log = MagicMock()
    monkeypatch.setattr(inst, "_get_or_create_histogram", lambda *args, **kwargs: histogram)
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


def test_trace_mode_logs_nested_call_hierarchy_and_resets_depth(monkeypatch) -> None:
    histogram = _FakeHistogram()
    counter = _FakeCounter()
    error_counter = _FakeCounter()
    debug_log = MagicMock()
    monkeypatch.setattr(inst, "_get_or_create_histogram", lambda *args, **kwargs: histogram)
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
