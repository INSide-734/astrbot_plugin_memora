"""验证普通群消息可以独立触发记忆反思。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from astrbot.api.platform import MessageType

from core.event_handler import EventHandler
from core.handlers.reflection_handler import ReflectionHandler
from core.identity.models import IdentityTrust, ResolvedIdentity


def _unsupported_group_identity() -> ResolvedIdentity:
    """构造不要求协议目录写入的群聊身份快照。"""

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


def _group_event() -> MagicMock:
    """构造具有稳定 UMO 的普通群消息事件。"""

    event = MagicMock()
    event.unified_msg_origin = "test:GroupMessage:group-1"
    event.get_message_type.return_value = MessageType.GROUP_MESSAGE
    event.get_sender_id.return_value = "user-1"
    event.get_self_id.return_value = "bot-1"
    event.is_at_or_wake_command = False
    return event


@pytest.mark.asyncio
async def test_group_capture_checks_only_ambient_messages_after_persisting() -> None:
    """环境消息落库后应检查阈值，唤醒 Bot 的消息则等待响应钩子。"""

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: (
        True if key == "session_manager.enable_full_group_capture" else default
    )
    conversation = MagicMock()
    conversation.identity_runtime = None
    conversation.add_message_from_event = AsyncMock()
    handler = EventHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
    )
    handler._identity_runtime.resolve = MagicMock(
        return_value=_unsupported_group_identity()
    )
    handler._extractor.extract_message_content = AsyncMock(return_value="普通群消息")
    handler._dedup.build_dedup_key = AsyncMock(return_value=None)
    handler._enforce_message_limit = AsyncMock()
    handler._reflection_handler.maybe_schedule_summary = AsyncMock()
    event = _group_event()

    await handler.handle_all_group_messages(event)
    maintenance_tasks = list(handler._maintenance_tasks)
    if maintenance_tasks:
        await asyncio.gather(*maintenance_tasks)

    conversation.add_message_from_event.assert_awaited_once()
    handler._reflection_handler.maybe_schedule_summary.assert_awaited_once_with(event)

    handler._reflection_handler.maybe_schedule_summary.reset_mock()
    event.is_at_or_wake_command = True
    await handler.handle_all_group_messages(event)
    maintenance_tasks = list(handler._maintenance_tasks)
    if maintenance_tasks:
        await asyncio.gather(*maintenance_tasks)

    assert conversation.add_message_from_event.await_count == 2
    handler._reflection_handler.maybe_schedule_summary.assert_not_awaited()
    await handler.shutdown()


@pytest.mark.asyncio
async def test_ambient_messages_schedule_summary_without_assistant_response() -> None:
    """达到阈值的环境消息应直接调度存储任务，不依赖 LLM 响应。"""

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "reflection_engine.summary_trigger_rounds": 1,
    }.get(key, default)
    conversation = MagicMock()
    conversation.get_session_info = AsyncMock(
        return_value=SimpleNamespace(message_count=2)
    )
    conversation.store = MagicMock()
    conversation.store.get_message_count = AsyncMock(return_value=2)
    conversation.get_session_metadata = AsyncMock(
        side_effect=lambda session_id, key, default=None: (
            0 if key == "last_summarized_index" else None
        )
    )
    history = [MagicMock(group_id="group-1") for _ in range(2)]
    conversation.get_messages_range = AsyncMock(return_value=history)
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
        enforce_limit_cb=AsyncMock(),
    )
    handler._storage_task = AsyncMock()
    event = _group_event()

    with patch(
        "core.handlers.reflection_trigger.get_persona_id",
        new=AsyncMock(return_value="persona-1"),
    ):
        await handler.maybe_schedule_summary(event)

    storage_tasks = list(handler._storage_tasks)
    assert len(storage_tasks) == 1
    await asyncio.gather(*storage_tasks)
    handler._storage_task.assert_awaited_once_with(
        event.unified_msg_origin,
        history,
        "persona-1",
        0,
        2,
        0,
    )
    await handler.shutdown()


@pytest.mark.asyncio
async def test_ambient_messages_below_threshold_do_not_schedule_summary() -> None:
    """未达到配置轮次时只保留会话消息，不应创建后台任务。"""

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "reflection_engine.summary_trigger_rounds": 1,
    }.get(key, default)
    conversation = MagicMock()
    conversation.get_session_info = AsyncMock(
        return_value=SimpleNamespace(message_count=1)
    )
    conversation.store = MagicMock()
    conversation.store.get_message_count = AsyncMock(return_value=1)
    conversation.get_session_metadata = AsyncMock(
        side_effect=lambda session_id, key, default=None: (
            0 if key == "last_summarized_index" else None
        )
    )
    conversation.get_messages_range = AsyncMock()
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
        enforce_limit_cb=AsyncMock(),
    )
    handler._storage_task = AsyncMock()

    await handler.maybe_schedule_summary(_group_event())

    assert not handler._storage_tasks
    conversation.get_messages_range.assert_not_awaited()
    handler._storage_task.assert_not_awaited()
    await handler.shutdown()


@pytest.mark.asyncio
async def test_ambient_reflection_failure_does_not_break_message_flow() -> None:
    """反思阈值检查的普通错误应被隔离，不得向消息钩子传播。"""

    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=MagicMock(),
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=MagicMock(),
        enforce_limit_cb=AsyncMock(),
    )
    handler._summary_trigger.prepare = AsyncMock(
        side_effect=RuntimeError("反思检查失败")
    )

    await handler.maybe_schedule_summary(_group_event())

    assert not handler._storage_tasks
    await handler.shutdown()


@pytest.mark.asyncio
async def test_ambient_reflection_propagates_cancellation() -> None:
    """关闭期取消必须穿过环境消息反思入口继续传播。"""

    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=MagicMock(),
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=MagicMock(),
        enforce_limit_cb=AsyncMock(),
    )
    handler._summary_trigger.prepare = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await handler.maybe_schedule_summary(_group_event())

    assert not handler._storage_tasks
    await handler.shutdown()
