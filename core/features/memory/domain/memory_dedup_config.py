"""跨窗口近重复合并的配置模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MemoryDedupConfig(BaseModel):
    """反思产线写入前的近重复检测与合并配置快照。"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_default=True,
    )

    mode: Literal["off", "observe", "enforce"] = Field(
        default="off",
        description="off 不检测；observe 只记录命中；enforce 把命中候选并入既有 canonical",
    )
    similarity_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="canonical token 集合 Jaccard 命中阈值",
    )
    candidate_limit: int = Field(
        default=5,
        ge=1,
        le=50,
        description="每次比较读取的近期同会话 canonical 数量上限",
    )
    min_tokens: int = Field(
        default=12,
        ge=1,
        le=1000,
        description="短文本护栏：token 数低于该值的正文不参与比较",
    )


__all__ = ["MemoryDedupConfig"]
