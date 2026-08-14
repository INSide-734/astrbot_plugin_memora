"""可观测性 feature 运行时门面契约。"""

from __future__ import annotations

import importlib


def _reload_observability_runtime():
    """重新加载可观测运行时，隔离 debug 开关状态。"""

    import core.features.observability.application.runtime as runtime

    return importlib.reload(runtime)


def test_set_debug_mode_toggles_functions_decorated_before_enable(
    monkeypatch,
) -> None:
    """启动前绑定的装饰器也必须在运行时开关后输出函数级诊断。"""

    runtime = _reload_observability_runtime()
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "core.features.observability.infrastructure.debug_reporter.report_debug_event",
        lambda event_name, **fields: events.append((event_name, fields)),
    )

    @runtime.monitored
    def measured() -> str:
        """返回固定结果以验证运行时装饰器切换。"""

        return "ok"

    assert measured() == "ok"
    assert events == []

    runtime.set_debug_mode(True)
    assert measured() == "ok"
    assert events[-1][0] == "instrumented_call"
    function_name = events[-1][1]["function"]
    assert isinstance(function_name, str)
    assert function_name.endswith("measured")

    runtime.set_debug_mode(False)
    event_count = len(events)
    assert measured() == "ok"
    assert len(events) == event_count
