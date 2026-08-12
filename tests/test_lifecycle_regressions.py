"""生命周期持有后台工作的回归测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from core.handlers.recall_handler import RecallHandler
from core.platform.composition.shutdown_lifecycle import stop_runtime_producers


@pytest.mark.asyncio
async def test_initializer_coalesces_concurrent_full_initialization(tmp_path) -> None:
    """并发 initialize 调用必须共享同一次组件构建。"""

    from core.platform.composition.plugin_initializer import PluginInitializer

    class ReadyWaiter:
        """返回受控 Provider，不等待真实时钟。"""

        attempts = 0

        async def wait_non_blocking(self, *_args):
            """返回两个已就绪的 Provider 哨兵。"""

            return object(), object(), True

    initializer = PluginInitializer(
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        str(tmp_path),
    )
    initializer._provider_waiter = ReadyWaiter()
    build_entered = asyncio.Event()
    second_call_started = asyncio.Event()
    release_build = asyncio.Event()
    build_count = 0

    async def controlled_full_init() -> None:
        """阻塞首次构建，让第二个调用与其重叠。"""

        nonlocal build_count
        build_count += 1
        build_entered.set()
        await release_build.wait()
        initializer._initialization_complete = True

    async def invoke_second() -> bool:
        """标记第二个调用开始等待 initialize 的时点。"""

        second_call_started.set()
        return await initializer.initialize()

    initializer._run_full_init = controlled_full_init
    first = asyncio.create_task(initializer.initialize())
    await build_entered.wait()
    second = asyncio.create_task(invoke_second())
    await second_call_started.wait()

    assert build_count == 1

    release_build.set()
    assert await asyncio.gather(first, second) == [True, True]
    assert build_count == 1


@pytest.mark.asyncio
async def test_provider_exhaustion_commits_terminal_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Provider 重试耗尽必须提交失败态并拒绝晚到回调。"""

    from core.platform.composition import provider_waiter as waiter_module
    from core.platform.composition.plugin_initializer import PluginInitializer

    class MissingLoader:
        """始终返回缺失的 Provider。"""

        @staticmethod
        def initialize_providers(_emb, _llm, *, silent: bool):
            """返回缺失状态且验证静默重试契约。"""

            assert silent is True
            return None, None

    async def foreground_wait(*_args):
        """跳过墙钟等待并进入生产后台重试路径。"""

        return None, None, False

    async def immediate_sleep(_delay: float) -> None:
        """立即推进确定性的重试预算。"""

    initializer = PluginInitializer(
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        str(tmp_path),
    )
    setattr(initializer, "_provider_loader", MissingLoader())
    initializer._provider_waiter._max_attempts = 2
    initializer._provider_waiter.wait_non_blocking = foreground_wait
    build_count = 0

    async def record_build() -> None:
        """记录任何不应发生的组件构建。"""

        nonlocal build_count
        build_count += 1

    initializer._run_full_init = record_build
    monkeypatch.setattr(waiter_module.asyncio, "sleep", immediate_sleep)

    assert await initializer.initialize() is False
    retry_task = initializer._provider_waiter._retry_task
    assert retry_task is not None
    await retry_task

    assert initializer.is_failed is True
    assert initializer.error_message == "Provider 重试预算耗尽"
    assert initializer.get_readiness_snapshot()["is_failed"] is True
    assert await asyncio.gather(initializer.initialize(), initializer.initialize()) == [
        False,
        False,
    ]

    await initializer._on_providers_ready(object(), object())

    assert build_count == 0
    assert initializer.embedding_provider is None
    assert initializer.llm_provider is None
    await initializer.stop_background_tasks()


