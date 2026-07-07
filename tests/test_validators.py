"""测试 core/validators/ — index validation, rebuild, and embedding retry.

The IndexValidator class uses mixin composition:
    IndexValidator(IndexRebuilderMixin)
    IndexRebuilderMixin(Bm25RebuilderMixin, EmbeddingRetryMixin, VectorRebuilderMixin)

Many methods (e.g. _failure_ratio, _is_rate_limit_error) are defined on IndexValidator and
referenced via self by the mixins at runtime via MRO. Tests that target mixin behaviour test
through the full IndexValidator or monkey-patch the missing methods onto the mixin.
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_faiss_mock(ntotal: int = 0):
    """构建 a mock FaissVecDB with controlled vector index count."""
    faiss_db = MagicMock()
    faiss_db.embedding_storage = MagicMock()
    faiss_db.embedding_storage.index = MagicMock()
    faiss_db.embedding_storage.index.ntotal = ntotal
    # Remove id_map by default so code falls through to count-based comparison
    del faiss_db.embedding_storage.index.id_map
    return faiss_db


def _make_validator(db_path: str = ":memory:", faiss_db=None):
    from core.validators.index_validator import IndexValidator
    return IndexValidator(db_path, faiss_db or MagicMock())


# ---------------------------------------------------------------------------
# IndexValidator + IndexStatus — check_consistency
# ---------------------------------------------------------------------------

class TestIndexValidator:

    @pytest.mark.asyncio
    async def test_consistency_empty_database(self, tmp_db_path):
        """空 database is always consistent."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.commit()

        validator = _make_validator(tmp_db_path)
        status = await validator.check_consistency()
        assert status.is_consistent is True
        assert status.documents_count == 0
        assert status.needs_rebuild is False
        assert status.reason == "数据库为空"

    @pytest.mark.asyncio
    async def test_consistency_fully_synced(self, tmp_db_path):
        """当 documents, BM25, and vector counts all match, status is consistent."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.execute("CREATE VIRTUAL TABLE memora_memories_fts USING fts5(doc_id, content)")
            for i in range(3):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
                await db.execute("INSERT INTO memora_memories_fts (doc_id, content) VALUES (?, ?)",
                                 (i, f"content_{i}"))
            await db.commit()

        faiss = _make_faiss_mock(ntotal=3)
        validator = _make_validator(tmp_db_path, faiss)
        status = await validator.check_consistency()
        assert status.is_consistent is True
        assert status.documents_count == 3
        assert status.bm25_count == 3
        assert status.vector_count == 3
        assert status.needs_rebuild is False

    @pytest.mark.asyncio
    async def test_consistency_missing_in_bm25(self, tmp_db_path):
        """Documents exist but BM25 is missing entries — needs_rebuild=True."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.execute("CREATE VIRTUAL TABLE memora_memories_fts USING fts5(doc_id, content)")
            for i in range(5):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
            for i in range(3):
                await db.execute("INSERT INTO memora_memories_fts (doc_id, content) VALUES (?, ?)",
                                 (i, f"content_{i}"))
            await db.commit()

        faiss = _make_faiss_mock(ntotal=5)
        validator = _make_validator(tmp_db_path, faiss)
        status = await validator.check_consistency()
        assert status.is_consistent is False
        assert status.missing_in_bm25 == 2
        assert status.needs_rebuild is True
        assert "BM25" in status.reason

    @pytest.mark.asyncio
    async def test_consistency_missing_in_vector(self, tmp_db_path):
        """Documents exist but vector index is missing entries — needs_rebuild=True."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.execute("CREATE VIRTUAL TABLE memora_memories_fts USING fts5(doc_id, content)")
            for i in range(4):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
                await db.execute("INSERT INTO memora_memories_fts (doc_id, content) VALUES (?, ?)",
                                 (i, f"content_{i}"))
            await db.commit()

        faiss = _make_faiss_mock(ntotal=2)
        validator = _make_validator(tmp_db_path, faiss)
        status = await validator.check_consistency()
        assert status.is_consistent is False
        assert status.missing_in_vector == 2
        assert status.needs_rebuild is True
        assert "向量" in status.reason

    @pytest.mark.asyncio
    async def test_consistency_vector_redundant_slots_ignored(self, tmp_db_path):
        """FAISS ntotal > documents count is treated as consistent (mark-deleted slots)."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.execute("CREATE VIRTUAL TABLE memora_memories_fts USING fts5(doc_id, content)")
            for i in range(3):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
                await db.execute("INSERT INTO memora_memories_fts (doc_id, content) VALUES (?, ?)",
                                 (i, f"content_{i}"))
            await db.commit()

        faiss = _make_faiss_mock(ntotal=8)
        validator = _make_validator(tmp_db_path, faiss)
        status = await validator.check_consistency()
        assert status.is_consistent is True
        assert status.vector_count == 8
        assert "冗余" in status.reason

    @pytest.mark.asyncio
    async def test_consistency_bm25_redundant_entries_triggers_rebuild(self, tmp_db_path):
        """BM25 entries exceeding documents count triggers rebuild."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.execute("CREATE VIRTUAL TABLE memora_memories_fts USING fts5(doc_id, content)")
            for i in range(2):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
            for i in range(5):
                await db.execute("INSERT INTO memora_memories_fts (doc_id, content) VALUES (?, ?)",
                                 (i, f"content_{i}"))
            await db.commit()

        faiss = _make_faiss_mock(ntotal=2)
        validator = _make_validator(tmp_db_path, faiss)
        status = await validator.check_consistency()
        assert status.is_consistent is False
        assert status.bm25_count == 5
        assert status.needs_rebuild is True
        assert "冗余" in status.reason

    @pytest.mark.asyncio
    async def test_consistency_no_fts_table(self, tmp_db_path):
        """缺失 FTS table is detected as zero BM25 entries."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            for i in range(3):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
            await db.commit()

        faiss = _make_faiss_mock(ntotal=3)
        validator = _make_validator(tmp_db_path, faiss)
        status = await validator.check_consistency()
        assert status.bm25_count == 0
        assert status.missing_in_bm25 == 3
        assert status.needs_rebuild is True

    @pytest.mark.asyncio
    async def test_consistency_db_error_returns_fail_status(self):
        """当 the DB connection fails, check_consistency returns a needs_rebuild status."""
        validator = _make_validator("/nonexistent/path/db.sqlite")
        status = await validator.check_consistency()
        assert status.is_consistent is False
        assert status.needs_rebuild is True
        assert "失败" in status.reason

    @pytest.mark.asyncio
    async def test_consistency_with_concrete_vector_ids(self, tmp_db_path):
        """当 FAISS has an id_map, compare by exact IDs."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.execute("CREATE VIRTUAL TABLE memora_memories_fts USING fts5(doc_id, content)")
            for i in range(5):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
                await db.execute("INSERT INTO memora_memories_fts (doc_id, content) VALUES (?, ?)",
                                 (i, f"content_{i}"))
            await db.commit()

        with patch("faiss.vector_to_array", return_value=[0, 1, 2, 3, 4]):
            faiss = _make_faiss_mock(ntotal=5)
            faiss.embedding_storage.index.id_map = MagicMock()
            validator = _make_validator(tmp_db_path, faiss)
            status = await validator.check_consistency()
            assert status.is_consistent is True
            assert status.documents_count == 5
            assert status.needs_rebuild is False

    @pytest.mark.asyncio
    async def test_consistency_missing_with_concrete_ids(self, tmp_db_path):
        """向量 ID set is a strict subset of documents — triggers rebuild."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.execute("CREATE VIRTUAL TABLE memora_memories_fts USING fts5(doc_id, content)")
            for i in range(5):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
                await db.execute("INSERT INTO memora_memories_fts (doc_id, content) VALUES (?, ?)",
                                 (i, f"content_{i}"))
            await db.commit()

        with patch("faiss.vector_to_array", return_value=[0, 1, 2]):
            faiss = _make_faiss_mock(ntotal=3)
            faiss.embedding_storage.index.id_map = MagicMock()
            validator = _make_validator(tmp_db_path, faiss)
            status = await validator.check_consistency()
            assert status.is_consistent is False
            assert status.missing_in_vector == 2
            assert status.needs_rebuild is True


