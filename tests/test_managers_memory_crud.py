"""MemoryEngine CRUD Mixin 测试：验证记忆的添加、获取、更新与删除。"""

from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

import core.features.observability.infrastructure.metrics as monitoring_metrics
from core.features.memory.application.memory_engine import MemoryEngine
from core.features.memory.infrastructure.schema_manager import SchemaManager
from core.features.retrieval.bm25_retriever import BM25Retriever
from core.features.retrieval.hybrid_retriever import HybridRetriever
from core.features.retrieval.rrf_fusion import RRFFusion
from core.features.retrieval.vector_retriever import VectorRetriever
from core.shared.recall_strategy import RecallStrategy
from core.shared.sql import MEMORY_FTS_CREATE_SQL
from tests.fact_evidence_helpers import candidate_evidence_metadata


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
    """测试 get_memory 方法。"""

    @pytest.mark.asyncio
    async def test_get_memory_returns_none_when_not_found(self) -> None:
        """未找到文档时应返回 None。"""
        mock_faiss = MagicMock()
        mock_faiss.document_storage = MagicMock()
        # 返回空列表，表示未找到文档。
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine.get_memory(42)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_memory_returns_doc_when_found(self) -> None:
        """找到文档时应返回标准记忆对象。"""
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
        """读取异常时应降级返回 None。"""
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

    @pytest.mark.asyncio
    async def test_get_memory_closes_cursor_when_revision_read_fails(self) -> None:
        """SQLite 时间列读取失败时必须关闭游标，并回退到文档自身字段。"""
        from core.features.memory.infrastructure.canonical_memory_reader import (
            load_canonical_memory,
        )

        class _FailingCursor:
            def __init__(self) -> None:
                self.closed = False

            async def fetchone(self):
                raise RuntimeError("read failed")

            async def close(self) -> None:
                self.closed = True

        class _FailingConnection:
            def __init__(self) -> None:
                self.cursor = _FailingCursor()

            async def execute(self, sql: str, params: tuple = ()):
                return self.cursor

        mock_faiss = MagicMock()
        mock_faiss.document_storage.get_documents = AsyncMock(
            return_value=[
                {
                    "id": 17,
                    "text": "匿名 canonical 正文",
                    "metadata": {},
                    "created_at": "doc-created",
                    "updated_at": "doc-updated",
                }
            ]
        )
        connection = _FailingConnection()

        result = await load_canonical_memory(mock_faiss, connection, 17)

        assert connection.cursor.closed is True
        assert result is not None
        assert result["created_at"] == "doc-created"
        assert result["updated_at"] == "doc-updated"


class TestMemoryEngineAddMemoryErrors:
    """测试不装配完整数据库时的 add_memory 错误路径。"""

    def test_add_memory_empty_content_raises(self) -> None:
        """空记忆正文应触发参数错误。"""
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)

        import asyncio

        with pytest.raises(ValueError, match="记忆内容不能为空"):
            asyncio.run(engine.add_memory(""))
        with pytest.raises(ValueError, match="记忆内容不能为空"):
            asyncio.run(engine.add_memory("   "))

    def test_add_memory_no_hybrid_retriever_raises(self) -> None:
        """缺少混合检索器时写入应失败。"""
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.hybrid_retriever = None
        engine._write_journal.start_op = AsyncMock(return_value=1)

        import asyncio

        with pytest.raises(RuntimeError, match="混合检索器未初始化"):
            asyncio.run(engine.add_memory("test content"))

    @pytest.mark.asyncio
    async def test_add_memory_records_quality_sample_after_success(self) -> None:
        """写入成功后应记录质量样本。"""
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
        """文档写入失败时应增加失败指标。"""
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
    """测试 delete_memory 错误路径。"""

    @pytest.mark.asyncio
    async def test_delete_memory_no_hybrid_retriever(self) -> None:
        """缺少混合检索器时删除应失败。"""
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.hybrid_retriever = None
        engine._write_journal.start_op = AsyncMock(return_value=1)
        engine._write_journal.advance_op = AsyncMock()

        result = await engine.delete_memory(42)
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_memory_hybrid_delete_fails(self) -> None:
        """混合检索器删除失败时应返回失败。"""
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.hybrid_retriever = MagicMock()
        engine.hybrid_retriever.delete_memory = AsyncMock(return_value=False)
        engine._write_journal.start_op = AsyncMock(return_value=1)
        engine._write_journal.advance_op = AsyncMock()

        result = await engine.delete_memory(42)
        assert result is False


