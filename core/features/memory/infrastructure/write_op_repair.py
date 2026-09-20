"""
写操作崩溃修复 Mixin
负责多存储写操作的断点恢复逻辑
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from astrbot.api import logger

from ....shared.contracts.events import CanonicalMemoryCommitted
from ..application.atom_source_binding import (
    bind_atoms_to_canonical_source,
    validate_bound_atoms_match_canonical_source,
)
from ..application.write_coordinator import coordinated_transaction
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
    return not any(
        metadata.get(field)
        for field in (
            "summary_source_orphan",
            "summary_source_pending",
            "replacement_pending",
        )
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
                      AND (
                        (
                          step = 'source_missing'
                          AND op_type IN ('add', 'graph_reindex')
                          AND EXISTS (
                            SELECT 1 FROM documents
                            WHERE documents.id = memory_write_ops.memory_id
                          )
                        )
                        OR (
                          op_type = 'replace_content'
                          AND step NOT IN (
                            'replacement_committed',
                            'replacement_rolled_back'
                          )
                        )
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
            current_cursor = await self._db.execute(
                "SELECT status, step FROM memory_write_ops WHERE id = ?",
                (int(row["id"]),),
            )
            current = await current_cursor.fetchone()
            await current_cursor.close()
            if current is None or (current["status"], current["step"]) != (
                row["status"],
                row["step"],
            ):
                continue
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
                elif op_type == "replace_content":
                    ok = await self._repair_replace(
                        int(row["id"]),
                        payload,
                    )
                else:
                    ok = False
                repaired += 1 if ok else 0
            except asyncio.CancelledError:
                raise
            except Exception:
                reason = (
                    "content_replace_repair_failed"
                    if row["op_type"] == "replace_content"
                    else "write_repair_failed"
                )
                logger.error("[WriteOpJournal] 修复未完成 reason_code=%s", reason)
                await self.advance_op(
                    int(row["id"]),
                    str(row["step"] or "repair_failed"),
                    status="needs_repair",
                    error=reason,
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

    async def _resolve_pending_add_document(
        self,
        op_id: int,
        payload: dict[str, Any],
    ) -> int | None:
        """按预写 ``doc_id`` 找回已提交但账本尚未记账的 canonical 行。

        写入端在 INSERT 前把该行的 ``doc_id`` 预写进本次操作的 ``pending_doc_id``
        （``documents`` 的宿主引擎与 ``memory_write_ops`` 的 aiosqlite 连接是同一
        个 ``memora.db`` 文件上的两个连接，无法共享事务）。这里用账本自身的
        canonical 连接按 ``documents.doc_id`` 唯一索引取回整数 ID，并就地补记
        ``documents_committed`` 与 ``indexes_pending``，后续阶段仍走既有的 add
        修复路径，不复制宿主插入语义。

        返回 ``None`` 时调用方不得继续按猜想的 ID 补索引：没有 ``pending_doc_id``
        的旧账本保持 ``unrepairable`` 终态；意图存在却没有 canonical 行说明 INSERT
        未提交，记 ``document_absent`` 终态而不是空转重试；查询本身失败只降级为
        ``needs_repair``（``pending_document_lookup_failed``）等下一轮有界重试。
        """

        doc_id = payload.get("pending_doc_id")
        if not isinstance(doc_id, str) or not doc_id:
            await self.advance_op(
                op_id,
                "unrepairable",
                status="failed",
                error="missing memory_id for add repair",
            )
            return None
        try:
            cursor = await self._db.execute(
                "SELECT id FROM documents WHERE doc_id = ? LIMIT 1",
                (doc_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "[WriteOpJournal] 预写意图定位失败 "
                "reason_code=pending_document_lookup_failed"
            )
            await self.advance_op(
                op_id,
                "document_lookup_degraded",
                status="needs_repair",
                error="pending_document_lookup_failed",
            )
            return None
        if row is None or row[0] is None:
            await self.advance_op(
                op_id,
                "document_absent",
                status="failed",
                error="pending_document_missing",
            )
            return None
        memory_id = int(row[0])
        # 预写意图早于 INSERT，索引阶段必然未执行：恢复后必须补索引。
        payload["memory_id"] = memory_id
        payload["indexes_pending"] = True
        await self.advance_op(
            op_id,
            "documents_committed",
            memory_id=memory_id,
            payload_patch={"memory_id": memory_id, "indexes_pending": True},
        )
        return memory_id

    async def _repair_add(
        self,
        op_id: int,
        memory_id: int | None,
        payload: dict[str, Any],
    ) -> bool:
        """重放未完成的 canonical 添加派生步骤。"""

        if memory_id is None:
            memory_id = await self._resolve_pending_add_document(op_id, payload)
            if memory_id is None:
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

        if payload.get("indexes_pending"):
            repair_indexes = getattr(self, "_repair_document_indexes", None)
            if repair_indexes is None or not await repair_indexes(int(memory_id)):
                await self.advance_op(
                    op_id,
                    "index_stage_degraded",
                    status="needs_repair",
                    memory_id=int(memory_id),
                    error="index_stage_degraded",
                )
                return False
            await self.advance_op(
                op_id,
                "document_indexed",
                memory_id=int(memory_id),
                payload_patch={"indexes_pending": False},
            )
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
        except Exception:
            await self.advance_op(
                int(op["op_id"]),
                "graph_failed",
                status="needs_repair",
                memory_id=int(memory_id),
                error="graph_reindex_failed",
            )
            logger.error(
                "[WriteOpJournal] 来源接受后建图降级 reason_code=graph_reindex_failed"
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

    @staticmethod
    def _ledger_memory_id(payload: dict[str, Any], key: str) -> int | None:
        """解析账本载荷中的整数 ID；缺失或非法时返回 ``None``。"""

        value = payload.get(key)
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def _repair_replace(self, op_id: int, payload: dict[str, Any]) -> bool:
        """收敛两阶段正文替换：按正文摘要判定赢家，绝不复活已删除内容。

        - 新旧并存且新行摘要等于账本 ``content_digest`` → 删除旧行（含派生清理），
          替换提交；
        - 新旧并存但摘要不匹配（新行已被再次编辑）→ 删除新行，替换失败，旧行保持
          权威；
        - 单边存在按事实收敛（只剩新行即已提交，只剩旧行即未提交）；
        - 账本尚未记录 ``new_id`` 时按 ``previous_id`` 反查替换新行，覆盖 add 提交
          后账本未收口的窗口；候选多于一个时保留待修复，不误删任何行。
        """

        old_id = self._ledger_memory_id(payload, "old_id")
        if old_id is None:
            await self.advance_op(
                op_id,
                "unrepairable",
                status="failed",
                error="missing old_id for content replace repair",
            )
            return False
        new_id = self._ledger_memory_id(payload, "new_id")
        if new_id == old_id:
            await self.advance_op(
                op_id,
                "replacement_rolled_back",
                status="failed",
                error="replacement_id_conflict",
            )
            return False
        if new_id is None:
            candidates = await self._find_replacement_documents(old_id)
            if len(candidates) > 1:
                await self.advance_op(
                    op_id,
                    "replacement_ambiguous",
                    status="needs_repair",
                    memory_id=old_id,
                    error="multiple_replacement_candidates",
                )
                return False
            new_id = candidates[0] if candidates else None
        old_text = await self._load_canonical_text(old_id)
        new_text = (
            await self._load_canonical_text(new_id) if new_id is not None else None
        )
        if old_text is not None and new_text is not None and new_id is not None:
            digest = payload.get("content_digest")
            if (
                isinstance(digest, str)
                and digest
                and ledger_content_digest(new_text) == digest
            ):
                return await self._commit_replacement(op_id, old_id, new_id, payload)
            return await self._rollback_replacement(op_id, old_id, new_id, payload)
        if new_id is not None and new_text is not None:
            # canonical 旧行虽已删，派生清理可能上次失败，必须重放后才能收口。
            return await self._commit_replacement(op_id, old_id, new_id, payload)
        if old_text is not None:
            await self._switch_replacement_visibility(old_id, new_id, payload, old_id)
            if new_id is not None and not await self._delete_replacement_side(new_id):
                return False
            await self.advance_op(
                op_id,
                "replacement_rolled_back",
                status="failed",
                memory_id=old_id,
                error="replacement_document_missing",
            )
            return False
        await self.advance_op(
            op_id,
            "replacement_rolled_back",
            status="failed",
            memory_id=old_id,
            error="replacement_documents_missing",
        )
        return False

    async def _commit_replacement(
        self, op_id: int, old_id: int, new_id: int, payload: dict[str, Any]
    ) -> bool:
        """新行摘要匹配账本意图：删除旧行并收口为已提交。"""
        if not await self._switch_replacement_visibility(
            old_id, new_id, payload, new_id
        ):
            return False

        if not await self._delete_replacement_side(old_id):
            await self.advance_op(
                op_id,
                "replacement_commit_failed",
                status="needs_repair",
                memory_id=new_id,
                error="content_replace_delete_failed",
                payload_patch={"old_id": old_id, "new_id": new_id},
            )
            return False
        await self.finalize_add_derivation(new_id)
        await self.advance_op(
            op_id,
            "replacement_committed",
            status="completed",
            memory_id=new_id,
            payload_patch={"old_id": old_id, "new_id": new_id},
        )
        return True

    async def _rollback_replacement(
        self,
        op_id: int,
        old_id: int,
        new_id: int,
        payload: dict[str, Any],
    ) -> bool:
        """新行摘要不匹配账本意图：删除新行，旧行保持权威。"""
        if not await self._switch_replacement_visibility(
            old_id, new_id, payload, old_id
        ):
            return False

        if not await self._delete_replacement_side(new_id):
            await self.advance_op(
                op_id,
                "replacement_rollback_failed",
                status="needs_repair",
                memory_id=old_id,
                error="content_replace_rollback_failed",
                payload_patch={"old_id": old_id, "new_id": new_id},
            )
            return False
        await self.advance_op(
            op_id,
            "replacement_rolled_back",
            status="failed",
            memory_id=old_id,
            error="content_digest_mismatch",
            payload_patch={"old_id": old_id, "new_id": new_id},
        )
        return False

    async def _delete_replacement_side(self, memory_id: int) -> bool:
        """删除替换的一侧（canonical 与派生清理），并证明该行已消失。

        端口缺失或删除后行仍存在时返回 ``False``：调用方保持账本开放，不把
        “未能确认删除”当成收敛，也不制造“正文还在、图与原子已丢”的半删状态。
        """

        if (
            self._delete_doc_indexes_batch is None
            or self._delete_graph_atoms_batch is None
        ):
            return False
        await self._delete_doc_indexes_batch([int(memory_id)])
        if not await self._canonical_document_deleted(int(memory_id)):
            return False
        await self._delete_graph_atoms_batch([int(memory_id)])
        if self._invalidate_cache:
            self._invalidate_cache()
        return True

    async def _switch_replacement_visibility(
        self,
        old_id: int,
        new_id: int | None,
        payload: dict[str, Any],
        winner_id: int,
    ) -> bool:
        """在同一事务隐藏输家、恢复赢家；不插入行，不复活已被删除的正文。"""

        if self._db is None:
            return False
        async with coordinated_transaction(self._db):
            cursor = await self._db.execute(
                "SELECT id, text, metadata FROM documents WHERE id IN (?, ?)",
                (old_id, new_id),
            )
            rows = {int(row[0]): row for row in await cursor.fetchall()}
            await cursor.close()
            if winner_id not in rows:
                return False
            if winner_id == new_id and old_id in rows:
                if ledger_content_digest(str(rows[winner_id][1])) != payload.get(
                    "content_digest"
                ):
                    return False
            # 先隐藏输家再恢复赢家；同连接的读取者也不会看到两条可召回行。
            for memory_id, row in sorted(
                rows.items(), key=lambda item: item[0] == winner_id
            ):
                metadata = safe_json_dict(row[2])
                if memory_id == winner_id:
                    if not metadata.pop("replacement_pending", False):
                        continue
                    status_key = (
                        "previous_status"
                        if memory_id == old_id
                        else "replacement_status"
                    )
                    snapshot = payload.get(status_key) or {}
                    for field in ("memory_status", "status", "status_changed_at"):
                        metadata.pop(field, None)
                        if field in snapshot:
                            metadata[field] = snapshot[field]
                else:
                    metadata.update(
                        memory_status="deleted",
                        status="deleted",
                        replacement_pending=True,
                    )
                await self._db.execute(
                    "UPDATE documents SET metadata=?, updated_at=? WHERE id=?",
                    (
                        json.dumps(metadata, ensure_ascii=False),
                        datetime.now(timezone.utc).isoformat(),
                        memory_id,
                    ),
                )
        if self._invalidate_cache:
            self._invalidate_cache()
        return True

    async def _load_canonical_text(self, memory_id: int) -> str | None:
        """读取 canonical 正文用于摘要比对；行不存在返回 ``None``（正文不落日志）。"""

        if self._db is None:
            return None
        cursor = await self._db.execute(
            "SELECT text FROM documents WHERE id = ? LIMIT 1",
            (int(memory_id),),
        )
        try:
            row = await cursor.fetchone()
        finally:
            with suppress(Exception):
                await cursor.close()
        return str(row[0] or "") if row is not None else None

    async def _find_replacement_documents(self, old_id: int) -> list[int]:
        """按 ``previous_id`` 反查替换新行，覆盖账本未记录 ``new_id`` 的窗口。

        只用于替换收敛：``previous_id`` 是替换写端唯一写入的元数据锚点；
        查询按 ID 升序返回，调用方在多于一个候选时放弃收敛。
        """

        if self._db is None:
            return []
        cursor = await self._db.execute(
            """
            SELECT id FROM documents
            WHERE CASE WHEN json_valid(metadata)
                  THEN json_extract(metadata, '$.previous_id') END
                  IN (:old_id, :old_id_text)
            ORDER BY id ASC
            LIMIT 25
            """,
            {"old_id": int(old_id), "old_id_text": str(int(old_id))},
        )
        try:
            rows = await cursor.fetchall()
        finally:
            with suppress(Exception):
                await cursor.close()
        return [int(row[0]) for row in rows]

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
