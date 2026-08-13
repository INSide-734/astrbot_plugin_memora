"""测试 message_operations — MessageOperationsMixin add/get/context 方法。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.conversation.application.message_operations import (
    MessageOperationsMixin,
)
from core.models.conversation_models import Message

# ---------------------------------------------------------------------------
# 具体测试类
# ---------------------------------------------------------------------------


class _TestMsgManager(MessageOperationsMixin):
    """提供 Mixin 所需依赖的具体类。"""

    def __init__(self, store=None, cache=None):
        self.store = store or MagicMock()
        self._cache = cache or {}
        self._cache_lock = MagicMock()
        self.context_window_size = 20

    async def _get_from_cache(self, session_id):
        """返回缓存中的消息，若无则返回 None。"""
        if session_id in self._cache:
            msgs, _ = self._cache[session_id]
            return msgs
        return None

    async def _update_cache(self, session_id, messages):
        """将消息存入缓存。"""
        self._cache[session_id] = [messages, 0.0]


# ---------------------------------------------------------------------------
# add_message 测试
# ---------------------------------------------------------------------------


class TestAddMessage:
    """add_message 方法测试。"""

    @pytest.fixture
    def mgr(self) -> _TestMsgManager:
        store = MagicMock()
        store.add_message = AsyncMock(return_value=42)
        store.get_session = AsyncMock(return_value=MagicMock(message_count=5))
        return _TestMsgManager(store=store)

    @pytest.mark.asyncio
    async def test_basic_message_creation(self, mgr: _TestMsgManager) -> None:
        """消息创建并存储到数据库中。"""
        msg = await mgr.add_message(
            session_id="s1",
            role="user",
            content="Hello world",
            sender_id="user-1",
            sender_name="Alice",
            platform="qq",
        )
        assert msg is not None
        assert msg.id == 42
        assert msg.session_id == "s1"
        assert msg.role == "user"
        assert msg.content == "Hello world"
        mgr.store.add_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_sender_id_fallback_to_session(self, mgr: _TestMsgManager) -> None:
        """未提供 sender_id 时，使用 session_id 作为回退。"""
        msg = await mgr.add_message(session_id="s1", role="user", content="test")
        assert msg.sender_id == "s1"

    @pytest.mark.asyncio
    async def test_bot_message_metadata(self, mgr: _TestMsgManager) -> None:
        """Bot 消息会附带 is_bot_message 元数据。"""
        msg = await mgr.add_message(
            session_id="s1",
            role="assistant",
            content="response",
            sender_id="bot-1",
            is_bot_message=True,
        )
        assert msg.metadata == {"is_bot_message": True}

    @pytest.mark.asyncio
    async def test_user_message_no_bot_metadata(self, mgr: _TestMsgManager) -> None:
        """用户消息不会附带 is_bot_message 元数据。"""
        msg = await mgr.add_message(
            session_id="s1",
            role="user",
            content="hello",
            sender_id="user-1",
        )
        assert msg.metadata == {}

    @pytest.mark.asyncio
    async def test_group_message(self, mgr: _TestMsgManager) -> None:
        """群组消息包含 group_id。"""
        msg = await mgr.add_message(
            session_id="group-123",
            role="user",
            content="test",
            sender_id="user-1",
            group_id="group-123",
        )
        assert msg.group_id == "group-123"

    @pytest.mark.asyncio
    async def test_cache_invalidated_on_add(self, mgr: _TestMsgManager) -> None:
        """添加消息将从缓存中清除该会话。"""
        mgr._cache["s1"] = [MagicMock()]
        await mgr.add_message(session_id="s1", role="user", content="test")
        assert "s1" not in mgr._cache


# ---------------------------------------------------------------------------
# get_messages 测试
# ---------------------------------------------------------------------------


class TestGetMessages:
    """get_messages 方法测试。"""

    def _make_mgr(self) -> _TestMsgManager:
        store = MagicMock()
        store.get_messages = AsyncMock()
        return _TestMsgManager(store=store)

    @pytest.mark.asyncio
    async def test_from_database(self) -> None:
        """未命中缓存时从数据库获取消息。"""
        mgr = self._make_mgr()
        msgs = [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="a",
                sender_id="u1",
                platform="test",
            ),
            Message(
                id=2,
                session_id="s1",
                role="assistant",
                content="b",
                sender_id="bot",
                platform="test",
            ),
        ]
        mgr.store.get_messages = AsyncMock(return_value=msgs)

        result = await mgr.get_messages("s1")
        assert len(result) == 2
        mgr.store.get_messages.assert_called_once_with(
            session_id="s1", limit=50, sender_id=None
        )

    @pytest.mark.asyncio
    async def test_filter_by_sender_id_skips_cache(self) -> None:
        """指定 sender_id 时跳过缓存，直接查询数据库。"""
        mgr = self._make_mgr()
        # 预先填充缓存
        cached = [
            Message(
                id=99,
                session_id="s1",
                role="user",
                content="cached",
                sender_id="other",
                platform="test",
            )
        ]
        mgr._cache["s1"] = [cached, MagicMock()]

        msgs = [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="filtered",
                sender_id="u1",
                platform="test",
            )
        ]
        mgr.store.get_messages = AsyncMock(return_value=msgs)

        result = await mgr.get_messages("s1", sender_id="u1")
        assert len(result) == 1
        assert result[0].content == "filtered"
        mgr.store.get_messages.assert_called_once_with(
            session_id="s1", limit=50, sender_id="u1"
        )

    @pytest.mark.asyncio
    async def test_cache_hit(self) -> None:
        """缓存命中时直接返回消息，不查询数据库。"""
        mgr = self._make_mgr()
        cached = [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="cached-a",
                sender_id="u1",
                platform="test",
            ),
            Message(
                id=2,
                session_id="s1",
                role="assistant",
                content="cached-b",
                sender_id="bot",
                platform="test",
            ),
            Message(
                id=3,
                session_id="s1",
                role="user",
                content="cached-c",
                sender_id="u1",
                platform="test",
            ),
        ]
        mgr._cache["s1"] = [cached, 0.0]

        result = await mgr.get_messages("s1", limit=2)
        assert len(result) == 2
        # 应返回缓存的最后 2 条
        assert result[0].content == "cached-b"
        assert result[1].content == "cached-c"
        mgr.store.get_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_hit_no_limit(self) -> None:
        """未指定 limit 且命中缓存时，返回所有缓存消息。"""
        mgr = self._make_mgr()
        cached = [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="a",
                sender_id="u1",
                platform="test",
            ),
        ]
        mgr._cache["s1"] = [cached, 0.0]

        result = await mgr.get_messages("s1", limit=0)  # limit=0 → 全部
        assert len(result) == 1


# ---------------------------------------------------------------------------
# get_context 测试
# ---------------------------------------------------------------------------


class TestGetContext:
    """get_context 方法测试。"""

    @pytest.mark.asyncio
    async def test_formats_for_llm(self) -> None:
        """get_context 将消息格式化为 LLM 可消费格式。"""
        mgr = _TestMsgManager()
        msgs = [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="hello",
                sender_id="u1",
                sender_name="Alice",
                platform="test",
            ),
            Message(
                id=2,
                session_id="s1",
                role="assistant",
                content="hi there",
                sender_id="bot",
                platform="test",
            ),
        ]
        mgr.store.get_messages = AsyncMock(return_value=msgs)

        result = await mgr.get_context("s1", format_for_llm=True)
        assert len(result) == 2
        assert isinstance(result[0], dict)
        assert "role" in result[0]
        assert "content" in result[0]

    @pytest.mark.asyncio
    async def test_raw_format(self) -> None:
        """format_for_llm=False 时返回 to_dict 格式。"""
        mgr = _TestMsgManager()
        msgs = [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="hello",
                sender_id="u1",
                platform="test",
            ),
        ]
        mgr.store.get_messages = AsyncMock(return_value=msgs)

        result = await mgr.get_context("s1", format_for_llm=False)
        assert isinstance(result[0], dict)
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_group_context_includes_sender_name(self) -> None:
        """LLM 格式中群组消息包含发送者名称。"""
        mgr = _TestMsgManager()
        msgs = [
            Message(
                id=1,
                session_id="group-1",
                role="user",
                content="hello",
                sender_id="u1",
                sender_name="Alice",
                group_id="group-1",
                platform="test",
            ),
        ]
        mgr.store.get_messages = AsyncMock(return_value=msgs)

        result = await mgr.get_context("group-1", format_for_llm=True)
        # format_for_llm 在群组场景下应包含发送者名称
        assert "Alice" in result[0]["content"]

    @pytest.mark.asyncio
    async def test_max_messages_override(self) -> None:
        """max_messages 参数会覆盖 context_window_size。"""
        mgr = _TestMsgManager()
        mgr.context_window_size = 50
        msgs = [
            Message(
                id=i,
                session_id="s1",
                role="user",
                content=f"msg{i}",
                sender_id="u1",
                platform="test",
            )
            for i in range(10)
        ]
        mgr.store.get_messages = AsyncMock(return_value=msgs)

        await mgr.get_context("s1", max_messages=5)
        mgr.store.get_messages.assert_called_once()
        call_kwargs = mgr.store.get_messages.call_args[1]
        assert call_kwargs["limit"] == 5
