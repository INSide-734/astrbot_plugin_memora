"""类人记忆的情感与季节性召回增强算法。"""

from __future__ import annotations

import json

from ....shared.number_utils import safe_float
from ...retrieval.emotion_scorer import compute_emotion_boost, emotion_similarity
from ...retrieval.rrf_fusion import HybridResult
from ...retrieval.seasonal_recall import seasonal_boost


def _emotion_tags(metadata: dict[str, object]) -> list[str]:
    """从 metadata 安全读取情绪标签列表。"""

    tags = metadata.get("emotion_tags", []) or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            tags = []
    if not isinstance(tags, list):
        return []
    return [str(tag).casefold().strip() for tag in tags if str(tag).strip()]


def apply_emotion_boost(
    results: list[HybridResult],
    emotion_context: list[str] | None,
    *,
    mode: str = "enhanced",
) -> list[HybridResult]:
    """按 disabled、basic 或 enhanced 模式应用情感分数增强。"""

    normalized_mode = str(mode).casefold().strip()
    if normalized_mode == "disabled" or not emotion_context:
        return results

    context_tags = {
        str(tag).casefold().strip() for tag in emotion_context if str(tag).strip()
    }
    if not context_tags:
        return results

    for result in results:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        memory_tags = _emotion_tags(metadata)
        if normalized_mode == "basic":
            overlap = len(context_tags.intersection(memory_tags)) / len(context_tags)
            boost = 1.0 + 0.1 * overlap
        else:
            memory_intensity = safe_float(
                metadata.get("emotional_intensity"),
                0.5,
            )
            similarity = emotion_similarity(
                list(context_tags),
                memory_tags,
                memory_intensity,
            )
            boost = compute_emotion_boost(similarity)
        result.final_score *= boost

    results.sort(key=lambda item: item.final_score, reverse=True)
    return results


def apply_seasonal_boost(
    results: list[HybridResult],
    *,
    enabled: bool = True,
) -> list[HybridResult]:
    """在启用时按事件周年距离应用季节性召回倍率。"""

    if not enabled:
        return results
    for result in results:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        timestamp = (
            metadata.get("event_time")
            or metadata.get("create_time")
            or metadata.get("timestamp")
        )
        if timestamp is not None:
            result.final_score *= seasonal_boost(float(timestamp))
    return results


__all__ = ["apply_emotion_boost", "apply_seasonal_boost"]