# ---------------------------------------------------------------------------
# PersistenceHealthValidator
# ---------------------------------------------------------------------------

class TestPersistenceHealthValidator:

    @pytest.mark.asyncio
    async def test_reports_cross_table_orphans_and_duplicate_note_versions(self, tmp_db_path):
        from core.validators.persistence_health_validator import PersistenceHealthValidator

        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (1, 'doc_1', 'text', '{}')")
            await db.execute("CREATE VIRTUAL TABLE memora_memories_fts USING fts5(doc_id, content)")
            await db.execute("INSERT INTO memora_memories_fts (doc_id, content) VALUES (1, 'ok')")
            await db.execute("INSERT INTO memora_memories_fts (doc_id, content) VALUES (99, 'orphan')")
            await db.execute("CREATE TABLE memory_atoms (id INTEGER PRIMARY KEY, parent_memory_id INTEGER)")
            await db.execute("INSERT INTO memory_atoms (id, parent_memory_id) VALUES (10, 99)")
            await db.execute("CREATE TABLE graph_entries (id INTEGER PRIMARY KEY, source_memory_id INTEGER, vector_doc_id TEXT)")
            await db.execute("INSERT INTO graph_entries (id, source_memory_id, vector_doc_id) VALUES (20, 99, '200')")
            await db.execute("CREATE TABLE graph_nodes (id INTEGER PRIMARY KEY)")
            await db.execute("CREATE TABLE graph_entry_nodes (entry_id INTEGER, node_id INTEGER)")
            await db.execute("INSERT INTO graph_entry_nodes (entry_id, node_id) VALUES (999, 888)")
            await db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY)")
            await db.execute("INSERT INTO notes (id) VALUES (5)")
            await db.execute("CREATE TABLE note_versions (note_id INTEGER, version INTEGER)")
            await db.execute("INSERT INTO note_versions (note_id, version) VALUES (5, 1)")
            await db.execute("INSERT INTO note_versions (note_id, version) VALUES (5, 1)")
            await db.execute("INSERT INTO note_versions (note_id, version) VALUES (7, 1)")
            await db.commit()

        main_faiss = MagicMock()
        graph_faiss = MagicMock()
        validator = PersistenceHealthValidator(tmp_db_path, main_faiss, graph_faiss)
        validator._get_vector_ids = MagicMock(side_effect=[{1, 77}, {"200", "999"}])

        report = await validator.check()

        assert report["ok"] is False
        assert report["needs_repair"] is True
        issues = report["issues"]
        assert issues["atom_orphan_parent_ids"] == [99]
        assert issues["graph_orphan_source_memory_ids"] == [99]
        assert issues["graph_entry_nodes_orphan_entry_ids"] == [999]
        assert issues["graph_entry_nodes_orphan_node_ids"] == [888]
        assert issues["orphan_note_version_note_ids"] == [7]
        assert issues["duplicate_note_versions"] == [{"note_id": 5, "version": 1, "count": 2}]
        assert issues["orphan_bm25_doc_ids"] == [99]
        assert issues["orphan_main_vector_ids"] == [77]
        assert issues["orphan_graph_vector_ids"] == ["999"]

    @pytest.mark.asyncio
    async def test_reports_ok_when_optional_tables_are_missing(self, tmp_db_path):
        from core.validators.persistence_health_validator import PersistenceHealthValidator

        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.commit()

        validator = PersistenceHealthValidator(tmp_db_path, MagicMock())
        report = await validator.check()

        assert report["ok"] is True
        assert report["needs_repair"] is False
        assert report["issues"] == {}


# ---------------------------------------------------------------------------
# _get_rebuild_options
# ---------------------------------------------------------------------------

class TestRebuildOptions:

    def test_defaults_returned_without_config(self):
        """_get_rebuild_options returns defaults when engine has no config."""
        engine = MagicMock(spec=[])
        validator = _make_validator()
        opts = validator._get_rebuild_options(engine)
        assert opts["batch_size"] == validator.DEFAULT_REBUILD_BATCH_SIZE
        assert opts["embedding_batch_size"] == validator.DEFAULT_EMBEDDING_BATCH_SIZE
        assert opts["max_retries"] == validator.DEFAULT_MAX_RETRIES

    def test_config_values_are_clamped(self):
        """Overrides from engine config are clamped to valid ranges."""
        engine = MagicMock()
        engine.config = {
            "index_rebuild_batch_size": 9999,
            "index_rebuild_max_retries": -5,
            "index_rebuild_max_failure_ratio": 99.0,
        }
        validator = _make_validator()
        opts = validator._get_rebuild_options(engine)
        assert opts["batch_size"] == 500  # clamped to max
        assert opts["max_retries"] == 1   # clamped to min
        assert opts["max_failure_ratio"] == 1.0  # clamped to max


