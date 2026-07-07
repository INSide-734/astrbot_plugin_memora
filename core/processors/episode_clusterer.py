"""情景聚类 — 时间窗口 + 主题重叠的 DBSCAN 风格聚类。

将同一事件的多条碎片化记忆自动聚合为 episode，分配 episode_id。
对距离当前时间 > 30 天的旧记忆不分配 episode_id（冷凝成本限制）。
"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger


class EpisodeClusterer:
    """时间窗口 + 主题 Jaccard 的轻量情景聚类器。

    不依赖 DBSCAN/FAISS embedding，使用主题标签和创建时间做聚类。
    适用于 SQLite 已有数据，无需外部库。
    """

    # 默认时间窗口（秒）：24 小时内的事件可能是同一 episode
    DEFAULT_TIME_WINDOW_SEC = 86400  # 24 hours

    # 主题 Jaccard 重叠阈值：高于此值视为同一 episode
    TOPIC_OVERLAP_THRESHOLD = 0.50

    def __init__(
        self,
        time_window_sec: float = DEFAULT_TIME_WINDOW_SEC,
        topic_overlap_threshold: float = TOPIC_OVERLAP_THRESHOLD,
        enabled: bool = True,
    ) -> None:
        self._time_window = time_window_sec
        self._topic_threshold = topic_overlap_threshold
        self._enabled = enabled
        self._episode_counter: int = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    async def cluster_memories(
        self,
        memories: list[dict[str, Any]],
        update_metadata_fn: Any | None = None,
    ) -> dict[int, str]:
        """对记忆列表做情景聚类，返回 {memory_id: episode_id} 映射。

        Args:
            memories: 记忆列表，每条含 {"id": int, "metadata": dict}
            update_metadata_fn: async (memory_id, {"metadata": ...}) -> bool

        Returns:
            {memory_id: episode_id} 映射
        """
        if not self._enabled or len(memories) < 2:
            return {}

        # 只处理最近的记忆（> 30 天的不聚类，减少计算）
        now = time.time()
        cutoff_30d = now - 30 * 86400.0
        recent = [m for m in memories if _get_create_time(m) >= cutoff_30d]

        if len(recent) < 2:
            return {}

        # 按创建时间排序
        recent.sort(key=_get_create_time)

        # 贪心聚类：时间窗口内 + 主题重叠 > 阈值 → 同一 episode
        clusters: list[list[int]] = []
        for mem in recent:
            mem_id = int(mem.get("id") or mem.get("doc_id") or 0)
            if mem_id <= 0:
                continue

            assigned = False
            for cluster in clusters:
                # 检查是否与 cluster 中任一记忆足够相似
                if self._can_join_cluster(mem, cluster, recent):
                    cluster.append(mem_id)
                    assigned = True
                    break

            if not assigned:
                clusters.append([mem_id])

        # 分配 episode_id
        episode_map: dict[int, str] = {}
        for i, cluster in enumerate(clusters, start=1):
            if len(cluster) < 2:
                continue  # 单条记忆不分配 episode（无聚类意义）
            episode_id = f"ep_{_now_timestamp()}_{i:04d}"
            for mem_id in cluster:
                episode_map[mem_id] = episode_id

        # 持久化 episode_id
        if update_metadata_fn is not None and episode_map:
            for mem_id, ep_id in episode_map.items():
                try:
                    await update_metadata_fn(
                        mem_id,
                        {"metadata": {"episode_id": ep_id}},
                    )
                except Exception:
                    logger.debug(
                        f"[EpisodeClusterer] 更新 episode_id 失败: id={mem_id}",
                        exc_info=True,
                    )

        logger.info(
            f"[EpisodeClusterer] 聚类完成: {len(recent)} 条记忆 → "
            f"{len([c for c in clusters if len(c) >= 2])} 个 episode"
        )
        return episode_map

    def _can_join_cluster(
        self,
        mem: dict[str, Any],
        cluster: list[int],
        all_memories: list[dict[str, Any]],
    ) -> bool:
        """判断记忆是否可以加入已有 cluster。"""
        mem_time = _get_create_time(mem)
        mem_topics = _get_topics(mem)

        id_to_mem = {int(m.get("id") or m.get("doc_id") or 0): m for m in all_memories}

        for cluster_mem_id in cluster:
            cluster_mem = id_to_mem.get(cluster_mem_id)
            if cluster_mem is None:
                continue

            cluster_time = _get_create_time(cluster_mem)
            # 时间窗口检查
            if abs(mem_time - cluster_time) > self._time_window:
                continue

            # 主题重叠检查
            cluster_topics = _get_topics(cluster_mem)
            overlap = _topic_jaccard(set(mem_topics), set(cluster_topics))
            if overlap >= self._topic_threshold:
                return True

        return False


def _get_create_time(mem: dict[str, Any]) -> float:
    """从记忆记录中提取创建时间（秒级时间戳）。"""
    meta = mem.get("metadata", {}) or {}
    if isinstance(meta, str):
        import json

        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    ts = meta.get("create_time") or mem.get("create_time") or meta.get("timestamp") or 0
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


def _get_topics(mem: dict[str, Any]) -> list[str]:
    """从记忆记录中提取主题标签。"""
    meta = mem.get("metadata", {}) or {}
    if isinstance(meta, str):
        import json

        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    topics = meta.get("topics", []) or []
    return [str(t) for t in topics if t]


def _topic_jaccard(topics_a: set[str], topics_b: set[str]) -> float:
    """主题集合的 Jaccard 重叠度。"""
    if not topics_a or not topics_b:
        return 0.0
    return len(topics_a & topics_b) / max(1, len(topics_a | topics_b))


def _now_timestamp() -> int:
    """当前时间戳（整数秒）。"""
    return int(time.time())


__all__ = ["EpisodeClusterer"]
