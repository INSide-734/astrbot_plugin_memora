from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.features.observability.application import PerfTracker
from core.managers.write_coordinator import reset_write_metrics_snapshot
from core.platform.transport.page_api.metrics_api import MetricsApiMixin


class _TaskStub:
    def __init__(
        self,
        *,
        done: bool,
        cancelled: bool = False,
        exc: Exception | None = None,
        name: str = "task",
    ) -> None:
        self._done = done
        self._cancelled = cancelled
        self._exc = exc
        self._name = name

    def done(self) -> bool:
        return self._done

    def cancelled(self) -> bool:
        return self._cancelled

    def exception(self) -> Exception | None:
        return self._exc

    def get_name(self) -> str:
        return self._name


class _MetricsApiStub(MetricsApiMixin):
    def __init__(self, plugin) -> None:
        self.plugin = plugin


@pytest.mark.asyncio
async def test_metrics_summary_returns_serializable_runtime_snapshot() -> None:
    reset_write_metrics_snapshot()
    tracker = PerfTracker(maxlen=10)
    tracker.record(
        {
            "total_ms": 100.0,
            "bm25_ms": 10.0,
            "vector_ms": 30.0,
            "graph_ms": 20.0,
            "rerank_ms": 40.0,
        }
    )
    tracker.record(
        {
            "total_ms": 200.0,
            "bm25_ms": 20.0,
            "vector_ms": 60.0,
            "graph_ms": 40.0,
            "rerank_ms": 80.0,
        }
    )

    quality_scorer = MagicMock()
    quality_scorer.get_stats.return_value = {
        "status": "ok",
        "total_scored": 3,
        "avg_overall": 0.74,
    }
    plugin = SimpleNamespace(
        _perf_tracker=tracker,
        _quality_scorer=quality_scorer,
        _pending_tasks={_TaskStub(done=False), _TaskStub(done=True)},
    )
    api = _MetricsApiStub(plugin)

    result = await api.get_metrics_summary()

    assert result["status"] == "ok"
    data = result["data"]
    assert data["recall"]["sample_count"] == 2
    assert data["recall"]["avg_total_ms"] == 150.0
    assert data["recall"]["p50_total_ms"] == 150.0
    assert data["recall"]["p95_total_ms"] == 195.0
    assert data["quality"]["total_scored"] == 3
    assert data["background_tasks"]["tracked"] == 2
    assert data["background_tasks"]["active"] == 1
    assert data["background_tasks"]["completed"] == 1
    assert data["background_tasks"]["failed"] == 0
    assert data["background_tasks"]["cancelled"] == 0
    assert data["write_coordinator"] == {
        "operations_total": 0,
        "lock_retries_total": 0,
        "failures_total": 0,
        "retry_exhausted_total": 0,
        "fatal_failures_total": 0,
        "non_retryable_failures_total": 0,
        "last_error": None,
    }
    assert isinstance(data["prometheus"]["available"], bool)
    assert isinstance(data["prometheus"]["collector_count"], int)


@pytest.mark.asyncio
async def test_metrics_summary_handles_missing_components_without_error() -> None:
    api = _MetricsApiStub(SimpleNamespace())

    result = await api.get_metrics_summary()

    assert result["status"] == "ok"
    data = result["data"]
    assert data["recall"]["sample_count"] == 0
    assert data["recall"]["recent"] == []
    assert data["quality"]["status"] == "unavailable"
    assert data["background_tasks"]["tracked"] == 0
    assert data["background_tasks"]["active"] == 0
    assert data["background_tasks"]["completed"] == 0
    assert data["background_tasks"]["failed"] == 0
    assert data["background_tasks"]["cancelled"] == 0
    assert data["provider"]["status"] == "unknown"
    assert data["index"]["validator_available"] is False