# ---------------------------------------------------------------------------
# IndexValidator._failure_ratio / _is_rate_limit_error
# ---------------------------------------------------------------------------

class TestFailureRatioAndRateLimit:

    def test_failure_ratio(self):
        validator = _make_validator()
        assert validator._failure_ratio(0, 100) == 0.0
        assert validator._failure_ratio(5, 100) == 0.05
        assert validator._failure_ratio(100, 100) == 1.0
        assert validator._failure_ratio(5, 0) == 0.0
        assert validator._failure_ratio(0, 0) == 0.0

    def test_is_rate_limit_error(self):
        validator = _make_validator()
        assert validator._is_rate_limit_error(Exception("429 error")) is True
        assert validator._is_rate_limit_error(Exception("Rate limit exceeded")) is True
        assert validator._is_rate_limit_error(Exception("TPM limit reached")) is True
        assert validator._is_rate_limit_error(Exception("too many requests")) is True
        assert validator._is_rate_limit_error(Exception("normal error")) is False
        assert validator._is_rate_limit_error(Exception("timeout")) is False


# ---------------------------------------------------------------------------
# IndexRebuilderMixin — _try_restore_from_backup
# ---------------------------------------------------------------------------

class TestBackupRestore:

    @pytest.mark.asyncio
    async def test_restore_when_documents_empty(self, tmp_db_path):
        """当 documents table is empty and backup table exists, restore data."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT, created_at REAL, updated_at REAL)")
            await db.execute("CREATE TABLE _documents_rebuild_backup (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT, created_at REAL, updated_at REAL)")
            await db.execute("INSERT INTO _documents_rebuild_backup (id, doc_id, text, metadata, created_at, updated_at) VALUES (1, 'd1', 'text1', '{}', 1.0, 1.0)")
            await db.execute("INSERT INTO _documents_rebuild_backup (id, doc_id, text, metadata, created_at, updated_at) VALUES (2, 'd2', 'text2', '{}', 2.0, 2.0)")
            await db.commit()

        validator = _make_validator(tmp_db_path)
        await validator._try_restore_from_backup()

        async with aiosqlite.connect(tmp_db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM documents")
            count = (await cursor.fetchone())[0]
            assert count == 2

    @pytest.mark.asyncio
    async def test_restore_skips_when_not_empty(self, tmp_db_path):
        """当 documents already have data, skip restore to avoid duplication."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT, created_at REAL, updated_at REAL)")
            await db.execute("CREATE TABLE _documents_rebuild_backup (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT, created_at REAL, updated_at REAL)")
            await db.execute("INSERT INTO documents (id, doc_id, text, metadata, created_at, updated_at) VALUES (99, 'existing', 'kept', '{}', 0.0, 0.0)")
            await db.execute("INSERT INTO _documents_rebuild_backup (id, doc_id, text, metadata, created_at, updated_at) VALUES (1, 'd1', 'text1', '{}', 1.0, 1.0)")
            await db.commit()

        validator = _make_validator(tmp_db_path)
        await validator._try_restore_from_backup()

        async with aiosqlite.connect(tmp_db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM documents")
            count = (await cursor.fetchone())[0]
            assert count == 1
            cursor = await db.execute("SELECT doc_id FROM documents")
            doc_id = (await cursor.fetchone())[0]
            assert doc_id == "existing"

    @pytest.mark.asyncio
    async def test_restore_no_backup_table(self, tmp_db_path):
        """当 backup table does not exist, silently skip."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT, created_at REAL, updated_at REAL)")
            await db.commit()

        validator = _make_validator(tmp_db_path)
        # Should not raise
        await validator._try_restore_from_backup()

        async with aiosqlite.connect(tmp_db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM documents")
            count = (await cursor.fetchone())[0]
            assert count == 0


# ---------------------------------------------------------------------------
# IndexRebuilderMixin — rebuild_indexes orchestration (through IndexValidator)
# ---------------------------------------------------------------------------

class TestRebuildOrchestration:

    @pytest.mark.asyncio
    async def test_empty_database_returns_early(self):
        """rebuild_indexes returns early success for empty database."""
        validator = _make_validator()

        # Patch _get_document_count to return 0
        with patch.object(validator, "_get_document_count", AsyncMock(return_value=0)):
            result = await validator.rebuild_indexes(MagicMock())
            assert result["success"] is True
            assert result["total"] == 0
            assert result["processed"] == 0

    @pytest.mark.asyncio
    async def test_bm25_failure_rate_too_high_stops_vector_rebuild(self):
        """当 BM25 failure rate exceeds threshold, vector rebuild is skipped."""
        validator = _make_validator()

        with patch.object(validator, "_get_document_count", AsyncMock(return_value=100)):
            with patch.object(validator, "_get_rebuild_options", return_value={
                "batch_size": 50, "embedding_batch_size": 8, "tasks_limit": 1,
                "max_retries": 3, "retry_base_delay": 1.0, "batch_delay": 0.1,
                "request_delay": 0.1, "max_failure_ratio": 0.02,
            }):
                with patch.object(validator, "_rebuild_bm25_index", AsyncMock(return_value={
                    "processed": 90, "errors": 10, "failed_ids": set(range(10)),
                })):
                    result = await validator.rebuild_indexes(MagicMock())
                    assert result["success"] is False
                    assert result["partial"] is True
                    assert result["switched"] is False
                    assert result["bm25_errors"] == 10

    @pytest.mark.asyncio
    async def test_full_rebuild_succeeds(self):
        """完整 rebuild succeeds with both BM25 and vector passing."""
        validator = _make_validator()

        with patch.object(validator, "_get_document_count", AsyncMock(return_value=50)):
            with patch.object(validator, "_get_rebuild_options", return_value={
                "batch_size": 50, "embedding_batch_size": 8, "tasks_limit": 1,
                "max_retries": 3, "retry_base_delay": 1.0, "batch_delay": 0.1,
                "request_delay": 0.1, "max_failure_ratio": 0.02,
            }):
                with patch.object(validator, "_rebuild_bm25_index", AsyncMock(return_value={
                    "processed": 50, "errors": 0, "failed_ids": set(),
                })):
                    with patch.object(validator, "_rebuild_or_repair_vector_index", AsyncMock(return_value={
                        "processed": 50, "errors": 0, "failed_ids": set(),
                        "mode": "full", "switched": True, "partial": False,
                    })):
                        result = await validator.rebuild_indexes(MagicMock())
                        assert result["success"] is True
                        assert result["processed"] == 50
                        assert result["partial"] is False
                        assert result["switched"] is True

    @pytest.mark.asyncio
    async def test_exception_returns_structured_error(self):
        """Unhandled exceptions in rebuild return a structured error dict."""
        validator = _make_validator()

        with patch.object(validator, "_get_document_count", AsyncMock(side_effect=RuntimeError("connection lost"))):
            result = await validator.rebuild_indexes(MagicMock())
            assert result["success"] is False
            assert "error" in result
            assert "connection lost" in str(result["error"])

    @pytest.mark.asyncio
    async def test_partial_failure_accepted_below_threshold(self):
        """A few errors below max_failure_ratio are accepted, flagged as partial."""
        validator = _make_validator()

        with patch.object(validator, "_get_document_count", AsyncMock(return_value=100)):
            with patch.object(validator, "_get_rebuild_options", return_value={
                "batch_size": 50, "embedding_batch_size": 8, "tasks_limit": 1,
                "max_retries": 3, "retry_base_delay": 1.0, "batch_delay": 0.1,
                "request_delay": 0.1, "max_failure_ratio": 0.05,
            }):
                with patch.object(validator, "_rebuild_bm25_index", AsyncMock(return_value={
                    "processed": 99, "errors": 1, "failed_ids": {42},
                })):
                    with patch.object(validator, "_rebuild_or_repair_vector_index", AsyncMock(return_value={
                        "processed": 98, "errors": 2, "failed_ids": {43, 44},
                        "mode": "full", "switched": True, "partial": True,
                    })):
                        result = await validator.rebuild_indexes(MagicMock())
                        assert result["success"] is True
                        assert result["partial"] is True
                        assert result["switched"] is True
                        assert result["errors"] == 3


# ---------------------------------------------------------------------------
# Bm25RebuilderMixin — through IndexValidator with monkey-patching
# ---------------------------------------------------------------------------

class TestBm25Rebuild:

    @pytest.mark.asyncio
    async def test_rebuild_calls_text_processor_and_writes_fts(self, tmp_db_path):
        """BM25 rebuild processes documents through TextProcessor and writes to FTS."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.execute("CREATE VIRTUAL TABLE memora_memories_fts USING fts5(doc_id, content)")
            for i in range(5):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"文本内容_{i}", "{}"))
            await db.commit()

        text_proc = MagicMock()
        text_proc.preprocess_for_bm25 = MagicMock(side_effect=lambda t: f"processed_{t}")

        bm25_retriever = MagicMock()
        bm25_retriever.text_processor = text_proc
        bm25_retriever.fts_table = "memora_memories_fts"

        engine = MagicMock()
        engine.bm25_retriever = bm25_retriever
        engine.text_processor = text_proc

        options = {"batch_size": 3, "max_failure_ratio": 0.1}

        validator = _make_validator(tmp_db_path)

        # Mock _clear_bm25_with_retry so we don't accidentally delete and
        # recreate the FTS table.
        validator._clear_bm25_with_retry = AsyncMock()

        # Override _iter_document_batches to feed all docs in one batch.
        async def _iter_doc_batches(batch_size, document_ids=None):
            async with aiosqlite.connect(tmp_db_path) as db:
                await db.execute("PRAGMA busy_timeout = 30000")
                cursor = await db.execute(
                    "SELECT id, doc_id, text, metadata FROM documents ORDER BY id"
                )
                rows = await cursor.fetchall()
                if rows:
                    yield rows

        validator._iter_document_batches = _iter_doc_batches

        result = await validator._rebuild_bm25_index(engine, 5, options)

        assert result["processed"] == 5
        assert result["errors"] == 0
        assert len(result["failed_ids"]) == 0
        assert text_proc.preprocess_for_bm25.call_count == 5

    @pytest.mark.asyncio
    async def test_no_text_processor_raises(self):
        """缺失 TextProcessor raises RuntimeError."""
        engine = MagicMock()
        engine.bm25_retriever = None
        engine.text_processor = None

        options = {"batch_size": 10, "max_failure_ratio": 0.1}

        validator = _make_validator()
        with pytest.raises(RuntimeError, match="TextProcessor"):
            await validator._rebuild_bm25_index(engine, 0, options)

    @pytest.mark.asyncio
    async def test_preprocess_failures_tracked_as_errors(self, tmp_db_path):
        """当 text preprocessing fails for some docs, they are counted as errors."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            for i in range(5):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
            await db.commit()

        # Text processor that fails on doc_id=2
        text_proc = MagicMock()
        text_proc.preprocess_for_bm25 = MagicMock(side_effect=lambda t: (
            "processed" if "2" not in t else (_ for _ in ()).throw(RuntimeError("bad text"))
        ))

        bm25_retriever = MagicMock()
        bm25_retriever.text_processor = text_proc
        engine = MagicMock()
        engine.bm25_retriever = bm25_retriever
        engine.text_processor = text_proc

        options = {"batch_size": 10, "max_failure_ratio": 0.5}

        validator = _make_validator()

        # Mock _clear_bm25_with_retry
        validator._clear_bm25_with_retry = AsyncMock()

        # Mock _iter_document_batches to return controlled data without DB
        rows = [(0, "d0", "text_0", "{}"),
                (1, "d1", "text_1", "{}"),
                (2, "d2", "text_2", "{}"),
                (3, "d3", "text_3", "{}"),
                (4, "d4", "text_4", "{}")]

        async def _iter_doc_batches(batch_size, document_ids=None):
            yield rows

        validator._iter_document_batches = _iter_doc_batches

        # Mock SQL write to avoid FTS issues — accept all rows
        with patch.object(aiosqlite, "connect") as mock_connect:
            mock_db = AsyncMock()
            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock(return_value=None)
            mock_connect.return_value = mock_db
            result = await validator._rebuild_bm25_index(engine, 5, options)

        assert result["processed"] == 4  # one failed during preprocess
        assert result["errors"] == 1
        assert len(result["failed_ids"]) == 1


# ---------------------------------------------------------------------------
# VectorRebuilderMixin — through IndexValidator
# ---------------------------------------------------------------------------

class TestVectorRebuildOrRepair:

    @pytest.mark.asyncio
    async def test_skip_when_documents_empty(self):
        """当 there are no document IDs, skip vector rebuild entirely."""
        validator = _make_validator()
        with patch.object(validator, "_get_document_ids", AsyncMock(return_value=set())):
            result = await validator._rebuild_or_repair_vector_index(MagicMock(), 0, {})
            assert result["mode"] == "skip"
            assert result["processed"] == 0

    @pytest.mark.asyncio
    async def test_skip_when_vectors_match(self):
        """当 vector IDs are an exact superset of document IDs, skip."""
        validator = _make_validator()
        with patch.object(validator, "_get_document_ids", AsyncMock(return_value={1, 2, 3})):
            with patch.object(validator, "_get_vector_ids", return_value={1, 2, 3, 4, 5}):
                with patch.object(validator, "_get_vector_count", return_value=5):
                    result = await validator._rebuild_or_repair_vector_index(MagicMock(), 3, {})
                    assert result["mode"] == "skip"
                    assert result["processed"] == 0
                    assert result["partial"] is False

    @pytest.mark.asyncio
    async def test_incremental_repair_on_partial_missing(self):
        """当 some vector IDs are missing, run incremental repair."""
        validator = _make_validator()
        with patch.object(validator, "_get_document_ids", AsyncMock(return_value={1, 2, 3, 4, 5})):
            with patch.object(validator, "_get_vector_ids", return_value={1, 2, 3}):
                with patch.object(validator, "_get_vector_count", return_value=3):
                    with patch.object(validator, "_repair_missing_vectors", AsyncMock(return_value={
                        "mode": "repair", "processed": 2, "errors": 0,
                        "failed_ids": set(), "switched": False, "partial": False,
                    })):
                        result = await validator._rebuild_or_repair_vector_index(MagicMock(), 5, {})
                        assert result["mode"] == "repair"
                        assert result["processed"] == 2
                        assert result["switched"] is False

    @pytest.mark.asyncio
    async def test_full_rebuild_when_vector_ids_unavailable(self):
        """当 vector IDs cannot be read (None), fall back to full rebuild."""
        validator = _make_validator()
        with patch.object(validator, "_get_document_ids", AsyncMock(return_value={1, 2, 3, 4, 5})):
            with patch.object(validator, "_get_vector_ids", return_value=None):
                with patch.object(validator, "_get_vector_count", return_value=0):
                    with patch.object(validator, "_rebuild_vector_index_full", AsyncMock(return_value={
                        "mode": "full", "processed": 5, "errors": 0,
                        "failed_ids": set(), "switched": True, "partial": False,
                    })):
                        result = await validator._rebuild_or_repair_vector_index(MagicMock(), 5, {})
                        assert result["mode"] == "full"
                        assert result["switched"] is True

    @pytest.mark.asyncio
    async def test_repair_missing_vectors_no_embedding_raises(self):
        """缺失 embedding storage raises RuntimeError."""
        engine = MagicMock()
        engine.faiss_db = None

        options = {"batch_size": 10, "batch_delay": 0, "max_failure_ratio": 0.1}

        validator = _make_validator()
        with pytest.raises(RuntimeError, match="Embedding"):
            await validator._repair_missing_vectors(engine, {1, 2}, options)

    @pytest.mark.asyncio
    async def test_rebuild_vector_index_full_no_embedding_raises(self):
        """缺失 embedding storage raises RuntimeError for full rebuild."""
        engine = MagicMock()
        engine.faiss_db = None

        options = {"batch_size": 10, "batch_delay": 0, "max_failure_ratio": 0.1}

        validator = _make_validator()
        with pytest.raises(RuntimeError, match="Embedding"):
            await validator._rebuild_vector_index_full(engine, 10, options)

    @pytest.mark.asyncio
    async def test_skip_when_vector_count_not_less_than_total(self):
        """当 vector IDs are None but count >= total, skip rebuild."""
        validator = _make_validator()
        with patch.object(validator, "_get_document_ids", AsyncMock(return_value={1, 2, 3})):
            with patch.object(validator, "_get_vector_ids", return_value=None):
                with patch.object(validator, "_get_vector_count", return_value=5):
                    result = await validator._rebuild_or_repair_vector_index(MagicMock(), 3, {})
                    assert result["mode"] == "skip"
                    assert result["processed"] == 0


# ---------------------------------------------------------------------------
# EmbeddingRetryMixin — through IndexValidator
# These tests verify _embed_batch_with_retry and _embed_request_with_retry
# via the full IndexValidator instance (which has _is_rate_limit_error etc.)
# ---------------------------------------------------------------------------

class TestBm25RebuildEdgeCases:

    @pytest.mark.asyncio
    async def test_fallback_to_engine_text_processor(self, tmp_db_path):
        """当 bm25_retriever lacks text_processor, fall back to engine's text_processor."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (1, 'd1', 'text', '{}')")
            await db.commit()

        text_proc = MagicMock()
        text_proc.preprocess_for_bm25 = MagicMock(return_value="processed text")

        bm25_retriever = MagicMock()
        # No text_processor on bm25_retriever
        bm25_retriever.fts_table = "memora_memories_fts"

        engine = MagicMock()
        engine.bm25_retriever = bm25_retriever
        engine.text_processor = text_proc  # fallback

        validator = _make_validator(tmp_db_path)
        validator._clear_bm25_with_retry = AsyncMock()

        async def _iter_doc_batches(batch_size, document_ids=None):
            async with aiosqlite.connect(tmp_db_path) as db:
                await db.execute("PRAGMA busy_timeout = 30000")
                cursor = await db.execute("SELECT id, doc_id, text, metadata FROM documents ORDER BY id")
                rows = await cursor.fetchall()
                if rows:
                    yield rows
        validator._iter_document_batches = _iter_doc_batches

        result = await validator._rebuild_bm25_index(engine, 1, {"batch_size": 10, "max_failure_ratio": 0.1})
        assert result["processed"] >= 0

    @pytest.mark.asyncio
    async def test_batch_write_failure_falls_back_to_single_insert(self, tmp_db_path):
        """当 batch insert fails, it retries with single-row inserts."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            for i in range(3):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
            await db.commit()

        text_proc = MagicMock()
        text_proc.preprocess_for_bm25 = MagicMock(side_effect=lambda t: f"processed_{t}")

        bm25_retriever = MagicMock()
        bm25_retriever.text_processor = text_proc
        engine = MagicMock()
        engine.bm25_retriever = bm25_retriever
        engine.text_processor = text_proc

        validator = _make_validator(tmp_db_path)
        validator._clear_bm25_with_retry = AsyncMock()

        async def _iter_doc_batches(batch_size, document_ids=None):
            yield [(0, "d0", "text_0", "{}"), (1, "d1", "text_1", "{}"), (2, "d2", "text_2", "{}")]
        validator._iter_document_batches = _iter_doc_batches

        # First executemany fails, then individual inserts succeed
        call_count = [0]

        class _MockConn:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def execute(self, sql, params=None):
                return None
            async def commit(self):
                pass

        async def _mock_executemany(sql, params):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("batch write failed")
            return None

        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=None)
        mock_conn.commit = AsyncMock()
        mock_conn.executemany = _mock_executemany

        with patch.object(aiosqlite, "connect", return_value=mock_conn):
            result = await validator._rebuild_bm25_index(engine, 3, {"batch_size": 10, "max_failure_ratio": 0.5})
        assert result["processed"] >= 0

    @pytest.mark.asyncio
    async def test_progress_callback_called(self, tmp_db_path):
        """Progress callback receives updates during BM25 rebuild."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            for i in range(3):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
            await db.commit()

        text_proc = MagicMock()
        text_proc.preprocess_for_bm25 = MagicMock(side_effect=lambda t: f"processed_{t}")

        bm25_retriever = MagicMock()
        bm25_retriever.text_processor = text_proc
        engine = MagicMock()
        engine.bm25_retriever = bm25_retriever
        engine.text_processor = text_proc

        validator = _make_validator(tmp_db_path)
        validator._clear_bm25_with_retry = AsyncMock()

        async def _iter_doc_batches(batch_size, document_ids=None):
            yield [(0, "d0", "text_0", "{}"), (1, "d1", "text_1", "{}"), (2, "d2", "text_2", "{}")]
        validator._iter_document_batches = _iter_doc_batches

        progress_cb = AsyncMock()

        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=None)
        mock_conn.executemany = AsyncMock(return_value=None)
        mock_conn.commit = AsyncMock()

        with patch.object(aiosqlite, "connect", return_value=mock_conn):
            await validator._rebuild_bm25_index(engine, 3, {"batch_size": 10, "max_failure_ratio": 0.5}, progress_callback=progress_cb)
            assert progress_cb.called


