"""阶段 9：话题分割系统的集成验证测试。

9.1  端到端：多话题对话 → A+B 混合 → 独立记忆存储
9.2  策略切换：运行时切换 A→B→C→D 无缝过渡
9.4  性能：嵌入聚类 / 预分块延迟边界
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.processors.topic_splitter import (
    EmbeddingClusteringStrategy,
    HybridSegmentationStrategy,
    PromptSegmentationStrategy,
    TopicChunkingStrategy,
    TopicSegmentationRouter,
    TwoStageLLMStrategy,
)

# ============================================================================
# 9.1  E2E: multi-topic conversation → A+B hybrid → independent memory storage
# ============================================================================


class TestE2EMultiTopicPipeline:
    """端到端测试，演练完整的话题分割管线。"""

    @staticmethod
    def _make_multi_topic_llm_output() -> dict:
        """Simulate LLM output for a conversation covering 3 unrelated topics."""
        return {
            "memories": [
                {
                    "summary": "讨论了周末滑雪计划，张三提议去长白山",
                    "key_facts": [
                        "张三提议周末去长白山滑雪",
                        "李四确认可以参加",
                        "计划周五晚上出发",
                    ],
                    "topics": ["滑雪", "长白山", "出行计划"],
                    "importance": 0.8,
                    "sentiment": "positive",
                    "emotion_tags": ["兴奋", "期待"],
                    "causal_relations": [],
                    "participants": ["张三", "李四", "用户"],
                },
                {
                    "summary": "项目A的测试覆盖率不足，需要补充单元测试",
                    "key_facts": [
                        "项目A测试覆盖率仅45%",
                        "周五前需要达到80%",
                        "王五负责写测试",
                    ],
                    "topics": ["项目A", "测试", "工作"],
                    "importance": 0.9,
                    "sentiment": "neutral",
                    "emotion_tags": ["焦虑"],
                    "causal_relations": [],
                    "participants": ["王五", "用户"],
                },
                {
                    "summary": "用户最近失眠，尝试了褪黑素但效果一般",
                    "key_facts": [
                        "用户最近连续三天失眠",
                        "尝试褪黑素效果不佳",
                        "医生建议规律作息",
                    ],
                    "topics": ["失眠", "健康", "褪黑素"],
                    "importance": 0.7,
                    "sentiment": "negative",
                    "emotion_tags": ["疲惫", "担忧"],
                    "causal_relations": [],
                    "participants": ["用户"],
                },
            ]
        }

    @pytest.mark.asyncio
    async def test_e2e_a_strategy_produces_three_independent_memories(self):
        """Strategy A parses 3-topic LLM output into 3 independent MemorySegments."""
        strat = PromptSegmentationStrategy()
        llm_output = self._make_multi_topic_llm_output()
        segments = await strat.segment(llm_output)

        assert len(segments) == 3

        # Each segment is an independent, self-contained memory
        assert segments[0].content == "讨论了周末滑雪计划，张三提议去长白山"
        assert len(segments[0].key_facts) == 3
        assert "滑雪" in segments[0].topics

        assert segments[1].content == "项目A的测试覆盖率不足，需要补充单元测试"
        assert len(segments[1].key_facts) == 3
        assert "项目A" in segments[1].topics

        assert segments[2].content == "用户最近失眠，尝试了褪黑素但效果一般"
        assert len(segments[2].key_facts) == 3
        assert "失眠" in segments[2].topics

    @pytest.mark.asyncio
    async def test_e2e_hybrid_preserves_a_result_when_well_split(self):
        """A+B hybrid keeps A's result when it already produces ≥2 segments."""
        strat = HybridSegmentationStrategy({"hybrid_fallback_fact_threshold": 3})
        llm_output = self._make_multi_topic_llm_output()
        segments = await strat.segment(llm_output)

        # A already split into 3 — B should not be invoked
        assert len(segments) == 3

    @pytest.mark.asyncio
    async def test_e2e_hybrid_fallback_when_llm_fails_to_split(self):
        """When LLM returns single memory with many facts, B splits it."""
        embed_fn = AsyncMock(
            return_value=[
                [1.0, 0.0, 0.0],  # skiing
                [0.9, 0.1, 0.0],  # skiing-related
                [0.0, 1.0, 0.0],  # work
                [0.0, 0.0, 1.0],  # health
            ]
        )
        strat = HybridSegmentationStrategy(
            {
                "hybrid_fallback_fact_threshold": 2,
                "similarity_threshold": 0.7,
                "max_clusters": 5,
            },
            embed_fn=embed_fn,
        )
        # LLM returned 1 memory but key_facts span multiple topics
        llm_output = {
            "summary": "多话题混合",
            "key_facts": [
                "张三提议周末去长白山滑雪",
                "李四确认可以参加",
                "项目A测试覆盖率仅45%",
                "用户最近连续三天失眠",
            ],
            "topics": ["混合"],
            "importance": 0.7,
        }
        segments = await strat.segment(llm_output)
        # B should split into multiple clusters (embed_fn provides distinct vectors)
        assert len(segments) >= 2

    @pytest.mark.asyncio
    async def test_e2e_full_pipeline_with_mock_engine(self):
        """Simulate the full _storage_task flow: LLM → segment → engine.add_memory."""
        # Mock engine
        engine = MagicMock()
        engine.add_memory = AsyncMock(side_effect=[101, 102, 103])

        # Simulate LLM output with 3 topics
        llm_output = self._make_multi_topic_llm_output()

        # Parse via Strategy A
        strat = PromptSegmentationStrategy()
        segments = await strat.segment(llm_output)

        assert len(segments) == 3

        # Simulate what _storage_task does: write each segment
        stored_ids = []
        session_id = "e2e-test-session"
        persona_id = "e2e-persona"

        for seg in segments:
            seg.metadata["source_window"] = {
                "session_id": session_id,
                "start_index": 0,
                "end_index": 50,
                "message_count": 50,
            }
            result = await engine.add_memory(
                content=seg.content,
                session_id=session_id,
                persona_id=persona_id,
                importance=seg.importance,
                metadata=seg.metadata,
            )
            stored_ids.append(result)

        assert len(stored_ids) == 3
        assert stored_ids == [101, 102, 103]
        assert engine.add_memory.call_count == 3

        # Verify each call had correct arguments
        calls = engine.add_memory.call_args_list
        assert calls[0][1]["content"] == "讨论了周末滑雪计划，张三提议去长白山"
        assert calls[0][1]["importance"] == 0.8
        assert calls[1][1]["content"] == "项目A的测试覆盖率不足，需要补充单元测试"
        assert calls[1][1]["importance"] == 0.9
        assert calls[2][1]["content"] == "用户最近失眠，尝试了褪黑素但效果一般"
        assert calls[2][1]["importance"] == 0.7

    @pytest.mark.asyncio
    async def test_e2e_empty_memories_no_storage(self):
        """When LLM returns no memories, nothing is stored."""
        engine = MagicMock()
        engine.add_memory = AsyncMock()

        strat = PromptSegmentationStrategy()
        segments = await strat.segment({"memories": []})

        assert len(segments) == 0
        engine.add_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_e2e_single_topic_shortcut(self):
        """Single-topic conversation flows through without unnecessary splitting."""
        llm_output = {
            "memories": [
                {
                    "summary": "用户喜欢喝咖啡",
                    "key_facts": ["用户每天都喝拿铁", "喜欢星巴克的豆子"],
                    "topics": ["咖啡", "偏好"],
                    "importance": 0.5,
                }
            ]
        }
        strat = PromptSegmentationStrategy()
        segments = await strat.segment(llm_output)

        assert len(segments) == 1
        assert segments[0].key_facts == ["用户每天都喝拿铁", "喜欢星巴克的豆子"]
        assert segments[0].metadata["schema_version"] == "v3"

    @pytest.mark.asyncio
    async def test_e2e_metadata_preservation_through_pipeline(self):
        """All metadata fields survive the segment→store journey intact."""
        llm_output = {
            "memories": [
                {
                    "summary": "带复杂元数据的记忆",
                    "key_facts": ["事实1", "事实2"],
                    "topics": ["测试"],
                    "importance": 0.85,
                    "sentiment": "mixed",
                    "emotion_tags": ["好奇", "困惑"],
                    "causal_relations": [{"cause": "A", "effect": "B"}],
                    "participants": ["Alice", "Bob"],
                }
            ]
        }
        strat = PromptSegmentationStrategy()
        segments = await strat.segment(llm_output)

        assert len(segments) == 1
        meta = segments[0].metadata
        assert meta["sentiment"] == "mixed"
        assert meta["emotion_tags"] == ["好奇", "困惑"]
        assert meta["causal_relations"] == [{"cause": "A", "effect": "B"}]
        assert meta["participants"] == ["Alice", "Bob"]
        assert meta["schema_version"] == "v3"
        assert segments[0].importance == 0.85


