"""MemoryEngine CRUD Mixin 测试 — 记忆的添加/获取/更新/删除。"""

from __future__ import annotations

import inspect
import json
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

import core.monitoring.metrics as monitoring_metrics
from core.managers.memory_engine import MemoryEngine
from core.models.recall_strategy import RecallStrategy


def _metric_sample_value(
    sample_name: str, labels: dict[str, str] | None = None
) -> float:
    labels = labels or {}
    for metric in monitoring_metrics.REGISTRY.collect():
        for sample in metric.samples:
            if sample.name != sample_name:
                continue
            if all(sample.labels.get(key) == value for key, value in labels.items()):
                return float(sample.value)
    return 0.0


class TestMemoryEngineGetMemory:
    """Tests for get_memory method."""

    @pytest.mark.asyncio
    async def test_get_memory_returns_none_when_not_found(self) -> None:
        mock_faiss = MagicMock()
        mock_faiss.document_storage = MagicMock()
        # Return empty list — doc not found
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine.get_memory(42)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_memory_returns_doc_when_found(self) -> None:
        mock_faiss = MagicMock()
        mock_faiss.document_storage = MagicMock()
        doc = {
            "id": 42,
            "text": "hello world",
            "metadata": {"importance": 0.5, "session_id": "s1"},
        }
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[doc])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine.get_memory(42)
        assert result is not None
        assert result["id"] == 42
        assert result["text"] == "hello world"
        assert result["metadata"] == doc["metadata"]

    @pytest.mark.asyncio
    async def test_get_memory_returns_none_on_exception(self) -> None:
        mock_faiss = MagicMock()
        mock_faiss.document_storage = MagicMock()
        mock_faiss.document_storage.get_documents = AsyncMock(
            side_effect=RuntimeError("db down")
        )

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine.get_memory(42)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_memory_preserves_raw_sqlite_revision(
        self,
        tmp_db_path: str,
    ) -> None:
        """canonical revision 必须保留 SQLite 原值供 Atom 事务校验。"""

        raw_revision = "2026-07-24 02:21:07.123456"
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute(
                """CREATE TABLE documents (
                       id INTEGER PRIMARY KEY, text TEXT NOT NULL, metadata TEXT,
                       created_at TEXT, updated_at TEXT
                   )"""
            )
            await db.execute(
                "INSERT INTO documents(id,text,metadata,created_at,updated_at) "
                "VALUES(?,?,?,?,?)",
                (
                    17,
                    "匿名 canonical 正文",
                    json.dumps({"privacy_level": "shared"}),
                    raw_revision,
                    raw_revision,
                ),
            )
            await db.commit()

        mock_faiss = MagicMock()
        mock_faiss.document_storage.get_documents = AsyncMock(
            return_value=[
                {
                    "id": 17,
                    "text": "匿名 canonical 正文",
                    "metadata": json.dumps({"privacy_level": "shared"}),
                    "created_at": "2026-07-24T02:21:07.123456",
                    "updated_at": "2026-07-24T02:21:07.123456",
                }
            ]
        )
        engine = MemoryEngine(db_path=tmp_db_path, faiss_db=mock_faiss)
        engine.db_connection = await aiosqlite.connect(tmp_db_path)
        try:
            result = await engine.get_memory(17)
        finally:
            await engine.db_connection.close()

        assert result is not None
        assert result["updated_at"] == raw_revision


