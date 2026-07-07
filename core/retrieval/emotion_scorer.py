"""
情感计分模块 - 情绪一致性偏差记忆召回

人类在特定情绪下更容易回忆起情绪相似的记忆（情绪一致性效应）。
本模块计算当前情感上下文与记忆情感标签之间的相似度，为检索排序提供情感维度的加成。
"""


def emotion_similarity(
    current_tags: list[str],
    memory_tags: list[str],
    memory_intensity: float,
) -> float:
    if not current_tags or not memory_tags:
        return 0.0

    current_set = set(current_tags)
    memory_set = set(memory_tags)

    intersection = current_set & memory_set
    union = current_set | memory_set

    jaccard = len(intersection) / len(union)

    max_len = max(len(current_set), len(memory_set))
    overlap_ratio = len(intersection) / max_len

    intensity = max(0.0, min(1.0, memory_intensity))

    return 0.4 * jaccard + 0.35 * overlap_ratio + 0.25 * intensity


def compute_emotion_boost(
    similarity_score: float,
    base_multiplier: float = 0.3,
) -> float:
    return 1.0 + base_multiplier * similarity_score


__all__ = ["emotion_similarity", "compute_emotion_boost"]