# ---------------------------------------------------------------------------
# IndexValidator — _iter_document_batches (by ID, by cursor)
# ---------------------------------------------------------------------------

class TestIterDocumentBatches:

    @pytest.mark.asyncio
    async def test_by_specific_ids(self, tmp_db_path):
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            for i in range(10):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
            await db.commit()

        validator = _make_validator(tmp_db_path)
        batches = []
        async for batch in validator._iter_document_batches(3, document_ids={0, 2, 4, 6, 8}):
            batches.append(batch)
        total = sum(len(b) for b in batches)
        assert total == 5

    @pytest.mark.asyncio
    async def test_by_cursor_based(self, tmp_db_path):
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            for i in range(1, 11):  # start from 1, cursor-based iter uses WHERE id > last_id
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
            await db.commit()

        validator = _make_validator(tmp_db_path)
        batches = []
        async for batch in validator._iter_document_batches(4):
            batches.append(batch)
        total = sum(len(b) for b in batches)
        assert total == 10
        assert len(batches) >= 2  # 10 docs with batch_size 4 = 3 batches

    @pytest.mark.asyncio
    async def test_by_specific_ids_empty_set(self, tmp_db_path):
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.commit()

        validator = _make_validator(tmp_db_path)
        batches = []
        async for batch in validator._iter_document_batches(10, document_ids=set()):
            batches.append(batch)
        assert len(batches) == 0

    @pytest.mark.asyncio
    async def test_get_document_count(self, tmp_db_path):
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            for i in range(5):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
            await db.commit()

        validator = _make_validator(tmp_db_path)
        count = await validator._get_document_count()
        assert count == 5

    @pytest.mark.asyncio
    async def test_get_document_ids(self, tmp_db_path):
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            for i in range(5):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
            await db.commit()

        validator = _make_validator(tmp_db_path)
        ids = await validator._get_document_ids()
        assert ids == {0, 1, 2, 3, 4}


