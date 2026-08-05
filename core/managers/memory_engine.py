"""
统一记忆引擎 - MemoryEngine
提供统一的记忆管理接口,整合所有底层组件

委托: WriteOpJournal(写日志+修复) / RetrievalOptimizer(缓存+增强+衰减+整合+触发词)
      MaintenanceOperations(衰减+清理+统计+迁移+维护) / SchemaManager(建表+迁移)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .maintenance_operations import MaintenanceOperations
from .memory_engine_batch import MemoryEngineBatchMixin
from .memory_engine_crud import MemoryEngineCRUDMixin
from .memory_engine_domain_hooks import MemoryEngineDomainHooksMixin
from .memory_engine_evolution_hooks import MemoryEngineEvolutionHooksMixin
from .memory_engine_lifecycle import MemoryEngineLifecycleMixin
from .retrieval_optimizer import RetrievalOptimizer
from .schema_manager import SchemaManager
from .write_op_journal import WriteOpJournal


class MemoryEngine(
    MemoryEngineLifecycleMixin,
    MemoryEngineEvolutionHooksMixin,
    MemoryEngineDomainHooksMixin,
    MemoryEngineCRUDMixin,
    MemoryEngineBatchMixin,
):
    """统一记忆引擎 — 整合多存储后端，提供完整的记忆生命周期管理"""

    def __init__(
        self,
        db_path: str,
        faiss_db,
        graph_vector_db=None,
        llm_provider=None,
        config: dict[str, Any] | None = None,
    ):
        self.db_path = db_path
        self.faiss_db = faiss_db
        self.graph_vector_db = graph_vector_db
        self.llm_provider = llm_provider
        self.config = config or {}
        self.graph_enabled = bool(self.config.get("graph_memory_enabled", False))
        self.atom_enabled = bool(
            self.config.get(
                "atom_enabled", self.config.get("graph_memory_atom_enabled", True)
            )
        )
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._pending_tasks: set[asyncio.Task] = set()
        self._write_op_repair_enabled = bool(
            self.config.get("write_reliability.repair_enabled", True)
        )
        # 组件占位（initialize 中赋值）
        self.text_processor = None
        self.bm25_retriever = None
        self.vector_retriever = None
        self.rrf_fusion = None
        self.hybrid_retriever = None
        self.graph_store = None
        self.graph_extractor = None
        self.graph_keyword_retriever = None
        self.graph_vector_retriever = None
        self.graph_retriever = None
        self.graph_memory_manager = None
        self.dual_route_retriever = None
        self.atom_store = None
        self.atom_lifecycle_manager = None
        self.atom_retriever = None
        self.db_connection = None
        # 用户画像组件
        self.profile_store = None
        self.profile_manager = None
        self.profile_proposal_pipeline = None
        self.knowledge_proposal_pipeline = None
        self.note_proposal_pipeline = None
        self.semantic_compressor = None
        self.personalized_ranker = None
        # 自主学习
        self.auto_learning = None
        # 知识库
        self.knowledge_store = None
        self.knowledge_manager = None
        self.knowledge_retriever = None
        # 笔记系统
        self.note_store = None
        self.note_manager = None
        # 重排序器
        self.reranker = None
        # 由 ComponentFactory 在 canonical 组件创建完成后注入；为空时不影响主写链。
        self.memory_evolution_store = None
        self.memory_evolution_manager = None
        self._last_write_reason_code = None
        self._last_debug_trace: list[dict[str, Any]] = []
        # 子模块（db_connection 在 initialize 中注入）
        self._retrieval = RetrievalOptimizer(
            config=self.config,
            dual_route_retriever=self.dual_route_retriever,
            search_memories_cb=self.search_memories,
            get_memory_cb=self.get_memory,
            update_memory_cb=self.update_memory,
            create_tracked_task_cb=self._create_tracked_task,
        )
        self._write_journal = WriteOpJournal(
            db_connection=None,
            graph_memory_manager=self.graph_memory_manager,
            atom_store=self.atom_store,
            atom_enabled=self.atom_enabled,
            write_op_max_retries=int(
                self.config.get("write_reliability.max_retries", 3)
            ),
            get_memory_cb=self.get_memory,
            invalidate_cache_cb=self._retrieval.invalidate_cache,
            delete_doc_indexes_batch_cb=self._delete_document_indexes_for_batch,
            delete_graph_atoms_batch_cb=self._delete_graph_and_atoms_for_batch,
        )
        self._schema = SchemaManager(db_connection=None)
        self._maintenance = MaintenanceOperations(
            config=self.config,
            db_path=self.db_path,
            faiss_db=self.faiss_db,
            graph_store=self.graph_store,
            graph_memory_manager=self.graph_memory_manager,
            batch_delete_memories_cb=self.batch_delete_memories,
            invalidate_cache_cb=self._retrieval.invalidate_cache,
            update_memory_cb=self.update_memory,
        )

    # ==================== 委托封装（公开 API 不变） ====================

    def get_last_write_reason_code(self) -> str | None:
        """返回最近一次同步 canonical 写入的稳定原因码。"""

        return self._last_write_reason_code

    async def update_importance(self, memory_id: int, new_importance: float) -> bool:
        return await self.update_memory(memory_id, {"importance": new_importance})

    async def update_access_time(
        self, memory_id: int, recall_type: str = "passive"
    ) -> bool:
        return await self._maintenance.update_access_time(memory_id, recall_type)

    async def update_access_times_batch(
        self, memory_ids: list[int], recall_type: str = "passive"
    ) -> int:
        """批量更新访问时间，消除 SQLite 写锁串行化瓶颈"""
        return await self._maintenance.update_access_times_batch(
            memory_ids, recall_type
        )

    async def get_session_memories(
        self, session_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        return await self._maintenance.get_session_memories(session_id, limit)

    async def apply_daily_decay(self, decay_rate: float, days: int = 1) -> int:
        return await self._maintenance.apply_daily_decay(decay_rate, days)

    async def cleanup_old_memories(
        self,
        days_threshold: int | None = None,
        importance_threshold: float | None = None,
    ) -> int:
        return await self._maintenance.cleanup_old_memories(
            days_threshold, importance_threshold
        )

    async def consolidate_memories(self) -> dict[str, int]:
        return await self._retrieval.consolidate()

    async def get_statistics(self) -> dict[str, Any]:
        return await self._maintenance.get_statistics()

    async def maintain_storage(self, *, vacuum: bool = False) -> dict[str, Any]:
        return await self._maintenance.maintain_storage(vacuum=vacuum)

    async def rebuild_graph_index(self) -> dict[str, int]:
        return await self._maintenance.rebuild_graph_index()

    async def register_trigger(self, word: str, memory_id: int) -> None:
        await self._retrieval.register_trigger(word, memory_id)
