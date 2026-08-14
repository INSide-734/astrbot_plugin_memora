"""验证调试事件时间戳与 AstrBot 时区配置一致。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.features.observability.application import runtime as observability_runtime
from core.features.observability.infrastructure import debug_reporter


@pytest.fixture(autouse=True)
def _reset_debug_reporter() -> Iterator[None]:
    """在每个用例前后重置模块级调试记录器。"""
    debug_reporter.close_debug_reporting()
    yield
    debug_reporter.close_debug_reporting()


def test_debug_timestamp_uses_configured_astrbot_timezone(tmp_path: Path) -> None:
    """调试事件应使用 AstrBot 配置时区并保留 UTC 偏移。"""
    debug_reporter.configure_debug_reporting(
        True,
        tmp_path,
        timezone_name="Asia/Shanghai",
    )

    debug_reporter.report_debug_event(
        "recall_completed",
        component="recall",
        stage="recall",
        status="completed",
    )

    path = tmp_path / "diagnostics" / "memora-debug.jsonl"
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    timestamp = datetime.fromisoformat(record["timestamp"])

    assert timestamp.utcoffset() == timedelta(hours=8)
    assert record["timestamp"].endswith("+08:00")


def test_observability_runtime_forwards_astrbot_timezone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """可观测性运行时应把 AstrBot 时区原样传给调试记录器。"""
    calls: list[tuple[bool, str | Path | None, str | None]] = []

    def capture_configuration(
        enabled: bool,
        data_dir: str | Path | None,
        *,
        timezone_name: str | None,
    ) -> None:
        """捕获门面传给调试记录器的配置。"""
        calls.append((enabled, data_dir, timezone_name))

    monkeypatch.setattr(
        debug_reporter,
        "configure_debug_reporting",
        capture_configuration,
    )

    observability_runtime.set_debug_mode(
        False,
        data_dir=tmp_path,
        timezone_name="Asia/Shanghai",
    )

    assert calls == [(False, tmp_path, "Asia/Shanghai")]
