"""派生索引重建的运行时配置模型。"""

from pydantic import BaseModel, Field


class IndexRebuildSettings(BaseModel):
    """索引重建设置"""

    batch_size: int = Field(default=50, ge=1, le=500, description="重建读取批量")
    embedding_batch_size: int = Field(
        default=8, ge=1, le=256, description="Embedding 请求批量"
    )
    tasks_limit: int = Field(default=1, ge=1, le=8, description="Embedding 并发上限")
    max_retries: int = Field(default=5, ge=1, le=8, description="批次最大重试次数")
    retry_base_delay: float = Field(
        default=30.0, ge=0.0, le=60.0, description="重试基础等待秒数"
    )
    batch_delay: float = Field(
        default=5.0, ge=0.0, le=10.0, description="读取批次间隔秒数"
    )
    request_delay: float = Field(
        default=5.0, ge=0.0, le=60.0, description="Embedding 请求间隔秒数"
    )
    max_failure_ratio: float = Field(
        default=0.02, ge=0.0, le=1.0, description="允许切换的最大失败比例"
    )


__all__ = ["IndexRebuildSettings"]
