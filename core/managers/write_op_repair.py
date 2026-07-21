"""
写操作崩溃修复 Mixin
负责多存储写操作的断点恢复逻辑
"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger

from ..models.memory_atom import MemoryAtom
from .atom_source_binding import (
    bind_atoms_to_canonical_source,
    validate_bound_atoms_match_canonical_source,
)
from .write_op_serialization import _deserialize_atom_from_repair, safe_json_dict


class WriteOpRepairMixin:
    """写操作日志 — 崩溃修复（Mixin，通过 MRO 访问宿主属性）"""

    # ---- 崩溃修复 ----

    async def repair_incomplete(self) -> int:
        """对未完成的添加/删除操作进行尽力重放修复。"""
        if self._db is None:
            return 0

        try:
            cursor = await self._db.execute(
                """
                SELECT id, op_type, memory_id, status, step, payload, retry_count
                FROM memory_write_ops
                WHERE status IN ('pending', 'needs_repair')
                  AND retry_count < ?
                ORDER BY id ASC
                LIMIT 25
                """,
                (self._max_retries,),
            )
            rows = await cursor.fetchall()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("[WriteOpJournal] 读取待修复写操作失败", exc_info=True)
            return 0

        repaired = 0
        for row in rows:
            payload = safe_json_dict(row["payload"])
            try:
                op_type = row["op_type"]
                memory_id = row["memory_id"]
                if op_type == "add":
                    ok = await self._repair_add(
                        int(row["id"]),
                        int(memory_id) if memory_id is not None else None,
                        payload,
                    )
                elif op_type == "delete":
                    ok = await self._repair_delete(
                        int(row["id"]),
                        int(memory_id) if memory_id is not None else None,
                    )
                elif op_type == "batch_delete":
                    ok = await self._repair_batch_delete(
                        int(row["id"]),
                        payload,
                    )
                elif op_type == "graph_reindex":
                    ok = await self._repair_graph_reindex(
                        int(row["id"]),
                        int(memory_id) if memory_id is not None else None,
                        payload,
                    )
                else:
                    ok = False
                repaired += 1 if ok else 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    f"[WriteOpJournal] 修复写操作失败 (op_id={row['id']})",
                    exc_info=True,
                )
                await self.advance_op(
                    int(row["id"]),
                    str(row["step"] or "repair_failed"),
                    status="needs_repair",
                    error=str(e),
                )

        if repaired:
            logger.info(f"[WriteOpJournal] 已修复 {repaired} 个未完成写操作")
            if self._invalidate_cache:
                self._invalidate_cache()
        return repaired

    async def _repair_add(
        self,
        op_id: int,
        memory_id: int | None,
        payload: dict[str, Any],
    ) -> bool:
        """重放未完成的 canonical 添加派生步骤。"""

        if memory_id is None:
            await self.advance_op(
                op_id,
                "unrepairable",
                status="failed",
                error="missing memory_id for add repair",
            )
            return False

        if self._get_memory is None:
            await self.advance_op(
                op_id,
                "unrepairable",
                status="failed",
                memory_id=int(memory_id),
                error="get_memory callback not available",
            )
            return False

        memory = await self._get_memory(int(memory_id))
        if memory is None:
            await self.advance_op(
                op_id,
                "source_missing",
                status="failed",
                memory_id=int(memory_id),
                error="source document missing",
            )
            return False

        metadata = memory.get("metadata") or payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = safe_json_dict(metadata)
        content = str(memory.get("text") or "")
        session_id = metadata.get("session_id") or payload.get("session_id")
        persona_id = metadata.get("persona_id") or payload.get("persona_id")

        atom_payloads = payload.get("failed_atoms") or payload.get("atoms", []) or []
        atoms: list[MemoryAtom] = []
        for atom_payload in atom_payloads:
            if isinstance(atom_payload, dict):
                atom = _deserialize_atom_from_repair(
                    atom_payload,
                    int(memory_id),
                    session_id,
                    persona_id,
                )
                if atom is not None:
                    atoms.append(atom)

        if atoms and not any(
            atom.parent_revision
            or atom.parent_scope_key
            or atom.parent_privacy_level
            for atom in atoms
        ):
            atoms = bind_atoms_to_canonical_source(
                atoms,
                memory,
                fallback_metadata=metadata,
            )

        try:
            validate_bound_atoms_match_canonical_source(
                atoms,
                memory,
                fallback_metadata=metadata,
            )
        except ValueError as exc:
            await self.advance_op(
                op_id,
                "source_stale",
                status="failed",
                memory_id=int(memory_id),
                error=str(exc),
            )
            return False

        if self._atom_store is not None and atoms and self._atom_enabled:
            raw_parent_loader = getattr(type(self._atom_store), "get_by_parent_raw", None)
            if callable(raw_parent_loader):
                existing_atoms = await self._atom_store.get_by_parent_raw(
                    int(memory_id)
                )
            else:
                # 兼容只提供旧 Store 协议的测试替身和外部适配器。
                existing_atoms = await self._atom_store.get_by_parent(int(memory_id))
            if payload.get("failed_atoms"):
                existing_keys = {
                    (
                        atom.content,
                        atom.atom_type.value,
                        atom.session_id,
                        atom.persona_id,
                    )
                    for atom in existing_atoms
                }
                atoms_to_insert = [
                    atom
                    for atom in atoms
                    if (
                        atom.content,
                        atom.atom_type.value,
                        atom.session_id,
                        atom.persona_id,
                    )
                    not in existing_keys
                ]
                if atoms_to_insert:
                    await self._atom_store.insert_many(atoms_to_insert)
            elif not existing_atoms:
                await self._atom_store.insert_many(atoms)
            await self.advance_op(op_id, "atoms_repaired", memory_id=memory_id)

        if self._graph_memory_manager is not None and content.strip():
            await self._graph_memory_manager.index_memory(
                int(memory_id),
                content,
                metadata,
                atoms or None,
            )
            await self.advance_op(op_id, "graph_repaired", memory_id=memory_id)

        await self.advance_op(
            op_id,
            "completed",
            status="completed",
            memory_id=int(memory_id),
        )
        return True

    async def _repair_graph_reindex(
        self,
        op_id: int,
        memory_id: int | None,
        payload: dict[str, Any],
    ) -> bool:
        """重放未完成的图索引构建。"""

        if memory_id is None:
            await self.advance_op(
                op_id,
                "unrepairable",
                status="failed",
                error="missing memory_id for graph reindex repair",
            )
            return False

        if self._graph_memory_manager is None:
            await self.advance_op(
                op_id,
                "completed",
                status="completed",
                memory_id=int(memory_id),
                payload_patch={"skipped": "graph manager not available"},
            )
            return True

        if self._get_memory is None:
            await self.advance_op(
                op_id,
                "unrepairable",
                status="failed",
                memory_id=int(memory_id),
                error="get_memory callback not available",
            )
            return False

        memory = await self._get_memory(int(memory_id))
        if memory is None:
            await self.advance_op(
                op_id,
                "source_missing",
                status="failed",
                memory_id=int(memory_id),
                error="source document missing",
            )
            return False

        content = str(memory.get("text") or "")
        metadata = memory.get("metadata") or payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = safe_json_dict(metadata)

        if not content.strip():
            await self.advance_op(
                op_id,
                "completed",
                status="completed",
                memory_id=int(memory_id),
                payload_patch={"skipped": "empty content"},
            )
            return True

        await self._graph_memory_manager.index_memory(
            int(memory_id),
            content,
            metadata,
            None,
        )
        await self.advance_op(op_id, "graph_repaired", memory_id=int(memory_id))
        await self.advance_op(
            op_id,
            "completed",
            status="completed",
            memory_id=int(memory_id),
        )
        return True

    async def _repair_delete(
        self,
        op_id: int,
        memory_id: int | None,
    ) -> bool:
        """完成 canonical 删除后的图与 Atom 清理。"""

        if memory_id is None:
            await self.advance_op(
                op_id,
                "unrepairable",
                status="failed",
                error="missing memory_id for delete repair",
            )
            return False

        if self._graph_memory_manager is not None:
            await self._graph_memory_manager.delete_memory(int(memory_id))
        if self._atom_store is not None:
            await self._atom_store.delete_by_parent(int(memory_id))

        await self.advance_op(
            op_id,
            "completed",
            status="completed",
            memory_id=int(memory_id),
        )
        return True

    async def _repair_batch_delete(
        self,
        op_id: int,
        payload: dict[str, Any],
    ) -> bool:
        """重放批量删除的索引与派生清理。"""

        memory_ids_raw = payload.get("memory_ids") or []
        if not isinstance(memory_ids_raw, list):
            await self.advance_op(
                op_id,
                "unrepairable",
                status="failed",
                error="missing memory_ids for batch delete repair",
            )
            return False

        memory_ids: list[int] = []
        for raw_id in memory_ids_raw:
            try:
                memory_ids.append(int(raw_id))
            except (TypeError, ValueError):
                continue

        if not memory_ids:
            await self.advance_op(
                op_id,
                "unrepairable",
                status="failed",
                error="empty memory_ids for batch delete repair",
            )
            return False

        if self._delete_doc_indexes_batch:
            await self._delete_doc_indexes_batch(memory_ids)
        if self._delete_graph_atoms_batch:
            await self._delete_graph_atoms_batch(memory_ids)
        await self.advance_op(
            op_id,
            "completed",
            status="completed",
            payload_patch={"deleted_count": len(memory_ids)},
        )
        return True
