"""MemoryEngine Batch Mixin 测试 — _delete_document_indexes_for_batch、_delete_graph_and_atoms_for_batch。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.managers.memory_engine import MemoryEngine


@pytest.mark.asyncio
class TestDeleteDocumentIndexesBatch:
    """Tests for _delete_document_indexes_for_batch."""

    async def test_empty_list_returns_zero(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        result = await engine._delete_document_indexes_for_batch([])
        assert result == 0

    async def test_no_db_connection_returns_zero(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.db_connection = None
        result = await engine._delete_document_indexes_for_batch([1, 2])
        assert result == 0

    async def test_deletes_from_fts_and_documents(self, tmp_db_path: str) -> None:
        mock_faiss = MagicMock()
        mock_faiss.delete = AsyncMock()

        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("CREATE TABLE IF NOT EXISTS memora_memories_fts (doc_id INTEGER)")
            await db.execute("CREATE TABLE IF NOT EXISTS documents (id INTEGER, doc_id TEXT)")
            await db.execute("INSERT INTO memora_memories_fts (doc_id) VALUES (1), (2), (3)")
            await db.execute("INSERT INTO documents (id, doc_id) VALUES (1, 'uuid-1'), (2, 'uuid-2'), (3, 'uuid-3')")
            await db.commit()

            engine = MemoryEngine(db_path=tmp_db_path, faiss_db=mock_faiss)
            engine.db_connection = db
            engine._retrieval = MagicMock()
            engine._retrieval.invalidate_cache = MagicMock()

            deleted = await engine._delete_document_indexes_for_batch([1, 2])
            assert deleted == 2

            # Verify remaining
            cursor = await db.execute("SELECT id FROM documents")
            rows = await cursor.fetchall()
            assert len(rows) == 1
            assert rows[0]["id"] == 3

    async def test_faiss_delete_handles_exception(self, tmp_db_path: str) -> None:
        mock_faiss = MagicMock()
        mock_faiss.delete = AsyncMock(side_effect=Exception("faiss error"))

        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("CREATE TABLE IF NOT EXISTS memora_memories_fts (doc_id INTEGER)")
            await db.execute("CREATE TABLE IF NOT EXISTS documents (id INTEGER, doc_id TEXT)")
            await db.execute("INSERT INTO memora_memories_fts (doc_id) VALUES (1)")
            await db.execute("INSERT INTO documents (id, doc_id) VALUES (1, 'uuid-1')")
            await db.commit()

            engine = MemoryEngine(db_path=tmp_db_path, faiss_db=mock_faiss)
            engine.db_connection = db
            engine._retrieval = MagicMock()
            engine._retrieval.invalidate_cache = MagicMock()

            # Should not raise — FAISS errors are caught
            deleted = await engine._delete_document_indexes_for_batch([1])
            assert deleted == 1


@pytest.mark.asyncio
class TestDeleteGraphAndAtomsBatch:
    """Tests for _delete_graph_and_atoms_for_batch."""

    async def test_empty_list_no_ops(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.graph_memory_manager = MagicMock()
        engine.atom_store = MagicMock()

        await engine._delete_graph_and_atoms_for_batch([])

        engine.graph_memory_manager.batch_delete_memories.assert_not_called()
        engine.atom_store.batch_delete_by_parent.assert_not_called()

    async def test_deletes_graph_and_atoms(self) -> None:
        mock_faiss = MagicMock()
        mock_graph = MagicMock()
        mock_graph.batch_delete_memories = AsyncMock()
        mock_atom = MagicMock()
        mock_atom.batch_delete_by_parent = AsyncMock()

        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.graph_memory_manager = mock_graph
        engine.atom_store = mock_atom

        await engine._delete_graph_and_atoms_for_batch([1, 2, 3])

        mock_graph.batch_delete_memories.assert_called_once_with([1, 2, 3])
        mock_atom.batch_delete_by_parent.assert_called_once_with([1, 2, 3])

    async def test_skips_when_components_none(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine.graph_memory_manager = None
        engine.atom_store = None

        # Should not raise
        await engine._delete_graph_and_atoms_for_batch([1, 2, 3])


@pytest.mark.asyncio
class TestBatchDeleteMemoriesIntegration:
    """Integration-style tests for batch_delete_memories with real SQLite."""

    async def test_batch_delete_single_batch(self, tmp_db_path: str) -> None:
        mock_faiss = MagicMock()
        mock_faiss.delete = AsyncMock()

        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row

            # Set up schema
            await db.execute("CREATE TABLE IF NOT EXISTS memora_memories_fts (doc_id INTEGER)")
            await db.execute("CREATE TABLE IF NOT EXISTS documents (id INTEGER, doc_id TEXT)")
            await db.execute("INSERT INTO memora_memories_fts (doc_id) VALUES (1), (2), (3)")
            await db.execute("INSERT INTO documents (id, doc_id) VALUES (1, 'uuid-1'), (2, 'uuid-2'), (3, 'uuid-3')")
            await db.commit()

            engine = MemoryEngine(db_path=tmp_db_path, faiss_db=mock_faiss)
            engine.db_connection = db
            engine._retrieval = MagicMock()
            engine._retrieval.invalidate_cache = MagicMock()
            engine._write_journal.start_op = AsyncMock(return_value=1)
            engine._write_journal.advance_op = AsyncMock()
            engine._delete_graph_and_atoms_for_batch = AsyncMock()

            deleted = await engine.batch_delete_memories([1, 2, 3])
            assert deleted == 3

            # Verify all gone
            cursor = await db.execute("SELECT id FROM documents")
            rows = await cursor.fetchall()
            assert len(rows) == 0

    async def test_batch_delete_detailed_reports_not_found(self, tmp_db_path: str) -> None:
        mock_faiss = MagicMock()
        mock_faiss.delete = AsyncMock()

        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("CREATE TABLE IF NOT EXISTS memora_memories_fts (doc_id INTEGER)")
            await db.execute("CREATE TABLE IF NOT EXISTS documents (id INTEGER, doc_id TEXT)")
            await db.execute("INSERT INTO memora_memories_fts (doc_id) VALUES (1)")
            await db.execute("INSERT INTO documents (id, doc_id) VALUES (1, 'uuid-1')")
            await db.commit()

            engine = MemoryEngine(db_path=tmp_db_path, faiss_db=mock_faiss)
            engine.db_connection = db
            engine._retrieval = MagicMock()
            engine._retrieval.invalidate_cache = MagicMock()
            engine._write_journal.start_op = AsyncMock(return_value=1)
            engine._write_journal.advance_op = AsyncMock()
            engine._delete_graph_and_atoms_for_batch = AsyncMock()

            result = await engine.batch_delete_memories_detailed([1, 999])
            assert result["deleted_count"] == 1
            assert result["deleted_ids"] == [1]
            assert result["not_found_ids"] == [999]
            assert result["failed_ids"] == []

    async def test_batch_delete_multiple_batches(self, tmp_db_path: str) -> None:
        """Test that batches of >200 items are split and processed."""
        mock_faiss = MagicMock()
        mock_faiss.delete = AsyncMock()

        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row

            await db.execute("CREATE TABLE IF NOT EXISTS memora_memories_fts (doc_id INTEGER)")
            await db.execute("CREATE TABLE IF NOT EXISTS documents (id INTEGER, doc_id TEXT)")

            # Create 250 docs
            for i in range(1, 251):
                await db.execute(
                    "INSERT INTO documents (id, doc_id) VALUES (?, ?)",
                    (i, f"uuid-{i}"),
                )
                await db.execute(
                    "INSERT INTO memora_memories_fts (doc_id) VALUES (?)", (i,)
                )
            await db.commit()

            engine = MemoryEngine(db_path=tmp_db_path, faiss_db=mock_faiss)
            engine.db_connection = db
            engine._retrieval = MagicMock()
            engine._retrieval.invalidate_cache = MagicMock()
            engine._write_journal.start_op = AsyncMock(return_value=1)
            engine._write_journal.advance_op = AsyncMock()
            engine._delete_graph_and_atoms_for_batch = AsyncMock()

            all_ids = list(range(1, 251))
            deleted = await engine.batch_delete_memories(all_ids)
            assert deleted == 250

            # Verify all gone
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM documents")
            row = await cursor.fetchone()
            assert row["cnt"] == 0
