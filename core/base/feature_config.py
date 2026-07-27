"""Agent、黑话和控制台的轻量功能开关配置。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentToolsConfig(BaseModel):
    """控制注册到 Agent 的工具。"""

    enable_recall_tool: bool = Field(
        default=True, description="是否启用 Agent 主动回忆工具"
    )
    enable_memorize_tool: bool = Field(
        default=False, description="是否启用 Agent 主动记忆写入工具"
    )
    enable_note_tools: bool | None = Field(
        default=None,
        description="兼容旧配置的笔记工具总开关；新配置请使用读/写拆分开关",
    )
    enable_note_read_tools: bool = Field(
        default=True,
        description="是否启用 Agent 笔记搜索/读取工具",
    )
    enable_note_write_tool: bool = Field(
        default=False,
        description="是否启用 Agent 笔记写入工具；谨慎开启",
    )
    enable_knowledge_tools: bool = Field(default=True, description="是否启用知识库工具")
    enable_profile_tools: bool = Field(default=True, description="是否启用用户画像工具")
    enable_jargon_tools: bool = Field(default=True, description="是否启用黑话查询工具")
    enable_affection_tools: bool = Field(default=True, description="是否启用好感度/情绪工具")
    enable_social_tools: bool = Field(default=True, description="是否启用社交关系工具")
    enable_expression_tools: bool = Field(default=True, description="是否启用表达模式工具")


class JargonConfig(BaseModel):
    """黑话自动发现配置。"""

    enabled: bool = Field(
        default=False,
        description="是否统计群消息候选词并调用 LLM 自动推断黑话",
    )


class DashboardConfig(BaseModel):
    """控制台运行时构建设置。"""

    allow_runtime_build: bool = Field(
        default=False,
        description="是否允许通过 Web API 在运行时执行 dashboard install/build",
    )
    build_timeout_seconds: int = Field(
        default=120,
        ge=5,
        le=1800,
        description="运行时 dashboard 构建命令的超时时间（秒）",
    )
    max_output_chars: int = Field(
        default=20000,
        ge=1000,
        le=200000,
        description="运行时 dashboard 构建命令回传输出的最大字符数",
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
    "is_jargon_discovery_enabled",
]
