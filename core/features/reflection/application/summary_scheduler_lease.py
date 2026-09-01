"""总结调度器的 claim 租约心跳辅助。"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from ..domain.summary_models import ClaimedJob, WindowOutcome


class SummarySchedulerLeaseMixin:
    """为总结调度器提供长任务 lease 续期。"""

    _job_store: Any
    _worker: Any
    _lease_seconds: float

    def _now(self) -> Any: ...

    async def _execute_with_heartbeat(self, claim: ClaimedJob) -> WindowOutcome:
        """执行窗口 worker，并在长调用期间续租当前 claim。"""

        renew = getattr(self._job_store, "renew_claim", None)
        heartbeat: asyncio.Task[None] | None = None
        if callable(renew):
            heartbeat = asyncio.create_task(
                self._heartbeat_claim(claim), name="memora-summary-lease-heartbeat"
            )
        try:
            return await self._worker.execute(claim)
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat_claim(self, claim: ClaimedJob) -> None:
        """在长时间 Provider 调用期间周期性续租，失去 fencing 即停止。"""

        renew = getattr(self._job_store, "renew_claim", None)
        if not callable(renew):
            return
        interval = max(0.1, self._lease_seconds / 3.0)
        while True:
            await asyncio.sleep(interval)
            renewed = renew(claim, max(1, int(self._lease_seconds)), now=self._now())
            if inspect.isawaitable(renewed):
                renewed = await renewed
            if renewed is not True:
                return


__all__ = ["SummarySchedulerLeaseMixin"]
