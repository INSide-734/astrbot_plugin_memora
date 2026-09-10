"""core/api/graph_api.py — GraphApiMixin 测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    mock.get_json = AsyncMock(return_value={})
    return mock


def _make_mixin(
    plugin_ready: bool = True, graph_store=None, search_memories_result=None
):
    """创建带有 GraphApiMixin 方法和模拟依赖的测试替身。"""

    from core.platform.transport.page_api.graph_api import GraphApiMixin

    class Stub:
        search_graph = GraphApiMixin.search_graph
        query_graph = GraphApiMixin.query_graph
        get_graph_overview = GraphApiMixin.get_graph_overview
        _query_graph_impl = GraphApiMixin._query_graph_impl

        def _ok(self, data):
            from core.platform.transport.page_api.response_utils import ok_response

            return ok_response(data)

        def _error(self, msg):
            from core.platform.transport.page_api.response_utils import error_response

            return error_response(msg)

        async def _ensure_plugin_ready(self):
            if not plugin_ready:
                return None, self._error("plugin not ready")
            engine = MagicMock()
            engine.get_statistics = AsyncMock(return_value={"total": 0})
            engine.search_memories = AsyncMock(
                return_value=search_memories_result or []
            )
            return {"memory_engine": engine}, None

        def _get_graph_store(self, engine):
            return graph_store

        def _build_graph_view_payload(self, snapshot, stats, **kwargs):
            result = {
                "nodes": snapshot.get("nodes", []),
                "edges": snapshot.get("edges", []),
                "stats": stats,
            }
            result.update(kwargs)
            return result

        def _tokenize_graph_query(self, text):
            return text.split() if text else []

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
    async def test_query_graph_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-query"])
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=True)
            result = await mixin.query_graph()
        assert result["status"] == "error"
        assert "JSON" in result["message"]


class TestGraphApiHappyPath:
    """验证图谱 API 使用模拟存储返回稳定响应。"""

    @pytest.mark.asyncio
    async def test_overview_with_graph_store_returns_ok(self) -> None:
        req = _mock_request()
        mock_gs = MagicMock()
        mock_gs.get_graph_snapshot = AsyncMock(
            return_value={"nodes": [], "edges": [], "entries": [], "memories": []}
        )
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=True, graph_store=mock_gs)
            result = await mixin.get_graph_overview()
        assert result["status"] == "ok"
        assert "nodes" in result["data"]
        assert "edges" in result["data"]
        assert "stats" in result["data"]
        assert result["data"].get("enabled") is True
        assert result["data"].get("mode") == "overview"

    @pytest.mark.asyncio
    async def test_overview_without_graph_store_returns_empty(self) -> None:
        req = _mock_request()
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=True, graph_store=None)
            result = await mixin.get_graph_overview()
        assert result["status"] == "ok"
        assert result["data"].get("enabled") is False
        assert result["data"]["nodes"] == []

    @pytest.mark.asyncio
    async def test_overview_with_session_filters(self) -> None:
        req = _mock_request(session_id="s1", persona_id="p1")
        mock_gs = MagicMock()
        mock_gs.get_graph_snapshot = AsyncMock(
            return_value={"nodes": [], "edges": [], "entries": [], "memories": []}
        )
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=True, graph_store=mock_gs)
            result = await mixin.get_graph_overview()
        assert result["status"] == "ok"
        assert "filters" in result["data"]

    @pytest.mark.asyncio
    async def test_query_graph_no_payload_falls_to_overview(self) -> None:
        req = _mock_request()
        mock_gs = MagicMock()
        mock_gs.get_graph_snapshot = AsyncMock(
            return_value={"nodes": [], "edges": [], "entries": [], "memories": []}
        )
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=True, graph_store=mock_gs)
            result = await mixin.query_graph()
        assert result["status"] == "ok"
        assert result["data"].get("mode") == "overview"

    @pytest.mark.asyncio
    async def test_empty_canvas_search_uses_lightweight_snapshot(self) -> None:
        """画布参数只替换默认全量读取，且响应不携带内部节点字段。"""
        req = _mock_request(canvas="1")
        mock_gs = MagicMock()
        mock_gs.get_canvas_snapshot = AsyncMock(
            return_value={
                "nodes": [
                    {
                        "id": 7,
                        "key": "person:qq:7",
                        "canonical_value": "qq:7",
                        "label": "QQ:7",
                        "type": "person",
                        "entry_count": 2,
                        "memory_count": 1,
                        "degree": 3,
                        "weight": 3.8,
                    }
                ],
                "edges": [
                    {
                        "id": 8,
                        "source": 7,
                        "target": 7,
                        "type": "related",
                        "weight": 1.0,
                        "timestamp": 100.0,
                        "metadata": {"internal": True},
                    }
                ],
            }
        )

        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=True, graph_store=mock_gs)
            result = await mixin.search_graph()

        mock_gs.get_canvas_snapshot.assert_awaited_once_with(
            session_id=None,
            persona_id=None,
        )
        node = result["data"]["nodes"][0]
        edge = result["data"]["edges"][0]
        assert "key" not in node
        assert "canonical_value" not in node
        assert "metadata" not in edge

    @pytest.mark.asyncio
    async def test_canvas_search_converts_time_range_to_unix_seconds(self) -> None:
        """画布请求把相对小时范围转换为绝对 Unix 秒再交给存储层。"""
        req = _mock_request(canvas="1", time_start_hours="24", time_end_hours="168")
        mock_gs = MagicMock()
        mock_gs.get_canvas_snapshot = AsyncMock(return_value={"nodes": [], "edges": []})
        mixin = _make_mixin(plugin_ready=True, graph_store=mock_gs)

        with (
            patch("core.platform.transport.page_api.graph_api.request", req),
            patch(
                "core.platform.transport.page_api.graph_api.time.time",
                return_value=1_000_000.0,
            ),
        ):
            result = await mixin.search_graph()

        assert result["status"] == "ok"
        mock_gs.get_canvas_snapshot.assert_awaited_once_with(
            session_id=None,
            persona_id=None,
            oldest_timestamp=395_200.0,
            newest_timestamp=913_600.0,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "time_params",
        [
            {"time_end_hours": "0"},
            {"time_end_hours": "-1"},
            {"time_end_hours": "721"},
            {"time_end_hours": "1.5"},
            {"time_end_hours": "invalid"},
            {"time_start_hours": "2", "time_end_hours": "1"},
            {"time_start_hours": "721", "time_end_hours": "720"},
            {"time_start_hours": "1"},
        ],
    )
    async def test_canvas_search_rejects_invalid_time_range(
        self,
        time_params: dict[str, str],
    ) -> None:
        """非法、越界或不完整的时间范围返回稳定参数错误。"""
        req = _mock_request(canvas="1", **time_params)
        mock_gs = MagicMock()
        mock_gs.get_canvas_snapshot = AsyncMock(return_value={"nodes": [], "edges": []})
        mixin = _make_mixin(plugin_ready=True, graph_store=mock_gs)

        with patch("core.platform.transport.page_api.graph_api.request", req):
            result = await mixin.search_graph()

        assert result["status"] == "error"
        assert "时间范围" in result["message"]
        mock_gs.get_canvas_snapshot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_memory_focus_search_applies_time_range_to_limited_snapshot(
        self,
    ) -> None:
        """记忆聚焦查询也只返回当前时间范围内的边和节点。"""
        mock_gs = MagicMock()
        mock_gs.get_subgraph_for_memories = AsyncMock(
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
        mixin = _make_mixin(plugin_ready=True, graph_store=mock_gs)

        with patch(
            "core.platform.transport.page_api.graph_api.time.time",
            return_value=1_000_000.0,
        ):
            result = await mixin._query_graph_impl(
                {"memory_id": 42, "time_start_hours": 0, "time_end_hours": 1}
            )

        assert result["status"] == "ok"
        assert [node["id"] for node in result["data"]["nodes"]] == [1]
        assert [edge["source"] for edge in result["data"]["edges"]] == [1]

    @pytest.mark.asyncio
    async def test_search_graph_with_query_strips_whitespace(self) -> None:
        req = _mock_request(query="  hello world  ")
        mock_gs = MagicMock()
        mock_gs.get_graph_snapshot = AsyncMock(
            return_value={"nodes": [], "edges": [], "entries": [], "memories": []}
        )
        mock_gs.search_nodes_by_tokens = AsyncMock(return_value=[])
        mock_gs.get_entries_for_node_ids = AsyncMock(return_value=[])
        mock_gs.get_subgraph_for_memories = AsyncMock(
            return_value={"nodes": [], "edges": [], "entries": [], "memories": []}
        )
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(
                plugin_ready=True, graph_store=mock_gs, search_memories_result=[]
            )
            result = await mixin.search_graph()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_error_response_has_message(self) -> None:
        req = _mock_request()
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=False)
            result = await mixin.query_graph()
        assert result["status"] == "error"
        assert "message" in result


class TestGraphApiEdgeCases:
    """补充参数校验、错误路径和分支覆盖。"""

    @pytest.mark.asyncio
    async def test_search_graph_with_memory_id_as_integer(self) -> None:
        """有效 memory_id 会传递给 _query_graph_impl。"""
        req = _mock_request(query="test", memory_id="42")
        mock_gs = MagicMock()
        mock_gs.get_subgraph_for_memories = AsyncMock(
            return_value={
                "nodes": [],
                "edges": [],
                "entries": [],
                "memories": [],
            }
        )
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=True, graph_store=mock_gs)
            result = await mixin.search_graph()
        assert result["status"] == "ok"
        assert result["data"].get("mode") == "memory_focus"
        assert result["data"].get("memory_id") == 42

    @pytest.mark.asyncio
    async def test_search_graph_invalid_memory_id(self) -> None:
        """非整数 memory_id 返回错误。"""
        req = _mock_request(query="test", memory_id="not_a_number")
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=True)
            result = await mixin.search_graph()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_graph_overview_invalid_params(self) -> None:
        """非整数概览限制参数返回错误。"""
        req = _mock_request(limit_memories="bad")
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=True)
            result = await mixin.get_graph_overview()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_graph_overview_with_filters(self) -> None:
        """概览接口将 session_id 和 persona_id 传递给图存储。"""
        req = _mock_request(
            session_id="sess_1",
            persona_id="pers_1",
            limit_memories="5",
            limit_entries="20",
            limit_nodes="30",
            limit_edges="40",
        )
        mock_gs = MagicMock()
        mock_gs.get_graph_snapshot = AsyncMock(
            return_value={
                "nodes": [{"id": 1}],
                "edges": [],
                "entries": [],
                "memories": [],
            }
        )
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=True, graph_store=mock_gs)
            result = await mixin.get_graph_overview()
        assert result["status"] == "ok"
        snapshot_call = mock_gs.get_graph_snapshot.call_args
        assert snapshot_call.kwargs["session_id"] == "sess_1"
        assert snapshot_call.kwargs["persona_id"] == "pers_1"
        assert snapshot_call.kwargs["limit_memories"] == 5
        assert snapshot_call.kwargs["limit_entries"] == 20

    @pytest.mark.asyncio
    async def test_query_graph_impl_without_graph_store(self) -> None:
        """_query_graph_impl returns empty when graph_store is None."""
        mixin = _make_mixin(plugin_ready=True, graph_store=None)
        result = await mixin._query_graph_impl({"query": "test"})
        assert result["status"] == "ok"
        assert result["data"].get("enabled") is False
        assert result["data"].get("mode") == "query"

    @pytest.mark.asyncio
    async def test_query_graph_impl_with_memory_id_focus(self) -> None:
        """_query_graph_impl with memory_id calls get_subgraph_for_memories."""
        mock_gs = MagicMock()
        mock_gs.get_subgraph_for_memories = AsyncMock(
            return_value={
                "nodes": [{"id": 1}],
                "edges": [],
                "entries": [],
                "memories": [],
            }
        )
        mixin = _make_mixin(plugin_ready=True, graph_store=mock_gs)
        result = await mixin._query_graph_impl({"memory_id": 42, "query": ""})
        assert result["status"] == "ok"
        assert result["data"].get("mode") == "memory_focus"
        assert result["data"].get("memory_id") == 42

    @pytest.mark.asyncio
    async def test_query_graph_impl_with_search(self) -> None:
        """_query_graph_impl with query runs search_memories + tokens + subgraph."""
        from core.features.retrieval.rrf_fusion import HybridResult

        mock_gs = MagicMock()
        mock_gs.search_nodes_by_tokens = AsyncMock(return_value=[])
        mock_gs.get_entries_for_node_ids = AsyncMock(return_value=[])
        mock_gs.get_subgraph_for_memories = AsyncMock(
            return_value={
                "nodes": [],
                "edges": [],
                "entries": [],
                "memories": [],
            }
        )
        sr = HybridResult(
            doc_id=1,
            final_score=0.9,
            rrf_score=0.9,
            bm25_score=0.8,
            vector_score=None,
            content="result",
            metadata={"k": "v"},
            score_breakdown={"k1": 0.5},
        )
        mixin = _make_mixin(
            plugin_ready=True, graph_store=mock_gs, search_memories_result=[sr]
        )
        result = await mixin._query_graph_impl({"query": "hello", "session_id": "s1"})
        assert result["status"] == "ok"
        assert "retrieval_items" in result["data"]

    @pytest.mark.asyncio
    async def test_query_graph_impl_deduplicates_memory_ids_and_filters_scores(
        self,
    ) -> None:
        """搜索和节点命中会去重 memory_id，并只保留数字分数。"""
        from core.features.retrieval.rrf_fusion import HybridResult

        mock_gs = MagicMock()
        mock_gs.search_nodes_by_tokens = AsyncMock(
            return_value=[
                {"id": 10, "node_value": "node-a"},
                {"id": 11, "node_value": "node-b"},
            ]
        )
        mock_gs.get_entries_for_node_ids = AsyncMock(
            return_value=[
                {"source_memory_id": 1},
                {"source_memory_id": 99},
                {"source_memory_id": 99},
            ]
        )
        mock_gs.get_subgraph_for_memories = AsyncMock(
            return_value={
                "nodes": [],
                "edges": [],
                "entries": [],
                "memories": [],
            }
        )

        sr1 = HybridResult(
            doc_id=1,
            final_score=0.9,
            rrf_score=0.9,
            bm25_score=0.8,
            vector_score=None,
            content="result-1",
            metadata={"m": 1},
            score_breakdown={"doc_kw": 0.5, "bad": "skip"},
        )
        sr2 = HybridResult(
            doc_id=1,
            final_score=0.7,
            rrf_score=0.7,
            bm25_score=None,
            vector_score=0.2,
            content="result-1-dup",
            metadata={"m": 2},
            score_breakdown={"doc_vec": 0.25, "nested": {"x": 1}},
        )

        mixin = _make_mixin(
            plugin_ready=True,
            graph_store=mock_gs,
            search_memories_result=[sr1, sr2],
        )
        result = await mixin._query_graph_impl({"query": "hello world"})

        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_query_graph_impl_tolerates_malformed_score_breakdown_results(
        self,
    ) -> None:
        """单条损坏的 score_breakdown 不应使整个查询失败。"""
        from types import SimpleNamespace

        from core.features.retrieval.rrf_fusion import HybridResult

        mock_gs = MagicMock()
        mock_gs.search_nodes_by_tokens = AsyncMock(return_value=[])
        mock_gs.get_entries_for_node_ids = AsyncMock(return_value=[])
        mock_gs.get_subgraph_for_memories = AsyncMock(
            return_value={"nodes": [], "edges": [], "entries": [], "memories": []}
        )

        broken = SimpleNamespace(
            doc_id=1,
            final_score=0.9,
            rrf_score=0.9,
            bm25_score=0.8,
            vector_score=0.7,
            content="broken",
            metadata={},
            score_breakdown=["not-a-dict"],
        )
        good = HybridResult(
            doc_id=2,
            final_score=0.7,
            rrf_score=0.7,
            bm25_score=0.6,
            vector_score=0.5,
            content="good",
            metadata={"ok": True},
            score_breakdown={"rrf": 0.7},
        )

        mixin = _make_mixin(
            plugin_ready=True,
            graph_store=mock_gs,
            search_memories_result=[broken, good],
        )
        result = await mixin._query_graph_impl({"query": "hello"})

        assert result["status"] == "ok"
        assert result["data"]["retrieval_items"] == [
            {
                "memory_id": 1,
                "content": "broken",
                "metadata": {},
                "final_score": 0.9,
                "rrf_score": 0.9,
                "bm25_score": 0.8,
                "vector_score": 0.7,
                "score_breakdown": {},
            },
            {
                "memory_id": 2,
                "content": "good",
                "metadata": {"ok": True},
                "final_score": 0.7,
                "rrf_score": 0.7,
                "bm25_score": 0.6,
                "vector_score": 0.5,
                "score_breakdown": {"rrf": 0.7},
            },
        ]
        mock_gs.get_subgraph_for_memories.assert_awaited_once_with(
            [1, 2], limit_entries=40, limit_nodes=56, limit_edges=96
        )
        assert result["data"]["matched_node_ids"] == []

    @pytest.mark.asyncio
    async def test_query_graph_impl_skips_malformed_search_and_graph_hits(self) -> None:
        """损坏的检索项和图命中项应被忽略，而不是使查询失败。"""
        from core.features.retrieval.rrf_fusion import HybridResult

        mock_gs = MagicMock()
        mock_gs.search_nodes_by_tokens = AsyncMock(
            return_value=[
                {"id": "10", "node_value": "node-a"},
                {"id": "bad"},
                "bad-node-hit",
            ]
        )
        mock_gs.get_entries_for_node_ids = AsyncMock(
            return_value=[
                {"source_memory_id": "99"},
                {"source_memory_id": "oops"},
                "bad-entry-hit",
            ]
        )
        mock_gs.get_subgraph_for_memories = AsyncMock(
            return_value={
                "nodes": [],
                "edges": [],
                "entries": [],
                "memories": [],
            }
        )

        good = HybridResult(
            doc_id=1,
            final_score=0.9,
            rrf_score=0.8,
            bm25_score=0.7,
            vector_score=0.6,
            content="good",
            metadata={"kind": "ok"},
            score_breakdown={"doc_kw": 0.5},
        )
        bad_doc_id = HybridResult(
            doc_id="oops",
            final_score=0.7,
            rrf_score=0.6,
            bm25_score=0.5,
            vector_score=0.4,
            content="bad-doc-id",
            metadata={"kind": "bad"},
            score_breakdown={"doc_kw": 0.1},
        )
        bad_score = HybridResult(
            doc_id=3,
            final_score="nan?",
            rrf_score=0.3,
            bm25_score=0.2,
            vector_score=0.1,
            content="bad-score",
            metadata={"kind": "bad"},
            score_breakdown={"doc_kw": 0.2},
        )

        mixin = _make_mixin(
            plugin_ready=True,
            graph_store=mock_gs,
            search_memories_result=[good, bad_doc_id, bad_score],
        )
        result = await mixin._query_graph_impl({"query": "hello world"})

        assert result["status"] == "ok"
        assert result["data"]["matched_node_ids"] == [10]
        assert result["data"]["retrieval_items"] == [
            {
                "memory_id": 1,
                "content": "good",
                "metadata": {"kind": "ok"},
                "final_score": 0.9,
                "rrf_score": 0.8,
                "bm25_score": 0.7,
                "vector_score": 0.6,
                "score_breakdown": {"doc_kw": 0.5},
            }
        ]
        mock_gs.get_subgraph_for_memories.assert_awaited_once_with(
            [1, 99], limit_entries=40, limit_nodes=56, limit_edges=96
        )

    @pytest.mark.asyncio
    async def test_query_graph_impl_clamps_limit_parameters(self) -> None:
        """查询限制参数在传给图存储前会被约束到安全范围。"""
        mock_gs = MagicMock()
        mock_gs.get_graph_snapshot = AsyncMock(
            return_value={
                "nodes": [],
                "edges": [],
                "entries": [],
                "memories": [],
            }
        )

        mixin = _make_mixin(plugin_ready=True, graph_store=mock_gs)
        result = await mixin._query_graph_impl(
            {
                "limit_memories": 999,
                "limit_entries": 1,
                "limit_nodes": 999,
                "limit_edges": 1,
            }
        )

        assert result["status"] == "ok"
        mock_gs.get_graph_snapshot.assert_awaited_once_with(
            session_id=None,
            persona_id=None,
            limit_memories=24,
            limit_entries=12,
            limit_nodes=80,
            limit_edges=12,
        )

    @pytest.mark.asyncio
    async def test_query_graph_impl_with_node_search(self) -> None:
        """_query_graph_impl with tokens triggers search_nodes_by_tokens."""
        mock_gs = MagicMock()
        mock_gs.search_nodes_by_tokens = AsyncMock(
            return_value=[{"id": 10, "node_value": "testnode"}]
        )
        mock_gs.get_entries_for_node_ids = AsyncMock(
            return_value=[{"source_memory_id": 99}]
        )
        mock_gs.get_subgraph_for_memories = AsyncMock(
            return_value={
                "nodes": [],
                "edges": [],
                "entries": [],
                "memories": [],
            }
        )
        mixin = _make_mixin(
            plugin_ready=True, graph_store=mock_gs, search_memories_result=[]
        )
        result = await mixin._query_graph_impl({"query": "hello world"})
        assert result["status"] == "ok"
        mock_gs.search_nodes_by_tokens.assert_called_once()
        mock_gs.get_entries_for_node_ids.assert_called_once()
        assert "matched_node_ids" in result["data"]

    @pytest.mark.asyncio
    async def test_query_graph_impl_invalid_params(self) -> None:
        """_query_graph_impl with invalid limit params returns error."""
        mixin = _make_mixin(plugin_ready=True)
        result = await mixin._query_graph_impl({"limit_memories": "bad"})
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_query_graph_impl_invalid_memory_id(self) -> None:
        """_query_graph_impl with invalid memory_id returns error."""
        mock_gs = MagicMock()
        mixin = _make_mixin(plugin_ready=True, graph_store=mock_gs)
        result = await mixin._query_graph_impl({"memory_id": "abc"})
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_query_graph_impl_rejects_boolean_memory_id(self) -> None:
        """JSON 布尔值不能被强制转换为 memory_id。"""
        mock_gs = MagicMock()
        mixin = _make_mixin(plugin_ready=True, graph_store=mock_gs)
        result = await mixin._query_graph_impl({"memory_id": True})
        assert result["status"] == "error"
        assert "memory_id 必须是整数" in result["message"]

    @pytest.mark.asyncio
    async def test_get_graph_overview_empty_session_persona(self) -> None:
        """空 session_id 和 persona_id 会规范化为 None。"""
        req = _mock_request(session_id="", persona_id="")
        mock_gs = MagicMock()
        mock_gs.get_graph_snapshot = AsyncMock(
            return_value={"nodes": [], "edges": [], "entries": [], "memories": []}
        )
        with patch("core.platform.transport.page_api.graph_api.request", req):
            mixin = _make_mixin(plugin_ready=True, graph_store=mock_gs)
            result = await mixin.get_graph_overview()
        assert result["status"] == "ok"
