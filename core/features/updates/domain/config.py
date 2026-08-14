"""插件更新 feature 的配置模型。"""

from pydantic import BaseModel, Field


class UpdateSettings(BaseModel):
    """插件 runtime 更新设置。"""

    enabled: bool = Field(default=True, description="是否允许管理员检查和下载插件更新")
    mirror_url: str = Field(
        default="",
        description="GitHub 下载镜像前缀；留空使用官方地址",
    )
    timeout_seconds: int = Field(
        default=30,
        ge=5,
        le=120,
        description="更新元数据和安装包请求超时时间（秒）",
    )


__all__ = ["UpdateSettings"]
