"""MemoraPlugin 的命令端点混入类。"""

from collections.abc import AsyncGenerator

from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.event.filter import PermissionType, permission_type


class CommandEndpointsMixin:
    """所有 /lmem 命令端点。"""

    @filter.command_group("lmem")
    def lmem(self):
        """长期记忆管理命令组 `/lmem`。"""
        pass

    @permission_type(PermissionType.ADMIN)
    @lmem.command("status", priority=10)
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
    @lmem.command("health", priority=10)
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
    @lmem.command("diagnostics", priority=10)
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
    @lmem.command("search", priority=10)
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
    @lmem.command("trace", priority=10)
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
    @lmem.command("forget")
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
    @lmem.command("rebuild-index")
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
    @lmem.command("rebuild-graph")
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
    @lmem.command("webui")
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
    @lmem.command("summarize")
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
    @lmem.command("reset")
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
    @lmem.command("cleanup")
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
    @lmem.command("help")
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
