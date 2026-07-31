"""本地检索与受绝对截止时间约束的向量检索协作。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

TLocal = TypeVar("TLocal")
TVector = TypeVar("TVector")


async def run_local_and_bounded_vector(
    local_factory: Callable[[], Awaitable[TLocal]],
    vector_factory: Callable[[], Awaitable[TVector]],
    *,
    deadline_monotonic: float | None,
) -> tuple[TLocal, TVector | None, bool]:
    """并发执行本地与向量路，只对向量路应用共享绝对截止时间。

    本地路始终执行。截止时间已经耗尽时不会创建向量协程；调用方取消时，
    所有已创建的子任务都会被取消并收束后再传播 ``CancelledError``。
    """

    local_task = asyncio.create_task(local_factory())
    vector_task: asyncio.Task[TVector] | None = None
    vector_wait_task: asyncio.Task[tuple[TVector | None, bool]] | None = None
    try:
        if deadline_monotonic is not None and deadline_monotonic <= time.perf_counter():
            return await local_task, None, True

        vector_task = asyncio.create_task(vector_factory())
        if deadline_monotonic is None:
            local_result, vector_result = await asyncio.gather(
                local_task,
                vector_task,
            )
            return local_result, vector_result, False

        async def wait_for_vector() -> tuple[TVector | None, bool]:
            """等待向量任务到绝对截止时间，并收束超时取消。"""

            remaining = max(0.0, deadline_monotonic - time.perf_counter())
            try:
                return await asyncio.wait_for(vector_task, timeout=remaining), False
            except TimeoutError:
                await asyncio.gather(vector_task, return_exceptions=True)
                return None, True

        vector_wait_task = asyncio.create_task(wait_for_vector())
        local_result, (vector_result, timed_out) = await asyncio.gather(
            local_task,
            vector_wait_task,
        )
        return local_result, vector_result, timed_out
    except asyncio.CancelledError:
        await _cancel_and_collect(local_task, vector_wait_task, vector_task)
        raise
    except Exception:
        await _cancel_and_collect(local_task, vector_wait_task, vector_task)
        raise


async def _cancel_and_collect(*tasks: asyncio.Task | None) -> None:
    """取消并收束仍在运行的唯一任务集合。"""

    unique_tasks = list(dict.fromkeys(task for task in tasks if task is not None))
    for task in unique_tasks:
        if not task.done():
            task.cancel()
    if unique_tasks:
        await asyncio.gather(*unique_tasks, return_exceptions=True)


__all__ = ["run_local_and_bounded_vector"]
