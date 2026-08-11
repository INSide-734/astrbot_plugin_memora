"""canonical memory 导入导出能力的配置模型。"""

from pydantic import BaseModel


class ExportConfig(BaseModel):
    """记忆导入导出能力配置。"""

    enabled: bool = True


__all__ = ["ExportConfig"]