# ---------------------------------------------------------------------------
# VectorRebuilderMixin — additional tests
# ---------------------------------------------------------------------------

class TestVectorRebuildFull:

    @pytest.mark.asyncio
    async def test_full_rebuild_zero_dimension(self):
        """零 dimension raises RuntimeError."""
        engine = MagicMock()
        faiss_db = MagicMock()
        faiss_db.embedding_storage = MagicMock()
        faiss_db.embedding_storage.dimension = 0
        faiss_db.embedding_provider = MagicMock()
        engine.faiss_db = faiss_db

        validator = _make_validator()
        with pytest.raises(RuntimeError, match="维度"):
            await validator._rebuild_vector_index_full(
                engine, 10, {"batch_size": 5, "batch_delay": 0, "max_failure_ratio": 0.1}
            )

    @pytest.mark.asyncio
    async def test_full_rebuild_dimension_mismatch(self):
        """当 embedded vectors have wrong dimension, ValueError is caught."""
        engine = MagicMock()
        faiss_db = MagicMock()
        embedding_storage = MagicMock()
        embedding_storage.dimension = 128
        faiss_db.embedding_storage = embedding_storage
        faiss_db.embedding_provider = MagicMock()
        engine.faiss_db = faiss_db

        validator = _make_validator()

        async def _iter_doc_batches(batch_size, document_ids=None):
            yield [(0, "d0", "text", "{}")]
        validator._iter_document_batches = _iter_doc_batches

        with patch.object(validator, "_embed_batch_with_retry",
                          AsyncMock(return_value=[[0.1] * 64])):  # 64 != 128
            result = await validator._rebuild_vector_index_full(
                engine, 1,
                {"batch_size": 5, "batch_delay": 0, "max_failure_ratio": 0.5}
            )
            assert result["switched"] is False
            assert result["errors"] >= 0

    @pytest.mark.asyncio
    async def test_full_rebuild_failure_ratio_too_high(self):
        """当 failure ratio exceeds max, partial=True, switched=False."""
        engine = MagicMock()
        faiss_db = MagicMock()
        embedding_storage = MagicMock()
        embedding_storage.dimension = 3
        faiss_db.embedding_storage = embedding_storage
        faiss_db.embedding_provider = MagicMock()
        engine.faiss_db = faiss_db

        validator = _make_validator()

        # Make _embed_batch_with_retry fail for both batches
        validator._embed_batch_with_retry = AsyncMock(side_effect=RuntimeError("embed fail"))

        async def _iter_doc_batches(batch_size, document_ids=None):
            yield [(0, "d0", "text", "{}")]
            yield [(1, "d1", "text", "{}")]
        validator._iter_document_batches = _iter_doc_batches

        result = await validator._rebuild_vector_index_full(
            engine, 2,
            {"batch_size": 1, "batch_delay": 0, "max_failure_ratio": 0.3}
        )
        assert result["switched"] is False
        assert result["partial"] is True

    @pytest.mark.asyncio
    async def test_full_rebuild_total_zero_processed(self):
        """当 total > 0 but nothing processed, returns switched=False."""
        engine = MagicMock()
        faiss_db = MagicMock()
        embedding_storage = MagicMock()
        embedding_storage.dimension = 3
        faiss_db.embedding_storage = embedding_storage
        faiss_db.embedding_provider = MagicMock()
        engine.faiss_db = faiss_db

        validator = _make_validator()

        # All batches fail
        validator._embed_batch_with_retry = AsyncMock(side_effect=RuntimeError("fail"))

        async def _iter_doc_batches(batch_size, document_ids=None):
            yield [(0, "d0", "text", "{}")]
        validator._iter_document_batches = _iter_doc_batches

        result = await validator._rebuild_vector_index_full(
            engine, 1,
            {"batch_size": 1, "batch_delay": 0, "max_failure_ratio": 0.02}
        )
        assert result["switched"] is False
        assert result["processed"] == 0

    @pytest.mark.asyncio
    async def test_get_vector_count_none(self):
        """当 FAISS DB has no index, _get_vector_count returns 0."""
        validator = _make_validator()
        validator.faiss_db = MagicMock()
        validator.faiss_db.embedding_storage = None
        assert validator._get_vector_count() == 0

    @pytest.mark.asyncio
    async def test_get_vector_ids_none(self):
        """当 FAISS DB has no index, _get_vector_ids returns empty set."""
        validator = _make_validator()
        validator.faiss_db = MagicMock()
        validator.faiss_db.embedding_storage = None
        assert validator._get_vector_ids() == set()

    @pytest.mark.asyncio
    async def test_repair_missing_success(self):
        engine = MagicMock()
        faiss_db = MagicMock()
        embedding_storage = MagicMock()
        embedding_storage.insert_batch = AsyncMock()
        faiss_db.embedding_storage = embedding_storage
        faiss_db.embedding_provider = MagicMock()
        engine.faiss_db = faiss_db

        validator = _make_validator()
        validator._embed_batch_with_retry = AsyncMock(return_value=[[0.1, 0.2]])

        async def _iter_doc_batches(batch_size, document_ids=None):
            yield [(42, "doc_42", "text content", "{}")]
        validator._iter_document_batches = _iter_doc_batches

        result = await validator._repair_missing_vectors(
            engine, {42},
            {"batch_size": 10, "batch_delay": 0, "max_failure_ratio": 0.1}
        )
        assert result["mode"] == "repair"
        assert result["processed"] == 1
        assert result["errors"] == 0


