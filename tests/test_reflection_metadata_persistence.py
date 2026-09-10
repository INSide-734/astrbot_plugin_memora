"""反思处理器到持久化总结调度器的入口契约。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.features.reflection.application.reflection_handler import ReflectionHandler
from core.features.reflection.domain.summary_models import (
    SummaryEnqueueResult,
    SummaryReasonCode,
)


def _handler() -> tuple[ReflectionHandler, MagicMock, AsyncMock]:
    """构造只依赖稳定来源和 scheduler 入队端口的反思处理器。"""
    store = MagicMock()
    store.get_summary_scope = AsyncMock(
        return_value=("private", None, "session-reflection", None)
    )
    store.get_summary_epoch = AsyncMock(return_value=(1, 0))
    store.get_message_seq_end = AsyncMock(return_value=4)
    manager = MagicMock(store=store)
    scheduler = MagicMock()
    scheduler.enqueue_automatic = AsyncMock(
        return_value=SummaryEnqueueResult(
            True,
            queued=1,
            reason_code=SummaryReasonCode.QUEUED,
        )
    )
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "reflection_engine.summary_trigger_rounds": 1,
    }.get(key, default)
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=manager,
        enforce_limit_cb=AsyncMock(),
        summary_scheduler=scheduler,
    )
    event = MagicMock()
    event.unified_msg_origin = "session-reflection"
    return handler, event, scheduler.enqueue_automatic


@pytest.mark.asyncio
async def test_automatic_reflection_only_enqueues_stable_context() -> None:
    """自动反思只构造稳定上下文，不直接读取正文或执行 Processor。"""
    handler, event, enqueue = _handler()

    with patch(
        "core.features.reflection.application.reflection_handler.get_persona_id",
        AsyncMock(return_value="persona-1"),
    ):
        await handler.maybe_schedule_summary(event)

    enqueue.assert_awaited_once()
    assert enqueue.await_args is not None
    context, observed_end = enqueue.await_args.args
    assert context.session_id == "session-reflection"
    assert context.session_epoch == 1
    assert context.start_seq == 0
    assert observed_end == 4


@pytest.mark.asyncio
async def test_automatic_reflection_propagates_cancellation() -> None:
    """自动入队取消时必须继续传播，不得吞成聊天主链失败。"""
    handler, event, enqueue = _handler()
    enqueue.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await handler.maybe_schedule_summary(event)
