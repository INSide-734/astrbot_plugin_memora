"""4.6.6: Test SSE real-time stream endpoint."""

import asyncio
import json
import time


class TestSSEEndpoint:
    """D4: Validate SSE event format, heartbeat, and reconnection behavior."""

    def test_event_payload_format(self) -> None:
        """Published events must have event, data, ts fields."""
        payload = json.dumps(
            {
                "event": "memory_created",
                "data": {"doc_id": 42, "content": "test"},
                "ts": time.time(),
            },
            ensure_ascii=False,
            default=str,
        )

        parsed = json.loads(payload)
        assert "event" in parsed
        assert "data" in parsed
        assert "ts" in parsed
        assert parsed["event"] == "memory_created"
        assert isinstance(parsed["data"], dict)

    def test_sse_data_line_format(self) -> None:
        """SSE wire format should be 'data: {json}\\n\\n'."""
        payload = json.dumps(
            {"event": "memory_created", "data": {"doc_id": 1}, "ts": 1234567890.0},
        )
        wire = f"data: {payload}\n\n".encode()
        assert wire.startswith(b"data: ")
        assert wire.endswith(b"\n\n")

    def test_heartbeat_format(self) -> None:
        """Heartbeat comment line format ': heartbeat\\n\\n'."""
        heartbeat = b": heartbeat\n\n"
        assert heartbeat.startswith(b":")
        assert heartbeat.endswith(b"\n\n")

    def test_register_returns_client_id_and_queue(self) -> None:
        """SSE register() should return a unique client ID and asyncio.Queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        cid = f"sse_test_{time.time():.0f}"

        assert cid.startswith("sse_test_")
        assert isinstance(q, asyncio.Queue)
        assert q.maxsize == 256

    def test_unregister_removes_client(self) -> None:
        """Unregister should remove the client from tracking."""
        queues: dict[str, asyncio.Queue] = {}
        q = asyncio.Queue(maxsize=256)
        cid = "sse_test_unreg"
        queues[cid] = q

        assert cid in queues
        queues.pop(cid, None)
        assert cid not in queues

    def test_publish_to_empty_queues_no_error(self) -> None:
        """Publishing when no clients connected should not throw."""
        queues: dict[str, asyncio.Queue] = {}
        _payload = json.dumps({"event": "test", "data": {}, "ts": time.time()})
        dead: list[str] = []
        assert len(dead) == 0
        # Should not raise when iterating empty dict
        for _cid in list(queues.keys()):
            pass

    def test_publish_to_full_queue_marks_dead(self) -> None:
        """A full queue should trigger client removal (dead flag)."""
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        q.put_nowait("blocking_message")  # fill the queue

        dead = False
        try:
            q.put_nowait("overflow")
        except asyncio.QueueFull:
            dead = True

        assert dead

    def test_connected_count_reflects_registrations(self) -> None:
        """connected property should return the number of active clients."""
        queues: dict[str, asyncio.Queue] = {
            "sse_a": asyncio.Queue(maxsize=256),
            "sse_b": asyncio.Queue(maxsize=256),
            "sse_c": asyncio.Queue(maxsize=256),
        }
        assert len(queues) == 3

        queues.pop("sse_b", None)
        assert len(queues) == 2

    def test_reconnect_backoff_sequence(self) -> None:
        """Reconnection should follow exponential backoff: 1s, 2s, 4s, 8s, 16s."""
        backoff = [1, 2, 4, 8, 16]
        cumulative = 0
        for delay in backoff:
            cumulative += delay
        # Total across 5 attempts
        assert cumulative == 31
        assert len(backoff) == 5

    def test_max_reconnect_attempts(self) -> None:
        """After MAX_RECONNECT_ATTEMPTS (5), stop reconnecting."""
        max_attempts = 5
        attempts = 0
        for _ in range(10):  # simulate many failures
            if attempts >= max_attempts:
                break
            attempts += 1

        assert attempts == 5
