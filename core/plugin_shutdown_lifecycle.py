"""插件关停生命周期的旧路径兼容导出。"""

from .platform.composition.shutdown_lifecycle import stop_runtime_producers
from .platform.security.lifecycle import close_prompt_protection
from .platform.transport.route_lifecycle import unregister_plugin_page_routes

__all__ = [
    "close_prompt_protection",
    "stop_runtime_producers",
    "unregister_plugin_page_routes",
]
