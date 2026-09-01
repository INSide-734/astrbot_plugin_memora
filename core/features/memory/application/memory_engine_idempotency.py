"""MemoryEngine 的 canonical 幂等预检与文档写入恢复协调。"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any

from ....shared.summary_source_fence import SummarySourceFence
from ...observability.application.memory_write_timing import (
    measure_memory_write_stage,
    observe_memory_write,
)
from ..infrastructure.canonical_idempotency import (
    find_canonical_memory_id_by_idempotency_key,
    normalize_canonical_idempotency_key,
)


class MemoryEngineIdempotencyMixin:
    """承载 canonical 幂等键预检、文档写入及竞态恢复。"""

    @observe_memory_write
    async def add_memory(
        self,
        content: str,
        session_id: str | None = None,
        persona_id: str | None = None,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
        atoms: list | None = None,
        *,
        source_fence: SummarySourceFence | None = None,
    ) -> int:
        """规范化幂等键后提交 canonical，并校验可选总结来源 fence。"""

        normalized_metadata = dict(metadata or {})
        idempotency_key = self._canonical_idempotency_key(normalized_metadata)
        if idempotency_key:
            normalized_metadata["idempotency_key"] = idempotency_key
        if source_fence is not None:
            await self._validate_summary_source_fence(source_fence)
            normalized_metadata.update(
                {
                    "source_epoch": source_fence.session_epoch,
                    "source_digest": source_fence.source_digest,
                    "source_fence_generation": source_fence.worker_generation,
                    "source_fence": source_fence.opaque_token,
                    "summary_source_orphan": True,
                    "summary_source_pending": True,
                }
            )

        if not idempotency_key:
            memory_id = await self._add_memory_unchecked(
                content,
                session_id=session_id,
                persona_id=persona_id,
                importance=importance,
                metadata=normalized_metadata or None,
                atoms=atoms,
                summary_source_staged=source_fence is not None,
            )
        else:
            lock = self._get_canonical_idempotency_lock()
            async with lock:
                existing_id = await self.find_memory_id_by_idempotency_key(
                    idempotency_key
                )
                if existing_id is not None:
                    memory_id = existing_id
                else:
                    memory_id = await self._add_memory_unchecked(
                        content,
                        session_id=session_id,
                        persona_id=persona_id,
                        importance=importance,
                        metadata=normalized_metadata,
                        atoms=atoms,
                        summary_source_staged=source_fence is not None,
                    )
        if source_fence is None:
            return memory_id
        if not await self._summary_source_fence_is_active(source_fence):
            await self._set_summary_source_orphan(memory_id, True)
            raise RuntimeError("summary_source_fenced")
        if not await self._set_summary_source_orphan(memory_id, False):
            raise RuntimeError("summary_source_activation_failed")
        await self._finalize_summary_source_write(memory_id)
        return memory_id

    async def _finalize_summary_source_write(self, memory_id: int) -> None:
        """在来源 fence 仍有效后补运行 canonical 派生和观测钩子。"""

        source = await self.get_memory(memory_id)
        if source is None:
            await self._set_summary_source_orphan(memory_id, True)
            raise RuntimeError("summary_source_activation_failed")
        content = str(source.get("text") or source.get("content") or "")
        metadata = source.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        self._create_tracked_task(
            self._retrieval.apply_interference(memory_id, content)
        )
        self._create_tracked_task(self._retrieval.extract_triggers(content, memory_id))
        self._record_add_memory_observability(
            doc_id=memory_id,
            content=content,
            metadata=metadata,
            atoms=[],
            duration_s=0.0,
        )
        await self._schedule_evolution_after_write(memory_id)
        self._schedule_domain_proposals_after_write(memory_id)

    def set_summary_source_validator(
        self,
        validator: Callable[[SummarySourceFence], bool | Awaitable[bool]],
    ) -> None:
        """注入总结来源 fence 验证器，供组合根绑定 ConversationStore。"""

        self._summary_source_validator = validator

    async def _summary_source_fence_is_active(
        self, source_fence: SummarySourceFence
    ) -> bool:
        """调用持久化来源验证器，异常和非法结果均视为失效。"""

        validator = getattr(self, "_summary_source_validator", None)
        if not callable(validator):
            return False
        try:
            result = validator(source_fence)
            if inspect.isawaitable(result):
                result = await result
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        return result is True

    async def _validate_summary_source_fence(
        self, source_fence: SummarySourceFence
    ) -> None:
        """在 canonical 外部副作用前执行 fail-closed 来源校验。"""

        if not isinstance(source_fence, SummarySourceFence):
            raise RuntimeError("summary_source_fenced")
        if not await self._summary_source_fence_is_active(source_fence):
            raise RuntimeError("summary_source_fenced")

    async def _set_summary_source_orphan(self, memory_id: int, orphan: bool) -> bool:
        """切换总结来源暂存标记，且不推进 canonical source revision。"""

        connection = getattr(self, "db_connection", None)
        if connection is None:
            return False
        try:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "SELECT metadata FROM documents WHERE id=?", (int(memory_id),)
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                await connection.rollback()
                return False
            raw_metadata = row[0]
            if isinstance(raw_metadata, str):
                try:
                    current = json.loads(raw_metadata)
                except (TypeError, json.JSONDecodeError):
                    await connection.rollback()
                    return False
            elif isinstance(raw_metadata, dict):
                current = dict(raw_metadata)
            else:
                await connection.rollback()
                return False
            if not isinstance(current, dict):
                await connection.rollback()
                return False
            current["summary_source_orphan"] = bool(orphan)
            current["summary_source_pending"] = False
            updated = await connection.execute(
                "UPDATE documents SET metadata=? WHERE id=?",
                (json.dumps(current, ensure_ascii=False), int(memory_id)),
            )
            if updated.rowcount != 1:
                await connection.rollback()
                return False
            await connection.commit()
            invalidate = getattr(
                getattr(self, "_retrieval", None), "invalidate_cache", None
            )
            if callable(invalidate):
                invalidate()
            return True
        except asyncio.CancelledError:
            try:
                await connection.rollback()
            except Exception:
                pass
            raise
        except Exception:
            try:
                await connection.rollback()
            except Exception:
                pass
            return False

    async def find_memory_id_by_idempotency_key(self, key: str) -> int | None:
        """从 v9 canonical 唯一映射查找幂等键 owner，不返回正文。"""

        if self.db_connection is None:
            return None
        return await find_canonical_memory_id_by_idempotency_key(
            self.db_connection,
            key,
        )

    @staticmethod
    def _canonical_idempotency_key(metadata: dict[str, Any] | None) -> str:
        """读取非空幂等键；没有显式键的普通写入保持原行为。"""

        if not isinstance(metadata, dict):
            return ""
        return normalize_canonical_idempotency_key(metadata.get("idempotency_key"))

    def _get_canonical_idempotency_lock(self) -> asyncio.Lock:
        """惰性创建当前引擎的幂等写锁，串行化同进程重试。"""

        lock = getattr(self, "_canonical_idempotency_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._canonical_idempotency_lock = lock
        return lock

    async def _write_document_stage(
        self,
        content: str,
        full_metadata: dict[str, Any],
        metadata: dict[str, Any] | None,
        op_id: int | None,
    ) -> tuple[int, bool]:
        """写入文档向量并在 keyed 竞态失败时恢复既有 canonical owner。"""

        try:
            with measure_memory_write_stage("document_vector"):
                doc_id = await self.hybrid_retriever.add_memory(
                    content,
                    full_metadata,
                )
            await self._write_journal.advance_op(
                op_id,
                "document_indexed",
                memory_id=doc_id,
                payload_patch={"memory_id": doc_id},
            )
            return doc_id, False
        except asyncio.CancelledError:
            raise
        except Exception as error:
            owner_id = await self._recover_idempotent_write_owner(op_id, metadata)
            if owner_id is not None:
                return owner_id, True
            await self._write_journal.advance_op(
                op_id,
                "document_failed",
                status="failed",
                error=str(error),
            )
            self._record_add_memory_failure("document")
            raise

    async def _recover_idempotent_write_owner(
        self,
        op_id: int | None,
        metadata: dict[str, Any] | None,
    ) -> int | None:
        """失败后至多查询一次 owner，并原子收口成功的 loser 日志。"""

        idempotency_key = self._canonical_idempotency_key(metadata)
        if not idempotency_key:
            return None
        try:
            owner_id = await self.find_memory_id_by_idempotency_key(idempotency_key)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        if owner_id is None:
            return None
        await self._write_journal.advance_op(
            op_id,
            "completed",
            status="completed",
            memory_id=owner_id,
            payload_patch={"memory_id": owner_id},
        )
        return owner_id
