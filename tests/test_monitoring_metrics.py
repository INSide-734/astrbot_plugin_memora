"""测试 monitoring.metrics real and stub-backed behaviors."""

from __future__ import annotations

import builtins
import importlib
import sys
from types import ModuleType


def test_metrics_registry_and_well_known_collectors_exist() -> None:
    import core.monitoring.metrics as metrics

    assert metrics.REGISTRY is not None
    assert metrics.RECALL_DURATION is not None
    assert metrics.RECALL_REQUESTS is not None
    assert metrics.CACHE_HITS is not None
    assert metrics.CACHE_MISSES is not None
    assert metrics.MEMORY_WRITE_DURATION is not None
    assert metrics.MEMORY_ATOMS_TOTAL is not None
    assert metrics.MEMORY_WRITE_FAILURES_TOTAL is not None
    assert metrics.WRITE_OPERATIONS_TOTAL is not None
    assert metrics.WRITE_LOCK_RETRIES_TOTAL is not None
    assert metrics.WRITE_FAILURES_TOTAL is not None


def test_real_metrics_are_usable_when_prometheus_is_available() -> None:
    import core.monitoring.metrics as metrics

    metrics.RECALL_REQUESTS.inc()
    metrics.CACHE_HITS.inc(2)
    metrics.MEMORY_WRITE_DURATION.observe(0.123)
    metrics.MEMORY_WRITE_FAILURES_TOTAL.labels(stage="document").inc()
    metrics.RECALL_DURATION.labels(stage="hybrid").observe(0.5)
    metrics.WRITE_OPERATIONS_TOTAL.inc()
    metrics.WRITE_LOCK_RETRIES_TOTAL.inc()
    metrics.WRITE_FAILURES_TOTAL.labels(reason="retry_exhausted").inc()

    if metrics.is_prometheus_available():
        names = {metric.name for metric in metrics.REGISTRY.collect()}
        assert "memora_recall_requests" in names
        assert "memora_recall_duration_seconds" in names
        assert "memora_memory_write_failures" in names
        assert "memora_write_operations" in names
        assert "memora_write_lock_retries" in names
        assert "memora_write_failures" in names
    else:
        assert metrics.REGISTRY.collect() == []


def test_stub_metrics_degrade_gracefully_without_prometheus() -> None:
    module_name = "core.monitoring.metrics"
    original = sys.modules.pop(module_name, None)
    real_import = builtins.__import__

    def blocking_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "prometheus_client":
            raise ImportError("blocked for test")
        return real_import(name, globals, locals, fromlist, level)

    try:
        builtins.__import__ = blocking_import
        stubbed = importlib.import_module(module_name)

        assert stubbed.is_prometheus_available() is False
        assert stubbed.REGISTRY.collect() == []

        labeled = stubbed.RECALL_DURATION.labels(stage="graph")
        labeled.observe(0.12)
        labeled.inc()
        stubbed.RECALL_REQUESTS.inc()
        stubbed.MEMORY_WRITE_DURATION.observe(0.34)
        stubbed.MEMORY_WRITE_FAILURES_TOTAL.labels(stage="document").inc()
        stubbed.WRITE_OPERATIONS_TOTAL.inc()
        stubbed.WRITE_LOCK_RETRIES_TOTAL.inc()
        stubbed.WRITE_FAILURES_TOTAL.labels(reason="fatal").inc()
    finally:
        builtins.__import__ = real_import
        sys.modules.pop(module_name, None)
        if isinstance(original, ModuleType):
            sys.modules[module_name] = original
        else:
            importlib.import_module(module_name)
