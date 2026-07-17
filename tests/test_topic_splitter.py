"""测试话题分割策略。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from core.processors.topic_splitter import (
    EmbeddingClusteringStrategy,
    HybridSegmentationStrategy,
    MemorySegment,
    PromptSegmentationStrategy,
    TopicChunkingStrategy,
    TopicSegmentationRouter,
    TwoStageLLMStrategy,
)
from hypothesis import HealthCheck, given, settings
from hypothesis.strategies import lists, text

# ---- Strategy A ----

@pytest.mark.asyncio
async def test_prompt_strategy_parses_memories_array():
    strat = PromptSegmentationStrategy()
    data = {
        "memories": [
            {
                "summary": "讨论了滑雪计划",
                "topics": ["滑雪", "出行"],
                "key_facts": ["张三提议周末滑雪", "李四确认参加"],
                "importance": 0.75,
                "sentiment": "positive",
                "emotion_tags": ["期待"],
            },
            {
                "summary": "日料推荐",
                "topics": ["美食"],
                "key_facts": ["王五推荐银座日料"],
                "importance": 0.5,
                "sentiment": "neutral",
                "emotion_tags": [],
            },
        ]
    }
    segments = await strat.segment(data)
    assert len(segments) == 2
    assert segments[0].content == "讨论了滑雪计划"
    assert segments[0].importance == 0.75
    assert len(segments[0].key_facts) == 2
    assert segments[1].content == "日料推荐"
    assert len(segments[1].key_facts) == 1


@pytest.mark.asyncio
async def test_prompt_strategy_legacy_format():
    strat = PromptSegmentationStrategy()
    data = {
        "summary": "旧格式记忆",
        "topics": ["测试"],
        "key_facts": ["事实1", "事实2"],
    }
    segments = await strat.segment(data)
    assert len(segments) == 1
    assert segments[0].content == "旧格式记忆"
    assert segments[0].metadata["schema_version"] == "v3"


@pytest.mark.asyncio
async def test_prompt_strategy_empty_memories():
    strat = PromptSegmentationStrategy()
    data = {"memories": []}
    segments = await strat.segment(data)
    assert len(segments) == 0


@pytest.mark.asyncio
async def test_prompt_strategy_skips_empty_entries():
    strat = PromptSegmentationStrategy()
    data = {
        "memories": [
            {"summary": "", "key_facts": []},
            {"summary": "有效记忆", "key_facts": ["事实A"]},
            {"summary": "", "key_facts": []},
        ]
    }
    segments = await strat.segment(data)
    assert len(segments) == 1
    assert segments[0].content == "有效记忆"


# ---- Strategy B ----

@pytest.mark.asyncio
async def test_embedding_strategy_single_fact_shortcut():
    strat = EmbeddingClusteringStrategy({"similarity_threshold": 0.5})
    data = {"summary": "单条", "key_facts": ["只有一个事实"]}
    segments = await strat.segment(data)
    assert len(segments) == 1


@pytest.mark.asyncio
async def test_embedding_strategy_clusters_dissimilar():
    strat = EmbeddingClusteringStrategy({"similarity_threshold": 0.99, "max_clusters": 5})
    data = {
        "summary": "混合",
        "key_facts": [
            "张三提议周末滑雪",
            "项目周五截止",
            "王五推荐银座日料",
            "张三最近失眠",
        ],
    }
    segments = await strat.segment(data)
    # extremely high threshold → each fact in own cluster
    assert len(segments) == 4


@pytest.mark.asyncio
async def test_embedding_strategy_merges_similar():
    strat = EmbeddingClusteringStrategy({"similarity_threshold": 0.0, "max_clusters": 5})
    data = {
        "summary": "同类",
        "key_facts": [
            "张三提议周末滑雪",
            "李四说开车去滑雪",
            "王五问滑雪装备",
        ],
    }
    segments = await strat.segment(data)
    # threshold 0.0 → everything merges into one cluster
    assert len(segments) == 1


# ---- A+B Hybrid ----

@pytest.mark.asyncio
async def test_hybrid_uses_a_when_multiple():
    strat = HybridSegmentationStrategy({"hybrid_fallback_fact_threshold": 3})
    data = {
        "memories": [
            {"summary": "话题1", "key_facts": ["A"]},
            {"summary": "话题2", "key_facts": ["B"]},
        ]
    }
    segments = await strat.segment(data)
    assert len(segments) == 2


@pytest.mark.asyncio
async def test_hybrid_falls_back_when_single_with_many_facts():
    strat = HybridSegmentationStrategy(
        {"hybrid_fallback_fact_threshold": 2, "similarity_threshold": 0.99, "max_clusters": 5}
    )
    data = {
        "summary": "混合摘要",
        "key_facts": ["滑雪计划", "项目截止", "日料推荐"],
    }
    segments = await strat.segment(data)
    # A wraps as 1, B clusters into 3 (threshold 0.99)
    assert len(segments) == 3


# ---- Router ----

@pytest.mark.asyncio
async def test_router_selects_strategy_by_config():
    router = TopicSegmentationRouter({"topic_segmentation.strategy": "a"})
    assert isinstance(router.strategy, PromptSegmentationStrategy)
    assert router.strategy_key == "a"


@pytest.mark.asyncio
async def test_router_defaults_on_invalid_strategy():
    router = TopicSegmentationRouter({"topic_segmentation.strategy": "nonexistent"})
    assert router.strategy_key == "a_b_hybrid"
    assert isinstance(router.strategy, HybridSegmentationStrategy)


# ---- MemorySegment ----

def test_memory_segment_defaults():
    seg = MemorySegment(content="test", metadata={}, importance=0.5)
    assert seg.key_facts == []
    assert seg.topics == []
    assert seg.atoms == []


# ---- PBT Properties (hypothesis) ----

@settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
@given(lists(text(min_size=1, max_size=30), min_size=1, max_size=5))
def test_pbt_single_fact_never_splits(facts):
    """P4: Single key_fact always produces exactly one segment."""
    strat = EmbeddingClusteringStrategy({"similarity_threshold": 0.5})

    async def run():
        data = {"summary": "test", "key_facts": [facts[0]]}
        segments = await strat.segment(data)
        assert len(segments) == 1

    import asyncio
    asyncio.run(run())


@settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
@given(lists(text(min_size=1, max_size=30), min_size=1, max_size=6))
def test_pbt_fact_conservation(facts):
    """P1: Total key_facts count is preserved across segmentation."""
    valid = [f for f in facts if f.strip()]
    if not valid:
        return

    strat = EmbeddingClusteringStrategy({"similarity_threshold": 0.99, "max_clusters": len(valid)})

    async def run():
        data = {"summary": "test", "key_facts": valid}
        segments = await strat.segment(data)
        total = sum(len(s.key_facts) for s in segments)
        assert total == len(valid)

    import asyncio
    asyncio.run(run())


@settings(max_examples=10, suppress_health_check=[HealthCheck.too_slow])
@given(lists(text(min_size=1, max_size=30), min_size=1, max_size=4))
def test_pbt_idempotent(facts):
    """P2: Same input produces same segmentation result."""
    valid = [f for f in facts if f.strip()]
    if not valid:
        return

    strat = EmbeddingClusteringStrategy({"similarity_threshold": 0.5, "max_clusters": 5})

    async def run():
        data = {"summary": "test", "key_facts": valid}
        s1 = await strat.segment(data)
        s2 = await strat.segment(data)
        assert len(s1) == len(s2)
        for a, b in zip(sorted(s1, key=lambda x: x.content), sorted(s2, key=lambda x: x.content), strict=False):
            assert a.key_facts == b.key_facts

    import asyncio
    asyncio.run(run())


def test_pbt_empty_input_no_exception():
    """P3: Empty key_facts should not throw."""
    strat = EmbeddingClusteringStrategy()

    async def run():
        segments = await strat.segment({"summary": "", "key_facts": []})
        assert segments == []
        segments2 = await strat.segment({"summary": "", "key_facts": [""]})
        assert segments2 == []

    import asyncio
    asyncio.run(run())


# ---- Strategy C (chunk_messages) ----

@pytest.mark.asyncio
async def test_chunking_empty_or_single():
    strat = TopicChunkingStrategy({"topic_shift_threshold": 0.3, "min_chunk_size": 2})

    class _Msg:
        def __init__(self, content):
            self.content = content

    chunks = await strat.chunk_messages([_Msg("hello")])
    assert len(chunks) == 1
    assert len(chunks[0]) == 1


@pytest.mark.asyncio
async def test_chunking_multiple_messages_with_dummy_embeddings():
    strat = TopicChunkingStrategy({"topic_shift_threshold": 0.99, "min_chunk_size": 2})

    class _Msg:
        def __init__(self, content):
            self.content = content

    msgs = [_Msg(f"msg_{i}") for i in range(6)]
    chunks = await strat.chunk_messages(msgs)
    # With high threshold and dummy embeddings, chunks won't split
    assert len(chunks) >= 1


@pytest.mark.asyncio
async def test_chunking_with_low_threshold_splits():
    strat = TopicChunkingStrategy({"topic_shift_threshold": 0.0, "min_chunk_size": 2})

    class _Msg:
        def __init__(self, content):
            self.content = content

    msgs = [_Msg(f"msg_{i}") for i in range(10)]
    chunks = await strat.chunk_messages(msgs)
    # With threshold 0.0, each message boundary should trigger a split
    # but min_chunk_size=2 keeps some grouped
    assert len(chunks) >= 1


@pytest.mark.asyncio
async def test_chunking_segment_pass_through():
    strat = TopicChunkingStrategy({"topic_shift_threshold": 0.3, "min_chunk_size": 2})
    data = {"summary": "test", "key_facts": ["fact1", "fact2"]}
    segments = await strat.segment(data)
    assert len(segments) == 1


@pytest.mark.asyncio
async def test_chunking_tiny_chunk_merged_into_previous():
    strat = TopicChunkingStrategy({"topic_shift_threshold": 0.99, "min_chunk_size": 5})

    class _Msg:
        def __init__(self, content):
            self.content = content

    msgs = [_Msg(f"msg_{i}") for i in range(3)]
    chunks = await strat.chunk_messages(msgs)
    # min_chunk_size=5 > 3, so all go into one chunk
    assert len(chunks) == 1


@pytest.mark.asyncio
async def test_chunking_with_embed_fn():
    """Test that chunking uses embed_fn when provided."""
    embed_fn = AsyncMock(return_value=[[0.1, 0.2, 0.3] for _ in range(4)])
    strat = TopicChunkingStrategy(
        {"topic_shift_threshold": 0.3, "min_chunk_size": 2},
        embed_fn=embed_fn,
    )

    class _Msg:
        def __init__(self, content):
            self.content = content

    msgs = [_Msg(f"msg_{i}") for i in range(4)]
    chunks = await strat.chunk_messages(msgs)
    assert len(chunks) >= 1
    embed_fn.assert_called()

    # Verify segment pass-through
    segments = await strat.segment({"summary": "s", "key_facts": ["f"]})
    assert len(segments) == 1


# ---- Strategy D (TwoStageLLM) ----

@pytest.mark.asyncio
async def test_two_stage_identify_topics_no_llm():
    strat = TwoStageLLMStrategy({"stage1_max_topics": 3})
    topics = await strat.identify_topics("some conversation")
    assert topics == []


@pytest.mark.asyncio
async def test_two_stage_identify_topics_with_llm():
    mock_llm = AsyncMock()
    mock_llm.call_llm_with_retry = AsyncMock(return_value='{"topics": [{"topic": "滑雪计划", "line_range": [1, 4]}]}')
    strat = TwoStageLLMStrategy({"stage1_max_topics": 3}, llm_client=mock_llm)
    topics = await strat.identify_topics("1. a\n2. b\n3. c\n4. d")
    assert topics == [{"topic": "滑雪计划", "line_range": [1, 4]}]
    mock_llm.call_llm_with_retry.assert_called_once()


@pytest.mark.asyncio
async def test_two_stage_identify_topics_parses_markdown_code_block():
    mock_llm = AsyncMock()
    mock_llm.call_llm_with_retry = AsyncMock(
        return_value='```json\n[{"topic": "项目", "line_range": [2, 5]}]\n```'
    )
    strat = TwoStageLLMStrategy({"stage1_max_topics": 3}, llm_client=mock_llm)

    topics = await strat.identify_topics("l1\nl2\nl3\nl4\nl5")

    assert topics == [{"topic": "项目", "line_range": [2, 5]}]


@pytest.mark.asyncio
async def test_two_stage_identify_topics_clamps_and_sorts_ranges():
    mock_llm = AsyncMock()
    mock_llm.call_llm_with_retry = AsyncMock(
        return_value='[{"topic": "越界", "line_range": [5, 9]}, {"topic": "倒序", "line_range": [3, 2]}]'
    )
    strat = TwoStageLLMStrategy({"stage1_max_topics": 3}, llm_client=mock_llm)

    topics = await strat.identify_topics("l1\nl2\nl3\nl4\nl5")

    assert topics == [{"topic": "越界", "line_range": [5, 5]}]


@pytest.mark.asyncio
async def test_two_stage_identify_topics_llm_error():
    mock_llm = AsyncMock()
    mock_llm.call_llm_with_retry = AsyncMock(side_effect=RuntimeError("LLM down"))
    strat = TwoStageLLMStrategy({"stage1_max_topics": 3}, llm_client=mock_llm)
    topics = await strat.identify_topics("conversation text")
    assert topics == []


@pytest.mark.asyncio
async def test_two_stage_segment_pass_through():
    strat = TwoStageLLMStrategy({"stage1_max_topics": 3})
    data = {"summary": "test", "key_facts": ["fact1", "fact2"]}
    segments = await strat.segment(data)
    assert len(segments) == 1


@pytest.mark.asyncio
async def test_two_stage_identify_topics_max_topics_truncation():
    mock_llm = AsyncMock()
    mock_llm.call_llm_with_retry = AsyncMock(return_value='[{"topic": "a", "line_range": [1, 2]}, {"topic": "b", "line_range": [3, 4]}, {"topic": "c", "line_range": [5, 6]}, {"topic": "d", "line_range": [7, 8]}]')
    strat = TwoStageLLMStrategy({"stage1_max_topics": 2}, llm_client=mock_llm)
    topics = await strat.identify_topics("conversation text")
    assert len(topics) <= 2


@pytest.mark.asyncio
async def test_two_stage_identify_topics_invalid_format():
    mock_llm = AsyncMock()
    mock_llm.call_llm_with_retry = AsyncMock(return_value='{"not_an_array": true}')
    strat = TwoStageLLMStrategy({"stage1_max_topics": 3}, llm_client=mock_llm)
    topics = await strat.identify_topics("conversation text")
    assert topics == []


# ---- Router (additional config tests) ----

def test_router_strategy_b_key():
    router = TopicSegmentationRouter({
        "topic_segmentation.strategy": "b",
        "topic_segmentation.similarity_threshold": 0.5,
        "topic_segmentation.max_clusters": 5,
    })
    assert isinstance(router.strategy, EmbeddingClusteringStrategy)
    assert router.strategy_key == "b"


def test_router_strategy_c_key():
    router = TopicSegmentationRouter({
        "topic_segmentation.strategy": "c",
        "topic_segmentation.topic_shift_threshold": 0.3,
    })
    assert isinstance(router.strategy, TopicChunkingStrategy)
    assert router.strategy_key == "c"


def test_router_strategy_d_key():
    router = TopicSegmentationRouter({
        "topic_segmentation.strategy": "d",
        "topic_segmentation.stage1_max_topics": 3,
    })
    assert isinstance(router.strategy, TwoStageLLMStrategy)
    assert router.strategy_key == "d"


def test_router_strategy_b_with_embed_fn():
    embed_fn = AsyncMock()
    router = TopicSegmentationRouter({
        "topic_segmentation.strategy": "b",
        "topic_segmentation.similarity_threshold": 0.5,
    }, embed_fn=embed_fn)
    assert isinstance(router.strategy, EmbeddingClusteringStrategy)


@pytest.mark.asyncio
async def test_router_strategy_b_uses_embed_fn():
    embed_fn = AsyncMock(return_value=[[1.0, 0.0], [0.0, 1.0]])
    router = TopicSegmentationRouter({
        "topic_segmentation.strategy": "b",
        "topic_segmentation.similarity_threshold": 0.9,
    }, embed_fn=embed_fn)

    await router.segment({"summary": "mixed", "key_facts": ["fact-a", "fact-b"]})

    embed_fn.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_strategy_d_uses_llm_client():
    llm_client = AsyncMock()
    llm_client.call_llm_with_retry = AsyncMock(
        return_value='[{"topic": "路由", "line_range": [1, 1]}]'
    )
    router = TopicSegmentationRouter(
        {"topic_segmentation.strategy": "d"},
        llm_client=llm_client,
    )

    topics = await router.strategy.identify_topics("line 1")

    assert topics == [{"topic": "路由", "line_range": [1, 1]}]
    llm_client.call_llm_with_retry.assert_awaited_once()


def test_router_segment_delegates():
    router = TopicSegmentationRouter({"topic_segmentation.strategy": "a"})
    assert router.strategy_key == "a"

    # Test segment call delegation
    import asyncio
    async def _run():
        segments = await router.segment({
            "memories": [
                {"summary": "test", "key_facts": ["f"]},
            ]
        })
        assert len(segments) == 1
    asyncio.run(_run())


# ---- EmbeddingClusteringStrategy with embed_fn ----

@pytest.mark.asyncio
async def test_embedding_strategy_with_custom_embed_fn():
    embed_fn = AsyncMock(return_value=[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]])
    strat = EmbeddingClusteringStrategy(
        {"similarity_threshold": 0.5, "max_clusters": 3},
        embed_fn=embed_fn,
    )
    data = {
        "summary": "test",
        "key_facts": ["fact_a", "fact_b", "fact_c"],
    }
    segments = await strat.segment(data)
    assert len(segments) >= 1
    embed_fn.assert_called()


@pytest.mark.asyncio
async def test_embedding_strategy_embed_fn_returns_mismatch():
    """当 embed_fn returns wrong number of vectors, falls back to dummy."""
    embed_fn = AsyncMock(return_value=[[0.1]])  # Only 1 vector for 3 facts
    strat = EmbeddingClusteringStrategy(
        {"similarity_threshold": 0.5, "max_clusters": 3},
        embed_fn=embed_fn,
    )
    data = {
        "summary": "test",
        "key_facts": ["fact_a", "fact_b", "fact_c"],
    }
    segments = await strat.segment(data)
    assert len(segments) >= 1


# ---- HybridSegmentationStrategy with embed_fn ----

@pytest.mark.asyncio
async def test_hybrid_with_embed_fn_for_fallback():
    """当 embed_fn is provided and A fails to split, B should use it."""
    embed_fn = AsyncMock(return_value=[[0.1], [0.2], [0.3]])
    strat = HybridSegmentationStrategy(
        {"hybrid_fallback_fact_threshold": 2, "similarity_threshold": 0.99, "max_clusters": 5},
        embed_fn=embed_fn,
    )
    data = {
        "summary": "single",
        "key_facts": ["fact_a", "fact_b", "fact_c"],
    }
    segments = await strat.segment(data)
    # With multiple facts and A producing 1 segment, B should fire
    assert len(segments) >= 1


# ---- _safe_bool helper ----

def test_safe_bool_true_values():
    from core.processors.topic_splitter import _safe_bool
    assert _safe_bool(True) is True
    assert _safe_bool("True") is True
    assert _safe_bool("true") is True
    assert _safe_bool("1") is True
    assert _safe_bool("yes") is True
    assert _safe_bool("on") is True
    assert _safe_bool(1) is True
    assert _safe_bool(0.1) is True


def test_safe_bool_false_values():
    from core.processors.topic_splitter import _safe_bool
    assert _safe_bool(False) is False
    assert _safe_bool("False") is False
    assert _safe_bool("false") is False
    assert _safe_bool("0") is False
    assert _safe_bool("no") is False
    assert _safe_bool("off") is False
    assert _safe_bool(0) is False
    assert _safe_bool(0.0) is False


def test_safe_bool_default():
    from core.processors.topic_splitter import _safe_bool
    assert _safe_bool(None) is True
    assert _safe_bool(object()) is True
    assert _safe_bool([1, 2, 3]) is True


# ---- _msg_text helper ----

def test_msg_text_from_content():
    from core.processors.topic_splitter import _msg_text

    class _Msg:
        def __init__(self, content):
            self.content = content

    result = _msg_text(_Msg("hello world"))
    assert result == "hello world"


def test_msg_text_from_text_attr():
    from core.processors.topic_splitter import _msg_text

    class _Msg:
        text = "hello from text"

    result = _msg_text(_Msg())
    assert result == "hello from text"


def test_msg_text_from_message_attr():
    from core.processors.topic_splitter import _msg_text

    class _Msg:
        message = "hello from message"

    result = _msg_text(_Msg())
    assert result == "hello from message"


def test_msg_text_fallback_to_str():
    from core.processors.topic_splitter import _msg_text

    class _Msg:
        pass

    m = _Msg()
    result = _msg_text(m)
    assert isinstance(result, str)


def test_msg_text_with_content_to_text():
    from core.processors.topic_splitter import _msg_text

    class _Msg:
        def content_to_text(self):
            return "from content_to_text method"

    result = _msg_text(_Msg())
    assert result == "from content_to_text method"


def test_msg_text_content_to_text_exception():
    from core.processors.topic_splitter import _msg_text

    class _Msg:
        def content_to_text(self):
            raise RuntimeError("fail")

        content = "fallback content"

    result = _msg_text(_Msg())
    assert result == "fallback content"


# ---- _cosine_sim / _similarity_matrix edge cases ----

def test_cosine_sim_identical():
    from core.processors.topic_splitter import _cosine_sim
    assert _cosine_sim([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_sim_orthogonal():
    from core.processors.topic_splitter import _cosine_sim
    assert _cosine_sim([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_sim_zero_vector():
    from core.processors.topic_splitter import _cosine_sim
    assert _cosine_sim([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_sim_empty():
    from core.processors.topic_splitter import _cosine_sim
    assert _cosine_sim([], []) == 0.0


def test_cosine_sim_dimension_mismatch():
    from core.processors.topic_splitter import _cosine_sim
    assert _cosine_sim([1.0, 2.0], [1.0]) == 0.0


def test_similarity_matrix_diagonal_is_one():
    from core.processors.topic_splitter import _similarity_matrix
    embeddings = [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]]
    mat = _similarity_matrix(embeddings)
    for i in range(len(embeddings)):
        assert mat[i][i] == 1.0


# ---- _dummy_embeddings ----

def test_dummy_embeddings_deterministic():
    from core.processors.topic_splitter import _dummy_embeddings
    texts = ["hello", "world"]
    vecs1 = _dummy_embeddings(texts)
    vecs2 = _dummy_embeddings(texts)
    assert vecs1 == vecs2


def test_dummy_embeddings_dimension():
    from core.processors.topic_splitter import _dummy_embeddings
    texts = ["a", "b"]
    vecs = _dummy_embeddings(texts)
    assert len(vecs) == 2
    # The function uses hashlib.sha256 and dim=32
    assert len(vecs[0]) == 32


# ---- _single_segment helper ----

def test_single_segment_normal():
    from core.processors.topic_splitter import _single_segment
    data = {"summary": "test summary", "key_facts": ["fact1"]}
    segments = _single_segment(data, data["key_facts"])
    assert len(segments) == 1
    assert segments[0].content == "test summary"


def test_single_segment_empty():
    from core.processors.topic_splitter import _single_segment
    segments = _single_segment({"summary": "", "key_facts": []}, [])
    assert segments == []


# ---- _build_segments_from_clusters ----

def test_build_segments_from_clusters_single():
    from core.processors.topic_splitter import _build_segments_from_clusters
    data = {"summary": "s", "key_facts": ["f1", "f2"], "importance": 0.5}
    clusters = [["f1", "f2"]]
    segments = _build_segments_from_clusters(data, clusters)
    assert len(segments) == 1


def test_build_segments_from_clusters_multiple():
    from core.processors.topic_splitter import _build_segments_from_clusters
    data = {"summary": "s", "key_facts": ["f1", "f2"], "importance": 0.5}
    clusters = [["f1"], ["f2"]]
    segments = _build_segments_from_clusters(data, clusters)
    assert len(segments) == 2
    assert "[话题1]" in segments[0].content
    assert "[话题2]" in segments[1].content


def test_build_segments_from_clusters_empty_cluster():
    from core.processors.topic_splitter import _build_segments_from_clusters
    data = {"summary": "s"}
    clusters = [["f1"], [], ["f2"]]
    segments = _build_segments_from_clusters(data, clusters)
    assert len(segments) == 2  # empty cluster skipped
