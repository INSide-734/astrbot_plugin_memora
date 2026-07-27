"""MemoraPlugin 的命令端点混入类。"""

from collections.abc import AsyncGenerator, Callable
from typing import Any, TypeVar

from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.event.filter import PermissionType, permission_type

_HandlerT = TypeVar("_HandlerT", bound=Callable[..., object])
_COMMAND_ENDPOINT_HANDLER_NAMES = frozenset(
    {
        "memora",
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
        "update",
        "help",
    }
)


def _remove_legacy_command_handlers(registry: Any | None = None) -> None:
    """移除热重载遗留在旧命令模块中的 /memora 处理器。

    AstrBot 仅按插件入口模块卸载 handler，旧版本拆分到 ``core`` 的命令
    会在热重载后继续留在注册表并与当前入口命令冲突。本函数只按当前模块
    路径和已知的 /memora 方法名清理这些旧条目。

    参数:
        registry: 可选的 AstrBot handler 注册表；省略时使用运行时全局注册表。

    返回:
        无。命中旧 handler 时会直接从注册表删除。
    """
    if registry is None:
        try:
            from astrbot.core.star.star_handler import star_handlers_registry
        except ImportError:
            return
        registry = star_handlers_registry

    for registered_handler in list(registry):
        if (
            getattr(registered_handler, "handler_module_path", None) == __name__
            and getattr(registered_handler, "handler_name", None)
            in _COMMAND_ENDPOINT_HANDLER_NAMES
        ):
            registry.remove(registered_handler)


_remove_legacy_command_handlers()


def _bind_to_plugin_entrypoint(handler: _HandlerT) -> _HandlerT:
    """把拆分模块中的命令端点归属到插件入口模块。

    AstrBot 按处理函数的 ``__module__`` 将装饰器元数据绑定到插件实例，
    因此命令实现拆到 ``core`` 后必须在注册装饰器执行前恢复入口归属。

    参数:
        handler: 即将交给 AstrBot 命令装饰器的处理函数。

    返回:
        已标记为插件入口模块所有的原处理函数。
    """
    package_root, separator, _ = __package__.rpartition(".")
    handler.__module__ = f"{package_root}.main" if separator else "main"
    return handler


class CommandEndpointsMixin:
    """所有 /memora 命令端点。"""

    @filter.command_group("memora")
    @_bind_to_plugin_entrypoint
    def memora(self):
        """长期记忆管理命令组 `/memora`。"""
        pass

    @permission_type(PermissionType.ADMIN)
    @memora.command("status", priority=10)
    @_bind_to_plugin_entrypoint
    async def status(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 显示记忆系统状态"""
        ready, message = await self._ensure_plugin_ready(wait=False)
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return
        async for message in self.command_handler.handle_status(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @memora.command("health", priority=10)
    @_bind_to_plugin_entrypoint
    async def health(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 显示运行时健康评分。"""
        ready, message = await self._ensure_plugin_ready(wait=False)
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_health(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @memora.command("diagnostics", priority=10)
    @_bind_to_plugin_entrypoint
    async def diagnostics(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 显示实时诊断快照。"""
        ready, message = await self._ensure_plugin_ready(wait=False)
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_diagnostics(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @memora.command("search", priority=10)
    @_bind_to_plugin_entrypoint
    async def search(
        self, event: AstrMessageEvent, query: str, k: int = 5
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 搜索记忆"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_search(event, query, k):
            yield message

    @permission_type(PermissionType.ADMIN)
    @memora.command("trace", priority=10)
    @_bind_to_plugin_entrypoint
    async def trace(
        self, event: AstrMessageEvent, query: str, k: int = 5
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 对当前会话执行可解释召回追踪。"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_trace(event, query, k):
            yield message

    @permission_type(PermissionType.ADMIN)
    @memora.command("forget")
    @_bind_to_plugin_entrypoint
    async def forget(
        self, event: AstrMessageEvent, doc_id: int
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 删除指定记忆"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_forget(event, doc_id):
            yield message

    @permission_type(PermissionType.ADMIN)
    @memora.command("rebuild-index")
    @_bind_to_plugin_entrypoint
    async def rebuild_index(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 手动重建索引"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_rebuild_index(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @memora.command("rebuild-graph")
    @_bind_to_plugin_entrypoint
    async def rebuild_graph(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 手动重建图记忆索引"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_rebuild_graph(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @memora.command("webui")
    @_bind_to_plugin_entrypoint
    async def webui(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 显示 WebUI 访问信息"""
        ready, message = await self._ensure_plugin_ready(wait=False)
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_webui(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @memora.command("summarize")
    @_bind_to_plugin_entrypoint
    async def summarize(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 立即触发当前会话的记忆总结"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_summarize(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @memora.command("reset")
    @_bind_to_plugin_entrypoint
    async def reset(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 重置当前会话的长期记忆上下文"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_reset(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @memora.command("cleanup")
    @_bind_to_plugin_entrypoint
    async def cleanup(
        self, event: AstrMessageEvent, mode: str = "preview"
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 清理历史消息中的记忆注入片段

        参数：
            mode: 执行模式，"preview"（默认）为预演，"exec" 为实际清理。
        """
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        # 判断是否为执行模式
        dry_run = mode.lower() != "exec"

        async for message in self.command_handler.handle_cleanup(
            event, dry_run=dry_run
        ):
            yield message

    @permission_type(PermissionType.ADMIN)
    @memora.command("update")
    @_bind_to_plugin_entrypoint
    async def update(
        self, event: AstrMessageEvent, action: str = "check"
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 检查、安装或下载插件 runtime 更新包。"""
        ready, message = await self._ensure_plugin_ready(wait=False)
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_update(event, action):
            yield message

    @permission_type(PermissionType.ADMIN)
    @memora.command("help")
    @_bind_to_plugin_entrypoint
    async def help(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 显示帮助信息"""
        ready, message = await self._ensure_plugin_ready(wait=False)
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_help(event):
            yield message