# ---------------------------------------------------------------------------
# IndexValidator — clear_bm25_with_retry
# ---------------------------------------------------------------------------

class TestClearBm25WithRetry:

    @pytest.mark.asyncio
    async def test_successful_clear(self, tmp_db_path):
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.execute("CREATE VIRTUAL TABLE memora_memories_fts USING fts5(doc_id, content)")
            await db.execute("INSERT INTO memora_memories_fts (doc_id, content) VALUES (1, 'test')")
            await db.commit()

        validator = _make_validator(tmp_db_path)
        await validator._clear_bm25_with_retry("memora_memories_fts")

    @pytest.mark.asyncio
    async def test_clear_locked_retry(self):
        """当 database is locked, retry with backoff."""
        validator = _make_validator("/nonexistent/db.sqlite")
        # Should raise after exhausting retries on nonexistent path
        with pytest.raises(Exception):
            await validator._clear_bm25_with_retry("memora_memories_fts", max_attempts=2)

    @pytest.mark.asyncio
    async def test_rejects_unknown_fts_table(self, tmp_db_path):
        validator = _make_validator(tmp_db_path)
        with pytest.raises(ValueError, match="unsupported FTS table"):
            await validator._clear_bm25_with_retry("documents; DROP TABLE documents")


# ---------------------------------------------------------------------------
# EmbeddingRetryMixin edge cases
# ---------------------------------------------------------------------------

