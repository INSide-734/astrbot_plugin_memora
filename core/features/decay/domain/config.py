"""记忆衰减 feature 的配置模型。"""

from pydantic import BaseModel, Field


class ForgettingAgentConfig(BaseModel):
    """遗忘代理配置。"""

    auto_cleanup_enabled: bool = Field(
        default=True, description="是否启用每日自动清理旧记忆"
    )
    cleanup_days_threshold: int = Field(
        default=30, ge=1, le=3650, description="清理天数阈值"
    )
    cleanup_importance_threshold: float = Field(
        default=0.3, ge=0.0, le=1.0, description="清理重要性阈值"
    )


class ImportanceDecayConfig(BaseModel):
    """重要性衰减配置。"""

    decay_rate: float = Field(default=0.01, ge=0.0, le=1.0, description="每日衰减率")
    access_decay_window_days: float = Field(
        default=30.0, ge=1.0, le=3650.0, description="访问频次强化的有效窗口天数"
    )
    access_decay_max_count: int = Field(
        default=10, ge=1, le=10000, description="抵消衰减所需的访问次数上限"
    )
    access_count_decay_multiplier: float = Field(
        default=0.5, ge=0.0, le=1.0, description="每日衰减后访问次数保留比例"
    )


__all__ = ["ForgettingAgentConfig", "ImportanceDecayConfig"]
