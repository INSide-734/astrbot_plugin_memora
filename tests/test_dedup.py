"""core/dedup/dedup_manager.py 测试 — DedupManager。"""

from __future__ import annotations

import hashlib
import time
from unittest.mock import MagicMock

import pytest

from core.dedup.dedup_manager import DedupManager


class TestBuildDedupKey:
    """验证去重键构造规则。"""

    @pytest.mark.asyncio
    async def test_uses_message_id_when_present(self) -> None:
        """存在非空消息 ID 时应优先构造 ID 键。"""

        event = MagicMock()
        event.message_obj.message_id = "msg_12345"

        key = await DedupManager.build_dedup_key(event, "session_x", "content")
        assert key == "id:session_x:msg_12345"

    @pytest.mark.asyncio
    async def test_strips_message_id(self) -> None:
        """消息 ID 两端空白不应进入去重键。"""

        event = MagicMock()
        event.message_obj.message_id = "  msg_trim  "

        key = await DedupManager.build_dedup_key(event, "s", "c")
        assert key == "id:s:msg_trim"

    @pytest.mark.asyncio
    async def test_message_id_is_scoped_by_platform_and_session(self) -> None:
        """相同消息 ID 必须受平台和 session 共同隔离。"""

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
        """缺少消息 ID 时应退化为 SHA-1 内容指纹。"""

        event = MagicMock()
        event.message_obj.message_id = None
        event.get_sender_id.return_value = "sender_001"
        event.message_obj.timestamp = 1234567890

        key = await DedupManager.build_dedup_key(event, "session_abc", "hello world")
        assert key is not None
        assert key.startswith("fallback:")

        # 验证得到 40 位 SHA-1 十六进制摘要。
        digest_part = key.split(":", 1)[1]
        assert len(digest_part) == 40

    @pytest.mark.asyncio
    async def test_fallback_fingerprint_is_deterministic(self) -> None:
        """相同 fallback 输入应稳定得到相同指纹。"""

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
        """不同正文不得共享 fallback 指纹。"""

        event = MagicMock()
        event.message_obj.message_id = None
        event.get_sender_id.return_value = "sender"
        event.message_obj.timestamp = 0

        key_a = await DedupManager.build_dedup_key(event, "s", "content A")
        key_b = await DedupManager.build_dedup_key(event, "s", "content B")
        assert key_a != key_b

    @pytest.mark.asyncio
    async def test_different_sessions_produce_different_fingerprints(self) -> None:
        """不同 session 不得共享 fallback 指纹。"""

        event = MagicMock()
        event.message_obj.message_id = None
        event.get_sender_id.return_value = "sender"
        event.message_obj.timestamp = 0

        key_a = await DedupManager.build_dedup_key(event, "session_a", "content")
        key_b = await DedupManager.build_dedup_key(event, "session_b", "content")
        assert key_a != key_b

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_message_id(self) -> None:
        """空消息 ID 应继续回退到内容指纹。"""

        event = MagicMock()
        event.message_obj.message_id = ""  # 清理后仍为空

        key = await DedupManager.build_dedup_key(event, "s", "c")
        # 空字符串为假值，因此应落到指纹分支。
        assert key is not None
        assert key.startswith("fallback:")

    @pytest.mark.asyncio
    async def test_handles_missing_get_sender_id(self) -> None:
        """缺少发送者 getter 时仍应生成 fallback 指纹。"""

        event = MagicMock(spec=["message_obj"])
        event.message_obj = MagicMock()
        event.message_obj.message_id = None
        event.message_obj.timestamp = 0

        key = await DedupManager.build_dedup_key(event, "s", "c")
        assert key is not None
        assert key.startswith("fallback:")

    @pytest.mark.asyncio
    async def test_fallback_uses_canonical_sender_override(self) -> None:
        """fallback 指纹应允许主链传入已验证的 canonical sender ID。"""

        event = MagicMock()
        event.message_obj.message_id = None
        event.message_obj.timestamp = 123
        event.get_sender_id.return_value = "可变包装值"

        key = await DedupManager.build_dedup_key(
            event,
            "session",
            "正文",
            sender_id_override="10001",
        )

        expected = hashlib.sha1("session|10001|123|正文".encode("utf-8")).hexdigest()
        assert key == f"fallback:{expected}"


