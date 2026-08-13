"""Page API 的插件级常量。"""

PLUGIN_NAME = "astrbot_plugin_memora"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"
PAGE_API_ALIASES = ("Memora",)
PAGE_API_ALIAS_PREFIXES = tuple(f"/{name}/page" for name in PAGE_API_ALIASES)

__all__ = [
    "PLUGIN_NAME",
    "PAGE_API_PREFIX",
    "PAGE_API_ALIASES",
    "PAGE_API_ALIAS_PREFIXES",
]
