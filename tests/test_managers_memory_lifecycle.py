"""MemoryEngineLifecycleMixin 测试 — 初始化、关闭、追踪任务。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from core.managers.memory_engine import MemoryEngine


class TestMemoryEngineInitialize:
    """Tests for MemoryEngine.initialize()."""

    @pytest.mark.asyncio
    async def test_initialize_basic(self, tmp_db_path: str) -> None:
        """Test basic initialization — DB PRAGMAs, schema, minimal components."""
        mock_faiss = MagicMock()
        engine = MemoryEngine(
            db_path=tmp_db_path,
            faiss_db=mock_faiss,
            config={
                "graph_memory_enabled": False,
                "recall_engine.stopwords_path": "",
                "rrf_k": 60,
                "write_reliability.repair_enabled": False,
                # Disable optional subsystems
                "user_profile.enabled": False,
                "auto_learning.enabled": False,
                "knowledge_base.enabled": False,
                "notes.enabled": False,
                "reranker.enabled": False,
                "export.enabled": False,
            },
        )
        # Mock SchemaManager to avoid creating memory_write_ops table
        engine._schema.create_tables = AsyncMock()
        # Mock BM25Retriever initialize
        with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as mock_bm25_cls:
            mock_bm25 = mock_bm25_cls.return_value
            mock_bm25.initialize = AsyncMock()
            await engine.initialize()

        # Should have DB connection
        assert engine.db_connection is not None
        # Should have basic retrievers
        assert engine.text_processor is not None
        assert engine.bm25_retriever is not None
        assert engine.vector_retriever is not None
        assert engine.hybrid_retriever is not None
        assert engine.rrf_fusion is not None

        # Cleanup
        await engine.close()

    @pytest.mark.asyncio
    async def test_initialize_with_graph_disabled(self, tmp_db_path: str) -> None:
        mock_faiss = MagicMock()
        mock_graph_db = AsyncMock()
        mock_graph_db.close = AsyncMock()
        engine = MemoryEngine(
            db_path=tmp_db_path,
            faiss_db=mock_faiss,
            graph_vector_db=mock_graph_db,
            config={
                "graph_memory_enabled": False,  # disabled
                "recall_engine.stopwords_path": "",
                "rrf_k": 60,
                "write_reliability.repair_enabled": False,
                "user_profile.enabled": False,
                "auto_learning.enabled": False,
                "knowledge_base.enabled": False,
                "notes.enabled": False,
                "reranker.enabled": False,
                "export.enabled": False,
            },
        )
        engine._schema.create_tables = AsyncMock()
        with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as mock_bm25_cls:
            mock_bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()

        # Graph should NOT be initialized
        assert engine.graph_store is None
        assert engine.graph_memory_manager is None
        assert engine.atom_store is None
        assert engine.dual_route_retriever is None

        await engine.close()

    @pytest.mark.asyncio
    async def test_initialize_db_pragmas_set(self, tmp_db_path: str) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(
            db_path=tmp_db_path,
            faiss_db=mock_faiss,
            config={
                "graph_memory_enabled": False,
                "recall_engine.stopwords_path": "",
                "rrf_k": 60,
                "write_reliability.repair_enabled": False,
                "user_profile.enabled": False,
                "auto_learning.enabled": False,
                "knowledge_base.enabled": False,
                "notes.enabled": False,
                "reranker.enabled": False,
                "export.enabled": False,
            },
        )
        engine._schema.create_tables = AsyncMock()
        with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as mock_bm25_cls:
            mock_bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()

        # Verify WAL PRAGMA was set
        cursor = await engine.db_connection.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        assert row[0].upper() == "WAL"

        # Verify synchronous setting
        cursor = await engine.db_connection.execute("PRAGMA synchronous")
        row = await cursor.fetchone()
        assert row[0] == 1  # NORMAL

        await engine.close()

    @pytest.mark.asyncio
    async def test_initialize_with_user_profile(self, tmp_db_path: str) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(
            db_path=tmp_db_path,
            faiss_db=mock_faiss,
            config={
                "graph_memory_enabled": False,
                "recall_engine.stopwords_path": "",
                "rrf_k": 60,
                "write_reliability.repair_enabled": False,
                "user_profile.enabled": True,
                "user_profile.boost_strength": 0.2,
                "auto_learning.enabled": False,
                "knowledge_base.enabled": False,
                "notes.enabled": False,
                "reranker.enabled": False,
                "export.enabled": False,
            },
        )
        engine._schema.create_tables = AsyncMock()
        with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as mock_bm25_cls:
            mock_bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()

        assert engine.profile_store is not None
        assert engine.profile_manager is not None
        assert engine.personalized_ranker is not None

        await engine.close()

    @pytest.mark.asyncio
    async def test_initialize_with_knowledge_base(self, tmp_db_path: str) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(
            db_path=tmp_db_path,
            faiss_db=mock_faiss,
            config={
                "graph_memory_enabled": False,
                "recall_engine.stopwords_path": "",
                "rrf_k": 60,
                "write_reliability.repair_enabled": False,
                "user_profile.enabled": False,
                "auto_learning.enabled": False,
                "knowledge_base.enabled": True,
                "notes.enabled": False,
                "reranker.enabled": False,
                "export.enabled": False,
            },
        )
        engine._schema.create_tables = AsyncMock()
        with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as mock_bm25_cls:
            mock_bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()

        assert engine.knowledge_store is not None
        assert engine.knowledge_manager is not None
        assert engine.knowledge_retriever is not None

        await engine.close()

    @pytest.mark.asyncio
    async def test_initialize_with_notes(self, tmp_db_path: str) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(
            db_path=tmp_db_path,
            faiss_db=mock_faiss,
            config={
                "graph_memory_enabled": False,
                "recall_engine.stopwords_path": "",
                "rrf_k": 60,
                "write_reliability.repair_enabled": False,
                "user_profile.enabled": False,
                "auto_learning.enabled": False,
                "knowledge_base.enabled": False,
                "notes.enabled": True,
                "reranker.enabled": False,
                "export.enabled": False,
            },
        )
        engine._schema.create_tables = AsyncMock()
        with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as mock_bm25_cls:
            mock_bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()

        assert engine.note_store is not None
        assert engine.note_manager is not None

        await engine.close()

    @pytest.mark.asyncio
    async def test_initialize_with_auto_learning(self, tmp_db_path: str) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(
            db_path=tmp_db_path,
            faiss_db=mock_faiss,
            config={
                "graph_memory_enabled": False,
                "data_dir": tmp_db_path,
                "recall_engine.stopwords_path": "",
                "rrf_k": 60,
                "write_reliability.repair_enabled": False,
                "user_profile.enabled": False,
                "auto_learning.enabled": True,
                "auto_learning.learning_rate": 0.01,
                "knowledge_base.enabled": False,
                "notes.enabled": False,
                "reranker.enabled": False,
                "export.enabled": False,
            },
        )
        engine._schema.create_tables = AsyncMock()
        mock_al_instance = MagicMock()
        mock_al_instance.load_state = AsyncMock()
        with patch("core.managers.auto_learning.AutoLearningManager", return_value=mock_al_instance) as mock_al_cls:
            with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as mock_bm25_cls:
                mock_bm25_cls.return_value.initialize = AsyncMock()
                await engine.initialize()

            assert engine.auto_learning is not None
            mock_al_cls.assert_called_once_with(
                data_dir=tmp_db_path,
                learning_rate=0.01,
            )

        await engine.close()

    @pytest.mark.asyncio
    async def test_initialize_reranker_failure_does_not_break(self, tmp_db_path: str) -> None:
        """When reranker creation fails, initialize should still succeed."""
        mock_faiss = MagicMock()
        engine = MemoryEngine(
            db_path=tmp_db_path,
            faiss_db=mock_faiss,
            config={
                "graph_memory_enabled": False,
                "recall_engine.stopwords_path": "",
                "rrf_k": 60,
                "write_reliability.repair_enabled": False,
                "user_profile.enabled": False,
                "auto_learning.enabled": False,
                "knowledge_base.enabled": False,
                "notes.enabled": False,
                "reranker.enabled": True,
                "export.enabled": False,
            },
        )
        engine._schema.create_tables = AsyncMock()
        with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as mock_bm25_cls:
            mock_bm25_cls.return_value.initialize = AsyncMock()
            with patch("core.retrieval.reranker_factory.create_reranker",
                       side_effect=Exception("reranker failure")):
                await engine.initialize()

        # Should still have basic components
        assert engine.text_processor is not None
        assert engine.hybrid_retriever is not None

        await engine.close()

    @pytest.mark.asyncio
    async def test_initialize_with_trait_evolution(self, tmp_db_path: str) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(
            db_path=tmp_db_path,
            faiss_db=mock_faiss,
            config={
                "graph_memory_enabled": False,
                "data_dir": tmp_db_path,
                "recall_engine.stopwords_path": "",
                "rrf_k": 60,
                "write_reliability.repair_enabled": False,
                "user_profile.enabled": False,
                "auto_learning.enabled": False,
                "knowledge_base.enabled": False,
                "notes.enabled": False,
                "reranker.enabled": False,
                "export.enabled": False,
                "trait_evolution.enabled": True,
            },
        )
        engine._schema.create_tables = AsyncMock()
        mock_trait_instance = MagicMock()
        mock_trait_instance.load_state = AsyncMock()
        with patch(
            "core.managers.trait_evolution.TraitEvolutionTracker",
            return_value=mock_trait_instance,
        ) as mock_trait_cls:
            with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as mock_bm25_cls:
                mock_bm25_cls.return_value.initialize = AsyncMock()
                await engine.initialize()

            assert engine.trait_tracker is not None
            mock_trait_cls.assert_called_once_with(data_dir=tmp_db_path)

        await engine.close()

    @pytest.mark.asyncio
    async def test_initialize_optional_subsystems_disabled(self, tmp_db_path: str) -> None:
        """Optional subsystems should stay None when disabled."""
        mock_faiss = MagicMock()
        engine = MemoryEngine(
            db_path=tmp_db_path,
            faiss_db=mock_faiss,
            config={
                "graph_memory_enabled": False,
                "recall_engine.stopwords_path": "",
                "rrf_k": 60,
                "write_reliability.repair_enabled": False,
                "user_profile.enabled": False,
                "auto_learning.enabled": False,
                "knowledge_base.enabled": False,
                "notes.enabled": False,
                "reranker.enabled": False,
                "export.enabled": False,
                "continuity_tracking.enabled": False,
                "relationship_tracking.enabled": False,
                "reconsolidation.enabled": False,
                "anomaly_detection.enabled": False,
                "weight_learning.enabled": False,
                "trait_evolution.enabled": False,
            },
        )
        engine._schema.create_tables = AsyncMock()
        with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as mock_bm25_cls:
            mock_bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()

        # These attrs are initialized conditionally in initialize(), so they won't exist
        assert not hasattr(engine, "continuity_tracker") or engine.continuity_tracker is None
        assert not hasattr(engine, "relationship_tracker") or engine.relationship_tracker is None
        assert not hasattr(engine, "reconsolidation") or engine.reconsolidation is None
        assert not hasattr(engine, "weight_learner") or engine.weight_learner is None
        assert engine.trait_tracker is None  # always set in MainEngine.__init__

        await engine.close()


class TestMemoryEngineLifecycleClose:
    """Tests for close() with initialized components."""

    @pytest.mark.asyncio
    async def test_close_with_save_state(self, tmp_db_path: str) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(
            db_path=tmp_db_path,
            faiss_db=mock_faiss,
            config={
                "graph_memory_enabled": False,
                "recall_engine.stopwords_path": "",
                "rrf_k": 60,
                "write_reliability.repair_enabled": False,
                "user_profile.enabled": False,
                "auto_learning.enabled": False,
                "knowledge_base.enabled": False,
                "notes.enabled": False,
                "reranker.enabled": False,
                "export.enabled": False,
            },
        )
        engine._schema.create_tables = AsyncMock()
        with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as mock_bm25_cls:
            mock_bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()

        # Add mock components with save_state
        mock_trait = MagicMock()
        mock_trait.save_state = AsyncMock()
        engine.trait_tracker = mock_trait

        mock_al = MagicMock()
        mock_al.save_state = AsyncMock()
        engine.auto_learning = mock_al

        await engine.close()

        mock_trait.save_state.assert_called_once()
        mock_al.save_state.assert_called_once()
