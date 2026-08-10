"""诊断 Page API 的路由、评分、事件与动作契约。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.diagnostics.infrastructure.event_store import DiagnosticEventStore
from core.page_api import PAGE_API_PREFIX, PluginPageApi


@pytest.fixture
def diagnostics_data_dir(tmp_path: Path) -> Path:
    """返回当前用例隔离的诊断数据目录。"""
    return tmp_path / "diagnostics-data"


def _plugin(*, data_dir: Path | None = None, include_data_dir: bool = True):
    """构造带最小 initializer 状态的插件替身。"""
    context = MagicMock()
    initializer_kwargs = {"memory_engine": object()}
    if include_data_dir:
        initializer_kwargs["data_dir"] = data_dir
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
async def test_diagnostics_events_newest_first_and_detail_lookup(
    diagnostics_data_dir,
) -> None:
    """事件接口应按新到旧列出并支持关联码详情查询。"""
    api = _api(diagnostics_data_dir)
    store = DiagnosticEventStore(diagnostics_data_dir / "diagnostics.sqlite3")
    await store.initialize()
    api._diagnostic_event_store = store
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
async def test_diagnostics_events_missing_data_dir_fails_without_relative_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺少隔离数据目录时应返回稳定错误码且不创建相对数据库。"""
    monkeypatch.chdir(tmp_path)
    api = PluginPageApi(_plugin(include_data_dir=False))

    result = await api.get_diagnostics_events_payload({})

    assert result["status"] == "error"
    assert result["message"] == "diagnostics_events_failed"
    assert not (tmp_path / "data" / "diagnostics_events.db").exists()
    assert not hasattr(api, "_diagnostic_event_store")


@pytest.mark.asyncio
async def test_rebuild_index_action_requires_confirmation(diagnostics_data_dir) -> None:
    """索引重建动作缺少确认时不得调用执行器。"""
    api = _api(diagnostics_data_dir)
    api.rebuild_index = AsyncMock(return_value={"status": "ok", "data": {"ran": True}})

    result = await api.run_diagnostics_action_payload({"action": "rebuild_index"})

    assert result == {"status": "error", "message": "confirmation_required"}
    api.rebuild_index.assert_not_awaited()


@pytest.mark.asyncio
async def test_rebuild_index_action_delegates_confirmed_response_unchanged(
    diagnostics_data_dir,
) -> None:
    """已确认的索引重建应原样返回执行器响应。"""
    api = _api(diagnostics_data_dir)
    expected = {"status": "ok", "data": {"message": "rebuilt"}}
    api.rebuild_index = AsyncMock(return_value=expected)

    result = await api.run_diagnostics_action_payload(
        {"action": "rebuild_index", "confirmed": True}
    )

    assert result is expected
    api.rebuild_index.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_restart_backfill_action_delegates_response_unchanged(
    diagnostics_data_dir,
) -> None:
    """回填重启动作应原样返回执行器响应。"""
    api = _api(diagnostics_data_dir)
    expected = {"status": "ok", "data": {"job_id": "bf_1"}}
    api.start_backfill = AsyncMock(return_value=expected)

    result = await api.run_diagnostics_action_payload({"action": "restart_backfill"})

    assert result is expected
    api.start_backfill.assert_awaited_once_with()


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
async def test_metrics_summary_still_works_with_diagnostics_mixin(
    diagnostics_data_dir,
) -> None:
    """组合诊断 mixin 后原指标摘要接口仍应正常工作。"""
    api = _api(diagnostics_data_dir)

    result = await api.get_metrics_summary()

    assert result["status"] == "ok"
    assert result["data"]["recall"]["sample_count"] == 0
    assert result["data"]["provider"]["status"] in {"unknown", "ready"}
