"""reflection candidate writer 的应用契约。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.features.reflection.application import candidate_writer as feature_writer


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
