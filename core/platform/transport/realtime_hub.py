"""与具体 Web 框架无关的实时发布 Hub。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from ...shared.contracts.ports import RealtimePublisher


class HubState(StrEnum):
    """Hub 生命周期状态。"""

    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


class RealtimeHubClosed(RuntimeError):
    """Hub 已关闭，不能建立新订阅。"""


class RealtimeHub(RealtimePublisher):
    """管理有界订阅队列，并提供幂等关闭和关闭 sentinel。

    Hub 不拥有 Web response，也不启动 heartbeat；SSE/Page 适配器负责把队列
    转成宿主公开的 ``stream_response``。关闭时先拒绝新订阅，再清空每个队列
    并放入同一个 sentinel，确保等待中的 generator 能被唤醒。
    """

    QUEUE_SIZE = 256
    CLOSE_SENTINEL = object()

    def __init__(
        self,
        *,
        queue_size: int = QUEUE_SIZE,
        client_prefix: str = "hub",
    ) -> None:
        """创建尚未关闭的 Hub。"""

        if (
            isinstance(queue_size, bool)
            or not isinstance(queue_size, int)
            or queue_size <= 0
        ):
            raise ValueError("queue_size_invalid")
        if not isinstance(client_prefix, str) or not client_prefix.strip():
            raise ValueError("client_prefix_invalid")
        self._queue_size = queue_size
        self._client_prefix = client_prefix.strip()
        self._queues: dict[str, asyncio.Queue[Any]] = {}
        self._counter = 0
        self._state = HubState.OPEN
        self._close_lock = asyncio.Lock()

    @property
    def state(self) -> HubState:
        """返回 Hub 当前状态。"""

        return self._state

    @property
    def closed(self) -> bool:
        """返回 Hub 是否已进入最终关闭状态。"""

        return self._state is HubState.CLOSED

    @property
    def connected(self) -> int:
        """返回当前订阅数量。"""

        return len(self._queues)

    @property
    def queues(self) -> Mapping[str, asyncio.Queue[Any]]:
        """返回只读视图，供 transport adapter 取得订阅队列。"""

        return self._queues

    def subscribe(self) -> tuple[str, asyncio.Queue[Any]]:
        """创建订阅；closing/closed 状态拒绝新客户端。"""

        if self._state is not HubState.OPEN:
            raise RealtimeHubClosed("realtime_hub_closed")
        self._counter += 1
        client_id = f"{self._client_prefix}_{self._counter}_{time.time():.0f}"
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._queue_size)
        self._queues[client_id] = queue
        return client_id, queue

    register = subscribe

    def unsubscribe(self, client_id: str) -> None:
        """移除单个订阅；重复移除是幂等的。"""

        self._queues.pop(client_id, None)

    unregister = unsubscribe

    async def publish(self, event_type: str, data: Mapping[str, Any]) -> bool:
        """发布 JSON 标量事件，返回是否投递给至少一个客户端。"""

        if self._state is not HubState.OPEN:
            return False
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type_required")
        if not isinstance(data, Mapping):
            raise TypeError("event_data_mapping_required")
        payload = json.dumps(
            {"event": event_type, "data": dict(data), "ts": time.time()},
            ensure_ascii=False,
            default=str,
        )
        delivered = False
        dead: list[str] = []
        for client_id, queue in tuple(self._queues.items()):
            try:
                queue.put_nowait(payload)
                delivered = True
            except asyncio.QueueFull:
                dead.append(client_id)
        for client_id in dead:
            self.unsubscribe(client_id)
        return delivered

    async def close(self) -> None:
        """进入 closing、唤醒所有订阅并最终转为 closed；可重复调用。"""

        async with self._close_lock:
            if self._state is HubState.CLOSED:
                return
            self._state = HubState.CLOSING
            queues = tuple(self._queues.values())
            self._queues.clear()
            for queue in queues:
                while True:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                try:
                    queue.put_nowait(self.CLOSE_SENTINEL)
                except asyncio.QueueFull:
                    pass
            self._state = HubState.CLOSED

    async def drain(self) -> None:
        """关闭并唤醒订阅者，作为 runtime ownership 的语义别名。"""

        await self.close()


__all__ = ["HubState", "RealtimeHub", "RealtimeHubClosed"]
