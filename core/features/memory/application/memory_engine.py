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

from .. import SchemaManager, TopicCatalogStore, WriteOpJournal
from .maintenance_operations import MaintenanceOperations
from .memory_engine_batch import MemoryEngineBatchMixin
from .memory_engine_crud import MemoryEngineCRUDMixin
from .memory_engine_domain_hooks import MemoryEngineDomainHooksMixin
from .memory_engine_evolution_hooks import MemoryEngineEvolutionHooksMixin
from .memory_engine_lifecycle import MemoryEngineLifecycleMixin
from .retrieval_optimizer import RetrievalOptimizer


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
        # Atom 重派生复用的规则分类端口，由组合根在 MemoryProcessor 建立后挂载。
        self.memory_processor = None
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
        # 生命周期观测器由组合根在注入组件就绪后绑定；引擎不拥有其存储生命周期。
        self.lifecycle_recorder: Any | None = None
        # ponytail: 单实例替换串行化；仅在吞吐瓶颈实测后再按 source 分锁。
        self._replacement_write_lock = asyncio.Lock()
        self._last_debug_trace: list[dict[str, Any]] = []
        # 子模块（db_connection 在 initialize 中注入）
        self._retrieval = RetrievalOptimizer(
            config=self.config,
            dual_route_retriever=self.dual_route_retriever,
            search_memories_cb=self.search_memories,
            get_memory_cb=self.get_memory,
            update_memory_cb=self.update_memory,
            apply_interference_decay_cb=self.apply_interference_decay,
        )
        self.topic_catalog_store = TopicCatalogStore(db_connection=None)
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
            topic_catalog_store=self.topic_catalog_store,
            repair_document_indexes_cb=self._repair_document_indexes,
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

    def set_lifecycle_recorder(self, recorder: Any | None) -> None:
        """绑定可选生命周期观测端口，不在引擎内创建或关闭观测存储。"""

        self.lifecycle_recorder = recorder

    def record_retrieved_candidates(
        self,
        results: Any,
        source: str = "passive",
        origin: str = "none",
    ) -> int:
        source_value = getattr(source, "value", source)
        origin_value = getattr(origin, "value", origin)
        if source_value not in {"passive", "agent", "debug"}:
            return 0
        if origin_value not in {"fresh", "cache", "none"}:
            return 0
        try:
            unique_ids = {
                value
                for result in results
                for value in (
                    result.get("id", result.get("doc_id"))
                    if isinstance(result, dict)
                    else getattr(result, "doc_id", None),
                )
                if type(value) is int and value > 0
            }
        except TypeError:
            return 0
        if not unique_ids:
            return 0
        recorder = getattr(self, "lifecycle_recorder", None)
        record = getattr(recorder, "record_retrieved", None)
        if not callable(record):
            return 0
        try:
            record(len(unique_ids), source=source_value, origin=origin_value)
        except asyncio.CancelledError:
            raise
        except Exception:
            return 0
        return len(unique_ids)

    async def find_replacement_memory_id(self, old_id: int) -> int | None:
        """按替换账本确认当前新 owner；未收敛、已删或无法证明时不返回 ID。"""

        if self.db_connection is None:
            return None
        cursor = await self.db_connection.execute(
            """
            SELECT op.memory_id FROM memory_write_ops AS op
            JOIN documents AS new ON new.id = op.memory_id
            WHERE op.op_type = 'replace_content'
              AND op.status IN ('completed', 'needs_repair')
              AND op.step IN ('replacement_committed', 'replacement_cleanup_pending')
              AND CASE WHEN json_valid(op.payload)
                  THEN json_extract(op.payload, '$.old_id') END = ?
              AND new.id != ?
              AND NOT EXISTS (SELECT 1 FROM documents WHERE id = ?)
              AND json_valid(new.metadata)
              AND NOT COALESCE(json_extract(new.metadata, '$.replacement_pending'), 0)
            ORDER BY op.id DESC LIMIT 1
            """,
            (int(old_id), int(old_id), int(old_id)),
        )
        try:
            row = await cursor.fetchone()
            return int(row[0]) if row is not None else None
        finally:
            await cursor.close()

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

    async def record_successful_injection(
        self,
        memory_ids: Any,
        source: str = "passive",
        origin: str = "none",
    ) -> int:
        source_value = getattr(source, "value", source)
        origin_value = getattr(origin, "value", origin)
        if source_value not in {"passive", "agent"}:
            return 0
        if origin_value not in {"fresh", "cache", "none"}:
            return 0
        try:
            unique_ids = tuple(
                dict.fromkeys(
                    value for value in memory_ids if type(value) is int and value > 0
                )
            )
        except TypeError:
            return 0
        if not unique_ids:
            return 0

        recorder = getattr(self, "lifecycle_recorder", None)
        record = getattr(recorder, "record_injected", None)
        try:
            await self.update_access_times_batch(list(unique_ids), "passive")
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

        try:
            top_k = max(
                0,
                int(
                    self.config.get(
                        "testing_effect_top_k",
                        self.config.get("recall_engine.testing_effect_top_k", 5),
                    )
                ),
            )
        except (TypeError, ValueError):
            top_k = 5
        use_async = bool(
            self.config.get(
                "testing_effect_async",
                self.config.get("recall_engine.testing_effect_async", True),
            )
        )

        async def reinforce() -> None:
            for memory_id in unique_ids[:top_k]:
                try:
                    await self.reinforce_recall_state(memory_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass

        if top_k:
            if use_async:
                task = reinforce()
                try:
                    self._create_tracked_task(task)
                except asyncio.CancelledError:
                    task.close()
                    raise
                except Exception:
                    task.close()
            else:
                await reinforce()

        if callable(record):
            try:
                record(len(unique_ids), source=source_value, origin=origin_value)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        return len(unique_ids)

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

    async def count_canonical_created_on(self, day_ts: int) -> int:
        """统计指定 UTC 日写入的 canonical 记忆数量（异常检测日聚合入口）。"""

        return await self._maintenance.count_canonical_created_on(day_ts)

    async def get_statistics(self) -> dict[str, Any]:
        return await self._maintenance.get_statistics()

    async def maintain_storage(self, *, vacuum: bool = False) -> dict[str, Any]:
        return await self._maintenance.maintain_storage(vacuum=vacuum)

    async def rebuild_graph_index(self) -> dict[str, Any]:
        return await self._maintenance.rebuild_graph_index()

    async def register_trigger(self, word: str, memory_id: int) -> None:
        await self._retrieval.register_trigger(word, memory_id)
