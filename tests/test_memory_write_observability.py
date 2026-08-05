"""canonical 写入阶段计时与 Evolution 诊断契约。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.handlers.reflection_handler as reflection_handler_module
import core.monitoring.memory_write_timing as write_timing
from core.handlers.reflection_handler import ReflectionHandler
from core.managers.memory_engine import MemoryEngine
from core.managers.memory_engine_evolution_hooks import (
    MemoryEngineEvolutionHooksMixin,
)
from core.retrieval.memory_lifecycle import MemoryLifecycleManager


@pytest.mark.asyncio
async def test_add_memory_reports_each_safe_write_stage_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """嵌套 lifecycle 计时必须按固定阶段去重并且不泄露业务数据。"""

    events: list[dict[str, object]] = []

    def capture_event(event_name: str, **fields: object) -> None:
        """捕获写入阶段安全标量。"""

        events.append({"event": event_name, **fields})

    monkeypatch.setattr(write_timing, "report_debug_event", capture_event)
    vector = MagicMock()
    vector.add_document = AsyncMock(return_value=42)
    bm25 = MagicMock()
    bm25.add_document = AsyncMock()
    lifecycle = MemoryLifecycleManager(bm25, vector)
    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    engine.hybrid_retriever = MagicMock()
    engine.hybrid_retriever.add_memory = lifecycle.add_memory
    engine.graph_memory_manager = MagicMock()
    engine.graph_memory_manager.index_memory = AsyncMock()
    engine.atom_store = None
    engine._write_journal.start_op = AsyncMock(return_value=1)
    engine._write_journal.advance_op = AsyncMock()
    engine._retrieval = MagicMock()
    engine._retrieval.invalidate_cache = MagicMock()
    engine._retrieval.apply_interference = MagicMock(return_value=None)
    engine._retrieval.extract_triggers = MagicMock(return_value=None)
    engine._create_tracked_task = MagicMock()
    engine._schedule_domain_proposals_after_write = MagicMock()

    assert await engine.add_memory("private memory") == 42

    assert [event["stage"] for event in events] == [
        "document_vector",
        "fts",
        "atom",
        "graph",
        "evolution",
    ]
    assert all(float(event["duration_ms"]) >= 0 for event in events)
    forbidden = {"content", "memory_id", "doc_id", "session_id", "source_refs"}
    assert all(forbidden.isdisjoint(event) for event in events)


@pytest.mark.asyncio
async def test_failed_canonical_write_does_not_emit_completed_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """canonical 写入口抛错时不得把局部耗时报告为已完成写入。"""

    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        write_timing,
        "report_debug_event",
        lambda event_name, **fields: events.append({"event": event_name, **fields}),
    )

    @write_timing.observe_memory_write
    async def fail_write() -> None:
        """模拟 document/vector 阶段之后失败的 canonical 写入口。"""

        with write_timing.measure_memory_write_stage("document_vector"):
            raise RuntimeError("write failed")

    with pytest.raises(RuntimeError, match="write failed"):
        await fail_write()

    assert events == []


class _EngineEvolutionHarness(MemoryEngineEvolutionHooksMixin):
    """为引擎演化钩子提供最小依赖。"""

    def __init__(self, manager: object) -> None:
        """保存待测的演化管理器。"""

        self.memory_evolution_manager = manager


@pytest.mark.asyncio
async def test_engine_evolution_disabled_skips_source_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """disabled 模式必须在读取 canonical source 前短路。"""

    manager = MagicMock(mode="disabled")
    manager.store.load_sources = AsyncMock()
    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        "core.managers.memory_engine_evolution_hooks.report_debug_event",
        lambda event_name, **fields: events.append({"event": event_name, **fields}),
    )

    await _EngineEvolutionHarness(manager)._schedule_evolution_after_write(17)

    manager.store.load_sources.assert_not_awaited()
    assert events[-1]["reason_code"] == "evolution_disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize("should_enqueue", [False, True])
async def test_engine_evolution_reports_actual_gate_decision(
    monkeypatch: pytest.MonkeyPatch,
    should_enqueue: bool,
) -> None:
    """引擎调度只能在 Gate 确实入队时报告 scheduled。"""

    source = MagicMock()
    manager = MagicMock(mode="active")
    manager.store.load_sources = AsyncMock(return_value=[source])
    manager.schedule_consider = AsyncMock(
        return_value=SimpleNamespace(should_enqueue=should_enqueue)
    )
    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        "core.managers.memory_engine_evolution_hooks.report_debug_event",
        lambda event_name, **fields: events.append({"event": event_name, **fields}),
    )

    await _EngineEvolutionHarness(manager)._schedule_evolution_after_write(17)

    expected = "evolution_scheduled" if should_enqueue else "evolution_skipped"
    assert events[-1]["reason_code"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("should_enqueue", [False, True])
async def test_reflection_evolution_reports_actual_gate_decision(
    monkeypatch: pytest.MonkeyPatch,
    should_enqueue: bool,
) -> None:
    """兼容调度只能在 Gate 确实入队时报告 scheduled。"""

    source = MagicMock()
    manager = MagicMock(mode="active")
    manager.store.load_sources = AsyncMock(return_value=[source])
    manager.schedule_consider = AsyncMock(
        return_value=SimpleNamespace(should_enqueue=should_enqueue)
    )
    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        reflection_handler_module,
        "report_debug_event",
        lambda event_name, **fields: events.append({"event": event_name, **fields}),
    )
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=MagicMock(),
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=MagicMock(),
        enforce_limit_cb=MagicMock(),
        memory_evolution_manager=manager,
    )

    await handler._schedule_evolution_after_write(17)

    expected = "evolution_scheduled" if should_enqueue else "evolution_skipped"
    assert events[-1]["reason_code"] == expected
