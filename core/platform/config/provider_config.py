"""Provider 选择的运行时配置模型。"""

from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    """Provider 配置"""

    embedding_provider_id: str | None = Field(
        default="", description="嵌入模型 Provider ID"
    )
    llm_provider_id: str | None = Field(default="", description="语言模型 Provider ID")


__all__ = ["ProviderConfig"]
