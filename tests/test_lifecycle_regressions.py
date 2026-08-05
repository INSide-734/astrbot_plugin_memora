"""生命周期持有后台工作的回归测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.handlers.recall_handler import RecallHandler
from core.plugin_shutdown_lifecycle import stop_runtime_producers


@pytest.mark.asyncio
async def test_reconsolidation_proposal_is_detached_from_pre_llm_hook() -> None:
    """缓慢的再巩固 Provider 不得阻塞召回 hook。"""

    started = asyncio.Event()
    release = asyncio.Event()
    tasks: list[asyncio.Task] = []

    async def slow_proposal(memory_id: int, *, context: str) -> None:
        """保持后台候选未完成，直到测试显式放行。"""

        assert (memory_id, context) == (7, "query")
        started.set()
        await release.wait()

    manager = SimpleNamespace(maybe_propose=slow_proposal)

    def track(coro) -> asyncio.Task:
        """捕获召回处理器创建并交给引擎持有的任务。"""

        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    handler = object.__new__(RecallHandler)
    handler._memory_engine = SimpleNamespace(
        reconsolidation=manager,
        _create_tracked_task=track,
    )

    await asyncio.wait_for(
        RecallHandler._maybe_propose_reconsolidation(
            handler,
            [{"id": 7, "text": "memory"}],
            "query",
        ),
        timeout=1.0,
    )

    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert len(tasks) == 1
    assert tasks[0].done() is False

    release.set()
    await tasks[0]


@pytest.mark.asyncio
async def test_shutdown_converges_producers_before_evolution_store() -> None:
    """调度器和引擎任务必须先于共享演化 Store 收敛。"""

    order: list[str] = []
    skipped: list[str] = []

    async def mark(stage: str) -> None:
        """记录可观察的关停阶段顺序。"""

        order.append(stage)

    async def safe_step(
        stage: str,
        _label: str,
        operation,
        *,
        timeout: float,
    ) -> None:
        """以测试替身执行单个关停步骤。"""

        assert timeout == 1.0
        await operation

    plugin = SimpleNamespace(
        initializer=SimpleNamespace(
            stop_scheduler=lambda: mark("scheduler"),
            stop_memory_engine_tasks=lambda: mark("engine_tasks"),
            close_memory_evolution_components=lambda: mark("evolution_store"),
            close_injection_components=lambda: mark("injection"),
        ),
        _backfill_scheduler=None,
    )

    await stop_runtime_producers(
        plugin,
        safe_step,
        lambda stage, _reason: skipped.append(stage),
        timeout=1.0,
    )

    assert order == ["scheduler", "engine_tasks", "evolution_store", "injection"]
    assert skipped == ["backfill_scheduler"]


@pytest.mark.asyncio
async def test_engine_pending_tasks_are_cancelled_before_shared_store_close() -> None:
    """生命周期 helper 必须在消费者关闭前收敛已跟踪任务。"""

    from core.managers.memory_engine_lifecycle import MemoryEngineLifecycleMixin

    engine = object.__new__(MemoryEngineLifecycleMixin)
    engine._pending_tasks = set()
    engine._pending_tasks_accepting = True
    engine._pending_tasks_lock = asyncio.Lock()

    started = asyncio.Event()

    async def blocked() -> None:
        """模拟正在使用共享演化 Store 的候选任务。"""

        started.set()
        await asyncio.Future()

    engine._create_tracked_task(blocked())
    task = next(iter(engine._pending_tasks))
    await asyncio.wait_for(started.wait(), timeout=1.0)

    await engine.stop_pending_tasks()

    assert task.cancelled() is True
    assert engine._pending_tasks == set()
    assert engine._pending_tasks_accepting is False


@pytest.mark.asyncio
async def test_engine_rejects_new_tasks_after_convergence() -> None:
    """关停开始后任何生产者都不能继续提交工作。"""

    from core.managers.memory_engine_lifecycle import MemoryEngineLifecycleMixin

    engine = object.__new__(MemoryEngineLifecycleMixin)
    engine._pending_tasks = set()
    engine._pending_tasks_accepting = False
    engine._pending_tasks_lock = asyncio.Lock()

    async def never_started() -> None:
        """必须被关闭而非调度的协程。"""

        raise AssertionError("task should not be scheduled")

    try:
        task = engine._create_tracked_task(never_started())

        assert task is None
        assert engine._pending_tasks == set()
    finally:
        for pending in tuple(engine._pending_tasks):
            pending.cancel()
        if engine._pending_tasks:
            await asyncio.gather(*engine._pending_tasks, return_exceptions=True)