@pytest.mark.asyncio
async def test_metrics_summary_exposes_provider_index_and_failed_background_tasks() -> (
    None
):
    failed_task = _TaskStub(
        done=True,
        exc=RuntimeError("scheduler exploded"),
        name="decay-startup",
    )
    cancelled_task = _TaskStub(done=True, cancelled=True, name="provider-retry")
    backfill_scheduler = SimpleNamespace(
        progress={
            "status": "completed_with_errors",
            "errors": 2,
            "processed": 7,
            "total": 10,
            "completed_at": 1234.5,
        },
        is_running=False,
    )
    decay_task = _TaskStub(done=False, name="decay-loop")
    decay_scheduler = SimpleNamespace(
        _running=True,
        _task=decay_task,
        _startup_task=failed_task,
    )
    provider_waiter = SimpleNamespace(
        providers_ready=False,
        attempts=4,
        _max_attempts=60,
        _retry_task=_TaskStub(done=False, name="provider-retry"),
    )
    initializer = SimpleNamespace(
        _provider_waiter=provider_waiter,
        get_readiness_snapshot=lambda: {
            "is_initialized": False,
            "is_failed": False,
            "error_message": None,
            "provider_attempts": 4,
            "missing_provider": ["embedding"],
            "components_ready": {"memory_engine": False},
        },
        index_validator=object(),
        backfill_scheduler=backfill_scheduler,
        decay_scheduler=decay_scheduler,
    )
    plugin = SimpleNamespace(
        initializer=initializer,
        _pending_tasks={failed_task, cancelled_task},
        _index_observability={
            "last_rebuild_success": False,
            "last_rebuild_duration_seconds": 1.25,
            "last_rebuild_errors": 3,
            "last_rebuild_total": 9,
            "last_rebuild_message": "BM25 failed",
        },
    )
    api = _MetricsApiStub(plugin)

    result = await api.get_metrics_summary()

    assert result["status"] == "ok"
    data = result["data"]
    assert data["background_tasks"]["failed"] == 1
    assert data["background_tasks"]["cancelled"] == 1
    assert data["background_tasks"]["failed_tasks"] == [
        {
            "name": "decay-startup",
            "error": "RuntimeError",
            "message": "scheduler exploded",
            "suggestion": "检查衰减调度器启动日志；修复异常后重启插件以恢复定期衰减。",
        }
    ]
    assert data["background_tasks"]["schedulers"]["backfill"] == {
        "job_id": None,
        "status": "completed_with_errors",
        "running": False,
        "errors": 2,
        "processed": 7,
        "total": 10,
        "last_error": None,
        "started_at": None,
        "completed_at": 1234.5,
        "cancelled_at": None,
        "last_finished_at": 1234.5,
        "retry_count": 0,
        "suggestion": "检查话题分割配置和最近的错误详情；修复后可重新启动存量回填。",
    }
    assert data["background_tasks"]["schedulers"]["decay"] == {
        "running": True,
        "loop_active": True,
        "startup_active": False,
        "startup_failed": True,
        "check_hour": 0,
        "check_minute": 0,
        "next_run_in_seconds": None,
        "last_decay_date": None,
        "last_completed_at": None,
        "retry_count": 0,
        "startup_error": "RuntimeError",
        "startup_message": "scheduler exploded",
        "suggestion": "检查衰减调度器启动日志；修复异常后重启插件以恢复定期衰减。",
    }
    assert data["provider"] == {
        "status": "waiting",
        "providers_ready": False,
        "attempts": 4,
        "max_attempts": 60,
        "retry_active": True,
        "missing_provider": ["embedding"],
        "is_initialized": False,
        "is_failed": False,
        "error_message": None,
        "components_ready": {"memory_engine": False},
    }
    assert data["index"] == {
        "validator_available": True,
        "last_rebuild_success": False,
        "last_rebuild_duration_seconds": 1.25,
        "last_rebuild_errors": 3,
        "last_rebuild_total": 9,
        "last_rebuild_message": "BM25 failed",
    }


@pytest.mark.asyncio
async def test_metrics_summary_includes_recovery_suggestions_for_failures() -> None:
    failed_task = _TaskStub(
        done=True,
        exc=TimeoutError("provider retry timed out"),
        name="provider-retry",
    )
    backfill_scheduler = SimpleNamespace(
        progress={
            "status": "failed",
            "errors": 4,
            "processed": 21,
            "total": 80,
            "error": "topic split failed",
        },
        is_running=False,
    )
    decay_scheduler = SimpleNamespace(
        _running=False,
        _task=None,
        _startup_task=_TaskStub(
            done=True,
            exc=RuntimeError("decay startup failed"),
            name="decay-startup",
        ),
    )
    initializer = SimpleNamespace(
        backfill_scheduler=backfill_scheduler,
        decay_scheduler=decay_scheduler,
    )
    plugin = SimpleNamespace(
        initializer=initializer,
        _pending_tasks={failed_task},
    )
    api = _MetricsApiStub(plugin)

    result = await api.get_metrics_summary()

    assert result["status"] == "ok"
    background = result["data"]["background_tasks"]
    assert background["failed_tasks"] == [
        {
            "name": "provider-retry",
            "error": "TimeoutError",
            "message": "provider retry timed out",
            "suggestion": "检查 LLM/Embedding provider 配置与网络状态，然后等待重试或重启插件初始化。",
        }
    ]
    assert background["schedulers"]["backfill"]["suggestion"] == (
        "检查话题分割配置和最近的错误详情；修复后可重新启动存量回填。"
    )
    assert background["schedulers"]["decay"]["startup_error"] == "RuntimeError"
    assert background["schedulers"]["decay"]["suggestion"] == (
        "检查衰减调度器启动日志；修复异常后重启插件以恢复定期衰减。"
    )


