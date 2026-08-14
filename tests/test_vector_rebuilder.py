"""测试 VectorRebuilderMixin — FAISS vector index repair and rebuild edge cases."""

import os
import tempfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import faiss
import numpy as np
import pytest

from core.features.memory.infrastructure.validators.vector_rebuilder import (
    VectorRebuilderMixin,
)

# ---------------------------------------------------------------------------
# Test harness — implements the methods VectorRebuilderMixin depends on
# ---------------------------------------------------------------------------


class FakeEmbeddingStorage:
    """Minimal fake embedding storage with a FAISS index."""

    def __init__(self, dimension: int = 128, path: str | None = None):
        self.dimension = dimension
        self.path = path
        self.index = faiss.IndexIDMap(faiss.IndexFlatL2(dimension))

    async def insert_batch(self, vectors: np.ndarray, ids: list[int]):
        self.index.add_with_ids(vectors, np.asarray(ids, dtype=np.int64))


class FakeProvider:
    """模拟 embedding provider — returns random vectors."""

    def __init__(self, dimension: int = 128):
        self.dimension = dimension

    async def text_embedding(self, text: str) -> list[float]:
        return list(np.random.randn(self.dimension).astype(np.float32))

    async def text_embedding_batch(self, texts: list[str]) -> list[list[float]]:
        return [list(np.random.randn(self.dimension).astype(np.float32)) for _ in texts]


class TestVectorRebuilder(VectorRebuilderMixin):
    """具体 test class with all required dependencies implemented."""

    __test__ = False

    def __init__(
        self,
        db_path: str,
        embedding_storage: FakeEmbeddingStorage,
        provider: FakeProvider,
    ):
        self.db_path = db_path
        self.faiss_db = MagicMock()
        self.faiss_db.embedding_storage = embedding_storage
        self.faiss_db.embedding_provider = provider

    # -- methods required by VectorRebuilderMixin --

    def _failure_ratio(self, errors: int, total: int) -> float:
        if total <= 0:
            return 0.0
        return errors / total

    async def _get_document_ids(self) -> set[int]:
        """返回 a fixed set of document IDs."""
        return {1, 2, 3}

    def _get_vector_ids(self) -> set[int] | None:
        """返回 vector IDs from the index id_map."""
        try:
            return {
                int(i)
                for i in faiss.vector_to_array(
                    self.faiss_db.embedding_storage.index.id_map
                )
            }
        except Exception:
            return None

    def _get_vector_count(self) -> int:
        return int(self.faiss_db.embedding_storage.index.ntotal)

    async def _iter_document_batches(
        self,
        batch_size: int,
        document_ids: set[int] | None = None,
    ):
        """Yield batches of (id, doc_id, text, metadata) tuples."""
        ids = sorted(int(d) for d in (document_ids or {1, 2, 3}))
        for start in range(0, len(ids), batch_size):
            chunk = ids[start : start + batch_size]
            batch_rows = [
                (i, f"doc_{i}", f"text for document {i}", "{}") for i in chunk
            ]
            yield batch_rows

    async def _embed_batch_with_retry(
        self, provider: Any, contents: list[str], options: dict[str, Any]
    ) -> list[list[float]]:
        """简单 batch embedding — returns random normalized vectors."""
        dim = self.faiss_db.embedding_storage.dimension
        return [list(np.random.randn(dim).astype(np.float32)) for _ in contents]


# ---------------------------------------------------------------------------
# Tests: _repair_missing_vectors
# ---------------------------------------------------------------------------


