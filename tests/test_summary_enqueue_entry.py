"""验证环境群消息与总结调度器之间的事件入口。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot.api.platform import MessageType

from core.event_handler import EventHandler
from core.features.identity.application.runtime import ProtocolIdentityRuntime
from core.features.identity.domain.models import IdentityTrust, ResolvedIdentity


def _identity() -> ResolvedIdentity:
    """构造不要求协议目录写入的测试身份。"""
    return ResolvedIdentity(
        protocol="test",
        identity_namespace="test",
        stable_user_id=None,
        canonical_user_id=None,
        scope_type="group",
        scope_id="group-1",
        global_name=None,
        scope_name=None,
        display_name=None,
        observed_at=0.0,
        trust_status=IdentityTrust.UNSUPPORTED,
        name_field_states={},
        conversation_sender_id="user-1",
    )


def _event() -> MagicMock:
    """构造具有稳定 UMO 的普通群消息事件。"""
    event = MagicMock()
    event.unified_msg_origin = "test:GroupMessage:group-1"
    event.get_message_type.return_value = MessageType.GROUP_MESSAGE
    event.get_sender_id.return_value = "user-1"
    event.get_self_id.return_value = "bot-1"
    event.is_at_or_wake_command = False
    return event


def _handler(conversation: MagicMock) -> EventHandler:
    """构造群消息捕获所需的最小事件处理器。"""
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: (
        True if key == "session_manager.enable_full_group_capture" else default
    )
    identity_runtime = ProtocolIdentityRuntime()
    identity_runtime.resolve = MagicMock(return_value=_identity())
    conversation.identity_runtime = identity_runtime
    handler = EventHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
        identity_runtime=identity_runtime,
    )
    handler._extractor.extract_message_content = AsyncMock(return_value="普通群消息")
    handler._dedup.build_dedup_key = AsyncMock(return_value=None)
    handler._enforce_message_limit = AsyncMock()
    return handler


@pytest.mark.asyncio
async def test_group_capture_checks_only_ambient_messages_after_persisting() -> None:
    """环境消息落库后检查总结，唤醒 Bot 的消息等待响应钩子。"""
    conversation = MagicMock()
    conversation.add_message_from_event = AsyncMock()
    handler = _handler(conversation)
    handler._reflection_handler.maybe_schedule_summary = AsyncMock()

    event = _event()
    await handler.handle_all_group_messages(event)
    maintenance_tasks = list(handler._maintenance_tasks)
    if maintenance_tasks:
        await asyncio.gather(*maintenance_tasks)

    conversation.add_message_from_event.assert_awaited_once()
    handler._reflection_handler.maybe_schedule_summary.assert_awaited_once_with(
        event, identity=_identity()
    )

    handler._reflection_handler.maybe_schedule_summary.reset_mock()
    event.is_at_or_wake_command = True
    await handler.handle_all_group_messages(event)
    maintenance_tasks = list(handler._maintenance_tasks)
    if maintenance_tasks:
        await asyncio.gather(*maintenance_tasks)

    assert conversation.add_message_from_event.await_count == 2
    handler._reflection_handler.maybe_schedule_summary.assert_not_awaited()
    await handler.shutdown()
