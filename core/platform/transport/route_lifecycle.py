"""AstrBot Page 路由登记的内部兼容生命周期边界。"""

from __future__ import annotations


def unregister_plugin_page_routes(plugin: object) -> int:
    """移除绑定方法属于当前 Page 实例的路由。

    AstrBot 4.27.2 公开了路由注册接口，但没有公开反注册接口。本适配层
    仅对注入 Context 的已知登记列表做能力探针和原地更新，不构成稳定的
    AstrBot 宿主契约。

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


__all__ = ["unregister_plugin_page_routes"]
