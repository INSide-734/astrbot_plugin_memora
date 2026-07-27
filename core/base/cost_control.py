"""
cost_control.py - 统一成本控制门

将额外 LLM 调用（reranker、strategy D、persona interpretation 等）
统一纳入成本模式管理。balanced/low_cost 模式下默认禁止高成本路径，
quality 模式显式允许。

使用方式:
    cc = CostControl(config)
    if cc.allow("llm_reranker"):
        reranker = await create_llm_reranker(...)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CostControl:
    """统一成本控制门 — 根据 mode 判定是否允许高成本 LLM 功能。"""

    mode: str = "balanced"  # balanced | low_cost | quality
    max_extra_llm_calls_per_turn: int = 0
    allow_llm_reranker_in_passive_recall: bool = False
    allow_llm_topic_strategy_d: bool = False
    max_reflection_parallel_llm_calls: int = 2
    llm_reranker_min_candidates: int = 12
    llm_reranker_prompt_chars: int = 3000
    # 运行时计数器
    _call_counts: dict[str, int] = field(default_factory=dict)

    def allow(self, feature: str) -> bool:
        """检查是否允许使用某高成本功能。

        支持的功能名:
        - "llm_reranker": LLM 重排序
        - "topic_strategy_d": 两阶段 LLM 话题分割
        - "persona_interpretation": 人设解释生成
        - "llm_query_rewrite": LLM 查询改写
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
            "persona_interpretation": False,
            "llm_query_rewrite": False,
        }
        return explicit_allows.get(feature, False)

    def check_call_limit(self, feature: str) -> bool:
        """检查是否超出每轮调用上限。"""
        if self.max_extra_llm_calls_per_turn <= 0:
            return False
        total = sum(self._call_counts.values())
        if total >= self.max_extra_llm_calls_per_turn:
            return False
        return True

    def record_call(self, feature: str) -> None:
        """记录一次高成本调用。"""
        self._call_counts[feature] = self._call_counts.get(feature, 0) + 1

    def reset_turn(self) -> None:
        """每轮开始时重置调用计数。"""
        self._call_counts.clear()

    def deny_reason(self, feature: str) -> str:
        """返回禁止原因（用于日志）。"""
        if self.mode == "quality":
            return "unreachable"
        if self.mode == "low_cost":
            return f"low_cost mode prohibits {feature}"
        if not self.allow(feature):
            return f"balanced mode prohibits {feature} (set cost_control.mode=quality to enable)"
        if not self.check_call_limit(feature):
            return f"{feature} call limit reached ({self.max_extra_llm_calls_per_turn}/turn)"
        return "unknown"


def build_cost_control_from_config(config: dict[str, Any]) -> CostControl:
    """从配置字典构建 CostControl 实例。"""
    cc_config = config.get("cost_control", {})
    if isinstance(cc_config, dict):
        mode = cc_config.get("mode", "balanced")
        return CostControl(
            mode=mode,
            max_extra_llm_calls_per_turn=cc_config.get(
                "max_extra_llm_calls_per_turn",
                0 if mode != "quality" else 1,
            ),
            allow_llm_reranker_in_passive_recall=cc_config.get(
                "allow_llm_reranker_in_passive_recall", False
            ),
            allow_llm_topic_strategy_d=cc_config.get(
                "allow_llm_topic_strategy_d", False
            ),
            max_reflection_parallel_llm_calls=cc_config.get(
                "max_reflection_parallel_llm_calls", 2
            ),
            llm_reranker_min_candidates=cc_config.get(
                "llm_reranker_min_candidates", 12
            ),
            llm_reranker_prompt_chars=cc_config.get("llm_reranker_prompt_chars", 3000),
        )
    # 向后兼容：无 cost_control 配置时默认 balanced
    return CostControl(mode="balanced")
