"""MemoryEngine 的 canonical 幂等预检与文档写入恢复协调。"""

from __future__ import annotations

import asyncio
from typing import Any

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
    ) -> int:
        """规范化幂等键后提交 canonical memory，并维护后续派生数据。"""

        idempotency_key = self._canonical_idempotency_key(metadata)
        if not idempotency_key:
            return await self._add_memory_unchecked(
                content,
                session_id=session_id,
                persona_id=persona_id,
                importance=importance,
                metadata=metadata,
                atoms=atoms,
            )

        normalized_metadata = dict(metadata or {})
        normalized_metadata["idempotency_key"] = idempotency_key
        lock = self._get_canonical_idempotency_lock()
        async with lock:
            existing_id = await self.find_memory_id_by_idempotency_key(idempotency_key)
            if existing_id is not None:
                return existing_id
            return await self._add_memory_unchecked(
                content,
                session_id=session_id,
                persona_id=persona_id,
                importance=importance,
                metadata=normalized_metadata,
                atoms=atoms,
            )

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
