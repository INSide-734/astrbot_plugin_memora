"""reflection candidate writer 的应用契约。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.features.quality.application.memory_quality_gate import MemoryGateResult
from core.features.reflection.application import candidate_writer as feature_writer
from core.features.reflection.domain.storage_outcomes import ReflectionStoreOutcome


def test_idempotency_key_is_stable_and_does_not_expose_content() -> None:
    """相同窗口和规范正文必须得到稳定摘要，输出不得泄露正文。"""

    key = feature_writer.build_reflection_idempotency_key(
        session_id="session-1",
        start_index=2,
        end_index=8,
        batch_index=1,
        memory_index=3,
        content="secret-canary",
    )

    assert key == feature_writer.build_reflection_idempotency_key(
        session_id="session-1",
        start_index=2,
        end_index=8,
        batch_index=1,
        memory_index=3,
        content="  secret-canary  ",
    )
    assert key != feature_writer.build_reflection_idempotency_key(
        session_id="session-1",
        start_index=2,
        end_index=8,
        batch_index=1,
        memory_index=4,
        content="secret-canary",
    )
    assert len(key) == 64
    assert "secret-canary" not in key


@pytest.mark.asyncio
async def test_candidate_writer_propagates_canonical_write_cancellation() -> None:
    """canonical 写入取消必须穿过批量收集边界向上传播。"""

    memory_engine = SimpleNamespace(
        add_memory=AsyncMock(side_effect=asyncio.CancelledError()),
        continuity_tracker=None,
    )

    with pytest.raises(asyncio.CancelledError):
        await feature_writer.store_reflection_candidates(
            [{"content": "memory", "importance": 0.8, "metadata": {}}],
            completed_idempotency_keys=set(),
            session_id="session-1",
            persona_id=None,
            start_index=0,
            end_index=2,
            is_group_chat=False,
            memory_engine=memory_engine,
            memory_quality_gate=None,
            schedule_evolution_after_write=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_discard_action_yields_discarded_outcome() -> None:
    """discard 处置不得写 canonical、不落隔离库，仅返回 DISCARDED 终态。"""

    memory_engine = SimpleNamespace(
        add_memory=AsyncMock(return_value=11),
        continuity_tracker=None,
    )
    quality_gate = SimpleNamespace(
        route_candidate=AsyncMock(
            return_value=MemoryGateResult(action="discard", reason_codes=("r1",))
        )
    )

    results = await feature_writer.store_reflection_candidates(
        [{"content": "discard-me", "importance": 0.4, "metadata": {}}],
        completed_idempotency_keys=set(),
        session_id="session-1",
        persona_id=None,
        start_index=0,
        end_index=2,
        is_group_chat=False,
        memory_engine=memory_engine,
        memory_quality_gate=quality_gate,
        schedule_evolution_after_write=AsyncMock(),
    )

    assert len(results) == 1
    assert results[0].outcome is ReflectionStoreOutcome.DISCARDED
    memory_engine.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_write_action_writes_with_tagged_metadata() -> None:
    """mark_write 处置写入 canonical 一次，携带门禁标记并返回 MARK_WRITE 终态。"""

    memory_engine = SimpleNamespace(
        add_memory=AsyncMock(return_value=11),
        continuity_tracker=None,
    )
    fake_atoms = [{"text": "低置信原子", "type": "fact"}]
    quality_gate = SimpleNamespace(
        route_candidate=AsyncMock(
            return_value=MemoryGateResult(action="mark_write", atoms=fake_atoms)
        )
    )
    candidate = {
        "content": "low-confidence",
        "importance": 0.4,
        "metadata": {"gate_disposition": "mark_write"},
        "atoms": [],
    }

    schedule_evolution_after_write = AsyncMock()
    results = await feature_writer.store_reflection_candidates(
        [candidate],
        completed_idempotency_keys=set(),
        session_id="session-1",
        persona_id="persona-1",
        start_index=0,
        end_index=2,
        is_group_chat=False,
        memory_engine=memory_engine,
        memory_quality_gate=quality_gate,
        schedule_evolution_after_write=schedule_evolution_after_write,
    )

    memory_engine.add_memory.assert_awaited_once()
    add_kwargs = memory_engine.add_memory.await_args.kwargs
    assert add_kwargs["metadata"]["gate_disposition"] == "mark_write"
    assert add_kwargs["atoms"] == fake_atoms
    assert results[0].outcome is ReflectionStoreOutcome.MARK_WRITE
    schedule_evolution_after_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_write_without_gate_atoms_falls_back_to_candidate_atoms() -> None:
    """门禁未返回 atoms 时回退候选自带 atoms，不得以空列表覆盖。"""

    memory_engine = SimpleNamespace(
        add_memory=AsyncMock(return_value=11),
        continuity_tracker=None,
    )
    quality_gate = SimpleNamespace(
        route_candidate=AsyncMock(
            return_value=MemoryGateResult(action="mark_write", atoms=None)
        )
    )
    candidate_atoms = [{"text": "候选自带原子"}]

    results = await feature_writer.store_reflection_candidates(
        [
            {
                "content": "low-confidence",
                "importance": 0.4,
                "metadata": {"gate_disposition": "mark_write"},
                "atoms": candidate_atoms,
            }
        ],
        completed_idempotency_keys=set(),
        session_id="session-1",
        persona_id=None,
        start_index=0,
        end_index=2,
        is_group_chat=False,
        memory_engine=memory_engine,
        memory_quality_gate=quality_gate,
        schedule_evolution_after_write=AsyncMock(),
    )

    memory_engine.add_memory.assert_awaited_once()
    assert memory_engine.add_memory.await_args.kwargs["atoms"] == candidate_atoms
    assert results[0].outcome is ReflectionStoreOutcome.MARK_WRITE


@pytest.mark.asyncio
async def test_group_id_and_chat_type_passed_to_gate() -> None:
    """store_reflection_candidates 必须把 group_id 与 chat_type 透传给质量门。"""

    memory_engine = SimpleNamespace(
        add_memory=AsyncMock(return_value=11),
        continuity_tracker=None,
    )
    quality_gate = SimpleNamespace(
        route_candidate=AsyncMock(
            return_value=MemoryGateResult(action="allow", reason_codes=())
        )
    )

    await feature_writer.store_reflection_candidates(
        [{"content": "memory", "importance": 0.8, "metadata": {}}],
        completed_idempotency_keys=set(),
        session_id="session-1",
        persona_id="persona-1",
        start_index=0,
        end_index=2,
        is_group_chat=True,
        group_id="group-1",
        memory_engine=memory_engine,
        memory_quality_gate=quality_gate,
        schedule_evolution_after_write=AsyncMock(),
    )

    quality_gate.route_candidate.assert_awaited_once()
    gate_kwargs = quality_gate.route_candidate.await_args.kwargs
    assert gate_kwargs["group_id"] == "group-1"
    assert gate_kwargs["chat_type"] == "group"
