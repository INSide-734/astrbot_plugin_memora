"""event_adapter 测试 — EventAdapterMixin 从 AstrBot 事件创建消息。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.features.conversation.application.event_adapter import EventAdapterMixin
from core.features.identity.domain.models import (
    IdentityTrust,
    NameFieldState,
    ResolvedIdentity,
)


def _trusted_group_identity() -> ResolvedIdentity:
    """构造事件适配测试使用的可信 OneBot 群身份。"""

    return ResolvedIdentity(
        protocol="onebot11",
        identity_namespace="qq",
        stable_user_id="10001",
        canonical_user_id="10001",
        scope_type="group",
        scope_id="20001",
        global_name="新昵称",
        scope_name="新群名片",
        display_name="新群名片",
        observed_at=200.0,
        trust_status=IdentityTrust.TRUSTED,
        name_field_states={
            "nickname": NameFieldState.VALID,
            "card": NameFieldState.VALID,
        },
        conversation_sender_id="10001",
        identity_label="QQ:10001",
    )


# ---------------------------------------------------------------------------
# 测试辅助：提供 Mixin 所需依赖的具体类
# ---------------------------------------------------------------------------


class _TestAdapter(EventAdapterMixin):
    """提供 EventAdapterMixin 所需依赖的测试实现。"""

    def __init__(self, store=None):
        """初始化调用参数捕获状态。"""

        self.store = store or MagicMock()
        self._session_id_captured = None
        self._call_kwargs: dict[str, object] | None = None

    async def add_message(
        self,
        session_id,
        role,
        content,
        sender_id=None,
        sender_name=None,
        group_id=None,
        platform="unknown",
        is_bot_message=False,
        metadata=None,
    ):
        """记录调用参数并返回伪造 Message。"""
        self._session_id_captured = session_id
        self._call_kwargs = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "group_id": group_id,
            "platform": platform,
            "is_bot_message": is_bot_message,
            "metadata": metadata,
        }
        msg = MagicMock()
        msg.id = 42
        return msg


def _captured_kwargs(adapter: _TestAdapter) -> dict[str, object]:
    """返回已捕获的调用参数，并明确测试前置条件。"""

    assert adapter._call_kwargs is not None
    return adapter._call_kwargs


# ---------------------------------------------------------------------------
# add_message_from_event 行为测试
# ---------------------------------------------------------------------------


class TestAddMessageFromEvent:
    """验证 add_message_from_event 的异步行为。"""

    @pytest.mark.asyncio
    async def test_basic_user_message(self) -> None:
        """普通用户消息应读取事件中的发送者名称。"""
        adapter = _TestAdapter()
        event = MagicMock()
        event.unified_msg_origin = "session-001"
        event.get_sender_id.return_value = "user-123"
        event.get_sender_name.return_value = "Alice"
        event.get_message_type.return_value = "PRIVATE_MESSAGE"  # 默认私聊类型
        event.get_platform_name.return_value = "test-platform"

        msg = await adapter.add_message_from_event(event, "user", "Hello world")
        assert msg is not None
        assert adapter._call_kwargs is not None
        assert adapter._call_kwargs["role"] == "user"

    @pytest.mark.asyncio
    async def test_uses_unified_msg_origin(self) -> None:
        """会话 ID 应来自 event.unified_msg_origin。"""
        adapter = _TestAdapter()
        event = MagicMock()
        event.unified_msg_origin = "group-abc-123"
        event.get_sender_id.return_value = "user-1"
        event.get_sender_name.return_value = "Bob"
        event.get_message_type.return_value = "PRIVATE_MESSAGE"
        event.get_platform_name.return_value = "qq"

        await adapter.add_message_from_event(event, "user", "test")
        assert _captured_kwargs(adapter)["session_id"] == "group-abc-123"

    @pytest.mark.asyncio
    async def test_falls_back_to_session_id_for_sender(self) -> None:
        """缺少 sender_id 时应回退到 session_id。"""
        adapter = _TestAdapter()
        # 使用 spec 严格限制事件可用属性。
        event = MagicMock(
            spec=[
                "unified_msg_origin",
                "get_message_type",
                "get_sender_name",
                "message_obj",
            ]
        )
        event.unified_msg_origin = "session-fallback"
        event.get_sender_name.return_value = None
        event.get_message_type.return_value = "PRIVATE_MESSAGE"
        event.message_obj = MagicMock(spec=["sender", "raw_message"])
        event.message_obj.sender = MagicMock()
        event.message_obj.raw_message = MagicMock()

        await adapter.add_message_from_event(event, "user", "test")
        call_kwargs = _captured_kwargs(adapter)
        assert call_kwargs["sender_id"] == "session-fallback"

    @pytest.mark.asyncio
    async def test_group_message_detection(self) -> None:
        """群消息应正确识别并设置 group_id。"""
        adapter = _TestAdapter()
        from astrbot.api.platform import MessageType

        event = MagicMock()
        event.unified_msg_origin = "group-456"
        event.get_sender_id.return_value = "user-789"
        event.get_sender_name.return_value = "Charlie"
        event.get_message_type.return_value = MessageType.GROUP_MESSAGE
        event.get_platform_name.return_value = "qq"

        await adapter.add_message_from_event(event, "user", "group message")
        call_kwargs = _captured_kwargs(adapter)
        assert call_kwargs["group_id"] == "group-456"

    @pytest.mark.asyncio
    async def test_bot_message_in_group(self) -> None:
        """群聊 Bot 消息应以 self_id 作为发送者。"""
        adapter = _TestAdapter()
        from astrbot.api.platform import MessageType

        event = MagicMock()
        event.unified_msg_origin = "group-789"
        event.get_sender_id.return_value = "user-1"
        event.get_sender_name.return_value = "Unknown"
        event.get_message_type.return_value = MessageType.GROUP_MESSAGE
        event.get_platform_name.return_value = "qq"
        event.get_self_id.return_value = "bot-999"
        event.message_obj = MagicMock()
        event.message_obj.self_id = "bot-999"

        await adapter.add_message_from_event(event, "assistant", "bot reply")
        call_kwargs = _captured_kwargs(adapter)
        assert call_kwargs["sender_id"] == "bot-999"

    @pytest.mark.asyncio
    async def test_bot_message_in_private_chat(self) -> None:
        """私聊 Bot 消息必须使用 self_id，而不是用户发送者 ID。"""
        adapter = _TestAdapter()
        from astrbot.api.platform import MessageType

        event = MagicMock()
        event.unified_msg_origin = "private-001"
        event.get_sender_id.return_value = "assistant-bot"
        event.get_sender_name.return_value = "BotName"
        event.get_message_type.return_value = MessageType.FRIEND_MESSAGE
        event.get_platform_name.return_value = "qq"
        event.get_self_id.return_value = "bot-999"

        await adapter.add_message_from_event(event, "assistant", "private reply")
        call_kwargs = _captured_kwargs(adapter)
        assert call_kwargs["is_bot_message"] is True
        assert call_kwargs["group_id"] is None
        assert call_kwargs["sender_id"] == "bot-999"

    @pytest.mark.asyncio
    async def test_trusted_identity_overrides_user_message_fields(self) -> None:
        """可信协议身份应覆盖 user Message 的发送者与身份元数据。"""

        adapter = _TestAdapter()
        event = MagicMock()
        event.unified_msg_origin = "group-session"
        event.get_sender_id.return_value = "可变包装值"
        event.get_sender_name.return_value = "旧名称"
        event.get_platform_name.return_value = "aiocqhttp"
        from astrbot.api.platform import MessageType

        event.get_message_type.return_value = MessageType.GROUP_MESSAGE
        identity = _trusted_group_identity()

        await adapter.add_message_from_event(event, "user", "正文", identity=identity)

        call_kwargs = _captured_kwargs(adapter)
        assert call_kwargs["sender_id"] == "10001"
        assert call_kwargs["sender_name"] == "新群名片"
        assert call_kwargs["group_id"] == "20001"
        assert call_kwargs["metadata"] == {
            "identity_trusted": True,
            "identity_protocol": "onebot11",
            "identity_namespace": "qq",
            "stable_user_id": "10001",
            "canonical_user_id": "10001",
            "identity_label": "QQ:10001",
        }

    @pytest.mark.asyncio
    async def test_trusted_group_identity_keeps_scope_for_assistant(self) -> None:
        """带严格身份的群聊 assistant 仍应保存群作用域和 Bot self ID。"""

        adapter = _TestAdapter()
        event = MagicMock()
        event.unified_msg_origin = "group-session"
        event.get_self_id.return_value = "bot-999"
        event.get_platform_name.return_value = "aiocqhttp"

        await adapter.add_message_from_event(
            event,
            "assistant",
            "回复",
            identity=_trusted_group_identity(),
        )

        call_kwargs = _captured_kwargs(adapter)
        assert call_kwargs["sender_id"] == "bot-999"
        assert call_kwargs["group_id"] == "20001"

    @pytest.mark.asyncio
    async def test_invalid_identity_skips_user_message_write(self) -> None:
        """非法协议身份不得回退到可变名称并写入用户消息。"""

        adapter = _TestAdapter()
        event = MagicMock()
        event.unified_msg_origin = "group-session"
        identity = ResolvedIdentity(
            protocol="onebot11",
            identity_namespace="qq",
            stable_user_id=None,
            canonical_user_id=None,
            scope_type="group",
            scope_id="20001",
            global_name=None,
            scope_name=None,
            display_name=None,
            observed_at=200.0,
            trust_status=IdentityTrust.INVALID,
            name_field_states={},
        )

        result = await adapter.add_message_from_event(
            event, "user", "正文", identity=identity
        )

        assert result is None
        assert adapter._call_kwargs is None

    @pytest.mark.asyncio
    async def test_platform_unknown_fallback(self) -> None:
        """缺少 get_platform_name 时平台应回退为 unknown。"""
        adapter = _TestAdapter()
        event = MagicMock()
        event.unified_msg_origin = "session-x"
        event.get_sender_id.return_value = "user-1"
        event.get_sender_name.return_value = "Test"
        event.get_message_type.return_value = "PRIVATE_MESSAGE"
        # 移除 get_platform_name，覆盖兼容降级路径。
        del event.get_platform_name

        await adapter.add_message_from_event(event, "user", "test")
        call_kwargs = _captured_kwargs(adapter)
        assert call_kwargs["platform"] == "unknown"

    @pytest.mark.asyncio
    async def test_sender_id_from_attr_fallback(self) -> None:
        """缺少 get_sender_id 时应读取 sender_id 属性。"""
        adapter = _TestAdapter()
        event = MagicMock()
        event.unified_msg_origin = "session-y"
        event.sender_id = "attr-sender-123"
        del event.get_sender_id  # 移除方法以覆盖属性降级路径
        event.get_message_type.return_value = "PRIVATE_MESSAGE"

        await adapter.add_message_from_event(event, "user", "test")
        call_kwargs = _captured_kwargs(adapter)
        assert call_kwargs["sender_id"] == "attr-sender-123"

    @pytest.mark.asyncio
    async def test_sender_name_from_attr_fallback(self) -> None:
        """缺少 get_sender_name 时应读取 sender_name 属性。"""
        adapter = _TestAdapter()
        event = MagicMock()
        event.unified_msg_origin = "session-z"
        event.get_sender_id.return_value = "sender-1"
        event.sender_name = "AttrName"
        del event.get_sender_name  # 移除方法以覆盖属性降级路径
        event.get_message_type.return_value = "PRIVATE_MESSAGE"

        await adapter.add_message_from_event(event, "user", "test")
        call_kwargs = _captured_kwargs(adapter)
        assert call_kwargs["sender_name"] == "AttrName"

    @pytest.mark.asyncio
    async def test_platform_from_attr_fallback(self) -> None:
        """平台 getter 可返回兼容适配器名称。"""
        adapter = _TestAdapter()
        event = MagicMock()
        event.unified_msg_origin = "session-p"
        event.get_sender_id.return_value = "sender-1"
        event.get_sender_name.return_value = "Name"
        event.get_platform_name = MagicMock(return_value="dotnet")
        event.get_message_type.return_value = "PRIVATE_MESSAGE"

        await adapter.add_message_from_event(event, "user", "test")
        call_kwargs = _captured_kwargs(adapter)
        assert call_kwargs["platform"] == "dotnet"
