"""测试 stats_operations — Statistics, storage maintenance, and graph index rebuild."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

import core.features.memory.application.stats_operations as stats_operations
from core.features.memory.application.maintenance_operations import (
    MaintenanceOperations,
)
from core.features.memory.graph.infrastructure.graph_store import GraphStore
from core.features.memory.infrastructure.validators.index_validator import (
    IndexValidator,
)
from tests.fact_evidence_helpers import source_evidence

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
                "id": 1,
                "text": "older",
                "metadata": {"create_time": 100.0, "session_id": "s1"},
            },
            {
                "id": 2,
                "text": "newer",
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

    def test_daily_memory_counts_normalize_units_and_fill_90_days(self) -> None:
        target = datetime(2026, 7, 10, 12, tzinfo=timezone.utc).timestamp()
        assert hasattr(stats_operations, "_build_daily_memory_counts")

        result = stats_operations._build_daily_memory_counts(
            [
                target,
                target * 1000,
                target * 1_000_000,
                datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp(),
                datetime(2026, 7, 13, tzinfo=timezone.utc).timestamp(),
                -1,
                "invalid",
                None,
            ],
            today=date(2026, 7, 12),
        )

        assert len(result) == 90
        assert result[0]["date"] == "2026-04-14"
        assert result[-1]["date"] == "2026-07-12"
        assert sum(item["count"] for item in result) == 2
        assert next(item for item in result if item["date"] == "2026-07-10") == {
            "date": "2026-07-10",
            "count": 2,
        }

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
        assert len(stats["daily_memory_counts"]) == 90
        assert sum(item["count"] for item in stats["daily_memory_counts"]) == 0

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
            for i, imp in enumerate(
                [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
            )
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
            assert dist[f"{i}-{i + 1}"] == 1

    @pytest.mark.asyncio
    async def test_unknown_status_is_counted_separately(self) -> None:
        """未知状态不得伪装为活跃记忆。"""
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
        assert stats["status_breakdown"]["active"] == 0
        assert stats["status_breakdown"]["unknown"] == 1

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
        assert len(stats["daily_memory_counts"]) == 90
        assert sum(item["count"] for item in stats["daily_memory_counts"]) == 0


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

    @staticmethod
    def _derivable_doc(memory_id: int, topic: str) -> dict:
        """构造通过图来源门禁的可派生文档行。"""

        fact = f"fact-{topic}"
        return {
            "id": memory_id,
            "text": f"content {topic}",
            "created_at": "2026-09-18T00:00:00+00:00",
            "updated_at": "2026-09-18T00:00:00+00:00",
            "metadata": {
                "scope_key": "scope-a",
                "privacy_level": "public",
                "key_facts": [fact],
                "fact_source_evidence": [source_evidence(fact)],
            },
        }

    @pytest.mark.asyncio
    async def test_rebuild_documents(self) -> None:
        """Documents are passed to graph_memory_manager.index_memory."""
        ops = self._make_ops()
        docs = [
            self._derivable_doc(1, "a"),
            {
                **self._derivable_doc(2, "b"),
                "metadata": json.dumps(self._derivable_doc(2, "b")["metadata"]),
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
        """空正文来源计入 skipped，合法来源仍继续重建。"""
        ops = self._make_ops()
        docs = [
            {"id": 1, "text": "", "metadata": {}},
            {"id": 2, "text": "  ", "metadata": {}},
            self._derivable_doc(3, "valid"),
        ]
        ops._faiss_db.document_storage.count_documents = AsyncMock(
            return_value=len(docs)
        )
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        result = await ops.rebuild_graph_index()
        assert result["rebuilt"] == 1
        assert result["skipped"] == 2

    @pytest.mark.asyncio
    async def test_untrusted_sources_are_skipped_between_valid_ones(self) -> None:
        """非法来源夹在合法来源之间时逐来源隔离，合法来源全部重建。"""
        ops = self._make_ops()
        legacy = {
            "id": 2,
            "text": "legacy content",
            "created_at": "r1",
            "updated_at": "r1",
            "metadata": {},
        }
        mark_write = self._derivable_doc(4, "mw")
        mark_write["metadata"] = {
            **mark_write["metadata"],
            "gate_disposition": "mark_write",
        }
        docs = [
            self._derivable_doc(1, "a"),
            legacy,
            self._derivable_doc(3, "c"),
            mark_write,
        ]
        ops._faiss_db.document_storage.count_documents = AsyncMock(
            return_value=len(docs)
        )
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        result = await ops.rebuild_graph_index()
        assert result["rebuilt"] == 2
        assert result["skipped"] == 2
        assert result["failed"] == 0
        assert [
            call.args[0]
            for call in ops._graph_memory_manager.index_memory.call_args_list
        ] == [1, 3]

    @pytest.mark.asyncio
    async def test_real_failure_is_counted_and_does_not_stop_batch(self) -> None:
        """真实存储失败计入 failed 且不伪装成跳过，后续合法来源继续处理。"""
        ops = self._make_ops()

        async def _index(memory_id, content, metadata):
            if memory_id == 1:
                raise RuntimeError("vector_write_failed")

        ops._graph_memory_manager.index_memory = AsyncMock(side_effect=_index)
        docs = [self._derivable_doc(1, "a"), self._derivable_doc(2, "b")]
        ops._faiss_db.document_storage.count_documents = AsyncMock(
            return_value=len(docs)
        )
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        result = await ops.rebuild_graph_index()
        assert result["rebuilt"] == 1
        assert result["failed"] == 1
        assert result["skipped"] == 0
        assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_cancellation_propagates_through_rebuild(self) -> None:
        """取消必须穿透重建循环，不得被计为跳过或失败。"""
        ops = self._make_ops()
        ops._graph_memory_manager.index_memory = AsyncMock(
            side_effect=asyncio.CancelledError
        )
        docs = [self._derivable_doc(1, "a")]
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=1)
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        with pytest.raises(asyncio.CancelledError):
            await ops.rebuild_graph_index()

    @pytest.mark.asyncio
    async def test_metadata_string_parsed(self) -> None:
        """JSON-string metadata is parsed before passing to index_memory."""
        ops = self._make_ops()
        expected = self._derivable_doc(1, "hello")["metadata"]
        docs = [
            {
                "id": 1,
                "text": "hello",
                "created_at": "r1",
                "updated_at": "r1",
                "metadata": json.dumps(expected),
            },
        ]
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=1)
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        await ops.rebuild_graph_index()
        ops._graph_memory_manager.index_memory.assert_called_once_with(
            1, "hello", expected
        )

    @pytest.mark.asyncio
    async def test_invalid_metadata_stays_skipped(self) -> None:
        """损坏 JSON 或缺失 metadata 的来源不能凭空派生，只计 skipped。"""
        ops = self._make_ops()
        docs = [
            {"id": 1, "text": "hello", "metadata": "{broken"},
            {"id": 2, "text": "hello"},
            {"id": 3, "text": "hello", "metadata": 42},
        ]
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=3)
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        result = await ops.rebuild_graph_index()
        assert result["rebuilt"] == 0
        assert result["skipped"] == 3
        ops._graph_memory_manager.index_memory.assert_not_called()

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


class _ResidualGraphStore:
    """按 canonical 快照返回已删除来源的图存储替身，复刻真实水位线判定。"""

    def __init__(self, residual_ids: list[int], *, fail: bool = False) -> None:
        self.residual_ids = residual_ids
        self.fail = fail
        self.snapshots: list[tuple[frozenset[int], int]] = []

    async def list_residual_source_memory_ids(
        self, canonical_memory_ids, *, max_source_memory_id: int
    ) -> list[int]:
        """记录 canonical 快照与水位线，只返回水位线内的已删除来源。"""

        if self.fail:
            raise RuntimeError("graph_residue_scan_failed")
        alive_ids = {int(item) for item in canonical_memory_ids}
        self.snapshots.append((frozenset(alive_ids), int(max_source_memory_id)))
        return sorted(
            memory_id
            for memory_id in self.residual_ids
            if memory_id not in alive_ids and memory_id <= int(max_source_memory_id)
        )


async def _open_canonical_documents(
    db_path: Path,
    *,
    alive: tuple[int, ...] = (1,),
    deleted: tuple[int, ...] = (),
    facts: bool = True,
) -> aiosqlite.Connection:
    """创建真实 canonical ``documents`` 表并返回连接。

    ``deleted`` 先写入再删除，用于推进 ``AUTOINCREMENT`` 的 ID 序列水位线，
    复刻「来源已物理删除但派生行仍在」的生产状态。
    """

    connection = await aiosqlite.connect(db_path)
    await connection.execute(
        "CREATE TABLE IF NOT EXISTS documents ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "text TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL DEFAULT '{}', "
        "created_at TEXT, updated_at TEXT)"
    )
    for memory_id in (*alive, *deleted):
        metadata = (
            TestRebuildGraphIndex._derivable_doc(memory_id, f"topic{memory_id}")[
                "metadata"
            ]
            if facts
            else {}
        )
        await connection.execute(
            "INSERT INTO documents(id, text, metadata, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (
                int(memory_id),
                f"content {memory_id}",
                json.dumps(metadata, ensure_ascii=False),
                "2026-09-18T00:00:00+00:00",
                "2026-09-18T00:00:00+00:00",
            ),
        )
    for memory_id in deleted:
        await connection.execute(
            "DELETE FROM documents WHERE id = ?", (int(memory_id),)
        )
    await connection.commit()
    return connection


async def _seed_graph_entries(db_path: Path, source_ids: tuple[int, ...]) -> None:
    """在真实图库中为每个来源写入一条图条目行。"""

    db = await aiosqlite.connect(db_path)
    try:
        for source_memory_id in source_ids:
            await db.execute(
                "INSERT INTO graph_entries(entry_key, source_memory_id, entry_type, "
                "content, created_at, updated_at, scope_key, privacy_level, "
                "revision_token) VALUES(?, ?, 'fact', ?, ?, ?, 'scope-a', 'public', "
                "'rev-1')",
                (
                    f"entry-{source_memory_id}",
                    int(source_memory_id),
                    f"graph-{source_memory_id}",
                    "2026-09-18T00:00:00+00:00",
                    "2026-09-18T00:00:00+00:00",
                ),
            )
        await db.commit()
    finally:
        await db.close()


async def _graph_entry_sources(db_path: Path, source_ids: tuple[int, ...]) -> set[int]:
    """返回真实图库中仍有条目行的来源 ID。"""

    db = await aiosqlite.connect(db_path)
    try:
        cursor = await db.execute(
            "SELECT DISTINCT source_memory_id FROM graph_entries "
            "WHERE source_memory_id IN (SELECT value FROM json_each(?))",
            (json.dumps(list(source_ids)),),
        )
        return {int(row[0]) for row in await cursor.fetchall()}
    finally:
        await db.close()


class _CanonicalDocumentStorage:
    """直接读取真实 ``documents`` 表的 document_storage 替身。"""

    def __init__(self, connection: aiosqlite.Connection, *, after_first_page=None):
        self._connection = connection
        self._after_first_page = after_first_page
        self._pages = 0

    async def count_documents(self, metadata_filters=None) -> int:
        """统计真实 canonical 行数。"""

        cursor = await self._connection.execute("SELECT COUNT(*) FROM documents")
        row = await cursor.fetchone()
        await cursor.close()
        return int(row[0] or 0)

    async def get_documents(self, metadata_filters, limit=None, offset=0, ids=None):
        """按 ID 顺序返回一页 canonical 行，并在首屏之后触发并发写入。"""

        cursor = await self._connection.execute(
            "SELECT id, text, metadata, created_at, updated_at FROM documents "
            "ORDER BY id LIMIT ? OFFSET ?",
            (-1 if limit is None else int(limit), int(offset or 0)),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        self._pages += 1
        if self._pages == 1 and self._after_first_page is not None:
            await self._after_first_page()
        return [
            {
                "id": int(row[0]),
                "text": str(row[1] or ""),
                "metadata": row[2],
                "created_at": row[3],
                "updated_at": row[4],
            }
            for row in rows
        ]


class _SourceReapingGraphManager:
    """把源级残留回收落到真实 GraphStore 上的图记忆管理器替身。"""

    def __init__(self, store: GraphStore) -> None:
        self._store = store
        self.index_memory = AsyncMock()
        self.deleted_ids: list[list[int]] = []

    async def batch_delete_memories(self, memory_ids: list[int]) -> None:
        """记录并执行真实源级删除。"""

        self.deleted_ids.append([int(item) for item in memory_ids])
        await self._store.reap_source_graphs([int(item) for item in memory_ids])


class TestRebuildGraphResidueCleanup:
    """重建必须对 skipped 与可证明已删除来源做源级残留回收并计数。"""

    def _make_ops(
        self,
        *,
        store=None,
        cleanup_failure: BaseException | None = None,
        db_connection=None,
    ) -> MaintenanceOperations:
        """构造带可观察清理端口的维护操作。"""

        faiss_db = MagicMock()
        faiss_db.document_storage = MagicMock()
        faiss_db.document_storage.count_documents = AsyncMock(return_value=0)
        faiss_db.document_storage.get_documents = AsyncMock(return_value=[])
        graph_mgr = MagicMock()
        graph_mgr.index_memory = AsyncMock()

        async def _cleanup(memory_ids):
            if cleanup_failure is not None:
                raise cleanup_failure
            return None

        graph_mgr.batch_delete_memories = AsyncMock(side_effect=_cleanup)
        return MaintenanceOperations(
            config={},
            db_connection=db_connection,
            faiss_db=faiss_db,
            graph_memory_manager=graph_mgr,
            graph_store=store,
            invalidate_cache_cb=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_skipped_sources_are_reaped(self) -> None:
        """不适用来源的旧图行按源级删除回收并计入清理计数。"""

        ops = self._make_ops()
        docs = [
            TestRebuildGraphIndex._derivable_doc(1, "valid"),
            {"id": 2, "text": "", "metadata": {}},
            TestRebuildGraphIndex._derivable_doc(3, "legacy-free"),
        ]
        docs[2]["metadata"] = {**docs[2]["metadata"], "gate_disposition": "mark_write"}
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=3)
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)

        result = await ops.rebuild_graph_index()

        assert result["rebuilt"] == 1
        assert result["skipped"] == 2
        assert result["residue_candidates"] == 2
        assert result["residue_cleaned"] == 2
        assert result["residue_failed"] == 0
        ops._graph_memory_manager.batch_delete_memories.assert_awaited_once_with([2, 3])

    @pytest.mark.asyncio
    async def test_deleted_sources_are_reaped_with_canonical_snapshot(
        self, tmp_path: Path
    ) -> None:
        """已删除来源进入源级回收：完整存活集合与 ID 序列水位线一起传给图存储。"""

        connection = await _open_canonical_documents(
            tmp_path / "memora.db", alive=(1,), deleted=(9,)
        )
        store = _ResidualGraphStore([9])
        ops = self._make_ops(store=store, db_connection=connection)
        docs = [TestRebuildGraphIndex._derivable_doc(1, "kept")]
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=1)
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        try:
            result = await ops.rebuild_graph_index()
        finally:
            await connection.close()

        assert store.snapshots == [(frozenset({1}), 9)]
        assert result["residue_candidates"] == 1
        assert result["residue_cleaned"] == 1
        ops._graph_memory_manager.batch_delete_memories.assert_awaited_once_with([9])

    @pytest.mark.asyncio
    async def test_missing_canonical_snapshot_only_reaps_ineligible_sources(
        self,
    ) -> None:
        """canonical 快照不可用时 fail-closed：不按不完整集合回收已删除来源。"""

        store = _ResidualGraphStore([9])
        ops = self._make_ops(store=store, db_connection=None)
        docs = [{"id": 1, "text": "", "metadata": {}}]
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=1)
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)

        result = await ops.rebuild_graph_index()

        assert store.snapshots == []
        assert result["residue_candidates"] == 1
        assert result["residue_cleaned"] == 1
        ops._graph_memory_manager.batch_delete_memories.assert_awaited_once_with([1])

    @pytest.mark.asyncio
    async def test_residue_cleanup_failure_is_counted_and_does_not_fail_rebuild(
        self,
    ) -> None:
        """清理失败只降级计入 residue_failed，不改变重建计数。"""

        ops = self._make_ops(cleanup_failure=RuntimeError("cleanup unavailable"))
        docs = [{"id": 1, "text": "", "metadata": {}}]
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=1)
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)

        result = await ops.rebuild_graph_index()

        assert result["skipped"] == 1
        assert result["failed"] == 0
        assert result["residue_candidates"] == 1
        assert result["residue_cleaned"] == 0
        assert result["residue_failed"] == 1
        assert result["failed_reasons"]["graph_residue_cleanup_failed"] == 1

    @pytest.mark.asyncio
    async def test_residue_scan_failure_keeps_skipped_cleanup(
        self, tmp_path: Path
    ) -> None:
        """已删来源枚举失败时仍回收 skipped 来源，不抛出异常。"""

        connection = await _open_canonical_documents(tmp_path / "memora.db", alive=(1,))
        store = _ResidualGraphStore([9], fail=True)
        ops = self._make_ops(store=store, db_connection=connection)
        docs = [{"id": 1, "text": "", "metadata": {}}]
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=1)
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)
        try:
            result = await ops.rebuild_graph_index()
        finally:
            await connection.close()

        assert result["residue_cleaned"] == 1
        assert result["residue_failed"] == 0
        ops._graph_memory_manager.batch_delete_memories.assert_awaited_once_with([1])

    @pytest.mark.asyncio
    async def test_residue_cleanup_cancellation_propagates(self) -> None:
        """清理阶段的取消必须穿透重建循环。"""

        ops = self._make_ops(cleanup_failure=asyncio.CancelledError())
        docs = [{"id": 1, "text": "", "metadata": {}}]
        ops._faiss_db.document_storage.count_documents = AsyncMock(return_value=1)
        ops._faiss_db.document_storage.get_documents = AsyncMock(return_value=docs)

        with pytest.raises(asyncio.CancelledError):
            await ops.rebuild_graph_index()


class TestRebuildGraphResidueWithRealStore:
    """真实 GraphStore/SQLite：并发新增来源的图行不得被当成残留回收。"""

    @pytest.mark.asyncio
    async def test_scan_window_insert_keeps_concurrently_added_graph_rows(
        self, tmp_path: Path
    ) -> None:
        """扫描期间新增来源的图行必须保留；已删除来源的图行仍被回收。"""

        db_path = tmp_path / "memora.db"
        connection = await _open_canonical_documents(
            db_path, alive=(1, 2), deleted=(9,)
        )
        store = GraphStore(str(db_path))
        await store.initialize()
        await _seed_graph_entries(db_path, (1, 2, 9))

        async def add_concurrent_source() -> None:
            """扫描首屏之后写入新来源的 canonical 行与图行。"""

            await connection.execute(
                "INSERT INTO documents(id, text, metadata, created_at, updated_at) "
                "VALUES(3, 'content 3', ?, '2026-09-18T00:00:00+00:00', "
                "'2026-09-18T00:00:00+00:00')",
                (
                    json.dumps(
                        TestRebuildGraphIndex._derivable_doc(3, "topic3")["metadata"],
                        ensure_ascii=False,
                    ),
                ),
            )
            await connection.commit()
            await _seed_graph_entries(db_path, (3,))

        manager = _SourceReapingGraphManager(store)
        ops = MaintenanceOperations(
            config={},
            db_connection=connection,
            faiss_db=SimpleNamespace(
                document_storage=_CanonicalDocumentStorage(
                    connection, after_first_page=add_concurrent_source
                )
            ),
            graph_memory_manager=manager,
            graph_store=store,
            invalidate_cache_cb=MagicMock(),
        )
        try:
            result = await ops.rebuild_graph_index()
            remaining_sources = await _graph_entry_sources(db_path, (1, 2, 3, 9))
        finally:
            await connection.close()

        assert result["rebuilt"] == 2
        assert result["residue_candidates"] == 1
        assert result["residue_cleaned"] == 1
        assert manager.deleted_ids == [[9]]
        assert remaining_sources == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_deleted_highest_id_source_is_still_reaped(
        self, tmp_path: Path
    ) -> None:
        """已删除来源即使曾是最大 canonical ID，仍在水位线内被回收。"""

        db_path = tmp_path / "memora.db"
        connection = await _open_canonical_documents(
            db_path, alive=(1, 2), deleted=(9,)
        )
        store = GraphStore(str(db_path))
        await store.initialize()
        await _seed_graph_entries(db_path, (1, 2, 9))

        manager = _SourceReapingGraphManager(store)
        ops = MaintenanceOperations(
            config={},
            db_connection=connection,
            faiss_db=SimpleNamespace(
                document_storage=_CanonicalDocumentStorage(connection)
            ),
            graph_memory_manager=manager,
            graph_store=store,
            invalidate_cache_cb=MagicMock(),
        )
        try:
            result = await ops.rebuild_graph_index()
            remaining_sources = await _graph_entry_sources(db_path, (1, 2, 9))
        finally:
            await connection.close()

        assert result["residue_candidates"] == 1
        assert result["residue_cleaned"] == 1
        assert manager.deleted_ids == [[9]]
        assert remaining_sources == {1, 2}
