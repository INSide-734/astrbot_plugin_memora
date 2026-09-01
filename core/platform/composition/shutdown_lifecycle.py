"""插件关停阶段的生产者收敛编排。"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from ..security.lifecycle import close_prompt_protection
from ..transport.route_lifecycle import unregister_plugin_page_routes


async def close_initializer_injection_components(initializer: Any) -> None:
    """按记录器后存储的顺序幂等关闭注入观测组件。"""

    async with initializer._injection_close_lock:
        first_error: BaseException | None = None
        recorder = initializer.injection_decision_recorder
        if recorder is not None:
            try:
                await recorder.close(timeout=5.0)
            except BaseException as error:
                first_error = error
            else:
                if initializer.injection_decision_recorder is recorder:
                    initializer.injection_decision_recorder = None

        store = initializer.injection_decision_store
        if store is not None:
            try:
                await store.close()
            except BaseException as error:
                if first_error is None:
                    first_error = error
            else:
                if initializer.injection_decision_store is store:
                    initializer.injection_decision_store = None

        if first_error is not None:
            raise first_error


async def close_initializer_memory_evolution_components(initializer: Any) -> None:
    """按 manager 后 Store 的顺序关闭记忆演化组件。"""

    async with initializer._evolution_close_lock:
        first_error: BaseException | None = None
        manager = initializer.memory_evolution_manager
        if manager is not None:
            try:
                await manager.stop()
            except BaseException as error:
                first_error = error
            else:
                if initializer.memory_evolution_manager is manager:
                    initializer.memory_evolution_manager = None

        store = initializer.memory_evolution_store
        if store is not None:
            try:
                await store.close()
            except BaseException as error:
                if first_error is None:
                    first_error = error
            else:
                if initializer.memory_evolution_store is store:
                    initializer.memory_evolution_store = None

        if first_error is not None:
            raise first_error


async def close_initializer_core_components_after_failure(initializer: Any) -> None:
    """初始化失败时继续关闭所有已发布的核心资源，并返回首个错误。"""
    first_error: BaseException | None = None
    try:
        await close_prompt_protection(initializer)
    except BaseException as error:
        first_error = error

    conversation_manager = getattr(initializer, "conversation_manager", None)
    conversation_store = getattr(conversation_manager, "store", None)
    steps = (
        ("backfill_scheduler", initializer.backfill_scheduler, "stop"),
        ("decay_scheduler", initializer.decay_scheduler, "stop"),
        ("memory_engine", initializer.memory_engine, "close"),
        ("graph_db", initializer.graph_db, "close"),
        ("db", initializer.db, "close"),
        ("conversation_manager", conversation_store, "close"),
    )
    closed_ids: set[int] = set()
    for attribute, component, method_name in steps:
        if component is None or id(component) in closed_ids:
            continue
        closed_ids.add(id(component))
        method = getattr(component, method_name, None)
        if not callable(method):
            continue
        try:
            result = method()
            if inspect.isawaitable(result):
                await result
        except BaseException as error:
            if first_error is None:
                first_error = error
        else:
            if attribute == "conversation_manager":
                if initializer.conversation_manager is conversation_manager:
                    initializer.conversation_manager = None
            elif getattr(initializer, attribute, None) is component:
                setattr(initializer, attribute, None)

    if first_error is not None:
        raise first_error


async def stop_initializer_summary_scheduler(initializer: Any) -> None:
    """停止总结调度器，并移除初始化器与会话管理器引用。"""
    scheduler = initializer.summary_scheduler
    if scheduler is None:
        return
    await scheduler.close()
    if initializer.summary_scheduler is scheduler:
        initializer.summary_scheduler = None
    manager = initializer.conversation_manager
    if manager is not None and getattr(manager, "summary_scheduler", None) is scheduler:
        manager.summary_scheduler = None


async def stop_runtime_producers(
    plugin: object,
    safe_step: Callable[..., Awaitable[None]],
    report_skipped: Callable[[str, str], None],
    *,
    timeout: float,
) -> None:
    """按生产者到消费者的顺序收敛共享运行时组件。

    调度器、存量回填和记忆引擎仍可能创建演化或注入工作，必须先停止并
    等待这些生产者，再关闭它们共同使用的演化 Store 与注入组件。所有
    超时、取消和普通异常仍由插件入口提供的 ``safe_step`` 统一处理。

    参数:
        plugin: 持有初始化器和可选回填调度器的插件实例。
        safe_step: 带超时、日志和降级处理的插件关停步骤包装器。
        report_skipped: 记录未装配组件的关停步骤。
        timeout: 单个普通关停步骤的超时时间。
    """

    initializer: Any = getattr(plugin, "initializer")
    summary_scheduler = getattr(initializer, "summary_scheduler", None)
    stop_summary_scheduler = getattr(initializer, "stop_summary_scheduler", None)
    if summary_scheduler is not None and callable(stop_summary_scheduler):
        await safe_step(
            "summary_scheduler",
            "停止记忆总结调度器",
            stop_summary_scheduler(),
            timeout=timeout,
        )
    unregister_plugin_page_routes(plugin)
    await safe_step(
        "schedulers",
        "停止衰减调度器",
        initializer.stop_scheduler(),
        timeout=timeout,
    )

    backfill_scheduler = getattr(plugin, "_backfill_scheduler", None)
    if backfill_scheduler:
        await safe_step(
            "backfill_scheduler",
            "停止存量回填调度器",
            backfill_scheduler.stop(),
            timeout=timeout,
        )
    else:
        report_skipped("backfill_scheduler", "component_inactive")

    stop_engine_tasks = getattr(initializer, "stop_memory_engine_tasks", None)
    if callable(stop_engine_tasks):
        await safe_step(
            "engine_pending_tasks",
            "收敛记忆引擎后台任务",
            stop_engine_tasks(),
            timeout=timeout,
        )
    else:
        report_skipped("engine_pending_tasks", "component_inactive")

    await safe_step(
        "prompt_protection",
        "清理提示词保护作用域",
        close_prompt_protection(initializer),
        timeout=timeout,
    )

    close_hub = getattr(initializer, "close_realtime_hub", None)
    if callable(close_hub):
        await safe_step(
            "realtime_hub",
            "关闭实时事件 Hub",
            close_hub(),
            timeout=timeout,
        )
    else:
        report_skipped("realtime_hub", "component_inactive")

    await safe_step(
        "memory_evolution",
        "关闭记忆演化组件",
        initializer.close_memory_evolution_components(),
        timeout=timeout,
    )
    await safe_step(
        "injection_components",
        "关闭注入决策组件",
        initializer.close_injection_components(),
        timeout=timeout,
    )


__all__ = [
    "close_initializer_core_components_after_failure",
    "close_initializer_injection_components",
    "close_initializer_memory_evolution_components",
    "stop_initializer_summary_scheduler",
    "stop_runtime_producers",
]
