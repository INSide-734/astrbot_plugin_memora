"""D4：实时记忆流的 AstrBot SSE 传输适配器。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.api.web import stream_response

from ..realtime_hub import RealtimeHub, RealtimeHubClosed

if TYPE_CHECKING:
    from ....managers.memory_engine import MemoryEngine


class RealtimeSSE:
    """把共享 ``RealtimeHub`` 队列转换为 AstrBot 公共 SSE 响应。"""

    HEARTBEAT_SEC = 30

    def __init__(
        self,
        memory_engine: MemoryEngine,
        *,
        hub: RealtimeHub | None = None,
    ) -> None:
        """创建兼容 SSE 适配器，队列生命周期由共享 Hub 管理。"""

        self._engine = memory_engine
        self._hub = hub or RealtimeHub(client_prefix="sse")
        # 旧测试和少量兼容调用方读取该属性；真实状态由 Hub 唯一持有。
        self._queues = self._hub.queues

    def register(self) -> tuple[str, asyncio.Queue]:
        """注册 SSE 客户端，关闭中的 Hub 会抛出稳定错误。"""

        cid, queue = self._hub.subscribe()
        logger.debug(f"[SSE] client {cid} registered (total={len(self._queues)})")
        return cid, queue

    def unregister(self, cid: str) -> None:
        """移除 SSE 客户端，重复移除安全。"""

        self._hub.unsubscribe(cid)

    async def publish(self, event_type: str, data: dict) -> bool:
        """发布事件；关闭后返回 ``False`` 而不重建队列。"""

        return await self._hub.publish(event_type, data)

    @staticmethod
    def _try_put(q: asyncio.Queue, payload: str) -> bool:
        """兼容旧调用方的有界队列探针。"""

        try:
            q.put_nowait(payload)
            return False
        except asyncio.QueueFull:
            return True

    async def stream(self):
        """通过 AstrBot 公共流式响应返回 SSE 流。"""

        try:
            cid, queue = self.register()
        except RealtimeHubClosed:
            return {"status": "error", "message": "SSE 服务正在关闭"}

        async def event_generator():
            try:
                while True:
                    try:
                        message = await asyncio.wait_for(
                            queue.get(), timeout=self.HEARTBEAT_SEC
                        )
                        if message is self._hub.CLOSE_SENTINEL:
                            return
                        yield f"data: {message}\n\n"
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                self.unregister(cid)

        return stream_response(
            event_generator(),
            content_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @property
    def connected(self) -> int:
        """返回当前 SSE 订阅数量。"""

        return self._hub.connected

    @property
    def state(self):
        """返回共享 Hub 生命周期状态。"""

        return self._hub.state

    async def close(self) -> None:
        """关闭共享 Hub；重复关闭安全。"""

        await self._hub.close()


__all__ = ["RealtimeSSE"]