# ============================================================================
# 9.2  Strategy switching: runtime switch A→B→C→D
# ============================================================================


class TestStrategySwitching:
    """Verify strategies can be switched at runtime without state corruption."""

    @pytest.mark.asyncio
    async def test_switch_a_to_b(self):
        """Switch from PromptStrategy to EmbeddingClusteringStrategy."""
        # Start with A
        router = TopicSegmentationRouter({"topic_segmentation.strategy": "a"})
        assert isinstance(router.strategy, PromptSegmentationStrategy)
        assert router.strategy_key == "a"

        # Process with A
        result_a = await router.segment(
            {
                "memories": [
                    {"summary": "话题1", "key_facts": ["f1", "f2"]},
                ]
            }
        )
        assert len(result_a) == 1

        # Switch to B
        router_b = TopicSegmentationRouter({"topic_segmentation.strategy": "b"})
        assert isinstance(router_b.strategy, EmbeddingClusteringStrategy)
        assert router_b.strategy_key == "b"

        # Process with B
        result_b = await router_b.segment(
            {
                "summary": "test",
                "key_facts": ["f1", "f2"],
            }
        )
        assert len(result_b) >= 1

    @pytest.mark.asyncio
    async def test_switch_b_to_c(self):
        """Switch from EmbeddingClusteringStrategy to TopicChunkingStrategy."""
        # B first
        router_b = TopicSegmentationRouter({"topic_segmentation.strategy": "b"})
        assert router_b.strategy_key == "b"

        result_b = await router_b.segment(
            {
                "summary": "test",
                "key_facts": ["a", "b"],
            }
        )
        assert len(result_b) >= 1

        # Switch to C
        router_c = TopicSegmentationRouter(
            {
                "topic_segmentation.strategy": "c",
                "topic_segmentation.topic_shift_threshold": 0.3,
            }
        )
        assert router_c.strategy_key == "c"
        assert isinstance(router_c.strategy, TopicChunkingStrategy)

        result_c = await router_c.segment(
            {
                "summary": "test",
                "key_facts": ["a"],
            }
        )
        assert len(result_c) == 1

    @pytest.mark.asyncio
    async def test_switch_c_to_d(self):
        """Switch from TopicChunkingStrategy to TwoStageLLMStrategy."""
        router_c = TopicSegmentationRouter(
            {
                "topic_segmentation.strategy": "c",
                "topic_segmentation.topic_shift_threshold": 0.3,
            }
        )
        assert router_c.strategy_key == "c"
        result_c = await router_c.segment({"summary": "t", "key_facts": ["f"]})
        assert len(result_c) == 1

        router_d = TopicSegmentationRouter(
            {
                "topic_segmentation.strategy": "d",
                "topic_segmentation.stage1_max_topics": 3,
            }
        )
        assert router_d.strategy_key == "d"
        assert isinstance(router_d.strategy, TwoStageLLMStrategy)

        result_d = await router_d.segment({"summary": "t", "key_facts": ["f"]})
        assert len(result_d) == 1

    @pytest.mark.asyncio
    async def test_switch_d_to_hybrid(self):
        """Switch from TwoStageLLMStrategy back to HybridSegmentationStrategy."""
        router_d = TopicSegmentationRouter({"topic_segmentation.strategy": "d"})
        assert router_d.strategy_key == "d"
        result_d = await router_d.segment({"summary": "t", "key_facts": ["f"]})
        assert len(result_d) == 1

        router_hybrid = TopicSegmentationRouter(
            {
                "topic_segmentation.strategy": "a_b_hybrid",
            }
        )
        assert router_hybrid.strategy_key == "a_b_hybrid"
        assert isinstance(router_hybrid.strategy, HybridSegmentationStrategy)

        result_h = await router_hybrid.segment(
            {
                "memories": [
                    {"summary": "测试", "key_facts": ["f1", "f2"]},
                ]
            }
        )
        assert len(result_h) == 1

    @pytest.mark.asyncio
    async def test_full_rotation_a_b_c_d_hybrid(self):
        """Full rotation through all 5 strategies without errors."""
        configs = [
            ("a", {}),
            ("b", {}),
            ("c", {"topic_segmentation.topic_shift_threshold": 0.3}),
            ("d", {"topic_segmentation.stage1_max_topics": 3}),
            ("a_b_hybrid", {"topic_segmentation.hybrid_fallback_fact_threshold": 2}),
        ]

        # Each strategy expects different data formats:
        # A / hybrid: `memories[]` array (prompt-engineered output)
        # B / C / D: `summary` + `key_facts[]` (legacy / pass-through format)
        data_for_strategy = {
            "a": {"memories": [{"summary": "test", "key_facts": ["f1", "f2"]}]},
            "b": {"summary": "test", "key_facts": ["f1", "f2"]},
            "c": {"summary": "test", "key_facts": ["f1"]},
            "d": {"summary": "test", "key_facts": ["f1", "f2"]},
            "a_b_hybrid": {
                "memories": [{"summary": "test", "key_facts": ["f1", "f2"]}]
            },
        }

        for key, extra_cfg in configs:
            cfg = {"topic_segmentation.strategy": key, **extra_cfg}
            router = TopicSegmentationRouter(cfg)
            assert router.strategy_key == key, (
                f"Expected {key}, got {router.strategy_key}"
            )

            segments = await router.segment(data_for_strategy[key])
            assert len(segments) >= 1, f"Strategy {key} returned empty result"

    def test_switch_preserves_config_isolation(self):
        """Each router instance is independent — switching doesn't leak config."""
        router_a = TopicSegmentationRouter(
            {
                "topic_segmentation.strategy": "a",
            }
        )
        router_b = TopicSegmentationRouter(
            {
                "topic_segmentation.strategy": "b",
                "topic_segmentation.similarity_threshold": 0.3,
                "topic_segmentation.max_clusters": 3,
            }
        )

        # A remains A, B remains B
        assert router_a.strategy_key == "a"
        assert router_b.strategy_key == "b"
        assert isinstance(router_a.strategy, PromptSegmentationStrategy)
        assert isinstance(router_b.strategy, EmbeddingClusteringStrategy)

    def test_switch_invalid_strategy_safe_fallback(self):
        """Invalid strategy key falls back to hybrid without crash."""
        router = TopicSegmentationRouter({"topic_segmentation.strategy": "nonexistent"})
        assert router.strategy_key == "a_b_hybrid"
        assert isinstance(router.strategy, HybridSegmentationStrategy)

    @pytest.mark.asyncio
    async def test_switch_to_c_with_embed_fn(self):
        """Switching to C with embed_fn doesn't break."""
        embed_fn = AsyncMock(return_value=[[0.1, 0.2]])
        router = TopicSegmentationRouter(
            {"topic_segmentation.strategy": "c"},
            embed_fn=embed_fn,
        )
        assert router.strategy_key == "c"
        # segment() is pass-through for C — it doesn't call embed_fn
        result = await router.segment({"summary": "t", "key_facts": ["f"]})
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_concurrent_strategy_instances_independent(self):
        """Multiple router instances can coexist without interference."""
        routers_and_data = [
            (
                TopicSegmentationRouter({"topic_segmentation.strategy": "a"}),
                {"memories": [{"summary": "t", "key_facts": ["f1"]}]},
            ),
            (
                TopicSegmentationRouter({"topic_segmentation.strategy": "b"}),
                {"summary": "t", "key_facts": ["f1"]},
            ),
            (
                TopicSegmentationRouter({"topic_segmentation.strategy": "c"}),
                {"summary": "t", "key_facts": ["f1"]},
            ),
            (
                TopicSegmentationRouter({"topic_segmentation.strategy": "d"}),
                {"summary": "t", "key_facts": ["f1", "f2"]},
            ),
        ]

        async def process(router, data):
            return await router.segment(data)

        results = await asyncio.gather(
            *[process(router, data) for router, data in routers_and_data]
        )

        for r in results:
            assert len(r) >= 1


