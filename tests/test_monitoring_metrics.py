"""测试可观测性指标在真实与降级环境中的行为。"""

from __future__ import annotations

import builtins
import importlib
import sys
from types import ModuleType


def test_metrics_registry_and_well_known_collectors_exist() -> None:
    """指标 owner 应公开独立注册表与全部常用采集器。"""
    import core.features.observability.infrastructure.metrics as metrics

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
    """真实 Prometheus 可用时指标应支持记录与采集。"""
    import core.features.observability.infrastructure.metrics as metrics

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


_EXPECTED_INJECTION_PAYLOAD_BUCKETS = (
    0.0,
    200.0,
    500.0,
    800.0,
    1200.0,
    2400.0,
    5000.0,
    10000.0,
    12000.0,
)


def _record_injection_payload_metric_config() -> tuple[
    tuple[float, ...], tuple[str, ...]
]:
    """在隔离的假 Prometheus 模块中捕获 payload 指标配置。"""
    module_name = "core.features.observability.infrastructure.metrics"
    original_metrics = sys.modules.pop(module_name, None)
    original_prometheus = sys.modules.get("prometheus_client")

    class StubRegistry:
        """替代 Prometheus 注册表，避免测试注册真实指标。"""

        pass

    class RecordingMetric:
        """记录构造时的 buckets 与 labelnames 参数。"""

        def __init__(self, *args, **kwargs) -> None:
            """保存指标构造参数中与契约相关的两个字段。"""
            self.buckets = tuple(kwargs.get("buckets", ()))
            self.labelnames = tuple(kwargs.get("labelnames", ()))

    fake_prometheus = ModuleType("prometheus_client")
    fake_prometheus.CollectorRegistry = StubRegistry
    fake_prometheus.Counter = RecordingMetric
    fake_prometheus.Gauge = RecordingMetric
    fake_prometheus.Histogram = RecordingMetric
    sys.modules["prometheus_client"] = fake_prometheus

    try:
        captured = importlib.import_module(module_name)
        metric = captured.INJECTION_PAYLOAD_CHARS
        return tuple(float(value) for value in metric.buckets), metric.labelnames
    finally:
        sys.modules.pop(module_name, None)
        if original_prometheus is None:
            sys.modules.pop("prometheus_client", None)
        else:
            sys.modules["prometheus_client"] = original_prometheus
        if isinstance(original_metrics, ModuleType):
            sys.modules[module_name] = original_metrics
        else:
            importlib.import_module(module_name)


def test_injection_payload_chars_uses_character_buckets_without_labels() -> None:
    """payload 字符指标应使用递增字符桶且不声明标签。"""
    bucket_bounds, labelnames = _record_injection_payload_metric_config()

    assert bucket_bounds == _EXPECTED_INJECTION_PAYLOAD_BUCKETS
    assert all(left < right for left, right in zip(bucket_bounds, bucket_bounds[1:]))
    assert bucket_bounds[0] == 0.0
    assert bucket_bounds[-1] == 12000.0
    assert labelnames == ()


def test_stub_metrics_degrade_gracefully_without_prometheus() -> None:
    """缺少 Prometheus 时所有指标操作应稳定降级为空操作。"""
    module_name = "core.features.observability.infrastructure.metrics"
    original = sys.modules.pop(module_name, None)
    real_import = builtins.__import__

    def blocking_import(name, globals=None, locals=None, fromlist=(), level=0):
        """仅阻断 prometheus_client，其他导入继续委托原实现。"""
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
        stubbed.INJECTION_PAYLOAD_CHARS.observe(512)
    finally:
        builtins.__import__ = real_import
        sys.modules.pop(module_name, None)
        if isinstance(original, ModuleType):
            sys.modules[module_name] = original
        else:
            importlib.import_module(module_name)
