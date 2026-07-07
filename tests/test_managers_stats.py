"""测试 stats_operations — Statistics, storage maintenance, and graph index rebuild."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.managers.maintenance_operations import MaintenanceOperations
from core.managers.stats_operations import StatsOperationsMixin
from core.validators.index_validator import IndexValidator


# ---------------------------------------------------------------------------
# MaintenanceOperations: combined class for real method testing
# ---------------------------------------------------------------------------


class TestMaintenanceOperationsInit:
    """测试 MaintenanceOperations 初始化。"""

    def test_default_construction(self) -> None:
        """MaintenanceOperations can be constructed with minimal config."""
        ops = MaintenanceOperations(config={})
        assert ops._config == {}
        assert ops._db is None
        assert ops._db_path == ""
        assert ops._faiss_db is None

    def test_full_construction(self) -> None:
        """所有 attributes are stored on init."""
        mock_db = MagicMock()
        mock_faiss = MagicMock()
        mock_retriever = MagicMock()
        mock_graph_mgr = MagicMock()
        mock_graph_store = MagicMock()
        mock_del = MagicMock()
        mock_inv = MagicMock()
        mock_upd = MagicMock()

        ops = MaintenanceOperations(
            config={"key": "val"},
            db_connection=mock_db,
            db_path="/tmp/test.db",
            faiss_db=mock_faiss,
            hybrid_retriever=mock_retriever,
            graph_memory_manager=mock_graph_mgr,
            graph_store=mock_graph_store,
            batch_delete_memories_cb=mock_del,
            invalidate_cache_cb=mock_inv,
            update_memory_cb=mock_upd,
        )
        assert ops._config == {"key": "val"}
        assert ops._db is mock_db
        assert ops._db_path == "/tmp/test.db"
        assert ops._faiss_db is mock_faiss
        assert ops._hybrid_retriever is mock_retriever
        assert ops._graph_memory_manager is mock_graph_mgr
        assert ops._graph_store is mock_graph_store
        assert ops._batch_delete_memories is mock_del
        assert ops._invalidate_cache is mock_inv
        assert ops._update_memory is mock_upd


# ---------------------------------------------------------------------------
# get_session_memories tests
# ---------------------------------------------------------------------------


class TestGetSessionMemories:
    """测试 get_session_memories 异步方法。"""

    def _make_ops(self) -> MaintenanceOperations:
        """创建 MaintenanceOperations with a mocked faiss_db."""
        faiss_db = MagicMock()
        faiss_db.document_storage = MagicMock()
        faiss_db.document_storage.count_documents = AsyncMock(return_value=0)
        faiss_db.document_storage.get_documents = AsyncMock(return_value=[])
        return MaintenanceOperations(config={}, faiss_db=faiss_db)

    @pytest.mark.asyncio
    async def test_empty_session(self) -> None:
        """当 session has no documents, returns empty list."""
        ops = self._make_ops()
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=0)
        result = await ops.get_session_memories("session_123")
        assert result == []

    @pytest.mark.asyncio
    async def test_few_documents_direct_return(self) -> None:
        """当 doc count <= limit, returns all sorted by create_time desc."""
        ops = self._make_ops()
        docs = [
            {
                "id": 1, "text": "older",
                "metadata": {"create_time": 100.0, "session_id": "s1"},
            },
            {
                "id": 2, "text": "newer",
                "metadata": {"create_time": 200.0, "session_id": "s1"},
            },
        ]
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=2)
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        result = await ops.get_session_memories("s1", limit=50)
        assert len(result) == 2
        assert result[0]["text"] == "newer"  # newer first
        assert result[1]["text"] == "older"

    @pytest.mark.asyncio
    async def test_many_documents_batched(self) -> None:
        """当 doc count > limit, batches and returns top <limit>."""
        ops = self._make_ops()
        # 1200 docs total, batch size 500
        total = 1200
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=total)

        def _make_batch(offset: int, limit: int):
            batch_id = offset // 500
            return [
                {
                    "id": offset + i,
                    "text": f"doc-{batch_id}-{i}",
                    "metadata": {
                        "create_time": float(offset + i),
                        "session_id": "s1",
                    },
                }
                for i in range(min(500, total - offset))
            ]

        ops._faiss_db.document_storage.get_documents = AsyncMock(
            side_effect=lambda metadata_filters, limit, offset: _make_batch(
                offset, limit
            )
        )
        result = await ops.get_session_memories("s1", limit=20)
        assert len(result) == 20
        # Newest first
        assert result[0]["id"] == 1199

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self) -> None:
        """当 an exception occurs, returns empty list."""
        ops = self._make_ops()
        ops._faiss_db.document_storage.count_documents = AsyncMock(
            side_effect=Exception("DB down")
        )
        result = await ops.get_session_memories("s1")
        assert result == []


# ---------------------------------------------------------------------------
# get_statistics tests
# ---------------------------------------------------------------------------


class TestGetStatistics:
    """测试 get_statistics 异步方法。"""

    def _make_ops(self) -> MaintenanceOperations:
        faiss_db = MagicMock()
        faiss_db.document_storage = MagicMock()
        faiss_db.document_storage.count_documents = AsyncMock(return_value=0)
        faiss_db.document_storage.get_documents = AsyncMock(return_value=[])
        return MaintenanceOperations(config={}, faiss_db=faiss_db)

    @pytest.mark.asyncio
    async def test_empty_stats(self) -> None:
        """当 no documents, returns defaults."""
        ops = self._make_ops()
        ops._graph_store = None
        stats = await ops.get_statistics()
        assert stats["total_memories"] == 0
        assert stats["sessions"] == {}
        assert stats["status_breakdown"]["active"] == 0
        assert stats["avg_importance"] == 0.0
        assert stats["oldest_memory"] is None
        assert stats["newest_memory"] is None
        assert stats["graph_memory_enabled"] is False

    @pytest.mark.asyncio
    async def test_stats_with_documents(self) -> None:
        """Documents contribute to session counts, status, importance, time range."""
        ops = self._make_ops()
        docs = [
            {
                "id": 1,
                "text": "a",
                "metadata": {
                    "session_id": "s1",
                    "status": "active",
                    "importance": 0.8,
                    "create_time": 100.0,
                },
            },
            {
                "id": 2,
                "text": "b",
                "metadata": {
                    "session_id": "s1",
                    "status": "archived",
                    "importance": 0.3,
                    "create_time": 300.0,
                },
            },
            {
                "id": 3,
                "text": "c",
                "metadata": {
                    "session_id": "s2",
                    "status": "active",
                    "importance": 0.5,
                    "create_time": 200.0,
                },
            },
        ]
        ops._faiss_db.document_storage.count_documents = AsyncMock(
            return_value=len(docs)
        )
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        ops._graph_store = None
        stats = await ops.get_statistics()
        assert stats["total_memories"] == 3
        assert stats["sessions"]["s1"] == 2
        assert stats["sessions"]["s2"] == 1
        assert stats["status_breakdown"]["active"] == 2
        assert stats["status_breakdown"]["archived"] == 1
        assert stats["oldest_memory"] == 100.0
        assert stats["newest_memory"] == 300.0

    @pytest.mark.asyncio
    async def test_importance_distribution(self) -> None:
        """Importance values are properly distributed across buckets."""
        ops = self._make_ops()
        docs = [
            {
                "id": i,
                "text": f"doc-{i}",
                "metadata": {"importance": imp, "session_id": "s1"},
            }
            for i, imp in enumerate([0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95])
        ]
        ops._faiss_db.document_storage.count_documents = AsyncMock(
            return_value=len(docs)
        )
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        ops._graph_store = None
        stats = await ops.get_statistics()
        dist = stats["importance_distribution"]
        # Each importance maps to bucket scaled*10 → floor
        # 0.05*10=0.5→0; 0.15*10=1.5→1; 0.25*10=2.5→2; 0.35→3; 0.45→4; 0.55→5; 0.65→6; 0.75→7; 0.85→8; 0.95→9
        for i in range(10):
            assert dist[f"{i}-{i+1}"] == 1

    @pytest.mark.asyncio
    async def test_unknown_status_defaults_to_active(self) -> None:
        """未知 status values map to 'active'."""
        ops = self._make_ops()
        docs = [
            {
                "id": 1,
                "text": "a",
                "metadata": {"status": "weird_status", "session_id": "s1"},
            }
        ]
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=1)
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        ops._graph_store = None
        stats = await ops.get_statistics()
        assert stats["status_breakdown"]["active"] == 1

    @pytest.mark.asyncio
    async def test_with_graph_store(self) -> None:
        """当 graph_store is set, stats include graph entries."""
        ops = self._make_ops()
        graph_store = MagicMock()
        graph_store.get_memory_entry_stats = AsyncMock(
            return_value={"graph_nodes": 10, "graph_edges": 5, "graph_entries": 3}
        )
        ops._graph_store = graph_store
        stats = await ops.get_statistics()
        assert stats["graph_nodes"] == 10
        assert stats["graph_memory_enabled"] is True

    @pytest.mark.asyncio
    async def test_exception_returns_defaults(self) -> None:
        """当 an exception occurs, returns a safe defaults dict."""
        ops = self._make_ops()
        ops._faiss_db.document_storage.count_documents = AsyncMock(
            side_effect=Exception("Boom")
        )
        ops._graph_store = MagicMock()  # truthy
        stats = await ops.get_statistics()
        assert stats["total_memories"] == 0
        assert stats["sessions"] == {}
        assert stats["avg_importance"] == 0.0
        # graph_memory_enabled is based on _graph_store truthiness even in error
        assert stats["graph_memory_enabled"] is True


# ---------------------------------------------------------------------------
# maintain_storage tests
# ---------------------------------------------------------------------------


class TestMaintainStorage:
    """测试 maintain_storage 异步方法。"""

    def _make_ops(self, db_path: str = "/fake/path.db") -> MaintenanceOperations:
        db = AsyncMock()
        db.execute = AsyncMock()
        db.commit = AsyncMock()
        return MaintenanceOperations(config={}, db_connection=db, db_path=db_path)

    @pytest.mark.asyncio
    async def test_db_none_returns_error(self) -> None:
        """当 db is None, returns error."""
        ops = MaintenanceOperations(config={}, db_connection=None)
        result = await ops.maintain_storage()
        assert result["success"] is False
        assert "not initialized" in result["error"]

    @pytest.mark.asyncio
    async def test_success_without_vacuum(self, tmp_path: Path) -> None:
        """在没有 vacuum, performs optimize and checkpoint only."""
        db_path = str(tmp_path / "test.db")
        db_path_str = db_path
        # Create a real file so stat works
        (tmp_path / "test.db").write_bytes(b"\x00" * 100)
        ops = self._make_ops(db_path_str)
        result = await ops.maintain_storage(vacuum=False)
        assert result["success"] is True
        assert result["vacuum"] is False
        assert result["db_size_before"] == 100
        assert result["db_size_after"] == 100  # unchanged since no vacuum on fake db

    @pytest.mark.asyncio
    async def test_with_vacuum(self, tmp_path: Path) -> None:
        """在 vacuum=True, VACUUM is executed."""
        db_path = str(tmp_path / "test2.db")
        (tmp_path / "test2.db").write_bytes(b"\x00" * 100)
        ops = self._make_ops(db_path)
        result = await ops.maintain_storage(vacuum=True)
        assert result["success"] is True
        assert result["vacuum"] is True

    @pytest.mark.asyncio
    async def test_bytes_reclaimed(self, tmp_path: Path) -> None:
        """bytes_reclaimed is computed correctly when sizes change."""
        db_path = str(tmp_path / "test3.db")
        (tmp_path / "test3.db").write_bytes(b"\x00" * 200)
        ops = self._make_ops(db_path)
        result = await ops.maintain_storage(vacuum=False)
        assert result["bytes_reclaimed"] == 0  # no change without vacuum

    @pytest.mark.asyncio
    async def test_exception_returns_error(self) -> None:
        """当 execute raises, returns error dict."""
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=Exception("Disk full"))
        ops = MaintenanceOperations(config={}, db_connection=db, db_path="/tmp/x.db")
        result = await ops.maintain_storage()
        assert result["success"] is False
        assert "Disk full" in result["error"]

    @pytest.mark.asyncio
    async def test_file_not_exist_zero_size(self, tmp_path: Path) -> None:
        """当 db file doesn't exist, before_size is 0."""
        db_path = str(tmp_path / "nonexistent.db")
        ops = self._make_ops(db_path)
        result = await ops.maintain_storage(vacuum=False)
        assert result["success"] is True
        assert result["db_size_before"] == 0

    @pytest.mark.asyncio
    async def test_vacuum_checkpoint_preserves_index_consistency_smoke(
        self,
        tmp_path: Path,
    ) -> None:
        """Real WAL checkpoint + VACUUM keeps documents/FTS/vector counts aligned."""
        db_path = str(tmp_path / "maintain.db")
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                "CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)"
            )
            await db.execute(
                "CREATE VIRTUAL TABLE memora_memories_fts USING fts5(doc_id UNINDEXED, content)"
            )
            for doc_id, text in ((1, "alpha memory"), (2, "beta memory")):
                await db.execute(
                    "INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                    (doc_id, f"doc-{doc_id}", text, "{}"),
                )
                await db.execute(
                    "INSERT INTO memora_memories_fts (doc_id, content) VALUES (?, ?)",
                    (doc_id, text),
                )
            await db.commit()

        db = await aiosqlite.connect(db_path)
        try:
            ops = MaintenanceOperations(config={}, db_connection=db, db_path=db_path)
            result = await ops.maintain_storage(vacuum=True)
        finally:
            await db.close()

        assert result["success"] is True
        assert result["vacuum"] is True
        assert result["fts_optimized"] == ["memora_memories_fts"]
        assert "memora_graph_entries_fts" in result["fts_skipped"]
        assert "memory_atoms_fts" in result["fts_skipped"]
        assert result["wal_checkpoint"]["mode"] == "TRUNCATE"

        fake_faiss = MagicMock()
        fake_faiss.embedding_storage.index.ntotal = 2
        status = await IndexValidator(db_path, fake_faiss).check_consistency()
        assert status.is_consistent is True
        assert status.needs_rebuild is False
        assert status.documents_count == 2
        assert status.bm25_count == 2


