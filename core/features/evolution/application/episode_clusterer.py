"""基于时间窗口和主题重叠生成 episode 派生候选。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from ....shared.contracts import MemorySourceRef


@dataclass(frozen=True, slots=True)
class EpisodeCandidate:
    """保存两条 canonical source 形成 episode 的只读证据。"""

    source_ids: tuple[int, int]
    revision_tokens: tuple[str, str]
    scope_key: str
    privacy_level: str
    subject_key: str | None
    topic_overlap: float
    confidence: float
    window_start: datetime
    window_end: datetime

    @property
    def source_revisions(self) -> dict[int, str]:
        """返回按 canonical ID 索引的 revision 快照。"""

        return dict(zip(self.source_ids, self.revision_tokens, strict=True))

    @property
    def candidate_key(self) -> str:
        """返回同时绑定 ID 和 revision 的稳定候选键。"""

        payload = "|".join(
            (
                str(self.source_ids[0]),
                self.revision_tokens[0],
                str(self.source_ids[1]),
                self.revision_tokens[1],
                self.scope_key,
            )
        )
        return f"episode:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


class EpisodeClusterer:
    """把同 scope 的近期 source 聚成不修改 canonical 的 episode 候选。"""

    DEFAULT_TIME_WINDOW_SEC = 86_400
    TOPIC_OVERLAP_THRESHOLD = 0.5
    MAX_SOURCE_AGE_DAYS = 30

    def __init__(
        self,
        time_window_sec: float = DEFAULT_TIME_WINDOW_SEC,
        topic_overlap_threshold: float = TOPIC_OVERLAP_THRESHOLD,
        enabled: bool = True,
    ) -> None:
        """保存确定性候选阈值，不创建数据库或后台任务。"""

        self._time_window = max(0.0, float(time_window_sec))
        self._topic_threshold = min(1.0, max(0.0, float(topic_overlap_threshold)))
        self._enabled = bool(enabled)

    @property
    def enabled(self) -> bool:
        """返回当前是否允许生成 episode 候选。"""

        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """切换候选生成开关；已有派生对象由读取门控制。"""

        self._enabled = bool(value)

    async def cluster_memories(
        self,
        memories: Sequence[MemorySourceRef],
    ) -> tuple[EpisodeCandidate, ...]:
        """从 canonical 快照生成星形 episode 候选，不执行任何写操作。"""

        if not self._enabled or len(memories) < 2:
            return ()
        ordered = sorted(memories, key=lambda item: (item.occurred_at, item.memory_id))
        reference_time = ordered[-1].occurred_at
        cutoff = reference_time - timedelta(days=self.MAX_SOURCE_AGE_DAYS)
        recent = [
            source
            for source in ordered
            if source.occurred_at >= cutoff and source.topic_keys
        ]
        if len(recent) < 2:
            return ()

        accepted: list[MemorySourceRef] = []
        candidates: list[EpisodeCandidate] = []
        for source in recent:
            for member in reversed(accepted):
                if self._can_join(source, member):
                    overlap = _topic_jaccard(source.topic_keys, member.topic_keys)
                    candidates.append(_candidate(member, source, overlap))
                    break
            accepted.append(source)
        return tuple(candidates)

    def _can_join(self, first: MemorySourceRef, second: MemorySourceRef) -> bool:
        """检查 scope、私聊主体、时间和主题四项硬边界。"""

        if first.scope_key != second.scope_key:
            return False
        if "confidential" in {first.privacy_level, second.privacy_level} and (
            not first.subject_key or first.subject_key != second.subject_key
        ):
            return False
        if (
            abs((first.occurred_at - second.occurred_at).total_seconds())
            > self._time_window
        ):
            return False
        return (
            _topic_jaccard(first.topic_keys, second.topic_keys) >= self._topic_threshold
        )


def _candidate(
    first: MemorySourceRef,
    second: MemorySourceRef,
    overlap: float,
) -> EpisodeCandidate:
    """把通过预筛的 source 对转换为不可变候选。"""

    privacy = (
        "confidential"
        if "confidential" in {first.privacy_level, second.privacy_level}
        else "shared"
        if "shared" in {first.privacy_level, second.privacy_level}
        else "public"
    )
    return EpisodeCandidate(
        source_ids=(first.memory_id, second.memory_id),
        revision_tokens=(first.revision_token, second.revision_token),
        scope_key=first.scope_key,
        privacy_level=privacy,
        subject_key=first.subject_key
        if first.subject_key == second.subject_key
        else None,
        topic_overlap=overlap,
        confidence=min(1.0, 0.5 + overlap / 2.0),
        window_start=min(first.occurred_at, second.occurred_at),
        window_end=max(first.occurred_at, second.occurred_at),
    )


def _topic_jaccard(first: Sequence[str], second: Sequence[str]) -> float:
    """计算两个非可信话题序列的 Jaccard 重叠。"""

    left = set(first)
    right = set(second)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


__all__ = ["EpisodeCandidate", "EpisodeClusterer"]
