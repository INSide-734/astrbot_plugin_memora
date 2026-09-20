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

# canonical 可读性是所有派生阶段的前置；其余阶段始终按下列相对次序串行执行。
_REBUILD_STAGE_ORDER: tuple[str, ...] = (
    "canonical",
    "indexes",
    "catalog",
    "atoms",
    "graph",
    "evolution",
    "semantic_compression",
    "notes",
)
_DERIVED_REBUILD_STAGES: tuple[str, ...] = _REBUILD_STAGE_ORDER[1:]

# atoms 阶段按 canonical 分页重派生的批次大小；与 graph 阶段保持同量级。
_ATOMS_REBUILD_BATCH_SIZE = 200

# 阶段名到「实现方法名、失败原因码」的固定映射。
_REBUILD_STAGE_OPERATIONS: dict[str, tuple[str, str]] = {
    "indexes": ("_rebuild_indexes", "index_rebuild_failed"),
    "catalog": ("_rebuild_catalog", "catalog_rebuild_failed"),
    "atoms": ("_rebuild_atoms", "atoms_rebuild_partial_failed"),
    "graph": ("_rebuild_graph", "graph_rebuild_failed"),
    "evolution": ("_rebuild_evolution", "derived_rebuild_failed"),
    "semantic_compression": (
        "_rebuild_semantic_compression",
        "semantic_compression_rebuild_failed",
    ),
    "notes": ("_rebuild_notes", "note_rebuild_failed"),
}


def _skipped_stage(reason_code: str) -> dict[str, Any]:
    """构造与既有阶段字段兼容的跳过结果。"""

    return {
        "status": "skipped",
        "success": True,
        "reason_code": reason_code,
        "duration_seconds": 0.0,
    }


def _safe_stage_count(value: Any) -> int:
    """把阶段计数规范化为非负整数；布尔值与非整数按 0 处理。"""

    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _normalize_stage_plan(stages: list[str]) -> list[str] | None:
    """把请求的阶段去重并归一到固定顺序；含未知名称时返回 ``None``。"""

    requested: set[str] = set()
    for stage in stages:
        name = str(stage).strip()
        if name == "canonical":
            # canonical 前置校验始终执行，显式请求按幂等处理。
            continue
        if name not in _REBUILD_STAGE_OPERATIONS:
            return None
        requested.add(name)
    return [name for name in _DERIVED_REBUILD_STAGES if name in requested]


