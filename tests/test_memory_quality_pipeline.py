"""反思与手动总结的 pre-canonical 质量门组合契约。"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.handlers.reflection_handler as reflection_handler_module
from core.handlers.reflection_handler import ReflectionHandler
from core.models.conversation_models import Message
from core.processors.memory_processor import MemoryProcessor
from core.review.memory_quality_gate import MemoryGateResult


def _pipeline_message(index: int, role: str, content: str) -> Message:
    """构造质量管线端到端回归使用的稳定消息。"""

    return Message(
        id=index + 1,
        session_id="session-1",
        role=role,
        content=content,
        sender_id="user-1" if role == "user" else "bot-1",
        sender_name="Alice" if role == "user" else "Memora",
        timestamp=time.time() + index,
    )


@pytest.mark.asyncio
async def test_reflection_quarantines_low_quality_before_engine_and_evolution() -> None:
    """反思链隔离低质量候选后仍可安全推进原始会话窗口。"""

    conversation_manager = MagicMock()
    conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    conversation_manager.update_session_metadata = AsyncMock(return_value=True)
    conversation_manager.update_session_metadata_fields = AsyncMock(return_value=True)
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
    conversation_manager.update_session_metadata_fields.assert_awaited_once_with(
        "session-1",
        {
            "last_summarized_index": 2,
            "pending_summary": None,
        },
    )


@pytest.mark.asyncio
async def test_reflection_preserves_canonical_path_for_allowed_candidate() -> None:
    """通过质量门的候选继续走现有 canonical 写入路径。"""

    conversation_manager = MagicMock()
    conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    conversation_manager.update_session_metadata = AsyncMock(return_value=True)
    conversation_manager.update_session_metadata_fields = AsyncMock(return_value=True)
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
async def test_reflection_reports_canonical_and_quarantine_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """混合窗口必须分别报告 canonical 与 quarantine，不能合并为成功存储。"""

    conversation_manager = MagicMock()
    conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    conversation_manager.update_session_metadata = AsyncMock(return_value=True)
    conversation_manager.update_session_metadata_fields = AsyncMock(return_value=True)
    processor = MagicMock()
    processor.process_conversation = AsyncMock(
        return_value=[
            {
                "content": "需要隔离的候选",
                "importance": 0.2,
                "metadata": {"summary_quality": "low"},
                "atoms": [],
            },
            {
                "content": "允许写入的候选",
                "importance": 0.8,
                "metadata": {"summary_quality": "high"},
                "atoms": [],
            },
        ]
    )
    engine = MagicMock()
    engine.add_memory = AsyncMock(return_value=11)
    quality_gate = MagicMock()
    quality_gate.route_candidate = AsyncMock(
        side_effect=[
            MemoryGateResult(action="quarantined", candidate_id="candidate-1"),
            MemoryGateResult(action="allow"),
        ]
    )
    events: list[tuple[str, dict[str, object]]] = []

    def capture_event(event_name: str, **fields: object) -> None:
        """捕获不含正文的诊断标量供断言。"""

        events.append((event_name, fields))

    monkeypatch.setattr(
        reflection_handler_module.observability,
        "report_debug_event",
        capture_event,
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
    write_event = next(
        fields
        for name, fields in events
        if name == "storage_task"
        and fields.get("reason_code") == "memory_write_completed"
    )
    assert write_event["canonical_count"] == 1
    assert write_event["quarantine_count"] == 1
    assert write_event["failed_count"] == 0
    assert write_event["skipped_idempotent_count"] == 0


@pytest.mark.asyncio
async def test_reflection_canonical_failure_preserves_pending_window() -> None:
    """真实 canonical 写入失败时必须保留重试状态且不得推进窗口。"""

    conversation_manager = MagicMock()
    conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    conversation_manager.update_session_metadata = AsyncMock(return_value=True)
    conversation_manager.update_session_metadata_fields = AsyncMock(return_value=True)
    processor = MagicMock()
    processor.process_conversation = AsyncMock(
        return_value=[
            {
                "content": "写入失败的候选",
                "importance": 0.8,
                "metadata": {"summary_quality": "high"},
                "atoms": [],
            }
        ]
    )
    engine = MagicMock()
    engine.add_memory = AsyncMock(side_effect=RuntimeError("write failed"))
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

    await handler._storage_task(
        session_id="session-1",
        history_messages=[MagicMock(group_id=None), MagicMock(group_id=None)],
        persona_id=None,
        start_index=0,
        end_index=2,
    )

    metadata_updates = conversation_manager.update_session_metadata.await_args_list
    assert not any(call.args[1] == "last_summarized_index" for call in metadata_updates)
    pending_call = next(
        call for call in metadata_updates if call.args[1] == "pending_summary"
    )
    assert pending_call.args[2]["failed_count"] == 1
    assert pending_call.args[2]["failed_stage"] == "memory_write"


@pytest.mark.asyncio
async def test_grounded_fact_reaches_canonical_write_end_to_end() -> None:
    """普通偏好事实应从真实抽取与来源校验一路到达 canonical 写入。"""

    source = "我喜欢喝咖啡。"
    response_payload = {
        "memories": [
            {
                "content": "Alice喜欢喝咖啡。",
                "key_facts": ["Alice喜欢喝咖啡。"],
                "topics": ["咖啡偏好"],
                "importance": 0.8,
                "sentiment": "positive",
                "source_refs": [{"message_index": 0, "start": 0, "end": len(source)}],
            }
        ],
        "confidence": 0.9,
        "extraction_quality": "high",
    }
    provider = MagicMock()
    provider.text_chat = AsyncMock(
        return_value=MagicMock(completion_text=json.dumps(response_payload))
    )
    processor = MemoryProcessor(llm_provider=provider)
    history = [
        _pipeline_message(0, "user", source),
        _pipeline_message(1, "assistant", "好的，我记住了。"),
    ]
    conversation_manager = MagicMock()
    conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    conversation_manager.update_session_metadata = AsyncMock(return_value=True)
    conversation_manager.update_session_metadata_fields = AsyncMock(return_value=True)
    engine = MagicMock()
    engine.add_memory = AsyncMock(return_value=11)
    quality_gate = MagicMock()

    async def allow_grounded_candidate(candidate, **kwargs):
        """断言真实处理器已经把正常事实标记为允许写入。"""

        assert candidate["metadata"]["quality_gate_action"] == "allow"
        assert candidate["metadata"]["grounding_status"] == "grounded"
        return MemoryGateResult(action="allow", reason_codes=())

    quality_gate.route_candidate = AsyncMock(side_effect=allow_grounded_candidate)
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=MagicMock(),
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=conversation_manager,
        enforce_limit_cb=MagicMock(),
        memory_quality_gate=quality_gate,
    )
    handler._prepare_message_batches = AsyncMock(return_value=[history])

    await handler._storage_task(
        session_id="session-1",
        history_messages=history,
        persona_id=None,
        start_index=0,
        end_index=2,
    )

    quality_gate.route_candidate.assert_awaited_once()
    engine.add_memory.assert_awaited_once()


@pytest.mark.asyncio
async def test_reflection_write_cancellation_propagates_from_gather() -> None:
    """批量写入不得把 canonical 写入取消吞成普通失败。"""

    conversation_manager = MagicMock()
    conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    conversation_manager.update_session_metadata = AsyncMock()
    conversation_manager.update_session_metadata_fields = AsyncMock(return_value=True)
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