# ---------------------------------------------------------------------------
# rebuild_graph_index tests
# ---------------------------------------------------------------------------


class TestRebuildGraphIndex:
    """测试 rebuild_graph_index 异步方法。"""

    def _make_ops(self) -> MaintenanceOperations:
        faiss_db = MagicMock()
        faiss_db.document_storage = MagicMock()
        faiss_db.document_storage.count_documents = AsyncMock(return_value=0)
        faiss_db.document_storage.get_documents = AsyncMock(return_value=[])
        graph_mgr = MagicMock()
        graph_mgr.index_memory = AsyncMock()
        return MaintenanceOperations(
            config={},
            faiss_db=faiss_db,
            graph_memory_manager=graph_mgr,
            invalidate_cache_cb=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_no_graph_manager_returns_zero(self) -> None:
        """当 graph_memory_manager is None, returns zero."""
        ops = MaintenanceOperations(config={})
        result = await ops.rebuild_graph_index()
        assert result["rebuilt"] == 0
        assert result["skipped"] == 0

    @pytest.mark.asyncio
    async def test_rebuild_documents(self) -> None:
        """Documents are passed to graph_memory_manager.index_memory."""
        ops = self._make_ops()
        docs = [
            {
                "id": 1,
                "text": "content A",
                "metadata": {"topics": ["a"]},
            },
            {
                "id": 2,
                "text": "content B",
                "metadata": json.dumps({"topics": ["b"]}),
            },
        ]
        ops._faiss_db.document_storage.count_documents = AsyncMock(
            return_value=len(docs)
        )
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        result = await ops.rebuild_graph_index()
        assert result["rebuilt"] == 2
        assert result["skipped"] == 0
        assert ops._graph_memory_manager.index_memory.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_content_skipped(self) -> None:
        """Documents with empty text are skipped."""
        ops = self._make_ops()
        docs = [
            {"id": 1, "text": "", "metadata": {}},
            {"id": 2, "text": "  ", "metadata": {}},
            {"id": 3, "text": "valid", "metadata": {}},
        ]
        ops._faiss_db.document_storage.count_documents = AsyncMock(
            return_value=len(docs)
        )
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        result = await ops.rebuild_graph_index()
        assert result["rebuilt"] == 1
        assert result["skipped"] == 2

    @pytest.mark.asyncio
    async def test_metadata_string_parsed(self) -> None:
        """JSON-string metadata is parsed before passing to index_memory."""
        ops = self._make_ops()
        docs = [
            {"id": 1, "text": "hello", "metadata": '{"key":"val"}'},
        ]
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=1)
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        await ops.rebuild_graph_index()
        ops._graph_memory_manager.index_memory.assert_called_once_with(
            1, "hello", {"key": "val"}
        )

    @pytest.mark.asyncio
    async def test_invalid_metadata_string(self) -> None:
        """Broken JSON metadata string falls back to empty dict."""
        ops = self._make_ops()
        docs = [
            {"id": 1, "text": "hello", "metadata": "{broken"},
        ]
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=1)
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        await ops.rebuild_graph_index()
        ops._graph_memory_manager.index_memory.assert_called_once_with(
            1, "hello", {}
        )

    @pytest.mark.asyncio
    async def test_non_dict_metadata_fallback(self) -> None:
        """Non-dict, non-string metadata falls back to empty dict."""
        ops = self._make_ops()
        docs = [
            {"id": 1, "text": "hello", "metadata": 42},
        ]
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=1)
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        await ops.rebuild_graph_index()
        ops._graph_memory_manager.index_memory.assert_called_once_with(
            1, "hello", {}
        )

    @pytest.mark.asyncio
    async def test_missing_metadata_fallback(self) -> None:
        """缺失 metadata key falls back to empty dict."""
        ops = self._make_ops()
        docs = [
            {"id": 1, "text": "hello"},
        ]
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=1)
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        await ops.rebuild_graph_index()
        ops._graph_memory_manager.index_memory.assert_called_once_with(
            1, "hello", {}
        )

    @pytest.mark.asyncio
    async def test_invalidate_cache_called(self) -> None:
        """缓存 invalidation callback is called after rebuild."""
        invalidate = MagicMock()
        ops = MaintenanceOperations(
            config={},
            faiss_db=self._make_ops()._faiss_db,
            graph_memory_manager=self._make_ops()._graph_memory_manager,
            invalidate_cache_cb=invalidate,
        )
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=0)
        await ops.rebuild_graph_index()
        invalidate.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalidate_cache_none_callback(self) -> None:
        """当 invalidate_cache is None, no error on rebuild."""
        ops = MaintenanceOperations(
            config={},
            faiss_db=self._make_ops()._faiss_db,
            graph_memory_manager=self._make_ops()._graph_memory_manager,
            invalidate_cache_cb=None,
        )
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=0)
        await ops.rebuild_graph_index()
