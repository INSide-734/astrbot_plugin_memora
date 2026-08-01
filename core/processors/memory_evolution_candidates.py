"""把确定性 episode/conflict 候选转换为 EvolutionProposal。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..models.memory_evolution import (
    EvolutionProposal,
    MemoryRelationProposal,
    MemorySourceRef,
    RelationType,
)
from .contradiction_detector import ContradictionDetector
from .episode_clusterer import EpisodeClusterer


class MemoryEvolutionCandidateGenerator:
    """组合本地确定性候选，不执行 Provider 或持久化调用。"""

    def __init__(
        self,
        *,
        episode_config: Mapping[str, Any] | None = None,
        contradiction_config: Mapping[str, Any] | None = None,
    ) -> None:
        """按正式 episode 配置和内部冲突阈值构造两个纯处理器。"""

        episode_config = episode_config or {}
        contradiction_config = contradiction_config or {}
        self._episode = EpisodeClusterer(
            enabled=bool(episode_config.get("enabled", True)),
            time_window_sec=_as_float(episode_config.get("time_window_hours"), 24.0)
            * 3600.0,
            topic_overlap_threshold=_as_float(
                episode_config.get("topic_overlap_threshold"), 0.5
            ),
        )
        self._contradiction = ContradictionDetector(
            enabled=bool(contradiction_config.get("enabled", True)),
            max_conflicts=_as_int(contradiction_config.get("max_conflicts"), 5),
            jaccard_threshold=_as_float(
                contradiction_config.get("jaccard_threshold"), 0.4
            ),
        )

    async def propose(
        self,
        sources: Sequence[MemorySourceRef],
        *,
        limit: int,
    ) -> EvolutionProposal:
        """优先生成冲突/更新，再补不与其重叠的 episode relation。"""

        aliases = {
            source.memory_id: f"M{index}"
            for index, source in enumerate(sources, start=1)
        }
        conflicts = self._contradiction.detect_candidates(sources)
        relations: list[MemoryRelationProposal] = []
        occupied_pairs: set[frozenset[int]] = set()
        for candidate in conflicts:
            if candidate.source_id not in aliases or candidate.target_id not in aliases:
                continue
            relation_type = (
                RelationType.UPDATES
                if candidate.conflict_type == "temporal_update"
                else RelationType.CONTRADICTS
            )
            relations.append(
                MemoryRelationProposal(
                    aliases[candidate.source_id],
                    aliases[candidate.target_id],
                    relation_type,
                    candidate.confidence,
                    candidate.conflict_type,
                    min(
                        candidate.source_occurred_at,
                        candidate.target_occurred_at,
                    ),
                    max(
                        candidate.source_occurred_at,
                        candidate.target_occurred_at,
                    ),
                )
            )
            occupied_pairs.add(frozenset((candidate.source_id, candidate.target_id)))

        episodes = await self._episode.cluster_memories(sources)
        for candidate in episodes:
            pair = frozenset(candidate.source_ids)
            if pair in occupied_pairs or any(item not in aliases for item in pair):
                continue
            relations.append(
                MemoryRelationProposal(
                    aliases[candidate.source_ids[0]],
                    aliases[candidate.source_ids[1]],
                    RelationType.SAME_EPISODE,
                    candidate.confidence,
                    "topic_time_overlap",
                    candidate.window_start,
                    candidate.window_end,
                )
            )
            if len(relations) >= max(0, limit):
                break
        return EvolutionProposal(relations=tuple(relations[: max(0, limit)]))


def _as_int(value: Any, default: int) -> int:
    """把配置值转为整数，非法值使用默认值。"""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    """把配置值转为浮点数，非法值使用默认值。"""

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


__all__ = ["MemoryEvolutionCandidateGenerator"]
