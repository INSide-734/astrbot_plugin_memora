"""把话题分割 Router 接到结构化抽取与存储构建之间。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .topic_splitter import MemorySegment, TopicSegmentationRouter

TOPIC_SEGMENTATION_OBSERVABILITY_FIELDS = (
    "topic_segmentation_strategy",
    "topic_segmentation_fallback_reason",
    "topic_segmentation_input_count",
    "topic_segmentation_output_count",
)


class TopicSegmentationPipeline:
    """为 MemoryProcessor 路由 A/B/Hybrid，并保持 C/D 的预切分边界。"""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        embed_fn: Callable[[list[str]], Awaitable[list[list[float]]]] | None = None,
    ) -> None:
        """保存启用状态并构造复用真实 Embedding 入口的 Router。"""

        self._config = config or {}
        self._enabled = bool(self._config.get("topic_segmentation.enabled", True))
        self._router = TopicSegmentationRouter(self._config, embed_fn=embed_fn)

    async def prepare_candidates(
        self,
        structured_data: dict[str, Any],
        messages: list[Any],
        *,
        is_group_chat: bool,
    ) -> list[dict[str, Any]]:
        """返回存储构建器可消费的候选；C/D 与关闭状态保持原始批次语义。"""

        if not self._enabled or self._router.strategy_key in {"c", "d"}:
            return _raw_memory_candidates(structured_data)

        segments = await self._router.segment(
            structured_data,
            messages,
            is_group_chat,
        )
        return [_segment_to_candidate(segment) for segment in segments]


def _raw_memory_candidates(structured_data: dict[str, Any]) -> list[dict[str, Any]]:
    """按旧生产行为提取 memories[]，旧格式则包装为单条候选。"""

    raw_memories = structured_data.get("memories")
    if isinstance(raw_memories, list):
        return list(raw_memories)
    return [
        {
            "summary": structured_data.get("summary", ""),
            "key_facts": structured_data.get("key_facts", []),
            "topics": structured_data.get("topics", []),
            "importance": structured_data.get("importance", 0.5),
            "sentiment": structured_data.get("sentiment", "neutral"),
            "emotion_tags": structured_data.get("emotion_tags"),
            "causal_relations": structured_data.get("causal_relations"),
            "participants": structured_data.get("participants"),
            "source_refs": structured_data.get("source_refs"),
            "atom_type": structured_data.get("atom_type"),
            "confidence": structured_data.get("confidence"),
        }
    ]


def _segment_to_candidate(segment: MemorySegment) -> dict[str, Any]:
    """把分段结果转换为既有 MemoryProcessor 候选字典。"""

    candidate = dict(segment.metadata)
    candidate.update(
        {
            "summary": segment.content,
            "key_facts": list(segment.key_facts),
            "topics": list(segment.topics),
            "importance": segment.importance,
        }
    )
    return candidate


__all__ = [
    "TOPIC_SEGMENTATION_OBSERVABILITY_FIELDS",
    "TopicSegmentationPipeline",
]
