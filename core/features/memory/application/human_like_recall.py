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


def _event_timestamp(metadata: dict[str, object]) -> float:
    """读取事件时间；只接受数值 Unix 秒，字段缺失或非法时返回 0。"""

    for key in ("event_time", "create_time", "timestamp"):
        raw_value = metadata.get(key)
        if raw_value is None:
            continue
        # 字段存在即视为事件时间来源：非法值直接跳过，不回退到另一字段。
        return safe_float(raw_value, 0.0)
    return 0.0


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
        event_timestamp = _event_timestamp(metadata)
        if event_timestamp <= 0:
            continue
        try:
            result.final_score *= seasonal_boost(event_timestamp)
        except (OverflowError, OSError, ValueError):
            # 派生增强失败不得中断召回链：时间值超出可转换范围时跳过。
            continue
    return results


__all__ = ["apply_emotion_boost", "apply_seasonal_boost"]