class TestMemoryEngineUpdateMemoryErrors:
    """测试 update_memory 错误路径。"""

    @pytest.mark.asyncio
    async def test_update_memory_not_found(self) -> None:
        """目标记忆不存在时更新应失败。"""
        mock_faiss = MagicMock()
        # get_memory 返回 None。
        mock_faiss.document_storage = MagicMock()
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine.update_memory(42, {"importance": 0.8})
        assert result is False

    @pytest.mark.asyncio
    async def test_update_memory_content_empty(self) -> None:
        """更新为空正文时应失败。"""
        mock_faiss = MagicMock()
        mock_faiss.document_storage = MagicMock()
        doc = {"id": 42, "text": "old content", "metadata": {}}
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[doc])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine.update_memory(42, {"content": ""})
        assert result is False

    @pytest.mark.asyncio
    async def test_update_memory_content_whitespace(self) -> None:
        """更新为空白正文时应失败。"""
        mock_faiss = MagicMock()
        mock_faiss.document_storage = MagicMock()
        doc = {"id": 42, "text": "old content", "metadata": {}}
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[doc])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine.update_memory(42, {"content": "   "})
        assert result is False

    @pytest.mark.asyncio
    async def test_update_memory_metadata_only_no_hybrid(self) -> None:
        """缺少混合检索器时 metadata 更新应失败。"""
        mock_faiss = MagicMock()
        mock_faiss.document_storage = MagicMock()
        doc = {"id": 42, "text": "old content", "metadata": {}}
        mock_faiss.document_storage.get_documents = AsyncMock(return_value=[doc])

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.hybrid_retriever = None  # 不启用混合检索。
        result = await engine.update_memory(42, {"importance": 0.9})
        assert result is False


class _CachedCanonicalRows:
    """缓存命中重校验的最小 canonical 替身：按 ID 返回固定正文与空 metadata。"""

    def __init__(self, text: str) -> None:
        self._text = text

    async def get_documents(
        self,
        metadata_filters: dict | None = None,
        ids: list[int] | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict]:
        """复刻宿主文档存储的按 ID 批量读取形状。"""

        del metadata_filters, offset
        docs = [
            {"id": int(doc_id), "text": self._text, "metadata": {}}
            for doc_id in ids or []
        ]
        return docs[: int(limit)] if limit is not None else docs


