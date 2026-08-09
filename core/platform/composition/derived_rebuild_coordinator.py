"""统一协调 canonical 派生索引的安全重建顺序。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.api import logger


class DerivedRebuildCoordinator:
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
    ) -> None:
        """保存重建所需组件并初始化串行锁。

        参数:
            index_validator: 提供 FTS5/BM25 与 FAISS 重建的验证器。
            memory_engine: 持有 canonical 文档存储和图重建入口的记忆引擎。
            evolution_manager: 可选的 relation/projection 重建协调器；为空时
                从 ``memory_engine.memory_evolution_manager`` 读取。
        """

        self.index_validator = index_validator
        self.memory_engine = memory_engine
        self.evolution_manager = evolution_manager
        self._lock = asyncio.Lock()

    async def rebuild_all(self) -> dict[str, Any]:
        """按 canonical、FTS/向量、graph、evolution 顺序执行一次重建。

        返回:
            只包含计数、状态和稳定 reason code 的结果字典。canonical 阶段
            失败时不会触碰任何派生数据；其他阶段失败会返回 ``success=False``
            和 ``degraded=True``，但不会删除 canonical。

        异常:
            asyncio.CancelledError: 调用方取消重建时继续传播取消信号。
        """

        async with self._lock:
            canonical = await self._verify_canonical()
            if not canonical["success"]:
                return {
                    "success": False,
                    "degraded": True,
                    "reason_code": canonical["reason_code"],
                    "canonical": canonical,
                    "stages": {},
                    "errors": 1,
                }

            stages: dict[str, dict[str, Any]] = {}
            stages["indexes"] = await self._run_stage(
                "indexes",
                self._rebuild_indexes,
                failure_reason="index_rebuild_failed",
            )
            stages["graph"] = await self._run_stage(
                "graph",
                self._rebuild_graph,
                failure_reason="graph_rebuild_failed",
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
                "notes",
                self._rebuild_notes,
                failure_reason="note_rebuild_failed",
            )

            failed_stages = [
                name
                for name, stage in stages.items()
                if stage.get("status") == "failed"
            ]
            success = not failed_stages
            reason_code = (
                "derived_rebuild_completed"
                if success
                else str(stages[failed_stages[0]].get("reason_code"))
            )
            return {
                "success": success,
                "degraded": not success,
                "reason_code": reason_code,
                "canonical": canonical,
                "stages": stages,
                "errors": len(failed_stages),
            }

    async def _verify_canonical(self) -> dict[str, Any]:
        """只读确认 canonical 文档可访问，并返回安全计数。"""

        try:
            count_loader = getattr(self.index_validator, "_get_document_count", None)
            if callable(count_loader):
                count = int(await count_loader())
            else:
                storage = getattr(self.memory_engine, "faiss_db", None)
                storage = getattr(storage, "document_storage", None)
                count_loader = getattr(storage, "count_documents", None)
                if not callable(count_loader):
                    raise RuntimeError("canonical_count_unavailable")
                count = int(await count_loader(metadata_filters={}))
            return {
                "status": "verified",
                "success": True,
                "documents": max(0, count),
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
        """执行一个派生阶段并把普通异常转换为稳定降级结果。"""

        try:
            result = await operation()
            if not isinstance(result, dict):
                return {
                    "status": "failed",
                    "success": False,
                    "reason_code": failure_reason,
                }
            if result.get("status") == "skipped":
                return result
            if result.get("status") == "failed":
                return {
                    **result,
                    "status": "failed",
                    "success": False,
                    "reason_code": failure_reason,
                }
            if result.get("success", True):
                return {"status": "completed", **result}
            return {
                **result,
                "status": "failed",
                "success": False,
                "reason_code": failure_reason,
            }
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("派生重建阶段失败：%s，reason_code=%s", name, failure_reason)
            return {
                "status": "failed",
                "success": False,
                "reason_code": failure_reason,
            }

    async def _rebuild_indexes(self) -> dict[str, Any]:
        """调用现有 IndexValidator 重建 FTS5/BM25 和 FAISS。"""

        rebuild = getattr(self.index_validator, "rebuild_indexes", None)
        if not callable(rebuild):
            return {
                "status": "failed",
                "success": False,
                "reason_code": "index_rebuild_unavailable",
            }
        result = await rebuild(self.memory_engine)
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
        result = await rebuild()
        if not isinstance(result, dict):
            return {"status": "completed", "success": True}
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
        return await rebuild()

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
        return await rebuild()

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
        return await rebuild()


__all__ = ["DerivedRebuildCoordinator"]
