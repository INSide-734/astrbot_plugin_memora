"""知识库检索器。"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from ..models.knowledge_models import KnowledgeEntry
from ..storage.knowledge_store import KnowledgeStore

# 全文搜索停用词（中/英）
_KNOWLEDGE_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "of",
        "in",
        "on",
        "to",
        "for",
        "with",
        "and",
        "or",
        "not",
        "it",
        "its",
        "this",
        "that",
        "的",
        "是",
        "在",
        "了",
        "和",
        "与",
        "或",
        "不",
        "也",
        "就",
        "都",
        "要",
        "会",
        "可以",
        "这个",
        "那个",
        "一个",
        "什么",
    }
)


@dataclass(slots=True)
class KnowledgeResult:
    """排序后的知识检索结果。"""

    entry_id: int
    title: str
    content: str
    category: str
    confidence: float
    keyword_score: float = 0.0
    vector_score: float = 0.0
    final_score: float = 0.0
    tags: list[str] = field(default_factory=list)
    source_ids: list[int] = field(default_factory=list)


class KnowledgeRetriever:
    """结构化知识条目的混合检索器。

    结合 KnowledgeStore 的 LIKE 关键词检索与可选的向量语义检索。
    当外部向量检索不可用时，会自动回退到纯关键词路径。
    """

    def __init__(
        self,
        knowledge_store: KnowledgeStore,
        config: dict[str, Any] | None = None,
        vector_search_fn: Any = None,
    ):
        self._store = knowledge_store
        self._config = config or {}
        self._keyword_weight = float(
            self._config.get("knowledge_base.keyword_weight", 0.55)
        )
        self._vector_weight = float(
            self._config.get("knowledge_base.vector_weight", 0.45)
        )
        self._min_confidence = float(
            self._config.get("knowledge_base.min_confidence", 0.3)
        )
        self._vector_search_fn = vector_search_fn

    async def search(
        self,
        query: str,
        k: int = 10,
        category: str = "",
    ) -> list[KnowledgeResult]:
        """按混合打分检索知识条目。"""
        if not query or not query.strip():
            return []

        keyword_future = asyncio.ensure_future(
            self._keyword_search(query, max(k * 2, 20), category)
        )

        vector_future = None
        if self._vector_search_fn is not None:
            vector_future = asyncio.ensure_future(
                self._vector_search(query, max(k * 2, 20))
            )

        keyword_entries, _ = await keyword_future
        vector_map: dict[int, float] = {}
        if vector_future is not None:
            vector_map = await vector_future

        return self._merge(keyword_entries, vector_map, k)

    async def _keyword_search(
        self,
        query: str,
        limit: int,
        category: str,
    ) -> tuple[list[KnowledgeEntry], int]:
        """通过 KnowledgeStore 执行关键词检索，并附加轻量 TF 打分。"""
        entries, total = await self._store.search(query, limit, category)
        scored: list[KnowledgeEntry] = []
        query_terms = _tokenize(query)

        for entry in entries:
            if entry.confidence < self._min_confidence:
                continue
            kw_score = _keyword_score(query_terms, entry.title, entry.content)
            object.__setattr__(entry, "_kw_score", kw_score)
            scored.append(entry)

        scored.sort(key=lambda e: getattr(e, "_kw_score", 0.0), reverse=True)
        return scored[:limit], total

    async def _vector_search(
        self,
        query: str,
        limit: int,
    ) -> dict[int, float]:
        """委托外部函数执行向量检索。

        `vector_search_fn(query, limit)` 应返回 `dict[int, float]`，
        用于将知识条目 ID 映射到余弦相似度分数。
        """
        if self._vector_search_fn is None:
            return {}
        try:
            result = self._vector_search_fn(query, limit)
            if asyncio.iscoroutine(result):
                result = await result
            return result if isinstance(result, dict) else {}
        except Exception:
            return {}

    def _merge(
        self,
        keyword_entries: list[KnowledgeEntry],
        vector_scores: dict[int, float],
        k: int,
    ) -> list[KnowledgeResult]:
        """按权重融合关键词结果与向量结果。"""
        results: dict[int, KnowledgeResult] = {}

        for entry in keyword_entries:
            kw_score = getattr(entry, "_kw_score", 0.0)
            results[entry.entry_id] = KnowledgeResult(
                entry_id=entry.entry_id,
                title=entry.title,
                content=entry.content,
                category=entry.category.value,
                confidence=entry.confidence,
                keyword_score=round(kw_score, 4),
                tags=list(entry.tags),
                source_ids=list(entry.source_ids),
            )

        # 将向量分数补充到已有的关键词结果中
        for eid, vec_score in vector_scores.items():
            if eid in results:
                results[eid].vector_score = round(vec_score, 4)

        # 计算最终分数
        kw_w, vec_w = self._keyword_weight, self._vector_weight
        for r in results.values():
            has_vec = r.vector_score > 0
            if has_vec:
                r.final_score = round(
                    kw_w * r.keyword_score + vec_w * r.vector_score, 4
                )
            else:
                r.final_score = r.keyword_score

        ranked = sorted(results.values(), key=lambda r: r.final_score, reverse=True)
        return ranked[:k]


def _tokenize(text: str) -> set[str]:
    """委托共享的关键词分词器处理文本。

    参见 :func:`core.utils.text_utils.tokenize_keywords`。
    """
    from ..utils.text_utils import tokenize_keywords

    return tokenize_keywords(text)


def _keyword_score(query_terms: set[str], title: str, content: str) -> float:
    """轻量 TF 打分，标题命中按 3 倍权重计分以提高精度。"""
    if not query_terms:
        return 0.0
    title_lower = title.lower()
    content_lower = content.lower()
    score = 0.0
    for term in query_terms:
        title_count = title_lower.count(term)
        content_count = content_lower.count(term)
        score += min(title_count * 3.0, 3.0) + min(float(content_count), 5.0)
    return min(1.0, score / max(1, len(query_terms) * 2))


__all__ = ["KnowledgeRetriever", "KnowledgeResult"]