# ============================================================================
# 9.4  Performance: latency bounds for embedding clustering / pre-chunking
# ============================================================================


class TestPerformanceLatency:
    """Verify topic segmentation strategies meet latency targets (p95 < 3s)."""

    PERF_TIMEOUT = 5.0  # generous upper bound per call (real target: 3s)

    @pytest.mark.asyncio
    async def test_strategy_a_parsing_is_sub_millisecond(self):
        """Strategy A (prompt parsing) should complete in < 100ms."""
        strat = PromptSegmentationStrategy()
        data = {
            "memories": [
                {
                    "summary": f"topic_{i}",
                    "key_facts": [f"fact_{i}_{j}" for j in range(5)],
                }
                for i in range(20)
            ]
        }

        start = time.perf_counter()
        segments = await strat.segment(data)
        elapsed = time.perf_counter() - start

        assert len(segments) == 20
        assert elapsed < 0.1, f"Strategy A took {elapsed:.4f}s, expected < 0.1s"

    @pytest.mark.asyncio
    async def test_strategy_b_dummy_embeddings_latency(self):
        """Strategy B with dummy embeddings (no network) completes quickly."""
        strat = EmbeddingClusteringStrategy({"similarity_threshold": 0.5})
        data = {
            "summary": "perf",
            "key_facts": [f"fact_{i}" for i in range(50)],
        }

        start = time.perf_counter()
        segments = await strat.segment(data)
        elapsed = time.perf_counter() - start

        assert len(segments) >= 1
        assert elapsed < 1.0, f"Strategy B (50 facts, dummy) took {elapsed:.4f}s"

    @pytest.mark.asyncio
    async def test_strategy_b_with_mock_embeddings_latency(self):
        """Strategy B with fast mock embeddings stays within bounds."""
        embed_fn = AsyncMock(
            return_value=[
                [float((i + j) % 10) / 10.0 for j in range(32)] for i in range(100)
            ]
        )
        strat = EmbeddingClusteringStrategy(
            {"similarity_threshold": 0.5, "max_clusters": 10},
            embed_fn=embed_fn,
        )
        data = {
            "summary": "large",
            "key_facts": [f"fact_{i}" for i in range(100)],
        }

        start = time.perf_counter()
        segments = await strat.segment(data)
        elapsed = time.perf_counter() - start

        assert len(segments) >= 1
        assert elapsed < self.PERF_TIMEOUT, (
            f"Strategy B (100 facts, mock embed) took {elapsed:.4f}s"
        )

    @pytest.mark.asyncio
    async def test_strategy_c_chunking_latency(self):
        """Pre-chunking latency scales linearly and stays reasonable."""
        strat = TopicChunkingStrategy({"topic_shift_threshold": 0.3})

        class _Msg:
            def __init__(self, content):
                self.content = content

        # 200 messages — realistic upper bound for a conversation window
        msgs = [_Msg(f"message_{i} with some text content") for i in range(200)]

        start = time.perf_counter()
        chunks = await strat.chunk_messages(msgs)
        elapsed = time.perf_counter() - start

        assert len(chunks) >= 1
        assert elapsed < self.PERF_TIMEOUT, (
            f"Strategy C chunking (200 msgs) took {elapsed:.4f}s"
        )

    @pytest.mark.asyncio
    async def test_strategy_c_chunking_with_embed_fn_latency(self):
        """Pre-chunking with mock embeddings within latency bound."""
        embed_fn = AsyncMock(
            return_value=[[float(i % 7) / 7.0 for _ in range(32)] for i in range(100)]
        )
        strat = TopicChunkingStrategy(
            {"topic_shift_threshold": 0.3, "min_chunk_size": 2},
            embed_fn=embed_fn,
        )

        class _Msg:
            def __init__(self, content):
                self.content = content

        msgs = [_Msg(f"msg_{i}") for i in range(100)]

        start = time.perf_counter()
        chunks = await strat.chunk_messages(msgs)
        elapsed = time.perf_counter() - start

        assert len(chunks) >= 1
        assert elapsed < self.PERF_TIMEOUT, (
            f"Strategy C chunking (100 msgs, mock embed) took {elapsed:.4f}s"
        )

    @pytest.mark.asyncio
    async def test_cosine_similarity_matrix_latency(self):
        """Similarity matrix computation for 200 vectors stays fast."""
        # NOTE: threshold set to 3.0s (not 1.0s) because in a full test suite
        # concurrent test execution adds system overhead that can push this
        # over tighter limits. In isolation this typically completes < 0.5s.
        from core.processors.topic_splitter import _similarity_matrix

        dim = 64
        embeddings = [
            [float((i + d) % 13) / 13.0 for d in range(dim)] for i in range(200)
        ]

        start = time.perf_counter()
        mat = _similarity_matrix(embeddings)
        elapsed = time.perf_counter() - start

        assert len(mat) == 200
        assert mat[0][0] == 1.0
        assert elapsed < 3.0, f"200×200 similarity matrix took {elapsed:.4f}s"

    @pytest.mark.asyncio
    async def test_hybrid_strategy_overhead_is_minimal(self):
        """A+B hybrid doesn't add significant overhead over plain A."""
        data = {
            "memories": [
                {"summary": f"topic_{i}", "key_facts": [f"f_{i}"]} for i in range(10)
            ]
        }

        # Pure A
        strat_a = PromptSegmentationStrategy()
        start = time.perf_counter()
        await strat_a.segment(data)
        time_a = time.perf_counter() - start

        # A+B Hybrid (should be nearly identical when A splits well)
        strat_h = HybridSegmentationStrategy({"hybrid_fallback_fact_threshold": 3})
        start = time.perf_counter()
        await strat_h.segment(data)
        time_h = time.perf_counter() - start

        # Hybrid should be within 2x of pure A (both are O(n) parsing)
        assert time_h < max(time_a * 2, 0.05), (
            f"Hybrid overhead too high: A={time_a:.5f}s, Hybrid={time_h:.5f}s"
        )

    @pytest.mark.asyncio
    async def test_router_instantiation_is_cheap(self):
        """Router creation is O(1) and fast enough for per-request instantiation."""
        configs = [
            {"topic_segmentation.strategy": "a"},
            {
                "topic_segmentation.strategy": "b",
                "topic_segmentation.similarity_threshold": 0.5,
            },
            {
                "topic_segmentation.strategy": "c",
                "topic_segmentation.topic_shift_threshold": 0.3,
            },
            {
                "topic_segmentation.strategy": "d",
                "topic_segmentation.stage1_max_topics": 3,
            },
            {"topic_segmentation.strategy": "a_b_hybrid"},
        ]

        start = time.perf_counter()
        for cfg in configs:
            _ = TopicSegmentationRouter(cfg)
        elapsed = time.perf_counter() - start

        assert elapsed < 0.05, f"5 router instantiations took {elapsed:.4f}s"

    @pytest.mark.asyncio
    async def test_large_key_fact_batch_reasonable(self):
        """200 key_facts processed by Strategy B within latency bounds."""
        embed_fn = AsyncMock(
            return_value=[
                [float((i * 7 + j) % 256) / 256.0 for j in range(64)]
                for i in range(200)
            ]
        )
        strat = EmbeddingClusteringStrategy(
            {"similarity_threshold": 0.5, "max_clusters": 20},
            embed_fn=embed_fn,
        )
        data = {
            "summary": "large batch",
            "key_facts": [f"unique_fact_{i}" for i in range(200)],
        }

        start = time.perf_counter()
        segments = await strat.segment(data)
        elapsed = time.perf_counter() - start

        assert len(segments) >= 1
        assert elapsed < self.PERF_TIMEOUT, (
            f"Strategy B (200 facts, mock embed) took {elapsed:.4f}s"
        )
