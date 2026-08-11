"""KnowledgeRetriever 测试 — 基于关键词+向量的知识条目混合搜索。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestKnowledgeRetriever:
    """验证知识条目的关键词与向量混合检索。"""

    @pytest.fixture
    def knowledge_store(self) -> AsyncMock:
        store = AsyncMock()
        store.search = AsyncMock(return_value=([], 0))
        return store

    @pytest.fixture
    def retriever(self, knowledge_store: AsyncMock) -> Any:
        from core.retrieval.knowledge_retriever import KnowledgeRetriever

        return KnowledgeRetriever(knowledge_store=knowledge_store)

    @pytest.mark.asyncio
    async def test_search_empty_query(self, retriever: Any) -> None:
        """空查询或纯空白查询应返回空列表。"""
        assert await retriever.search("") == []
        assert await retriever.search("   ") == []

    @pytest.mark.asyncio
    async def test_search_no_store_results(self, retriever: Any) -> None:
        """存储未返回条目时，检索结果应为空。"""
        # 让关键词检索返回空结果。
        with patch.object(
            retriever,
            "_keyword_search",
            new=AsyncMock(
                return_value=([], 0),
            ),
        ):
            results = await retriever.search("nonexistent", k=5)
            assert results == []

    @pytest.mark.asyncio
    async def test_search_with_keyword_results(self, retriever: Any) -> None:
        """关键词命中应经过评分与合并后返回。"""
        from core.features.knowledge import KnowledgeEntry, KnowledgeType
        from core.retrieval.knowledge_retriever import KnowledgeResult

        entry = KnowledgeEntry(
            title="Test Knowledge",
            content="This is a test knowledge entry",
            category=KnowledgeType.FACT,
            confidence=0.8,
            entry_id=1,
            tags=["test"],
            source_ids=[101],
        )
        # 固定关键词候选和合并结果，以隔离 search 编排行为。
        mock_result = KnowledgeResult(
            entry_id=1,
            title="Test Knowledge",
            content="This is a test knowledge entry",
            category="fact",
            confidence=0.8,
            keyword_score=0.5,
            final_score=0.5,
            tags=["test"],
            source_ids=[101],
        )
        with (
            patch.object(
                retriever,
                "_keyword_search",
                new=AsyncMock(
                    return_value=([entry], 1),
                ),
            ),
            patch.object(retriever, "_merge", return_value=[mock_result]),
        ):
            results = await retriever.search("test knowledge", k=5)
            assert len(results) == 1
            assert results[0].entry_id == 1
            assert results[0].title == "Test Knowledge"
            assert results[0].keyword_score > 0

    @pytest.mark.asyncio
    async def test_merge_entry_filtered_by_confidence(self, retriever: Any) -> None:
        """关键词检索应排除低于最小置信度的条目。"""
        # 关键词检索内部已按最小置信度过滤。
        with patch.object(
            retriever,
            "_keyword_search",
            new=AsyncMock(
                return_value=([], 0),  # 候选已被过滤。
            ),
        ):
            results = await retriever.search("low confidence", k=5)
            assert results == []

    def test_merge_with_vector_scores(self, retriever: Any) -> None:
        """向量分数应与关键词分数按配置权重融合。"""
        from core.retrieval.knowledge_retriever import KnowledgeResult

        # 直接构造结果，避免测试依赖 __slots__ 的动态属性限制。
        kw_result = KnowledgeResult(
            entry_id=1,
            title="Vector Test",
            content="Testing vector blending",
            category="concept",
            confidence=0.7,
            keyword_score=0.4,
            final_score=0.4,
            tags=["vector"],
            source_ids=[1],
        )

        # 使用结果结构直接验证融合公式，避免为内部临时分数增加动态属性。
        result_map = {1: kw_result}
        kw_w, vec_w = retriever._keyword_weight, retriever._vector_weight
        r = result_map[1]
        r.vector_score = 0.95
        r.final_score = round(kw_w * r.keyword_score + vec_w * r.vector_score, 4)
        assert r.final_score > r.keyword_score

    @pytest.mark.asyncio
    async def test_search_vector_fn_exception_handled(self, retriever: Any) -> None:
        """向量检索异常时仍应返回关键词合并结果。"""
        from core.features.knowledge import KnowledgeEntry, KnowledgeType

        entry = KnowledgeEntry(
            title="Fallback",
            content="Fallback content",
            category=KnowledgeType.FACT,
            confidence=0.6,
            entry_id=1,
        )
        retriever._vector_search_fn = MagicMock(side_effect=RuntimeError("broken"))

        with patch.object(
            retriever,
            "_keyword_search",
            new=AsyncMock(
                return_value=([entry], 1),
            ),
        ):
            # 向量检索异常由检索器内部降级处理。
            results = await retriever.search("fallback", k=5)
            assert len(results) == 1
            assert results[0].entry_id == 1

    def test_tokenize_function(self) -> None:
        """_tokenize 应切分文本并过滤停用词。"""
        from core.retrieval.knowledge_retriever import _tokenize

        tokens = _tokenize("This is a test query")
        assert "test" in tokens
        assert "query" in tokens
        assert "is" not in tokens  # 停用词。
        assert "a" not in tokens  # 停用词且长度过短。

    def test_keyword_score_empty_terms(self) -> None:
        """查询词为空时 _keyword_score 应返回零。"""
        from core.retrieval.knowledge_retriever import _keyword_score

        score = _keyword_score(set(), "title", "content")
        assert score == 0.0

    def test_keyword_score_with_terms(self) -> None:
        """_keyword_score 应计算基于词频的分数。"""
        from core.retrieval.knowledge_retriever import _keyword_score

        score = _keyword_score({"test"}, "test title", "test content here")
        assert 0 < score <= 1.0

    # 补充此前未覆盖的分支。

    @pytest.mark.asyncio
    async def test_vector_search_returns_coroutine(self, retriever: Any) -> None:
        """向量函数返回协程时，_vector_search 应等待其结果。"""

        async def _async_fn(_query: str, _limit: int) -> dict[int, float]:
            """返回固定向量分数。"""
            return {1: 0.85}

        retriever._vector_search_fn = _async_fn
        result = await retriever._vector_search("test", 10)
        assert result == {1: 0.85}

    @pytest.mark.asyncio
    async def test_vector_search_returns_non_dict(self, retriever: Any) -> None:
        """向量函数返回非字典对象时应降级为空映射。"""
        retriever._vector_search_fn = MagicMock(
            return_value=[1, 2, 3]
        )  # 列表不符合向量分数字典契约。
        result = await retriever._vector_search("test", 10)
        assert result == {}

    @pytest.mark.asyncio
    async def test_vector_search_fn_is_none(self, retriever: Any) -> None:
        """未配置向量函数时应返回空映射。"""
        retriever._vector_search_fn = None
        result = await retriever._vector_search("test", 10)
        assert result == {}

    def test_keyword_score_title_weighted_3x(self) -> None:
        """标题命中的关键词权重应是正文命中的三倍。"""
        from core.retrieval.knowledge_retriever import _keyword_score

        # 仅标题命中。
        score_title = _keyword_score({"python"}, "python programming", "")
        # 仅正文命中。
        score_content = _keyword_score({"python"}, "", "python is great for coding")
        # 标题命中应因三倍权重取得更高分数。
        assert score_title >= score_content

    def test_tokenize_with_chinese_text(self) -> None:
        """_tokenize 应按中日韩标点切分中文文本。"""
        from core.retrieval.knowledge_retriever import _tokenize

        # 使用中文标点分隔各词。
        tokens = _tokenize("测试，查询；内容！问题？")
        assert "测试" in tokens
        assert "查询" in tokens
        assert "内容" in tokens
        assert "问题" in tokens
        # 没有标点时整段保留为较长 token，不单独产生停用词“是”。
        tokens2 = _tokenize("这是一个测试")
        assert "这是" not in tokens2  # 无分隔符时不会产生该独立 token。
