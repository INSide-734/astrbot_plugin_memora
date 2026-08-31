"""总结 worker 的跨库 owner reconcile 辅助。"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ..domain.summary_models import CandidateIntent, ClaimedJob

if TYPE_CHECKING:
    from ..domain.summary_ports import SummaryJobStorePort


class SummaryWorkerReconcileMixin:
    """将 canonical owner 映射交给窄 Store port，不携带正文或会话投影。"""

    if TYPE_CHECKING:
        _job_store: SummaryJobStorePort

    async def _reconcile_discovered_owners(
        self,
        claim: ClaimedJob,
        candidates: Sequence[dict[str, Any]],
        intents: Sequence[CandidateIntent],
        owners: Mapping[str, int],
    ) -> bool:
        """将幂等索引发现的 owner 先写入 slot ledger，失败则停止新写入。"""
        if not owners:
            return True
        candidate_keys = {
            str(candidate.get("metadata", {}).get("idempotency_key") or "")
            for candidate in candidates
        }
        if any(key not in candidate_keys for key in owners):
            return False
        if any(
            isinstance(owner, bool) or not isinstance(owner, int) or owner <= 0
            for owner in owners.values()
        ):
            return False
        reconcile = getattr(self._job_store, "reconcile_window", None)
        if not callable(reconcile):
            return False
        mapping = {
            intent.slot: owners[key]
            for intent, candidate in zip(intents, candidates, strict=True)
            if (key := str(candidate.get("metadata", {}).get("idempotency_key") or ""))
            in owners
        }
        if not mapping:
            return True
        try:

            async def _reconcile() -> object:
                """在 claim/source fence 内登记已发现的 canonical owner。"""

                result = reconcile(claim, mapping)
                if inspect.isawaitable(result):
                    return await result
                return result

            result = await self.run_claim_side_effect(claim, _reconcile)
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        status = getattr(result, "status", None)
        status_value = getattr(status, "value", status)
        return bool(getattr(result, "accepted", False) and status_value != "unknown")

    async def run_claim_side_effect(
        self,
        claim: ClaimedJob,
        operation: Callable[[], Awaitable[object]],
    ) -> object:
        """通过 Store 的窄 runner 执行质量门、隔离或 canonical 副作用。"""
        runner = getattr(self._job_store, "run_claim_side_effect", None)
        if not callable(runner):
            raise RuntimeError("summary_claim_fence_unavailable")
        result = runner(claim, operation)
        if inspect.isawaitable(result):
            return await result
        return result


__all__ = ["SummaryWorkerReconcileMixin"]
