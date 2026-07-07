"""基于 LLM 的重排序器 — 通过 LLM 对记忆候选项评分并重新排序。"""

from __future__ import annotations

import json
import re
from typing import Any

from astrbot.api import logger

from .rrf_fusion import HybridResult

_RERANK_PROMPT = """Score each memory for relevance to the query (0-10).
Return ONLY a JSON array: [8.5, 3.2, ...]

Query: {query}

Memories:
{memories}"""


class LLMReranker:
    """让 LLM 直接对记忆候选项评分并重新排序。"""

    def __init__(self, llm_client=None, batch_size: int = 10) -> None:
        self._llm_client = llm_client
        self._batch_size = max(5, min(20, batch_size))

    async def rerank(
        self, results: list[HybridResult], k: int, query: str = "", **kwargs: Any
    ) -> list[HybridResult]:
        if not self._llm_client or not query or len(results) <= k:
            return results[:k]
        candidates = sorted(results, key=lambda r: r.final_score, reverse=True)
        candidates = candidates[: min(self._batch_size * 2, len(candidates))]
        scores = await self._score_candidates(query, candidates)
        for r, score in zip(candidates, scores, strict=False):
            r.final_score = (r.final_score + score / 10.0) / 2.0
        candidates.sort(key=lambda item: item.final_score, reverse=True)
        return candidates[:k]

    async def _score_candidates(
        self, query: str, candidates: list[HybridResult]
    ) -> list[float]:
        memories_text = "\n".join(
            f"[{i}] {c.content[:200]}" for i, c in enumerate(candidates)
        )
        prompt = _RERANK_PROMPT.format(query=query, memories=memories_text)
        try:
            import asyncio

            raw = await asyncio.to_thread(self._llm_client.complete_sync, prompt)
            raw = raw.strip()
            match = re.search(r"\[[\d.,\s]*\]", raw)
            if match:
                return [float(s) for s in json.loads(match.group())]
        except Exception as exc:
            logger.debug(f"[LLMReranker] scoring failed: {exc}")
        return [max(0.0, 10.0 - i * 0.5) for i in range(len(candidates))]


__all__ = ["LLMReranker"]
