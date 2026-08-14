"""跨 feature 的不可变额外 LLM 成本许可策略。"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


class CostControlConfig(BaseModel):
    """成本控制配置 — 统一管理高成本 LLM 功能的启用/降级策略。"""

    mode: str = Field(
        default="balanced",
        description="成本模式: balanced(默认，禁止额外LLM调用), low_cost(最小化token), quality(允许高成本路径)",
    )
    max_extra_llm_calls_per_turn: int = Field(
        default=0,
        ge=0,
        le=10,
        description="每轮额外 LLM 调用上限。balanced/low_cost 下默认 0",
    )
    allow_llm_reranker_in_passive_recall: bool = Field(
        default=False, description="是否允许被动召回中触发 LLM reranker"
    )
    allow_llm_topic_strategy_d: bool = Field(
        default=False, description="是否允许两阶段 LLM 话题分割（strategy D）"
    )
    max_reflection_parallel_llm_calls: int = Field(
        default=2, ge=1, le=8, description="反思时并行 LLM 调用上限"
    )
    llm_reranker_min_candidates: int = Field(
        default=12, ge=1, le=50, description="触发 LLM reranker 的最小候选数"
    )
    llm_reranker_prompt_chars: int = Field(
        default=3000, ge=500, le=10000, description="LLM reranker prompt 最大字符数"
    )


@dataclass(frozen=True, slots=True)
class CostControl:
    """根据成本模式判断额外 LLM 能力是否允许执行。"""

    mode: str = "balanced"
    max_extra_llm_calls_per_turn: int = 0
    allow_llm_reranker_in_passive_recall: bool = False
    allow_llm_topic_strategy_d: bool = False
    max_reflection_parallel_llm_calls: int = 2
    llm_reranker_min_candidates: int = 12
    llm_reranker_prompt_chars: int = 3000

    def allow(self, feature: str) -> bool:
        """判断固定名称的额外 LLM 能力是否允许执行。"""

        if self.mode == "quality":
            return True
        if self.mode == "low_cost":
            return False
        explicit_allows = {
            "llm_reranker": self.allow_llm_reranker_in_passive_recall,
            "topic_strategy_d": self.allow_llm_topic_strategy_d,
            "reflection_extra_batch": self.allow_llm_topic_strategy_d,
            "persona_interpretation": False,
            "profile_extraction": False,
            "knowledge_extraction": False,
            "note_generation": False,
            "llm_query_rewrite": False,
            "memory_grounding_judge": False,
        }
        return explicit_allows.get(feature, False)

    def deny_reason(self, feature: str) -> str:
        """返回指定能力被拒绝时的稳定中文原因。"""

        if self.mode == "quality":
            return "质量模式已允许该额外 LLM 功能"
        if self.mode == "low_cost":
            return "低成本模式禁止额外 LLM 功能"
        if not self.allow(feature):
            return "均衡模式未显式允许该额外 LLM 功能"
        return "该功能已允许或拒绝原因未知"


__all__ = ["CostControl", "CostControlConfig"]
