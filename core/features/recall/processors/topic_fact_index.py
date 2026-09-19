"""话题分割的逐事实下标与证据投影（纯函数）。

这里只负责「原始 ``key_facts`` 下标 → 事实文本 / 逐事实引用 / 分段元数据 /
事实簇」的映射，不推断引用、不猜测下标：任何无法证明索引一致的输入都按
fail-closed 返回 ``None``，由调用方放弃逐事实证据并让下游隔离该候选。
"""

from __future__ import annotations

from typing import Any

from .topic_embeddings import similarity_matrix


def indexed_facts(raw_facts: Any) -> tuple[list[str], list[int]]:
    """按原始下标提取非空事实，保持事实与下标一一对应。"""

    if not isinstance(raw_facts, list):
        return [], []
    facts: list[str] = []
    indices: list[int] = []
    for index, fact in enumerate(raw_facts):
        if not fact:
            continue
        facts.append(str(fact))
        indices.append(index)
    return facts, indices


def cluster_facts(
    original_facts: Any, cluster: list[Any]
) -> tuple[list[str], list[int] | None]:
    """把簇解析为事实文本与原始下标。

    下标簇按下标取值；旧式文本簇只能确认文本，无法保持索引时返回 ``None``，
    由调用方保守放弃逐事实引用，不回退到文本反查。
    """

    if all(isinstance(item, int) and not isinstance(item, bool) for item in cluster):
        if not isinstance(original_facts, list) or any(
            index < 0 or index >= len(original_facts) for index in cluster
        ):
            return [], None
        return [str(original_facts[index]) for index in cluster], list(cluster)
    return [str(item) for item in cluster], None


def aligned_fact_refs(
    data: dict[str, Any],
    key_facts: list[str],
    fact_indices: list[int] | None,
) -> list[list[Any]] | None:
    """按原始下标提取逐事实引用；无法证明下标与事实一致时返回 ``None``。

    只做下标映射与事实文本一致性核对，不做文本反查、不推断引用；任何不一致都
    按 fail-closed 处理，让下游按缺失证据隔离该候选。
    """

    original_facts = data.get("key_facts")
    fact_refs = data.get("fact_source_refs")
    if not isinstance(original_facts, list) or not isinstance(fact_refs, list):
        return None
    if len(fact_refs) != len(original_facts):
        return None
    if fact_indices is None:
        if len(key_facts) != len(original_facts):
            return None
        fact_indices = list(range(len(key_facts)))
    if len(fact_indices) != len(key_facts):
        return None
    aligned: list[list[Any]] = []
    seen: set[int] = set()
    for index, fact in zip(fact_indices, key_facts, strict=True):
        if isinstance(index, bool) or not isinstance(index, int):
            return None
        if index < 0 or index >= len(original_facts) or index in seen:
            return None
        original_fact = original_facts[index]
        if not isinstance(original_fact, str) or original_fact != fact:
            return None
        group = fact_refs[index]
        if not isinstance(group, list):
            return None
        seen.add(index)
        aligned.append(group)
    return aligned


def segment_metadata(
    data: dict[str, Any],
    key_facts: list[str],
    topics: list[str],
    fact_indices: list[int] | None = None,
) -> dict[str, Any]:
    """复制分段允许继承的内容元数据，不推断身份或作用域。

    ``fact_indices`` 是 ``key_facts`` 在 ``data["key_facts"]`` 中的原始下标；
    缺省只在片段事实等于原始全量列表时按位置对齐，其余情况不输出逐事实引用。
    """

    metadata: dict[str, Any] = {
        "topics": list(topics),
        "key_facts": list(key_facts),
        "sentiment": data.get("sentiment", "neutral"),
        "emotion_tags": data.get("emotion_tags") or [],
        "causal_relations": data.get("causal_relations") or [],
        "participants": data.get("participants") or [],
        "schema_version": "v3",
    }
    aligned_refs = aligned_fact_refs(data, key_facts, fact_indices)
    if aligned_refs is not None:
        metadata["fact_source_refs"] = aligned_refs
    for key in ("source_refs", "atom_type", "confidence"):
        if data.get(key) is not None:
            metadata[key] = data[key]
    return metadata


def agglomerative_index_clusters(
    embeddings: list[list[float]],
    fact_indices: list[int],
    *,
    threshold: float,
    max_clusters: int,
) -> list[list[int]]:
    """按余弦阈值把事实下标聚成簇（贪心凝聚 + 最大簇数上限）。

    聚类只使用向量相似度，事实文本不参与分组，重复事实也不会互相吞并。
    """

    n = len(fact_indices)
    if n <= 1:
        return [list(fact_indices)]

    sim = similarity_matrix(embeddings)

    # 基于配置阈值执行贪心式凝聚聚类
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # 合并相似度高于阈值的点对（遍历上三角）
    for i in range(n):
        for j in range(i + 1, n):
            if sim[i][j] >= threshold:
                union(i, j)

    # 收集聚类结果
    groups: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(fact_indices[i])

    clusters = list(groups.values())
    # 按簇大小排序（大的在前），并限制最大簇数
    clusters.sort(key=len, reverse=True)
    if len(clusters) > max_clusters:
        # 将超出上限的小簇并入最大簇
        overflow = clusters[max_clusters:]
        clusters = clusters[:max_clusters]
        for c in overflow:
            clusters[0].extend(c)

    return clusters


__all__ = [
    "agglomerative_index_clusters",
    "aligned_fact_refs",
    "cluster_facts",
    "indexed_facts",
    "segment_metadata",
]