@pytest.mark.asyncio
async def test_provider_retry_terminate_cancels_without_false_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """terminate 取消后台等待时不得伪造预算耗尽失败。"""

    from core.platform.composition import provider_waiter as waiter_module
    from core.platform.composition.plugin_initializer import PluginInitializer

    class MissingLoader:
        """保持 Provider 缺失直到关停。"""

        @staticmethod
        def initialize_providers(_emb, _llm, *, silent: bool):
            """返回缺失状态。"""

            assert silent is True
            return None, None

    async def foreground_wait(*_args):
        """直接转入后台等待。"""

        return None, None, False

    retry_entered = asyncio.Event()

    async def blocked_sleep(_delay: float) -> None:
        """暴露可取消的退避等待。"""

        retry_entered.set()
        await asyncio.Future()

    initializer = PluginInitializer(
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        str(tmp_path),
    )
    setattr(initializer, "_provider_loader", MissingLoader())
    initializer._provider_waiter.wait_non_blocking = foreground_wait
    monkeypatch.setattr(waiter_module.asyncio, "sleep", blocked_sleep)

    assert await initializer.initialize() is False
    retry_task = initializer._provider_waiter._retry_task
    assert retry_task is not None
    await asyncio.wait_for(retry_entered.wait(), timeout=1.0)

    await initializer.stop_background_tasks()

    assert retry_task.cancelled() is True
    assert initializer._provider_waiter._retry_task is None
    assert initializer.is_failed is False
    assert initializer.error_message is None


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
    setattr(
        handler,
        "_memory_engine",
        SimpleNamespace(
            reconsolidation=manager,
            _create_tracked_task=track,
        ),
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
            close_realtime_hub=lambda: mark("realtime_hub"),
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

    assert order == [
        "scheduler",
        "engine_tasks",
        "realtime_hub",
        "evolution_store",
        "injection",
    ]
    assert skipped == ["backfill_scheduler"]


@pytest.mark.asyncio
async def test_shutdown_removes_only_routes_owned_by_current_page_instance() -> None:
    """关停必须移除旧 Page handler，且不得影响新实例。"""

    class PageApi:
        """提供可通过绑定 owner 识别 Page 实例的 handler。"""

        async def route(self) -> None:
            """表示一条已注册的 Page 路由。"""

    async def foreign_route() -> None:
        """表示不属于当前插件实例的路由。"""

    async def noop() -> None:
        """提供立即完成的生命周期操作。"""

    async def safe_step(
        _stage: str,
        _label: str,
        operation,
        *,
        timeout: float,
    ) -> None:
        """通过生产 helper 执行生命周期操作。"""

        assert timeout == 1.0
        await operation

    current_page = PageApi()
    newer_page = PageApi()
    current_handler = current_page.route
    current_alias_handler = current_page.route
    newer_handler = newer_page.route
    routes = [
        ("/astrbot_plugin_memora/page/status", current_handler, ["GET"], "owned"),
        ("/Memora/page/status", current_alias_handler, ["GET"], "owned alias"),
        ("/astrbot_plugin_memora/page/status", newer_handler, ["GET"], "newer"),
        ("/other/status", foreign_route, ["GET"], "foreign"),
    ]
    context = SimpleNamespace(registered_web_apis=routes)
    plugin = SimpleNamespace(
        context=context,
        page_api=current_page,
        initializer=SimpleNamespace(
            stop_scheduler=noop,
            stop_memory_engine_tasks=noop,
            close_memory_evolution_components=noop,
            close_injection_components=noop,
        ),
        _backfill_scheduler=None,
    )

    await stop_runtime_producers(plugin, safe_step, lambda *_args: None, timeout=1.0)

    assert context.registered_web_apis is routes
    assert context.registered_web_apis == [
        ("/astrbot_plugin_memora/page/status", newer_handler, ["GET"], "newer"),
        ("/other/status", foreign_route, ["GET"], "foreign"),
    ]

    await stop_runtime_producers(plugin, safe_step, lambda *_args: None, timeout=1.0)
    assert context.registered_web_apis == [
        ("/astrbot_plugin_memora/page/status", newer_handler, ["GET"], "newer"),
        ("/other/status", foreign_route, ["GET"], "foreign"),
    ]


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
