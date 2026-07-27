"""测试 WriteOpJournal and WriteOpRepairMixin."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.managers.write_op_journal import WriteOpJournal


class TestWriteOpJournalConstructor:
    """测试 WriteOpJournal.__init__。"""

    def test_init_sets_attributes(self) -> None:
        mock_db = MagicMock()
        mock_graph = MagicMock()
        mock_atom = MagicMock()
        mock_get = MagicMock()
        mock_inv = MagicMock()
        mock_del_idx = MagicMock()
        mock_del_ga = MagicMock()

        journal = WriteOpJournal(
            db_connection=mock_db,
            graph_memory_manager=mock_graph,
            atom_store=mock_atom,
            atom_enabled=True,
            write_op_max_retries=5,
            get_memory_cb=mock_get,
            invalidate_cache_cb=mock_inv,
            delete_doc_indexes_batch_cb=mock_del_idx,
            delete_graph_atoms_batch_cb=mock_del_ga,
        )

        assert journal._db is mock_db
        assert journal._graph_memory_manager is mock_graph
        assert journal._atom_store is mock_atom
        assert journal._atom_enabled is True
        assert journal._max_retries == 5
        assert journal._get_memory is mock_get
        assert journal._invalidate_cache is mock_inv
        assert journal._delete_doc_indexes_batch is mock_del_idx
        assert journal._delete_graph_atoms_batch is mock_del_ga

    def test_init_with_none_db(self) -> None:
        journal = WriteOpJournal(
            db_connection=None,
            graph_memory_manager=None,
            atom_store=None,
        )
        assert journal._db is None
        assert journal._atom_enabled is True  # default
        assert journal._max_retries == 3  # default


@pytest.mark.asyncio
class TestWriteOpJournalDBOps:
    """测试 WriteOpJournal 数据库操作。"""

    async def test_create_table_skips_when_db_none(self) -> None:
        journal = WriteOpJournal(
            db_connection=None, graph_memory_manager=None, atom_store=None
        )
        # Should not raise
        await journal.create_table()

    async def test_create_table(self, tmp_db_path: str) -> None:
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            journal = WriteOpJournal(
                db_connection=db, graph_memory_manager=None, atom_store=None
            )
            await journal.create_table()

            # Verify table exists
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_write_ops'"
            )
            row = await cursor.fetchone()
            assert row is not None

    async def test_start_op_skips_when_db_none(self) -> None:
        journal = WriteOpJournal(
            db_connection=None, graph_memory_manager=None, atom_store=None
        )
        result = await journal.start_op("add", {"key": "val"}, memory_id=42)
        assert result is None

    async def test_start_op_creates_record(self, tmp_db_path: str) -> None:
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            journal = WriteOpJournal(
                db_connection=db, graph_memory_manager=None, atom_store=None
            )
            await journal.create_table()

            op_id = await journal.start_op("add", {"content": "test"}, memory_id=1)
            assert op_id is not None
            assert op_id > 0

            cursor = await db.execute(
                "SELECT * FROM memory_write_ops WHERE id = ?", (op_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["op_type"] == "add"
            assert row["status"] == "pending"
            assert row["step"] == "started"
            assert json.loads(row["payload"]) == {"content": "test"}
            assert row["memory_id"] == 1

    async def test_start_op_with_none_payload(self, tmp_db_path: str) -> None:
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            journal = WriteOpJournal(
                db_connection=db, graph_memory_manager=None, atom_store=None
            )
            await journal.create_table()

            op_id = await journal.start_op("delete")
            assert op_id is not None

            cursor = await db.execute(
                "SELECT * FROM memory_write_ops WHERE id = ?", (op_id,)
            )
            row = await cursor.fetchone()
            assert json.loads(row["payload"]) == {}

    async def test_advance_op_skips_when_db_none(self) -> None:
        journal = WriteOpJournal(
            db_connection=None, graph_memory_manager=None, atom_store=None
        )
        # Should not raise
        await journal.advance_op(1, "step_name", status="pending")

    async def test_advance_op_skips_when_op_id_none(self) -> None:
        mock_db = AsyncMock()
        journal = WriteOpJournal(
            db_connection=mock_db, graph_memory_manager=None, atom_store=None
        )
        await journal.advance_op(None, "step_name")
        mock_db.execute.assert_not_called()

    async def test_advance_op_updates_record(self, tmp_db_path: str) -> None:
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            journal = WriteOpJournal(
                db_connection=db, graph_memory_manager=None, atom_store=None
            )
            await journal.create_table()
            op_id = await journal.start_op("add", {"init": True})

            await journal.advance_op(
                op_id,
                "indexed",
                status="pending",
                memory_id=99,
                payload_patch={"extra": "data"},
            )

            cursor = await db.execute(
                "SELECT * FROM memory_write_ops WHERE id = ?", (op_id,)
            )
            row = await cursor.fetchone()
            assert row["step"] == "indexed"
            assert row["status"] == "pending"
            assert row["memory_id"] == 99
            payload = json.loads(row["payload"])
            assert payload["init"] is True
            assert payload["extra"] == "data"

    async def test_advance_op_completed_clears_error(self, tmp_db_path: str) -> None:
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            journal = WriteOpJournal(
                db_connection=db, graph_memory_manager=None, atom_store=None
            )
            await journal.create_table()
            op_id = await journal.start_op("add")

            # First advance with error
            await journal.advance_op(op_id, "step", error="some error")
            cursor = await db.execute(
                "SELECT * FROM memory_write_ops WHERE id = ?", (op_id,)
            )
            row = await cursor.fetchone()
            assert row["error"] == "some error"

            # Complete — should clear error
            await journal.advance_op(op_id, "completed", status="completed")
            cursor = await db.execute(
                "SELECT * FROM memory_write_ops WHERE id = ?", (op_id,)
            )
            row = await cursor.fetchone()
            assert row["error"] is None


@pytest.mark.asyncio
class TestWriteOpRepairMixinBasics:
    """测试 WriteOpRepairMixin 基本路径。"""

    async def test_repair_incomplete_skips_when_db_none(self) -> None:
        journal = WriteOpJournal(
            db_connection=None, graph_memory_manager=None, atom_store=None
        )
        result = await journal.repair_incomplete()
        assert result == 0

    async def test_repair_incomplete_no_pending_ops(self, tmp_db_path: str) -> None:
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            journal = WriteOpJournal(
                db_connection=db, graph_memory_manager=None, atom_store=None
            )
            await journal.create_table()
            result = await journal.repair_incomplete()
            assert result == 0

    async def test_repair_add_missing_memory_id(self, tmp_db_path: str) -> None:
        """_repair_add with None memory_id marks as failed."""
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            journal = WriteOpJournal(
                db_connection=db, graph_memory_manager=None, atom_store=None
            )
            await journal.create_table()
            op_id = await journal.start_op("add", memory_id=None)

            # Force the record to pending state via direct update
            await db.execute(
                "UPDATE memory_write_ops SET status='pending', step='document_indexed' WHERE id = ?",
                (op_id,),
            )
            await db.commit()

            result = await journal.repair_incomplete()
            assert result == 0  # unrepairable

            # Verify marked as failed
            cursor = await db.execute(
                "SELECT status, step FROM memory_write_ops WHERE id = ?", (op_id,)
            )
            row = await cursor.fetchone()
            assert row["status"] == "failed"
            assert row["step"] == "unrepairable"

    async def test_repair_add_missing_get_memory_cb(self, tmp_db_path: str) -> None:
        """_repair_add without get_memory callback marks as failed."""
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            journal = WriteOpJournal(
                db_connection=db, graph_memory_manager=None, atom_store=None
            )
            await journal.create_table()
            op_id = await journal.start_op("add", memory_id=1)

            # Force to be picked up by repair
            await db.execute(
                "UPDATE memory_write_ops SET status='pending', step='document_indexed' WHERE id = ?",
                (op_id,),
            )
            await db.commit()

            result = await journal.repair_incomplete()
            assert result == 0

            cursor = await db.execute(
                "SELECT status, step FROM memory_write_ops WHERE id = ?", (op_id,)
            )
            row = await cursor.fetchone()
            assert row["status"] == "failed"

    async def test_repair_delete_missing_memory_id(self, tmp_db_path: str) -> None:
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            journal = WriteOpJournal(
                db_connection=db, graph_memory_manager=None, atom_store=None
            )
            await journal.create_table()
            op_id = await journal.start_op("delete", memory_id=None)

            await db.execute(
                "UPDATE memory_write_ops SET status='pending' WHERE id = ?", (op_id,)
            )
            await db.commit()

            result = await journal.repair_incomplete()
            assert result == 0

    async def test_repair_batch_delete_missing_memory_ids(
        self, tmp_db_path: str
    ) -> None:
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            journal = WriteOpJournal(
                db_connection=db, graph_memory_manager=None, atom_store=None
            )
            await journal.create_table()
            op_id = await journal.start_op("batch_delete", {"memory_ids": []})

            await db.execute(
                "UPDATE memory_write_ops SET status='pending' WHERE id = ?", (op_id,)
            )
            await db.commit()

            result = await journal.repair_incomplete()
            assert result == 0

    async def test_repair_delete_with_graph_and_atom(self, tmp_db_path: str) -> None:
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            mock_graph = MagicMock()
            mock_graph.delete_memory = AsyncMock()
            mock_atom = MagicMock()
            mock_atom.delete_by_parent = AsyncMock()

            journal = WriteOpJournal(
                db_connection=db,
                graph_memory_manager=mock_graph,
                atom_store=mock_atom,
            )
            await journal.create_table()
            op_id = await journal.start_op("delete", memory_id=42)

            await db.execute(
                "UPDATE memory_write_ops SET status='needs_repair', step='document_deleted' WHERE id = ?",
                (op_id,),
            )
            await db.commit()

            result = await journal.repair_incomplete()
            assert result == 1

            mock_graph.delete_memory.assert_called_once_with(42)
            mock_atom.delete_by_parent.assert_called_once_with(42)

            # Verify completed
            cursor = await db.execute(
                "SELECT status FROM memory_write_ops WHERE id = ?", (op_id,)
            )
            row = await cursor.fetchone()
            assert row["status"] == "completed"

    async def test_repair_batch_delete_happy_path(self, tmp_db_path: str) -> None:
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            mock_del_idx = AsyncMock()
            mock_del_ga = AsyncMock()
            mock_get_mem = AsyncMock(return_value=None)

            journal = WriteOpJournal(
                db_connection=db,
                graph_memory_manager=None,
                atom_store=None,
                get_memory_cb=mock_get_mem,
                delete_doc_indexes_batch_cb=mock_del_idx,
                delete_graph_atoms_batch_cb=mock_del_ga,
            )
            await journal.create_table()
            op_id = await journal.start_op("batch_delete", {"memory_ids": [1, 2, 3]})

            await db.execute(
                "UPDATE memory_write_ops SET status='needs_repair' WHERE id = ?",
                (op_id,),
            )
            await db.commit()

            result = await journal.repair_incomplete()
            assert result == 1

            mock_del_idx.assert_called_once_with([1, 2, 3])
            mock_del_ga.assert_called_once_with([1, 2, 3])

            cursor = await db.execute(
                "SELECT status FROM memory_write_ops WHERE id = ?", (op_id,)
            )
            row = await cursor.fetchone()
            assert row["status"] == "completed"

    async def test_repair_add_source_missing(self, tmp_db_path: str) -> None:
        """_repair_add when source document is gone marks as failed."""
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            mock_get = AsyncMock(return_value=None)  # source missing

            journal = WriteOpJournal(
                db_connection=db,
                graph_memory_manager=None,
                atom_store=None,
                get_memory_cb=mock_get,
            )
            await journal.create_table()
            op_id = await journal.start_op("add", memory_id=42)

            await db.execute(
                "UPDATE memory_write_ops SET status='needs_repair' WHERE id = ?",
                (op_id,),
            )
            await db.commit()

            result = await journal.repair_incomplete()
            assert result == 0

            cursor = await db.execute(
                "SELECT status, step FROM memory_write_ops WHERE id = ?", (op_id,)
            )
            row = await cursor.fetchone()
            assert row["status"] == "failed"
            assert row["step"] == "source_missing"


@pytest.mark.asyncio
class TestWriteOpRepairAddIntegration:
    """集成测试：_repair_add."""

    async def test_repair_add_with_only_atoms(self, tmp_db_path: str) -> None:
        """add repair: document exists, atoms present, graph absent."""
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row

            existing_doc = {
                "id": 42,
                "text": "repair this memory",
                "updated_at": "rev-42",
                "metadata": {
                    "session_id": "s1",
                    "persona_id": "p1",
                    "privacy_level": "shared",
                },
            }
            mock_get = AsyncMock(return_value=existing_doc)
            mock_atom = MagicMock()
            mock_atom.get_by_parent = AsyncMock(return_value=[])
            mock_atom.insert_many = AsyncMock()

            journal = WriteOpJournal(
                db_connection=db,
                graph_memory_manager=None,
                atom_store=mock_atom,
                atom_enabled=True,
                get_memory_cb=mock_get,
            )
            await journal.create_table()

            atom_payload = {
                "content": "atom content",
                "atom_type": "factual",
                "entities": ["e1"],
                "importance": 0.5,
                "confidence": 0.7,
                "created_at": time.time(),
                "last_accessed_at": time.time(),
                "ttl_days": 30.0,
                "status": "active",
                "reinforcement_count": 0,
                "decay_type": "exponential",
            }
            op_id = await journal.start_op(
                "add",
                {"session_id": "s1", "persona_id": "p1", "atoms": [atom_payload]},
                memory_id=42,
            )

            await db.execute(
                "UPDATE memory_write_ops SET status='needs_repair' WHERE id = ?",
                (op_id,),
            )
            await db.commit()

            result = await journal.repair_incomplete()
            assert result == 1
            mock_atom.insert_many.assert_called_once()
            mock_atom.get_by_parent.assert_called_once_with(42)

            cursor = await db.execute(
                "SELECT status FROM memory_write_ops WHERE id = ?", (op_id,)
            )
            row = await cursor.fetchone()
            assert row["status"] == "completed"

    async def test_repair_add_with_graph_manager(self, tmp_db_path: str) -> None:
        """add repair: with graph_memory_manager, should re-index."""
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row

            existing_doc = {
                "id": 42,
                "text": "graph repair test",
                "metadata": {},
            }
            mock_get = AsyncMock(return_value=existing_doc)
            mock_graph = MagicMock()
            mock_graph.index_memory = AsyncMock()

            journal = WriteOpJournal(
                db_connection=db,
                graph_memory_manager=mock_graph,
                atom_store=None,
                get_memory_cb=mock_get,
            )
            await journal.create_table()
            op_id = await journal.start_op("add", memory_id=42)

            await db.execute(
                "UPDATE memory_write_ops SET status='needs_repair' WHERE id = ?",
                (op_id,),
            )
            await db.commit()

            result = await journal.repair_incomplete()
            assert result == 1
            mock_graph.index_memory.assert_called_once()

            cursor = await db.execute(
                "SELECT status FROM memory_write_ops WHERE id = ?", (op_id,)
            )
            row = await cursor.fetchone()
            assert row["status"] == "completed"

    async def test_repair_add_with_failed_atoms_dedup(self, tmp_db_path: str) -> None:
        """add repair: with failed_atoms payload, deduplicates against existing atoms."""
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row

            existing_doc = {
                "id": 42,
                "text": "dedup repair test",
                "updated_at": "rev-42",
                "metadata": {
                    "session_id": "s1",
                    "persona_id": "p1",
                    "privacy_level": "shared",
                },
            }
            mock_get = AsyncMock(return_value=existing_doc)

            from core.models.memory_atom import AtomType, MemoryAtom

            existing_atom = MemoryAtom(
                parent_memory_id=42,
                content="existing atom",
                atom_type=AtomType.FACTUAL,
                session_id="s1",
                persona_id="p1",
            )
            mock_atom = MagicMock()
            mock_atom.get_by_parent = AsyncMock(return_value=[existing_atom])
            mock_atom.insert_many = AsyncMock()

            journal = WriteOpJournal(
                db_connection=db,
                graph_memory_manager=None,
                atom_store=mock_atom,
                atom_enabled=True,
                get_memory_cb=mock_get,
            )
            await journal.create_table()

            now = time.time()
            atom_payload = {
                "content": "existing atom",  # same content as existing
                "atom_type": "factual",
                "entities": [],
                "importance": 0.5,
                "confidence": 0.7,
                "created_at": now,
                "last_accessed_at": now,
                "ttl_days": 30.0,
                "status": "active",
                "reinforcement_count": 0,
                "decay_type": "exponential",
                "session_id": "s1",
                "persona_id": "p1",
            }
            op_id = await journal.start_op(
                "add",
                {
                    "failed_atoms": [atom_payload],
                    "session_id": "s1",
                    "persona_id": "p1",
                },
                memory_id=42,
            )

            await db.execute(
                "UPDATE memory_write_ops SET status='needs_repair' WHERE id = ?",
                (op_id,),
            )
            await db.commit()

            result = await journal.repair_incomplete()
            assert result == 1
            # Should not insert because dedup matched
            mock_atom.insert_many.assert_not_called()

    async def test_repair_graph_reindex_rebuilds_graph(self, tmp_db_path: str) -> None:
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row

            existing_doc = {
                "id": 42,
                "text": "graph repair content",
                "metadata": {"session_id": "s1", "kind": "updated"},
            }
            mock_get = AsyncMock(return_value=existing_doc)
            mock_graph = MagicMock()
            mock_graph.index_memory = AsyncMock()

            journal = WriteOpJournal(
                db_connection=db,
                graph_memory_manager=mock_graph,
                atom_store=None,
                get_memory_cb=mock_get,
            )
            await journal.create_table()
            op_id = await journal.start_op(
                "graph_reindex",
                {"metadata": {"kind": "from_payload"}},
                memory_id=42,
            )
            await db.execute(
                "UPDATE memory_write_ops SET status='needs_repair', step='graph_reindex_failed' WHERE id = ?",
                (op_id,),
            )
            await db.commit()

            result = await journal.repair_incomplete()

            assert result == 1
            mock_get.assert_called_once_with(42)
            mock_graph.index_memory.assert_called_once_with(
                42,
                "graph repair content",
                {"session_id": "s1", "kind": "updated"},
                None,
            )
            cursor = await db.execute(
                "SELECT status, step FROM memory_write_ops WHERE id = ?",
                (op_id,),
            )
            row = await cursor.fetchone()
            assert row["status"] == "completed"
            assert row["step"] == "completed"

    async def test_repair_graph_reindex_source_missing_fails(
        self,
        tmp_db_path: str,
    ) -> None:
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row

            mock_get = AsyncMock(return_value=None)
            mock_graph = MagicMock()
            mock_graph.index_memory = AsyncMock()

            journal = WriteOpJournal(
                db_connection=db,
                graph_memory_manager=mock_graph,
                atom_store=None,
                get_memory_cb=mock_get,
            )
            await journal.create_table()
            op_id = await journal.start_op("graph_reindex", memory_id=42)
            await db.execute(
                "UPDATE memory_write_ops SET status='needs_repair', step='graph_reindex_failed' WHERE id = ?",
                (op_id,),
            )
            await db.commit()

            result = await journal.repair_incomplete()

            assert result == 0
            mock_graph.index_memory.assert_not_called()
            cursor = await db.execute(
                "SELECT status, step, error FROM memory_write_ops WHERE id = ?",
                (op_id,),
            )
            row = await cursor.fetchone()
            assert row["status"] == "failed"
            assert row["step"] == "source_missing"
            assert "source document missing" in row["error"]
