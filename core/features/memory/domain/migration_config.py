"""canonical memory schema 迁移配置模型。"""

from pydantic import BaseModel, Field


class MigrationSettings(BaseModel):
    """数据库迁移设置"""

    auto_migrate: bool = Field(default=True, description="是否启用自动迁移")
    create_backup: bool = Field(default=True, description="迁移前是否创建备份")


__all__ = ["MigrationSettings"]
