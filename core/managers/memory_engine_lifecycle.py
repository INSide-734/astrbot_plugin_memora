"""
MemoryEngine 生命周期 Mixin
提供 initialize / close / _create_tracked_task
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite
from astrbot.api import logger

from ..features.memory.graph.infrastructure.graph_store import GraphStore
from ..features.memory.infrastructure.atom_store import AtomStore
from ..features.memory.infrastructure.base import apply_perf_pragmas
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
from .schema_migration import SchemaMigrationCoordinator
from .write_coordinator import ConnectionRegistry

if TYPE_CHECKING:
    from .anomaly_detector import AnomalyDetector
    from .continuity_tracker import ContinuityTracker


def _build_continuity_tracker(
    config: Mapping[str, Any],
    db_path: str,
) -> ContinuityTracker | None:
    """按运行时配置构造并同步恢复连续性 Tracker。

    Args:
        config: 已由工厂投影的引擎运行时配置。
        db_path: canonical SQLite 路径，用于缺省 data_dir 推导。

    Returns:
        功能关闭时返回 ``None``；否则返回已恢复状态的真实 Tracker。
    """

    if not bool(config.get("continuity_tracking.enabled", True)):
        return None
    from .continuity_tracker import ContinuityTracker

    data_dir = str(config.get("data_dir") or Path(db_path).parent)
    topic_ttl_days = float(config.get("continuity_tracking.topic_ttl_days", 7))
    tracker = ContinuityTracker(
        data_dir=data_dir,
        topic_ttl_sec=topic_ttl_days * 86400.0,
        max_topics=int(config.get("continuity_tracking.max_pending_topics", 10)),
    )
    tracker.load_state()
    return tracker


def _build_anomaly_detector(
    config: Mapping[str, Any],
    data_dir: str,
) -> AnomalyDetector | None:
    """按运行时配置构造并同步恢复异常检测器。

    Args:
        config: 已由工厂投影的引擎运行时配置。
        data_dir: 状态文件所在数据目录。

    Returns:
        功能关闭时返回 ``None``；否则返回已恢复状态的真实检测器。
    """

    if not bool(config.get("anomaly_detection.enabled", False)):
        return None
    from .anomaly_detector import AnomalyDetector

    detector = AnomalyDetector(
        data_dir=data_dir,
        window_days=int(config.get("anomaly_detection.window_days", 7)),
        sigma_threshold=float(config.get("anomaly_detection.sigma_threshold", 3.0)),
    )
    detector.load_state()
    return detector


class MemoryEngineLifecycleMixin:
    """MemoryEngine 生命周期方法（initialize / close / _create_tracked_task）"""

    # ==================== 生命周期 ====================

    def __init__(self):
        """初始化生命周期内由后续装配填充的组件引用。"""

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
        self.feedback_signal_manager = None
        self.knowledge_store = None
        self.knowledge_manager = None
        self.knowledge_retriever = None
        self.note_store = None
        self.note_manager = None
        self.continuity_tracker = None
        self.reconsolidation = None
        self.anomaly_detector = None
        self.memory_exporter = None
        self.dual_route_retriever = None
        self.sse = None

    async def initialize(self):
        """初始化 canonical 数据库、检索器及可重建 Atom/图派生组件。

        该方法会建立 SQLite 连接并把同一个请求级时钟与文本处理器装配到
        文档、图和 Atom 召回路径；任一必要初始化异常由调用方处理。
        """

        self._pending_tasks_lock = asyncio.Lock()
        self._pending_tasks_accepting = True
        self.db_connection = await aiosqlite.connect(self.db_path)
        self.db_connection.row_factory = aiosqlite.Row
        # ---- SQLite 写性能优化 PRAGMA ----
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
            from ..features.profiles.application import ProfileManager
            from ..features.profiles.infrastructure.profile_store import ProfileStore
            from ..retrieval.personalized_ranker import PersonalizedRanker

            self.profile_store = ProfileStore(self.db_path)
            await self.profile_store.init_table()
            self.profile_manager = ProfileManager(self.profile_store)
            boost_strength = float(self.config.get("user_profile.boost_strength", 0.15))
            self.personalized_ranker = PersonalizedRanker(boost_strength)

        # 自主学习关闭时仍恢复状态，确保已有发布或中断 intent 可显式回滚。
        from ..features.learning.domain.models import (
            FeedbackAdapterKind,
            FeedbackSignalPolicy,
        )
        from ..features.learning.infrastructure.feedback_signal_store import (
            FeedbackSignalStore,
        )
        from .auto_learning import AutoLearningManager
        from .auto_learning_state import AutoLearningStatePersistenceError
        from .feedback_signal_manager import FeedbackSignalManager

        auto_learning_enabled = bool(self.config.get("auto_learning.enabled", False))
        feedback_store = FeedbackSignalStore(
            configured_data_dir / "feedback_signals.db"
        )
        feedback_store.initialize()
        feedback_policy = FeedbackSignalPolicy(
            baseline_document_weight=float(
                self.config.get("document_route_weight", 0.65)
            ),
            baseline_graph_weight=float(self.config.get("graph_route_weight", 0.35)),
        )
        feedback_manager = FeedbackSignalManager(
            feedback_store,
            policy=feedback_policy,
        )
        feedback_manager.register_adapter(FeedbackAdapterKind.REVIEW_DECISION)
        self.feedback_signal_manager = feedback_manager
        auto_learning = AutoLearningManager(
            feedback_manager,
            data_dir=str(configured_data_dir),
            enabled=auto_learning_enabled,
            evidence_provider=self.config.get("auto_learning_evidence_provider"),
        )
        await auto_learning.load_state()
        try:
            await auto_learning.reconcile_reload_operation(
                effective_document_weight=float(
                    self.config.get("document_route_weight", 0.65)
                ),
                effective_graph_weight=float(
                    self.config.get("graph_route_weight", 0.35)
                ),
            )
        except AutoLearningStatePersistenceError:
            # 自主学习状态恢复失败时保持 fail-closed；不得阻断记忆主链启动。
            logger.warning("自主学习 reload 状态仍需恢复，继续启动记忆引擎")
        self.auto_learning = auto_learning

        # 知识库初始化
        if bool(self.config.get("knowledge_base.enabled", True)):
            from ..features.knowledge.application.knowledge_manager import (
                KnowledgeManager,
            )
            from ..features.knowledge.infrastructure.knowledge_store import (
                KnowledgeStore,
            )
            from ..retrieval.knowledge_retriever import KnowledgeRetriever

            self.knowledge_store = KnowledgeStore(self.db_path)
            await self.knowledge_store.init_table()
            self.knowledge_manager = KnowledgeManager(
                self.knowledge_store,
                dedup_threshold=float(
                    self.config.get("knowledge_base.dedup_threshold", 0.85)
                ),
                expire_days=int(self.config.get("knowledge_base.expire_days", 365)),
            )
            self.knowledge_retriever = KnowledgeRetriever(
                self.knowledge_store, self.config
            )

        # 笔记系统初始化
        if bool(self.config.get("notes.enabled", True)):
            from ..features.notes.application.note_manager import NoteManager
            from ..features.notes.infrastructure.note_store import NoteStore

            self.note_store = NoteStore(self.db_path)
            await self.note_store.init_table()
            self.note_manager = NoteManager(
                self.note_store,
                max_versions=int(self.config.get("notes.max_versions", 20)),
                auto_create_min_length=int(
                    self.config.get("notes.auto_create_min_length", 50)
                ),
                max_tags=int(self.config.get("notes.max_tags", 10)),
            )

        # v2.5 重排序器初始化（成本控制门）
        from ..shared.cost_control import CostControl

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
        self.continuity_tracker = _build_continuity_tracker(
            self.config,
            self.db_path,
        )

        # 记忆再巩固：默认关闭；启用时只生成候选，人工 CAS 应用与回滚
        self.reconsolidation = None
        self.reconsolidation_store = None
        if bool(self.config.get("reconsolidation.enabled", False)):
            from ..storage.reconsolidation_store import ReconsolidationStore
            from .reconsolidation import ReconsolidationManager

            store = ReconsolidationStore(
                configured_data_dir / "reconsolidation_candidates.db"
            )
            await store.initialize()
            self.reconsolidation_store = store
            self.reconsolidation = ReconsolidationManager(
                store,
                get_memory_cb=self.get_memory,
                update_memory_cb=self.update_memory,
                refresh_derived_cb=self._refresh_reconsolidation_derived,
                llm_caller=self._build_reconsolidation_llm_caller(),
                enabled=True,
                min_recall_count=int(
                    self.config.get("reconsolidation.min_recall_count", 5)
                ),
            )
            await self.reconsolidation.recover_incomplete_applies()
            await self.reconsolidation.recover_incomplete_rollbacks()

        # 异常检测：记忆创建速率滚动统计
        self.anomaly_detector = _build_anomaly_detector(
            self.config,
            str(configured_data_dir),
        )

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
        from ..platform.transport.realtime_hub import RealtimeHub

        realtime_hub = self.config.get("realtime_hub")
        if isinstance(realtime_hub, RealtimeHub):
            self.sse = RealtimeSSE(self, hub=realtime_hub)
        else:
            # 组合根未注入共享 Hub 时关闭 SSE，避免引擎私自创建第二个实例。
            self.sse = None
            logger.warning("实时事件 Hub 未由组合根注入，SSE 已禁用")

    async def close(self):
        """停止后台组件、保存状态并关闭数据库与向量资源。"""

        if self.atom_lifecycle_manager is not None:
            await self.atom_lifecycle_manager.stop()
        # 持久化子系统状态
        for attr_name in ("auto_learning",):
            component = getattr(self, attr_name, None)
            if component is not None and hasattr(component, "save_state"):
                with contextlib.suppress(Exception):
                    await component.save_state()
        stop_pending_tasks = getattr(self, "stop_pending_tasks", None)
        if callable(stop_pending_tasks):
            await stop_pending_tasks()
        else:
            # 兼容旧版轻量测试替身，真实引擎始终走带锁的收敛路径。
            pending_tasks = getattr(self, "_pending_tasks", set())
            for task in tuple(pending_tasks):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending_tasks, return_exceptions=True)
            pending_tasks.clear()
        continuity_tracker = getattr(self, "continuity_tracker", None)
        if continuity_tracker is not None:
            with contextlib.suppress(Exception):
                continuity_tracker.save_state()
        anomaly_detector = getattr(self, "anomaly_detector", None)
        if anomaly_detector is not None:
            with contextlib.suppress(Exception):
                anomaly_detector.save_state()
        feedback_signal_manager = getattr(self, "feedback_signal_manager", None)
        if feedback_signal_manager is not None:
            with contextlib.suppress(Exception):
                feedback_signal_manager.close()
        if self.db_connection:
            await self.db_connection.close()
        if self.graph_vector_db is not None:
            await self.graph_vector_db.close()

    async def stop_pending_tasks(self) -> None:
        """拒绝新工作并取消所有由引擎持有的后台任务。"""

        lock = getattr(self, "_pending_tasks_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._pending_tasks_lock = lock
        async with lock:
            self._pending_tasks_accepting = False
            pending_tasks = getattr(self, "_pending_tasks", None)
            if pending_tasks is None:
                pending_tasks = set()
                self._pending_tasks = pending_tasks
            current_task = asyncio.current_task()
            tasks = tuple(task for task in pending_tasks if task is not current_task)
        for task in tasks:
            if not task.done():
                task.cancel()
        try:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            async with lock:
                pending_tasks = getattr(self, "_pending_tasks", None)
                if pending_tasks is not None:
                    pending_tasks.difference_update(
                        task for task in tasks if task.done()
                    )

    def _create_tracked_task(self, coro):
        """关停尚未开始时创建由引擎持有的后台任务。"""

        if not getattr(self, "_pending_tasks_accepting", True):
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            return None
        pending_tasks = getattr(self, "_pending_tasks", None)
        if pending_tasks is None:
            pending_tasks = set()
            self._pending_tasks = pending_tasks
        task = asyncio.create_task(coro)
        pending_tasks.add(task)
        task.add_done_callback(self._on_pending_task_done)
        return task

    def _on_pending_task_done(self, task: asyncio.Task) -> None:
        """移除已完成任务，并收口普通后台异常。"""

        pending_tasks = getattr(self, "_pending_tasks", None)
        if pending_tasks is not None:
            pending_tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.warning(
                "MemoryEngine 后台任务失败 type=%s",
                type(error).__name__,
            )

    def _build_reconsolidation_llm_caller(self) -> Any | None:
        """构造带形状校验的再巩固 LLM 调用器，Provider 缺失时返回 None。"""

        from ..provider_adapters import LLMProviderAdapter

        provider = getattr(self, "llm_provider", None)
        if provider is None:
            return None
        try:
            adapter = LLMProviderAdapter.from_provider(provider)
        except Exception:
            return None

        async def _call(prompt: str) -> str | None:
            try:
                return await adapter.generate(prompt, system_prompt="")
            except asyncio.CancelledError:
                raise
            except Exception:
                return None

        return _call

    async def _refresh_reconsolidation_derived(self, memory_id: int) -> bool:
        """按当前 canonical 快照重刷单条再巩固 graph 派生数据。

        Args:
            memory_id: 已由正常更新入口恢复的 canonical 记忆 ID。

        Returns:
            source 可读取且 graph 已刷新时返回 True；graph 未启用时也返回 True。
        """

        memory = await self.get_memory(memory_id)
        if not memory:
            return False
        graph_manager = getattr(self, "graph_memory_manager", None)
        if graph_manager is None:
            return True
        metadata = memory.get("metadata", {}) or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        elif not isinstance(metadata, dict):
            metadata = {}
        content = str(memory.get("text") or memory.get("content") or "")
        if not content.strip():
            return False
        await graph_manager.index_memory(memory_id, content, metadata)
        return True
