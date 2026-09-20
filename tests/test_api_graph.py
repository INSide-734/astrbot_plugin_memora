"""core/api/graph_api.py — GraphApiMixin 测试。"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fact_evidence_helpers import fact_evidence


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    mock.get_json = AsyncMock(return_value={})
    return mock


def _canonical_graph_metadata() -> dict[str, str]:
    """返回最小 canonical 图来源边界。"""
    return {
        "scope_key": "session:canonical",
        "privacy_level": "confidential",
        "revision_token": "revision-canonical",
    }


def _canonical_graph_fact_metadata() -> dict[str, Any]:
    """返回聚焦图读取门要求的事实用户证据（与边界字段分注入）。"""

    fact = "匿名事实"
    return {"key_facts": [fact], "fact_source_evidence": fact_evidence([fact])}


def _make_mixin(
    plugin_ready: bool = True,
    graph_store=None,
    *,
    canonical_metadata: dict[str, Any] | None = None,
    memory_exists: bool = True,
):
    """创建带有 GraphApiMixin 方法和模拟依赖的测试替身。"""

    from core.platform.transport.page_api.graph_api import GraphApiMixin

    engine = MagicMock()
    engine.get_statistics = AsyncMock(return_value={"total": 0})
    engine.get_memory = AsyncMock(
        return_value=(
            {
                "id": 42,
                "metadata": {
                    **(
                        canonical_metadata
                        if canonical_metadata is not None
                        else _canonical_graph_metadata()
                    ),
                    # 聚焦图读取门与管理员画布同义：来源还必须有用户事实证据。
                    **_canonical_graph_fact_metadata(),
                },
            }
            if memory_exists
            else None
        )
    )

    class Stub(GraphApiMixin):
        """继承真实 mixin，只替换宿主提供的响应、就绪与视图辅助方法。"""

        def __init__(self) -> None:
            self._memory_engine = engine

        def _ok(self, data):
            from core.platform.transport.page_api.response_utils import ok_response

            return ok_response(data)

        def _error(self, msg):
            from core.platform.transport.page_api.response_utils import error_response

            return error_response(msg)

        async def _ensure_plugin_ready(self):
            if not plugin_ready:
                return None, self._error("plugin not ready")
            return {"memory_engine": self._memory_engine}, None

        def _get_graph_store(self, _engine):
            return graph_store

        def _build_graph_view_payload(self, snapshot, stats, **kwargs):
            result = {
                "nodes": snapshot.get("nodes", []),
                "edges": snapshot.get("edges", []),
                "stats": stats,
            }
            result.update(kwargs)
            return result

    return Stub()


class TestGraphApiValidation:
    """验证图谱 API 的参数校验和插件未就绪处理。"""

    @pytest.mark.asyncio
    async def test_search_graph_plugin_not_ready(self) -> None:
        req = _mock_request()
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=False)
            result = await mixin.search_graph()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_graph_overview_plugin_not_ready(self) -> None:
        req = _mock_request()
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=False)
            result = await mixin.get_graph_overview()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_query_graph_plugin_not_ready(self) -> None:
        req = _mock_request()
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=False)
            result = await mixin.query_graph()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", [["bad-query"], [], None, 0, False, ""])
    async def test_query_graph_rejects_non_object_json_payload(self, payload) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=payload)
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=True)
            result = await mixin.query_graph()
        assert result["status"] == "error"
        assert "JSON" in result["message"]


class TestGraphApiBoundary:
    """验证页面图查询的 canonical 聚焦边界与无 ID 的管理员总览分派。"""

    @pytest.mark.asyncio
    async def test_overview_returns_admin_canvas_without_memory_id(self) -> None:
        req = _mock_request(session_id="client-session", persona_id="client-persona")
        graph_store = MagicMock()
        graph_store.get_admin_canvas_snapshot = AsyncMock(
            return_value={"nodes": [{"id": 1, "label": "甲"}], "edges": []}
        )
        with patch("core.platform.transport.page_api.graph_api.request", req):
            result = await _make_mixin(graph_store=graph_store).get_graph_overview()

        assert result["status"] == "ok"
        assert result["data"]["mode"] == "overview"
        assert [node["id"] for node in result["data"]["nodes"]] == [1]
        assert result["data"]["filters"] == {
            "session_id": "client-session",
            "persona_id": "client-persona",
        }
        # 管理员读取只接受展示过滤，不信任任何客户端 scope/revision 字段。
        assert graph_store.get_admin_canvas_snapshot.await_args.kwargs == {
            "session_id": "client-session",
            "persona_id": "client-persona",
            "oldest_timestamp": None,
            "newest_timestamp": None,
        }

    @pytest.mark.asyncio
    async def test_get_search_without_memory_id_uses_admin_canvas(self) -> None:
        req = _mock_request(query="alice")
        graph_store = MagicMock()
        graph_store.get_admin_canvas_snapshot = AsyncMock(
            return_value={
                "nodes": [
                    {"id": 1, "label": "Alice"},
                    {"id": 2, "label": "Bob"},
                ],
                "edges": [{"source": 2, "target": 2}],
            }
        )
        with patch("core.platform.transport.page_api.graph_api.request", req):
            result = await _make_mixin(graph_store=graph_store).search_graph()

        assert result["status"] == "ok"
        assert result["data"]["mode"] == "query"
        assert result["data"]["query"] == "alice"
        assert [node["label"] for node in result["data"]["nodes"]] == ["Alice"]
        assert result["data"]["edges"] == []
        graph_store.get_admin_canvas_snapshot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_post_query_ignores_client_boundary_fields(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "query": "client-query",
                "scope_key": "client-scope",
                "privacy_level": "public",
                "revision_token": "client-revision",
            }
        )
        graph_store = MagicMock()
        graph_store.get_admin_canvas_snapshot = AsyncMock(
            return_value={"nodes": [], "edges": []}
        )
        with patch("core.platform.transport.page_api.graph_api.request", req):
            result = await _make_mixin(graph_store=graph_store).query_graph()

        assert result["status"] == "ok"
        assert result.get("code") is None
        call_kwargs = graph_store.get_admin_canvas_snapshot.await_args.kwargs
        assert set(call_kwargs) == {
            "session_id",
            "persona_id",
            "oldest_timestamp",
            "newest_timestamp",
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "canonical_metadata",
        [None, {}, {"scope_key": "session:canonical", "privacy_level": "shared"}],
    )
    async def test_memory_focus_missing_or_invalid_boundary_fails_closed(
        self,
        canonical_metadata: dict[str, str] | None,
    ) -> None:
        graph_store = MagicMock()
        mixin = _make_mixin(
            graph_store=graph_store,
            canonical_metadata=canonical_metadata,
            memory_exists=canonical_metadata is not None,
        )

        result = await mixin._query_graph_impl({"memory_id": 42})

        assert result["code"] == "graph_boundary_required"
        assert graph_store.mock_calls == []

    @pytest.mark.asyncio
    async def test_memory_focus_hides_canonical_load_failure(self) -> None:
        graph_store = MagicMock()
        mixin = _make_mixin(graph_store=graph_store)
        mixin._memory_engine.get_memory.side_effect = RuntimeError("source-secret")

        result = await mixin._query_graph_impl({"memory_id": 42})

        assert result["code"] == "graph_boundary_required"
        assert "source-secret" not in result["message"]
        assert graph_store.mock_calls == []

    @pytest.mark.asyncio
    async def test_memory_focus_uses_canonical_boundary_and_keeps_filters(self) -> None:
        canonical_metadata = _canonical_graph_metadata()
        graph_store = MagicMock()
        graph_store.get_subgraph_for_memories = AsyncMock(
            return_value={
                "nodes": [
                    {"id": 1, "label": "近期", "type": "fact"},
                    {"id": 2, "label": "旧节点", "type": "fact"},
                ],
                "edges": [
                    {"source": 1, "target": 1, "timestamp": 999_000.0},
                    {"source": 2, "target": 2, "timestamp": 900_000.0},
                ],
                "entries": [],
                "memories": [],
            }
        )
        mixin = _make_mixin(
            graph_store=graph_store,
            canonical_metadata=canonical_metadata,
        )

        with patch(
            "core.platform.transport.page_api.graph_api.time.time",
            return_value=1_000_000.0,
        ):
            result = await mixin._query_graph_impl(
                {
                    "memory_id": 42,
                    "scope_key": "client-scope",
                    "privacy_level": "public",
                    "revision_token": "client-revision",
                    "session_id": "client-session",
                    "persona_id": "client-persona",
                    "time_start_hours": 0,
                    "time_end_hours": 1,
                    "limit_entries": 20,
                    "limit_nodes": 30,
                    "limit_edges": 40,
                }
            )

        assert result["status"] == "ok"
        assert [node["id"] for node in result["data"]["nodes"]] == [1]
        assert [edge["source"] for edge in result["data"]["edges"]] == [1]
        assert result["data"]["filters"] == {
            "session_id": "client-session",
            "persona_id": "client-persona",
        }
        call = graph_store.get_subgraph_for_memories.await_args
        assert call.args == ([42],)
        assert call.kwargs["boundary"].as_params() == canonical_metadata
        assert call.kwargs["limit_entries"] == 20
        assert call.kwargs["limit_nodes"] == 30
        assert call.kwargs["limit_edges"] == 40

    @pytest.mark.asyncio
    async def test_memory_focus_rejects_invalid_memory_id(self) -> None:
        graph_store = MagicMock()
        result = await _make_mixin(graph_store=graph_store)._query_graph_impl(
            {"memory_id": True}
        )

        assert result["status"] == "error"
        assert "memory_id 必须是整数" in result["message"]
        assert graph_store.mock_calls == []

    @pytest.mark.asyncio
    async def test_query_matches_only_inside_focused_memory(self) -> None:
        store = MagicMock()
        store.get_subgraph_for_memories = AsyncMock(
            return_value={
                "nodes": [
                    {"id": "a", "label": "Alice"},
                    {"id": "b", "label": "Coffee"},
                    {"id": "c", "label": "Other"},
                ],
                "edges": [
                    {"source": "a", "target": "b"},
                    {"source": "b", "target": "c"},
                ],
                "entries": [{"content": "Alice likes coffee", "node_ids": ["a", "b"]}],
                "memories": [{"memory_id": 42}],
            }
        )
        api = _make_mixin(graph_store=store)
        result = await api._query_graph_impl({"memory_id": 42, "query": "ALICE"})
        assert [node["id"] for node in result["data"]["nodes"]] == ["a", "b"]
        assert result["data"]["edges"] == [{"source": "a", "target": "b"}]
        empty = await api._query_graph_impl({"memory_id": 42, "query": "unmatched"})
        assert empty["data"]["nodes"] == []
        assert empty["data"]["edges"] == []

    @pytest.mark.asyncio
    async def test_focus_reads_current_canonical_revision(self, tmp_db_path) -> None:
        from core.features.memory.graph.domain.models import (
            GraphBoundary,
            GraphEntry,
            GraphNode,
        )
        from core.features.memory.graph.infrastructure.graph_store import GraphStore

        store = GraphStore(tmp_db_path)
        await store.initialize()
        metadata = _canonical_graph_metadata()
        revision = "2026-09-16T12:00:00+00:00"
        boundary = GraphBoundary(
            metadata["scope_key"], metadata["privacy_level"], revision
        )
        node = GraphNode("topic", "Current", "current")
        entry = GraphEntry(
            "current",
            42,
            None,
            None,
            "topic",
            "Current fact",
            node_keys=[node.node_key],
        )
        await store.replace_memory_graph(42, [node], [], [entry], boundary=boundary)
        api = _make_mixin(graph_store=store, canonical_metadata=metadata)
        api._memory_engine.get_memory.return_value["updated_at"] = revision
        result = await api._query_graph_impl({"memory_id": 42})
        assert result["status"] == "ok"
        assert [node["label"] for node in result["data"]["nodes"]] == ["Current"]


class TestGraphAdminCanvas:
    """验证无 memory ID 时的管理员总览、标签搜索与失败/取消契约。"""

    @pytest.mark.asyncio
    async def test_admin_canvas_disabled_when_graph_store_missing(self) -> None:
        result = await _make_mixin(graph_store=None)._query_graph_impl({})

        assert result["status"] == "ok"
        assert result["data"]["enabled"] is False
        assert result["data"]["nodes"] == []

    @pytest.mark.asyncio
    async def test_admin_canvas_failure_returns_internal_error_envelope(self) -> None:
        graph_store = MagicMock()
        graph_store.get_admin_canvas_snapshot = AsyncMock(
            side_effect=RuntimeError("source-secret")
        )

        result = await _make_mixin(graph_store=graph_store)._query_graph_impl({})

        assert result["status"] == "error"
        assert result["code"] == "internal_error"
        assert "source-secret" not in result["message"]

    @pytest.mark.asyncio
    async def test_admin_canvas_cancellation_propagates(self) -> None:
        graph_store = MagicMock()
        graph_store.get_admin_canvas_snapshot = AsyncMock(
            side_effect=asyncio.CancelledError()
        )
        req = _mock_request()
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(graph_store=graph_store)
            with pytest.raises(asyncio.CancelledError):
                await mixin.query_graph()
            with pytest.raises(asyncio.CancelledError):
                await mixin.get_graph_overview()

        assert graph_store.get_admin_canvas_snapshot.await_count == 2
