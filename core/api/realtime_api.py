"""D4：实时记忆流 —— 用于控制台实时更新的 SSE 端点。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.api.web import stream_response

if TYPE_CHECKING:
    from ..managers.memory_engine import MemoryEngine


class RealtimeSSE:
    """管理 SSE 连接与事件广播，为实时记忆流提供服务。"""

    HEARTBEAT_SEC = 30

    def __init__(self, memory_engine: MemoryEngine) -> None:
        self._engine = memory_engine
        self._queues: dict[str, asyncio.Queue] = {}
        self._counter = 0

    def register(self) -> tuple[str, asyncio.Queue]:
        self._counter += 1
        cid = f"sse_{self._counter}_{time.time():.0f}"
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._queues[cid] = q
        logger.debug(f"[SSE] client {cid} registered (total={len(self._queues)})")
        return cid, q

    def unregister(self, cid: str) -> None:
        self._queues.pop(cid, None)

    async def publish(self, event_type: str, data: dict) -> None:
        payload = json.dumps(
            {"event": event_type, "data": data, "ts": time.time()},
            ensure_ascii=False,
            default=str,
        )
        dead = [cid for cid, q in self._queues.items() if self._try_put(q, payload)]
        for cid in dead:
            self.unregister(cid)

    @staticmethod
    def _try_put(q: asyncio.Queue, payload: str) -> bool:
        """若队列已满则返回 True（表示应移除该客户端）。"""
        try:
            q.put_nowait(payload)
            return False
        except asyncio.QueueFull:
            return True

    async def stream(self):
        """通过 AstrBot 公共响应工厂返回 SSE 流。"""
        cid, q = self.register()

        async def event_generator():
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(
                            q.get(), timeout=self.HEARTBEAT_SEC
                        )
                        yield f"data: {msg}\n\n"
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
            except GeneratorExit:
                pass
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
        return len(self._queues)


__all__ = ["RealtimeSSE"]
