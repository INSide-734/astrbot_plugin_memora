"""总结任务公开诊断投影的隐私契约。"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.features.reflection.domain.summary_models import (
    sanitize_summary_task_snapshot,
)
from core.platform.transport.commands.diagnostic_commands import DiagnosticCommandMixin
from core.platform.transport.page_api.metrics_api import MetricsApiMixin

_CANARY = "SUMMARY-PRIVACY-CANARY"


def _unsafe_snapshot() -> dict[str, object]:
    """构造含内部标识和正文 canary 的不可信诊断映射。"""
    return {
        "queued": _CANARY,
        "running": 2,
        "session_id": _CANARY,
        "job_id": _CANARY,
        "source_digest": _CANARY,
        "canonical_total": 1,
    }


@pytest.mark.asyncio
async def test_summary_snapshot_is_allowlisted_across_page_and_command() -> None:
    """Page metrics 与命令格式化都只能看到统一非负标量。"""
    unsafe = _unsafe_snapshot()
    safe = sanitize_summary_task_snapshot(unsafe).to_dict()
    scheduler = SimpleNamespace(snapshot=AsyncMock(return_value=unsafe))
    api: Any = MetricsApiMixin()
    api.plugin = SimpleNamespace(
        initializer=SimpleNamespace(summary_scheduler=scheduler)
    )

    page_projection = await api._build_summary_task_summary()
    command_projection = DiagnosticCommandMixin._format_diagnostics(
        {"summary_tasks": unsafe}
    )

    assert page_projection == safe
    assert page_projection is not None
    assert set(page_projection) == set(safe)
    assert _CANARY not in str(page_projection)
    assert _CANARY not in command_projection
    assert "session_id" not in command_projection
    assert "job_id" not in command_projection


@pytest.mark.asyncio
async def test_summary_snapshot_failure_logs_only_exception_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """总结快照异常不得把正文、任务或来源标识写入普通日志。"""
    scheduler = SimpleNamespace(snapshot=AsyncMock(side_effect=RuntimeError(_CANARY)))
    api: Any = MetricsApiMixin()
    api.plugin = SimpleNamespace(
        initializer=SimpleNamespace(summary_scheduler=scheduler)
    )

    with caplog.at_level(logging.WARNING):
        projection = await api._build_summary_task_summary()

    assert projection == sanitize_summary_task_snapshot(None).to_dict()
    assert _CANARY not in caplog.text
    assert "RuntimeError" in caplog.text
