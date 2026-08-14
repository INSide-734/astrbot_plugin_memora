"""canonical memory feature 的图记忆检索配置模型。"""

import math

from pydantic import BaseModel, Field, model_validator


class GraphMemoryConfig(BaseModel):
    """图记忆检索配置。"""

    enabled: bool = Field(default=True, description="是否启用图记忆双路检索")
    document_route_weight: float = Field(
        default=0.65, ge=0.0, le=1.0, description="文档路权重"
    )
    graph_route_weight: float = Field(
        default=0.35, ge=0.0, le=1.0, description="图路权重"
    )
    cross_route_bonus: float = Field(
        default=0.08, ge=0.0, le=0.5, description="双路同时命中的额外加分"
    )
    expansion_limit: int = Field(
        default=24, ge=1, le=200, description="图邻居扩展候选上限"
    )
    expansion_hops: int = Field(
        default=1, ge=1, le=2, description="图关键词检索邻居扩展跳数"
    )
    second_hop_weight: float = Field(
        default=0.4, ge=0.0, le=1.0, description="二跳图扩展候选权重"
    )
    dynamic_route_weighting: bool = Field(
        default=True, description="是否按查询意图动态调整文档路和图路权重"
    )
    max_topics_per_memory: int = Field(
        default=6, ge=1, le=20, description="单条记忆最多索引主题数"
    )
    max_participants_per_memory: int = Field(
        default=8, ge=1, le=30, description="单条记忆最多索引参与者数"
    )
    max_facts_per_memory: int = Field(
        default=8, ge=1, le=30, description="单条记忆最多索引事实数"
    )
    # 原子级记忆配置
    atom_enabled: bool = Field(
        default=True, description="是否启用记忆原子化（细化粒度+时间衰减）"
    )
    atom_maintenance_interval_hours: float = Field(
        default=24.0, ge=1.0, le=168.0, description="原子生命周期维护间隔(小时)"
    )
    atom_forget_delay_days: float = Field(
        default=7.0, ge=1.0, le=90.0, description="过期原子延迟遗忘天数"
    )
    atom_purge_delay_days: float = Field(
        default=30.0, ge=1.0, le=365.0, description="遗忘原子物理清理延迟天数"
    )
    score_alpha: float = Field(
        default=0.55, ge=0.0, le=1.0, description="图向量相似度权重"
    )
    score_beta: float = Field(
        default=0.2, ge=0.0, le=1.0, description="图关键词匹配权重"
    )
    score_gamma: float = Field(
        default=0.15, ge=0.0, le=1.0, description="图时间新鲜度权重"
    )
    score_delta: float = Field(
        default=0.1, ge=0.0, le=1.0, description="图结构特征权重"
    )
    temporal_edges_enabled: bool = Field(default=True, description="是否启用时序图边")
    causal_edges_enabled: bool = Field(default=True, description="是否启用因果图边")

    @model_validator(mode="after")
    def validate_route_weights(self):
        """归一化路由权重，并拒绝总和不为一的图评分权重。"""

        total = self.document_route_weight + self.graph_route_weight
        if total <= 0:
            self.document_route_weight = 0.65
            self.graph_route_weight = 0.35
        elif total != 1.0:
            self.document_route_weight = self.document_route_weight / total
            self.graph_route_weight = self.graph_route_weight / total
        score_total = (
            self.score_alpha + self.score_beta + self.score_gamma + self.score_delta
        )
        if not math.isclose(score_total, 1.0, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError("graph_memory 评分权重总和必须为 1.0")
        return self


__all__ = ["GraphMemoryConfig"]