class TestRepairMissingVectors:
    """测试 _repair_missing_vectors."""

    def _make_harness(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        fes = FakeEmbeddingStorage(dimension=4)
        provider = FakeProvider(dimension=4)
        harness = TestVectorRebuilder(tmp.name, fes, provider)
        return harness, tmp.name

    @pytest.mark.asyncio
    async def test_repair_successful(self):
        """_repair_missing_vectors repairs missing vectors successfully."""
        harness, db_path = self._make_harness()
        try:
            memory_engine = MagicMock()
            memory_engine.faiss_db = harness.faiss_db
            options = {
                "batch_size": 50,
                "batch_delay": 0.0,
                "max_failure_ratio": 0.5,
            }
            progress_calls = []

            async def progress_cb(current, total, msg):
                progress_calls.append((current, total))

            result = await harness._repair_missing_vectors(
                memory_engine, {1, 2, 3}, options, progress_callback=progress_cb
            )
            assert result["mode"] == "repair"
            assert result["processed"] == 3
            assert result["errors"] == 0
            assert len(progress_calls) > 0
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_repair_embedding_mismatch_error(self):
        """_repair_missing_vectors handles embedding count mismatch (line 55)."""
        harness, db_path = self._make_harness()
        try:
            memory_engine = MagicMock()
            memory_engine.faiss_db = harness.faiss_db

            # Override _embed_batch_with_retry to return wrong count
            async def bad_embed(provider, contents, options):
                dim = harness.faiss_db.embedding_storage.dimension
                # Return 1 vector for 3 contents → mismatch
                return [list(np.random.randn(dim).astype(np.float32))]

            harness._embed_batch_with_retry = bad_embed

            options = {
                "batch_size": 2,
                "batch_delay": 0.0,
                "max_failure_ratio": 0.5,
            }
            result = await harness._repair_missing_vectors(
                memory_engine, {1, 2, 3}, options
            )
            assert result["mode"] == "repair"
            assert result["errors"] > 0
            assert result["partial"] is True
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_repair_exception_captured(self):
        """_repair_missing_vectors captures exception and adds to failed_ids (lines 60-62)."""
        harness, db_path = self._make_harness()
        try:
            memory_engine = MagicMock()
            memory_engine.faiss_db = harness.faiss_db

            # Override insert_batch to always throw
            harness.faiss_db.embedding_storage.insert_batch = AsyncMock(
                side_effect=RuntimeError("simulated failure")
            )

            options = {
                "batch_size": 1,
                "batch_delay": 0.0,
                "max_failure_ratio": 1.0,  # allow all failures
            }
            result = await harness._repair_missing_vectors(
                memory_engine, {1, 2, 3}, options
            )
            assert result["mode"] == "repair"
            assert result["errors"] == 3
            assert result["partial"] is True
            assert len(result["failed_ids"]) == 3
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_repair_failure_ratio_exceeded_breaks(self):
        """_repair_missing_vectors breaks when failure_ratio > max (line 78)."""
        harness, db_path = self._make_harness()
        try:
            memory_engine = MagicMock()
            memory_engine.faiss_db = harness.faiss_db

            harness.faiss_db.embedding_storage.insert_batch = AsyncMock(
                side_effect=RuntimeError("fail")
            )

            options = {
                "batch_size": 1,
                "batch_delay": 0.0,
                "max_failure_ratio": 0.1,  # very low threshold
            }
            result = await harness._repair_missing_vectors(
                memory_engine, {1, 2, 3}, options
            )
            # Should break after first batch since 1/3 > 0.1
            assert result["mode"] == "repair"
            assert result["errors"] >= 1
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_repair_with_batch_delay(self):
        """_repair_missing_vectors with batch_delay > 0 (line 80)."""
        harness, db_path = self._make_harness()
        try:
            memory_engine = MagicMock()
            memory_engine.faiss_db = harness.faiss_db

            options = {
                "batch_size": 1,
                "batch_delay": 0.001,  # non-zero delay
                "max_failure_ratio": 0.5,
            }
            result = await harness._repair_missing_vectors(
                memory_engine, {1, 2, 3}, options
            )
            assert result["processed"] == 3
            assert result["errors"] == 0
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_repair_missing_embedding_components(self):
        """_repair_missing_vectors raises RuntimeError when embedding is not initialized."""
        harness, db_path = self._make_harness()
        try:
            memory_engine = MagicMock()
            # No faiss_db attribute → embedding_storage will be None
            memory_engine.faiss_db = MagicMock()
            memory_engine.faiss_db.embedding_storage = None
            memory_engine.faiss_db.embedding_provider = None

            with pytest.raises(RuntimeError, match="Embedding 组件未初始化"):
                await harness._repair_missing_vectors(memory_engine, {1}, {})
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# Tests: _rebuild_vector_index_full
# ---------------------------------------------------------------------------


class TestRebuildVectorIndexFull:
    """测试 _rebuild_vector_index_full."""

    def _make_harness(self, dimension: int = 64):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        fes = FakeEmbeddingStorage(dimension=dimension)
        provider = FakeProvider(dimension=dimension)
        harness = TestVectorRebuilder(tmp.name, fes, provider)
        return harness, tmp.name

    @pytest.mark.asyncio
    async def test_rebuild_full_successful_with_file_write(self):
        """_rebuild_vector_index_full writes temp index and replaces (lines 138-139, 169-194)."""
        dim = 4
        harness, db_path = self._make_harness(dimension=dim)
        try:
            # Set a file path so the file-write code path is taken
            tmp_faiss = tempfile.NamedTemporaryFile(suffix=".index", delete=False)
            tmp_faiss.close()
            harness.faiss_db.embedding_storage.path = tmp_faiss.name

            memory_engine = MagicMock()
            memory_engine.faiss_db = harness.faiss_db

            options = {
                "batch_size": 50,
                "batch_delay": 0.0,
                "max_failure_ratio": 0.5,
            }
            progress_calls = []

            async def progress_cb(current, total, msg):
                progress_calls.append((current, total))

            result = await harness._rebuild_vector_index_full(
                memory_engine, 3, options, progress_callback=progress_cb
            )
            assert result["mode"] == "full"
            assert result["processed"] == 3
            assert result["switched"] is True
            # Verify the embedding_storage.index was replaced
            assert harness.faiss_db.embedding_storage.index.ntotal == 3
            assert len(progress_calls) > 0
        finally:
            os.unlink(db_path)
            if os.path.exists(tmp_faiss.name):
                os.unlink(tmp_faiss.name)

    @pytest.mark.asyncio
    async def test_rebuild_dimension_mismatch_error(self):
        """_rebuild_vector_index_full raises ValueError on dimension mismatch (line 131)."""
        dim = 4
        harness, db_path = self._make_harness(dimension=dim)
        try:
            memory_engine = MagicMock()
            memory_engine.faiss_db = harness.faiss_db

            # Override _embed_batch_with_retry to return wrong dimension
            async def wrong_dim_embed(provider, contents, options):
                return [list(np.random.randn(8).astype(np.float32)) for _ in contents]

            harness._embed_batch_with_retry = wrong_dim_embed

            options = {
                "batch_size": 1,
                "batch_delay": 0.0,
                "max_failure_ratio": 0.5,
            }
            result = await harness._rebuild_vector_index_full(memory_engine, 1, options)
            assert result["mode"] == "full"
            assert result["errors"] > 0
            assert result["switched"] is False
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_rebuild_missing_embedding_components(self):
        """_rebuild_vector_index_full raises RuntimeError when components missing."""
        harness, db_path = self._make_harness()
        try:
            memory_engine = MagicMock()
            memory_engine.faiss_db = MagicMock()
            memory_engine.faiss_db.embedding_storage = None
            memory_engine.faiss_db.embedding_provider = None

            with pytest.raises(RuntimeError, match="Embedding 组件未初始化"):
                await harness._rebuild_vector_index_full(memory_engine, 1, {})
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_rebuild_zero_dimension(self):
        """_rebuild_vector_index_full raises RuntimeError when dimension <= 0."""
        harness, db_path = self._make_harness(dimension=0)
        try:
            memory_engine = MagicMock()
            memory_engine.faiss_db = harness.faiss_db

            with pytest.raises(RuntimeError, match="索引维度无效"):
                await harness._rebuild_vector_index_full(memory_engine, 1, {})
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_rebuild_embedding_count_mismatch(self):
        """_rebuild_vector_index_full handles embedding count mismatch (line 131)."""
        harness, db_path = self._make_harness(dimension=128)
        try:
            memory_engine = MagicMock()
            memory_engine.faiss_db = harness.faiss_db

            async def bad_count_embed(provider, contents, options):
                # Return wrong number of vectors
                dim = harness.faiss_db.embedding_storage.dimension
                return [
                    list(np.random.randn(dim).astype(np.float32))
                    for _ in range(len(contents) + 1)
                ]

            harness._embed_batch_with_retry = bad_count_embed

            options = {
                "batch_size": 1,
                "batch_delay": 0.0,
                "max_failure_ratio": 0.5,
            }
            result = await harness._rebuild_vector_index_full(memory_engine, 1, options)
            assert result["errors"] > 0
            assert result["switched"] is False
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_rebuild_high_failure_ratio_returns_without_switch(self):
        """_rebuild_vector_index_full returns early when failure_ratio > max (lines 157-168)."""
        harness, db_path = self._make_harness(dimension=128)
        try:
            memory_engine = MagicMock()
            memory_engine.faiss_db = harness.faiss_db

            # Patch faiss.IndexIDMap.add_with_ids since _rebuild_vector_index_full
            # creates a temp_index = faiss.IndexIDMap(faiss.IndexFlatL2(dimension))
            # and calls temp_index.add_with_ids on it directly.
            from faiss import IndexIDMap

            with patch.object(
                IndexIDMap, "add_with_ids", side_effect=RuntimeError("inject fail")
            ):
                options = {
                    "batch_size": 1,
                    "batch_delay": 0.0,
                    "max_failure_ratio": 0.01,  # very low — first failure triggers
                }
                result = await harness._rebuild_vector_index_full(
                    memory_engine, 3, options
                )
                assert result["switched"] is False
                assert result["errors"] > 0
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# Tests: _rebuild_or_repair_vector_index
# ---------------------------------------------------------------------------


class TestRebuildOrRepairVectorIndex:
    """测试 _rebuild_or_repair_vector_index."""

    def _make_harness(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        fes = FakeEmbeddingStorage(dimension=4)
        provider = FakeProvider(dimension=4)
        harness = TestVectorRebuilder(tmp.name, fes, provider)
        return harness, tmp.name

    @pytest.mark.asyncio
    async def test_no_document_ids_returns_skip(self):
        """Returns skip mode when no documents exist."""
        harness, db_path = self._make_harness()
        try:
            # Override _get_document_ids to return empty
            async def no_docs():
                return set()

            harness._get_document_ids = no_docs

            memory_engine = MagicMock()
            result = await harness._rebuild_or_repair_vector_index(memory_engine, 0, {})
            assert result["mode"] == "skip"
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_vector_ids_match_documents_returns_skip(self):
        """Returns skip mode when all document IDs have vector IDs."""
        harness, db_path = self._make_harness()
        try:
            # Make _get_vector_ids return all doc ids (simulating complete index)
            async def get_ids():
                return {1, 2, 3}

            harness._get_document_ids = get_ids

            def get_vector_ids():
                return {1, 2, 3}

            harness._get_vector_ids = get_vector_ids

            memory_engine = MagicMock()
            result = await harness._rebuild_or_repair_vector_index(memory_engine, 3, {})
            assert result["mode"] == "skip"
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_partial_missing_vectors_triggers_repair(self):
        """缺失 vectors trigger repair (line 236)."""
        harness, db_path = self._make_harness()
        try:

            async def get_ids():
                return {1, 2, 3, 4}

            harness._get_document_ids = get_ids

            def get_vector_ids():
                return {1, 2}  # Missing 3, 4

            harness._get_vector_ids = get_vector_ids

            memory_engine = MagicMock()
            memory_engine.faiss_db = harness.faiss_db

            options = {
                "batch_size": 50,
                "batch_delay": 0.0,
                "max_failure_ratio": 0.5,
            }
            result = await harness._rebuild_or_repair_vector_index(
                memory_engine, 4, options
            )
            assert result["mode"] == "repair"
            assert result["processed"] > 0
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_vector_ids_none_count_exceeds_triggers_full_rebuild(self):
        """当 vector_ids is None and vector_count >= total, skip (line 240)."""
        harness, db_path = self._make_harness()
        try:

            async def get_ids():
                return {1, 2, 3}

            harness._get_document_ids = get_ids

            def get_vector_ids():
                return None  # Can't read IDs

            harness._get_vector_ids = get_vector_ids

            def get_vector_count():
                return 5  # Count >= total (3)

            harness._get_vector_count = get_vector_count

            memory_engine = MagicMock()
            memory_engine.faiss_db = harness.faiss_db

            result = await harness._rebuild_or_repair_vector_index(memory_engine, 3, {})
            assert result["mode"] == "skip"  # Skip because count >= total
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_vector_ids_none_count_low_triggers_full_rebuild(self):
        """当 vector_ids is None and vector_count < total, full rebuild (line 251)."""
        harness, db_path = self._make_harness()
        try:

            async def get_ids():
                return {1, 2, 3}

            harness._get_document_ids = get_ids

            def get_vector_ids():
                return None

            harness._get_vector_ids = get_vector_ids

            def get_vector_count():
                return 1  # Count < total

            harness._get_vector_count = get_vector_count

            memory_engine = MagicMock()
            memory_engine.faiss_db = harness.faiss_db

            options = {
                "batch_size": 50,
                "batch_delay": 0.0,
                "max_failure_ratio": 0.5,
            }
            result = await harness._rebuild_or_repair_vector_index(
                memory_engine, 3, options
            )
            assert result["mode"] == "full"
            assert result["processed"] == 3
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_empty_vector_ids_triggers_full_rebuild(self):
        """当 vector_ids is empty (all vectors missing), triggers full rebuild."""
        harness, db_path = self._make_harness()
        try:

            async def get_ids():
                return {1, 2, 3}

            harness._get_document_ids = get_ids

            def get_vector_ids():
                return set()  # No vector IDs

            harness._get_vector_ids = get_vector_ids

            memory_engine = MagicMock()
            memory_engine.faiss_db = harness.faiss_db

            options = {
                "batch_size": 50,
                "batch_delay": 0.0,
                "max_failure_ratio": 0.5,
            }
            result = await harness._rebuild_or_repair_vector_index(
                memory_engine, 3, options
            )
            assert result["mode"] == "full"
        finally:
            os.unlink(db_path)
