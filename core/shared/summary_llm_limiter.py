"""总结链路共享的进程内物理 LLM 并发限流原语。"""

from __future__ import annotations

import asyncio


class SummaryLlmLimiter:
    """使用有界信号量限制总结链路的物理 LLM 调用并发数。"""

    __slots__ = ("_capacity", "_active", "_semaphore")

    def __init__(self, capacity: int) -> None:
        """创建限流器。

        参数:
            capacity: 允许同时进行的物理 LLM 调用数，必须为正整数。

        异常:
            ValueError: ``capacity`` 不是正整数。
        """

        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("limiter capacity must be a positive integer")
        self._capacity = capacity
        self._active = 0
        self._semaphore = asyncio.BoundedSemaphore(capacity)

    @property
    def capacity(self) -> int:
        """返回限流器容量这一安全标量。"""

        return self._capacity

    @property
    def active(self) -> int:
        """返回当前持有 permit 的调用数这一安全标量。"""

        return self._active

    async def acquire(self) -> None:
        """等待并持有一个物理 LLM 调用 permit。"""

        await self._semaphore.acquire()
        self._active += 1

    def release(self) -> None:
        """释放一个物理 LLM 调用 permit。"""

        if self._active < 1:
            raise RuntimeError("limiter permit released without acquire")
        self._active -= 1
        self._semaphore.release()

    async def __aenter__(self) -> "SummaryLlmLimiter":
        """异步获取 permit 并返回当前限流器。"""

        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> bool:
        """退出调用范围时释放 permit，并继续传播异常或取消。"""

        self.release()
        return False


__all__ = ["SummaryLlmLimiter"]
