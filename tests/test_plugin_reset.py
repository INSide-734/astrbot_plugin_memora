"""MemoraPlugin 宿主会话重置 hook 的行为回归。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_plugin_init import _load_memora_plugin_class


def _make_plugin():
    """创建不启动后台初始化任务的插件实例。"""

    plugin_class = _load_memora_plugin_class()
    with (
        patch.object(plugin_class, "_register_official_page_api_if_available"),
        patch.object(
            plugin_class,
            "_create_tracked_task",
            side_effect=lambda coro: coro.close(),
        ),
    ):
        return plugin_class(MagicMock(), {})


def _event_with_marker(enabled: bool) -> MagicMock:
    """构造只暴露宿主 reset marker 的事件替身。"""

    event = MagicMock()
    event.get_extra.side_effect = lambda name, default=False: (
        enabled if name == "_clean_group_context_session" else default
    )
    return event


class TestMemoraPluginSessionReset:
    """验证宿主 reset/new marker 到既有清理 owner 的委托。"""

    @pytest.mark.asyncio
    async def test_group_context_marker_delegates_to_event_handler(self) -> None:
        plugin = _make_plugin()
        plugin._ensure_plugin_ready = AsyncMock(return_value=(True, ""))
        plugin.event_handler = MagicMock()
        plugin.event_handler.handle_session_reset = AsyncMock()
        event = _event_with_marker(True)

        await plugin.handle_session_reset(event)

        plugin._ensure_plugin_ready.assert_awaited_once_with()
        plugin.event_handler.handle_session_reset.assert_awaited_once_with(event)

    @pytest.mark.asyncio
    async def test_ordinary_event_does_not_clear_session(self) -> None:
        plugin = _make_plugin()
        plugin._ensure_plugin_ready = AsyncMock(return_value=(True, ""))
        plugin.event_handler = MagicMock()
        plugin.event_handler.handle_session_reset = AsyncMock()
        event = _event_with_marker(False)

        await plugin.handle_session_reset(event)

        plugin._ensure_plugin_ready.assert_not_awaited()
        plugin.event_handler.handle_session_reset.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_marker_keeps_readiness_fence_before_delegation(self) -> None:
        plugin = _make_plugin()
        plugin._ensure_plugin_ready = AsyncMock(return_value=(False, "not ready"))
        plugin.event_handler = MagicMock()
        plugin.event_handler.handle_session_reset = AsyncMock()
        event = _event_with_marker(True)

        await plugin.handle_session_reset(event)

        plugin._ensure_plugin_ready.assert_awaited_once_with()
        plugin.event_handler.handle_session_reset.assert_not_awaited()
