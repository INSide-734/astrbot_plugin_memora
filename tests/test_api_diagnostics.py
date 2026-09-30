"""诊断 Page API 的路由、评分、事件与动作契约。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.diagnostics.infrastructure.event_store import DiagnosticEventStore
from core.platform.transport.page_api.page_api import PAGE_API_PREFIX, PluginPageApi

_SENTINEL = "PRIVATE_SENTINEL_NEVER_EXPOSE"


@pytest.fixture
def diagnostics_data_dir(tmp_path: Path) -> Path:
    """返回当前用例隔离的诊断数据目录。"""
    return tmp_path / "diagnostics-data"


def _plugin(
    *,
    data_dir: Path | None = None,
    include_data_dir: bool = True,
    diagnostic_event_store: object | None = None,
):
    """构造带最小 initializer 状态的插件替身。"""
    context = MagicMock()
    initializer_kwargs = {"memory_engine": object()}
    if include_data_dir:
        initializer_kwargs["data_dir"] = data_dir
    if diagnostic_event_store is not None:
        initializer_kwargs["diagnostic_event_store"] = diagnostic_event_store
    initializer = SimpleNamespace(**initializer_kwargs)
    return SimpleNamespace(context=context, initializer=initializer)


def _api(tmp_path: Path) -> PluginPageApi:
    """构造绑定隔离数据目录的 Page API。"""
    return PluginPageApi(_plugin(data_dir=tmp_path))


def test_diagnostics_routes_registered(diagnostics_data_dir) -> None:
    """Page API 应注册四条诊断路由。"""
    api = _api(diagnostics_data_dir)

    api.register_routes()

    paths = [call[0][0] for call in api.plugin.context.register_web_api.call_args_list]
    assert f"{PAGE_API_PREFIX}/diagnostics/health" in paths
    assert f"{PAGE_API_PREFIX}/diagnostics/events" in paths
    assert f"{PAGE_API_PREFIX}/diagnostics/events/detail" in paths
    assert f"{PAGE_API_PREFIX}/diagnostics/actions/run" in paths


@pytest.mark.asyncio
async def test_diagnostics_health_returns_score_level_domains_and_actions(
    diagnostics_data_dir,
) -> None:
    """健康接口应返回分数、等级、领域明细和建议动作。"""
    api = _api(diagnostics_data_dir)
    api._build_recall_summary = MagicMock(return_value={"p95_total_ms": 1500.0})
    api._build_background_task_summary = MagicMock(return_value={"failed": 1})
    api._build_provider_summary = MagicMock(
        return_value={"status": "failed", "attempts": 60, "max_attempts": 60}
    )
    api._build_index_summary = MagicMock(
        return_value={"last_rebuild_errors": 2, "last_rebuild_total": 10}
    )
    api._build_write_coordinator_summary = MagicMock(return_value={"failures_total": 0})
    api._build_prometheus_summary = MagicMock(return_value={"available": True})

    result = await api.get_diagnostics_health()

    assert result["status"] == "ok"
    data = result["data"]
    assert isinstance(data["score"], int)
    assert data["level"] == "critical"
    assert {item["name"] for item in data["domains"]} >= {
        "provider",
        "recall",
        "scheduler",
        "index",
    }
    assert data["recommended_actions"]


@pytest.mark.asyncio
async def test_diagnostics_health_projects_blocked_restore_state(
    diagnostics_data_dir,
) -> None:
    """恢复事务阻塞时健康域应报告 watch 且不回显操作 ID。"""
    api = _api(diagnostics_data_dir)
    operation_id = _SENTINEL
    api.plugin._backup_manager = MagicMock()
    api.plugin._backup_manager.get_maintenance_state.return_value = {
        "blocked": True,
        "operation_id": operation_id,
        "status": "staged",
    }

    result = await api.get_diagnostics_health()

    restore = next(
        item for item in result["data"]["domains"] if item["name"] == "restore"
    )
    assert restore["status"] == "watch"
    assert restore["score"] == 55
    assert operation_id not in str(result["data"])


@pytest.mark.asyncio
async def test_diagnostics_events_newest_first_and_detail_lookup(
    diagnostics_data_dir,
) -> None:
    """事件接口应按新到旧列出并支持关联码详情查询。"""
    store = DiagnosticEventStore(diagnostics_data_dir / "diagnostics.sqlite3")
    await store.initialize()
    api = PluginPageApi(
        _plugin(data_dir=diagnostics_data_dir, diagnostic_event_store=store)
    )
    older = await store.add_event(
        {
            "event_id": "older",
            "created_at": "2026-07-04T10:00:00+00:00",
            "domain": "provider",
            "severity": "warning",
            "title": "Provider slow",
            "message": "older event",
            "source": "test",
        }
    )
    newer = await store.add_event(
        {
            "event_id": "newer",
            "created_at": "2026-07-04T11:00:00+00:00",
            "domain": "index",
            "severity": "critical",
            "title": "Index failed",
            "message": "newer event",
            "source": "test",
            "payload": {"attempt": 2},
        }
    )

    listed = await api.get_diagnostics_events_payload({"limit": 10})
    detail = await api.get_diagnostics_event_detail_payload(
        {"event_id": older["event_id"]}
    )

    assert listed["status"] == "ok"
    assert [item["event_id"] for item in listed["data"]["events"]] == [
        newer["event_id"],
        older["event_id"],
    ]
    assert listed["data"]["total"] == 2
    assert detail["status"] == "ok"
    assert detail["data"]["event"]["event_id"] == older["event_id"]


@pytest.mark.asyncio
async def test_diagnostics_events_without_published_store_fails_without_relative_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """组合根未发布 Store 时返回稳定错误码，且请求路径不得创建任何数据库。"""
    monkeypatch.chdir(tmp_path)
    api = PluginPageApi(_plugin(include_data_dir=False))

    result = await api.get_diagnostics_events_payload({})

    assert result["status"] == "error"
    assert result["message"] == "diagnostics_events_failed"
    assert not (tmp_path / "data" / "diagnostics_events.db").exists()
    assert list(tmp_path.rglob("*.db")) == []
    assert not hasattr(api, "_diagnostic_event_store")


@pytest.mark.asyncio
async def test_diagnostics_request_path_never_builds_event_store(
    diagnostics_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """并发首访只消费已发布实例，请求路径不得再构造或初始化 Store。"""
    store = DiagnosticEventStore(diagnostics_data_dir / "diagnostics.sqlite3")
    await store.initialize()
    api = PluginPageApi(
        _plugin(data_dir=diagnostics_data_dir, diagnostic_event_store=store)
    )
    store_type = MagicMock(side_effect=AssertionError("request path built a store"))
    monkeypatch.setattr(DiagnosticEventStore, "__init__", store_type)

    first, second = await asyncio.gather(
        api.get_diagnostics_events_payload({}),
        api.get_diagnostics_events_payload({}),
    )

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    store_type.assert_not_called()
    assert api._get_diagnostic_event_store() is store


@pytest.mark.asyncio
async def test_rebuild_index_action_requires_confirmation(diagnostics_data_dir) -> None:
    """索引重建动作缺少确认时不得调用执行器。"""
    api = _api(diagnostics_data_dir)
    api.rebuild_index = AsyncMock(return_value={"status": "ok", "data": {"ran": True}})

    result = await api.run_diagnostics_action_payload({"action": "rebuild_index"})

    assert result == {"status": "error", "message": "confirmation_required"}
    api.rebuild_index.assert_not_awaited()


@pytest.mark.asyncio
async def test_rebuild_index_action_projects_only_safe_aggregates(
    diagnostics_data_dir,
) -> None:
    """已确认的索引重建只公开有界计数与闭集模式，丢弃消息和失败 ID。"""
    api = _api(diagnostics_data_dir)
    api.rebuild_index = AsyncMock(
        return_value={
            "status": "ok",
            "data": {
                "message": _SENTINEL,
                "result": {
                    "success": True,
                    "message": _SENTINEL,
                    "processed": 8,
                    "errors": 2,
                    "total": 10,
                    "partial": True,
                    "vector_mode": "repair",
                    "failed_ids": [_SENTINEL],
                    "failure_ratio": float("inf"),
                },
            },
        }
    )

    result = await api.run_diagnostics_action_payload(
        {"action": "rebuild_index", "confirmed": True}
    )

    assert result == {
        "status": "ok",
        "data": {
            "action": "rebuild_index",
            "status": "completed",
            "result": {
                "processed": 8,
                "errors": 2,
                "total": 10,
                "partial": True,
                "vector_mode": "repair",
            },
        },
    }
    api.rebuild_index.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_rebuild_index_unsuccessful_result_is_not_reported_completed(
    diagnostics_data_dir,
) -> None:
    """ok envelope 内 success=False 的重建必须报告失败，而不是成功。"""
    api = _api(diagnostics_data_dir)
    api.rebuild_index = AsyncMock(
        return_value={
            "status": "ok",
            "data": {"result": {"success": False, "errors": 3, "error": _SENTINEL}},
        }
    )

    result = await api.run_diagnostics_action_payload(
        {"action": "rebuild_index", "confirmed": True}
    )

    assert result == {
        "status": "error",
        "message": "rebuild_index_failed",
        "data": {
            "action": "rebuild_index",
            "status": "failed",
            "result": {"errors": 3},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "method"),
    (("rebuild_index", "rebuild_index"), ("restart_backfill", "start_backfill")),
)
async def test_delegated_action_errors_hide_free_text(
    diagnostics_data_dir,
    action: str,
    method: str,
) -> None:
    """委托接口的自由文本错误只收敛为稳定错误码，保留既有闭集 code。"""
    api = _api(diagnostics_data_dir)
    setattr(
        api,
        method,
        AsyncMock(return_value={"status": "error", "message": f"失败：{_SENTINEL}"}),
    )
    free_text = await api.run_diagnostics_action_payload(
        {"action": action, "confirmed": True}
    )
    setattr(
        api,
        method,
        AsyncMock(
            return_value={
                "status": "error",
                "message": _SENTINEL,
                "code": "maintenance_blocked",
            }
        ),
    )
    blocked = await api.run_diagnostics_action_payload(
        {"action": action, "confirmed": True}
    )

    assert free_text == {"status": "error", "message": f"{action}_failed"}
    assert blocked == {"status": "error", "message": "maintenance_blocked"}


@pytest.mark.asyncio
async def test_restart_backfill_action_hides_job_id(
    diagnostics_data_dir,
) -> None:
    """回填启动成功只报告 started，不回显内部 job ID 或消息。"""
    api = _api(diagnostics_data_dir)
    api.start_backfill = AsyncMock(
        return_value={"status": "ok", "data": {"job_id": _SENTINEL, "message": "x"}}
    )

    result = await api.run_diagnostics_action_payload({"action": "restart_backfill"})

    assert result == {
        "status": "ok",
        "data": {"action": "restart_backfill", "status": "started"},
    }
    api.start_backfill.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_diagnostics_action_propagates_cancellation(
    diagnostics_data_dir,
) -> None:
    """委托动作被取消时必须继续传播 CancelledError。"""
    api = _api(diagnostics_data_dir)
    api.start_backfill = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await api.run_diagnostics_action_payload({"action": "restart_backfill"})


@pytest.mark.asyncio
async def test_diagnostics_events_reject_unknown_filters_and_missing_detail(
    diagnostics_data_dir,
) -> None:
    """非闭集筛选值与缺失事件返回稳定错误码，合法筛选大小写不敏感。"""
    store = DiagnosticEventStore(diagnostics_data_dir / "diagnostics.sqlite3")
    await store.initialize()
    await store.add_event(
        {"event_id": "index-event", "domain": "index", "severity": "warning"}
    )
    api = PluginPageApi(
        _plugin(data_dir=diagnostics_data_dir, diagnostic_event_store=store)
    )

    invalid = await api.get_diagnostics_events_payload({"domain": _SENTINEL})
    filtered = await api.get_diagnostics_events_payload(
        {"domain": "INDEX", "severity": "Warning"}
    )
    missing = await api.get_diagnostics_event_detail_payload({"event_id": "absent"})
    required = await api.get_diagnostics_event_detail_payload({})

    assert invalid == {"status": "error", "message": "invalid_diagnostics_filter"}
    assert [item["event_id"] for item in filtered["data"]["events"]] == ["index-event"]
    assert missing == {"status": "error", "message": "diagnostics_event_not_found"}
    assert required == {"status": "error", "message": "event_id_required"}


@pytest.mark.asyncio
async def test_clear_completed_events_reports_resolved_count(
    diagnostics_data_dir: Path,
) -> None:
    """清理动作应读取已发布 Store，并保持当前 noop 语义。"""
    store = DiagnosticEventStore(diagnostics_data_dir / "diagnostics.sqlite3")
    await store.initialize()
    await store.add_event(
        {
            "event_id": "resolved-event",
            "created_at": "2026-07-04T10:00:00+00:00",
            "domain": "provider",
            "severity": "warning",
            "source": "test",
        }
    )
    await store.resolve_event("resolved-event")
    await store.add_event(
        {
            "event_id": "active-event",
            "created_at": "2026-07-04T11:00:00+00:00",
            "domain": "index",
            "severity": "warning",
            "source": "test",
        }
    )
    api = PluginPageApi(
        _plugin(data_dir=diagnostics_data_dir, diagnostic_event_store=store)
    )

    result = await api.run_diagnostics_action_payload(
        {"action": "clear_completed_events"}
    )

    assert result == {
        "status": "ok",
        "data": {
            "action": "clear_completed_events",
            "status": "noop",
            "cleared": 0,
            "resolved": 1,
        },
    }


@pytest.mark.asyncio
async def test_diagnostics_action_delegate_exception_returns_error(
    diagnostics_data_dir,
) -> None:
    """诊断动作异常应隐藏原始消息并返回稳定错误码。"""
    api = _api(diagnostics_data_dir)
    api.start_backfill = AsyncMock(side_effect=RuntimeError("backfill failed"))

    result = await api.run_diagnostics_action_payload({"action": "restart_backfill"})

    assert result["status"] == "error"
    assert result["message"] == "diagnostics_action_failed"


@pytest.mark.asyncio
async def test_refresh_metrics_action_succeeds_without_confirmation(
    diagnostics_data_dir,
) -> None:
    """指标刷新是只读动作，无需确认即可返回新快照。"""
    api = _api(diagnostics_data_dir)
    api._build_recall_summary = MagicMock(return_value={"sample_count": 0})
    api._build_background_task_summary = MagicMock(return_value={"failed": 0})
    api._build_provider_summary = MagicMock(return_value={"status": "ready"})
    api._build_index_summary = MagicMock(return_value={"validator_available": True})
    api._build_write_coordinator_summary = MagicMock(return_value={"failures_total": 0})
    api._build_prometheus_summary = MagicMock(return_value={"available": True})

    result = await api.run_diagnostics_action_payload({"action": "refresh_metrics"})

    assert result["status"] == "ok"
    assert result["data"]["action"] == "refresh_metrics"
    assert result["data"]["metrics"]["provider"]["status"] == "ready"
    assert result["data"]["health"]["level"] == "healthy"
    api._build_recall_summary.assert_called_once_with()
    api._build_background_task_summary.assert_called_once_with()
    api._build_provider_summary.assert_called_once_with()
    api._build_index_summary.assert_called_once_with()
    api._build_write_coordinator_summary.assert_called_once_with()
    api._build_prometheus_summary.assert_called_once_with()


@pytest.mark.asyncio
async def test_refresh_metrics_projects_bounded_closed_set_canary(
    diagnostics_data_dir,
) -> None:
    """刷新指标必须去除自由文本、ID 和失败任务详情。"""
    api = _api(diagnostics_data_dir)
    api._build_recall_summary = MagicMock(
        return_value={"sample_count": 1, "error_message": _SENTINEL}
    )
    api._build_background_task_summary = MagicMock(
        return_value={
            "failed": 1,
            "failed_tasks": [
                {"name": _SENTINEL, "error": _SENTINEL, "suggestion": _SENTINEL}
            ],
            "job_id": _SENTINEL,
        }
    )
    api._build_provider_summary = MagicMock(
        return_value={
            "status": "ready",
            "error_message": _SENTINEL,
            "job_id": _SENTINEL,
        }
    )
    api._build_index_summary = MagicMock(
        return_value={"last_rebuild_message": _SENTINEL, "last_rebuild_errors": 2}
    )
    api._build_write_coordinator_summary = MagicMock(
        return_value={"failures_total": 1, "last_error": _SENTINEL}
    )
    api._build_anomaly_summary = MagicMock(
        return_value={"available": True, "error_message": _SENTINEL, "alerts": 1}
    )
    api._build_learning_summary = MagicMock(
        return_value={"available": True, "reason": _SENTINEL, "candidate_count": 2}
    )
    api._build_prometheus_summary = MagicMock(
        return_value={"available": True, "metric_names": [_SENTINEL]}
    )

    result = await api.run_diagnostics_action_payload({"action": "refresh_metrics"})

    metrics = result["data"]["metrics"]
    assert set(metrics) == {
        "recall",
        "provider",
        "index",
        "write_coordinator",
        "background_tasks",
        "anomaly",
        "learning",
        "prometheus",
        "summary_tasks",
    }
    encoded = str(metrics)
    assert _SENTINEL not in encoded
    assert "error_message" not in encoded
    assert "last_error" not in encoded
    assert "last_rebuild_message" not in encoded
    assert "job_id" not in encoded
    assert metrics["summary_tasks"]["status"] == "unknown"
    assert metrics["prometheus"]["status"] == "available"


@pytest.mark.asyncio
async def test_metrics_summary_still_works_with_diagnostics_mixin(
    diagnostics_data_dir,
) -> None:
    """组合诊断 mixin 后原指标摘要接口仍应正常工作。"""
    api = _api(diagnostics_data_dir)

    result = await api.get_metrics_summary()

    assert result["status"] == "ok"
    assert result["data"]["recall"]["sample_count"] == 0
    assert result["data"]["provider"]["status"] in {"unknown", "ready"}
