"""验证普通群消息可以独立触发记忆反思。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from astrbot.api.platform import MessageType

from core.event_handler import EventHandler
from core.features.identity.domain.models import IdentityTrust, ResolvedIdentity
from core.features.reflection.application.reflection_handler import ReflectionHandler
from core.features.reflection.application.reflection_trigger import (
    ReflectionWindowRequest,
)


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

    from core.features.identity.application.runtime import ProtocolIdentityRuntime

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: (
        True if key == "session_manager.enable_full_group_capture" else default
    )
    conversation = MagicMock()
    identity_runtime = ProtocolIdentityRuntime()
    identity_runtime.resolve = MagicMock(return_value=_unsupported_group_identity())
    conversation.identity_runtime = identity_runtime
    conversation.add_message_from_event = AsyncMock()
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
        "core.features.reflection.application.reflection_trigger.get_persona_id",
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
async def test_summary_trigger_bounds_each_window_to_trigger_rounds() -> None:
    """积压消息不得被合并成超过触发轮次的单次 LLM 窗口。"""

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "reflection_engine.summary_trigger_rounds": 2,
    }.get(key, default)
    conversation = MagicMock()
    conversation.get_session_info = AsyncMock(
        return_value=SimpleNamespace(message_count=12)
    )
    conversation.store = MagicMock()
    conversation.store.get_message_count = AsyncMock(return_value=12)
    conversation.get_session_metadata = AsyncMock(
        side_effect=lambda session_id, key, default=None: (
            0 if key == "last_summarized_index" else None
        )
    )
    history = [MagicMock(group_id=None) for _ in range(4)]
    conversation.get_messages_range = AsyncMock(return_value=history)
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
        enforce_limit_cb=AsyncMock(),
    )

    with patch(
        "core.features.reflection.application.reflection_trigger.get_persona_id",
        new=AsyncMock(return_value="persona-1"),
    ):
        request = await handler._summary_trigger.prepare(
            _group_event(),
            "test:GroupMessage:group-1",
        )

    assert request is not None
    assert request.start_index == 0
    assert request.end_index == 4
    assert request.drain_end_index == 12
    conversation.get_messages_range.assert_awaited_once_with(
        session_id="test:GroupMessage:group-1",
        start_index=0,
        end_index=4,
    )


@pytest.mark.asyncio
async def test_summary_gate_reports_effective_five_round_threshold() -> None:
    """四轮应跳过、五轮应触发，诊断必须暴露实际配置阈值。"""

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "reflection_engine.summary_trigger_rounds": 5,
    }.get(key, default)
    message_count = {"value": 8}
    conversation = MagicMock()
    conversation.get_session_info = AsyncMock(
        side_effect=lambda session_id: SimpleNamespace(
            message_count=message_count["value"]
        )
    )
    conversation.store = MagicMock()
    conversation.store.get_message_count = AsyncMock(
        side_effect=lambda session_id: message_count["value"]
    )
    conversation.get_session_metadata = AsyncMock(
        side_effect=lambda session_id, key, default=None: (
            0 if key == "last_summarized_index" else None
        )
    )
    conversation.get_messages_range = AsyncMock(
        return_value=[MagicMock(group_id=None) for _ in range(10)]
    )
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
        enforce_limit_cb=AsyncMock(),
    )

    with (
        patch(
            "core.features.reflection.application.reflection_trigger.report_debug_event"
        ) as report,
        patch(
            "core.features.reflection.application.reflection_trigger.get_persona_id",
            new=AsyncMock(return_value="persona-1"),
        ),
    ):
        assert (
            await handler._summary_trigger.prepare(
                _group_event(),
                "test:GroupMessage:group-1",
            )
            is None
        )
        message_count["value"] = 10
        request = await handler._summary_trigger.prepare(
            _group_event(),
            "test:GroupMessage:group-1",
        )

    assert request is not None
    assert request.end_index == 10
    gate_events = [
        call.kwargs
        for call in report.call_args_list
        if call.args == ("reflection_state",)
        and call.kwargs.get("stage") == "summary_gate"
    ]
    assert [event["status"] for event in gate_events] == ["skipped", "completed"]
    assert all(event["threshold_rounds"] == 5 for event in gate_events)


@pytest.mark.asyncio
async def test_summary_backlog_drains_in_bounded_windows() -> None:
    """单任务只清空初始积压，新到消息必须留给下一次触发。"""

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "reflection_engine.summary_trigger_rounds": 1,
    }.get(key, default)
    progress = {"last_summarized_index": 0}
    conversation = MagicMock()
    message_count = {"value": 6}
    conversation.get_session_info = AsyncMock(
        side_effect=lambda session_id: SimpleNamespace(
            message_count=message_count["value"]
        )
    )
    conversation.store = MagicMock()
    conversation.store.get_message_count = AsyncMock(
        side_effect=lambda session_id: message_count["value"]
    )

    async def get_metadata(session_id, key, default=None):
        """返回测试内维护的总结进度。"""

        if key == "last_summarized_index":
            return progress[key]
        return None

    conversation.get_session_metadata = AsyncMock(side_effect=get_metadata)
    messages = [MagicMock(group_id=None) for _ in range(10)]
    conversation.get_messages_range = AsyncMock(
        side_effect=lambda session_id, start_index, end_index: messages[
            start_index:end_index
        ]
    )
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
        enforce_limit_cb=AsyncMock(),
    )

    async def store_window(
        session_id,
        history_messages,
        persona_id,
        start_index,
        end_index,
        retry_count,
    ):
        """模拟成功提交一个有界窗口。"""

        assert len(history_messages) <= 2
        if start_index == 0:
            message_count["value"] = 10
        progress["last_summarized_index"] = end_index

    handler._storage_task = AsyncMock(side_effect=store_window)

    with patch(
        "core.features.reflection.application.reflection_trigger.get_persona_id",
        new=AsyncMock(return_value="persona-1"),
    ):
        request = await handler._summary_trigger.prepare(
            _group_event(),
            "test:GroupMessage:group-1",
        )
    assert request is not None

    original_prepare = handler._summary_trigger.prepare_for_persona
    handler._summary_trigger.prepare_for_persona = AsyncMock(
        side_effect=original_prepare
    )
    await handler._drain_summary_backlog(request)

    assert [call.args[3:5] for call in handler._storage_task.await_args_list] == [
        (0, 2),
        (2, 4),
        (4, 6),
    ]
    assert len(handler._summary_trigger.prepare_for_persona.await_args_list) == 2
    assert all(
        call.kwargs == {"drain_end_index": 6}
        for call in handler._summary_trigger.prepare_for_persona.await_args_list
    )


@pytest.mark.asyncio
async def test_pending_retry_keeps_original_bounded_end() -> None:
    """失败重试必须保持原窗口，不能把新积压再次合并成超大 Prompt。"""

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "reflection_engine.summary_trigger_rounds": 10,
    }.get(key, default)
    conversation = MagicMock()
    conversation.get_session_info = AsyncMock(
        return_value=SimpleNamespace(message_count=100)
    )
    conversation.store = MagicMock()
    conversation.store.get_message_count = AsyncMock(return_value=100)
    conversation.get_session_metadata = AsyncMock(
        side_effect=lambda session_id, key, default=None: {
            "last_summarized_index": 0,
            "pending_summary": {
                "start_index": 0,
                "end_index": 20,
                "retry_count": 1,
            },
        }.get(key, default)
    )
    conversation.get_messages_range = AsyncMock(
        return_value=[MagicMock(group_id=None) for _ in range(20)]
    )
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
        enforce_limit_cb=AsyncMock(),
    )

    with patch(
        "core.features.reflection.application.reflection_trigger.get_persona_id",
        new=AsyncMock(return_value="persona-1"),
    ):
        request = await handler._summary_trigger.prepare(
            _group_event(),
            "test:GroupMessage:group-1",
        )

    assert request is not None
    assert request.start_index == 0
    assert request.end_index == 20
    assert request.drain_end_index == 100


@pytest.mark.asyncio
async def test_stale_pending_is_cleared_before_next_bounded_window() -> None:
    """已由游标覆盖的旧 pending 只清理，不得重新播放旧窗口。"""

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "reflection_engine.summary_trigger_rounds": 1,
    }.get(key, default)
    conversation = MagicMock()
    conversation.get_session_info = AsyncMock(
        return_value=SimpleNamespace(message_count=6)
    )
    conversation.store = MagicMock()
    conversation.store.get_message_count = AsyncMock(return_value=6)
    conversation.get_session_metadata = AsyncMock(
        side_effect=lambda _session_id, key, default=None: {
            "last_summarized_index": 2,
            "pending_summary": {
                "start_index": 0,
                "end_index": 2,
                "retry_count": 1,
            },
        }.get(key, default)
    )
    conversation.update_session_metadata = AsyncMock(return_value=True)
    conversation.get_messages_range = AsyncMock(
        return_value=[MagicMock(group_id=None), MagicMock(group_id=None)]
    )
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
        enforce_limit_cb=AsyncMock(),
    )

    request = await handler._summary_trigger.prepare_for_persona(
        "test:GroupMessage:group-1",
        "persona-1",
        drain_end_index=6,
    )

    assert request is not None
    assert request.start_index == 2
    assert request.end_index == 4
    conversation.update_session_metadata.assert_awaited_once_with(
        "test:GroupMessage:group-1",
        "pending_summary",
        None,
    )
    conversation.get_messages_range.assert_awaited_once_with(
        session_id="test:GroupMessage:group-1",
        start_index=2,
        end_index=4,
    )


@pytest.mark.asyncio
async def test_stale_pending_clear_failure_stops_backlog_without_replay() -> None:
    """旧 pending 清理未提交时停止续跑，避免同窗紧循环。"""

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "reflection_engine.summary_trigger_rounds": 1,
    }.get(key, default)
    conversation = MagicMock()
    conversation.get_session_info = AsyncMock(
        return_value=SimpleNamespace(message_count=6)
    )
    conversation.store = MagicMock()
    conversation.store.get_message_count = AsyncMock(return_value=6)
    conversation.get_session_metadata = AsyncMock(
        side_effect=lambda _session_id, key, default=None: {
            "last_summarized_index": 2,
            "pending_summary": {
                "start_index": 0,
                "end_index": 2,
                "retry_count": 1,
            },
        }.get(key, default)
    )
    conversation.update_session_metadata = AsyncMock(return_value=False)
    conversation.get_messages_range = AsyncMock()
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
        enforce_limit_cb=AsyncMock(),
    )

    request = await handler._summary_trigger.prepare_for_persona(
        "test:GroupMessage:group-1",
        "persona-1",
        drain_end_index=6,
    )

    assert request is None
    conversation.update_session_metadata.assert_awaited_once_with(
        "test:GroupMessage:group-1",
        "pending_summary",
        None,
    )
    conversation.get_messages_range.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_exhaustion_advances_and_clears_in_one_commit() -> None:
    """放弃耗尽窗口时游标推进与 pending 清理必须原子提交。"""

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "reflection_engine.summary_trigger_rounds": 1,
    }.get(key, default)
    conversation = MagicMock()
    conversation.get_session_info = AsyncMock(
        return_value=SimpleNamespace(message_count=6)
    )
    conversation.store = MagicMock()
    conversation.store.get_message_count = AsyncMock(return_value=6)
    conversation.get_session_metadata = AsyncMock(
        side_effect=lambda _session_id, key, default=None: {
            "last_summarized_index": 0,
            "pending_summary": {
                "start_index": 0,
                "end_index": 2,
                "retry_count": 3,
            },
        }.get(key, default)
    )
    conversation.update_session_metadata = AsyncMock()
    conversation.update_session_metadata_fields = AsyncMock(return_value=True)
    conversation.get_messages_range = AsyncMock()
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
        enforce_limit_cb=AsyncMock(),
    )

    request = await handler._summary_trigger.prepare_for_persona(
        "test:GroupMessage:group-1",
        "persona-1",
        drain_end_index=6,
    )

    assert request is None
    conversation.update_session_metadata_fields.assert_awaited_once_with(
        "test:GroupMessage:group-1",
        {
            "last_summarized_index": 2,
            "pending_summary": None,
        },
    )
    conversation.update_session_metadata.assert_not_awaited()
    conversation.get_messages_range.assert_not_awaited()


@pytest.mark.asyncio
async def test_summary_backlog_stops_after_current_window_during_shutdown() -> None:
    """关闭开始后只完成当前窗口，不得继续拉取后续 LLM 总结窗口。"""

    conversation = MagicMock()
    conversation.get_session_metadata = AsyncMock(return_value=2)
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=MagicMock(),
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
        enforce_limit_cb=AsyncMock(),
    )

    async def finish_current_window(*args, **kwargs):
        """模拟当前窗口提交期间插件进入关闭状态。"""

        handler._shutting_down = True

    handler._storage_task = AsyncMock(side_effect=finish_current_window)
    handler._summary_trigger.prepare_for_persona = AsyncMock(return_value=None)
    request = ReflectionWindowRequest(
        session_id="test:GroupMessage:group-1",
        history_messages=[MagicMock(), MagicMock()],
        persona_id="persona-1",
        start_index=0,
        end_index=2,
        drain_end_index=6,
        retry_count=0,
    )

    await handler._drain_summary_backlog(request)

    handler._storage_task.assert_awaited_once()
    handler._summary_trigger.prepare_for_persona.assert_not_awaited()


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
