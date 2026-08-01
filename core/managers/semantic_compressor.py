"""把同边界旧 canonical 记忆聚合为可失效的语义摘要 Projection。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from astrbot.api import logger

from ..models.memory_evolution import (
    EvolutionProposal,
    MemoryProjectionProposal,
    MemorySourceRef,
    ProjectionType,
)
from ..models.temporal import normalize_datetime

_DEFAULT_AGE_DAYS = 60.0
_DEFAULT_SIMILARITY = 0.85
_MAX_CONTENT_CHARS = 4_000
_MAX_SUMMARY_CHARS = 600
_MAX_CLUSTER_SOURCES = 16

ProjectionProposalApplier = Callable[
    [EvolutionProposal, list[MemorySourceRef]],
    Awaitable[int],
]


class SemanticCompressor:
    """扫描旧 canonical 来源并提交 source-backed 语义摘要 proposal。"""

    def __init__(
        self,
        *,
        source_store: Any | None = None,
        proposal_applier: ProjectionProposalApplier | None = None,
        enabled: bool = True,
        age_days: float = _DEFAULT_AGE_DAYS,
        similarity_threshold: float = _DEFAULT_SIMILARITY,
    ) -> None:
        """绑定 canonical 读取器、派生写入边界和聚类门槛。

        Args:
            source_store: 提供 ``load_all_sources`` 的只读 canonical Store。
            proposal_applier: 写前二次校验 source revision 的异步回调。
            enabled: 是否允许扫描和生成语义摘要。
            age_days: canonical 写入时间达到该天数后才成为候选。
            similarity_threshold: topic Jaccard 相似度下限。
        """

        self._source_store = source_store
        self._proposal_applier = proposal_applier
        self._enabled = bool(enabled)
        self._age_days = max(30.0, float(age_days))
        self._sim_threshold = max(0.7, min(0.98, float(similarity_threshold)))

    async def compress_old_memories(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """扫描旧来源并把每个合法聚类写成 ``semantic_summary`` Projection。

        Args:
            now: 测试或维护任务提供的 UTC 参考时间；为空时使用当前时间。

        Returns:
            只含候选组、成功写入、失败组和 canonical mutation 数量的安全计数。

        Raises:
            asyncio.CancelledError: 调用方取消扫描或写入时继续传播。
        """

        result = {
            "candidate_groups": 0,
            "projections_applied": 0,
            "failed_groups": 0,
            "canonical_mutations": 0,
        }
        if (
            not self._enabled
            or self._source_store is None
            or self._proposal_applier is None
        ):
            return result

        current = normalize_datetime(now or datetime.now(timezone.utc))
        if current is None:
            current = datetime.now(timezone.utc)
        cutoff = current - timedelta(days=self._age_days)
        try:
            loaded = await self._source_store.load_all_sources(
                max_content_chars=_MAX_CONTENT_CHARS,
            )
            sources = [
                source
                for source in loaded
                if isinstance(source, MemorySourceRef)
                and _eligible_source(source, cutoff)
            ]
            for partition in _partition_sources(sources).values():
                for cluster in _cluster_sources(
                    partition,
                    threshold=self._sim_threshold,
                ):
                    result["candidate_groups"] += 1
                    proposal = _proposal_for_cluster(cluster)
                    if proposal is None:
                        result["failed_groups"] += 1
                        continue
                    try:
                        applied = await self._proposal_applier(proposal, cluster)
                        result["projections_applied"] += max(0, int(applied))
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        result["failed_groups"] += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[语义压缩] 扫描失败，已保留 canonical")
            result["failed_groups"] += 1
        return result

    async def rebuild_from_canonical(self) -> dict[str, int | bool | str]:
        """使用当前 canonical revision 幂等重建语义摘要 Projection。"""

        result = await self.compress_old_memories()
        return {
            "success": result["failed_groups"] == 0,
            "reason_code": (
                "semantic_compression_rebuilt"
                if result["failed_groups"] == 0
                else "semantic_compression_rebuild_degraded"
            ),
            **result,
        }


def _eligible_source(source: MemorySourceRef, cutoff: datetime) -> bool:
    """判断来源是否达到年龄、内容、topic 和 role 最小门槛。"""

    reference = source.ingested_at or source.occurred_at
    return bool(
        reference is not None
        and reference <= cutoff
        and source.content
        and source.content.strip()
        and source.topic_keys
        and source.source_role in {"primary", "supporting"}
    )


def _partition_sources(
    sources: list[MemorySourceRef],
) -> dict[tuple[str, str, str], list[MemorySourceRef]]:
    """按完全相同的 scope、privacy 和 role 隔离压缩候选。"""

    partitions: dict[tuple[str, str, str], list[MemorySourceRef]] = {}
    for source in sorted(sources, key=lambda item: item.memory_id):
        key = (source.scope_key, source.privacy_level, source.source_role)
        partitions.setdefault(key, []).append(source)
    return partitions


def _cluster_sources(
    sources: list[MemorySourceRef],
    *,
    threshold: float,
) -> list[list[MemorySourceRef]]:
    """按 anchor topic Jaccard 相似度生成稳定且有界的来源聚类。"""

    clusters: list[list[MemorySourceRef]] = []
    used: set[int] = set()
    ordered = sorted(sources, key=lambda item: item.memory_id)
    for index, source in enumerate(ordered):
        if source.memory_id in used:
            continue
        anchor_topics = _normalized_topics(source)
        if not anchor_topics:
            continue
        cluster = [source]
        for other in ordered[index + 1 :]:
            if other.memory_id in used or len(cluster) >= _MAX_CLUSTER_SOURCES:
                continue
            if _topic_similarity(anchor_topics, _normalized_topics(other)) >= threshold:
                cluster.append(other)
        if len(cluster) < 2:
            continue
        used.update(item.memory_id for item in cluster)
        clusters.append(cluster)
    return clusters


def _proposal_for_cluster(
    cluster: list[MemorySourceRef],
) -> EvolutionProposal | None:
    """把一个同边界聚类转换为确定性的语义摘要 proposal。"""

    summary = _synthesize_abstract([str(source.content or "") for source in cluster])
    if not summary:
        return None
    anchor_topics = _normalized_topics(cluster[0])
    confidence = min(
        _topic_similarity(anchor_topics, _normalized_topics(source))
        for source in cluster[1:]
    )
    aliases = tuple(f"M{index}" for index in range(1, len(cluster) + 1))
    return EvolutionProposal(
        projections=(
            MemoryProjectionProposal(
                projection_type=ProjectionType.SEMANTIC_SUMMARY,
                source_aliases=aliases,
                title="语义摘要",
                summary=summary,
                confidence=confidence,
                valid_from=None,
                valid_to=None,
            ),
        )
    )


def _normalized_topics(source: MemorySourceRef) -> frozenset[str]:
    """返回用于 Jaccard 计算的大小写无关 topic 集合。"""

    return frozenset(
        topic.strip().casefold()
        for topic in source.topic_keys
        if isinstance(topic, str) and topic.strip()
    )


def _topic_similarity(first: frozenset[str], second: frozenset[str]) -> float:
    """计算两个 topic 集合的 Jaccard 相似度。"""

    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def _synthesize_abstract(contents: list[str]) -> str:
    """从有限来源正文生成确定性、长度受限的摘要。"""

    normalized = [content.strip().rstrip("。！？.!?") for content in contents]
    normalized = [content for content in normalized if content]
    if not normalized:
        return ""
    base = normalized[0]
    supplements = [
        content for content in normalized[1:3] if len(content) < max(1, len(base) * 0.3)
    ]
    summary = base + ("。" + "；".join(supplements) if supplements else "") + "。"
    return summary[:_MAX_SUMMARY_CHARS].rstrip("；")


__all__ = ["SemanticCompressor"]
