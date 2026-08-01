"""
MemoryEngine 生命周期 Mixin
提供 initialize / close / _create_tracked_task
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import aiosqlite
from astrbot.api import logger

from ..managers.atom_lifecycle_manager import AtomLifecycleManager
from ..managers.graph_memory_manager import GraphMemoryManager
from ..processors.graph_extractor import GraphExtractor
from ..processors.text_processor import TextProcessor
from ..retrieval.atom_retriever import AtomRetriever
from ..retrieval.bm25_retriever import BM25Retriever
from ..retrieval.dual_route_retriever import DualRouteRetriever
from ..retrieval.graph_keyword_retriever import GraphKeywordRetriever
from ..retrieval.graph_retriever import GraphRetriever
from ..retrieval.graph_vector_retriever import GraphVectorRetriever
from ..retrieval.hybrid_retriever import HybridRetriever
from ..retrieval.rrf_fusion import RRFFusion
from ..retrieval.vector_retriever import VectorRetriever
from ..storage.atom_store import AtomStore
from ..storage.graph_store import GraphStore
from .schema_migration import SchemaMigrationCoordinator
from .write_coordinator import ConnectionRegistry


class MemoryEngineLifecycleMixin:
    """MemoryEngine 生命周期方法（initialize / close / _create_tracked_task）"""

    # ==================== 生命周期 ====================

    def __init__(self):
        self.db_connection = None
        self.text_processor = None
        self.rrf_fusion = None
        self.bm25_retriever = None
        self.vector_retriever = None
        self.hybrid_retriever = None
        self.graph_store = None
        self.atom_store = None
        self.atom_lifecycle_manager = None
        self.atom_retriever = None
        self.graph_extractor = None
        self.hierarchy_store = None
        self.graph_keyword_retriever = None
        self.graph_vector_retriever = None
        self.graph_retriever = None
        self.graph_memory_manager = None
        self.profile_store = None
        self.profile_manager = None
        self.personalized_ranker = None
        self.auto_learning = None
        self.knowledge_store = None
        self.knowledge_manager = None
        self.knowledge_retriever = None
        self.note_store = None
        self.note_manager = None
        self.trait_tracker = None
        self.continuity_tracker = None
        self.relationship_tracker = None
        self.reconsolidation = None
        self.anomaly_detector = None
        self.weight_learner = None
        self.memory_exporter = None
        self.dual_route_retriever = None
        self.sse = None

    async def initialize(self):
        """初始化 canonical 数据库、检索器及可重建 Atom/图派生组件。

        该方法会建立 SQLite 连接并把同一个请求级时钟与文本处理器装配到
        文档、图和 Atom 召回路径；任一必要初始化异常由调用方处理。
        """

        self.db_connection = await aiosqlite.connect(self.db_path)
        self.db_connection.row_factory = aiosqlite.Row
        # ---- SQLite 写性能优化 PRAGMA ----
        from ..storage.base import apply_perf_pragmas

        await apply_perf_pragmas(self.db_connection)
        # 降低 WAL checkpoint 频率，减少生命周期初始化阶段的写阻塞。
        await self.db_connection.execute("PRAGMA wal_autocheckpoint = 1000")
        for mod in (
            self._write_journal,
            self._retrieval,
            self._maintenance,
            self._schema,
        ):
            mod._db = self.db_connection
        configured_data_dir = Path(
            self.config.get("data_dir") or Path(self.db_path).parent
        )
        if configured_data_dir == Path(self.db_path):
            configured_data_dir = Path(self.db_path).parent
        migration_coordinator = SchemaMigrationCoordinator(
            self._schema,
            db_path=self.db_path,
            data_dir=configured_data_dir,
            auto_migrate=bool(self.config.get("migration_settings.auto_migrate", True)),
            create_backup=bool(
                self.config.get("migration_settings.create_backup", True)
            ),
            backup_manager=self.config.get("backup_manager"),
        )
        self.schema_migration_status = await migration_coordinator.run(
            self._write_journal.create_table
        )
        # Schema 完成后再注册连接，避免恢复流程留下已关闭连接。
        ConnectionRegistry.register(
            self.db_path,
            self.db_connection,
            [self._write_journal, self._retrieval, self._maintenance, self._schema],
        )
        stopwords_path = self.config.get("recall_engine.stopwords_path")
        self.text_processor = TextProcessor(stopwords_path)
        rrf_k = self.config.get("rrf_k", 60)
        self.rrf_fusion = RRFFusion(k=rrf_k)
        self.bm25_retriever = BM25Retriever(
            self.db_path, self.text_processor, self.config
        )
        await self.bm25_retriever.initialize()
        self.vector_retriever = VectorRetriever(self.faiss_db, self.config)
        self.hybrid_retriever = HybridRetriever(
            self.bm25_retriever, self.vector_retriever, self.rrf_fusion, self.config
        )
        if self.graph_enabled and self.graph_vector_db is not None:
            self.graph_store = GraphStore(self.db_path)
            await self.graph_store.initialize()
            self.atom_store = AtomStore(self.db_path, self.config)
            await self.atom_store.initialize()
            if self.atom_enabled:
                self.atom_lifecycle_manager = AtomLifecycleManager(
                    self.atom_store, self.config
                )
                self.atom_retriever = AtomRetriever(
                    self.atom_store,
                    self.config,
                    text_processor=self.text_processor,
                )
                await self.atom_lifecycle_manager.start()
            self.graph_extractor = GraphExtractor(self.config)
            from ..storage.hierarchy_store import EntityHierarchyStore

            self.hierarchy_store = EntityHierarchyStore(self.db_connection)
            await self.hierarchy_store.init_table()
            self.graph_keyword_retriever = GraphKeywordRetriever(
                self.graph_store,
                self.text_processor,
                hierarchy_store=self.hierarchy_store,
                config=self.config,
            )
            self.graph_vector_retriever = GraphVectorRetriever(
                self.graph_vector_db, self.config
            )
            self.graph_retriever = GraphRetriever(
                self.graph_keyword_retriever,
                self.graph_vector_retriever,
                self.rrf_fusion,
                self.config,
            )
            self.graph_memory_manager = GraphMemoryManager(
                self.graph_store, self.graph_vector_retriever, self.graph_extractor
            )
            self._write_journal._graph_memory_manager = self.graph_memory_manager
            self._write_journal._atom_store = self.atom_store
            self._maintenance._graph_memory_manager = self.graph_memory_manager
            self._maintenance._graph_store = self.graph_store
        if self._write_op_repair_enabled:
            await self._write_journal.repair_incomplete()

        # ===== v2.5 子系统初始化（必须在 DualRouteRetriever 之前）=====

        # 用户画像初始化
        if bool(self.config.get("user_profile.enabled", True)):
            from ..retrieval.personalized_ranker import PersonalizedRanker
            from ..storage.profile_store import ProfileStore
            from .profile_manager import ProfileManager

            self.profile_store = ProfileStore(self.db_path)
            await self.profile_store.init_table()
            self.profile_manager = ProfileManager(self.profile_store)
            boost_strength = float(self.config.get("user_profile.boost_strength", 0.15))
            self.personalized_ranker = PersonalizedRanker(boost_strength)

        # 自主学习初始化
        if bool(self.config.get("auto_learning.enabled", True)):
            from .auto_learning import AutoLearningManager

            data_dir = str(self.config.get("data_dir", ""))
            lr = float(self.config.get("auto_learning.learning_rate", 0.01))
            self.auto_learning = AutoLearningManager(
                data_dir=data_dir, learning_rate=lr
            )
            await self.auto_learning.load_state()

        # 知识库初始化
        if bool(self.config.get("knowledge_base.enabled", True)):
            from ..retrieval.knowledge_retriever import KnowledgeRetriever
            from ..storage.knowledge_store import KnowledgeStore
            from .knowledge_manager import KnowledgeManager

            self.knowledge_store = KnowledgeStore(self.db_path)
            await self.knowledge_store.init_table()
            self.knowledge_manager = KnowledgeManager(self.knowledge_store)
            self.knowledge_retriever = KnowledgeRetriever(
                self.knowledge_store, self.config
            )

        # 笔记系统初始化
        if bool(self.config.get("notes.enabled", True)):
            from ..storage.note_store import NoteStore
            from .note_manager import NoteManager

            self.note_store = NoteStore(self.db_path)
            await self.note_store.init_table()
            self.note_manager = NoteManager(self.note_store)

        # 性格演化追踪器（可选 — 与自主学习联动）
        if bool(self.config.get("trait_evolution.enabled", False)):
            from .trait_evolution import TraitEvolutionTracker

            data_dir = str(self.config.get("data_dir", ""))
            self.trait_tracker = TraitEvolutionTracker(data_dir=data_dir)
            await self.trait_tracker.load_state()

        # v2.5 重排序器初始化（成本控制门）
        from ..base.cost_control import CostControl

        configured_cost_control = self.config.get("cost_control_runtime")
        self.cost_control = (
            configured_cost_control
            if isinstance(configured_cost_control, CostControl)
            else CostControl()
        )
        reranker = None
        if bool(self.config.get("reranker.enabled", True)):
            try:
                from ..retrieval.reranker_factory import create_reranker

                strategy = self.config.get("reranker.strategy", "mmr")

                # 高成本策略（llm/hybrid）在 balanced/low_cost 下自动降级为 MMR
                if strategy in ("llm", "hybrid") and not self.cost_control.allow(
                    "llm_reranker"
                ):
                    logger.info(
                        f"[CostControl] reranker strategy={strategy} 降级为 mmr: "
                        f"{self.cost_control.deny_reason('llm_reranker')}"
                    )
                    strategy = "mmr"

                reranker = await create_reranker(
                    strategy,
                    self.config,
                    faiss_db=self.faiss_db,
                    llm_client=self.llm_provider,
                    cost_control=self.cost_control,
                )
            except Exception:
                pass  # 重排序器创建失败不影响启动
        self.reranker = reranker

        # ===== 可选子系统初始化 =====

        # 对话连续性追踪
        if bool(self.config.get("continuity_tracking.enabled", False)):
            from .continuity_tracker import ContinuityTracker

            self.continuity_tracker = ContinuityTracker(
                self.db_path, self.db_connection
            )

        # 关系阶段追踪
        if bool(self.config.get("relationship_tracking.enabled", False)):
            from .relationship_tracker import RelationshipTracker

            self.relationship_tracker = RelationshipTracker(
                self.db_path, self.db_connection
            )

        # 记忆再巩固
        if bool(self.config.get("reconsolidation.enabled", False)):
            from .reconsolidation import ReconsolidationManager

            self.reconsolidation = ReconsolidationManager(
                self.db_connection, self.llm_provider
            )

        # 异常检测
        if bool(self.config.get("anomaly_detection.enabled", False)):
            from .anomaly_detector import AnomalyDetector

            self.anomaly_detector = AnomalyDetector(self.db_path, self.config)

        # MAB 权重学习
        if bool(self.config.get("weight_learning.enabled", False)):
            from .weight_learner import MABWeightLearner

            self.weight_learner = MABWeightLearner(self.db_connection, self.config)

        # 记忆导入导出
        if bool(self.config.get("export.enabled", True)):
            import json as _json

            from .memory_exporter import MemoryExporter

            async def _get_all_memories(session_id: str | None = None):
                if session_id:
                    cursor = await self.db_connection.execute(
                        "SELECT id, doc_id, text, metadata FROM documents "
                        "WHERE json_extract(metadata, '$.session_id') = ?",
                        (session_id,),
                    )
                else:
                    cursor = await self.db_connection.execute(
                        "SELECT id, doc_id, text, metadata FROM documents"
                    )
                rows = await cursor.fetchall()
                return [
                    {
                        "id": row[0],
                        "doc_id": row[1],
                        "text": row[2],
                        "content": row[2],
                        "metadata": _json.loads(row[3]) if row[3] else {},
                    }
                    for row in rows
                ]

            self.memory_exporter = MemoryExporter(get_all_memories_cb=_get_all_memories)

        # DualRouteRetriever（需在 v2.5 组件之后创建，以传入依赖）
        if self.graph_enabled and self.graph_vector_db is not None:
            from ..retrieval.evidence_scorer import RetrievalEvidenceScorer

            self.dual_route_retriever = DualRouteRetriever(
                self.hybrid_retriever,
                self.graph_retriever,
                self.get_memory,
                self.config,
                personalized_ranker=self.personalized_ranker,
                profile_manager=self.profile_manager,
                reranker=reranker,
                derived_expander=self.config.get("derived_expander"),
                projection_reader=self.config.get("projection_reader"),
                atom_retriever=self.atom_retriever,
                evidence_scorer=RetrievalEvidenceScorer(),
                create_tracked_task_cb=self._create_tracked_task,
            )

        # D4：实时 SSE 事件流。
        from ..api.realtime_api import RealtimeSSE

        self.sse = RealtimeSSE(self)

    async def close(self):
        if self.atom_lifecycle_manager is not None:
            await self.atom_lifecycle_manager.stop()
        # 持久化子系统状态
        for attr_name in ("trait_tracker", "auto_learning", "anomaly_detector"):
            component = getattr(self, attr_name, None)
            if component is not None and hasattr(component, "save_state"):
                with contextlib.suppress(Exception):
                    await component.save_state()
        if self._pending_tasks:
            for task in self._pending_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
            self._pending_tasks.clear()
        if self.db_connection:
            await self.db_connection.close()
        if self.graph_vector_db is not None:
            await self.graph_vector_db.close()

    def _create_tracked_task(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
