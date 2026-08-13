"""实时 SSE API 和辅助函数测试。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from starlette.responses import StreamingResponse

from core.page_api import PluginPageApi
from core.platform.transport.page_api.realtime_api import RealtimeSSE


class TestRealtimeSSE:
    @pytest.mark.asyncio
    async def test_publish_removes_full_clients_and_delivers_payload(self) -> None:
        sse = RealtimeSSE(memory_engine=MagicMock())
        dead_cid, dead_queue = sse.register()
        live_cid, live_queue = sse.register()

        for i in range(dead_queue.maxsize):
            dead_queue.put_nowait(f"blocking-{i}")

        await sse.publish("memory_created", {"doc_id": 42})

        assert dead_cid not in sse._queues
        assert live_cid in sse._queues

        payload = await asyncio.wait_for(live_queue.get(), timeout=0.1)
        parsed = json.loads(payload)
        assert parsed["event"] == "memory_created"
        assert parsed["data"] == {"doc_id": 42}
        assert "ts" in parsed

    @pytest.mark.asyncio
    async def test_stream_emits_heartbeat_and_unregisters_on_close(self) -> None:
        sse = RealtimeSSE(memory_engine=MagicMock())
        sse.HEARTBEAT_SEC = 0.01

        response = await sse.stream()

        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/event-stream"
        assert response.headers["Cache-Control"] == "no-cache"
        assert sse.connected == 1

        first_chunk = await asyncio.wait_for(
            response.body_iterator.__anext__(), timeout=0.1
        )
        assert first_chunk == ": heartbeat\n\n"

        await response.body_iterator.aclose()
        assert sse.connected == 0

    @pytest.mark.asyncio
    async def test_stream_propagates_cancellation_and_unregisters(self) -> None:
        sse = RealtimeSSE(memory_engine=MagicMock())
        response = await sse.stream()
        next_chunk = asyncio.create_task(response.body_iterator.__anext__())
        await asyncio.sleep(0)

        next_chunk.cancel()

        with pytest.raises(asyncio.CancelledError):
            await next_chunk
        assert sse.connected == 0


class TestPageApiSseStream:
    @pytest.mark.asyncio
    async def test_sse_stream_when_initializer_missing_returns_error(self) -> None:
        plugin = MagicMock(spec=[])
        api = PluginPageApi(plugin)

        result = await api.sse_stream()

        assert result["status"] == "error"
        assert "SSE" in result["message"]