@pytest.mark.asyncio
async def test_metrics_summary_exposes_scheduler_run_metadata(tmp_path) -> None:
    decay_state = tmp_path / "decay_state.json"
    decay_state.write_text(
        json.dumps(
            {
                "last_decay_date": "2026-07-04",
                "last_decay_timestamp": 1783140000.5,
            }
        ),
        encoding="utf-8",
    )
    backfill_scheduler = SimpleNamespace(
        progress={
            "job_id": "bf_1783140000",
            "status": "completed",
            "errors": 0,
            "processed": 12,
            "total": 12,
            "started_at": 1783139900.0,
            "completed_at": 1783140000.0,
            "retry_count": 2,
        },
        is_running=False,
    )
    decay_scheduler = SimpleNamespace(
        _running=True,
        _task=_TaskStub(done=False, name="decay-loop"),
        _startup_task=_TaskStub(done=True, name="decay-startup"),
        _state_file=decay_state,
        _retry_count=1,
        check_hour=3,
        check_minute=15,
        _seconds_until_next_run=lambda: 7200.25,
    )
    initializer = SimpleNamespace(
        backfill_scheduler=backfill_scheduler,
        decay_scheduler=decay_scheduler,
    )
    api = _MetricsApiStub(SimpleNamespace(initializer=initializer))

    result = await api.get_metrics_summary()

    assert result["status"] == "ok"
    schedulers = result["data"]["background_tasks"]["schedulers"]
    assert schedulers["backfill"]["job_id"] == "bf_1783140000"
    assert schedulers["backfill"]["started_at"] == 1783139900.0
    assert schedulers["backfill"]["completed_at"] == 1783140000.0
    assert schedulers["backfill"]["last_finished_at"] == 1783140000.0
    assert schedulers["backfill"]["retry_count"] == 2
    assert schedulers["decay"]["check_hour"] == 3
    assert schedulers["decay"]["check_minute"] == 15
    assert schedulers["decay"]["next_run_in_seconds"] == 7200.25
    assert schedulers["decay"]["last_decay_date"] == "2026-07-04"
    assert schedulers["decay"]["last_completed_at"] == 1783140000.5
    assert schedulers["decay"]["retry_count"] == 1


@pytest.mark.asyncio
async def test_recall_samples_payload_clamps_cursor_and_limit() -> None:
    """指标端点必须限制游标和分页大小。"""
    tracker = PerfTracker(maxlen=3)
    tracker.record(
        {
            "retrieval_total_ms": 7.5,
            "partial_fallback": True,
            "graph_route_degraded": True,
            "route_aborted": False,
        }
    )
    plugin = SimpleNamespace(initializer=SimpleNamespace(_perf_tracker=tracker))
    api = _MetricsApiStub(plugin)

    response = await api.get_recall_samples_payload(
        {"after_sequence": "0", "limit": "9999"}
    )

    assert response["status"] == "ok"
    assert response["data"]["items"][0]["retrieval_total_ms"] == 7.5
    assert response["data"]["items"][0]["partial_fallback"] is True
    assert response["data"]["items"][0]["graph_route_degraded"] is True
    assert response["data"]["items"][0]["route_aborted"] is False
    assert response["data"]["latest_sequence"] == 1


@pytest.mark.asyncio
async def test_recall_samples_returns_empty_page_when_no_tracker() -> None:
    """无 PerfTracker 时返回空页。"""
    plugin = SimpleNamespace(initializer=None)
    api = _MetricsApiStub(plugin)

    response = await api.get_recall_samples_payload({})

    assert response["status"] == "ok"
    assert response["data"]["items"] == []
    assert response["data"]["next_sequence"] == 0


@pytest.mark.asyncio
async def test_recall_samples_rejects_invalid_query() -> None:
    """非法参数必须返回稳定错误，不能重置游标读取历史样本。"""
    tracker = PerfTracker(maxlen=3)
    plugin = SimpleNamespace(initializer=SimpleNamespace(_perf_tracker=tracker))
    api = _MetricsApiStub(plugin)

    response = await api.get_recall_samples_payload({"after_sequence": "not-a-number"})

    assert response == {
        "status": "error",
        "message": "recall_samples_invalid_query",
    }


@pytest.mark.asyncio
async def test_recall_samples_rejects_invalid_query_without_tracker() -> None:
    """参数校验不得因 PerfTracker 尚未初始化而被绕过。"""

    plugin = SimpleNamespace(initializer=None)
    api = _MetricsApiStub(plugin)

    response = await api.get_recall_samples_payload({"limit": "not-a-number"})

    assert response == {
        "status": "error",
        "message": "recall_samples_invalid_query",
    }


@pytest.mark.asyncio
async def test_recall_samples_reports_unavailable_when_tracker_raises() -> None:
    """PerfTracker 读取异常时必须返回稳定错误码。"""

    tracker = MagicMock()
    tracker.get_samples.side_effect = RuntimeError("unexpected failure")
    plugin = SimpleNamespace(initializer=SimpleNamespace(_perf_tracker=tracker))
    api = _MetricsApiStub(plugin)

    response = await api.get_recall_samples_payload({})

    assert response == {
        "status": "error",
        "message": "recall_samples_unavailable",
    }
    tracker.get_samples.assert_called_once_with(after_sequence=0, limit=50)
