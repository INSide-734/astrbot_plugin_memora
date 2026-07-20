from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.page_api import PAGE_API_PREFIX, PluginPageApi


class FakeEngine:
    async def search_memories(self, **kwargs):
        query = kwargs["query"]
        if "咖啡" in query:
            return [{"doc_id": "mem-coffee", "score": 1.0}]
        return []


def _plugin_with_engine(engine=FakeEngine(), *, data_dir: Path | None = None):
    context = MagicMock()
    initializer = SimpleNamespace(memory_engine=engine)
    if data_dir is not None:
        initializer.data_dir = data_dir
    return SimpleNamespace(context=context, initializer=initializer)


def test_evaluation_routes_registered() -> None:
    plugin = _plugin_with_engine()
    api = PluginPageApi(plugin)

    api.register_routes()

    paths = [call[0][0] for call in plugin.context.register_web_api.call_args_list]
    assert f"{PAGE_API_PREFIX}/evaluation/datasets" in paths
    assert f"{PAGE_API_PREFIX}/evaluation/run" in paths
    assert f"{PAGE_API_PREFIX}/evaluation/reports" in paths
    assert f"{PAGE_API_PREFIX}/evaluation/reports/detail" in paths
    assert f"{PAGE_API_PREFIX}/evaluation/reports/compare" in paths


@pytest.mark.asyncio
async def test_evaluation_datasets_describe_live_engine_capabilities(tmp_path) -> None:
    engine = FakeEngine()
    engine.config = {"recall_engine.chain_graph_expansion_enabled": True}
    plugin = _plugin_with_engine(engine, data_dir=tmp_path)
    api = PluginPageApi(plugin)

    result = await api.get_evaluation_datasets_payload({})

    assert result["status"] == "ok"
    descriptors = {
        item["name"]: item for item in result["data"]["variants"]
    }
    assert descriptors["graph_expansion_off"] == {
        "name": "graph_expansion_off",
        "available": True,
        "reason_code": "available",
        "default_selected": True,
    }


@pytest.mark.asyncio
async def test_evaluation_api_refuses_to_run_without_memory_engine(tmp_path) -> None:
    plugin = _plugin_with_engine(None, data_dir=tmp_path)
    api = PluginPageApi(plugin)

    result = await api.run_evaluation_payload(
        {
            "datasets": ["private_basic"],
            "k": 5,
            "variants": ["baseline"],
            "baseline": "baseline",
            "save_report": False,
        }
    )

    assert result == {
        "status": "error",
        "message": "MemoryEngine unavailable",
    }


@pytest.mark.asyncio
async def test_evaluation_api_clamps_k_and_unknown_datasets(tmp_path) -> None:
    plugin = _plugin_with_engine(FakeEngine(), data_dir=tmp_path)
    api = PluginPageApi(plugin)

    result = await api.run_evaluation_payload(
        {
            "datasets": ["private_basic", "missing_fixture"],
            "k": 99,
            "variants": ["baseline", "unknown_variant"],
            "baseline": "baseline",
            "save_report": True,
        }
    )

    assert result["status"] == "ok"
    data = result["data"]
    assert data["summary"]["k"] == 20
    assert data["datasets"] == ["private_basic"]
    assert data["report_id"]
    assert data["saved"] is True


@pytest.mark.asyncio
async def test_evaluation_api_rejects_only_unknown_dataset_selection(tmp_path) -> None:
    plugin = _plugin_with_engine(FakeEngine(), data_dir=tmp_path)
    api = PluginPageApi(plugin)

    result = await api.run_evaluation_payload(
        {
            "datasets": ["missing_fixture"],
            "k": 5,
            "variants": ["baseline"],
            "baseline": "baseline",
            "save_report": False,
        }
    )

    assert result == {
        "status": "error",
        "message": "No known evaluation datasets selected",
    }


@pytest.mark.asyncio
async def test_evaluation_api_ordinary_failure_uses_stable_safe_error(
    tmp_path,
    monkeypatch,
) -> None:
    """普通评测异常不得把 Provider 正文或堆栈写入响应和日志。"""

    plugin = _plugin_with_engine(FakeEngine(), data_dir=tmp_path)
    api = PluginPageApi(plugin)

    class FailingService:
        async def initialize(self) -> None:
            """模拟已完成的 Store 初始化。"""

        def list_datasets(self) -> dict[str, list[dict[str, object]]]:
            """返回一项已知数据集，使请求进入评测执行。"""

            return {"datasets": [{"name": "private_basic"}], "variants": []}

        async def run_evaluation(self, **_kwargs):
            """模拟含敏感正文的底层普通异常。"""

            raise RuntimeError("PROVIDER-SECRET-CANARY")

    log_spy = MagicMock()
    monkeypatch.setattr("core.api.evaluation_api.logger", log_spy)
    monkeypatch.setattr(
        api,
        "_build_evaluation_service",
        MagicMock(return_value=FailingService()),
    )

    result = await api.run_evaluation_payload(
        {
            "datasets": ["private_basic"],
            "variants": ["baseline"],
        }
    )

    assert result == {
        "status": "error",
        "message": "执行评测失败",
        "code": "evaluation_run_failed",
    }
    assert "PROVIDER-SECRET-CANARY" not in str(log_spy.method_calls)


@pytest.mark.asyncio
async def test_evaluation_api_surfaces_service_error_for_unavailable_baseline(
    tmp_path,
) -> None:
    plugin = _plugin_with_engine(FakeEngine(), data_dir=tmp_path)
    api = PluginPageApi(plugin)

    result = await api.run_evaluation_payload(
        {
            "datasets": ["private_basic"],
            "k": 5,
            "variants": ["graph_expansion_off"],
            "baseline": "graph_expansion_off",
            "save_report": False,
        }
    )

    assert result["status"] == "error"
    assert result["message"] == "Baseline variant unavailable"
    assert "data" not in result


@pytest.mark.asyncio
async def test_evaluation_api_reports_round_trip_and_compare(tmp_path) -> None:
    plugin = _plugin_with_engine(FakeEngine(), data_dir=tmp_path)
    api = PluginPageApi(plugin)
    first = (
        await api.run_evaluation_payload(
            {
                "datasets": ["private_basic"],
                "k": 1,
                "variants": ["baseline"],
                "baseline": "baseline",
                "save_report": True,
            }
        )
    )["data"]
    second = (
        await api.run_evaluation_payload(
            {
                "datasets": ["private_basic"],
                "k": 5,
                "variants": ["baseline"],
                "baseline": "baseline",
                "save_report": True,
            }
        )
    )["data"]

    reports = await api.list_evaluation_reports_payload({"limit": 10})
    detail = await api.get_evaluation_report_payload({"report_id": first["report_id"]})
    comparison = await api.compare_evaluation_reports_payload(
        {
            "report_id_a": first["report_id"],
            "report_id_b": second["report_id"],
        }
    )

    assert reports["status"] == "ok"
    assert {item["report_id"] for item in reports["data"]["reports"]} >= {
        first["report_id"],
        second["report_id"],
    }
    assert detail["data"]["report"]["report_id"] == first["report_id"]
    assert comparison["data"]["report_id_a"] == first["report_id"]
    assert comparison["data"]["report_id_b"] == second["report_id"]
