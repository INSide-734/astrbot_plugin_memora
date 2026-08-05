"""验证话题 B 与 A+B Hybrid 已接入真实记忆处理链。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models.conversation_models import Message
from core.processors.memory_grounding import GroundingResult
from core.processors.memory_processor import MemoryProcessor


def _messages(*names: str) -> list[Message]:
    """构造不携带可信身份标记的群聊消息。"""

    return [
        Message(
            id=index,
            session_id="topic-production",
            role="user",
            content=f"{name} 的测试消息",
            sender_id=f"legacy-{index}",
            sender_name=name,
            metadata={},
        )
        for index, name in enumerate(names, start=1)
    ]


def _processor(
    structured_data: dict,
    *,
    config: dict,
    embed_fn: AsyncMock,
) -> MemoryProcessor:
    """构造返回固定结构化结果并注入话题向量入口的处理器。"""

    provider = MagicMock()
    response = MagicMock()
    response.completion_text = "{}"
    provider.text_chat = AsyncMock(return_value=response)
    processor = MemoryProcessor(
        llm_provider=provider,
        config=config,
        topic_embed_fn=embed_fn,
    )
    processor._parse_llm_response = MagicMock(return_value=structured_data)
    return processor


@pytest.mark.asyncio
async def test_hybrid_fallback_runs_in_memory_processor() -> None:
    """A 只产出一条混合记忆时，生产链必须调用 B 并记录低敏决策。"""

    embed_fn = AsyncMock(return_value=[[1.0, 0.0], [0.0, 1.0]])
    processor = _processor(
        {
            "summary": "混合话题",
            "key_facts": ["Alice 喜欢咖啡", "Alice 负责部署"],
            "topics": ["混合"],
            "participants": ["Alice"],
            "importance": 0.7,
        },
        config={
            "topic_segmentation.enabled": True,
            "topic_segmentation.strategy": "a_b_hybrid",
            "topic_segmentation.hybrid_fallback_fact_threshold": 2,
            "topic_segmentation.strategy_b.similarity_threshold": 0.8,
            "topic_segmentation.strategy_b.max_clusters": 5,
        },
        embed_fn=embed_fn,
    )

    results = await processor.process_conversation(
        _messages("Alice"),
        is_group_chat=True,
    )

    assert len(results) == 2
    embed_fn.assert_awaited_once_with(["Alice 喜欢咖啡", "Alice 负责部署"])
    for result in results:
        metadata = result["metadata"]
        assert metadata["topic_segmentation_strategy"] == "a_b_hybrid"
        assert metadata["topic_segmentation_fallback_reason"] == "a_single_mixed_facts"
        assert metadata["topic_segmentation_input_count"] == 1
        assert metadata["topic_segmentation_output_count"] == 2


@pytest.mark.asyncio
async def test_hybrid_keeps_single_topic_without_embedding_fallback() -> None:
    """事实数未达门槛时，单话题结果必须保持单条且不调用向量入口。"""

    embed_fn = AsyncMock(return_value=[[1.0, 0.0], [1.0, 0.0]])
    processor = _processor(
        {
            "summary": "咖啡偏好",
            "key_facts": ["Alice 喜欢拿铁", "Alice 喜欢深烘豆"],
            "topics": ["咖啡"],
            "participants": ["Alice"],
            "importance": 0.6,
        },
        config={
            "topic_segmentation.enabled": True,
            "topic_segmentation.strategy": "a_b_hybrid",
            "topic_segmentation.hybrid_fallback_fact_threshold": 3,
        },
        embed_fn=embed_fn,
    )

    results = await processor.process_conversation(
        _messages("Alice"),
        is_group_chat=True,
    )

    assert len(results) == 1
    embed_fn.assert_not_awaited()
    metadata = results[0]["metadata"]
    assert metadata["participants"] == ["Alice"]
    assert metadata["topic_segmentation_strategy"] == "a_b_hybrid"
    assert "topic_segmentation_fallback_reason" not in metadata


@pytest.mark.asyncio
async def test_strategy_b_never_clusters_across_participant_memories() -> None:
    """纯 B 策略必须按原始 memory 边界聚类，不能跨主体合并事实。"""

    embed_fn = AsyncMock(
        side_effect=[
            [[1.0, 0.0], [1.0, 0.0]],
            [[1.0, 0.0], [1.0, 0.0]],
        ]
    )
    processor = _processor(
        {
            "memories": [
                {
                    "summary": "Alice 的偏好",
                    "key_facts": ["Alice 喜欢咖啡", "Alice 喜欢拿铁"],
                    "topics": ["咖啡"],
                    "participants": ["Alice"],
                    "source_refs": [{"message_index": 0, "start": 0, "end": 5}],
                    "importance": 0.6,
                },
                {
                    "summary": "Bob 的偏好",
                    "key_facts": ["Bob 喜欢咖啡", "Bob 喜欢拿铁"],
                    "topics": ["咖啡"],
                    "participants": ["Bob"],
                    "source_refs": [{"message_index": 1, "start": 0, "end": 3}],
                    "importance": 0.6,
                },
            ]
        },
        config={
            "topic_segmentation.enabled": True,
            "topic_segmentation.strategy": "b",
            "topic_segmentation.strategy_b.similarity_threshold": 0.8,
            "topic_segmentation.strategy_b.max_clusters": 5,
        },
        embed_fn=embed_fn,
    )
    captured_candidates: list[dict] = []

    def capture_grounding(candidate: dict, *_args, **_kwargs) -> GroundingResult:
        """记录送入来源校验器的分段候选并返回确定性通过。"""

        captured_candidates.append(candidate)
        return GroundingResult(True, "grounded", (), [])

    processor.grounding_validator.validate = MagicMock(side_effect=capture_grounding)

    results = await processor.process_conversation(
        _messages("Alice", "Bob"),
        is_group_chat=True,
    )

    assert len(results) == 2
    assert embed_fn.await_count == 2
    assert [result["metadata"]["participants"] for result in results] == [
        ["Alice"],
        ["Bob"],
    ]
    assert [candidate["source_refs"] for candidate in captured_candidates] == [
        [{"message_index": 0, "start": 0, "end": 5}],
        [{"message_index": 1, "start": 0, "end": 3}],
    ]
    assert all(
        result["metadata"]["topic_segmentation_strategy"] == "b" for result in results
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["c", "d"])
async def test_prechunk_strategies_are_not_split_again_in_processor(
    strategy: str,
) -> None:
    """C/D 继续只由预切分层负责，处理器不得再次运行分段策略。"""

    embed_fn = AsyncMock(return_value=[[1.0, 0.0], [0.0, 1.0]])
    processor = _processor(
        {
            "summary": "上游已准备的批次",
            "key_facts": ["事实一", "事实二"],
            "topics": ["批次"],
            "importance": 0.5,
        },
        config={
            "topic_segmentation.enabled": True,
            "topic_segmentation.strategy": strategy,
        },
        embed_fn=embed_fn,
    )

    results = await processor.process_conversation(_messages("Alice"))

    assert len(results) == 1
    embed_fn.assert_not_awaited()
    assert "topic_segmentation_strategy" not in results[0]["metadata"]
