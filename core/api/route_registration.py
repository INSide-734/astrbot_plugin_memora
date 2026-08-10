"""Page API 主路由、兼容别名与审计元数据的统一注册辅助。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def make_page_route_registrar(
    *,
    raw_register: Callable[..., Any],
    route_metadata: list[dict[str, Any]],
    metadata_builder: Callable[..., dict[str, Any]],
    primary_prefix: str,
    alias_prefixes: tuple[str, ...],
) -> Callable[..., Any]:
    """创建同时注册主路由、兼容别名和审计元数据的调用器。

    Args:
        raw_register: AstrBot 提供的原始 Web API 注册函数。
        route_metadata: 接收主路由审计元数据的可变列表。
        metadata_builder: 根据路由参数构建审计元数据的函数。
        primary_prefix: 主 Page API 路由前缀。
        alias_prefixes: 需要同步注册的兼容路由前缀。

    Returns:
        与 AstrBot 原始注册函数参数兼容的路由注册调用器。
    """

    def register(
        path: str,
        handler: Callable[..., Any],
        methods: list[str],
        description: str,
    ) -> Any:
        """注册单条主路由，并同步其兼容别名和审计元数据。"""
        route_metadata.append(metadata_builder(path, handler, methods, description))
        result = raw_register(path, handler, methods, description)
        if path.startswith(primary_prefix):
            suffix = path[len(primary_prefix) :]
            for alias_prefix in alias_prefixes:
                raw_register(
                    alias_prefix + suffix,
                    handler,
                    methods,
                    f"{description}（别名）",
                )
        return result

    return register