class TestIsDuplicate:
    """验证缓存命中与惰性过期。"""

    @pytest.mark.asyncio
    async def test_none_key_returns_false(self) -> None:
        """空键不应被认定为重复。"""

        mgr = DedupManager()
        result = await mgr.is_duplicate(None)
        assert result is False

    @pytest.mark.asyncio
    async def test_not_in_cache_returns_false(self) -> None:
        """缓存中不存在的键不应被认定为重复。"""

        mgr = DedupManager()
        result = await mgr.is_duplicate("key_not_there")
        assert result is False

    @pytest.mark.asyncio
    async def test_in_cache_returns_true(self) -> None:
        """已标记键在有效期内应被认定为重复。"""

        mgr = DedupManager()
        await mgr.mark_processed("key_1")
        result = await mgr.is_duplicate("key_1")
        assert result is True

    @pytest.mark.asyncio
    async def test_expired_entry_returns_false_and_removes(self) -> None:
        """过期键应返回非重复并从缓存移除。"""

        mgr = DedupManager(ttl=1)  # 一秒生存时间
        await mgr.mark_processed("stale_key")
        # 手动回拨时间以模拟过期。
        mgr._cache["stale_key"] = time.time() - 10  # 十秒前

        result = await mgr.is_duplicate("stale_key")
        assert result is False
        assert "stale_key" not in mgr._cache


class TestMarkProcessed:
    """验证已处理键写入和容量淘汰。"""

    @pytest.mark.asyncio
    async def test_none_key_is_noop(self) -> None:
        """空键标记应为无操作。"""

        mgr = DedupManager()
        await mgr.mark_processed(None)
        assert len(mgr._cache) == 0

    @pytest.mark.asyncio
    async def test_adds_key_to_cache(self) -> None:
        """有效键应写入去重缓存。"""

        mgr = DedupManager()
        await mgr.mark_processed("key_add")
        assert "key_add" in mgr._cache

    @pytest.mark.asyncio
    async def test_evicts_oldest_on_max_size(self) -> None:
        """缓存超限时应淘汰最早写入的键。"""

        mgr = DedupManager(max_size=3)

        # 写入四个条目以触发容量淘汰。
        await mgr.mark_processed("key_1")
        await mgr.mark_processed("key_2")
        await mgr.mark_processed("key_3")
        await mgr.mark_processed("key_4")

        # 最早条目 key_1 应被淘汰。
        assert len(mgr._cache) == 3
        assert "key_1" not in mgr._cache
        assert "key_4" in mgr._cache

    @pytest.mark.asyncio
    async def test_stamps_current_time(self) -> None:
        """写入键时应记录当前时间。"""

        mgr = DedupManager()
        before = time.time()
        await mgr.mark_processed("ts_key")
        after = time.time()
        assert before <= mgr._cache["ts_key"] <= after + 0.1  # 小幅时间容差

    @pytest.mark.asyncio
    async def test_reprocessing_updates_timestamp(self) -> None:
        """重复标记应刷新键的写入时间。"""

        mgr = DedupManager()
        await mgr.mark_processed("dup_key")
        first_ts = mgr._cache["dup_key"]

        # 短暂等待以确保时间戳变化。
        time.sleep(0.01)
        await mgr.mark_processed("dup_key")
        second_ts = mgr._cache["dup_key"]

        # 重复标记会刷新时间；若时钟未推进，允许相等。
        assert second_ts >= first_ts


class TestDedupManagerEndToEnd:
    """验证去重键、检查和标记的端到端协作。"""

    @pytest.mark.asyncio
    async def test_full_deduplication_flow(self) -> None:
        """同一消息第一次可写入，第二次应命中重复。"""

        mgr = DedupManager(max_size=100, ttl=300)

        event = MagicMock()
        event.message_obj.message_id = "msg_e2e_001"
        event.get_sender_id.return_value = "user_1"
        event.message_obj.timestamp = time.time()

        key = await DedupManager.build_dedup_key(event, "sess", "hello")

        # 首次处理不应命中重复。
        assert await mgr.is_duplicate(key) is False

        await mgr.mark_processed(key)

        # 第二次处理应命中重复。
        assert await mgr.is_duplicate(key) is True

    @pytest.mark.asyncio
    async def test_different_keys_are_not_duplicates(self) -> None:
        """不同键不应相互影响重复判断。"""

        mgr = DedupManager()
        await mgr.mark_processed("key_a")
        assert await mgr.is_duplicate("key_a") is True
        assert await mgr.is_duplicate("key_b") is False
