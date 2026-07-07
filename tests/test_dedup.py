"""core/dedup/dedup_manager.py 测试 — DedupManager。"""

from __future__ import annotations

import hashlib
import time
from unittest.mock import MagicMock

import pytest

from core.dedup.dedup_manager import DedupManager


class TestBuildDedupKey:

    @pytest.mark.asyncio
    async def test_uses_message_id_when_present(self) -> None:
        event = MagicMock()
        event.message_obj.message_id = "msg_12345"

        key = await DedupManager.build_dedup_key(event, "session_x", "content")
        assert key == "id:session_x:msg_12345"

    @pytest.mark.asyncio
    async def test_strips_message_id(self) -> None:
        event = MagicMock()
        event.message_obj.message_id = "  msg_trim  "

        key = await DedupManager.build_dedup_key(event, "s", "c")
        assert key == "id:s:msg_trim"

    @pytest.mark.asyncio
    async def test_message_id_is_scoped_by_platform_and_session(self) -> None:
        event = MagicMock()
        event.message_obj.message_id = "42"
        event.get_platform_name.return_value = "aiocqhttp"

        key_a = await DedupManager.build_dedup_key(event, "group_a", "content")
        key_b = await DedupManager.build_dedup_key(event, "group_b", "content")

        assert key_a == "id:aiocqhttp:group_a:42"
        assert key_b == "id:aiocqhttp:group_b:42"
        assert key_a != key_b

    @pytest.mark.asyncio
    async def test_falls_back_to_fingerprint_when_no_message_id(self) -> None:
        event = MagicMock()
        event.message_obj.message_id = None
        event.get_sender_id.return_value = "sender_001"
        event.message_obj.timestamp = 1234567890

        key = await DedupManager.build_dedup_key(event, "session_abc", "hello world")
        assert key is not None
        assert key.startswith("fallback:")

        # Verify it's a valid sha1 hex digest (40 chars)
        digest_part = key.split(":", 1)[1]
        assert len(digest_part) == 40

    @pytest.mark.asyncio
    async def test_fallback_fingerprint_is_deterministic(self) -> None:
        for _ in range(3):
            event = MagicMock()
            event.message_obj.message_id = None
            event.get_sender_id.return_value = "sender_A"
            event.message_obj.timestamp = 999

            key1 = await DedupManager.build_dedup_key(event, "sess", "same content")
            key2 = await DedupManager.build_dedup_key(event, "sess", "same content")
            assert key1 == key2

    @pytest.mark.asyncio
    async def test_different_content_produces_different_fingerprint(self) -> None:
        event = MagicMock()
        event.message_obj.message_id = None
        event.get_sender_id.return_value = "sender"
        event.message_obj.timestamp = 0

        key_a = await DedupManager.build_dedup_key(event, "s", "content A")
        key_b = await DedupManager.build_dedup_key(event, "s", "content B")
        assert key_a != key_b

    @pytest.mark.asyncio
    async def test_different_sessions_produce_different_fingerprints(self) -> None:
        event = MagicMock()
        event.message_obj.message_id = None
        event.get_sender_id.return_value = "sender"
        event.message_obj.timestamp = 0

        key_a = await DedupManager.build_dedup_key(event, "session_a", "content")
        key_b = await DedupManager.build_dedup_key(event, "session_b", "content")
        assert key_a != key_b

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_message_id(self) -> None:
        event = MagicMock()
        event.message_obj.message_id = ""  # empty after strip

        key = await DedupManager.build_dedup_key(event, "s", "c")
        # Empty string is falsy, so should fall through to fingerprint
        assert key is not None
        assert key.startswith("fallback:")

    @pytest.mark.asyncio
    async def test_handles_missing_get_sender_id(self) -> None:
        event = MagicMock(spec=["message_obj"])
        event.message_obj = MagicMock()
        event.message_obj.message_id = None
        event.message_obj.timestamp = 0

        key = await DedupManager.build_dedup_key(event, "s", "c")
        assert key is not None
        assert key.startswith("fallback:")


