"""实时 SSE API 和辅助函数测试。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.api.realtime_api import RealtimeSSE
from core.page_api import PluginPageApi


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

        async def fake_make_response(generator, headers):
            return SimpleNamespace(response=generator, headers=headers, timeout="preset")

        with patch("core.api.realtime_api.make_response", fake_make_response):
            response = await sse.stream()

        assert response.headers["Content-Type"] == "text/event-stream"
        assert response.headers["Cache-Control"] == "no-cache"
        assert response.timeout is None
        assert sse.connected == 1

        first_chunk = await asyncio.wait_for(response.response.__anext__(), timeout=0.1)
        assert first_chunk == ": heartbeat\n\n"

        await response.response.aclose()
        assert sse.connected == 0


class TestPageApiSseStream:
    @pytest.mark.asyncio
    async def test_sse_stream_when_initializer_missing_returns_error(self) -> None:
        plugin = MagicMock(spec=[])
        api = PluginPageApi(plugin)

        result = await api.sse_stream()

        assert result["status"] == "error"
        assert "SSE" in result["message"]
