"""BackfillScheduler 测试 — 存量记忆话题重分割。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from core.schedulers.backfill_scheduler import BackfillScheduler

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class TestBackfillScheduler:
    @staticmethod
    def _make_scheduler(engine=None, config=None, embed_fn=None):
        return BackfillScheduler(
            memory_engine=engine or MagicMock(),
            config=config or {},
            embed_fn=embed_fn,
        )

    @staticmethod
    def _legacy_meta(schema_version: str = "v2", key_facts=None):
        """Create a minimal legacy metadata dict."""
        return {
            "schema_version": schema_version,
            "key_facts": key_facts or ["fact_a", "fact_b", "fact_c"],
            "summary": "multi-topic memory",
            "topics": ["topic1", "topic2"],
            "importance": 0.7,
            "sentiment": "positive",
            "emotion_tags": ["curious"],
        }

    @staticmethod
    def _make_doc_storage(docs=None, *, side_effect=None):
        ds = MagicMock()
        if side_effect is not None:
            ds.get_documents = AsyncMock(side_effect=side_effect)
            ds.get_all_documents = AsyncMock(side_effect=side_effect)
        else:
            ds.get_documents = AsyncMock(return_value=docs or [])
            ds.get_all_documents = AsyncMock(return_value=docs or [])
        return ds

    # ---- Initialization ----

    def test_default_config_values(self):
        """BackfillScheduler picks up defaults when no config is given."""
        s = self._make_scheduler()
        assert s._enabled is True
        assert s._batch_size == 50
        assert s._max_per_run == 500

    def test_config_overrides(self):
        """Config dict values override defaults."""
        s = self._make_scheduler(
            config={
                "enabled": False,
                "batch_size": 10,
                "max_backfill_per_run": 100,
            }
        )
        assert s._enabled is False
        assert s._batch_size == 10
        assert s._max_per_run == 100

    def test_config_false_string_parsed_as_bool(self):
        """String "False" is correctly parsed by _safe_bool."""
        s = self._make_scheduler(config={"enabled": "False"})
        assert s._enabled is False

    def test_initial_progress_is_idle(self):
        s = self._make_scheduler()
        assert s.progress["status"] == "idle"
        assert s.progress["processed"] == 0
        assert s.progress["errors"] == 0
        assert s.is_running is False

    # ---- start / lifecycle ----

    @pytest.mark.asyncio
    async def test_start_returns_job_id_and_sets_running(self):
        s = self._make_scheduler()
        # Prevent _run from actually doing anything
        with patch.object(s, "_run", AsyncMock()):
            job_id = await s.start()
            assert job_id.startswith("bf_")
            assert s.is_running is True
            assert s.progress["status"] == "running"

    @pytest.mark.asyncio
    async def test_start_raises_when_already_running(self):
        s = self._make_scheduler()
        s._progress["status"] = "running"
        with pytest.raises(RuntimeError, match="already running"):
            await s.start()

    @pytest.mark.asyncio
    async def test_get_status_returns_copy(self):
        s = self._make_scheduler()
        status = await s.get_status()
        assert status["status"] == "idle"
        # Mutation on returned dict should not affect internal state
        status["modified"] = True
        assert "modified" not in s.progress

    @pytest.mark.asyncio
    async def test_stop_cancels_running_task_and_marks_cancelled(self):
        s = self._make_scheduler()

        async def _long_running():
            await asyncio.sleep(10)

        task = asyncio.create_task(_long_running())
        s._task = task
        s._progress["status"] = "running"

        await s.stop()

        assert s._task is None
        assert s.progress["status"] == "cancelled"
        assert "cancelled_at" in s.progress

    # ---- _run: empty database ----

    @pytest.mark.asyncio
    async def test_run_no_legacy_memories_completes_immediately(self):
        """When _fetch_legacy_batch returns empty, _run completes with status=completed."""
        s = self._make_scheduler()
        s._job_id = "bf_test"
        s._fetch_legacy_batch = AsyncMock(return_value=[])

        await s._run()

        assert s.progress["status"] == "completed"
        assert s.progress["processed"] == 0

    # ---- _run: successful processing ----

    @pytest.mark.asyncio
    async def test_run_processes_batch_and_completes(self):
        """_run processes one batch with 2 legacy docs and marks completed."""
        s = self._make_scheduler()
        s._job_id = "bf_test"
        s._max_per_run = 500

        batch = [
            (10, self._legacy_meta(schema_version="v2", key_facts=["a", "b", "c"])),
            (20, self._legacy_meta(schema_version="v1", key_facts=["d", "e", "f"])),
        ]
        s._fetch_legacy_batch = AsyncMock(side_effect=[batch, []])
        s._backfill_one = AsyncMock()

        await s._run()

        assert s.progress["status"] == "completed"
        assert s.progress["processed"] == 2
        assert s.progress["errors"] == 0
        assert s._backfill_one.call_count == 2

    @pytest.mark.asyncio
    async def test_run_respects_max_per_run(self):
        """_run stops when processed >= max_per_run."""
        s = self._make_scheduler(config={"max_backfill_per_run": 3})
        s._job_id = "bf_test"
        s._max_per_run = 3

        batch1 = [(1, self._legacy_meta(key_facts=["a", "b"]))]
        batch2 = [(2, self._legacy_meta(key_facts=["c", "d"]))]
        batch3 = [(3, self._legacy_meta(key_facts=["e", "f"]))]
        s._fetch_legacy_batch = AsyncMock(side_effect=[batch1, batch2, batch3])
        s._backfill_one = AsyncMock()

        await s._run()

        assert s.progress["processed"] == 3
        assert s._backfill_one.call_count == 3

    # ---- _run: error handling ----

    @pytest.mark.asyncio
    async def test_run_tracks_backfill_one_errors(self):
        """Individual _backfill_one failures are counted but don't abort the run."""
        s = self._make_scheduler()
        s._job_id = "bf_test"

        batch = [
            (1, self._legacy_meta(key_facts=["a", "b", "c"])),
            (2, self._legacy_meta(key_facts=["d", "e", "f"])),
        ]
        s._fetch_legacy_batch = AsyncMock(side_effect=[batch, []])
        s._backfill_one = AsyncMock(side_effect=[None, RuntimeError("boom")])

        await s._run()

        assert s.progress["processed"] == 1
        assert s.progress["errors"] == 1
        assert s.progress["status"] == "completed_with_errors"

    @pytest.mark.asyncio
    async def test_run_marks_failed_on_unhandled_exception(self):
        """An unhandled exception in _run sets status to 'failed'."""
        s = self._make_scheduler()
        s._job_id = "bf_test"
        s._fetch_legacy_batch = AsyncMock(side_effect=RuntimeError("db down"))

        await s._run()

        assert s.progress["status"] == "failed"
        assert "db down" in s.progress["error"]

    @pytest.mark.asyncio
    async def test_run_checkpoint_advances(self):
        """_checkpoint is updated after each processed doc."""
        s = self._make_scheduler()
        s._job_id = "bf_test"

        batch = [
            (5, self._legacy_meta(key_facts=["a", "b", "c"])),
            (12, self._legacy_meta(key_facts=["d", "e", "f"])),
        ]
        s._fetch_legacy_batch = AsyncMock(side_effect=[batch, []])
        s._backfill_one = AsyncMock()

        await s._run()

        assert s._checkpoint == 12

    # ---- _fetch_legacy_batch ----

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_no_engine_returns_empty(self):
        """When memory_engine is None, fetch returns empty list."""
        s = BackfillScheduler(memory_engine=None)
        result = await s._fetch_legacy_batch()
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_no_faiss_db_returns_empty(self):
        """When memory_engine.faiss_db is None, fetch returns empty list."""
        engine = MagicMock()
        engine.faiss_db = None
        s = self._make_scheduler(engine=engine)
        result = await s._fetch_legacy_batch()
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_filters_by_schema_version(self):
        """Only docs with schema_version < v3 and >1 key_facts are returned."""
        engine = MagicMock()
        ds = self._make_doc_storage(
            [
                {"id": 1, "metadata": self._legacy_meta("v2", ["a", "b", "c"])},
                {
                    "id": 2,
                    "metadata": self._legacy_meta("v3", ["d", "e", "f"]),
                },  # v3, skip
                {
                    "id": 3,
                    "metadata": self._legacy_meta("v1", ["g"]),
                },  # only 1 fact, skip
                {"id": 4, "metadata": self._legacy_meta("v2", ["h", "i", "j"])},
                {
                    "id": 5,
                    "metadata": self._legacy_meta("", ["k", "l"]),
                },  # no version, keep
            ]
        )
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds

        s = self._make_scheduler(engine=engine)
        s._batch_size = 10
        result = await s._fetch_legacy_batch()

        # Only docs 1, 4, and 5 should be returned
        ids = [doc_id for doc_id, _meta in result]
        assert ids == [1, 4, 5]

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_respects_checkpoint(self):
        """Documents with id <= checkpoint are skipped."""
        engine = MagicMock()
        ds = self._make_doc_storage(
            [
                {"id": 5, "metadata": self._legacy_meta("v2", ["a", "b", "c"])},
                {"id": 10, "metadata": self._legacy_meta("v2", ["d", "e", "f"])},
                {"id": 15, "metadata": self._legacy_meta("v2", ["g", "h", "i"])},
            ]
        )
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds

        s = self._make_scheduler(engine=engine)
        s._checkpoint = 7
        s._batch_size = 10

        result = await s._fetch_legacy_batch()

        ids = [doc_id for doc_id, _meta in result]
        assert ids == [10, 15]

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_uses_document_storage_after_id(self):
        engine = MagicMock()
        ds = MagicMock()
        ds.get_documents_after_id = AsyncMock(
            return_value=[
                {"id": 12, "metadata": self._legacy_meta("v2", ["a", "b", "c"])},
            ]
        )
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds
        engine.db_connection = None

        s = self._make_scheduler(engine=engine)
        s._checkpoint = 10
        s._batch_size = 5

        result = await s._fetch_legacy_batch()

        assert [doc_id for doc_id, _meta in result] == [12]
        ds.get_documents_after_id.assert_called_once_with(last_id=10, limit=5)

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_uses_sqlite_id_page(self, tmp_db_path):
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("""
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY,
                    metadata TEXT
                )
            """)
            for doc_id in (1, 2, 3, 4):
                await db.execute(
                    "INSERT INTO documents(id, metadata) VALUES (?, ?)",
                    (
                        doc_id,
                        json.dumps(
                            self._legacy_meta("v2", [f"fact-{doc_id}", "b"]),
                            ensure_ascii=False,
                        ),
                    ),
                )
            await db.commit()

            engine = MagicMock()
            engine.faiss_db = MagicMock()
            engine.faiss_db.document_storage = MagicMock()
            engine.db_connection = db

            s = self._make_scheduler(engine=engine)
            s._checkpoint = 2
            s._batch_size = 1

            result = await s._fetch_legacy_batch()

            assert [doc_id for doc_id, _meta in result] == [3]

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_respects_batch_size(self):
        """Only up to batch_size results are returned."""
        engine = MagicMock()
        docs = [
            {"id": i, "metadata": self._legacy_meta("v2", ["a", "b", "c"])}
            for i in range(20)
        ]
        ds = self._make_doc_storage(docs)
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds

        s = self._make_scheduler(engine=engine)
        s._batch_size = 5

        result = await s._fetch_legacy_batch()
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_handles_string_metadata(self):
        """String metadata is JSON-parsed before inspection."""
        engine = MagicMock()
        ds = self._make_doc_storage(
            [
                {
                    "id": 1,
                    "metadata": json.dumps(self._legacy_meta("v2", ["a", "b", "c"])),
                },
                {
                    "id": 2,
                    "metadata": json.dumps(self._legacy_meta("v3", ["d", "e", "f"])),
                },
                {"id": 3, "metadata": json.dumps(self._legacy_meta("v1", ["g", "h"]))},
            ]
        )
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds

        s = self._make_scheduler(engine=engine)
        s._batch_size = 10
        result = await s._fetch_legacy_batch()

        ids = [doc_id for doc_id, _meta in result]
        assert ids == [1, 3]

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_handles_invalid_json_metadata(self):
        """Invalid JSON metadata defaults to empty dict and is skipped."""
        engine = MagicMock()
        ds = self._make_doc_storage(
            [
                {"id": 1, "metadata": "not valid json {{{"},
            ]
        )
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds

        s = self._make_scheduler(engine=engine)
        s._batch_size = 10
        result = await s._fetch_legacy_batch()
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_exception_returns_empty(self):
        """When get_all_documents raises, fetch returns empty list gracefully."""
        engine = MagicMock()
        ds = self._make_doc_storage(side_effect=RuntimeError("db locked"))
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds

        s = self._make_scheduler(engine=engine)
        result = await s._fetch_legacy_batch()
        assert result == []

    # ---- _backfill_one ----

    @pytest.mark.asyncio
    async def test_backfill_one_skips_single_fact(self):
        """When key_facts has <=1 entry, nothing happens (early return, no engine calls)."""
        engine = MagicMock()
        # Deliberately don't set up add_memory etc. -- should never be called
        s = self._make_scheduler(engine=engine)
        meta = self._legacy_meta(key_facts=["only_one_fact"])
        # Should not raise and should not call engine
        await s._backfill_one(1, meta)
        # engine.add_memory / engine.delete_memory / engine.hybrid_retriever should never be called
        engine.add_memory.assert_not_called()
        engine.delete_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_backfill_one_single_segment_upgrades_version(self):
        """When clustering produces only 1 segment, just bump schema_version."""
        engine = MagicMock()
        engine.hybrid_retriever = AsyncMock()

        s = self._make_scheduler(engine=engine)
        s._cluster_strategy = MagicMock()
        s._cluster_strategy.segment = AsyncMock(
            return_value=[
                MagicMock(
                    content="same",
                    importance=0.5,
                    metadata={},
                    key_facts=["a", "b"],
                    topics=[],
                    atoms=[],
                )
            ]
        )

        meta = self._legacy_meta(key_facts=["a", "b"])
        await s._backfill_one(1, meta)

        engine.hybrid_retriever.update_metadata.assert_called_once_with(
            1,
            {"schema_version": "v3"},
            advance_revision=False,
        )

    @pytest.mark.asyncio
    async def test_backfill_one_splits_into_multiple_segments(self):
        """When clustering produces multiple segments, delete old + insert new."""
        from core.processors.topic_splitter import MemorySegment

        engine = MagicMock()
        engine.add_memory = AsyncMock(side_effect=[101, 102])
        engine.delete_memory = AsyncMock()

        seg1 = MemorySegment(
            content="topic A",
            importance=0.6,
            metadata={},
            key_facts=["a1", "a2"],
            topics=[],
        )
        seg2 = MemorySegment(
            content="topic B", importance=0.8, metadata={}, key_facts=["b1"], topics=[]
        )

        s = self._make_scheduler(engine=engine)
        s._cluster_strategy = MagicMock()
        s._cluster_strategy.segment = AsyncMock(return_value=[seg1, seg2])

        meta = self._legacy_meta(key_facts=["a1", "a2", "b1"])
        await s._backfill_one(1, meta)

        assert engine.add_memory.call_count == 2
        engine.delete_memory.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_backfill_one_partial_write_preserves_old_memory(self):
        """When not all segments are written successfully, old memory is NOT deleted."""
        engine = MagicMock()
        engine.add_memory = AsyncMock(side_effect=[101, Exception("write failed")])
        engine.delete_memory = AsyncMock()

        seg1 = MagicMock(
            content="good",
            importance=0.5,
            metadata={},
            key_facts=[],
            topics=[],
            atoms=[],
        )
        seg2 = MagicMock(
            content="bad",
            importance=0.5,
            metadata={},
            key_facts=[],
            topics=[],
            atoms=[],
        )

        s = self._make_scheduler(engine=engine)
        s._cluster_strategy = MagicMock()
        s._cluster_strategy.segment = AsyncMock(return_value=[seg1, seg2])

        meta = self._legacy_meta(key_facts=["a", "b", "c"])
        await s._backfill_one(1, meta)

        # delete_memory NOT called because only 1/2 segments written
        engine.delete_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_backfill_one_marks_schema_v3_and_backfill_source(self):
        """New segments get schema_version=v3 and backfill_source metadata."""
        from core.processors.topic_splitter import MemorySegment

        engine = MagicMock()
        engine.add_memory = AsyncMock(return_value=999)
        engine.delete_memory = AsyncMock()

        seg = MemorySegment(
            content="new",
            importance=0.5,
            metadata={"existing": "val"},
            key_facts=["x"],
            topics=[],
            atoms=[],
        )

        s = self._make_scheduler(engine=engine)
        s._cluster_strategy = MagicMock()
        # Return 2 segments so it takes the delete+insert path (not update_metadata)
        s._cluster_strategy.segment = AsyncMock(
            return_value=[
                seg,
                MagicMock(
                    content="other",
                    importance=0.3,
                    metadata={},
                    key_facts=["y"],
                    topics=[],
                    atoms=[],
                ),
            ]
        )

        meta = self._legacy_meta(key_facts=["x", "y"])
        await s._backfill_one(42, meta)

        # Verify metadata was augmented on the first segment
        assert seg.metadata["schema_version"] == "v3"
        assert seg.metadata["backfill_source"] == 42
        assert seg.metadata["existing"] == "val"

    @pytest.mark.asyncio
    async def test_backfill_one_handles_delete_failure(self):
        """If delete_memory fails, old memory is marked and the job can count an error."""
        from core.processors.topic_splitter import MemorySegment

        engine = MagicMock()
        engine.add_memory = AsyncMock(return_value=1)
        engine.delete_memory = AsyncMock(side_effect=RuntimeError("delete error"))
        engine.hybrid_retriever = MagicMock()
        engine.hybrid_retriever.update_metadata = AsyncMock(return_value=True)

        seg = MemorySegment(
            content="x",
            importance=0.5,
            metadata={},
            key_facts=["a"],
            topics=[],
            atoms=[],
        )
        seg2 = MagicMock(
            content="y",
            importance=0.5,
            metadata={},
            key_facts=["b"],
            topics=[],
            atoms=[],
        )

        s = self._make_scheduler(engine=engine)
        s._cluster_strategy = MagicMock()
        s._cluster_strategy.segment = AsyncMock(return_value=[seg, seg2])

        meta = self._legacy_meta(key_facts=["a", "b"])
        with pytest.raises(RuntimeError, match="delete error"):
            await s._backfill_one(1, meta)

        engine.add_memory.assert_called()
        engine.delete_memory.assert_called_once()
        engine.hybrid_retriever.update_metadata.assert_awaited_once_with(
            1,
            {
                "schema_version": "v3",
                "backfill_delete_failed": True,
                "backfill_new_ids": [1, 1],
            },
            advance_revision=False,
        )

    @pytest.mark.asyncio
    async def test_backfill_one_passes_session_and_persona(self):
        """Backfill passes session_id from metadata's session_id or source_window."""
        from core.processors.topic_splitter import MemorySegment

        engine = MagicMock()
        engine.add_memory = AsyncMock(return_value=1)
        engine.delete_memory = AsyncMock()

        seg = MemorySegment(
            content="x",
            importance=0.5,
            metadata={},
            key_facts=["a"],
            topics=[],
            atoms=[],
        )
        seg2 = MagicMock(
            content="y",
            importance=0.5,
            metadata={},
            key_facts=["b"],
            topics=[],
            atoms=[],
        )

        s = self._make_scheduler(engine=engine)
        s._cluster_strategy = MagicMock()
        s._cluster_strategy.segment = AsyncMock(return_value=[seg, seg2])

        meta = self._legacy_meta(key_facts=["a", "b"])
        meta["session_id"] = "s123"
        meta["persona_id"] = "p456"
        await s._backfill_one(1, meta)

        call_kwargs = engine.add_memory.call_args
        assert call_kwargs[1]["session_id"] == "s123"
        assert call_kwargs[1]["persona_id"] == "p456"

    @pytest.mark.asyncio
    async def test_backfill_one_session_from_source_window(self):
        """When session_id is not in top-level meta, fall back to source_window."""
        from core.processors.topic_splitter import MemorySegment

        engine = MagicMock()
        engine.add_memory = AsyncMock(return_value=1)
        engine.delete_memory = AsyncMock()

        seg = MemorySegment(
            content="x",
            importance=0.5,
            metadata={},
            key_facts=["a"],
            topics=[],
            atoms=[],
        )
        seg2 = MagicMock(
            content="y",
            importance=0.5,
            metadata={},
            key_facts=["b"],
            topics=[],
            atoms=[],
        )

        s = self._make_scheduler(engine=engine)
        s._cluster_strategy = MagicMock()
        s._cluster_strategy.segment = AsyncMock(return_value=[seg, seg2])

        meta = {
            "key_facts": ["a", "b"],
            "source_window": {"session_id": "sw-sess"},
            "persona_id": "p99",
        }
        await s._backfill_one(1, meta)

        call_kwargs = engine.add_memory.call_args
        assert call_kwargs[1]["session_id"] == "sw-sess"

    # ---- edge cases ----

    @pytest.mark.asyncio
    async def test_fetch_batch_null_doc_id_skipped(self):
        """Documents with id=None are skipped."""
        engine = MagicMock()
        ds = self._make_doc_storage(
            [
                {"id": None, "metadata": self._legacy_meta("v2", ["a", "b", "c"])},
                {"id": 5, "metadata": self._legacy_meta("v2", ["d", "e", "f"])},
            ]
        )
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds

        s = self._make_scheduler(engine=engine)
        s._batch_size = 10
        result = await s._fetch_legacy_batch()
        ids = [doc_id for doc_id, _meta in result]
        assert ids == [5]

    def test_progress_is_readonly_snapshot(self):
        """progress returns a dict copy that doesn't mutate internals."""
        s = self._make_scheduler()
        p = s.progress
        p["processed"] = 9999
        assert s._progress["processed"] == 0
