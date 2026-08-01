"""提供 MemoryEngine 的增删改查、检索和派生维护入口。"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from astrbot.api import logger

from ..models.recall_strategy import RecallStrategy
from ..models.temporal import canonical_visible_at, normalize_datetime
from ..retrieval.query_rewriter import resolve_reference_time
from ..retrieval.rrf_fusion import HybridResult
from ..utils.number_utils import clamp_float
from .atom_source_binding import bind_atoms_to_canonical_source
from .canonical_memory_reader import load_canonical_memory
from .memory_engine_atom_support import (
    prepare_atoms_for_write,
    record_quality_samples,
    reinforce_existing_atoms,
    successful_atoms,
)
from .memory_engine_evolution_hooks import memory_revision
from .retrieval_timing import RetrievalTimingSink
from .write_op_serialization import serialize_atom_for_repair


class MemoryEngineCRUDMixin:
    """MemoryEngine 核心 CRUD 方法"""

    # ==================== 核心 CRUD ====================

    async def add_memory(
        self,
        content: str,
        session_id: str | None = None,
        persona_id: str | None = None,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
        atoms: list | None = None,
    ) -> int:
        """提交 canonical memory，并在成功后维护 Atom、图与演化派生。"""

        if not content or not content.strip():
            raise ValueError("记忆内容不能为空")
        prepared_atoms = prepare_atoms_for_write(
            atoms or [],
            session_id=session_id,
            persona_id=persona_id,
            config=self.config,
        )
        write_started = time.perf_counter()
        op_id = await self._write_journal.start_op(
            "add",
            {
                "content_preview": content[:500],
                "session_id": session_id,
                "persona_id": persona_id,
                "importance": importance,
                "metadata": metadata or {},
                "atoms": [serialize_atom_for_repair(a) for a in prepared_atoms],
            },
        )
        current_time = time.time()
        full_metadata = {
            "session_id": session_id,
            "persona_id": persona_id,
            "importance": max(0.0, min(1.0, importance)),
            "create_time": current_time,
            "last_access_time": current_time,
        }
        if metadata:
            full_metadata.update(metadata)
        full_metadata["create_time"] = current_time
        full_metadata["last_access_time"] = current_time
        if self.hybrid_retriever is None:
            self._record_add_memory_failure("not_initialized")
            raise RuntimeError("混合检索器未初始化")
        try:
            doc_id = await self.hybrid_retriever.add_memory(content, full_metadata)
            await self._write_journal.advance_op(
                op_id,
                "document_indexed",
                memory_id=doc_id,
                payload_patch={"memory_id": doc_id},
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._write_journal.advance_op(
                op_id, "document_failed", status="failed", error=str(e)
            )
            self._record_add_memory_failure("document")
            raise
        atom_write_failed = False
        if prepared_atoms and self.atom_store is not None and self.atom_enabled:
            sources_bound = False
            try:
                canonical_memory = await self.get_memory(doc_id)
                prepared_atoms = bind_atoms_to_canonical_source(
                    prepared_atoms,
                    canonical_memory,
                    fallback_metadata=full_metadata,
                )
                sources_bound = True
                await reinforce_existing_atoms(
                    self.atom_lifecycle_manager,
                    prepared_atoms,
                )
                await self.atom_store.insert_many(prepared_atoms)
                await self._write_journal.advance_op(
                    op_id, "atoms_indexed", memory_id=doc_id
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("[MemoryEngine] 批量写入记忆原子失败", exc_info=True)
                failed_atoms: list[dict[str, Any]] = []
                if sources_bound:
                    for atom in prepared_atoms:
                        if getattr(atom, "atom_id", 0):
                            continue
                        try:
                            await self.atom_store.insert(atom)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            failed_atoms.append(serialize_atom_for_repair(atom))
                else:
                    failed_atoms = [
                        serialize_atom_for_repair(atom) for atom in prepared_atoms
                    ]
                if failed_atoms:
                    await self._write_journal.advance_op(
                        op_id,
                        "atoms_partial",
                        status="needs_repair",
                        memory_id=doc_id,
                        error="atom insert failed",
                        payload_patch={"failed_atoms": failed_atoms},
                    )
                    self._record_add_memory_failure("atom")
                    atom_write_failed = True
                else:
                    await self._write_journal.advance_op(
                        op_id, "atoms_indexed", memory_id=doc_id
                    )
        else:
            await self._write_journal.advance_op(
                op_id, "atoms_skipped", memory_id=doc_id
            )
        persisted_atoms = successful_atoms(prepared_atoms)
        needs_repair = atom_write_failed
        if self.graph_memory_manager is not None:
            try:
                await self.graph_memory_manager.index_memory(
                    doc_id,
                    content,
                    full_metadata,
                    persisted_atoms or None,
                )
                await self._write_journal.advance_op(
                    op_id,
                    "graph_indexed",
                    status="needs_repair" if needs_repair else "pending",
                    memory_id=doc_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await self._write_journal.advance_op(
                    op_id,
                    "graph_failed",
                    status="needs_repair",
                    memory_id=doc_id,
                    error=str(e),
                )
                self._record_add_memory_failure("graph")
                needs_repair = True
                logger.error(
                    f"[MemoryEngine] 图记忆索引失败 (memory_id={doc_id})", exc_info=True
                )
        else:
            await self._write_journal.advance_op(
                op_id,
                "graph_skipped",
                status="needs_repair" if needs_repair else "pending",
                memory_id=doc_id,
            )
        if not needs_repair:
            await self._write_journal.advance_op(
                op_id, "completed", status="completed", memory_id=doc_id
            )
        self._retrieval.invalidate_cache()
        self._create_tracked_task(self._retrieval.apply_interference(doc_id, content))
        self._create_tracked_task(self._retrieval.extract_triggers(content, doc_id))
        sse = getattr(self, "sse", None)
        if sse is not None:
            self._create_tracked_task(
                sse.publish(
                    "memory_created", {"doc_id": doc_id, "content": content[:200]}
                )
            )
        self._record_add_memory_observability(
            doc_id=doc_id,
            content=content,
            metadata=full_metadata,
            atoms=persisted_atoms,
            duration_s=time.perf_counter() - write_started,
        )
        await self._schedule_evolution_after_write(doc_id)
        self._schedule_profile_proposal_after_write(doc_id)
        return doc_id

    @staticmethod
    def _record_add_memory_failure(stage: str) -> None:
        """按固定阶段记录 canonical 写入失败计数。"""

        try:
            from ..monitoring.metrics import MEMORY_WRITE_FAILURES_TOTAL

            MEMORY_WRITE_FAILURES_TOTAL.labels(stage=stage).inc()
        except Exception:
            logger.debug("[MemoryEngine] 写入失败指标记录失败", exc_info=True)

    def _record_add_memory_observability(
        self,
        *,
        doc_id: int,
        content: str,
        metadata: dict[str, Any],
        atoms: list | None,
        duration_s: float,
    ) -> None:
        """canonical 提交后记录低成本写入指标与质量样本。"""
        try:
            from ..monitoring.metrics import MEMORY_ATOMS_TOTAL, MEMORY_WRITE_DURATION

            MEMORY_WRITE_DURATION.observe(max(0.0, duration_s))
            if atoms:
                MEMORY_ATOMS_TOTAL.inc(len(atoms))
        except Exception:
            logger.debug("[MemoryEngine] 写入指标记录失败", exc_info=True)

        scorer = getattr(self, "_quality_scorer", None) or getattr(
            self, "quality_scorer", None
        )
        if scorer is None:
            return
        try:
            record_quality_samples(
                scorer,
                doc_id=doc_id,
                content=content,
                metadata=metadata,
                atoms=list(atoms or []),
            )
        except Exception:
            logger.warning("[MemoryEngine] 质量评分记录失败", exc_info=True)

    async def search_memories(
        self,
        query: str,
        k: int = 5,
        session_id: str | None = None,
        persona_id: str | None = None,
        emotion_context: list[str] | None = None,
        recall_type: str = "passive",
        chain_depth: int = 0,
        recall_strategy: RecallStrategy | None = None,
        memory_types: list[str] | None = None,
        chat_type: str = "private",
        query_intent: Any | None = None,
        user_id: str | None = None,
        trace_debug: bool = False,
        debug_trace: list[dict[str, Any]] | None = None,
        reference_time: datetime | None = None,
        query_plan: Any | None = None,
        timing_sink: RetrievalTimingSink | None = None,
        deadline_monotonic: float | None = None,
    ) -> list[HybridResult]:
        """执行受 scope、privacy、参考时间与可选软截止时间约束的召回。"""

        requested_reference_time = normalize_datetime(
            reference_time
        ) or resolve_reference_time(query_intent)
        effective_reference_time = requested_reference_time or datetime.now(
            timezone.utc
        )
        trace_requested = trace_debug or debug_trace is not None
        active_debug_trace = (
            debug_trace if debug_trace is not None else ([] if trace_debug else None)
        )
        if active_debug_trace is not None:
            active_debug_trace.clear()
            self._last_debug_trace = active_debug_trace
        if not query or not query.strip():
            return []

        # 阶段计时：追踪每次检索的各阶段耗时
        _t_start = time.perf_counter()
        _t_cache = _t_start
        cache_intent = query_plan or query_intent
        cache_key = self._retrieval.cache_key(
            query,
            k,
            session_id,
            persona_id,
            user_id=user_id,
            chat_type=chat_type,
            memory_types=memory_types,
            query_intent=cache_intent,
            chain_depth=chain_depth,
            emotion_context=emotion_context,
            recall_strategy=recall_strategy,
            reference_time=requested_reference_time,
        )
        cached_results = (
            None if trace_requested else self._retrieval.get_cached(cache_key)
        )
        if cached_results is not None:
            _t_cache_end = time.perf_counter()
            ids = [
                r.doc_id
                for r in cached_results
                if getattr(r, "doc_id", None) is not None
            ]
            if ids:
                self._create_tracked_task(
                    self._maintenance.update_access_times_batch(ids, recall_type)
                )
            self._last_search_timing = {
                "cache_hit": True,
                "cache_lookup_ms": (_t_cache_end - _t_cache) * 1000.0,
                "retrieval_total_ms": (_t_cache_end - _t_start) * 1000.0,
            }
            if timing_sink is not None:
                timing_sink.update(self._last_search_timing)
            return cached_results

        # 请求级会话缓存：消除 Bridge→RecallHandler 同一请求的重复搜索
        session_cached = None
        if not trace_requested:
            session_cached = self._retrieval.get_session_cached(
                query,
                k,
                session_id,
                persona_id,
                user_id=user_id,
                chat_type=chat_type,
                memory_types=memory_types,
                query_intent=cache_intent,
                chain_depth=chain_depth,
                emotion_context=emotion_context,
                recall_strategy=recall_strategy,
                reference_time=requested_reference_time,
            )
        _t_cache_end = time.perf_counter()
        if session_cached is not None:
            # 会话缓存可能用不同 k 检索，截断到请求的 k 值
            truncated = session_cached[:k]
            # 仍更新 access time
            ids = [
                r.doc_id for r in truncated if getattr(r, "doc_id", None) is not None
            ]
            if ids:
                self._create_tracked_task(
                    self._maintenance.update_access_times_batch(ids, recall_type)
                )
            self._retrieval.set_cached(cache_key, truncated)
            self._last_search_timing = {
                "cache_hit": True,
                "cache_lookup_ms": (_t_cache_end - _t_cache) * 1000.0,
                "retrieval_total_ms": (_t_cache_end - _t_start) * 1000.0,
            }
            if timing_sink is not None:
                timing_sink.update(self._last_search_timing)
            return truncated
        if session_id and ":" in session_id:
            self._create_tracked_task(
                self._maintenance.migrate_session_if_needed(session_id)
            )
        # 自适应候选规模：根据查询意图调整 fetch_k
        intent_str = (
            getattr(query_intent, "intent", "default") if query_intent else "default"
        )
        if intent_str in ("factual", "preference"):
            fetch_k = max(k * 2, 8)
        elif intent_str in ("relationship", "temporal"):
            fetch_k = max(k * 3, 12)
        else:
            fetch_k = max(k * 2, 10)
        _t_search_start = time.perf_counter()
        _t_doc_route = 0.0
        _t_graph_route = 0.0
        _t_merge = 0.0
        _t_rerank = 0.0
        route_timing: dict[str, float | int | bool] = {}
        if self.dual_route_retriever is not None:
            results = await self.dual_route_retriever.search(
                query,
                fetch_k,
                session_id,
                persona_id,
                strategy=recall_strategy,
                memory_types=memory_types,
                chat_type=chat_type,
                query_intent=cache_intent,
                user_id=user_id,
                reference_time=effective_reference_time,
                query_plan=query_plan,
                timing_sink=route_timing,
                deadline_monotonic=deadline_monotonic,
            )
            _t_doc_route = float(route_timing.get("document_route_ms", 0.0))
            _t_graph_route = float(route_timing.get("graph_route_ms", 0.0))
            _t_merge = float(route_timing.get("merge_ms", 0.0))
            _t_rerank = float(route_timing.get("rerank_ms", 0.0))
        else:
            if self.hybrid_retriever is None:
                raise RuntimeError("混合检索器未初始化")
            results = await self.hybrid_retriever.search(
                query,
                fetch_k,
                session_id,
                persona_id,
                memory_types=memory_types,
                timing_sink=route_timing,
                deadline_monotonic=deadline_monotonic,
            )
            # 群聊过滤机密记忆（hybrid_retriever 不支持 chat_type 参数，后置过滤）
            if chat_type == "group":
                results = [
                    r
                    for r in results
                    if (r.metadata or {}).get("privacy_level", "shared")
                    != "confidential"
                ]
        _t_search_end = time.perf_counter()
        results = [
            item
            for item in results
            if canonical_visible_at(item.metadata or {}, requested_reference_time)
        ]
        _t_boost = 0.0
        if results:
            _t_boost_start = time.perf_counter()
            results = await self._retrieval.apply_trigger_boost(query, results)
            if active_debug_trace is not None:
                results = await self._retrieval.apply_boosts(
                    results,
                    emotion_context,
                    debug_trace=active_debug_trace,
                )
                self._last_debug_trace = active_debug_trace
            else:
                results = await self._retrieval.apply_boosts(results, emotion_context)
            results = results[:k]
            _t_boost = (time.perf_counter() - _t_boost_start) * 1000.0
        _t_chain = 0.0
        if chain_depth > 0 and results:
            # R2: 多跳检索 — 仅对关系/时间查询或显式 trace 启用
            # 事实类与偏好类查询跳过链式扩展以节省计算
            _should_expand = (
                intent_str in ("relationship", "temporal")
                or trace_requested
                or chain_depth > 1  # 显式要求深度 > 1
            )
            if _should_expand:
                _t_chain_start = time.perf_counter()
                max_hops = self.config.get("recall_engine.max_chain_hops", chain_depth)
                hop_decay = self.config.get("recall_engine.chain_hop_decay", None)
                chained = await self._retrieval.chain_expand_multi_hop(
                    results,
                    k,
                    session_id,
                    persona_id,
                    max_hops=max_hops,
                    hop_decay=hop_decay,
                    reference_time=requested_reference_time,
                )
                _t_chain = (time.perf_counter() - _t_chain_start) * 1000.0
                if chained:
                    results = chained[:k]
        ids = [r.doc_id for r in results if getattr(r, "doc_id", None) is not None]
        if ids:
            self._create_tracked_task(
                self._maintenance.update_access_times_batch(ids, recall_type)
            )
        if not trace_requested:
            self._retrieval.set_cached(cache_key, results)
            self._retrieval.set_session_cached(
                query,
                k,
                session_id,
                persona_id,
                results,
                user_id=user_id,
                chat_type=chat_type,
                memory_types=memory_types,
                query_intent=cache_intent,
                chain_depth=chain_depth,
                emotion_context=emotion_context,
                recall_strategy=recall_strategy,
                reference_time=requested_reference_time,
            )
        # === 存储阶段计时供 RecallHandler 读取 ===
        retrieval_total_ms = (time.perf_counter() - _t_start) * 1000.0
        self._last_search_timing = {
            "cache_hit": False,
            "cache_lookup_ms": (_t_cache_end - _t_cache) * 1000.0,
            "total_search_ms": (_t_search_end - _t_search_start) * 1000.0,
            "retrieval_total_ms": retrieval_total_ms,
            "bm25_ms": _t_doc_route,  # 文档路含 BM25+Vector
            "vector_ms": 0.0,  # 包含在 document_route_ms 中
            "graph_ms": _t_graph_route,
            "rerank_ms": _t_rerank,
            "merge_ms": _t_merge,
            "boost_ms": _t_boost,
            "chain_expand_ms": _t_chain,
        }
        self._last_search_timing.update(route_timing)
        if timing_sink is not None:
            timing_sink.update(self._last_search_timing)
        return results

    async def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        """按 canonical 整数 ID 读取记忆详情。"""

        try:
            return await load_canonical_memory(
                self.faiss_db,
                self.db_connection,
                memory_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[MemoryEngine] 获取记忆详情失败", exc_info=True)
            return None

    async def update_memory(
        self,
        memory_id: int,
        updates: dict[str, Any],
        skip_graph_reindex: bool = False,
        expected_revision: str | None = None,
    ) -> bool:
        """更新 canonical memory，并在需要时执行 source revision 乐观校验。

        ``expected_revision`` 只用于内部受控调用方；缺省时保持既有 API
        行为。revision 不匹配会以稳定 reason code 记录并返回 ``False``，
        不会触碰 canonical 正文或任何派生索引。
        """

        self._last_write_reason_code = None
        if expected_revision is None and isinstance(updates, dict):
            embedded_revision = updates.get("expected_revision")
            if embedded_revision is not None:
                expected_revision = str(embedded_revision)
                updates = {
                    key: value
                    for key, value in updates.items()
                    if key != "expected_revision"
                }
        memory = await self.get_memory(memory_id)
        if not memory:
            logger.error(f"[更新] 记忆不存在 (memory_id={memory_id})")
            self._last_write_reason_code = "source_not_found"
            return False
        if expected_revision is not None:
            current_revision = memory_revision(memory)
            if not current_revision or current_revision != str(expected_revision):
                self._last_write_reason_code = "source_revision_mismatch"
                logger.warning(
                    f"[更新] source revision 冲突，拒绝覆盖 (memory_id={memory_id})"
                )
                return False
        current_metadata = memory.get("metadata", {})
        if isinstance(current_metadata, str):
            try:
                current_metadata = json.loads(current_metadata)
            except (json.JSONDecodeError, TypeError):
                current_metadata = {}
        elif not isinstance(current_metadata, dict):
            current_metadata = {}
        if "content" in updates:
            new_content = updates["content"]
            if not new_content or not new_content.strip():
                self._last_write_reason_code = "invalid_content"
                return False
            if expected_revision is not None:
                if self.hybrid_retriever is None:
                    self._last_write_reason_code = "not_initialized"
                    return False
                guarded_metadata = current_metadata.copy()
                guarded_metadata["updated_at"] = time.time()
                success = await self.hybrid_retriever.update_content_if_revision(
                    memory_id,
                    new_content,
                    guarded_metadata,
                    expected_revision,
                )
                if not success:
                    self._last_write_reason_code = "source_revision_mismatch"
                    return False
                await self._invalidate_evolution_after_revision(memory_id)
                await self._schedule_evolution_after_write(memory_id)
                self._retrieval.invalidate_cache()
                if self.graph_memory_manager is not None and not skip_graph_reindex:
                    try:
                        await self.graph_memory_manager.index_memory(
                            memory_id,
                            new_content,
                            guarded_metadata,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.warning(
                            "[更新] 正文已提交但图派生刷新失败",
                            exc_info=True,
                        )
                return True
            try:
                session_id = current_metadata.get("session_id")
                persona_id = current_metadata.get("persona_id")
                importance = clamp_float(
                    current_metadata.get("importance", updates.get("importance", 0.5)),
                    default=0.5,
                )
                new_metadata = current_metadata.copy()
                new_metadata["updated_at"] = time.time()
                new_metadata["previous_id"] = memory_id
                new_memory_id = await self.add_memory(
                    content=new_content,
                    session_id=session_id,
                    persona_id=persona_id,
                    importance=importance,
                    metadata=new_metadata,
                )
                if new_memory_id is None:
                    logger.error(f"[更新] 创建新记忆失败 (old_id={memory_id})")
                    return False
                delete_success = await self.delete_memory(memory_id)
                if not delete_success:
                    logger.warning(
                        f"[更新] 删除旧记忆失败，回滚 (old={memory_id}, new={new_memory_id})"
                    )
                    await self.delete_memory(new_memory_id)
                    return False
                logger.info(f"[更新] 内容更新完成 ({memory_id} → {new_memory_id})")
                self._retrieval.invalidate_cache()
                return True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[更新] 内容更新失败 ({memory_id}): {e}", exc_info=True)
                return False
        metadata_updates = {}
        if "importance" in updates:
            metadata_updates["importance"] = clamp_float(
                updates["importance"], default=0.5
            )
        if "metadata" in updates:
            metadata_updates.update(updates["metadata"])
        if metadata_updates:
            if not isinstance(current_metadata, dict):
                try:
                    current_metadata = (
                        json.loads(current_metadata)
                        if isinstance(current_metadata, str)
                        else {}
                    )
                except (json.JSONDecodeError, TypeError):
                    current_metadata = {}
            current_metadata.update(metadata_updates)
            current_metadata["updated_at"] = time.time()
            if self.hybrid_retriever is None:
                logger.error("混合检索器未初始化")
                return False
            if expected_revision is None:
                success = await self.hybrid_retriever.update_metadata(
                    memory_id,
                    metadata_updates,
                )
            else:
                success = await self.hybrid_retriever.update_metadata(
                    memory_id,
                    metadata_updates,
                    expected_revision=expected_revision,
                )
            if success:
                await self._invalidate_evolution_after_revision(memory_id)
                await self._schedule_evolution_after_write(memory_id)
                self._retrieval.invalidate_cache()
                if self.graph_memory_manager is not None and not skip_graph_reindex:
                    op_id = await self._write_journal.start_op(
                        "graph_reindex",
                        {"memory_id": memory_id, "metadata": current_metadata},
                        memory_id=memory_id,
                    )
                    try:
                        await self.graph_memory_manager.index_memory(
                            memory_id, memory["text"], current_metadata
                        )
                        await self._write_journal.advance_op(
                            op_id,
                            "graph_reindexed",
                            status="completed",
                            memory_id=memory_id,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        await self._write_journal.advance_op(
                            op_id,
                            "graph_reindex_failed",
                            status="needs_repair",
                            memory_id=memory_id,
                            error=str(e),
                            payload_patch={"metadata": current_metadata},
                        )
                        logger.error(
                            f"[更新] 图记忆重建失败，已加入修复队列 (memory_id={memory_id}): {e}",
                            exc_info=True,
                        )
                        return False
            return success
        return True

    async def delete_memory(self, memory_id: int) -> bool:
        """删除 canonical memory，并清理或失效关联派生对象。"""

        op_id = await self._write_journal.start_op(
            "delete", {"memory_id": memory_id}, memory_id=memory_id
        )
        if self.hybrid_retriever is None:
            logger.error("混合检索器未初始化")
            await self._write_journal.advance_op(
                op_id,
                "document_delete_failed",
                status="failed",
                error="hybrid retriever not initialized",
            )
            return False
        success = await self.hybrid_retriever.delete_memory(memory_id)
        if not success:
            await self._write_journal.advance_op(
                op_id,
                "document_delete_failed",
                status="failed",
                error="document/vector delete failed",
            )
            return False
        await self._write_journal.advance_op(
            op_id, "document_deleted", memory_id=memory_id
        )
        needs_repair = await self._delete_sub_resources(memory_id, op_id)
        await self._invalidate_evolution_after_delete(memory_id)
        if not needs_repair:
            await self._write_journal.advance_op(
                op_id, "completed", status="completed", memory_id=memory_id
            )
        self._retrieval.invalidate_cache()
        return success

    async def _delete_sub_resources(self, memory_id: int, op_id: int | None) -> bool:
        """删除图记忆和原子子资源，返回是否需修复"""
        needs_repair = False
        try:
            if self.graph_memory_manager is not None:
                await self.graph_memory_manager.delete_memory(memory_id)
            if op_id is not None:
                await self._write_journal.advance_op(
                    op_id, "graph_deleted", memory_id=memory_id
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if op_id is not None:
                await self._write_journal.advance_op(
                    op_id,
                    "graph_delete_failed",
                    status="needs_repair",
                    memory_id=memory_id,
                    error=str(e),
                )
            needs_repair = True
            logger.error(
                f"[MemoryEngine] 图记忆删除失败 (memory_id={memory_id})", exc_info=True
            )
        try:
            if self.atom_store is not None:
                await self.atom_store.delete_by_parent(memory_id)
            if op_id is not None:
                await self._write_journal.advance_op(
                    op_id, "atoms_deleted", memory_id=memory_id
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if op_id is not None:
                await self._write_journal.advance_op(
                    op_id,
                    "atom_delete_failed",
                    status="needs_repair",
                    memory_id=memory_id,
                    error=str(e),
                )
            needs_repair = True
            logger.error(
                f"[MemoryEngine] 原子删除失败 (memory_id={memory_id})", exc_info=True
            )
        return needs_repair
