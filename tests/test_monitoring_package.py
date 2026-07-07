"""测试 core.monitoring 包级懒加载门面。"""

from __future__ import annotations

import importlib

import pytest


def _reload_monitoring_package():
    import core.monitoring as monitoring

    return importlib.reload(monitoring)


def test_lazy_perf_tracker_export_is_cached() -> None:
    monitoring = _reload_monitoring_package()

    first = monitoring.PerfTracker
    second = monitoring.PerfTracker

    assert first is second
    assert monitoring._lazy["PerfTracker"] is first


def test_lazy_quality_scorer_exports_are_cached_together() -> None:
    monitoring = _reload_monitoring_package()

    scorer_cls = monitoring.MemoryQualityScorer

    assert monitoring._lazy["MemoryQualityScorer"] is scorer_cls
    assert monitoring._lazy["QualityScore"] is monitoring.QualityScore
    assert monitoring._lazy["QualityAlert"] is monitoring.QualityAlert
    assert monitoring._lazy["AlertLevel"] is monitoring.AlertLevel


def test_set_debug_mode_can_enable_and_then_restore_zero_overhead_stub() -> None:
    monitoring = _reload_monitoring_package()
    stub_monitored = monitoring.monitored
    stub_reset_trace_context = monitoring.reset_trace_context

    monitoring.set_debug_mode(True)
    enabled_monitored = monitoring.monitored
    enabled_reset_trace_context = monitoring.reset_trace_context

    monitoring.set_debug_mode(False)

    assert enabled_monitored is not stub_monitored
    assert enabled_reset_trace_context is not stub_reset_trace_context
    assert monitoring.monitored is stub_monitored
    assert monitoring.reset_trace_context is stub_reset_trace_context


def test_unknown_package_attribute_raises_attribute_error() -> None:
    monitoring = _reload_monitoring_package()

    with pytest.raises(AttributeError, match="does_not_exist"):
        _ = monitoring.does_not_exist
