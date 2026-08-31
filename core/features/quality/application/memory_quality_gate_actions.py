"""隔离候选人工处置的状态机动作。"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any


class MemoryQualityGateActionsMixin:
    """提供隔离候选的人工修复与拒绝动作。"""

    if TYPE_CHECKING:
        store: Any

        def _repair_source_guard(
            self, candidate: dict[str, Any]
        ) -> AbstractAsyncContextManager[bool]: ...

    async def repair_blocked(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        actor_id: str | None,
        confirm_canonical_absent: bool,
    ) -> dict[str, Any]:
        """管理员确认 canonical 缺失后安全退回 blocked。"""
        if confirm_canonical_absent is not True:
            raise ValueError("quarantine_canonical_absence_confirmation_required")
        current = await self.store.get_candidate(candidate_id)
        if current is None:
            raise KeyError("quarantine_candidate_not_found")
        if current["status"] != "approving":
            raise ValueError("quarantine_status_conflict")
        if current.get("canonical_memory_id") is not None:
            raise ValueError("quarantine_canonical_presence_conflict")
        async with self._repair_source_guard(current):
            return await self.store.block_approval(
                candidate_id,
                expected_revision=expected_revision,
                actor_id=actor_id,
                reason_code="canonical_write_not_found_confirmed",
            )

    async def reject(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        actor_id: str | None,
    ) -> dict[str, Any]:
        """拒绝候选并保留 ConversationStore 中的原始消息证据。"""
        return await self.store.reject(
            candidate_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
        )


__all__ = ["MemoryQualityGateActionsMixin"]
