"""命令端点路由和就绪门控测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.command_endpoints import CommandEndpointsMixin


async def _yielding_handler(*items):
    for item in items:
        yield item


class _Plugin(CommandEndpointsMixin):
    def __init__(self) -> None:
        self.command_handler = None
        self._ensure_plugin_ready = AsyncMock(return_value=(True, ""))

    @staticmethod
    def _command_handler_not_ready_message() -> str:
        return "handler not ready"


def _event():
    event = MagicMock()
    event.plain_result = MagicMock(side_effect=lambda message: message)
    return event


async def _collect(gen: AsyncGenerator):
    results = []
    async for item in gen:
        results.append(item)
    return results


class TestEndpointReadinessGate:
    @pytest.mark.asyncio
    async def test_status_returns_not_ready_message_when_plugin_not_ready(self) -> None:
        plugin = _Plugin()
        plugin._ensure_plugin_ready = AsyncMock(return_value=(False, "plugin warming up"))

        results = await _collect(plugin.status(_event()))

        assert results == ["plugin warming up"]
        plugin._ensure_plugin_ready.assert_awaited_once_with(wait=False)
        assert plugin.command_handler is None

    @pytest.mark.asyncio
    async def test_help_returns_handler_not_ready_when_handler_missing(self) -> None:
        plugin = _Plugin()

        results = await _collect(plugin.help(_event()))

        assert results == ["handler not ready"]
        plugin._ensure_plugin_ready.assert_awaited_once_with(wait=False)

    @pytest.mark.asyncio
    async def test_webui_uses_nonblocking_ready_check(self) -> None:
        plugin = _Plugin()
        plugin._ensure_plugin_ready = AsyncMock(return_value=(False, "plugin warming up"))

        results = await _collect(plugin.webui(_event()))

        assert results == ["plugin warming up"]
        plugin._ensure_plugin_ready.assert_awaited_once_with(wait=False)

    @pytest.mark.asyncio
    async def test_search_keeps_blocking_ready_check(self) -> None:
        plugin = _Plugin()
        plugin._ensure_plugin_ready = AsyncMock(return_value=(False, "plugin warming up"))

        results = await _collect(plugin.search(_event(), "query"))

        assert results == ["plugin warming up"]
        plugin._ensure_plugin_ready.assert_awaited_once_with()


class TestEndpointDelegation:
    @pytest.mark.asyncio
    async def test_search_delegates_query_and_k_to_command_handler(self) -> None:
        plugin = _Plugin()
        handler = MagicMock()
        handler.handle_search = MagicMock(
            return_value=_yielding_handler("search-start", "search-done")
        )
        plugin.command_handler = handler
        event = _event()

        results = await _collect(plugin.search(event, "python", k=7))

        assert results == ["search-start", "search-done"]
        handler.handle_search.assert_called_once_with(event, "python", 7)

    @pytest.mark.asyncio
    async def test_forget_delegates_doc_id_to_command_handler(self) -> None:
        plugin = _Plugin()
        handler = MagicMock()
        handler.handle_forget = MagicMock(return_value=_yielding_handler("forgot"))
        plugin.command_handler = handler
        event = _event()

        results = await _collect(plugin.forget(event, 42))

        assert results == ["forgot"]
        handler.handle_forget.assert_called_once_with(event, 42)

    @pytest.mark.asyncio
    async def test_summarize_delegates_without_extra_arguments(self) -> None:
        plugin = _Plugin()
        handler = MagicMock()
        handler.handle_summarize = MagicMock(
            return_value=_yielding_handler("queued", "done")
        )
        plugin.command_handler = handler
        event = _event()

        results = await _collect(plugin.summarize(event))

        assert results == ["queued", "done"]
        handler.handle_summarize.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_help_delegates_to_command_handler(self) -> None:
        plugin = _Plugin()
        handler = MagicMock()
        handler.handle_help = MagicMock(return_value=_yielding_handler("help text"))
        plugin.command_handler = handler
        event = _event()

        results = await _collect(plugin.help(event))

        assert results == ["help text"]
        handler.handle_help.assert_called_once_with(event)


class TestCleanupModeMapping:
    @pytest.mark.asyncio
    async def test_cleanup_preview_maps_to_dry_run_true(self) -> None:
        plugin = _Plugin()
        handler = MagicMock()
        handler.handle_cleanup = MagicMock(return_value=_yielding_handler("preview"))
        plugin.command_handler = handler
        event = _event()

        results = await _collect(plugin.cleanup(event, mode="preview"))

        assert results == ["preview"]
        handler.handle_cleanup.assert_called_once_with(event, dry_run=True)

    @pytest.mark.asyncio
    async def test_cleanup_exec_maps_to_dry_run_false(self) -> None:
        plugin = _Plugin()
        handler = MagicMock()
        handler.handle_cleanup = MagicMock(return_value=_yielding_handler("exec"))
        plugin.command_handler = handler
        event = _event()

        results = await _collect(plugin.cleanup(event, mode="exec"))

        assert results == ["exec"]
        handler.handle_cleanup.assert_called_once_with(event, dry_run=False)

    @pytest.mark.asyncio
    async def test_cleanup_mode_mapping_is_case_insensitive(self) -> None:
        plugin = _Plugin()
        handler = MagicMock()
        handler.handle_cleanup = MagicMock(return_value=_yielding_handler("exec"))
        plugin.command_handler = handler
        event = _event()

        results = await _collect(plugin.cleanup(event, mode="EXEC"))

        assert results == ["exec"]
        handler.handle_cleanup.assert_called_once_with(event, dry_run=False)
