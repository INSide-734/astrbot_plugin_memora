"""R1: 语义查询改写 — LLM few-shot 查询展开 + 实体提取 + 意图分类。

替换硬编码 intent_keywords.py，使用 LLM 将模糊的自然语言查询
（如"上次那个事"、"小明喜欢的那个"）展开为精准的多查询检索词。

当 LLM 不可用时，自动回退到 intent_keywords 的硬编码规则。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

from .intent_keywords import FACTUAL_TERMS, RELATION_TERMS, TEMPORAL_TERMS


@dataclass
class QueryIntent:
    """LLM 查询意图解析结果"""

    intent: str = "default"  # factual | relational | temporal | preference | contextual
    extracted_entities: list[str] = field(default_factory=list)
    time_reference: str | None = None
    rewritten_queries: list[str] = field(default_factory=list)
    memory_types: list[str] = field(default_factory=list)

    @classmethod
    def from_keywords(cls, query: str) -> QueryIntent:
        """Fallback: 使用硬编码关键词做意图分类（LLM 不可用时调用）。"""
        normalized = query.casefold()
        intent = "default"

        rel = any(t in normalized for t in RELATION_TERMS)
        tmp = any(t in normalized for t in TEMPORAL_TERMS)
        fac = any(t in normalized for t in FACTUAL_TERMS)

        if rel:
            intent = "relationship"
        if tmp:
            intent = "temporal"
        if fac and not rel:
            intent = "factual"

        memory_types = []
        if intent == "relationship":
            memory_types = ["RELATIONAL", "EPISODIC"]
        elif intent == "temporal":
            memory_types = ["EPISODIC", "PLANNED"]
        elif intent == "factual":
            memory_types = ["FACTUAL"]

        return cls(
            intent=intent,
            extracted_entities=[],
            time_reference=None,
            rewritten_queries=[query],
            memory_types=memory_types,
        )


class QueryRewriter:
    """使用 LLM few-shot 将模糊查询展开为多角度检索词。

    输入: "上次聊的那个事"
    输出: QueryIntent(
        intent="temporal",
        extracted_entities=["那个事"],
        time_reference="recent",
        rewritten_queries=["最近对话", "之前提到的话题", "最近一周的事"],
        memory_types=["EPISODIC"]
    )
    """

    REWRITE_PROMPT = """分析用户的记忆查询意图，将模糊指代展开为具体检索词。

输入: {query}
上下文: {recent_context}

返回纯 JSON（不要 markdown 代码块）:
{{
    "intent": "factual|relational|temporal|preference|contextual",
    "extracted_entities": ["人物/事物/地点实体列表"],
    "time_reference": "recent|today|yesterday|this_week|null",
    "rewritten_queries": ["展开后的检索词1", "检索词2", "检索词3"],
    "memory_types": ["EPISODIC", "FACTUAL", "RELATIONAL", "PREFERENCE", "PLANNED"]
}}

规则:
- "上次那个事" → intent=temporal, rewritten_queries 展开为多条时间+主题查询
- "小明喜欢的那个" → 提取 "小明" 为实体, intent=preference
- "我告诉过你什么来着" → intent=contextual, rewritten_queries=["重要对话", "最近的提醒"]
- 保留原始查询词在 rewritten_queries 中
- 仅返回 JSON，不要任何解释"""

    def __init__(
        self,
        llm_caller: Any | None = None,
        enabled: bool = True,
    ) -> None:
        self._llm = llm_caller  # 可选：注入 LLM 调用函数
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    async def rewrite(
        self,
        query: str,
        recent_context: str = "",
    ) -> QueryIntent:
        """展开查询意图。

        Args:
            query: 用户原始查询
            recent_context: 最近对话上下文（可选，增强指代消解）

        Returns:
            QueryIntent 包含展开后的多角度检索词
        """
        if not self._enabled:
            return QueryIntent.from_keywords(query)

        # 短查询或无需改写的直接返回
        if len(query.strip()) < 2:
            return QueryIntent.from_keywords(query)

        # 尝试 LLM 改写
        if self._llm is not None:
            try:
                prompt = self.REWRITE_PROMPT.format(
                    query=query,
                    recent_context=recent_context or "无",
                )
                raw = await self._llm(prompt)
                intent = self._parse_llm_response(raw, query)
                if intent is not None:
                    return intent
            except Exception:
                logger.debug(
                    "[QueryRewriter] LLM 查询改写失败，回退到关键词匹配",
                    exc_info=True,
                )

        # 回退
        return QueryIntent.from_keywords(query)

    @staticmethod
    def _parse_llm_response(raw: str, fallback_query: str) -> QueryIntent | None:
        """从 LLM 原始响应中提取 QueryIntent，失败返回 None。"""
        try:
            # 去除可能的 markdown 代码块包裹
            text = raw.strip()
            if text.startswith("```"):
                # 去除 ```json 和尾部 ```
                lines = text.split("\n")
                text = "\n".join(
                    line for line in lines if not line.strip().startswith("```")
                ).strip()

            data = json.loads(text)

            return QueryIntent(
                intent=str(data.get("intent", "default")),
                extracted_entities=list(data.get("extracted_entities", [])),
                time_reference=data.get("time_reference"),
                rewritten_queries=list(data.get("rewritten_queries", [fallback_query])),
                memory_types=list(data.get("memory_types", [])),
            )
        except (json.JSONDecodeError, TypeError, KeyError):
            return None


__all__ = ["QueryIntent", "QueryRewriter"]
