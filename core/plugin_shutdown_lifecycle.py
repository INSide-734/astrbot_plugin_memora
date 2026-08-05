"""插件关停阶段的生产者收敛编排。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


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

    Args:
        plugin: 持有初始化器和可选回填调度器的插件实例。
        safe_step: 带超时、日志和降级处理的插件关停步骤包装器。
        report_skipped: 记录未装配组件的关停步骤。
        timeout: 单个普通关停步骤的超时时间。
    """

    initializer: Any = getattr(plugin, "initializer")
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


__all__ = ["stop_runtime_producers"]
