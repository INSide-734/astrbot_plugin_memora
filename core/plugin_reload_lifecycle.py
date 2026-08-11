"""插件延迟重载生命周期的旧路径兼容导出。"""

# 保留旧 monkeypatch 属性；新旧路径共享同一个标准库模块对象。
import asyncio as asyncio

from .platform.composition.reload_lifecycle import (
    run_scheduled_plugin_reload,
    schedule_learning_reload,
    supports_learning_reload_callback,
)

__all__ = [
    "run_scheduled_plugin_reload",
    "schedule_learning_reload",
    "supports_learning_reload_callback",
]
