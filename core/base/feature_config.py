"""Agent、黑话和控制台的轻量功能开关配置。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..platform.config.feature_contributions import UpdateSettings
from ..platform.config.transport_config import AgentToolsConfig, DashboardConfig


class JargonConfig(BaseModel):
    """黑话自动发现配置。"""

    enabled: bool = Field(
        default=False,
        description="是否统计群消息候选词并调用 LLM 自动推断黑话",
    )


def is_jargon_discovery_enabled(config_manager: Any | None) -> bool:
    """读取黑话自动发现开关，并为旧的无配置调用方保留兼容行为。

    参数:
        config_manager: 插件配置管理器；旧调用方可能尚未提供该对象。

    返回:
        配置管理器存在时返回 ``jargon.enabled``；旧调用方缺少管理器时返回
        ``True``，以免改变其原有可用性。
    """
    if config_manager is None:
        return True
    return bool(config_manager.get("jargon.enabled", False))


__all__ = [
    "AgentToolsConfig",
    "DashboardConfig",
    "JargonConfig",
    "UpdateSettings",
    "is_jargon_discovery_enabled",
]
