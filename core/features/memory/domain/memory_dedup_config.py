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
    semantic_mode: Literal["off", "observe", "enforce"] = Field(
        default="off",
        description=(
            "off 不做语义检测；observe 只记录 lexical 未命中后的语义命中；"
            "enforce 把阈值内的语义命中并入既有 canonical。仅 mode 非 off 时生效"
        ),
    )
    semantic_threshold: float = Field(
        default=0.90,
        ge=0.0,
        le=1.0,
        description="可选语义检索的相似度命中阈值；仅 semantic_mode 非 off 时使用",
    )
    metrics_retention_days: int = Field(
        default=30,
        ge=1,
        le=3650,
        description="去重指标小时桶保留天数，过期桶在启动与写入节流时清理",
    )


__all__ = ["MemoryDedupConfig"]