def _unknown_stage_report() -> dict[str, Any]:
    """未知阶段名的稳定降级结果：不读取 canonical，也不执行任何阶段。"""

    return {
        "success": False,
        "degraded": True,
        "reason_code": "rebuild_stage_unknown",
        "canonical": {
            "status": "skipped",
            "success": True,
            "documents": 0,
            "reason_code": "rebuild_stage_unknown",
        },
        "stages": {},
        "errors": 1,
    }


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
        # 维护入口登记：命令与 Page API 只持有验证器/初始化器，这里把自身挂到同一
        # 组合持有的验证器上，transport 按端口名 derived_rebuild_coordinator 解析。
        setattr(index_validator, "derived_rebuild_coordinator", self)

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
        """按 canonical、FTS/向量、catalog、atoms、graph、evolution 顺序重建。"""

        pending_trigger = getattr(self, "_pending_rebuild_trigger_reason", None)
        setattr(self, "_pending_rebuild_trigger_reason", None)
        return await self.rebuild_stages(
            list(_DERIVED_REBUILD_STAGES),
            trigger_reason=trigger_reason
            or pending_trigger
            or ("indexes_inconsistent" if rebuild_indexes else "catalog_dirty"),
            rebuild_indexes=rebuild_indexes,
        )

    async def rebuild_stages(
        self,
        stages: list[str],
        *,
        trigger_reason: str | None = None,
        rebuild_indexes: bool = True,
    ) -> dict[str, Any]:
        """按阶段子集执行固定顺序的派生重建，返回同一份重建报告。

        允许只请求子集（例如 ``["indexes"]``、``["graph"]``）；执行次序始终服从
        canonical → indexes → catalog → atoms → graph → evolution →
        semantic_compression → notes 的相对顺序。canonical 可读性是所有派生阶段的
        前置，无论是否显式请求都会先校验。``rebuild_indexes=False`` 保留既有兼容
        语义：indexes 记为 skipped/indexes_consistent。未知阶段名不执行任何阶段，
        直接返回 ``reason_code=rebuild_stage_unknown`` 的降级结果；普通阶段失败只
        降级后续阶段，``asyncio.CancelledError`` 继续传播。
        """

        plan = _normalize_stage_plan(stages)
        async with self._lock:
            started = time.perf_counter()
            measurement = None
            try:
                with rebuild_measurement_scope(
                    normalize_rebuild_trigger(trigger_reason)
                ) as active_measurement:
                    measurement = active_measurement
                    if plan is None:
                        result = _unknown_stage_report()
                        measurement.record_stage(
                            "rebuild", 0.0, result, status="failed"
                        )
                    else:
                        result = await self._execute_stage_plan(
                            plan, rebuild_indexes=rebuild_indexes
                        )
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

    @staticmethod
    def stage_result(report: Any, stage: str) -> Any:
        """从统一重建报告中取出指定阶段结果；报告缺失该阶段时原样返回。

        命令与 Page API 依赖既有单阶段结果字段，用本投影保持返回结构兼容。
        """

        if isinstance(report, dict):
            stages = report.get("stages")
            if isinstance(stages, dict) and isinstance(stages.get(stage), dict):
                return stages[stage]
        return report

    async def _execute_stage_plan(
        self, stages: list[str], *, rebuild_indexes: bool
    ) -> dict[str, Any]:
        """执行 canonical-first 的阶段计划，保持既有阶段失败与降级语义。"""

        measurement = current_rebuild_measurement()
        if measurement is None:
            raise RuntimeError("rebuild_measurement_missing")
        results: dict[str, dict[str, Any]] = {}
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

        for name in stages:
            if name == "indexes" and not rebuild_indexes:
                results[name] = _skipped_stage("indexes_consistent")
                measurement.record_stage(name, 0.0, results[name], status="skipped")
                continue
            operation_name, failure_reason = _REBUILD_STAGE_OPERATIONS[name]
            operation = cast(
                Callable[[], Awaitable[dict[str, Any]]],
                getattr(self, operation_name, None),
            )
            if not callable(operation):
                # 未接入实现的阶段只进报告，不写测量：阶段名是固定闭集，
                # 未注册的占位不得污染观测。
                logger.debug(
                    "派生重建阶段未注册，reason_code=%s_rebuild_unavailable", name
                )
                results[name] = _skipped_stage(f"{name}_rebuild_unavailable")
                continue
            results[name] = await self._run_stage(
                name, operation, failure_reason=failure_reason
            )

        failed_stages = [
            name for name, stage in results.items() if stage.get("status") == "failed"
        ]
        success = not failed_stages
        return {
            "success": success,
            "degraded": not success,
            "reason_code": (
                "derived_rebuild_completed"
                if success
                else str(results[failed_stages[0]].get("reason_code"))
            ),
            "canonical": canonical,
            "stages": results,
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

    async def _rebuild_atoms(self) -> dict[str, Any]:
        """按 canonical 分页重派生 Atom，并清除父不存在或事实失效的残留行。

        每个 200 条来源批次交给 ``AtomLifecycleManager.rederive_for_sources``
        按当前 canonical 重新分类并替换 Atom 行；不可召回来源的 Atom 行一并
        清除。批次或单来源失败只降级计数（``atoms_rebuild_partial_failed``），
        不阻断 canonical；原子组件未装配时按跳过报告；
        ``asyncio.CancelledError`` 继续传播。
        """

        store = getattr(self.memory_engine, "atom_store", None)
        manager = getattr(self.memory_engine, "atom_lifecycle_manager", None)
        rederive = getattr(manager, "rederive_for_sources", None)
        if store is None or not callable(rederive):
            return _skipped_stage("atoms_rebuild_unavailable")
        storage = getattr(
            getattr(self.memory_engine, "faiss_db", None),
            "document_storage",
            None,
        )
        get_documents = getattr(storage, "get_documents", None)
        count_documents = getattr(storage, "count_documents", None)
        if not callable(get_documents) or not callable(count_documents):
            return {
                "status": "failed",
                "success": False,
                "reason_code": "atoms_rebuild_unavailable",
            }

        total = max(0, int(await count_documents(metadata_filters={}) or 0))
        rebuilt = 0
        purged = 0
        skipped = 0
        failed = 0
        canonical_ids: set[int] = set()
        offset = 0
        while offset < total:
            docs = await get_documents(
                metadata_filters={},
                limit=_ATOMS_REBUILD_BATCH_SIZE,
                offset=offset,
            )
            if not docs:
                break
            batch: list[int] = []
            for doc in docs:
                try:
                    memory_id = int(doc["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                canonical_ids.add(memory_id)
                batch.append(memory_id)
            for index in range(0, len(batch), _ATOMS_REBUILD_BATCH_SIZE):
                report = await self._rederive_atoms_batch(
                    rederive,
                    batch[index : index + _ATOMS_REBUILD_BATCH_SIZE],
                )
                rebuilt += report["rederived"]
                purged += report["purged"]
                skipped += report["skipped"]
                failed += report["failed"]
            offset += len(docs)

        (
            residue_cleaned,
            residue_failed,
            residue_reason,
        ) = await self._cleanup_atom_residue(store, canonical_ids)
        result = {
            "rebuilt": rebuilt,
            "purged": purged,
            "skipped": skipped,
            "failed": failed,
            "residue_cleaned": residue_cleaned,
            "residue_failed": residue_failed,
            "total": rebuilt + purged + skipped + failed,
        }
        if residue_reason is not None:
            result["residue_reason_code"] = residue_reason
        if failed or residue_failed:
            return {
                **result,
                "status": "failed",
                "success": False,
                "reason_code": "atoms_rebuild_partial_failed",
            }
        return result

    async def _rederive_atoms_batch(
        self,
        rederive: Callable[..., Any],
        memory_ids: list[int],
    ) -> dict[str, int]:
        """执行一批来源的 Atom 重派生；批次异常只降级为整批失败计数。"""

        failed_batch = {
            "rederived": 0,
            "purged": 0,
            "skipped": 0,
            "failed": len(memory_ids),
        }
        try:
            result = rederive(list(memory_ids), "rebuild_atoms")
            if inspect.isawaitable(result):
                result = await cast(Awaitable[Any], result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Atom 重建批次失败，reason_code=atom_rederive_failed")
            return failed_batch
        if not isinstance(result, dict):
            return failed_batch
        return {
            "rederived": _safe_stage_count(result.get("rederived")),
            "purged": _safe_stage_count(result.get("purged")),
            "skipped": _safe_stage_count(result.get("skipped")),
            "failed": _safe_stage_count(result.get("failed")),
        }

    async def _cleanup_atom_residue(
        self,
        store: Any,
        canonical_ids: set[int],
    ) -> tuple[int, int, str | None]:
        """删除父 canonical 已不存在的 Atom 行；枚举或删除失败只降级计数。

        返回 ``(已清理数, 失败计数, 稳定原因码)``，成功时原因码为 ``None``。
        残留父来源枚举失败时残留规模未知，按「至少一项残留处理失败」计 1 并给出
        ``atom_residue_scan_failed``；枚举不到父来源不等于清理成功，阶段必须据此降级。
        """

        lister = getattr(store, "list_parent_ids", None)
        deleter = getattr(store, "batch_delete_by_parent", None)
        if not callable(lister) or not callable(deleter):
            return 0, 0, None
        list_parents = cast(Callable[[], Awaitable[Any]], lister)
        delete_parents = cast(Callable[[list[int]], Awaitable[Any]], deleter)
        try:
            parent_ids = await list_parents()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Atom 残留父来源枚举失败，reason_code=atom_residue_scan_failed"
            )
            return 0, 1, "atom_residue_scan_failed"
        orphan_ids = sorted(
            {int(parent_id) for parent_id in parent_ids or ()} - canonical_ids
        )
        if not orphan_ids:
            return 0, 0, None
        try:
            deleted = await delete_parents(orphan_ids)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Atom 残留清理失败，reason_code=atom_residue_cleanup_failed")
            return 0, len(orphan_ids), "atom_residue_cleanup_failed"
        return _safe_stage_count(deleted), 0, None

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
        try:
            residue_failed = max(0, int(result.get("residue_failed", 0) or 0))
        except (TypeError, ValueError):
            residue_failed = 0
        if failed or residue_failed:
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
