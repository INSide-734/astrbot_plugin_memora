"""提供 MemoryEngine 的增删改查、检索和派生维护入口。"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, cast

from astrbot.api import logger

from ....shared.contracts.events import CanonicalMemoryCommitted
from ....shared.memory_status import is_memory_recallable
from ....shared.number_utils import clamp_float
from ....shared.recall_strategy import RecallStrategy
from ....shared.temporal import canonical_visible_at, normalize_datetime
from ...injection.application.selection import metadata_has_user_evidence
from ...observability.application.memory_write_timing import (
    measure_memory_write_stage,
)
from ...quality.application.gate_disposition_filter import (
    filter_mark_write,
    is_mark_write,
)
from ...retrieval.query_rewriter import resolve_reference_time
from ...retrieval.rrf_fusion import HybridResult
from ..domain.revision import memory_revision, revision_is_stale, revision_snapshot
from ..graph.domain.models import GraphQueryScope
from ..infrastructure.canonical_memory_reader import (
    load_canonical_memories,
    load_canonical_memory,
)
from ..infrastructure.write_op_repair import (
    CONTENT_PREVIEW_LIMIT,
    ledger_content_digest,
)
from ..infrastructure.write_op_serialization import (
    serialize_atom_for_repair,
)
from .atom_source_binding import (
    bind_atoms_to_canonical_source,
)
from .fact_text_alignment import (
    FactTextAlignment,
    facts_aligned,
)
from .memory_engine_atom_support import (
    prepare_atoms_for_write,
    reinforce_existing_atoms,
    successful_atoms,
)
from .memory_engine_idempotency import MemoryEngineIdempotencyMixin
from .memory_engine_semantic_updates import (
    apply_interference_maintenance_delta,
    apply_reinforcement_maintenance_delta,
    is_runtime_maintenance_delta,
    prepare_semantic_metadata_update,
)
from .memory_engine_write_observability import MemoryEngineWriteObservabilityMixin
from .retrieval_timing import RetrievalTimingSink


def _user_evidence_filter(results: list[HybridResult]) -> list[HybridResult]:
    """只保留全部事实与摘要都能归属用户来源的候选。"""

    return [
        item
        for item in results
        if metadata_has_user_evidence(getattr(item, "metadata", None))
    ]


def _has_revision_snapshot(metadata: dict[str, Any]) -> bool:
    """判断候选 metadata 是否自带可比较的 canonical revision 快照。"""

    token = metadata.get("revision_token")
    if isinstance(token, str) and token.strip():
        return True
    return metadata.get("updated_at") is not None


def _request_visibility_violation(
    metadata: dict[str, Any],
    *,
    chat_type: str,
    query_scope: GraphQueryScope | None,
) -> bool:
    """按本次请求的可见性判定 canonical 行是否仍可返回。

    复用召回链路的既有口径：群会话不返回 ``confidential`` 行（缺
    ``privacy_level`` 按 ``shared``，与 ``DualRouteRetriever._filter_by_privacy``
    一致）；请求给出来源范围时，行的 scope/privacy 必须与请求范围一致（与图路
    ``GraphRetriever`` 的 boundary 校验同义）。判定只返回是否剔除，不回显行字段。
    """

    if (
        chat_type == "group"
        and metadata.get("privacy_level", "shared") == "confidential"
    ):
        return True
    if query_scope is None:
        return False
    return (
        metadata.get("scope_key") != query_scope.scope_key
        or metadata.get("privacy_level") != query_scope.privacy_level
    )


class MemoryEngineCRUDMixin(
    MemoryEngineIdempotencyMixin,
    MemoryEngineWriteObservabilityMixin,
):
    """MemoryEngine 核心 CRUD 方法"""

    # ==================== 核心 CRUD ====================

    async def _add_memory_unchecked(
        self,
        content: str,
        session_id: str | None = None,
        persona_id: str | None = None,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
        atoms: list | None = None,
        *,
        summary_source_staged: bool = False,
    ) -> int:
        """执行尚未按幂等键过滤的 canonical 写入。"""

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
                # 崩溃修复用摘要证明正文未被改写；正文本身不得进入账本之外的日志。
                "content_digest": CanonicalMemoryCommitted.digest_content(content)[:32],
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
        if isinstance(full_metadata.get("topics"), (list, tuple)):
            full_metadata["topic_observed_at"] = current_time
        summary_source_staged = summary_source_staged or bool(
            full_metadata.get("replacement_pending")
        )
        if self.hybrid_retriever is None:
            self._record_add_memory_failure("not_initialized")
            raise RuntimeError("混合检索器未初始化")
        document_outcome = await self._write_document_stage(
            content,
            full_metadata,
            metadata,
            op_id,
        )
        if document_outcome.owner_reused:
            return document_outcome.doc_id
        doc_id = document_outcome.doc_id
        # canonical 之后任一阶段失败都只降级：账本保留待修复意图，不撤销已提交行。
        needs_repair = document_outcome.index_degraded
        with measure_memory_write_stage("atom"):
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
                    if not summary_source_staged:
                        # 未接受来源不得强化既有 Atom；接受后由来源 owner 重试收尾。
                        await reinforce_existing_atoms(
                            self.atom_lifecycle_manager,
                            prepared_atoms,
                        )
                    await self.atom_store.insert_many(prepared_atoms)
                    await self._write_journal.advance_op(
                        op_id,
                        "atoms_indexed",
                        status="needs_repair" if needs_repair else "pending",
                        memory_id=doc_id,
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
                            op_id,
                            "atoms_indexed",
                            status="needs_repair" if needs_repair else "pending",
                            memory_id=doc_id,
                        )
            else:
                await self._write_journal.advance_op(
                    op_id,
                    "atoms_skipped",
                    status="needs_repair" if needs_repair else "pending",
                    memory_id=doc_id,
                )
        persisted_atoms = successful_atoms(prepared_atoms)
        needs_repair = needs_repair or atom_write_failed
        with measure_memory_write_stage("graph"):
            if summary_source_staged:
                # 来源尚未接受：禁止派生图产物，只保留待收口意图供接受后统一收尾。
                await self._write_journal.advance_op(
                    op_id,
                    "source_staged",
                    status="needs_repair" if needs_repair else "pending",
                    memory_id=doc_id,
                )
            elif self.graph_memory_manager is not None:
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
                        f"[MemoryEngine] 图记忆索引失败 (memory_id={doc_id})",
                        exc_info=True,
                    )
            else:
                await self._write_journal.advance_op(
                    op_id,
                    "graph_skipped",
                    status="needs_repair" if needs_repair else "pending",
                    memory_id=doc_id,
                )
        if not needs_repair and not summary_source_staged:
            await self._write_journal.advance_op(
                op_id, "completed", status="completed", memory_id=doc_id
            )
        self._retrieval.invalidate_cache()
        if summary_source_staged:
            return doc_id
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
        self._schedule_domain_proposals_after_write(doc_id)
        return doc_id

    async def _revalidate_cached_results(
        self,
        results: list[HybridResult],
        *,
        include_mark_write: bool,
        require_user_evidence: bool,
        reference_time: datetime | None,
        chat_type: str,
        query_scope: GraphQueryScope | None,
    ) -> tuple[list[HybridResult], int] | None:
        """按 canonical 当前状态与本次请求可见性重校验缓存命中结果。

        R2.1：缓存命中不再盲信缓存——按结果 ``doc_id`` 批量回读 canonical
        （单次主键批量查询，无网络调用），剔除已删除、正文已改写、不可召回、
        mark_write（未显式包含时）、缺少用户证据（显式要求时）、当前 as-of
        不可见，或被本次请求范围排除（群会话机密行、请求 scope/privacy 不一致）
        的条目，并返回 ``(有效结果, 剔除数量)``。

        返回 ``None`` 表示 canonical 回读不可用：此时无法证明缓存正文仍属于
        当前 canonical，调用方不得把它当结果返回，按未命中回落实时检索。
        """

        if not results:
            return [], 0
        doc_ids = [
            item.doc_id
            for item in results
            if isinstance(getattr(item, "doc_id", None), int)
        ]
        try:
            records = await load_canonical_memories(
                self.faiss_db,
                doc_ids,
                # 读取端口的 db 连接是可选的：轻量宿主没有它时回退文档字段。
                getattr(self, "db_connection", None),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[检索] 缓存重校验无法回读 canonical，回退实时检索")
            return None
        visible: list[HybridResult] = []
        dropped = 0
        for item in results:
            record = records.get(item.doc_id)
            if record is None:
                dropped += 1
                continue
            metadata = record["metadata"]
            if (
                record.get("text") != item.content
                or not is_memory_recallable(metadata)
                or (not include_mark_write and is_mark_write(metadata))
                or (require_user_evidence and not metadata_has_user_evidence(metadata))
                or not canonical_visible_at(metadata, reference_time)
                or _request_visibility_violation(
                    metadata, chat_type=chat_type, query_scope=query_scope
                )
                or revision_is_stale(
                    # R2.1：revision 相等也是命中条件——缓存条目按契约必须自带
                    # revision 快照（发布时固化），缺失而 canonical 行已有快照即
                    # 无法证明相等；旧正文与其携带的旧派生投影一并失效。
                    item.metadata,
                    metadata,
                    revision_snapshot(record),
                    require_snapshot=True,
                )
            ):
                dropped += 1
                continue
            visible.append(item)
        return visible, dropped

    async def _snapshot_candidates_for_cache(
        self, results: list[HybridResult]
    ) -> list[HybridResult]:
        """返回带 canonical revision 快照的缓存副本（R2.1）。

        首次 add 写入的 metadata 可能既无 ``revision_token`` 也无 ``updated_at``，
        候选因此无法在命中时证明仍等于当前 canonical。这里按 ``doc_id`` 一次批量
        回读 canonical，把权威 revision 快照写进缓存副本；只有正文与当前 canonical
        正文一致的候选才补快照（否则等于把旧候选伪装成新 revision），已有快照的
        候选原样复用，回读失败时保持原状（由命中时的 fail-closed 校验兜底）。返回
        副本只交给检索缓存，调用方拿到的候选不被改写。
        """

        missing = [
            item
            for item in results
            if isinstance(getattr(item, "doc_id", None), int)
            and isinstance(item.metadata, dict)
            and not _has_revision_snapshot(item.metadata)
        ]
        if not missing:
            return results
        try:
            records = await load_canonical_memories(
                self.faiss_db,
                [item.doc_id for item in missing],
                getattr(self, "db_connection", None),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("[检索] 缓存建立时无法回读 canonical revision 快照")
            return results
        stamped: dict[int, HybridResult] = {}
        for item in missing:
            record = records.get(item.doc_id)
            if not isinstance(record, dict) or record.get("text") != item.content:
                continue
            snapshot = revision_snapshot(record)
            if not snapshot:
                continue
            metadata = dict(item.metadata)
            metadata["revision_token"] = snapshot
            stamped[item.doc_id] = replace(item, metadata=metadata)
        if not stamped:
            return results
        return [stamped.get(item.doc_id, item) for item in results]

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
        include_mark_write: bool = False,
        query_scope: GraphQueryScope | None = None,
        require_user_evidence: bool = False,
    ) -> list[HybridResult]:
        """执行受 scope、privacy、参考时间与可选软截止时间约束的召回。

        ``require_user_evidence`` 为真时，只有全部事实与摘要都归属用户来源的
        候选才允许占用 k 名额、缓存与链式扩展种子；缺省值保持既有 API 行为。
        """
        if query_scope is not None:
            query_scope = GraphQueryScope.require(query_scope)

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
            return filter_mark_write([], include_mark_write=include_mark_write)

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
            recall_strategy=recall_strategy,
            reference_time=requested_reference_time,
            include_mark_write=include_mark_write,
            query_scope=query_scope,
            require_user_evidence=require_user_evidence,
        )
        cached_results = (
            None if trace_requested else self._retrieval.get_cached(cache_key)
        )
        if cached_results is not None:
            _t_cache_end = time.perf_counter()
            revalidated = await self._revalidate_cached_results(
                cached_results,
                include_mark_write=include_mark_write,
                require_user_evidence=require_user_evidence,
                reference_time=effective_reference_time,
                chat_type=chat_type,
                query_scope=query_scope,
            )
            if revalidated is not None:
                visible, stale_dropped = revalidated
                ids = [
                    r.doc_id for r in visible if getattr(r, "doc_id", None) is not None
                ]
                if ids:
                    self._create_tracked_task(
                        self._maintenance.update_access_times_batch(ids, recall_type)
                    )
                self._last_search_timing = {
                    "cache_hit": True,
                    "cache_lookup_ms": (_t_cache_end - _t_cache) * 1000.0,
                    "retrieval_total_ms": (_t_cache_end - _t_start) * 1000.0,
                    "dropped_stale_count": stale_dropped,
                }
                if timing_sink is not None:
                    timing_sink.update(self._last_search_timing)
                return visible
            # 无法确认 canonical 当前状态：不得返回未经验证的缓存正文，按未命中回落。

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
                recall_strategy=recall_strategy,
                reference_time=requested_reference_time,
                include_mark_write=include_mark_write,
                query_scope=query_scope,
                require_user_evidence=require_user_evidence,
            )
        _t_cache_end = time.perf_counter()
        if session_cached is not None:
            # 会话缓存可能用不同 k 检索：先按 canonical 当前状态剔除失效项，
            # 再截断到请求的 k 值，避免失效项占用返回名额。
            revalidated = await self._revalidate_cached_results(
                session_cached,
                include_mark_write=include_mark_write,
                require_user_evidence=require_user_evidence,
                reference_time=effective_reference_time,
                chat_type=chat_type,
                query_scope=query_scope,
            )
            if revalidated is not None:
                truncated, stale_dropped = revalidated
                truncated = truncated[:k]
                # 仍更新 access time
                ids = [
                    r.doc_id
                    for r in truncated
                    if getattr(r, "doc_id", None) is not None
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
                    "dropped_stale_count": stale_dropped,
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
        # 读取窗口起始代际：发布到缓存前若有任何推进 revision 的写入使代际前进，
        # 本次候选集合与其派生注解就无法证明属于被固化的那个 revision。
        publish_generation = self._retrieval.cache_generation
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
                include_mark_write=include_mark_write,
                query_scope=query_scope,
                require_user_evidence=require_user_evidence,
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
        # mark_write 在截断与链式扩展之前过滤，避免占用 k 名额或充当扩展种子。
        results = filter_mark_write(results, include_mark_write=include_mark_write)
        if require_user_evidence:
            # 用户证据同样先于 boost 与 k 截断生效，混合候选不得先占槽位再被丢弃。
            results = _user_evidence_filter(results)
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
                    query_scope=query_scope,
                    require_user_evidence=require_user_evidence,
                )
                _t_chain = (time.perf_counter() - _t_chain_start) * 1000.0
                if chained:
                    results = chained[:k]
        if require_user_evidence:
            # 链式扩展可能引入无用户证据的候选，进入缓存与返回前再过滤一次。
            results = _user_evidence_filter(results)
        ids = [r.doc_id for r in results if getattr(r, "doc_id", None) is not None]
        if ids:
            self._create_tracked_task(
                self._maintenance.update_access_times_batch(ids, recall_type)
            )
        window_stable = self._retrieval.cache_generation == publish_generation
        if not window_stable:
            # R2.1：读取窗口内发生过推进 revision 的写入，本次候选与其派生注解
            # 无法证明属于同一 revision——不发布到缓存，也不让未证明的派生注解
            # 外流（命中时的 revision 相等门拦不住被补成新 revision 的旧结果）。
            logger.debug("[检索] 读取窗口内缓存已失效，跳过本次缓存写入")
            for item in results:
                metadata = getattr(item, "metadata", None)
                if isinstance(metadata, dict):
                    metadata.pop("derived_projections", None)
        elif not trace_requested:
            cached_payload = await self._snapshot_candidates_for_cache(results)
            self._retrieval.set_cached(cache_key, cached_payload)
            self._retrieval.set_session_cached(
                query,
                k,
                session_id,
                persona_id,
                cached_payload,
                user_id=user_id,
                chat_type=chat_type,
                memory_types=memory_types,
                query_intent=cache_intent,
                chain_depth=chain_depth,
                recall_strategy=recall_strategy,
                reference_time=requested_reference_time,
                include_mark_write=include_mark_write,
                query_scope=query_scope,
                require_user_evidence=require_user_evidence,
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
        return filter_mark_write(results, include_mark_write=include_mark_write)

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
        observed_revision = memory_revision(memory)
        if expected_revision is not None:
            if not observed_revision or observed_revision != str(expected_revision):
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
        if current_metadata.get("replacement_pending"):
            self._last_write_reason_code = "content_replace_pending"
            return False
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
                requested_metadata = updates.get("metadata")
                if isinstance(requested_metadata, dict):
                    guarded_metadata.update(requested_metadata)
                # R4.2：documents.text 是唯一权威事实文本，key_facts/fact_source_evidence
                # 只是同一表示的准入元数据，必须与即将落地的正文一致。判定复用
                # fact_text_alignment 的三态，不在此另写规范化：
                # - 调用方提供过任一事实字段：只按「本次提供的那对值」判定，不与
                #   旧表示混合（缺另一侧而借用旧值会得到「新事实 + 旧证据」）；
                #   不构成对齐表示即按提供分支 fail closed，整次写入拒绝并给稳定
                #   原因码，canonical 完全不变；
                # - 未提供且旧表示无法判定为对齐（含已与新正文矛盾）：按「无事实
                #   元数据」在同一事务内清理，避免留下「新正文 + 旧事实」。
                facts_provided = isinstance(requested_metadata, dict) and (
                    "key_facts" in requested_metadata
                    or "fact_source_evidence" in requested_metadata
                )
                cleared_keys: tuple[str, ...] = ()
                if facts_provided:
                    alignment = facts_aligned(
                        new_content,
                        requested_metadata.get("key_facts"),
                        requested_metadata.get("fact_source_evidence"),
                    )
                    if alignment is not FactTextAlignment.ALIGNED:
                        self._last_write_reason_code = "fact_evidence_mismatch"
                        logger.warning(
                            "[更新] 提供的事实元数据与正文不一致，拒绝覆盖 "
                            "reason_code=fact_evidence_mismatch"
                        )
                        return False
                else:
                    alignment = facts_aligned(
                        new_content,
                        guarded_metadata.get("key_facts"),
                        guarded_metadata.get("fact_source_evidence"),
                    )
                    if alignment is not FactTextAlignment.ALIGNED:
                        cleared_keys = tuple(
                            key
                            for key in ("key_facts", "fact_source_evidence")
                            if key in guarded_metadata
                        )
                        for key in cleared_keys:
                            guarded_metadata.pop(key)
                # canonical_summary 必须与即将落地的正文一致：正文已变化时，无论
                # 调用方显式提供还是从旧 metadata 继承，只要存在摘要字段就用新正文
                # 覆盖，绝不提交「新正文 + 旧摘要」。
                if "canonical_summary" in guarded_metadata:
                    guarded_metadata["canonical_summary"] = new_content
                guarded_metadata["updated_at"] = time.time()
                update_kwargs: dict[str, Any] = {}
                if cleared_keys:
                    # metadata 写入是键级合并：清除必须显式交给 canonical 层，
                    # 只在确有字段要清理时追加参数，保持既有调用形状。
                    update_kwargs["drop_metadata_keys"] = cleared_keys
                success = await self.hybrid_retriever.update_content_if_revision(
                    memory_id,
                    new_content,
                    guarded_metadata,
                    expected_revision,
                    **update_kwargs,
                )
                if not success:
                    self._last_write_reason_code = "source_revision_mismatch"
                    return False
                await self._invalidate_evolution_after_revision(memory_id)
                await self._schedule_evolution_after_write(memory_id)
                self._schedule_domain_proposals_after_write(memory_id)
                # canonical 已提交：按当前来源重派生 Atom 信号（旧事实不得继续
                # 进入前瞻注入/图派生），失败只降级原因码，不回滚正文。
                await self._rederive_atoms_after_write(memory_id)
                self._retrieval.invalidate_cache()
                if self.graph_memory_manager is not None and not skip_graph_reindex:
                    graph_op_id = await self._write_journal.start_op(
                        "graph_reindex",
                        {"memory_id": memory_id},
                        memory_id=memory_id,
                    )
                    try:
                        await self.graph_memory_manager.index_memory(
                            memory_id,
                            new_content,
                            guarded_metadata,
                        )
                        await self._write_journal.advance_op(
                            graph_op_id,
                            "graph_reindexed",
                            status="completed",
                            memory_id=memory_id,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        # canonical 已提交：图派生失败只登记修复账本，不回滚正文。
                        self._last_write_reason_code = "graph_reindex_failed"
                        await self._write_journal.advance_op(
                            graph_op_id,
                            "graph_reindex_failed",
                            status="needs_repair",
                            memory_id=memory_id,
                            error="graph_reindex_failed",
                        )
                        logger.error(
                            "[更新] 图派生刷新降级 reason_code=graph_reindex_failed"
                        )
                return True
            async with self._replacement_write_lock:
                return await self._replace_content(memory_id, updates)
        metadata_updates = {}
        if "importance" in updates:
            metadata_updates["importance"] = clamp_float(
                updates["importance"], default=0.5
            )
        if "metadata" in updates:
            metadata_updates.update(updates["metadata"])
        if metadata_updates:
            semantic_metadata_changed = prepare_semantic_metadata_update(
                current_metadata, metadata_updates, observed_at=time.time()
            )
            current_metadata.update(metadata_updates)
            current_metadata["updated_at"] = time.time()
            if self.hybrid_retriever is None:
                logger.error("混合检索器未初始化")
                return False
            update_kwargs: dict[str, Any] = {}
            if not semantic_metadata_changed:
                update_kwargs["advance_revision"] = False
            effective_revision = expected_revision or observed_revision
            if effective_revision:
                update_kwargs["expected_revision"] = effective_revision
            success = await self.hybrid_retriever.update_metadata(
                memory_id,
                metadata_updates,
                **update_kwargs,
            )
            if success:
                if semantic_metadata_changed:
                    await self._invalidate_evolution_after_revision(memory_id)
                    await self._schedule_evolution_after_write(memory_id)
                    self._schedule_domain_proposals_after_write(memory_id)
                    # 语义元数据（含事实表示）已提交：按当前来源重派生 Atom 信号，
                    # 失败只降级原因码，不回滚 canonical。
                    await self._rederive_atoms_after_write(memory_id)
                self._retrieval.invalidate_cache()
                if (
                    semantic_metadata_changed
                    and self.graph_memory_manager is not None
                    and not skip_graph_reindex
                ):
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
                    except Exception:
                        self._last_write_reason_code = "graph_reindex_failed"
                        await self._write_journal.advance_op(
                            op_id,
                            "graph_reindex_failed",
                            status="needs_repair",
                            memory_id=memory_id,
                            error="graph_reindex_failed",
                        )
                        logger.error(
                            "[更新] 图记忆重建降级 reason_code=graph_reindex_failed"
                        )
                        return False
            if not success:
                # 区分 CAS 冲突与存储失败：CAS 拒绝意味着并发写入已生效。
                retriever_reason = getattr(
                    self.hybrid_retriever, "_last_update_reason", None
                )
                if isinstance(retriever_reason, str) and retriever_reason:
                    self._last_write_reason_code = retriever_reason
                else:
                    self._last_write_reason_code = (
                        "source_revision_mismatch"
                        if effective_revision
                        else "metadata_update_failed"
                    )
            return success
        return True

    async def _rederive_atoms_after_write(self, memory_id: int) -> None:
        """canonical 提交后按当前来源重派生 Atom 信号。

        Atom 是 canonical 的派生信号：正文或事实元数据变化后必须整体替换该父
        来源的 Atom 行，否则旧事实会继续进入前瞻注入与图派生。重派生失败只降级
        为 ``atom_rederive_failed`` 原因码并保持已经提交的 canonical，绝不回滚；
        派生面可收敛（``rebuild_stages(["atoms"])`` 会重派生全部来源）。
        ``asyncio.CancelledError`` 继续传播。
        """

        manager = getattr(self, "atom_lifecycle_manager", None)
        rederive = getattr(manager, "rederive_for_sources", None)
        if not callable(rederive):
            return
        try:
            result = rederive([memory_id], "canonical_content_update")
            if inspect.isawaitable(result):
                await cast(Awaitable[Any], result)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._last_write_reason_code = "atom_rederive_failed"
            logger.error("[更新] Atom 重派生降级 reason_code=atom_rederive_failed")

    async def _replace_content(self, memory_id: int, updates: dict[str, Any]) -> bool:
        """暂存新行后原子切换可召回 owner；删除和补偿均以 canonical 回读为准。"""

        journal = self._write_journal
        source = await self.get_memory(memory_id)
        if source is None or self.db_connection is None:
            self._last_write_reason_code = "source_not_found"
            return False
        cursor = await self.db_connection.execute(
            "SELECT 1 FROM memory_write_ops WHERE op_type='replace_content' "
            "AND json_extract(payload, '$.old_id')=? "
            "AND status IN ('pending', 'needs_repair') LIMIT 1",
            (memory_id,),
        )
        pending = await cursor.fetchone()
        await cursor.close()
        if pending:
            self._last_write_reason_code = "content_replace_pending"
            return False
        metadata = source.get("metadata") or {}
        metadata = json.loads(metadata) if isinstance(metadata, str) else dict(metadata)
        requested_metadata = updates.get("metadata")
        requested_metadata = (
            requested_metadata if isinstance(requested_metadata, dict) else {}
        )
        facts_provided = (
            "key_facts" in requested_metadata
            or "fact_source_evidence" in requested_metadata
        )
        candidate_facts = requested_metadata.get("key_facts")
        candidate_evidence = requested_metadata.get("fact_source_evidence")
        if facts_provided and (
            facts_aligned(updates["content"], candidate_facts, candidate_evidence)
            is not FactTextAlignment.ALIGNED
        ):
            self._last_write_reason_code = "fact_evidence_mismatch"
            return False
        new_metadata = dict(metadata)
        new_metadata.update(requested_metadata)
        if not facts_provided:
            for key in ("key_facts", "fact_source_evidence", "canonical_summary"):
                new_metadata.pop(key, None)
        if (
            facts_provided
            or "canonical_summary" in metadata
            or "canonical_summary" in requested_metadata
        ):
            new_metadata["canonical_summary"] = updates["content"]
        if "importance" in updates:
            new_metadata["importance"] = clamp_float(updates["importance"], default=0.5)
        status_fields = ("memory_status", "status", "status_changed_at")
        payload = {
            "old_id": memory_id,
            "content_digest": ledger_content_digest(updates["content"]),
            "content_preview": updates["content"][:CONTENT_PREVIEW_LIMIT],
            "previous_status": {
                key: metadata[key] for key in status_fields if key in metadata
            },
            "replacement_status": {
                key: new_metadata[key] for key in status_fields if key in new_metadata
            },
        }
        op_id = await journal.start_op("replace_content", payload, memory_id=memory_id)
        if op_id is None:
            self._last_write_reason_code = "content_replace_journal_unavailable"
            return False
        # 新 owner 不继承旧请求幂等键，否则 add 会复用并随后删除唯一旧行。
        new_metadata.pop("merged_idempotency_keys", None)
        new_metadata.update(
            idempotency_key=f"replace:{op_id}",
            previous_id=memory_id,
            updated_at=time.time(),
            memory_status="deleted",
            status="deleted",
            replacement_pending=True,
        )
        try:
            new_id = await self.add_memory(
                updates["content"],
                session_id=new_metadata.get("session_id"),
                persona_id=new_metadata.get("persona_id"),
                importance=clamp_float(new_metadata.get("importance"), default=0.5),
                metadata=new_metadata,
            )
            if (
                not isinstance(new_id, int)
                or isinstance(new_id, bool)
                or new_id <= 0
                or new_id == memory_id
            ):
                raise ValueError("replacement_id_conflict")
            payload["new_id"] = new_id
            await journal.advance_op(
                op_id,
                "replacement_created",
                memory_id=new_id,
                payload_patch={"new_id": new_id},
            )
            if not await journal._switch_replacement_visibility(
                memory_id, new_id, payload, new_id
            ):
                raise RuntimeError("content_replace_publish_failed")
            deleted = await self.delete_memory(memory_id)
            if not await journal._canonical_document_deleted(memory_id):
                if not await journal._switch_replacement_visibility(
                    memory_id, new_id, payload, memory_id
                ):
                    raise RuntimeError("content_replace_restore_failed")
                compensated = await self.delete_memory(new_id)
                rolled_back = compensated and await journal._canonical_document_deleted(
                    new_id
                )
                await journal.advance_op(
                    op_id,
                    "replacement_rolled_back"
                    if rolled_back
                    else "replacement_rollback_failed",
                    status="failed" if rolled_back else "needs_repair",
                    memory_id=memory_id,
                    error="content_replace_failed"
                    if rolled_back
                    else "content_replace_compensation_failed",
                )
                self._last_write_reason_code = "content_replace_failed"
                return False
            # 宿主可能已删 canonical 才在向量保存时报错；不可补偿删除唯一的新 owner。
            await journal.advance_op(
                op_id,
                "replacement_committed" if deleted else "replacement_cleanup_pending",
                status="completed" if deleted else "needs_repair",
                memory_id=new_id,
                error=None if deleted else "content_replace_cleanup_failed",
            )
            try:
                await self._finalize_summary_source_write(new_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error(
                    "[更新] 替换后派生降级 reason_code=content_replace_derivation_failed"
                )
            self._retrieval.invalidate_cache()
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            await journal.advance_op(
                op_id,
                "replacement_aborted",
                status="needs_repair",
                error="content_replace_failed",
            )
            self._last_write_reason_code = "content_replace_failed"
            logger.error("[更新] 替换未完成 reason_code=content_replace_failed")
            return False

    async def reinforce_recall_state(self, memory_id: int) -> bool:
        """按最新运行态推进强化计数与 TTL，且不推进 source revision。

        该入口只服务内部测试效应：增量在读取到当前 canonical 后计算，并以该
        revision 作为 CAS 前提写入白名单字段，语义更新或并发编辑会使其失败。
        """

        return await self._persist_runtime_maintenance(
            memory_id,
            apply_reinforcement_maintenance_delta,
        )

    async def apply_interference_decay(
        self, memory_id: int, *, source_memory_id: int
    ) -> bool:
        """按最新运行态衰减被干扰记忆的重要性，且不推进 source revision。"""

        def _delta(metadata: dict[str, Any]) -> dict[str, Any]:
            return apply_interference_maintenance_delta(
                metadata,
                source_memory_id=source_memory_id,
            )

        return await self._persist_runtime_maintenance(memory_id, _delta)

    async def _persist_runtime_maintenance(
        self,
        memory_id: int,
        delta_builder: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> bool:
        """以当前 canonical revision 为 CAS 前提合并运行态维护增量。"""

        self._last_write_reason_code = None
        memory = await self.get_memory(memory_id)
        if not isinstance(memory, dict):
            self._last_write_reason_code = "source_not_found"
            return False
        current_revision = memory_revision(memory)
        if not current_revision:
            self._last_write_reason_code = "source_revision_missing"
            return False
        metadata: Any = memory.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        delta = delta_builder(metadata)
        if not is_runtime_maintenance_delta(metadata, delta):
            self._last_write_reason_code = "runtime_maintenance_field_rejected"
            logger.error(
                f"[更新] 运行态维护增量不在白名单内，拒绝写入 (memory_id={memory_id})"
            )
            return False
        if not delta:
            return True
        if self.hybrid_retriever is None:
            self._last_write_reason_code = "not_initialized"
            return False
        try:
            success = await self.hybrid_retriever.update_metadata(
                memory_id,
                delta,
                expected_revision=current_revision,
                advance_revision=False,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                f"[更新] 运行态维护写入失败 (memory_id={memory_id})", exc_info=True
            )
            self._last_write_reason_code = "runtime_maintenance_failed"
            return False
        if not success:
            # CAS 失败说明并发发生了语义更新或该行已不可写。
            self._last_write_reason_code = "source_revision_mismatch"
            return False
        self._retrieval.invalidate_cache()
        return True

    async def delete_memory(self, memory_id: int) -> bool:
        """删除 canonical memory，并清理或失效关联派生对象。

        R2.2：无论成功、提前失败、异常还是取消，返回或抛出控制权之前都必须
        失效检索缓存——已删除的正文不得继续从缓存命中返回。
        """

        try:
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
            return success
        finally:
            self._retrieval.invalidate_cache()

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
