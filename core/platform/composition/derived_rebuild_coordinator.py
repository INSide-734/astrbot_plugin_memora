"""统一协调 canonical 派生索引的安全重建顺序。"""

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

from astrbot.api import logger

from ...features.memory.rebuild_metrics import record_rebuild_metrics
from ...features.memory.rebuild_observability import (
    current_rebuild_measurement,
    finalize_rebuild_observability,
    normalize_rebuild_trigger,
    rebuild_measurement_scope,
)
from .derived_rebuild_catalog import DerivedRebuildCatalogMixin


class DerivedRebuildCoordinator(DerivedRebuildCatalogMixin):
    """按固定顺序重建所有可丢弃派生数据。

    该协调器只持有已经装配好的组件，不创建新的 canonical 存储，也不把
    relation/projection 当作同步写入的权威结果。每个阶段独立记录状态；后续
    派生阶段失败时仍保留 canonical 和已成功切换的索引。
    """

    def __init__(
        self,
        index_validator: Any,
        memory_engine: Any,
        evolution_manager: Any | None = None,
        catalog_store: Any | None = None,
    ) -> None:
        """保存重建所需组件并初始化串行锁。

        参数:
            index_validator: 提供 FTS5/BM25 与 FAISS 重建的验证器。
            memory_engine: 持有 canonical 文档存储和图重建入口的记忆引擎。
            evolution_manager: 可选的 relation/projection 重建协调器；为空时
                从 ``memory_engine.memory_evolution_manager`` 读取。
            catalog_store: canonical SQLite 上的 topic 派生目录；为空时跳过目录阶段。
        """

        self.index_validator = index_validator
        self.memory_engine = memory_engine
        self.evolution_manager = evolution_manager
        self.catalog_store = (
            catalog_store
            if catalog_store is not None
            else vars(memory_engine).get("topic_catalog_store")
        )
        self._lock = asyncio.Lock()

    async def catalog_needs_reconcile(self) -> bool:
        """判断 topic catalog 是否需要独立启动回填或 dirty 收敛。"""

        catalog = self.catalog_store
        if catalog is None:
            return False
        state = await catalog.get_state()
        if (
            state.get("status") != "ready"
            or not state.get("active_generation")
            or state.get("staging_generation") is not None
            or int(state.get("canonical_write_watermark") or 0)
            != int(state.get("published_dirty_watermark") or 0)
        ):
            return True
        dirty = await catalog.list_dirty(
            states=("pending", "running", "failed"), limit=1
        )
        if dirty:
            return True
        verify = getattr(catalog, "verify_published_generation", None)
        if not callable(verify):
            return True
        verify_generation = cast(Callable[[int], Awaitable[bool]], verify)
        return not await verify_generation(int(state["active_generation"]))

    async def catalog_readiness_decision(self) -> dict[str, Any]:
        """返回目录可用于总结调度的明确 ready/degraded 决策。"""

        if self.catalog_store is None:
            return {
                "catalog_decision": "degraded",
                "safe_baseline": True,
                "reason_code": "catalog_unavailable",
            }
        try:
            if not await self.catalog_needs_reconcile():
                return {
                    "catalog_decision": "ready",
                    "safe_baseline": True,
                    "reason_code": "catalog_ready",
                }
            state = await self._catalog_state_snapshot()
            if state is not None:
                safe_active = await self._safe_active_catalog_state(state)
                if safe_active is not None:
                    return {
                        "catalog_decision": "ready",
                        "safe_baseline": True,
                        "reason_code": "catalog_ready_pending_reconcile",
                    }
                if (
                    state.get("status") == "degraded"
                    and state.get("staging_generation") is None
                ):
                    return {
                        "catalog_decision": "degraded",
                        "safe_baseline": True,
                        "reason_code": str(
                            state.get("reason_code") or "catalog_degraded"
                        ),
                    }
                if state.get("staging_generation") is not None:
                    return {
                        "catalog_decision": "degraded",
                        "safe_baseline": True,
                        "reason_code": "catalog_rebuild_in_progress",
                    }
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        if await self._mark_catalog_degraded("catalog_not_ready"):
            return {
                "catalog_decision": "degraded",
                "safe_baseline": True,
                "reason_code": "catalog_not_ready",
            }
        return {
            "catalog_decision": "degraded",
            "safe_baseline": True,
            "reason_code": "catalog_not_ready",
        }

    async def _catalog_state_snapshot(self) -> dict[str, Any] | None:
        """读取目录状态；普通失败由调用方转为显式降级。"""

        get_state = getattr(self.catalog_store, "get_state", None)
        if not callable(get_state):
            return None
        try:
            state = get_state()
            if inspect.isawaitable(state):
                state = await state
            return state if isinstance(state, dict) else None
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    async def _mark_catalog_degraded(self, reason_code: str) -> bool:
        """调用目录 owner 的原子降级入口并继续传播取消。"""

        mark_degraded = getattr(self.catalog_store, "mark_degraded", None)
        if not callable(mark_degraded):
            return False
        try:
            result = mark_degraded(reason_code)
            return bool(await result) if inspect.isawaitable(result) else False
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("话题目录降级失败")
            return False

    async def reconcile_catalog_for_shutdown(
        self,
        *,
        dirty_limit: int = 25,
    ) -> dict[str, Any]:
        """在 canonical SQLite 仍可用时有界收敛目录 dirty 与回填。"""

        catalog = self.catalog_store
        if catalog is None:
            return {
                "success": True,
                "status": "skipped",
                "reason_code": "catalog_unavailable",
            }
        if dirty_limit <= 0:
            return {
                "success": False,
                "reason_code": "catalog_shutdown_invalid",
            }
        async with self._lock:
            repaired = 0
            repair = getattr(catalog, "repair_pending", None)
            if callable(repair):
                try:
                    repaired_result = repair(
                        f"shutdown-catalog-{id(self)}",
                        limit=dirty_limit,
                    )
                    if inspect.isawaitable(repaired_result):
                        repaired_result = await repaired_result
                    repaired = max(0, int(repaired_result or 0))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("关停前话题目录 dirty 修复失败")

            result = await self._rebuild_catalog()
            if not isinstance(result, dict):
                raise RuntimeError("catalog_shutdown_reconcile_failed")
            result = dict(result)
            result["repaired_dirty"] = repaired
            if result.get("success") is not True:
                logger.warning("关停前话题目录回填未收敛")
                raise RuntimeError("catalog_shutdown_reconcile_failed")
            return result

    async def rebuild_all(
        self,
        *,
        rebuild_indexes: bool = True,
        trigger_reason: str | None = None,
    ) -> dict[str, Any]:
        """按 canonical、FTS/向量、catalog、graph、evolution 顺序重建。"""

        async with self._lock:
            pending_trigger = getattr(self, "_pending_rebuild_trigger_reason", None)
            trigger = normalize_rebuild_trigger(
                trigger_reason
                or pending_trigger
                or ("indexes_inconsistent" if rebuild_indexes else "catalog_dirty")
            )
            setattr(self, "_pending_rebuild_trigger_reason", None)
            started = time.perf_counter()
            measurement = None
            try:
                with rebuild_measurement_scope(trigger) as active_measurement:
                    measurement = active_measurement
                    result = await self._rebuild_stages(rebuild_indexes)
                    output = finalize_rebuild_observability(
                        result,
                        active_measurement,
                        duration_seconds=time.perf_counter() - started,
                    )
                    self._publish_rebuild_observability(output)
                    return output
            except asyncio.CancelledError:
                if measurement is not None:
                    measurement.record_stage(
                        "rebuild",
                        time.perf_counter() - started,
                        status="cancelled",
                    )
                    cancelled = finalize_rebuild_observability(
                        {
                            "success": False,
                            "degraded": True,
                            "reason_code": "rebuild_cancelled",
                            "stages": {},
                            "errors": 1,
                        },
                        measurement,
                        duration_seconds=time.perf_counter() - started,
                    )
                    self._publish_rebuild_observability(cancelled)
                raise

    async def _rebuild_stages(self, rebuild_indexes: bool) -> dict[str, Any]:
        """执行 canonical-first 的固定派生阶段序列。"""

        measurement = current_rebuild_measurement()
        if measurement is None:
            raise RuntimeError("rebuild_measurement_missing")
        stages: dict[str, dict[str, Any]] = {}
        canonical_started = time.perf_counter()
        try:
            canonical = await self._verify_canonical()
        except asyncio.CancelledError:
            measurement.record_stage(
                "canonical",
                time.perf_counter() - canonical_started,
                status="cancelled",
            )
            raise
        canonical_elapsed = time.perf_counter() - canonical_started
        canonical = dict(canonical)
        canonical["duration_seconds"] = max(0.0, canonical_elapsed)
        measurement.record_stage(
            "canonical",
            canonical_elapsed,
            canonical,
            status=("completed" if canonical.get("success") else "failed"),
        )
        if not canonical["success"]:
            return {
                "success": False,
                "degraded": True,
                "reason_code": canonical["reason_code"],
                "canonical": canonical,
                "stages": {},
                "errors": 1,
            }

        if rebuild_indexes:
            stages["indexes"] = await self._run_stage(
                "indexes", self._rebuild_indexes, failure_reason="index_rebuild_failed"
            )
        else:
            stages["indexes"] = {
                "status": "skipped",
                "success": True,
                "reason_code": "indexes_consistent",
                "duration_seconds": 0.0,
            }
            measurement.record_stage(
                "indexes", 0.0, stages["indexes"], status="skipped"
            )
        stages["catalog"] = await self._run_stage(
            "catalog", self._rebuild_catalog, failure_reason="catalog_rebuild_failed"
        )
        stages["graph"] = await self._run_stage(
            "graph", self._rebuild_graph, failure_reason="graph_rebuild_failed"
        )
        stages["evolution"] = await self._run_stage(
            "evolution",
            self._rebuild_evolution,
            failure_reason="derived_rebuild_failed",
        )
        stages["semantic_compression"] = await self._run_stage(
            "semantic_compression",
            self._rebuild_semantic_compression,
            failure_reason="semantic_compression_rebuild_failed",
        )
        stages["notes"] = await self._run_stage(
            "notes", self._rebuild_notes, failure_reason="note_rebuild_failed"
        )
        failed_stages = [
            name for name, stage in stages.items() if stage.get("status") == "failed"
        ]
        success = not failed_stages
        return {
            "success": success,
            "degraded": not success,
            "reason_code": (
                "derived_rebuild_completed"
                if success
                else str(stages[failed_stages[0]].get("reason_code"))
            ),
            "canonical": canonical,
            "stages": stages,
            "errors": len(failed_stages),
        }

    def _publish_rebuild_observability(self, result: dict[str, Any]) -> None:
        """发布到既有 validator 快照并投影固定 Prometheus 指标。"""

        snapshot = result.get("observability")
        if not isinstance(snapshot, dict):
            return
        setattr(self.index_validator, "_observability", snapshot)
        record_rebuild_metrics(snapshot)

    async def _verify_canonical(self) -> dict[str, Any]:
        """只读确认 canonical 文档可访问，并返回安全计数。"""

        try:
            count_loader = getattr(self.index_validator, "_get_document_count", None)
            if callable(count_loader):
                load_count = cast(Callable[[], Awaitable[int]], count_loader)
                count = await load_count()
            else:
                storage = getattr(self.memory_engine, "faiss_db", None)
                storage = getattr(storage, "document_storage", None)
                count_loader = getattr(storage, "count_documents", None)
                if not callable(count_loader):
                    raise RuntimeError("canonical_count_unavailable")
                load_count = cast(Callable[..., Awaitable[int]], count_loader)
                count = await load_count(metadata_filters={})
            return {
                "status": "verified",
                "success": True,
                "documents": max(0, int(count)),
                "reason_code": "canonical_verified",
            }
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                "重建前读取 canonical 计数失败，reason_code=canonical_unavailable"
            )
            return {
                "status": "failed",
                "success": False,
                "documents": 0,
                "reason_code": "canonical_unavailable",
            }

    async def _run_stage(
        self,
        name: str,
        operation: Callable[[], Awaitable[dict[str, Any]]],
        *,
        failure_reason: str,
    ) -> dict[str, Any]:
        """执行派生阶段并记录耗时，普通异常转换为稳定降级结果。"""

        started = time.perf_counter()
        try:
            result = await operation()
            if not isinstance(result, dict):
                result = {
                    "status": "failed",
                    "success": False,
                    "reason_code": failure_reason,
                }
            elif result.get("status") == "skipped":
                result = dict(result)
            elif result.get("status") == "failed":
                result = {
                    **result,
                    "status": "failed",
                    "success": False,
                    "reason_code": failure_reason,
                }
            elif result.get("success", True):
                result = {"status": "completed", **result}
            else:
                result = {
                    **result,
                    "status": "failed",
                    "success": False,
                    "reason_code": failure_reason,
                }
        except asyncio.CancelledError:
            measurement = current_rebuild_measurement()
            if measurement is not None:
                measurement.record_stage(
                    name,
                    time.perf_counter() - started,
                    status="cancelled",
                )
            raise
        except Exception:
            logger.error("派生重建阶段失败：%s，reason_code=%s", name, failure_reason)
            result = {
                "status": "failed",
                "success": False,
                "reason_code": failure_reason,
            }

        measurement = current_rebuild_measurement()
        if measurement is not None:
            measurement.record_stage(
                name,
                time.perf_counter() - started,
                result,
                status=str(result.get("status") or "completed"),
            )
        return result

    async def _rebuild_indexes(self) -> dict[str, Any]:
        """调用现有 IndexValidator 重建 FTS5/BM25 和 FAISS。"""

        rebuild = getattr(self.index_validator, "rebuild_indexes", None)
        if not callable(rebuild):
            return {
                "status": "failed",
                "success": False,
                "reason_code": "index_rebuild_unavailable",
            }
        rebuild_indexes = cast(
            Callable[[Any], Awaitable[dict[str, Any]]],
            rebuild,
        )
        result = await rebuild_indexes(self.memory_engine)
        if not isinstance(result, dict):
            return {
                "status": "failed",
                "success": False,
                "reason_code": "index_rebuild_failed",
            }
        return result

    async def _rebuild_graph(self) -> dict[str, Any]:
        """调用现有图记忆入口重建图条目与原子派生数据。"""

        rebuild = getattr(self.memory_engine, "rebuild_graph_index", None)
        if not callable(rebuild):
            return {
                "status": "skipped",
                "success": True,
                "reason_code": "graph_rebuild_unavailable",
            }
        rebuild_graph = cast(Callable[[], Awaitable[dict[str, Any]]], rebuild)
        result = await rebuild_graph()
        if not isinstance(result, dict):
            return {"status": "completed", "success": True}
        result = dict(result)
        try:
            failed = max(0, int(result.get("failed", 0) or 0))
        except (TypeError, ValueError):
            failed = 0
        if failed:
            return {
                **result,
                "status": "failed",
                "success": False,
                "reason_code": "graph_rebuild_partial_failed",
            }
        return result

    async def _rebuild_evolution(self) -> dict[str, Any]:
        """失效旧 relation/projection 并重新排队当前 canonical revision。"""

        manager = self.evolution_manager or getattr(
            self.memory_engine, "memory_evolution_manager", None
        )
        if manager is None or getattr(manager, "mode", "disabled") == "disabled":
            return {
                "status": "skipped",
                "success": True,
                "reason_code": "evolution_disabled",
            }
        rebuild = getattr(manager, "rebuild_from_canonical", None)
        if not callable(rebuild):
            return {
                "status": "skipped",
                "success": True,
                "reason_code": "evolution_rebuild_unavailable",
            }
        rebuild_evolution = cast(Callable[[], Awaitable[dict[str, Any]]], rebuild)
        return await rebuild_evolution()

    async def _rebuild_notes(self) -> dict[str, Any]:
        """从 canonical source 幂等重建自动派生笔记。"""

        pipeline = getattr(self.memory_engine, "note_proposal_pipeline", None)
        rebuild = getattr(pipeline, "rebuild_from_canonical", None)
        if not callable(rebuild):
            return {
                "status": "skipped",
                "success": True,
                "reason_code": "note_rebuild_unavailable",
            }
        rebuild_notes = cast(Callable[[], Awaitable[dict[str, Any]]], rebuild)
        return await rebuild_notes()

    async def _rebuild_semantic_compression(self) -> dict[str, Any]:
        """从当前 canonical revision 幂等重建语义摘要 Projection。"""

        compressor = vars(self.memory_engine).get("semantic_compressor")
        rebuild = getattr(compressor, "rebuild_from_canonical", None)
        if not callable(rebuild):
            return {
                "status": "skipped",
                "success": True,
                "reason_code": "semantic_compression_disabled",
            }
        rebuild_compression = cast(
            Callable[[], Awaitable[dict[str, Any]]],
            rebuild,
        )
        return await rebuild_compression()


__all__ = ["DerivedRebuildCoordinator"]
