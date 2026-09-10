"""手动总结命令的即时入队确认契约。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.features.identity.application.runtime import ProtocolIdentityRuntime
from core.features.identity.domain.models import IdentityTrust, ResolvedIdentity
from core.features.reflection.domain.summary_models import (
    SummaryEnqueueResult,
    SummaryReasonCode,
)
from core.platform.transport.commands.command_handler import CommandHandler


def _trusted_identity() -> ResolvedIdentity:
    """构造与生产 CanonicalScopeResolver 契约一致的可信私聊身份。"""
    return ResolvedIdentity(
        protocol="test",
        identity_namespace="test",
        stable_user_id="user-1",
        canonical_user_id="session-feedback",
        scope_type="private",
        scope_id="session-feedback",
        global_name=None,
        scope_name=None,
        display_name=None,
        observed_at=0.0,
        trust_status=IdentityTrust.TRUSTED,
        name_field_states={},
    )


def _handler(
    *,
    observed_end: int = 4,
    epoch: tuple[int, int] = (1, 0),
) -> tuple[CommandHandler, MagicMock, AsyncMock]:
    """构造只依赖总结入队端口的命令处理器。"""
    store = MagicMock()
    store.get_summary_epoch = AsyncMock(return_value=epoch)
    store.get_message_seq_end = AsyncMock(return_value=observed_end)
    store.get_summary_scope = AsyncMock(
        return_value=("private", None, "session-feedback", None)
    )
    manager = MagicMock(store=store)
    scheduler = MagicMock()
    scheduler.enqueue_manual = AsyncMock(
        return_value=SummaryEnqueueResult(
            True,
            queued=2,
            duplicates=1,
            active_parallelism=1,
            target_parallelism=2,
            reason_code=SummaryReasonCode.QUEUED,
        )
    )
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "reflection_engine.summary_trigger_rounds": 1,
    }.get(key, default)
    identity_runtime = ProtocolIdentityRuntime()
    identity_runtime.resolve = MagicMock(return_value=_trusted_identity())
    handler = CommandHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        conversation_manager=manager,
        index_validator=None,
        summary_scheduler=scheduler,
        identity_runtime=identity_runtime,
    )
    event = MagicMock()
    event.unified_msg_origin = "session-feedback"
    event.plain_result = MagicMock(side_effect=lambda message: message)
    return handler, event, scheduler.enqueue_manual


@pytest.mark.asyncio
async def test_summarize_returns_enqueue_ack_without_waiting_for_provider() -> None:
    """命令只等待入队结果，不等待 Processor 或 canonical 写入。"""
    handler, event, enqueue = _handler()

    with patch(
        "core.platform.context_helpers.get_persona_id",
        AsyncMock(return_value="persona-1"),
    ):
        results = [result async for result in handler.handle_summarize(event)]

    assert results == ["已接受：queued=2，重复=1，active=1，target=2"]
    enqueue.assert_awaited_once()
    assert enqueue.await_args is not None
    context, observed_end = enqueue.await_args.args
    assert context.start_seq == 0
    assert observed_end == 4


@pytest.mark.asyncio
async def test_summarize_reports_no_window_without_calling_scheduler() -> None:
    """少于两条消息时直接返回固定 no_window reason。"""
    handler, event, enqueue = _handler(observed_end=1)

    results = [result async for result in handler.handle_summarize(event)]

    assert results == ["未接受：reason=no_window"]
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_summarize_propagates_cancellation() -> None:
    """命令协程取消时不得把控制流转换为普通失败反馈。"""
    handler, event, enqueue = _handler()
    enqueue.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        _ = [result async for result in handler.handle_summarize(event)]


@pytest.mark.asyncio
async def test_summarize_confirm_abandon_uses_current_epoch() -> None:
    """显式确认只按当前会话 epoch 收口可放弃窗口。"""
    handler, event, enqueue = _handler(epoch=(3, 0))
    scheduler = handler._summary_scheduler
    scheduler.confirm_abandon_session_jobs = AsyncMock(return_value=2)

    results = [
        result async for result in handler.handle_summarize(event, "confirm-abandon")
    ]

    assert results == ["已确认：abandoned=2"]
    scheduler.confirm_abandon_session_jobs.assert_awaited_once_with(
        "session-feedback", 3
    )
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_summarize_rejects_unknown_action() -> None:
    """未知 summarize 动作不得入队或改变任务状态。"""
    handler, event, enqueue = _handler()

    results = [result async for result in handler.handle_summarize(event, "force")]

    assert results == ["未接受：reason=invalid_summarize_action"]
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_summarize_confirm_abandon_maps_store_failure_to_fixed_reason() -> None:
    """管理员确认存储失败不得回显异常正文。"""
    handler, event, _enqueue = _handler()
    scheduler = handler._summary_scheduler
    scheduler.confirm_abandon_session_jobs = AsyncMock(
        side_effect=RuntimeError("SUMMARY-PRIVATE-ERROR")
    )

    results = [
        result async for result in handler.handle_summarize(event, "confirm-abandon")
    ]

    assert results == ["未接受：reason=summary_action_failed"]
    assert "SUMMARY-PRIVATE-ERROR" not in results[0]