class TestMemoryEngineAddMemoryErrors:
    """Tests for add_memory error paths (without full DB setup)."""

    def test_add_memory_empty_content_raises(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)

        import asyncio

        with pytest.raises(ValueError, match="记忆内容不能为空"):
            asyncio.run(engine.add_memory(""))
        with pytest.raises(ValueError, match="记忆内容不能为空"):
            asyncio.run(engine.add_memory("   "))

    def test_add_memory_no_hybrid_retriever_raises(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.hybrid_retriever = None
        engine._write_journal.start_op = AsyncMock(return_value=1)

        import asyncio

        with pytest.raises(RuntimeError, match="混合检索器未初始化"):
            asyncio.run(engine.add_memory("test content"))

    @pytest.mark.asyncio
    async def test_add_memory_records_quality_sample_after_success(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.hybrid_retriever = MagicMock()
        engine.hybrid_retriever.add_memory = AsyncMock(return_value=123)
        engine.graph_memory_manager = None
        engine.atom_store = None
        engine._write_journal.start_op = AsyncMock(return_value=1)
        engine._write_journal.advance_op = AsyncMock()
        engine._retrieval = MagicMock()
        engine._retrieval.invalidate_cache = MagicMock()
        engine._retrieval.apply_interference = MagicMock(return_value=None)
        engine._retrieval.extract_triggers = MagicMock(return_value=None)
        engine._create_tracked_task = MagicMock()

        scorer = MagicMock()
        scorer.score_atom.return_value = MagicMock()
        scorer.check_alerts = MagicMock(return_value=[])
        engine._quality_scorer = scorer

        doc_id = await engine.add_memory(
            "Alice likes tea",
            session_id="session-1",
            persona_id="persona-1",
            importance=0.7,
            metadata={"source_type": "private_chat"},
        )

        assert doc_id == 123
        scorer.score_atom.assert_called_once()
        atom_payload = scorer.score_atom.call_args.args[0]
        assert atom_payload["id"] == 123
        assert atom_payload["content"] == "Alice likes tea"
        assert atom_payload["source_type"] == "private_chat"
        scorer.check_alerts.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_memory_records_document_write_failure_metric(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.hybrid_retriever = MagicMock()
        engine.hybrid_retriever.add_memory = AsyncMock(
            side_effect=RuntimeError("vector down")
        )
        engine._write_journal.start_op = AsyncMock(return_value=1)
        engine._write_journal.advance_op = AsyncMock()
        before = _metric_sample_value(
            "memora_memory_write_failures_total",
            {"stage": "document"},
        )

        with pytest.raises(RuntimeError, match="vector down"):
            await engine.add_memory("Alice likes tea")

        if monitoring_metrics.is_prometheus_available():
            assert (
                _metric_sample_value(
                    "memora_memory_write_failures_total",
                    {"stage": "document"},
                )
                == before + 1
            )


class TestMemoryEngineDeleteMemoryErrors:
    """Tests for delete_memory error paths."""

    @pytest.mark.asyncio
    async def test_delete_memory_no_hybrid_retriever(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.hybrid_retriever = None
        engine._write_journal.start_op = AsyncMock(return_value=1)
        engine._write_journal.advance_op = AsyncMock()

        result = await engine.delete_memory(42)
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_memory_hybrid_delete_fails(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.hybrid_retriever = MagicMock()
        engine.hybrid_retriever.delete_memory = AsyncMock(return_value=False)
        engine._write_journal.start_op = AsyncMock(return_value=1)
        engine._write_journal.advance_op = AsyncMock()

        result = await engine.delete_memory(42)
        assert result is False


class TestMemoryEngineUpdateMemoryErrors:
    """Tests for update_memory error paths."""

    @pytest.mark.asyncio
    async def test_update_memory_not_found(self) -> None:
        mock_faiss = MagicMock()
        # get_memory returns None
        mock_faiss.document_storage = MagicMock()
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine.update_memory(42, {"importance": 0.8})
        assert result is False

    @pytest.mark.asyncio
    async def test_update_memory_content_empty(self) -> None:
        mock_faiss = MagicMock()
        mock_faiss.document_storage = MagicMock()
        doc = {"id": 42, "text": "old content", "metadata": {}}
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[doc])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine.update_memory(42, {"content": ""})
        assert result is False

    @pytest.mark.asyncio
    async def test_update_memory_content_whitespace(self) -> None:
        mock_faiss = MagicMock()
        mock_faiss.document_storage = MagicMock()
        doc = {"id": 42, "text": "old content", "metadata": {}}
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[doc])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine.update_memory(42, {"content": "   "})
        assert result is False

    @pytest.mark.asyncio
    async def test_update_memory_metadata_only_no_hybrid(self) -> None:
        mock_faiss = MagicMock()
        mock_faiss.document_storage = MagicMock()
        doc = {"id": 42, "text": "old content", "metadata": {}}
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[doc])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.hybrid_retriever = None  # no hybrid
        result = await engine.update_memory(42, {"importance": 0.9})
        assert result is False


class TestMemoryEngineSearchMemories:
    """Tests for search_memories."""

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_empty(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine.search_memories("")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_whitespace_query_returns_empty(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine.search_memories("   ")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_no_hybrid_and_no_dual_route_raises(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.hybrid_retriever = None
        engine.dual_route_retriever = None

        with pytest.raises(RuntimeError, match="混合检索器未初始化"):
            await engine.search_memories("test query")

    @pytest.mark.asyncio
    async def test_search_forwards_memory_types_and_user_id_to_dual_route(self) -> None:
        from core.retrieval.rrf_fusion import HybridResult

        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.dual_route_retriever = MagicMock()
        engine.dual_route_retriever.search = AsyncMock(
            return_value=[
                HybridResult(
                    doc_id=1,
                    final_score=0.9,
                    rrf_score=0.9,
                    bm25_score=None,
                    vector_score=None,
                    content="memory",
                    metadata={},
                )
            ]
        )
        engine._retrieval = MagicMock()
        engine._retrieval.cache_key = MagicMock(return_value="cache-key")
        engine._retrieval.get_cached = MagicMock(return_value=None)
        engine._retrieval.get_session_cached = MagicMock(return_value=None)
        engine._retrieval.apply_trigger_boost = AsyncMock(side_effect=lambda _q, r: r)
        engine._retrieval.apply_boosts = AsyncMock(side_effect=lambda r, _e: r)
        engine._retrieval.set_cached = MagicMock()
        engine._retrieval.set_session_cached = MagicMock()
        engine._maintenance = MagicMock()
        engine._maintenance.update_access_times_batch = AsyncMock(return_value=1)
        engine._maintenance.migrate_session_if_needed = AsyncMock()

        def _close_background(coro):
            if inspect.iscoroutine(coro):
                coro.close()

        engine._create_tracked_task = MagicMock(side_effect=_close_background)

        await engine.search_memories(
            "test query",
            k=3,
            session_id="session-1",
            persona_id="persona-1",
            memory_types=["fact", "preference"],
            user_id="user-1",
        )

        engine.dual_route_retriever.search.assert_awaited_once()
        kwargs = engine.dual_route_retriever.search.await_args.kwargs
        assert kwargs["memory_types"] == ["fact", "preference"]
        assert kwargs["user_id"] == "user-1"

    @pytest.mark.asyncio
    async def test_search_forwards_strategy_and_debug_trace_to_retrieval_path(
        self,
    ) -> None:
        from core.retrieval.rrf_fusion import HybridResult

        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.dual_route_retriever = MagicMock()
        engine.dual_route_retriever.search = AsyncMock(
            return_value=[
                HybridResult(
                    doc_id=7,
                    final_score=0.9,
                    rrf_score=0.9,
                    bm25_score=None,
                    vector_score=None,
                    content="memory",
                    metadata={},
                )
            ]
        )
        engine._retrieval = MagicMock()
        engine._retrieval.cache_key = MagicMock(return_value="cache-key")
        engine._retrieval.get_cached = MagicMock(return_value=None)
        engine._retrieval.get_session_cached = MagicMock(return_value=None)
        engine._retrieval.apply_trigger_boost = AsyncMock(side_effect=lambda _q, r: r)

        async def apply_boosts(results, _emotion_context, debug_trace=None):
            assert debug_trace is not None
            debug_trace.append(
                {
                    "doc_id": 7,
                    "initial_score": 0.9,
                    "final_score": 0.9,
                    "stages": [],
                }
            )
            return results

        engine._retrieval.apply_boosts = AsyncMock(side_effect=apply_boosts)
        engine._retrieval.set_cached = MagicMock()
        engine._retrieval.set_session_cached = MagicMock()
        engine._maintenance = MagicMock()
        engine._maintenance.update_access_times_batch = AsyncMock(return_value=1)
        engine._maintenance.migrate_session_if_needed = AsyncMock()

        def _close_background(coro):
            if inspect.iscoroutine(coro):
                coro.close()

        engine._create_tracked_task = MagicMock(side_effect=_close_background)
        debug_trace: list[dict] = []

        await engine.search_memories(
            "test query",
            k=3,
            recall_strategy=RecallStrategy.RELATIONSHIP_REVIEW,
            debug_trace=debug_trace,
        )

        search_kwargs = engine.dual_route_retriever.search.await_args.kwargs
        assert search_kwargs["strategy"] is RecallStrategy.RELATIONSHIP_REVIEW
        assert debug_trace == engine._last_debug_trace
        assert debug_trace[0]["doc_id"] == 7


class TestMemoryEngineDeleteSubResources:
    """Tests for _delete_sub_resources."""

    @pytest.mark.asyncio
    async def test_delete_sub_resources_graph_and_atom(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)

        mock_graph = MagicMock()
        mock_graph.delete_memory = AsyncMock()
        engine.graph_memory_manager = mock_graph

        mock_atom = MagicMock()
        mock_atom.delete_by_parent = AsyncMock()
        engine.atom_store = mock_atom

        engine._write_journal.advance_op = AsyncMock()

        needs_repair = await engine._delete_sub_resources(42, None)
        assert needs_repair is False
        mock_graph.delete_memory.assert_called_once_with(42)
        mock_atom.delete_by_parent.assert_called_once_with(42)

    @pytest.mark.asyncio
    async def test_delete_sub_resources_graph_fails(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)

        mock_graph = MagicMock()
        mock_graph.delete_memory = AsyncMock(side_effect=Exception("graph error"))
        engine.graph_memory_manager = mock_graph

        mock_atom = MagicMock()
        mock_atom.delete_by_parent = AsyncMock()
        engine.atom_store = mock_atom

        engine._write_journal.advance_op = AsyncMock()

        needs_repair = await engine._delete_sub_resources(42, None)
        # Graph failed, atom succeeded — still needs_repair=True
        assert needs_repair is True

    @pytest.mark.asyncio
    async def test_delete_sub_resources_atom_fails(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)

        mock_graph = MagicMock()
        mock_graph.delete_memory = AsyncMock()
        engine.graph_memory_manager = mock_graph

        mock_atom = MagicMock()
        mock_atom.delete_by_parent = AsyncMock(side_effect=Exception("atom error"))
        engine.atom_store = mock_atom

        engine._write_journal.advance_op = AsyncMock()

        needs_repair = await engine._delete_sub_resources(42, None)
        assert needs_repair is True

    @pytest.mark.asyncio
    async def test_delete_sub_resources_no_components(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.graph_memory_manager = None
        engine.atom_store = None
        engine._write_journal.advance_op = AsyncMock()

        needs_repair = await engine._delete_sub_resources(42, None)
        assert needs_repair is False


class TestMemoryEngineBatchDelete:
    """Tests for batch_delete_memories."""

    @pytest.mark.asyncio
    async def test_batch_delete_empty_list(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine.batch_delete_memories([])
        assert result == 0

    @pytest.mark.asyncio
    async def test_batch_delete_no_db_connection(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.db_connection = None
        result = await engine.batch_delete_memories([1, 2, 3])
        assert result == 0


class TestMemoryEngineUpdateMemoryContentSuccess:
    """Tests for update_memory content replacement."""

    @pytest.mark.asyncio
    async def test_update_content_success(self) -> None:
        mock_faiss = MagicMock()
        mock_faiss.document_storage = MagicMock()
        doc = {"id": 42, "text": "old content", "metadata": {"session_id": "s1"}}
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[doc])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)

        # Mock write journal
        engine._write_journal.start_op = AsyncMock(return_value=1)
        engine._write_journal.advance_op = AsyncMock()

        # Mock hybrid retriever for the new add
        engine.hybrid_retriever = MagicMock()
        engine.hybrid_retriever.add_memory = AsyncMock(return_value=99)
        engine.hybrid_retriever.delete_memory = AsyncMock(return_value=True)

        # Mock graph
        engine.graph_memory_manager = None

        # Mock _delete_sub_resources via override
        engine._delete_sub_resources = AsyncMock(return_value=False)
        engine._retrieval = MagicMock()
        engine._retrieval.invalidate_cache = MagicMock()
        # _create_tracked_task is safe (it just schedules)
        engine._create_tracked_task = MagicMock()

        result = await engine.update_memory(42, {"content": "new content"})
        assert result is True


class TestMemoryEngineUpdateMetadata:
    """Tests for update_memory metadata-only path."""

    @pytest.mark.asyncio
    async def test_update_metadata_success(self) -> None:
        mock_faiss = MagicMock()
        mock_faiss.document_storage = MagicMock()
        doc = {"id": 42, "text": "content", "metadata": {"old_key": "old_val"}}
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[doc])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)

        engine.hybrid_retriever = MagicMock()
        engine.hybrid_retriever.update_metadata = AsyncMock(return_value=True)
        engine.graph_memory_manager = None
        engine._retrieval = MagicMock()
        engine._retrieval.invalidate_cache = MagicMock()

        result = await engine.update_memory(
            42, {"importance": 0.7, "metadata": {"new_key": "new_val"}}
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_update_metadata_with_graph_reindex(self) -> None:
        mock_faiss = MagicMock()
        mock_faiss.document_storage = MagicMock()
        doc = {"id": 42, "text": "content", "metadata": {}}
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[doc])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)

        engine.hybrid_retriever = MagicMock()
        engine.hybrid_retriever.update_metadata = AsyncMock(return_value=True)
        engine.graph_memory_manager = MagicMock()
        engine.graph_memory_manager.index_memory = AsyncMock()
        engine._write_journal.start_op = AsyncMock(return_value=1)
        engine._write_journal.advance_op = AsyncMock()
        engine._retrieval = MagicMock()
        engine._retrieval.invalidate_cache = MagicMock()

        result = await engine.update_memory(42, {"importance": 0.9})
        assert result is True
        engine.graph_memory_manager.index_memory.assert_called_once()
        engine._write_journal.start_op.assert_called_once()
        op_args, op_kwargs = engine._write_journal.start_op.call_args
        assert op_args[0] == "graph_reindex"
        assert op_kwargs["memory_id"] == 42
        engine._write_journal.advance_op.assert_called_once()
        advance_args, advance_kwargs = engine._write_journal.advance_op.call_args
        assert advance_args[:2] == (1, "graph_reindexed")
        assert advance_kwargs["status"] == "completed"
        assert advance_kwargs["memory_id"] == 42

    @pytest.mark.asyncio
    async def test_update_metadata_graph_reindex_failure_marks_repair(self) -> None:
        mock_faiss = MagicMock()
        mock_faiss.document_storage = MagicMock()
        doc = {"id": 42, "text": "content", "metadata": {"old": "value"}}
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[doc])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)

        engine.hybrid_retriever = MagicMock()
        engine.hybrid_retriever.update_metadata = AsyncMock(return_value=True)
        engine.graph_memory_manager = MagicMock()
        engine.graph_memory_manager.index_memory = AsyncMock(
            side_effect=RuntimeError("graph down")
        )
        engine._write_journal.start_op = AsyncMock(return_value=7)
        engine._write_journal.advance_op = AsyncMock()
        engine._retrieval = MagicMock()
        engine._retrieval.invalidate_cache = MagicMock()

        result = await engine.update_memory(42, {"metadata": {"new": "value"}})

        assert result is False
        engine.hybrid_retriever.update_metadata.assert_called_once_with(
            42,
            {"new": "value"},
        )
        engine._retrieval.invalidate_cache.assert_called_once()
        engine._write_journal.start_op.assert_called_once()
        op_args, op_kwargs = engine._write_journal.start_op.call_args
        assert op_args[0] == "graph_reindex"
        assert op_kwargs["memory_id"] == 42

        engine._write_journal.advance_op.assert_called_once()
        advance_args, advance_kwargs = engine._write_journal.advance_op.call_args
        assert advance_args[:2] == (7, "graph_reindex_failed")
        assert advance_kwargs["status"] == "needs_repair"
        assert advance_kwargs["memory_id"] == 42
        assert "graph down" in advance_kwargs["error"]
        assert advance_kwargs["payload_patch"]["metadata"]["new"] == "value"

    @pytest.mark.asyncio
    async def test_update_metadata_string_metadata(self) -> None:
        """Metadata stored as JSON string should be parsed."""
        import json

        mock_faiss = MagicMock()
        mock_faiss.document_storage = MagicMock()
        doc = {
            "id": 42,
            "text": "content",
            "metadata": json.dumps({"str_key": "str_val"}),
        }
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[doc])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)

        engine.hybrid_retriever = MagicMock()
        engine.hybrid_retriever.update_metadata = AsyncMock(return_value=True)
        engine.graph_memory_manager = None
        engine._retrieval = MagicMock()
        engine._retrieval.invalidate_cache = MagicMock()

        result = await engine.update_memory(42, {"importance": 0.6})
        assert result is True
