"""
cost_control.py - 统一成本控制门

将额外 LLM 调用（reranker、strategy D、persona interpretation 等）
统一纳入成本模式管理。balanced/low_cost 模式下默认禁止高成本路径，
quality 模式显式允许。

使用方式:
    control = build_cost_control_from_config(cost_control_config)
    async with budgeted_extra_llm_call(control, "llm_reranker") as allowed:
        if allowed:
            result = await call_provider_once()
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .config_validator import CostControlConfig


@dataclass(frozen=True, slots=True)
class CostControl:
    """统一成本控制门 — 根据 mode 判定是否允许高成本 LLM 功能。"""

    mode: str = "balanced"  # balanced | low_cost | quality
    max_extra_llm_calls_per_turn: int = 0
    allow_llm_reranker_in_passive_recall: bool = False
    allow_llm_topic_strategy_d: bool = False
    max_reflection_parallel_llm_calls: int = 2
    llm_reranker_min_candidates: int = 12
    llm_reranker_prompt_chars: int = 3000

    def allow(self, feature: str) -> bool:
        """检查是否允许使用某高成本功能。

        支持的功能名:
        - "llm_reranker": LLM 重排序
        - "topic_strategy_d": 两阶段 LLM 话题分割
        - "reflection_extra_batch": 第 2 个及后续反思批次
        - "persona_interpretation": 人设解释生成
        - "profile_extraction": canonical 写后的自动画像提取
        - "llm_query_rewrite": LLM 查询改写
        - "memory_grounding_judge": 高风险记忆来源忠实性判断
        """
        if self.mode == "quality":
            return True
        if self.mode == "low_cost":
            # low_cost: 禁止所有额外 LLM 调用
            return False
        # balanced: 默认禁止额外 LLM 调用
        explicit_allows = {
            "llm_reranker": self.allow_llm_reranker_in_passive_recall,
            "topic_strategy_d": self.allow_llm_topic_strategy_d,
            "reflection_extra_batch": self.allow_llm_topic_strategy_d,
            "persona_interpretation": False,
            "profile_extraction": False,
            "llm_query_rewrite": False,
            "memory_grounding_judge": False,
        }
        return explicit_allows.get(feature, False)

    def deny_reason(self, feature: str) -> str:
        """返回禁止原因（用于日志）。"""
        if self.mode == "quality":
            return "质量模式已允许该额外 LLM 功能"
        if self.mode == "low_cost":
            return "低成本模式禁止额外 LLM 功能"
        if not self.allow(feature):
            return "均衡模式未显式允许该额外 LLM 功能"
        return "该功能已允许或拒绝原因未知"


def build_cost_control_from_config(
    config: CostControlConfig | Mapping[str, Any],
) -> CostControl:
    """从唯一的 typed cost_control 分支构建不可变运行时成本门。"""

    if isinstance(config, CostControlConfig):
        validated = config
    elif isinstance(config, Mapping):
        unknown = set(config) - set(CostControlConfig.model_fields)
        if unknown:
            raise TypeError("cost_control 配置必须是叶子分支，不能传入完整配置树")
        validated = CostControlConfig.model_validate(dict(config))
    else:
        raise TypeError("cost_control 配置必须是 CostControlConfig 或叶子映射")

    return CostControl(**validated.model_dump())
