"""总结任务来源 fence 的锁、快照与外部副作用边界。"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

from ....shared.summary_source_fence import SummarySourceFence
from ...reflection.domain.summary_models import ClaimedJob, SummaryReasonCode


class SummarySourceFenceMixin:
    """集中维护来源锁与 claim 快照，避免跨库副作用持有 Store 事务。"""

    if TYPE_CHECKING:
        connection: Any
        _write_lock: asyncio.Lock

        def _summary_now(self) -> float: ...
        async def _begin_summary(self) -> None: ...
        async def _rollback_summary(self) -> None: ...
        async def _ensure_epoch(
            self, session_id: str, now: float
        ) -> tuple[int, int]: ...
        async def _claim_matches(self, claim: ClaimedJob) -> bool: ...

    def _summary_source_lock_for(self, session_id: str) -> asyncio.Lock:
        """返回会话级来源锁，串行化 reset、trim 与外部副作用。"""
        locks = getattr(self, "_summary_source_locks", None)
        if locks is None:
            locks = {}
            setattr(self, "_summary_source_locks", locks)
        lock = locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            locks[session_id] = lock
        return lock

    @asynccontextmanager
    async def _summary_source_locks_for(
        self, session_ids: Sequence[str]
    ) -> AsyncGenerator[None, None]:
        """按固定顺序持有多个会话来源锁，保护批量来源删除。"""
        async with AsyncExitStack() as stack:
            for session_id in sorted({item for item in session_ids if item}):
                await stack.enter_async_context(
                    self._summary_source_lock_for(session_id)
                )
            yield

    @asynccontextmanager
    async def _summary_quarantine_guard(self) -> AsyncGenerator[None, None]:
        """在来源锁之后进入隔离 Store 协调锁。"""
        guard = getattr(getattr(self, "quarantine_store", None), "source_guard", None)
        if callable(guard):
            async with cast(Any, guard()):
                yield
            return
        yield

    @staticmethod
    def _source_fence_from_claim(claim: ClaimedJob) -> SummarySourceFence:
        """把 claim 转为不含正文的不可变来源快照。"""
        return SummarySourceFence(
            job_id=claim.job_id,
            session_id=claim.session_id,
            session_epoch=claim.session_epoch,
            start_seq=claim.start_seq,
            end_seq=claim.end_seq,
            expected_count=claim.expected_count,
            source_digest=claim.source_digest,
            worker_generation=claim.worker_generation,
            claim_token=claim.claim_token,
            scope_key=claim.scope_key or None,
            privacy_level=claim.privacy_level,
            resolver_revision=claim.resolver_revision or None,
            scope_provenance_complete=claim.scope_provenance_complete,
        )

    async def _claim_fence_snapshot(self, claim: ClaimedJob) -> SummarySourceFence:
        """在 Store 写锁内校验 claim，并返回无事务来源快照。"""
        connection = getattr(self, "connection", None)
        if connection is None or not isinstance(claim, ClaimedJob):
            raise RuntimeError(SummaryReasonCode.CLAIM_LOST.value)
        async with self._write_lock:
            if bool(getattr(connection, "in_transaction", False)):
                raise RuntimeError("summary_store_transaction_active")
            if not await self._claim_matches(claim):
                raise RuntimeError(SummaryReasonCode.CLAIM_LOST.value)
            if bool(getattr(connection, "in_transaction", False)):
                raise RuntimeError("summary_store_transaction_active")
            try:
                return self._source_fence_from_claim(claim)
            except (TypeError, ValueError) as error:
                raise RuntimeError(SummaryReasonCode.CLAIM_LOST.value) from error

    async def run_claim_side_effect(
        self,
        claim: ClaimedJob,
        operation: Callable[[], Awaitable[object]],
    ) -> object:
        """在来源锁内运行外部副作用，快照短暂持 Store 写锁。"""
        connection = getattr(self, "connection", None)
        if (
            connection is None
            or not isinstance(claim, ClaimedJob)
            or not callable(operation)
        ):
            raise RuntimeError(SummaryReasonCode.CLAIM_LOST.value)
        async with self._summary_source_lock_for(claim.session_id):
            await self._claim_fence_snapshot(claim)
            result = operation()
            if inspect.isawaitable(result):
                result = await result
            await self._claim_fence_snapshot(claim)
            return result


__all__ = ["SummarySourceFenceMixin"]
