"""MemoryEngineLifecycleMixin 测试 — 初始化、关闭、追踪任务。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api.learning_config_adapter import (
    LearningConfigApplyResult,
    LearningConfigSnapshot,
)
from core.evaluation.feedback_learning_evidence import (
    LatencyEvidence,
    QualityMetricEvidence,
    build_learning_evidence,
)
from core.evaluation.feedback_learning_evidence_contract import (
    REQUIRED_EVIDENCE_REGRESSION_CHECKS,
)
from core.managers.auto_learning_actions import (
    aggregation_revision_for,
    stable_revision,
    weight_snapshot_hash,
)
from core.managers.feedback_signal_manager import FeedbackSignalManager
from core.managers.memory_engine import MemoryEngine
from core.models.feedback_signal import FeedbackSignalAggregate


class TestMemoryEngineInitialize:
    """验证 MemoryEngine.initialize() 的组件装配。"""

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
        with patch(
            "core.managers.memory_engine_lifecycle.BM25Retriever"
        ) as mock_bm25_cls:
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
        with patch(
            "core.managers.memory_engine_lifecycle.BM25Retriever"
        ) as mock_bm25_cls:
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
        with patch(
            "core.managers.memory_engine_lifecycle.BM25Retriever"
        ) as mock_bm25_cls:
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
        with patch(
            "core.managers.memory_engine_lifecycle.BM25Retriever"
        ) as mock_bm25_cls:
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
        with patch(
            "core.managers.memory_engine_lifecycle.BM25Retriever"
        ) as mock_bm25_cls:
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
                "notes.auto_create_min_length": 73,
                "notes.max_tags": 3,
                "notes.max_versions": 7,
                "reranker.enabled": False,
                "export.enabled": False,
            },
        )
        engine._schema.create_tables = AsyncMock()
        with patch(
            "core.managers.memory_engine_lifecycle.BM25Retriever"
        ) as mock_bm25_cls:
            mock_bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()

        assert engine.note_store is not None
        assert engine.note_manager is not None
        assert engine.note_manager._auto_create_min_length == 73
        assert engine.note_manager._max_tags == 3
        assert engine.note_manager._max_versions == 7

        await engine.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("enabled", [False, True])
    async def test_initialize_auto_learning_in_both_modes(
        self,
        tmp_db_path: str,
        enabled: bool,
    ) -> None:
        """启用与禁用模式都装配可恢复的自主学习组件。"""

        mock_faiss = MagicMock()
        evidence_provider = AsyncMock()
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
                "auto_learning.enabled": enabled,
                "auto_learning_evidence_provider": evidence_provider,
                "document_route_weight": 0.61,
                "graph_route_weight": 0.39,
                "knowledge_base.enabled": False,
                "notes.enabled": False,
                "reranker.enabled": False,
                "export.enabled": False,
            },
        )
        engine._schema.create_tables = AsyncMock()
        mock_al_instance = MagicMock()
        mock_al_instance.load_state = AsyncMock()
        mock_al_instance.reconcile_reload_operation = AsyncMock()
        mock_al_instance.save_state = AsyncMock()
        with patch(
            "core.managers.auto_learning.AutoLearningManager",
            return_value=mock_al_instance,
        ) as mock_al_cls:
            with patch(
                "core.managers.memory_engine_lifecycle.BM25Retriever"
            ) as mock_bm25_cls:
                mock_bm25_cls.return_value.initialize = AsyncMock()
                await engine.initialize()

            assert engine.auto_learning is mock_al_instance
        mock_al_cls.assert_called_once()
        call_args = mock_al_cls.call_args
        assert isinstance(call_args.args[0], FeedbackSignalManager)
        assert call_args.kwargs["enabled"] is enabled
        assert call_args.kwargs["data_dir"] == str(Path(tmp_db_path).parent)
        assert call_args.kwargs["evidence_provider"] is evidence_provider
        mock_al_instance.load_state.assert_awaited_once()
        mock_al_instance.reconcile_reload_operation.assert_awaited_once_with(
            effective_document_weight=0.61,
            effective_graph_weight=0.39,
        )
        assert engine.feedback_signal_manager is not None
        assert engine.feedback_signal_manager.policy.baseline_document_weight == 0.61
        assert engine.feedback_signal_manager.policy.baseline_graph_weight == 0.39
        feedback_manager = engine.feedback_signal_manager

        await engine.close()

        mock_al_instance.save_state.assert_awaited_once()
        with pytest.raises(RuntimeError, match="feedback_store_not_initialized"):
            feedback_manager.store.safe_summary()

    @pytest.mark.asyncio
    async def test_disabled_restart_preserves_explicit_rollback(
        self,
        tmp_path: Path,
    ) -> None:
        """禁用重启阻止新学习动作，但保留已发布配置的显式回滚。"""

        config_revision = "b" * 64
        window_start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        aggregates = [
            FeedbackSignalAggregate(
                scope_domain=f"scope-{index}",
                persona_domain=None,
                window_start=window_start + timedelta(hours=index),
                window_end=window_start + timedelta(hours=index + 1),
                accepted_count=4,
                independent_window_count=2,
                decayed_support=0.8,
                proposed_document_weight=0.76,
                proposed_graph_weight=0.24,
                delta_from_baseline=0.06,
                status="candidate",
                policy_version=3,
            )
            for index in range(2)
        ]
        artifact = build_learning_evidence(
            aggregation_revision=aggregation_revision_for(aggregates),
            source_config_revision=config_revision,
            quality_gate_version="quality-gate-v1",
            dataset_hash="dataset-hash",
            replay_window_hash="replay-window-hash",
            evaluator_version="feedback-ranking-v2",
            sample_count=8,
            independent_window_count=2,
            quality_metrics=(
                QualityMetricEvidence("Recall@K", 0.5, 0.56, 0.01, 0.11),
                QualityMetricEvidence("MRR", 0.5, 0.51, 0.0, 0.02),
                QualityMetricEvidence("nDCG", 0.5, 0.51, 0.0, 0.02),
            ),
            latency_metrics=(
                LatencyEvidence("retrieval_stage", 50.0, 100.0, 45.0, 95.0),
                LatencyEvidence("ttft", 100.0, 200.0, 95.0, 190.0),
            ),
            baseline_token_cost=100.0,
            candidate_token_cost=100.0,
            regression_checks=tuple(sorted(REQUIRED_EVIDENCE_REGRESSION_CHECKS)),
            regression_failures=(),
        )
        snapshot_holder = {
            "value": LearningConfigSnapshot(
                revision=config_revision,
                document_route_weight=0.7,
                graph_route_weight=0.3,
                config_hash=stable_revision(
                    "lifecycle-config",
                    {"revision": config_revision, "weights": [0.7, 0.3]},
                ),
                weight_hash=weight_snapshot_hash(
                    {
                        "document_route_weight": 0.7,
                        "graph_route_weight": 0.3,
                    }
                ),
            )
        }
        adapter = MagicMock()
        adapter.get_weight_snapshot = AsyncMock(
            side_effect=lambda: snapshot_holder["value"]
        )

        async def apply_weights(
            target_weights: dict[str, float],
            *,
            expected_revision: str,
        ) -> LearningConfigApplyResult:
            """模拟会推进 revision 的 ConfigManager CAS 写入。"""

            before = snapshot_holder["value"]
            assert before.revision == expected_revision
            applied_revision = f"{expected_revision}:next"
            after = LearningConfigSnapshot(
                revision=applied_revision,
                document_route_weight=target_weights["document_route_weight"],
                graph_route_weight=target_weights["graph_route_weight"],
                config_hash=stable_revision(
                    "lifecycle-config",
                    {"revision": applied_revision, "weights": target_weights},
                ),
                weight_hash=weight_snapshot_hash(target_weights),
            )
            snapshot_holder["value"] = after
            return LearningConfigApplyResult(
                requested_revision=expected_revision,
                applied_revision=applied_revision,
                changed_paths=(
                    "graph_memory.document_route_weight",
                    "graph_memory.graph_route_weight",
                ),
                before_hash=before.config_hash,
                after_hash=after.config_hash,
                applied=True,
                no_op=False,
                reason_code="config_applied",
            )

        adapter.apply_weights = AsyncMock(side_effect=apply_weights)
        db_path = str(tmp_path / "memory.db")

        def build_engine(enabled: bool) -> MemoryEngine:
            """构造共享状态目录但开关不同的测试引擎。"""

            engine = MemoryEngine(
                db_path=db_path,
                faiss_db=MagicMock(),
                config={
                    "graph_memory_enabled": False,
                    "data_dir": str(tmp_path),
                    "recall_engine.stopwords_path": "",
                    "rrf_k": 60,
                    "write_reliability.repair_enabled": False,
                    "user_profile.enabled": False,
                    "auto_learning.enabled": enabled,
                    "document_route_weight": 0.7,
                    "graph_route_weight": 0.3,
                    "knowledge_base.enabled": False,
                    "notes.enabled": False,
                    "reranker.enabled": False,
                    "export.enabled": False,
                },
            )
            engine._schema.create_tables = AsyncMock()
            return engine

        with patch(
            "core.managers.memory_engine_lifecycle.BM25Retriever"
        ) as mock_bm25_cls:
            mock_bm25_cls.return_value.initialize = AsyncMock()
            enabled_engine = build_engine(True)
            await enabled_engine.initialize()

        enabled_engine.feedback_signal_manager.rebuild = MagicMock(
            return_value=aggregates
        )
        candidates = await enabled_engine.auto_learning.rebuild_candidates(
            reference_time=datetime(2026, 8, 3, tzinfo=timezone.utc),
            evidence_artifact=artifact,
        )
        ready = next(
            item for item in candidates if item["status"] == "ready_for_review"
        )
        published = await enabled_engine.auto_learning.publish_candidate(
            ready["candidate_id"],
            config_adapter=adapter,
            expected_revision=config_revision,
        )
        assert published["published"] is True
        await enabled_engine.close()

        with patch(
            "core.managers.memory_engine_lifecycle.BM25Retriever"
        ) as mock_bm25_cls:
            mock_bm25_cls.return_value.initialize = AsyncMock()
            disabled_engine = build_engine(False)
            await disabled_engine.initialize()

        assert disabled_engine.auto_learning.enabled is False
        assert await disabled_engine.auto_learning.rebuild_candidates() == []
        blocked = await disabled_engine.auto_learning.publish_candidate(
            ready["candidate_id"],
            config_adapter=adapter,
            expected_revision=snapshot_holder["value"].revision,
        )
        assert blocked["published"] is False
        assert blocked["reason_code"] == "disabled"
        assert adapter.apply_weights.await_count == 1

        rollback = await disabled_engine.auto_learning.rollback_last_publish(
            ready["candidate_id"],
            config_adapter=adapter,
            expected_revision=snapshot_holder["value"].revision,
        )
        assert rollback["restored"] is True
        assert rollback["reason_code"] == "restored"
        assert adapter.apply_weights.await_count == 2
        await disabled_engine.close()

    @pytest.mark.asyncio
    async def test_initialize_reranker_failure_does_not_break(
        self, tmp_db_path: str
    ) -> None:
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
        with patch(
            "core.managers.memory_engine_lifecycle.BM25Retriever"
        ) as mock_bm25_cls:
            mock_bm25_cls.return_value.initialize = AsyncMock()
            with patch(
                "core.retrieval.reranker_factory.create_reranker",
                side_effect=Exception("reranker failure"),
            ):
                await engine.initialize()

        # Should still have basic components
        assert engine.text_processor is not None
        assert engine.hybrid_retriever is not None

        await engine.close()

    @pytest.mark.asyncio
    async def test_initialize_optional_subsystems_disabled(
        self, tmp_db_path: str
    ) -> None:
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
                "reconsolidation.enabled": False,
                "anomaly_detection.enabled": False,
            },
        )
        engine._schema.create_tables = AsyncMock()
        with patch(
            "core.managers.memory_engine_lifecycle.BM25Retriever"
        ) as mock_bm25_cls:
            mock_bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()

        # These attrs are initialized conditionally in initialize(), so they won't exist
        assert (
            not hasattr(engine, "continuity_tracker")
            or engine.continuity_tracker is None
        )
        assert not hasattr(engine, "reconsolidation") or engine.reconsolidation is None

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
        with patch(
            "core.managers.memory_engine_lifecycle.BM25Retriever"
        ) as mock_bm25_cls:
            mock_bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()

        mock_al = MagicMock()
        mock_al.save_state = AsyncMock()
        engine.auto_learning = mock_al

        await engine.close()

        mock_al.save_state.assert_called_once()