class TestMemoryEngineSearchMemories:
    """验证 search_memories 的行为。"""

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_empty(self) -> None:
        """空查询应直接返回空结果。"""
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.lifecycle_recorder = MagicMock()
        result = await engine.search_memories("")
        assert result == []

        engine.lifecycle_recorder.record_retrieved.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_whitespace_query_returns_empty(self) -> None:
        """空白查询应直接返回空结果。"""
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine.search_memories("   ")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_no_hybrid_and_no_dual_route_raises(self) -> None:
        """缺少两类检索器时查询应失败。"""
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.hybrid_retriever = None
        engine.dual_route_retriever = None

        with pytest.raises(RuntimeError, match="混合检索器未初始化"):
            await engine.search_memories("test query")

    @pytest.mark.asyncio
    async def test_explicit_retrieved_observation_counts_unique_final_candidates(
        self,
    ) -> None:
        """显式标记的检索只统计最终可见候选的唯一 canonical ID。"""
        from core.features.retrieval.rrf_fusion import HybridResult

        engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
        engine.dual_route_retriever = MagicMock()
        engine.dual_route_retriever.search = AsyncMock(
            return_value=[
                HybridResult(
                    1, 0.9, 0.9, None, None, "one", {"memory_status": "active"}
                ),
                HybridResult(
                    1, 0.8, 0.8, None, None, "one", {"memory_status": "active"}
                ),
                HybridResult(
                    2,
                    0.7,
                    0.7,
                    None,
                    None,
                    "hidden",
                    {"gate_disposition": "mark_write"},
                ),
            ]
        )
        engine._retrieval = MagicMock()
        engine._retrieval.cache_key.return_value = "cache-key"
        engine._retrieval.cache_generation = 0
        engine._snapshot_candidates_for_cache = AsyncMock(return_value=[])
        engine._retrieval.get_cached.return_value = None
        engine._retrieval.get_session_cached.return_value = None
        engine._retrieval.apply_trigger_boost = AsyncMock(side_effect=lambda _q, r: r)
        engine._retrieval.apply_boosts = AsyncMock(side_effect=lambda r, _e: r)
        engine._retrieval.set_cached = MagicMock()
        engine._retrieval.set_session_cached = MagicMock()
        engine.lifecycle_recorder = MagicMock()

        results = await engine.search_memories("query", lifecycle_source="passive")

        assert [result.doc_id for result in results] == [1, 1]
        engine.lifecycle_recorder.record_retrieved.assert_called_once_with(
            1,
            source="passive",
            origin="fresh",
        )

    @pytest.mark.asyncio
    async def test_cache_hits_report_retrieval_total_timing(self) -> None:
        """结果缓存与会话缓存命中时都应上报完整检索耗时。"""

        from core.features.retrieval.rrf_fusion import HybridResult

        cached = [
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
        engine = MemoryEngine(
            db_path=":memory:",
            faiss_db=SimpleNamespace(document_storage=_CachedCanonicalRows("memory")),
        )
        engine._retrieval = MagicMock()
        engine._retrieval.cache_key.return_value = "cache-key"
        engine._retrieval.get_cached.side_effect = [cached, None]
        engine._retrieval.get_session_cached.return_value = cached
        engine._maintenance = MagicMock()
        engine.lifecycle_recorder = MagicMock()
        engine._maintenance.update_access_times_batch = AsyncMock(return_value=1)

        def _close_background(coro):
            """关闭测试中不需要实际调度的后台协程。"""

            if inspect.iscoroutine(coro):
                coro.close()

        engine._create_tracked_task = MagicMock(side_effect=_close_background)

        assert (
            await engine.search_memories("result cache", lifecycle_source="passive")
            == cached
        )
        assert engine._last_search_timing["cache_hit"] is True
        assert engine._last_search_timing["retrieval_total_ms"] >= 0.0

        assert (
            await engine.search_memories(
                "session cache", session_id="session", lifecycle_source="passive"
            )
            == cached
        )
        assert engine._last_search_timing["cache_hit"] is True
        assert engine._last_search_timing["retrieval_total_ms"] >= 0.0
        engine._maintenance.update_access_times_batch.assert_not_awaited()
        assert engine.lifecycle_recorder.record_retrieved.call_count == 2
        assert all(
            call.kwargs["origin"] == "cache"
            for call in engine.lifecycle_recorder.record_retrieved.call_args_list
        )

    @pytest.mark.asyncio
    async def test_search_forwards_memory_types_and_user_id_to_dual_route(self) -> None:
        """查询应向双路检索转发类型、用户与查询计划。"""
        from core.features.retrieval.rrf_fusion import HybridResult

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
        engine.lifecycle_recorder = MagicMock()
        engine._maintenance.update_access_times_batch = AsyncMock(return_value=1)
        engine._maintenance.migrate_session_if_needed = AsyncMock()

        def _close_background(coro):
            if inspect.iscoroutine(coro):
                coro.close()

        engine._create_tracked_task = MagicMock(side_effect=_close_background)
        query_plan = MagicMock()

        await engine.search_memories(
            "test query",
            k=3,
            session_id="session-1",
            persona_id="persona-1",
            memory_types=["fact", "preference"],
            user_id="user-1",
            query_plan=query_plan,
        )

        engine.dual_route_retriever.search.assert_awaited_once()
        kwargs = engine.dual_route_retriever.search.await_args.kwargs
        assert kwargs["memory_types"] == ["fact", "preference"]
        assert kwargs["user_id"] == "user-1"
        assert kwargs["query_plan"] is query_plan
        session_kwargs = engine._retrieval.set_session_cached.call_args.kwargs
        assert session_kwargs["query_intent"] is query_plan
        engine._maintenance.update_access_times_batch.assert_not_awaited()
        engine.lifecycle_recorder.record_retrieved.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_forwards_strategy_and_debug_trace_to_retrieval_path(
        self,
    ) -> None:
        """查询应向检索路径转发策略和调试追踪开关。"""
        from core.features.retrieval.rrf_fusion import HybridResult

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
                    metadata=candidate_evidence_metadata("memory"),
                ),
                HybridResult(
                    doc_id=8,
                    final_score=0.8,
                    rrf_score=0.8,
                    bm25_score=None,
                    vector_score=None,
                    content="mark-write memory",
                    metadata={
                        **candidate_evidence_metadata("mark-write memory"),
                        "gate_disposition": "mark_write",
                    },
                ),
                HybridResult(
                    doc_id=9,
                    final_score=0.7,
                    rrf_score=0.7,
                    bm25_score=None,
                    vector_score=None,
                    content="assistant-only memory",
                    metadata={},
                ),
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
        engine.lifecycle_recorder = MagicMock()
        engine._maintenance.migrate_session_if_needed = AsyncMock()

        def _close_background(coro):
            if inspect.iscoroutine(coro):
                coro.close()

        engine._create_tracked_task = MagicMock(side_effect=_close_background)
        debug_trace: list[dict] = []

        results = await engine.search_memories(
            "test query",
            k=3,
            include_mark_write=True,
            session_id="platform:private:session",
            recall_strategy=RecallStrategy.RELATIONSHIP_REVIEW,
            debug_trace=debug_trace,
            lifecycle_source="debug",
        )
        assert [result.doc_id for result in results] == [7, 8, 9]

        search_call = cast(Any, engine).dual_route_retriever.search.await_args
        assert search_call is not None
        search_kwargs = search_call.kwargs
        assert search_kwargs["strategy"] is RecallStrategy.RELATIONSHIP_REVIEW
        assert debug_trace == engine._last_debug_trace
        engine._maintenance.update_access_times_batch.assert_not_awaited()
        engine._maintenance.migrate_session_if_needed.assert_not_awaited()
        engine.lifecycle_recorder.record_retrieved.assert_called_once_with(
            1,
            source="debug",
            origin="fresh",
        )


