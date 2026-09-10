"""core/api/topic_segmentation_api.py — TopicSegmentationApiMixin 测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from astrbot.api import web as astrbot_web

from core.platform.config import ConfigApplyResult
from core.platform.transport.page_api.config_api import ConfigApiMixin
from core.platform.transport.page_api.topic_segmentation_api import (
    TopicSegmentationApiMixin,
)


def _make_config_manager(*, apply_error: Exception | None = None):
    values = {
        "topic_segmentation.enabled": True,
        "topic_segmentation.strategy": "a_b_hybrid",
        "topic_segmentation.strategy_b.similarity_threshold": 0.75,
        "topic_segmentation.strategy_b.min_cluster_size": 2,
        "topic_segmentation.strategy_b.max_clusters": 5,
        "topic_segmentation.strategy_c.topic_shift_threshold": 0.3,
        "topic_segmentation.strategy_c.min_chunk_size": 3,
        "topic_segmentation.strategy_d.stage1_max_topics": 4,
        "topic_segmentation.strategy_d.enable_parallel_stage2": True,
        "topic_segmentation.hybrid_fallback_fact_threshold": 2,
        "topic_segmentation.legacy_backfill.enabled": True,
        "topic_segmentation.legacy_backfill.batch_size": 50,
        "topic_segmentation.legacy_backfill.max_backfill_per_run": 200,
    }

    manager = MagicMock()
    manager.get.side_effect = values.get
    manager.get_config_snapshot_async = AsyncMock(return_value=({}, "rev-1"))

    async def _apply(changes, *, expected_revision=None, persist=True):
        if apply_error is not None:
            raise apply_error
        return ConfigApplyResult("rev-2", tuple(sorted(changes)))

    manager.apply_config_changes = AsyncMock(side_effect=_apply)
    return manager


def _make_stub(
    *,
    apply_error: Exception | None = None,
    scheduler=None,
    catalog_gen: int | None = 1,
    dirty_count=0,
):
    """组合真实 handler 与公共 Store 端口，不耦合实现内部 SQL。"""
    summary = {
        "catalog_status": "unavailable"
        if catalog_gen is None
        else "degraded"
        if catalog_gen == -1
        else "ready",
        "dirty_count": dirty_count,
        "scope_buckets": {"small": 10, "medium": 5, "large": 2},
    }
    store = SimpleNamespace(get_catalog_summary=AsyncMock(return_value=summary))

    class Stub(TopicSegmentationApiMixin, ConfigApiMixin):
        """复用 API 真实方法，仅替换外部组件。"""

        async def _ensure_plugin_ready(self):
            """返回无生产数据的运行时组件。"""
            return {
                "memory_engine": SimpleNamespace(topic_catalog_store=store),
                "conversation_store": SimpleNamespace(connection=None),
            }, None

    stub = Stub()
    stub.plugin = MagicMock()
    stub.plugin.initializer = None
    stub.plugin.config_manager = _make_config_manager(apply_error=apply_error)
    stub.plugin.context = MagicMock()
    stub.plugin.context.request = MagicMock()
    stub.plugin.context.request.json = AsyncMock(return_value={})
    stub.plugin._backfill_scheduler = scheduler
    return stub


class TestTopicSegmentationConfig:
    @pytest.mark.asyncio
    async def test_get_config_returns_nested_sections(self) -> None:
        stub = _make_stub()

        result = await stub.get_topic_segmentation_config()

        assert result["status"] == "ok"
        assert result["data"]["strategy"] == "a_b_hybrid"
        assert result["data"]["strategy_b"]["min_cluster_size"] == 2
        assert result["data"]["legacy_backfill"]["batch_size"] == 50

    @pytest.mark.asyncio
    async def test_update_accepts_strategy_alias_and_numeric_values(self) -> None:
        stub = _make_stub()
        stub.plugin.context.request.json = AsyncMock(
            return_value={
                "strategy": "b",
                "enabled": False,
                "strategy_b": {
                    "similarity_threshold": 0.8,
                    "min_cluster_size": 3,
                    "max_clusters": 6,
                },
            }
        )

        result = await stub.update_topic_segmentation_config()

        assert result["status"] == "ok"
        stub.plugin.config_manager.apply_config_changes.assert_awaited_once_with(
            {
                "topic_segmentation.strategy": "strategy_b",
                "topic_segmentation.enabled": False,
                "topic_segmentation.strategy_b.similarity_threshold": 0.8,
                "topic_segmentation.strategy_b.min_cluster_size": 3,
                "topic_segmentation.strategy_b.max_clusters": 6,
            },
            expected_revision="rev-1",
            persist=True,
        )

    @pytest.mark.asyncio
    async def test_update_uses_public_web_request_proxy(self) -> None:
        """真实 AstrBot Context 没有 request 属性时使用公共请求代理。"""
        stub = _make_stub()
        stub.plugin.context = SimpleNamespace()
        request = SimpleNamespace(
            json=AsyncMock(return_value={"enabled": False}),
        )

        with patch.object(astrbot_web, "request", request):
            result = await stub.update_topic_segmentation_config()

        assert result["status"] == "ok"
        stub.plugin.config_manager.apply_config_changes.assert_awaited_once_with(
            {"topic_segmentation.enabled": False},
            expected_revision="rev-1",
            persist=True,
        )

    @pytest.mark.asyncio
    async def test_update_rejects_non_object_json_body(self) -> None:
        stub = _make_stub()
        stub.plugin.context.request.json = AsyncMock(return_value=["bad-body"])

        result = await stub.update_topic_segmentation_config()

        assert result["status"] == "error"
        assert "JSON" in result["message"]
        stub.plugin.config_manager.apply_config_changes.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("min_cluster_size", 1.5),
            ("max_clusters", 2.2),
            ("min_chunk_size", 3.9),
            ("stage1_max_topics", 4.1),
        ],
    )
    async def test_update_rejects_fractional_values_for_integer_fields(
        self, field: str, value: float
    ) -> None:
        stub = _make_stub()
        section = (
            "strategy_b"
            if field in {"min_cluster_size", "max_clusters"}
            else "strategy_c"
            if field == "min_chunk_size"
            else "strategy_d"
        )
        stub.plugin.context.request.json = AsyncMock(
            return_value={section: {field: value}}
        )

        result = await stub.update_topic_segmentation_config()

        assert result["status"] == "error"
        stub.plugin.config_manager.apply_config_changes.assert_not_awaited()


class TestTopicSegmentationBackfill:
    @pytest.mark.asyncio
    async def test_start_backfill_returns_running_error(self) -> None:
        scheduler = MagicMock()
        scheduler.is_running = True
        stub = _make_stub(scheduler=scheduler)

        result = await stub.start_backfill()

        assert result["status"] == "error"
        assert "运行" in result["message"]

    @pytest.mark.asyncio
    async def test_get_backfill_status_returns_progress(self) -> None:
        scheduler = MagicMock()
        scheduler.progress = {"status": "running", "processed": 12}
        stub = _make_stub(scheduler=scheduler)

        result = await stub.get_backfill_status()

        assert result["status"] == "ok"
        assert result["data"]["processed"] == 12


class TestCandidateReuseStatus:
    @pytest.mark.asyncio
    async def test_get_config_includes_candidate_reuse_ready(self) -> None:
        """catalog ready 时返回完整 candidate_reuse 对象。"""
        stub = _make_stub(catalog_gen=1, dirty_count=5)

        result = await stub.get_topic_segmentation_config()

        assert result["status"] == "ok"
        data = result["data"]
        assert "candidate_reuse" in data

        reuse = data["candidate_reuse"]
        assert reuse["catalog_status"] == "ready"
        assert reuse["dirty_count"] == 5
        assert reuse["scope_buckets"] == {"small": 10, "medium": 5, "large": 2}
        assert reuse["aggregated_metrics"] is None

    @pytest.mark.asyncio
    async def test_get_config_includes_candidate_reuse_degraded(self) -> None:
        """catalog degraded (-1) 时返回 degraded 状态。"""
        stub = _make_stub(catalog_gen=-1, dirty_count=0)

        result = await stub.get_topic_segmentation_config()

        assert result["status"] == "ok"
        reuse = result["data"]["candidate_reuse"]
        assert reuse["catalog_status"] == "degraded"

    @pytest.mark.asyncio
    async def test_get_config_includes_candidate_reuse_unavailable(self) -> None:
        """catalog None 时返回 unavailable 状态。"""
        stub = _make_stub(catalog_gen=None, dirty_count=0)

        result = await stub.get_topic_segmentation_config()

        assert result["status"] == "ok"
        reuse = result["data"]["candidate_reuse"]
        assert reuse["catalog_status"] == "unavailable"