class TestIsDuplicate:

    @pytest.mark.asyncio
    async def test_none_key_returns_false(self) -> None:
        mgr = DedupManager()
        result = await mgr.is_duplicate(None)
        assert result is False

    @pytest.mark.asyncio
    async def test_not_in_cache_returns_false(self) -> None:
        mgr = DedupManager()
        result = await mgr.is_duplicate("key_not_there")
        assert result is False

    @pytest.mark.asyncio
    async def test_in_cache_returns_true(self) -> None:
        mgr = DedupManager()
        await mgr.mark_processed("key_1")
        result = await mgr.is_duplicate("key_1")
        assert result is True

    @pytest.mark.asyncio
    async def test_expired_entry_returns_false_and_removes(self) -> None:
        mgr = DedupManager(ttl=1)  # 1 second TTL
        await mgr.mark_processed("stale_key")
        # Manually backdate the timestamp to simulate expiry
        mgr._cache["stale_key"] = time.time() - 10  # 10 seconds ago

        result = await mgr.is_duplicate("stale_key")
        assert result is False
        assert "stale_key" not in mgr._cache


class TestMarkProcessed:

    @pytest.mark.asyncio
    async def test_none_key_is_noop(self) -> None:
        mgr = DedupManager()
        await mgr.mark_processed(None)
        assert len(mgr._cache) == 0

    @pytest.mark.asyncio
    async def test_adds_key_to_cache(self) -> None:
        mgr = DedupManager()
        await mgr.mark_processed("key_add")
        assert "key_add" in mgr._cache

    @pytest.mark.asyncio
    async def test_evicts_oldest_on_max_size(self) -> None:
        mgr = DedupManager(max_size=3)

        # Add 4 entries
        await mgr.mark_processed("key_1")
        await mgr.mark_processed("key_2")
        await mgr.mark_processed("key_3")
        await mgr.mark_processed("key_4")

        # Should have evicted the oldest one (key_1)
        assert len(mgr._cache) == 3
        assert "key_1" not in mgr._cache
        assert "key_4" in mgr._cache

    @pytest.mark.asyncio
    async def test_stamps_current_time(self) -> None:
        mgr = DedupManager()
        before = time.time()
        await mgr.mark_processed("ts_key")
        after = time.time()
        assert before <= mgr._cache["ts_key"] <= after + 0.1  # small tolerance

    @pytest.mark.asyncio
    async def test_reprocessing_updates_timestamp(self) -> None:
        mgr = DedupManager()
        await mgr.mark_processed("dup_key")
        first_ts = mgr._cache["dup_key"]

        # Small wait to ensure timestamp changes
        time.sleep(0.01)
        await mgr.mark_processed("dup_key")
        second_ts = mgr._cache["dup_key"]

        # Even re-marking replaces; the new timestamp should be >= old
        # (Or it could be exactly the same if time didn't advance — use >=)
        assert second_ts >= first_ts


class TestDedupManagerEndToEnd:

    @pytest.mark.asyncio
    async def test_full_deduplication_flow(self) -> None:
        mgr = DedupManager(max_size=100, ttl=300)

        event = MagicMock()
        event.message_obj.message_id = "msg_e2e_001"
        event.get_sender_id.return_value = "user_1"
        event.message_obj.timestamp = time.time()

        key = await DedupManager.build_dedup_key(event, "sess", "hello")

        # First time: not duplicate
        assert await mgr.is_duplicate(key) is False

        await mgr.mark_processed(key)

        # Second time: duplicate
        assert await mgr.is_duplicate(key) is True

    @pytest.mark.asyncio
    async def test_different_keys_are_not_duplicates(self) -> None:
        mgr = DedupManager()
        await mgr.mark_processed("key_a")
        assert await mgr.is_duplicate("key_a") is True
        assert await mgr.is_duplicate("key_b") is False