class TestMemoryEngineDeleteSubResources:
    """测试 _delete_sub_resources。"""

    @pytest.mark.asyncio
    async def test_delete_sub_resources_graph_and_atom(self) -> None:
        """删除子资源时应同时处理图和 Atom。"""
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
        """图删除失败时应标记需要修复。"""
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
        # 图删除失败但 Atom 删除成功时仍需标记 needs_repair=True。
        assert needs_repair is True

    @pytest.mark.asyncio
    async def test_delete_sub_resources_atom_fails(self) -> None:
        """Atom 删除失败时应标记需要修复。"""
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
        """没有子组件时删除不应要求修复。"""
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.graph_memory_manager = None
        engine.atom_store = None
        engine._write_journal.advance_op = AsyncMock()

        needs_repair = await engine._delete_sub_resources(42, None)
        assert needs_repair is False


class TestMemoryEngineBatchDelete:
    """测试 batch_delete_memories。"""

    @pytest.mark.asyncio
    async def test_batch_delete_empty_list(self) -> None:
        """空批量删除请求应直接成功。"""
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine.batch_delete_memories([])
        assert result == 0

    @pytest.mark.asyncio
    async def test_batch_delete_no_db_connection(self) -> None:
        """缺少数据库连接时批量删除应失败。"""
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.db_connection = None
        result = await engine.batch_delete_memories([1, 2, 3])
        assert result == 0


class TestMemoryEngineUpdateMemoryContentSuccess:
    """测试 update_memory 的内容替换路径。"""

    @pytest.mark.asyncio
    async def test_update_content_success(self, tmp_db_path: str) -> None:
        """正文替换必须保留新 ID 和正文，不能只依赖 mock 返回 True。"""
        engine, db, store = await _engine_with_canonical_db(tmp_db_path)
        try:
            old_id = await store.insert_document(
                "old",
                "old content",
                {"session_id": "s1", "idempotency_key": "original"},
            )
            engine.hybrid_retriever = _ReplaceLifecycleStub(store)
            result = await engine.update_memory(old_id, {"content": "new content"})
            assert result is True
            assert await _canonical_rows(tmp_db_path) == [(old_id + 1, "new content")]
            assert await _active_canonical_ids(db) == [old_id + 1]
        finally:
            await db.close()


