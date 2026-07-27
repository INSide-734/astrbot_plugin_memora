"""命令端点路由和就绪门控测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
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

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method_name", ("health", "diagnostics"))
    async def test_diagnostic_snapshot_commands_use_nonblocking_ready_check(
        self,
        method_name: str,
    ) -> None:
        plugin = _Plugin()
        plugin._ensure_plugin_ready = AsyncMock(return_value=(False, "plugin warming up"))

        results = await _collect(getattr(plugin, method_name)(_event()))

        assert results == ["plugin warming up"]
        plugin._ensure_plugin_ready.assert_awaited_once_with(wait=False)

    @pytest.mark.asyncio
    async def test_trace_keeps_blocking_ready_check(self) -> None:
        plugin = _Plugin()
        plugin._ensure_plugin_ready = AsyncMock(return_value=(False, "plugin warming up"))

        results = await _collect(plugin.trace(_event(), "query"))

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

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "handler_name", "messages"),
        (
            ("health", "handle_health", ("health-start", "health-done")),
            (
                "diagnostics",
                "handle_diagnostics",
                ("diagnostics-start", "diagnostics-done"),
            ),
        ),
    )
    async def test_diagnostic_snapshot_commands_delegate_all_messages(
        self,
        method_name: str,
        handler_name: str,
        messages: tuple[str, str],
    ) -> None:
        plugin = _Plugin()
        handler = MagicMock()
        setattr(
            handler,
            handler_name,
            MagicMock(return_value=_yielding_handler(*messages)),
        )
        plugin.command_handler = handler
        event = _event()

        results = await _collect(getattr(plugin, method_name)(event))

        assert results == list(messages)
        getattr(handler, handler_name).assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_trace_delegates_query_and_k_to_command_handler(self) -> None:
        plugin = _Plugin()
        handler = MagicMock()
        handler.handle_trace = MagicMock(
            return_value=_yielding_handler("trace-start", "trace-done")
        )
        plugin.command_handler = handler
        event = _event()

        results = await _collect(plugin.trace(event, "python", k=7))

        assert results == ["trace-start", "trace-done"]
        handler.handle_trace.assert_called_once_with(event, "python", 7)


def test_diagnostic_endpoints_keep_admin_permission_decorators() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "core" / "command_endpoints.py"
    ).read_text(encoding="utf-8")

    for command in ("health", "diagnostics", "trace"):
        marker = f'@memora.command("{command}"'
        command_index = source.index(marker)
        permission_index = source.rfind(
            "@permission_type(PermissionType.ADMIN)",
            0,
            command_index,
        )
        previous_command_index = source.rfind("@memora.command(", 0, command_index)

        assert permission_index > previous_command_index


def test_command_endpoints_are_owned_by_plugin_entrypoint() -> None:
    """确保拆分后的命令仍由 AstrBot 插件入口模块注册。"""
    endpoint_names = (
        "status",
        "health",
        "diagnostics",
        "search",
        "trace",
        "forget",
        "rebuild_index",
        "rebuild_graph",
        "webui",
        "summarize",
        "reset",
        "cleanup",
        "help",
    )

    for endpoint_name in endpoint_names:
        endpoint = getattr(CommandEndpointsMixin, endpoint_name)
        assert endpoint.__module__ == "main"


def test_legacy_command_handlers_are_removed_without_touching_others() -> None:
    """只清理旧模块归属的 /memora 命令处理器。"""
    from core.command_endpoints import _remove_legacy_command_handlers

    class Handler:
        """用于模拟 AstrBot 注册表条目的最小对象。"""

        def __init__(self, module_path: str, name: str) -> None:
            """保存处理器所属模块和方法名称。"""
            self.handler_module_path = module_path
            self.handler_name = name

    class Registry:
        """用于观察注册表删除范围的最小容器。"""

        def __init__(self, handlers: list[Handler]) -> None:
            """使用传入的处理器列表初始化注册表。"""
            self.handlers = handlers

        def __iter__(self):
            """返回当前处理器的迭代器。"""
            return iter(self.handlers)

        def remove(self, handler: Handler) -> None:
            """删除指定的处理器。"""
            self.handlers.remove(handler)

    legacy_group = Handler("core.command_endpoints", "memora")
    legacy_endpoint = Handler("core.command_endpoints", "rebuild_graph")
    non_command_handler = Handler("core.command_endpoints", "unrelated")
    another_plugin_handler = Handler("other_plugin.commands", "memora")
    registry = Registry(
        [
            legacy_group,
            legacy_endpoint,
            non_command_handler,
            another_plugin_handler,
        ]
    )

    _remove_legacy_command_handlers(registry)

    assert registry.handlers == [non_command_handler, another_plugin_handler]


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
