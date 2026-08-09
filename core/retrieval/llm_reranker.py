"""基于 LLM 的重排序器 — 通过 LLM 对记忆候选项评分并重新排序。"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from astrbot.api import logger

from ..base.cost_control import CostControl
from ..shared.extra_llm_budget import budgeted_extra_llm_call
from .rrf_fusion import HybridResult

_RERANK_PROMPT = """Score each memory for relevance to the query (0-10).
Return ONLY a JSON array: [8.5, 3.2, ...]

Query: {query}

Memories:
{memories}"""


class LLMReranker:
    """让 LLM 直接对记忆候选项评分并重新排序。"""

    def __init__(
        self,
        llm_client=None,
        batch_size: int = 10,
        cost_control: CostControl | None = None,
    ) -> None:
        """初始化 LLM 客户端和有界批量大小。"""
        self._llm_client = llm_client
        self._batch_size = max(5, min(20, batch_size))
        self._cost_control = cost_control or CostControl()

    async def rerank(
        self, results: list[HybridResult], k: int, query: str = "", **kwargs: Any
    ) -> list[HybridResult]:
        """使用 LLM 分数融合原始排序，并返回前 k 项。"""
        if (
            not self._llm_client
            or not query
            or len(results) <= k
            or len(results) < self._cost_control.llm_reranker_min_candidates
        ):
            return results[:k]
        unchanged_results = results[:k]
        candidates = sorted(results, key=lambda r: r.final_score, reverse=True)
        candidates = candidates[: min(self._batch_size * 2, len(candidates))]
        scores = await self._score_candidates(query, candidates)
        if scores is None:
            return unchanged_results
        for r, score in zip(candidates, scores, strict=False):
            r.final_score = (r.final_score + score / 10.0) / 2.0
        candidates.sort(key=lambda item: item.final_score, reverse=True)
        return candidates[:k]

    async def _score_candidates(
        self, query: str, candidates: list[HybridResult]
    ) -> list[float] | None:
        """批量评分候选；拒绝、无效响应或普通失败时返回 ``None``。"""
        memories_text = "\n".join(
            f"[{i}] {c.content[:200]}" for i, c in enumerate(candidates)
        )
        prompt = _RERANK_PROMPT.format(query=query, memories=memories_text)[
            : self._cost_control.llm_reranker_prompt_chars
        ]
        try:
            async with budgeted_extra_llm_call(
                self._cost_control,
                "llm_reranker",
            ) as allowed:
                if not allowed:
                    return None
                raw = await asyncio.to_thread(self._llm_client.complete_sync, prompt)
            raw = raw.strip()
            match = re.search(r"\[[\d.,\s]*\]", raw)
            if match:
                scores = [float(score) for score in json.loads(match.group())]
                if len(scores) == len(candidates):
                    return scores
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(
                "[LLM 重排] 评分失败，异常类型=%s",
                exc.__class__.__name__,
            )
        return None


__all__ = ["LLMReranker"]