class TestMemoryEngineUpdateMetadata:
    """测试 update_memory 仅更新 metadata 的路径。"""

    @pytest.mark.asyncio
    async def test_update_metadata_success(self) -> None:
        """仅更新 metadata 时应成功写入。"""
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
    async def test_topic_update_records_observed_at(self, monkeypatch) -> None:
        """topic 集合变化时写入独立观察时间，避免状态更新刷新目录排序。"""

        monkeypatch.setattr(
            "core.features.memory.application.memory_engine_crud.time.time",
            lambda: 123.0,
        )
        mock_faiss = MagicMock()
        mock_faiss.document_storage.get_documents = AsyncMock(
            return_value=[
                {"id": 42, "text": "content", "metadata": {"topics": ["旧话题"]}}
            ]
        )
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.hybrid_retriever = MagicMock()
        engine.hybrid_retriever.update_metadata = AsyncMock(return_value=True)
        engine.graph_memory_manager = None
        engine._retrieval = MagicMock()
        engine._retrieval.invalidate_cache = MagicMock()

        result = await engine.update_memory(42, {"metadata": {"topics": ["新话题"]}})

        assert result is True
        engine.hybrid_retriever.update_metadata.assert_awaited_once_with(
            42,
            {"topics": ["新话题"], "topic_observed_at": 123.0},
        )

    @pytest.mark.asyncio
    async def test_update_metadata_with_graph_reindex(self) -> None:
        """语义 metadata 更新后应重建图索引。"""
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
        """图重建失败时应标记记忆需要修复。"""
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
        assert advance_kwargs["error"] == "graph_reindex_failed"
        assert "metadata" not in advance_kwargs.get("payload_patch", {})

    @pytest.mark.asyncio
    async def test_update_metadata_string_metadata(self) -> None:
        """以 JSON 字符串存储的 metadata 应被正确解析。"""
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


class _SqliteCanonicalStore:
    """用真实临时 SQLite 落 canonical 行，供故障注入后断言 active 行数。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def insert_document(self, doc_id: str, text: str, metadata: dict) -> int:
        """写入一条 canonical 行并返回整数 ID。"""

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO documents(doc_id, text, metadata, created_at, updated_at)
                VALUES (?, ?, ?, 'r1', 'r1')
                """,
                (doc_id, text, json.dumps(metadata, ensure_ascii=False)),
            )
            await db.commit()
            row_id = cursor.lastrowid
            assert row_id is not None
            return int(row_id)

    async def get_documents(
        self,
        *,
        metadata_filters: dict,
        ids: list | None = None,
        offset: int | None = 0,
        limit: int | None = 100,
    ) -> list[dict]:
        """复刻宿主文档存储的按 ID 读取形状。"""

        del metadata_filters, offset
        query = (
            "SELECT id, doc_id, text, metadata, created_at, updated_at FROM documents"
        )
        params: list[object] = []
        if ids:
            placeholders = ", ".join("?" for _ in ids)
            query += f" WHERE id IN ({placeholders})"
            params.extend(int(item) for item in ids)
        query += " ORDER BY id ASC LIMIT ?"
        params.append(int(limit or 100))
        async with aiosqlite.connect(self.db_path) as db:
            rows = await (await db.execute(query, params)).fetchall()
        return [
            {
                "id": int(row[0]),
                "doc_id": row[1],
                "text": row[2],
                "metadata": json.loads(row[3] or "{}"),
                "created_at": row[4],
                "updated_at": row[5],
            }
            for row in rows
        ]


