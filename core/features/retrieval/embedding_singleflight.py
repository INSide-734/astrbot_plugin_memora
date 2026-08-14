"""只合并并发中相同 Embedding 请求的能力保持型代理。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class _Flight:
    """单个进行中 Provider 任务及其等待者数量。"""

    task: asyncio.Task[Any]
    waiters: int


class InFlightEmbeddingProviderProxy:
    """保留原 Provider 能力面，并合并并发中的相同调用。"""

    def __init__(self, provider: Any) -> None:
        """冻结 Provider 的实际 Embedding 方法并初始化飞行注册表。"""

        self._provider = provider
        self._inflight: dict[str, _Flight] = {}
        self._lock = asyncio.Lock()
        self._install_embedding_method("get_embeddings")
        self._install_embedding_method("get_embeddings_batch")
        self._install_embedding_method("get_embedding")

    def __getattr__(self, name: str) -> Any:
        """把非 Embedding 属性委托给原 Provider。"""

        return getattr(self._provider, name)

    def _install_embedding_method(self, method_name: str) -> None:
        """仅为原 Provider 实际支持的调用模式安装拦截方法。"""

        original = getattr(self._provider, method_name, None)
        if not callable(original):
            return

        async def coalesced(*args: Any, **kwargs: Any) -> Any:
            """按调用模式与参数摘要合并当前正在执行的相同请求。"""

            key = _call_digest(method_name, args, kwargs)
            return await self._coalesce(
                key,
                lambda: original(*args, **kwargs),
            )

        setattr(self, method_name, coalesced)

    async def _coalesce(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        """加入或创建飞行任务，并隔离单个等待者的取消。"""

        async with self._lock:
            flight = self._inflight.get(key)
            if flight is None:
                flight = _Flight(task=asyncio.create_task(factory()), waiters=1)
                self._inflight[key] = flight
            else:
                flight.waiters += 1

        try:
            return await asyncio.shield(flight.task)
        finally:
            await self._release_waiter(key, flight)

    async def _release_waiter(self, key: str, flight: _Flight) -> None:
        """释放等待者；最后一个等待者离开时取消并收束底层任务。"""

        collect_task: asyncio.Task[Any] | None = None
        async with self._lock:
            current = self._inflight.get(key)
            if current is not flight:
                return
            flight.waiters -= 1
            if flight.waiters <= 0:
                self._inflight.pop(key, None)
                if not flight.task.done():
                    flight.task.cancel()
                    collect_task = flight.task
        if collect_task is not None:
            await asyncio.gather(collect_task, return_exceptions=True)


def _call_digest(
    method_name: str,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
) -> str:
    """把调用模式和输入参数转换为不含原文的稳定 SHA-256 摘要。"""

    serialized = json.dumps(
        {
            "method": method_name,
            "args": args,
            "kwargs": sorted(kwargs.items()),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=repr,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


__all__ = ["InFlightEmbeddingProviderProxy"]
