"""canonical memory 写入日志修复的配置模型。"""

from pydantic import BaseModel


class WriteReliabilityConfig(BaseModel):
    """canonical 写入日志修复配置。"""

    repair_enabled: bool = True
    max_retries: int = 3


__all__ = ["WriteReliabilityConfig"]