class _ReplaceLifecycleStub:
    """以真实 documents 行实现「新 ID 替换旧 ID」，并按用例注入失败。"""

    def __init__(self, store: _SqliteCanonicalStore) -> None:
        self._store = store
        self.fail_delete_ids: set[int] = set()
        self.cancel_after_insert = False

    async def add_memory(self, content: str, metadata: dict) -> int:
        """先提交新 canonical 行，再按注入决定是否取消。"""

        new_id = await self._store.insert_document(f"doc-{content}", content, metadata)
        if self.cancel_after_insert:
            raise asyncio.CancelledError()
        return new_id

    async def delete_memory(self, memory_id: int) -> bool:
        """删除 canonical 行；注入的 ID 一律返回删除失败。"""

        if int(memory_id) in self.fail_delete_ids:
            return False
        async with aiosqlite.connect(self._store.db_path) as db:
            await db.execute("DELETE FROM documents WHERE id = ?", (int(memory_id),))
            await db.commit()
        return True


async def _canonical_rows(db_path: str) -> list[tuple[int, str]]:
    """返回 canonical 行（ID 升序）的 ID 与正文。"""

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT id, text FROM documents ORDER BY id")
        return [(int(row[0]), str(row[1])) for row in await cursor.fetchall()]


async def _active_canonical_ids(db: aiosqlite.Connection) -> list[int]:
    """按运行态召回门读取真实 SQLite 行，而非把 documents 数量当 active 数量。"""
    from core.shared.memory_status import is_memory_recallable

    cursor = await db.execute("SELECT id, metadata FROM documents ORDER BY id")
    return [
        int(row[0])
        for row in await cursor.fetchall()
        if is_memory_recallable(json.loads(row[1] or "{}"))
    ]


async def _latest_operation(
    db: aiosqlite.Connection,
    op_type: str,
) -> aiosqlite.Row | None:
    """读取最近一条指定类型的写操作账本行。"""

    cursor = await db.execute(
        "SELECT status, step, error, payload FROM memory_write_ops "
        "WHERE op_type = ? ORDER BY id DESC LIMIT 1",
        (op_type,),
    )
    return await cursor.fetchone()


async def _engine_with_canonical_db(tmp_db_path: str):
    """装配真实写账本、真实 canonical 行与替换端口的引擎边界。"""

    store = _SqliteCanonicalStore(tmp_db_path)
    faiss_db = SimpleNamespace(document_storage=store, delete=AsyncMock())
    engine = MemoryEngine(db_path=":memory:", faiss_db=faiss_db)
    db = await aiosqlite.connect(tmp_db_path)
    db.row_factory = aiosqlite.Row
    engine.db_connection = db
    engine._write_journal._db = db
    await SchemaManager(db).create_tables(engine._write_journal.create_table)
    await db.execute(MEMORY_FTS_CREATE_SQL)
    await db.commit()
    engine.graph_memory_manager = None
    engine.atom_store = None
    engine._retrieval = MagicMock()
    engine._retrieval.invalidate_cache = MagicMock()
    engine._create_tracked_task = MagicMock()
    return engine, db, store


