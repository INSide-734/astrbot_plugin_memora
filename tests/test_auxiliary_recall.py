"""辅助召回剩余预算与取消语义测试。"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.handlers.auxiliary_recall import AuxiliaryRecall
from core.managers.retrieval_timing import RetrievalTimingSink


def _config(values: dict[str, object]) -> MagicMock:
    """创建支持点路径默认值的最小配置对象。"""

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: values.get(key, default)
    return config


@pytest.mark.asyncio
async def test_expired_budget_skips_spontaneous_and_prospective_io() -> None:
    """主检索已耗尽预算时，两类辅助召回都不启动 I/O。"""

    engine = MagicMock()
    engine.search_memories = AsyncMock(return_value=[])
    engine.atom_store.query_upcoming_planned = AsyncMock(return_value=[])
    auxiliary = AuxiliaryRecall(
        _config(
            {
                "recall_engine.spontaneous_recall_enabled": True,
                "recall_engine.spontaneous_recall_probability": 1.0,
                "recall_engine.prospective_recall_enabled": True,
            }
        ),
        engine,
    )
    expired = time.perf_counter() - 1.0

    assert (
        await auxiliary.maybe_spontaneous_recall(
            session_id="session",
            persona_id=None,
            chat_type="private",
            deadline_monotonic=expired,
        )
        == []
    )
    assert (
        await auxiliary.maybe_prospective_recall(
            session_id="session",
            persona_id=None,
            chat_type="private",
            deadline_monotonic=expired,
        )
        == []
    )
    engine.search_memories.assert_not_awaited()
    engine.atom_store.query_upcoming_planned.assert_not_awaited()


@pytest.mark.asyncio
async def test_spontaneous_recall_uses_independent_timing_sink() -> None:
    """辅助搜索不得覆盖主检索的请求局部阶段计时。"""

    captured_sink: RetrievalTimingSink | None = None

    async def search_memories(**kwargs):
        """捕获辅助搜索的局部 sink。"""

        nonlocal captured_sink
        captured_sink = kwargs["timing_sink"]
        captured_sink.record("retrieval_total_ms", 9.0)
        return []

    engine = MagicMock()
    engine.search_memories = AsyncMock(side_effect=search_memories)
    auxiliary = AuxiliaryRecall(
        _config(
            {
                "recall_engine.spontaneous_recall_enabled": True,
                "recall_engine.spontaneous_recall_probability": 1.0,
                "recall_engine.spontaneous_recall_k": 2,
            }
        ),
        engine,
    )
    main_sink = RetrievalTimingSink()
    main_sink.record("retrieval_total_ms", 3.0)

    assert (
        await auxiliary.maybe_spontaneous_recall(
            session_id="session",
            persona_id=None,
            chat_type="private",
            deadline_monotonic=None,
        )
        == []
    )
    assert captured_sink is not None
    assert captured_sink is not main_sink
    assert main_sink.snapshot()["retrieval_total_ms"] == 3.0


@pytest.mark.asyncio
async def test_spontaneous_recall_probability_gates_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """概率值在阈值上下时必须分别触发和抑制自发召回。"""

    engine = MagicMock()
    engine.search_memories = AsyncMock(return_value=[])
    auxiliary = AuxiliaryRecall(
        _config(
            {
                "recall_engine.spontaneous_recall_enabled": True,
                "recall_engine.spontaneous_recall_probability": 0.4,
            }
        ),
        engine,
    )
    draws = iter((0.399, 0.401))

    def next_draw() -> float:
        """依次返回阈值下方和上方的确定性抽样值。"""

        return next(draws)

    monkeypatch.setattr(
        "core.handlers.auxiliary_recall.random.random",
        next_draw,
    )

    triggered = await auxiliary.maybe_spontaneous_recall(
        session_id="session",
        persona_id=None,
        chat_type="private",
        deadline_monotonic=None,
    )
    suppressed = await auxiliary.maybe_spontaneous_recall(
        session_id="session",
        persona_id=None,
        chat_type="private",
        deadline_monotonic=None,
    )

    assert triggered == []
    assert suppressed == []
    engine.search_memories.assert_awaited_once()


@pytest.mark.asyncio
async def test_auxiliary_recall_propagates_calling_task_cancellation() -> None:
    """调用方取消时辅助搜索被收束，并继续传播 ``CancelledError``。"""

    started = asyncio.Event()
    collected = asyncio.Event()

    async def search_memories(**_kwargs):
        """等待取消并用 finally 证明检索已收束。"""

        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            collected.set()

    engine = MagicMock()
    engine.search_memories = AsyncMock(side_effect=search_memories)
    auxiliary = AuxiliaryRecall(
        _config(
            {
                "recall_engine.spontaneous_recall_enabled": True,
                "recall_engine.spontaneous_recall_probability": 1.0,
            }
        ),
        engine,
    )
    task = asyncio.create_task(
        auxiliary.maybe_spontaneous_recall(
            session_id="session",
            persona_id=None,
            chat_type="private",
            deadline_monotonic=None,
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert collected.is_set()


@pytest.mark.asyncio
async def test_prospective_atom_is_converted_to_complete_hybrid_result() -> None:
    """命中的 PLANNED 原子必须生成字段完整、可直接参与注入的候选。"""

    atom = SimpleNamespace(
        parent_memory_id=42,
        content="提交复盘记录",
        event_time="2026-08-01T09:00:00Z",
        metadata={},
    )
    engine = MagicMock()
    engine.atom_store.query_upcoming_planned = AsyncMock(return_value=[atom])
    auxiliary = AuxiliaryRecall(
        _config({"recall_engine.prospective_recall_enabled": True}),
        engine,
    )

    results = await auxiliary.maybe_prospective_recall(
        session_id="session",
        persona_id=None,
        chat_type="private",
        deadline_monotonic=None,
    )

    assert len(results) == 1
    assert results[0].doc_id == 42
    assert results[0].rrf_score == 0.9
    assert results[0].bm25_score is None
    assert results[0].vector_score is None
    assert results[0].metadata["recall_source"] == "prospective"
