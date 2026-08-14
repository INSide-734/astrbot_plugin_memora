"""会话 feature 的配置模型。"""

from pydantic import BaseModel, Field


class SessionManagerConfig(BaseModel):
    """会话管理器配置"""

    max_sessions: int = Field(
        default=100, ge=1, le=10000, description="最大会话缓存数量"
    )
    session_ttl: int = Field(
        default=3600, ge=60, le=86400, description="会话生存时间（秒）"
    )
    context_window_size: int = Field(
        default=50, ge=1, le=1000, description="上下文窗口大小"
    )
    enable_full_group_capture: bool = Field(
        default=True, description="是否捕获群聊中的所有消息(包括非@Bot的消息)"
    )
    max_messages_per_session: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="单会话最大消息数量(超出后自动删除旧消息)",
    )
    cleanup_batch_size: int = Field(
        default=50,
        ge=1,
        le=1000,
        description="历史消息超过上限后每次批量删除的旧已总结消息数",
    )


__all__ = ["SessionManagerConfig"]