@pytest.mark.asyncio
class TestMemoryEngineContentReplaceTwoPhase:
    """无 expected_revision 的正文替换：故障注入后 active canonical 行数恒为 1。"""

    async def test_old_delete_failure_rolls_back_new_row(
        self, tmp_db_path: str
    ) -> None:
        """旧删失败且补偿成功：回滚新行，账本记为已回滚且只剩旧行。"""

        engine, db, store = await _engine_with_canonical_db(tmp_db_path)
        try:
            await store.insert_document("doc-old", "旧正文", {"session_id": "s1"})
            stub = _ReplaceLifecycleStub(store)
            stub.fail_delete_ids = {1}
            engine.hybrid_retriever = stub

            assert await engine.update_memory(1, {"content": "新正文"}) is False

            assert engine.get_last_write_reason_code() == "content_replace_failed"
            assert await _canonical_rows(tmp_db_path) == [(1, "旧正文")]
            assert await _active_canonical_ids(db) == [1]
            row = await _latest_operation(db, "replace_content")
            assert row is not None
            assert (row["status"], row["step"]) == (
                "failed",
                "replacement_rolled_back",
            )
        finally:
            await db.close()

    async def test_compensation_failure_converges_to_single_row(
        self, tmp_db_path: str
    ) -> None:
        """旧删与补偿删除都失败：先保留待修复意图，再由 repair 按摘要收敛到一行。"""

        engine, db, store = await _engine_with_canonical_db(tmp_db_path)
        try:
            await store.insert_document("doc-old", "旧正文", {"session_id": "s1"})
            stub = _ReplaceLifecycleStub(store)
            stub.fail_delete_ids = {1, 2}
            engine.hybrid_retriever = stub

            assert await engine.update_memory(1, {"content": "新正文"}) is False

            assert await _canonical_rows(tmp_db_path) == [
                (1, "旧正文"),
                (2, "新正文"),
            ]
            assert await _active_canonical_ids(db) == [1]
            assert await engine.update_memory(1, {"content": "新正文"}) is False
            assert await _canonical_rows(tmp_db_path) == [(1, "旧正文"), (2, "新正文")]
            row = await _latest_operation(db, "replace_content")
            assert row is not None
            assert (row["status"], row["step"]) == (
                "needs_repair",
                "replacement_rollback_failed",
            )
            payload = json.loads(row["payload"])
            assert (payload["old_id"], payload["new_id"]) == (1, 2)

            stub.fail_delete_ids.clear()
            assert await engine._write_journal.repair_incomplete() == 1

            assert await _canonical_rows(tmp_db_path) == [(2, "新正文")]
            assert await _active_canonical_ids(db) == [2]
            row = await _latest_operation(db, "replace_content")
            assert row is not None
            assert (row["status"], row["step"]) == (
                "completed",
                "replacement_committed",
            )
        finally:
            await db.close()

    async def test_cancel_after_new_commit_converges_to_single_row(
        self, tmp_db_path: str
    ) -> None:
        """add 提交后取消：异常继续传播，repair 按摘要删除旧行并收敛到一行。"""

        engine, db, store = await _engine_with_canonical_db(tmp_db_path)
        try:
            await store.insert_document("doc-old", "旧正文", {"session_id": "s1"})
            stub = _ReplaceLifecycleStub(store)
            stub.cancel_after_insert = True
            engine.hybrid_retriever = stub

            with pytest.raises(asyncio.CancelledError):
                await engine.update_memory(1, {"content": "新正文"})

            assert await _canonical_rows(tmp_db_path) == [
                (1, "旧正文"),
                (2, "新正文"),
            ]
            assert await _active_canonical_ids(db) == [1]
            row = await _latest_operation(db, "replace_content")
            assert row is not None
            assert row["status"] == "pending"

            stub.cancel_after_insert = False
            assert await engine._write_journal.repair_incomplete() == 1

            assert await _canonical_rows(tmp_db_path) == [(2, "新正文")]
            assert await _active_canonical_ids(db) == [2]
            row = await _latest_operation(db, "replace_content")
            assert row is not None
            assert row["status"] == "completed"
        finally:
            await db.close()


