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


def test_set_debug_mode_toggles_functions_decorated_before_enable(monkeypatch) -> None:
    """启动前绑定的装饰器也必须在运行时开关后输出函数级诊断。"""
    monitoring = _reload_monitoring_package()
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "core.monitoring.debug_reporter.report_debug_event",
        lambda event_name, **fields: events.append((event_name, fields)),
    )

    @monitoring.monitored
    def measured() -> str:
        return "ok"

    assert measured() == "ok"
    assert events == []

    monitoring.set_debug_mode(True)
    assert measured() == "ok"
    assert events[-1][0] == "instrumented_call"
    assert events[-1][1]["function"].endswith("measured")

    monitoring.set_debug_mode(False)
    event_count = len(events)
    assert measured() == "ok"
    assert len(events) == event_count


def test_unknown_package_attribute_raises_attribute_error() -> None:
    monitoring = _reload_monitoring_package()

    with pytest.raises(AttributeError, match="does_not_exist"):
        _ = monitoring.does_not_exist
