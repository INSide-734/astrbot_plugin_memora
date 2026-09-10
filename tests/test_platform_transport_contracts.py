"""平台 transport 宿主生命周期契约测试。"""

from __future__ import annotations

from types import SimpleNamespace

from core.platform.transport.route_lifecycle import unregister_plugin_page_routes


def test_route_lifecycle_removes_only_current_page_instance_routes() -> None:
    """路由清理应原地移除当前 Page 实例的登记并保留其他 owner。"""

    class PageApi:
        """提供能通过绑定方法 owner 区分实例的 Page API。"""

        async def route(self) -> None:
            """表示一条 Page API 路由。"""

    current_page = PageApi()
    newer_page = PageApi()
    current_handler = current_page.route
    newer_handler = newer_page.route
    registrations = [
        ("/astrbot_plugin_memora/page/status", current_handler, ["GET"], "旧实例"),
        ("/astrbot_plugin_memora/page/status", newer_handler, ["GET"], "新实例"),
        object(),
    ]
    plugin = SimpleNamespace(
        context=SimpleNamespace(registered_web_apis=registrations),
        page_api=current_page,
    )

    assert unregister_plugin_page_routes(plugin) == 1
    assert plugin.context.registered_web_apis is registrations
    assert registrations == [
        ("/astrbot_plugin_memora/page/status", newer_handler, ["GET"], "新实例"),
        registrations[-1],
    ]
    assert unregister_plugin_page_routes(plugin) == 0
