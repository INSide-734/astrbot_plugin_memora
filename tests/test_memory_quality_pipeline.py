"""反思与手动总结的 pre-canonical 质量门组合契约。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.handlers.reflection_handler import ReflectionHandler
from core.review.memory_quality_gate import MemoryGateResult


@pytest.mark.asyncio
async def test_reflection_quarantines_low_quality_before_engine_and_evolution() -> None:
    """反思链隔离低质量候选后仍可安全推进原始会话窗口。"""

    conversation_manager = MagicMock()
    conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    conversation_manager.update_session_metadata = AsyncMock()
    processor = MagicMock()
    processor.process_conversation = AsyncMock(
        return_value=[
            {
                "content": "低质量候选",
                "importance": 0.2,
                "metadata": {"summary_quality": "low"},
                "atoms": ["must-not-persist"],
            }
        ]
    )
    engine = MagicMock()
    engine.add_memory = AsyncMock(return_value=11)
    evolution = MagicMock()
    evolution.schedule_consider = AsyncMock()
    quality_gate = MagicMock()
    quality_gate.route_candidate = AsyncMock(
        return_value=MemoryGateResult(
            action="quarantined",
            candidate_id="candidate-1",
            reason_codes=("summary_quality_low",),
        )
    )
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=MagicMock(),
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=conversation_manager,
        enforce_limit_cb=MagicMock(),
        memory_evolution_manager=evolution,
        memory_quality_gate=quality_gate,
    )
    handler._prepare_message_batches = AsyncMock(
        return_value=[[MagicMock(group_id=None)]]
    )

    await handler._storage_task(
        session_id="session-1",
        history_messages=[MagicMock(group_id=None), MagicMock(group_id=None)],
        persona_id="persona-1",
        start_index=0,
        end_index=2,
    )

    engine.add_memory.assert_not_awaited()
    evolution.schedule_consider.assert_not_awaited()
    conversation_manager.update_session_metadata.assert_any_await(
        "session-1",
        "last_summarized_index",
        2,
    )
    conversation_manager.update_session_metadata.assert_any_await(
        "session-1",
        "pending_summary",
        None,
    )


@pytest.mark.asyncio
async def test_reflection_preserves_canonical_path_for_allowed_candidate() -> None:
    """通过质量门的候选继续走现有 canonical 写入路径。"""

    conversation_manager = MagicMock()
    conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    conversation_manager.update_session_metadata = AsyncMock()
    processor = MagicMock()
    processor.process_conversation = AsyncMock(
        return_value=[
            {
                "content": "可信候选",
                "importance": 0.8,
                "metadata": {"summary_quality": "high"},
                "atoms": ["atom"],
            }
        ]
    )
    engine = MagicMock()
    engine.add_memory = AsyncMock(return_value=11)
    quality_gate = MagicMock()
    quality_gate.route_candidate = AsyncMock(
        return_value=MemoryGateResult(action="allow", reason_codes=())
    )
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=MagicMock(),
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=conversation_manager,
        enforce_limit_cb=MagicMock(),
        memory_quality_gate=quality_gate,
    )
    handler._prepare_message_batches = AsyncMock(
        return_value=[[MagicMock(group_id=None)]]
    )

    await handler._storage_task(
        session_id="session-1",
        history_messages=[MagicMock(group_id=None), MagicMock(group_id=None)],
        persona_id=None,
        start_index=0,
        end_index=2,
    )

    engine.add_memory.assert_awaited_once()


@pytest.mark.asyncio
async def test_reflection_write_cancellation_propagates_from_gather() -> None:
    """批量写入不得把 canonical 写入取消吞成普通失败。"""

    conversation_manager = MagicMock()
    conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    conversation_manager.update_session_metadata = AsyncMock()
    processor = MagicMock()
    processor.process_conversation = AsyncMock(
        return_value=[
            {
                "content": "可信候选",
                "importance": 0.8,
                "metadata": {"summary_quality": "high"},
                "atoms": [],
            }
        ]
    )
    engine = MagicMock()
    engine.add_memory = AsyncMock(side_effect=asyncio.CancelledError)
    quality_gate = MagicMock()
    quality_gate.route_candidate = AsyncMock(
        return_value=MemoryGateResult(action="allow")
    )
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=MagicMock(),
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=conversation_manager,
        enforce_limit_cb=MagicMock(),
        memory_quality_gate=quality_gate,
    )
    handler._prepare_message_batches = AsyncMock(
        return_value=[[MagicMock(group_id=None)]]
    )

    with pytest.raises(asyncio.CancelledError):
        await handler._storage_task(
            session_id="session-1",
            history_messages=[MagicMock(group_id=None), MagicMock(group_id=None)],
            persona_id=None,
            start_index=0,
            end_index=2,
        )

    conversation_manager.update_session_metadata.assert_not_awaited()