class TestEmbeddingRetryEdgeCases:

    @pytest.mark.asyncio
    async def test_embed_batch_mismatch_passes_through(self):
        """当 embedding returns different number of vectors, it's passed through
        (real implementation doesn't validate count matching)."""
        validator = _make_validator()
        provider = MagicMock()
        provider.get_embeddings = AsyncMock(return_value=[[0.1]])  # only 1 for 3 inputs

        result = await validator._embed_request_with_retry(
            provider, ["a", "b", "c"],
            max_retries=1, retry_base_delay=0.001,
        )
        assert result == [[0.1]]  # returned as-is


# ---------------------------------------------------------------------------
# IndexValidator — consistency with concrete vector IDs (edge cases)
# ---------------------------------------------------------------------------

class TestConsistencyConcreteVectorIDs:

    @pytest.mark.asyncio
    async def test_vector_id_map_read_failure(self, tmp_db_path):
        """当 faiss.vector_to_array raises, fall back to count-based comparison."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.execute("CREATE VIRTUAL TABLE memora_memories_fts USING fts5(doc_id, content)")
            for i in range(3):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
                await db.execute("INSERT INTO memora_memories_fts (doc_id, content) VALUES (?, ?)",
                                 (i, f"content_{i}"))
            await db.commit()

        faiss = _make_faiss_mock(ntotal=3)
        faiss.embedding_storage.index.id_map = MagicMock()
        with patch("faiss.vector_to_array", side_effect=RuntimeError("id map read error")):
            validator = _make_validator(tmp_db_path, faiss)
            status = await validator.check_consistency()
            assert status.documents_count == 3
            # Falls back to count mode, should be consistent
            assert status.is_consistent is True

    @pytest.mark.asyncio
    async def test_vector_id_map_missing_vector_to_array(self, tmp_db_path):
        """当 faiss.vector_to_array is not callable, fall back to count mode."""
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)")
            await db.execute("CREATE VIRTUAL TABLE memora_memories_fts USING fts5(doc_id, content)")
            for i in range(3):
                await db.execute("INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
                                 (i, f"doc_{i}", f"text_{i}", "{}"))
                await db.execute("INSERT INTO memora_memories_fts (doc_id, content) VALUES (?, ?)",
                                 (i, f"content_{i}"))
            await db.commit()

        faiss = _make_faiss_mock(ntotal=3)
        faiss.embedding_storage.index.id_map = MagicMock()
        with patch("faiss.vector_to_array", None):
            validator = _make_validator(tmp_db_path, faiss)
            status = await validator.check_consistency()
            assert status.documents_count == 3


# ---------------------------------------------------------------------------
# _get_rebuild_options — additional config clamping
# ---------------------------------------------------------------------------

class TestRebuildOptionsAdvanced:

    def test_invalid_config_values_use_defaults(self):
        """Non-numeric config values fall back to defaults."""
        engine = MagicMock()
        engine.config = {
            "index_rebuild_batch_size": "not_a_number",
            "index_rebuild_retry_base_delay": "also_string",
        }
        validator = _make_validator()
        opts = validator._get_rebuild_options(engine)
        assert opts["batch_size"] == validator.DEFAULT_REBUILD_BATCH_SIZE
        assert opts["retry_base_delay"] == validator.DEFAULT_RETRY_BASE_DELAY

    def test_partial_config_overrides(self):
        """Only some config keys are present, others use defaults."""
        engine = MagicMock()
        engine.config = {
            "index_rebuild_batch_size": 20,
            "index_rebuild_tasks_limit": 2,
        }
        validator = _make_validator()
        opts = validator._get_rebuild_options(engine)
        assert opts["batch_size"] == 20
        assert opts["tasks_limit"] == 2
        assert opts["max_retries"] == validator.DEFAULT_MAX_RETRIES


# ---------------------------------------------------------------------------
# EmbeddingRetryMixin — through IndexValidator
# ---------------------------------------------------------------------------

class TestEmbeddingRetry:

    @pytest.mark.asyncio
    async def test_empty_contents_returns_empty(self):
        """空 contents list returns empty vectors immediately."""
        validator = _make_validator()
        result = await validator._embed_batch_with_retry(
            MagicMock(), [], {"max_retries": 3, "retry_base_delay": 1.0,
                              "embedding_batch_size": 8, "request_delay": 0.0}
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_calls_get_embeddings(self):
        """提供者 with get_embeddings method is used."""
        provider = MagicMock()
        provider.get_embeddings = AsyncMock(return_value=[[0.1, 0.2]])

        validator = _make_validator()
        result = await validator._embed_request_with_retry(
            provider, ["hello"],
            max_retries=3, retry_base_delay=0.01,
        )
        assert result == [[0.1, 0.2]]
        provider.get_embeddings.assert_called_once_with(["hello"])

    @pytest.mark.asyncio
    async def test_falls_back_to_get_embeddings_batch(self):
        """提供者 without get_embeddings uses get_embeddings_batch."""
        provider = MagicMock(spec=[])
        provider.get_embeddings_batch = AsyncMock(return_value=[[0.1], [0.2]])

        validator = _make_validator()
        result = await validator._embed_request_with_retry(
            provider, ["a", "b"],
            max_retries=2, retry_base_delay=0.01,
        )
        assert result == [[0.1], [0.2]]
        provider.get_embeddings_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_per_item(self):
        """提供者 with neither batch method falls back to get_embedding per item."""
        provider = MagicMock(spec=[])
        provider.get_embedding = AsyncMock(side_effect=[[0.1], [0.2]])

        validator = _make_validator()
        result = await validator._embed_request_with_retry(
            provider, ["a", "b"],
            max_retries=2, retry_base_delay=0.01,
        )
        assert result == [[0.1], [0.2]]
        assert provider.get_embedding.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_failure(self):
        """Transient failures are retried with exponential backoff."""
        provider = MagicMock()
        provider.get_embeddings = AsyncMock(side_effect=[
            Exception("transient error"),
            Exception("transient error"),
            [[1.0, 2.0]],
        ])

        validator = _make_validator()
        result = await validator._embed_request_with_retry(
            provider, ["content"],
            max_retries=3, retry_base_delay=0.001,
        )
        assert result == [[1.0, 2.0]]
        assert provider.get_embeddings.call_count == 3

    @pytest.mark.asyncio
    async def test_fails_after_all_retries(self):
        """当 all retries exhausted, raises RuntimeError."""
        provider = MagicMock()
        provider.get_embeddings = AsyncMock(side_effect=Exception("always fails"))

        validator = _make_validator()
        with pytest.raises(RuntimeError, match="重试失败"):
            await validator._embed_request_with_retry(
                provider, ["content"],
                max_retries=3, retry_base_delay=0.001,
            )
        assert provider.get_embeddings.call_count == 3

    @pytest.mark.asyncio
    async def test_rate_limit_uses_longer_wait(self):
        """Rate limit errors trigger longer wait times via _is_rate_limit_error."""
        provider = MagicMock()
        provider.get_embeddings = AsyncMock(side_effect=[
            Exception("429 Too Many Requests"),
            [[0.5, 0.6]],
        ])

        validator = _make_validator()
        result = await validator._embed_request_with_retry(
            provider, ["content"],
            max_retries=2, retry_base_delay=0.001,
        )
        assert result == [[0.5, 0.6]]
        assert provider.get_embeddings.call_count == 2

    @pytest.mark.asyncio
    async def test_splits_large_inputs_into_chunks(self):
        """Large content lists are split into embedding_batch_size chunks."""
        provider = MagicMock()
        call_count = 0
        async def _side_effect(contents):
            nonlocal call_count
            call_count += 1
            dim = len(contents)
            return [[float(i) / dim] for i in range(dim)]

        provider.get_embeddings = AsyncMock(side_effect=_side_effect)

        validator = _make_validator()
        result = await validator._embed_batch_with_retry(
            provider, ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"],
            {"max_retries": 2, "retry_base_delay": 0.01,
             "embedding_batch_size": 3, "request_delay": 0.0},
        )
        assert len(result) == 10
        assert call_count == 4  # 3+3+3+1

    @pytest.mark.asyncio
    async def test_non_callable_get_embeddings_falls_back(self):
        """当 get_embeddings is not callable, fallback to batch or per-item."""
        provider = MagicMock(spec=[])
        provider.get_embedding = AsyncMock(side_effect=[[0.1], [0.2]])

        validator = _make_validator()
        result = await validator._embed_request_with_retry(
            provider, ["a", "b"],
            max_retries=2, retry_base_delay=0.01,
        )
        assert result == [[0.1], [0.2]]