@pytest.mark.asyncio
class TestMemoryEngineStagedDocumentWrite:
    """分段 canonical/FAISS/FTS 写入：canonical 提交后索引失败只降级。"""

    async def _engine(self, tmp_db_path: str, *, bm25_retriever: Any = None):
        """装配真实检索器分段端口与真实账本的引擎边界。"""

        store = _SqliteCanonicalStore(tmp_db_path)
        faiss_db = SimpleNamespace(
            document_storage=store,
            embedding_provider=SimpleNamespace(
                get_embedding=AsyncMock(return_value=[0.1, 0.2])
            ),
            embedding_storage=SimpleNamespace(insert=AsyncMock()),
            delete=AsyncMock(),
        )
        engine = MemoryEngine(db_path=":memory:", faiss_db=faiss_db)
        db = await aiosqlite.connect(tmp_db_path)
        db.row_factory = aiosqlite.Row
        engine.db_connection = db
        engine._write_journal._db = db
        await SchemaManager(db).create_tables(engine._write_journal.create_table)
        await db.execute(MEMORY_FTS_CREATE_SQL)
        await db.commit()
        bm25 = bm25_retriever or MagicMock(spec=BM25Retriever)
        if not isinstance(bm25, BM25Retriever):
            bm25.add_document = AsyncMock()
        engine.hybrid_retriever = HybridRetriever(
            bm25_retriever=bm25,
            vector_retriever=VectorRetriever(faiss_db),
            rrf_fusion=RRFFusion(),
        )
        engine.graph_memory_manager = None
        engine.atom_store = None
        engine._retrieval = MagicMock()
        engine._retrieval.invalidate_cache = MagicMock()
        engine._create_tracked_task = MagicMock()
        return engine, db, faiss_db, bm25

    async def test_add_writes_canonical_vector_and_fts(self, tmp_db_path: str) -> None:
        """正常路径下两阶段写入落 canonical 行、FAISS 向量与 FTS 行。"""

        text_processor = MagicMock()
        text_processor.tokenize_async = AsyncMock(return_value=["需要", "入库"])
        engine, db, faiss_db, _bm25 = await self._engine(
            tmp_db_path,
            bm25_retriever=BM25Retriever(
                db_path=tmp_db_path,
                text_processor=text_processor,
            ),
        )
        try:
            doc_id = await engine.add_memory("需要入库的事实", session_id="s1")

            assert await _canonical_rows(tmp_db_path) == [(doc_id, "需要入库的事实")]
            faiss_db.embedding_storage.insert.assert_awaited_once()
            _vector, vector_id = faiss_db.embedding_storage.insert.await_args.args
            assert int(vector_id) == doc_id
            cursor = await db.execute(
                "SELECT COUNT(*) FROM memora_memories_fts "
                "WHERE CAST(doc_id AS TEXT) = ?",
                (str(doc_id),),
            )
            fts_row = await cursor.fetchone()
            assert fts_row is not None
            assert int(fts_row[0]) == 1
            row = await _latest_operation(db, "add")
            assert row is not None
            assert (row["status"], row["step"]) == ("completed", "completed")
        finally:
            await db.close()

    async def test_faiss_failure_returns_committed_doc_id(
        self, tmp_db_path: str
    ) -> None:
        """FAISS 写入失败：返回已提交 ID，账本保留 index_stage_degraded。"""

        engine, db, faiss_db, _bm25 = await self._engine(tmp_db_path)
        try:
            faiss_db.embedding_storage.insert = AsyncMock(
                side_effect=RuntimeError("faiss down")
            )

            doc_id = await engine.add_memory("需要入库的事实", session_id="s1")

            assert isinstance(doc_id, int) and doc_id > 0
            assert await _canonical_rows(tmp_db_path) == [(doc_id, "需要入库的事实")]
            row = await _latest_operation(db, "add")
            assert row is not None
            assert row["status"] == "needs_repair"
            assert row["error"] == "index_stage_degraded"
            payload = json.loads(row["payload"])
            assert payload["memory_id"] == doc_id
            assert payload["content_preview"] == "需要入库的事实"
            assert len(payload["content_digest"]) == 32
        finally:
            await db.close()

    async def test_fts_failure_keeps_committed_row(self, tmp_db_path: str) -> None:
        """FTS 写入失败：canonical 与向量已提交，账本同样只记降级。"""

        engine, db, faiss_db, _bm25 = await self._engine(tmp_db_path)
        try:
            engine.hybrid_retriever.bm25_retriever.add_document = AsyncMock(
                side_effect=RuntimeError("fts down")
            )

            doc_id = await engine.add_memory("需要入库的事实", session_id="s1")

            assert await _canonical_rows(tmp_db_path) == [(doc_id, "需要入库的事实")]
            faiss_db.embedding_storage.insert.assert_awaited_once()
            row = await _latest_operation(db, "add")
            assert row is not None
            assert row["status"] == "needs_repair"
            assert row["error"] == "index_stage_degraded"
        finally:
            await db.close()

    async def test_degraded_add_retry_reuses_canonical_owner(
        self, tmp_db_path: str
    ) -> None:
        """降级写入后用同一幂等键重试：复用 owner，不产生第二条 active 行。"""

        engine, db, _faiss_db, _bm25 = await self._engine(tmp_db_path)
        try:
            engine.hybrid_retriever.bm25_retriever.add_document = AsyncMock(
                side_effect=RuntimeError("fts down")
            )

            first = await engine.add_memory(
                "同一条事实",
                session_id="s1",
                metadata={"idempotency_key": "k-1"},
            )
            second = await engine.add_memory(
                "同一条事实",
                session_id="s1",
                metadata={"idempotency_key": "k-1"},
            )

            assert second == first
            assert await _canonical_rows(tmp_db_path) == [(first, "同一条事实")]
        finally:
            await db.close()
