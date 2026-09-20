"""
写操作崩溃修复 Mixin
负责多存储写操作的断点恢复逻辑
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from astrbot.api import logger

from ....shared.contracts.events import CanonicalMemoryCommitted
from ..application.atom_source_binding import (
    bind_atoms_to_canonical_source,
    validate_bound_atoms_match_canonical_source,
)
from ..domain.memory_atom import MemoryAtom
from .write_op_serialization import _deserialize_atom_from_repair, safe_json_dict


def source_acceptance_allows_derivation(metadata: Any) -> bool:
    """判断 canonical metadata 是否已脱离来源暂存/拒绝状态。

    只有来源 owner 明确清除 ``summary_source_orphan`` 与
    ``summary_source_pending`` 后才允许派生图与业务副作用；缺省字段的历史行
    视为已接受，避免把旧数据误判为暂存。
    """

    if not isinstance(metadata, dict):
        return True
    return not metadata.get("summary_source_orphan") and not metadata.get(
        "summary_source_pending"
    )


def ledger_content_digest(content: str) -> str:
    """生成写账本暂存与修复共用的正文摘要（只落摘要，不落正文）。"""

    return CanonicalMemoryCommitted.digest_content(content)[:32]


# add 账本载荷以 ``content[:500]`` 暂存 ``content_preview``（见
# application/memory_engine_crud.py 的写端）；预览达到该长度即无法再用前缀证明全文。
CONTENT_PREVIEW_LIMIT = 500


class WriteOpRepairMixin:
    """写操作日志 — 崩溃修复（Mixin，通过 MRO 访问宿主属性）"""

    # ---- 崩溃修复 ----

    async def repair_incomplete(self) -> int:
        """重放未完成操作及 canonical 仍存在的误标 source_missing。"""
        if self._db is None:
            return 0

        try:
            cursor = await self._db.execute(
                """
                SELECT id, op_type, memory_id, status, step, payload, retry_count
                FROM memory_write_ops
                WHERE retry_count < ?
                  AND NOT (status = 'pending' AND step = 'source_staged')
                  AND (
                    status IN ('pending', 'needs_repair')
                    OR (
                      status = 'failed'
                      AND step = 'source_missing'
                      AND op_type IN ('add', 'graph_reindex')
                      AND EXISTS (
                        SELECT 1 FROM documents
                        WHERE documents.id = memory_write_ops.memory_id
                      )
                    )
                  )
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

        catalog = getattr(self, "_topic_catalog_store", None)
        if catalog is not None:
            try:
                repaired += int(
                    await catalog.repair_pending(
                        f"write-journal-{id(self)}",
                        limit=25,
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("[WriteOpJournal] topic catalog 修复失败", exc_info=True)

        if repaired:
            logger.info(f"[WriteOpJournal] 已修复 {repaired} 个未完成写操作")
            if self._invalidate_cache:
                self._invalidate_cache()
        return repaired

    @staticmethod
    def _can_converge_stale_source(
        payload: dict[str, Any],
        content: str,
        metadata: dict[str, Any],
    ) -> bool:
        """判断过期 Atom 绑定能否按当前 canonical 安全收敛。

        收敛前提是正文可证明未变：账本载荷带 ``content_digest`` 时按摘要比对；
        没有摘要时只能用历史写端的截断规则反推——``content_preview`` 是
        ``content[:500]``，只有长度未达 500 才说明它本身就是全文，此时
        ``content == preview`` 才能证明正文未变（恰好 500 字符的前缀可能来自
        被截断的长正文，等价也不足以证明第 500 字符之后仍未被改写）。
        正文可证明未变、且 scope/privacy 未变化时，revision 推进才可归因于
        元数据维护；语义修改必须继续保持 ``needs_repair``，不得以旧边界收口。
        """

        preview = payload.get("content_preview")
        if not isinstance(preview, str) or not preview:
            return False
        digest = payload.get("content_digest")
        if isinstance(digest, str) and digest:
            if ledger_content_digest(content) != digest:
                return False
        elif len(preview) >= CONTENT_PREVIEW_LIMIT or content != preview:
            return False
        staged_metadata = payload.get("metadata")
        if isinstance(staged_metadata, dict):
            for field in ("scope_key", "privacy_level"):
                recorded = staged_metadata.get(field)
                if recorded is not None and metadata.get(field) != recorded:
                    return False
        return True

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

        metadata = memory.get("metadata")
        if not isinstance(metadata, dict):
            metadata = safe_json_dict(metadata)
        content = str(memory.get("text") or "")
        session_id = metadata.get("session_id")
        persona_id = metadata.get("persona_id")

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
            atom.parent_revision or atom.parent_scope_key or atom.parent_privacy_level
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
            if not self._can_converge_stale_source(payload, content, metadata):
                # 正文或 scope 已变化：保留有界重试而不是终态失败，
                # 且该行已可召回，图意图必须继续可见。
                await self.advance_op(
                    op_id,
                    "source_stale",
                    status="needs_repair",
                    memory_id=int(memory_id),
                    error=str(exc),
                )
                return False
            # 仅元数据推进导致的 revision 变化：按当前 canonical 重新绑定后继续。
            if atoms:
                atoms = bind_atoms_to_canonical_source(
                    atoms,
                    memory,
                    fallback_metadata=metadata,
                )

        if self._atom_store is not None and atoms and self._atom_enabled:
            await self._repair_source_atoms(int(memory_id), atoms, payload)
            await self.advance_op(op_id, "atoms_repaired", memory_id=memory_id)

        if not source_acceptance_allows_derivation(metadata):
            # 来源 owner 尚未接受：连同待建图意图一起保留，等合法 claim 收尾。
            await self.advance_op(
                op_id,
                "source_staged",
                status="pending",
                memory_id=int(memory_id),
            )
            return False

        await self._derive_graph_stage(
            op_id,
            int(memory_id),
            content,
            metadata,
            atoms or None,
        )

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
                "graph_skipped",
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
        metadata = memory.get("metadata")
        if not isinstance(metadata, dict):
            metadata = safe_json_dict(metadata)

        if not content.strip():
            await self.advance_op(
                op_id,
                "graph_skipped",
                status="completed",
                memory_id=int(memory_id),
                payload_patch={"skipped": "empty content"},
            )
            return True

        await self._derive_graph_stage(
            op_id,
            int(memory_id),
            content,
            metadata,
            None,
        )
        await self.advance_op(
            op_id,
            "completed",
            status="completed",
            memory_id=int(memory_id),
        )
        return True

    async def finalize_add_derivation(self, memory_id: int) -> bool:
        """来源接受后补建图派生并收口该 canonical 的 add 操作。

        与启动修复共用同一条单来源收尾实现：图功能禁用、正文为空或图不可用时记为
        明确终态；来源仍处于暂存状态时保持账本开放，绝不派生图。账本不可用
        （``start_op`` 失败）时不能把“无记录”当成无需派生：这里按当前 canonical
        直接补建图，失败仍然可观察。
        """

        op = await self.find_open_add_op(int(memory_id))
        if op is None:
            return await self._derive_without_open_op(int(memory_id))
        try:
            return await self._repair_add(
                int(op["op_id"]),
                int(memory_id),
                op["payload"],
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # canonical 已接受：图失败只进入修复队列，不回滚 canonical。
            await self.advance_op(
                int(op["op_id"]),
                "graph_failed",
                status="needs_repair",
                memory_id=int(memory_id),
                error=str(exc),
            )
            logger.error(
                f"[WriteOpJournal] 来源接受后建图失败 (memory_id={memory_id})",
                exc_info=True,
            )
            return False

    async def _derive_without_open_op(self, memory_id: int) -> bool:
        """没有开放 add 账本时按当前 canonical 幂等补建图。

        返回 ``False`` 表示未派生（来源仍未接受、正文为空或图不可用）；
        真实索引异常只记录脱敏日志并由调用方继续降级，绝不伪报完成。
        """

        if self._get_memory is None:
            return False
        try:
            memory = await self._get_memory(int(memory_id))
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        if not isinstance(memory, dict):
            return False
        metadata = memory.get("metadata")
        if not isinstance(metadata, dict):
            metadata = safe_json_dict(metadata)
        if not source_acceptance_allows_derivation(metadata):
            return False
        content = str(memory.get("text") or "")
        if not content.strip() or self._graph_memory_manager is None:
            return False
        atoms = None
        if self._atom_store is not None and self._atom_enabled:
            loader = getattr(self._atom_store, "get_by_parent", None)
            if callable(loader):
                try:
                    loaded = await loader(int(memory_id))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    loaded = None
                if isinstance(loaded, (list, tuple)) and loaded:
                    atoms = list(loaded)
        try:
            await self._graph_memory_manager.index_memory(
                int(memory_id),
                content,
                metadata,
                atoms,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                f"[WriteOpJournal] 无账本收尾建图失败 (memory_id={memory_id})",
                exc_info=True,
            )
            return False
        return True

    async def find_open_add_op(self, memory_id: int) -> dict[str, Any] | None:
        """返回该 canonical 仍未收口的 add 操作记录（含修复载荷）。"""

        if self._db is None:
            return None
        try:
            cursor = await self._db.execute(
                """
                SELECT id, status, step, payload FROM memory_write_ops
                WHERE memory_id = ? AND op_type = 'add'
                  AND status IN ('pending', 'needs_repair')
                ORDER BY id DESC LIMIT 1
                """,
                (int(memory_id),),
            )
            row = await cursor.fetchone()
            await cursor.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("[WriteOpJournal] 读取待收口 add 操作失败", exc_info=True)
            return None
        if row is None:
            return None
        return {
            "op_id": int(row[0]),
            "status": str(row[1] or ""),
            "step": str(row[2] or ""),
            "payload": safe_json_dict(row[3]),
        }

    async def _derive_graph_stage(
        self,
        op_id: int,
        memory_id: int,
        content: str,
        metadata: dict[str, Any],
        atoms: list[MemoryAtom] | None,
    ) -> None:
        """按当前 canonical 建图并推进账本；不可派生时记为明确不适用。"""

        if self._graph_memory_manager is None or not content.strip():
            await self.advance_op(op_id, "graph_skipped", memory_id=memory_id)
            return
        await self._graph_memory_manager.index_memory(
            memory_id,
            content,
            metadata,
            atoms or None,
        )
        await self.advance_op(op_id, "graph_indexed", memory_id=memory_id)

    async def _repair_source_atoms(
        self,
        memory_id: int,
        atoms: list[MemoryAtom],
        payload: dict[str, Any],
    ) -> None:
        """只补写该来源缺失的暂存原子；调用方负责随后推进账本步骤。"""

        raw_parent_loader = getattr(type(self._atom_store), "get_by_parent_raw", None)
        if callable(raw_parent_loader):
            existing_atoms = await self._atom_store.get_by_parent_raw(memory_id)
        else:
            # 兼容只提供旧 Store 协议的测试替身和外部适配器。
            existing_atoms = await self._atom_store.get_by_parent(memory_id)
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

        if not await self._canonical_document_deleted(int(memory_id)):
            # canonical 仍存在（或存在性无法确认）说明崩溃点在删除提交之前：
            # 派生数据不得清理，账本也不能收口，否则会留下“正文还在、图与原子已丢”
            # 的不可重放状态。
            await self.advance_op(
                op_id,
                "source_alive",
                status="needs_repair",
                memory_id=int(memory_id),
                error="canonical document still present",
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

    async def _canonical_document_deleted(self, memory_id: int) -> bool:
        """严格判定 canonical 文档是否已删除，只在明确查无行时返回真。

        写账本的 ``_db`` 就是 canonical SQLite 连接，因此这里直接按 ID 查
        ``documents``；展示型 ``get_memory`` 端口会把读取异常吞成 None，不能
        作为“文档不存在”的证明。连接缺失时返回假（保持账本开放），查询报错
        继续上抛，由 ``repair_incomplete`` 记为 ``needs_repair``，取消照常传播。
        """

        if self._db is None:
            return False
        cursor = await self._db.execute(
            "SELECT 1 FROM documents WHERE id = ? LIMIT 1",
            (int(memory_id),),
        )
        try:
            return await cursor.fetchone() is None
        finally:
            with suppress(Exception):
                await cursor.close()

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
