"""测试 core.monitoring 包级懒加载门面。"""

from __future__ import annotations

import importlib
from importlib.util import find_spec


def _reload_monitoring_package():
    """重新加载监控包，隔离运行时 debug 开关状态。"""

    import core.features.observability.application.runtime as runtime
    import core.monitoring as monitoring

    importlib.reload(runtime)
    return importlib.reload(monitoring)


def test_legacy_manager_metrics_collector_is_absent() -> None:
    """监控指标只能由 core.monitoring 提供，不保留第二套 Manager。"""

    assert find_spec("core.managers.metrics_collector") is None


def test_set_debug_mode_toggles_functions_decorated_before_enable(monkeypatch) -> None:
    """启动前绑定的装饰器也必须在运行时开关后输出函数级诊断。"""
    monitoring = _reload_monitoring_package()
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "core.features.observability.infrastructure.debug_reporter.report_debug_event",
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
