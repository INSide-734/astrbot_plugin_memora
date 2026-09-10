"""LLM 请求前召回的非阻塞 readiness 契约。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_plugin_init import _load_memora_plugin_class


def _plugin_shell():
    """创建不触发插件初始化副作用的最小实例。"""

    plugin_class = _load_memora_plugin_class()
    plugin = object.__new__(plugin_class)
    plugin.config_manager = MagicMock()
    plugin.config_manager.get.return_value = 800
    return plugin


@pytest.mark.asyncio
async def test_llm_recall_uses_nonblocking_readiness() -> None:
    """LLM 请求钩子必须显式使用非阻塞 readiness。"""

    plugin = _plugin_shell()
    plugin._ensure_plugin_ready = AsyncMock(return_value=(False, "初始化中"))
    plugin.event_handler = MagicMock()

    await plugin.handle_memory_recall(MagicMock(), MagicMock())

    plugin._ensure_plugin_ready.assert_awaited_once_with(wait=False)
    plugin.event_handler.handle_memory_recall.assert_not_called()


@pytest.mark.asyncio
async def test_cold_recall_does_not_assemble_runtime_components() -> None:
    """初始化快照未发布运行期组件时不得在请求内补装组件。"""

    plugin = _plugin_shell()
    plugin.initializer = MagicMock()
    plugin.initializer.ensure_initialized = AsyncMock(return_value=True)
    plugin.get_readiness_snapshot = MagicMock(return_value={"is_initialized": True})
    plugin._ensure_runtime_components = AsyncMock(return_value=True)
    plugin._get_initialization_status_message = MagicMock(return_value="初始化中")
    plugin.event_handler = None
    plugin.command_handler = None
    plugin._terminating = False

    ready, _ = await plugin._ensure_plugin_ready(wait=False)

    assert ready is False
    plugin.initializer.ensure_initialized.assert_not_awaited()
    plugin._ensure_runtime_components.assert_not_awaited()


@pytest.mark.asyncio
async def test_ready_recall_still_delegates_once() -> None:
    """已就绪时非阻塞检查仍应把请求准确委托一次。"""

    plugin = _plugin_shell()
    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, ""))
    plugin.event_handler = SimpleNamespace(handle_memory_recall=AsyncMock())
    event = MagicMock()
    request = MagicMock()

    await plugin.handle_memory_recall(event, request)

    plugin.event_handler.handle_memory_recall.assert_awaited_once()
    call = plugin.event_handler.handle_memory_recall.await_args
    assert call.args == (event, request)
    assert call.kwargs["timing_context"].deadline_monotonic is not None
