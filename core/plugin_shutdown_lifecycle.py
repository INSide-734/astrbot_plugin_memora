"""插件关停阶段的生产者收敛编排。"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any


def unregister_plugin_page_routes(plugin: object) -> int:
    """移除绑定方法属于当前 Page 实例的路由。

    AstrBot 公开了路由注册接口但未提供反注册接口。受支持版本把注册记录
    保存在注入的 Context 上，因此这里先检查宿主能力，再原地更新共享列表。

    参数:
        plugin: 持有 Context 与 Page API 对象的插件实例。

    返回:
        从 Context 中移除的注册项数量。
    """

    page_api = getattr(plugin, "page_api", None)
    context = getattr(plugin, "context", None)
    registrations = getattr(context, "registered_web_apis", None)
    if page_api is None or not isinstance(registrations, list):
        return 0

    retained = []
    removed = 0
    for registration in registrations:
        handler = (
            registration[1]
            if isinstance(registration, (list, tuple)) and len(registration) > 1
            else None
        )
        if getattr(handler, "__self__", None) is page_api:
            removed += 1
        else:
            retained.append(registration)
    registrations[:] = retained
    return removed


async def close_prompt_protection(initializer: object) -> None:
    """关闭组合根发布的提示词保护端口并清理运行时作用域。

    参数:
        initializer: 持有平台提示词保护端口的组合根。
    """

    protection = getattr(initializer, "prompt_protection", None)
    if protection is None:
        return
    try:
        closer = getattr(protection, "close", None)
        if callable(closer):
            result = closer()
            if inspect.isawaitable(result):
                await result
    finally:
        if getattr(initializer, "prompt_protection", None) is protection:
            setattr(initializer, "prompt_protection", None)


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
    "close_prompt_protection",
    "stop_runtime_producers",
    "unregister_plugin_page_routes",
]
