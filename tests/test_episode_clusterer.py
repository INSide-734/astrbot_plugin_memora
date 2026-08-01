"""EpisodeClusterer 的派生候选契约测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.models.memory_evolution import MemorySourceRef
from core.processors.episode_clusterer import EpisodeClusterer

UTC = timezone.utc
NOW = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)


def source(
    memory_id: int,
    *,
    minutes_ago: int,
    topics: tuple[str, ...],
    subject_key: str = "subject:a",
    scope_key: str = "private:scope-a",
    privacy_level: str = "confidential",
    revision: str | None = None,
) -> MemorySourceRef:
    """构造带主题和匿名主体证据的 canonical source。"""

    return MemorySourceRef(
        memory_id=memory_id,
        revision_token=revision or f"r-{memory_id}",
        scope_key=scope_key,
        privacy_level=privacy_level,
        occurred_at=NOW - timedelta(minutes=minutes_ago),
        content=f"证据 {memory_id}",
        topic_keys=topics,
        subject_key=subject_key,
    )


@pytest.mark.asyncio
async def test_disabled_or_single_source_returns_no_candidates() -> None:
    """关闭功能或只有单 source 时不得产生 episode。"""

    disabled = EpisodeClusterer(enabled=False)
    enabled = EpisodeClusterer(enabled=True)

    assert (
        await disabled.cluster_memories([source(1, minutes_ago=1, topics=("咖啡",))])
        == ()
    )
    assert (
        await enabled.cluster_memories([source(1, minutes_ago=1, topics=("咖啡",))])
        == ()
    )


@pytest.mark.asyncio
async def test_same_event_produces_source_backed_candidate_without_mutation() -> None:
    """同 scope、相近时间和重叠主题应生成只读候选证据。"""

    first = source(1, minutes_ago=20, topics=("咖啡", "拿铁"))
    second = source(2, minutes_ago=10, topics=("咖啡", "浓缩"))
    clusterer = EpisodeClusterer(
        time_window_sec=3600,
        topic_overlap_threshold=0.3,
    )

    candidates = await clusterer.cluster_memories([first, second])

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_ids == (1, 2)
    assert candidate.source_revisions == {1: "r-1", 2: "r-2"}
    assert candidate.topic_overlap == pytest.approx(1 / 3)
    assert candidate.window_start == first.occurred_at
    assert candidate.window_end == second.occurred_at
    assert first.revision_token == "r-1"
    assert second.revision_token == "r-2"


@pytest.mark.asyncio
async def test_private_sources_with_different_subjects_do_not_cluster() -> None:
    """同一私聊 scope 内的不同可信主体也不能被聚为同一 episode。"""

    clusterer = EpisodeClusterer(
        time_window_sec=3600,
        topic_overlap_threshold=0.3,
    )
    candidates = await clusterer.cluster_memories(
        [
            source(1, minutes_ago=20, topics=("咖啡",), subject_key="subject:a"),
            source(2, minutes_ago=10, topics=("咖啡",), subject_key="subject:b"),
        ]
    )

    assert candidates == ()


@pytest.mark.asyncio
async def test_scope_time_and_topic_boundaries_remain_negative() -> None:
    """跨 scope、超时窗或主题不相交均不得形成候选。"""

    clusterer = EpisodeClusterer(
        time_window_sec=3600,
        topic_overlap_threshold=0.5,
    )

    assert (
        await clusterer.cluster_memories(
            [
                source(1, minutes_ago=20, topics=("咖啡",), scope_key="private:a"),
                source(2, minutes_ago=10, topics=("咖啡",), scope_key="private:b"),
            ]
        )
        == ()
    )
    assert (
        await clusterer.cluster_memories(
            [
                source(1, minutes_ago=200, topics=("咖啡",)),
                source(2, minutes_ago=10, topics=("咖啡",)),
            ]
        )
        == ()
    )
    assert (
        await clusterer.cluster_memories(
            [
                source(1, minutes_ago=20, topics=("咖啡",)),
                source(2, minutes_ago=10, topics=("徒步",)),
            ]
        )
        == ()
    )


@pytest.mark.asyncio
async def test_rebuild_is_deterministic_and_revision_sensitive() -> None:
    """相同 canonical 快照可重放，source revision 变化会生成新证据键。"""

    clusterer = EpisodeClusterer(
        time_window_sec=3600,
        topic_overlap_threshold=0.3,
    )
    original_sources = [
        source(1, minutes_ago=20, topics=("咖啡", "拿铁")),
        source(2, minutes_ago=10, topics=("咖啡", "浓缩")),
    ]

    first = await clusterer.cluster_memories(original_sources)
    replay = await clusterer.cluster_memories(original_sources)
    revised = await clusterer.cluster_memories(
        [
            original_sources[0],
            source(
                2,
                minutes_ago=10,
                topics=("咖啡", "浓缩"),
                revision="r-2-new",
            ),
        ]
    )

    assert first == replay
    assert first[0].candidate_key != revised[0].candidate_key


@pytest.mark.asyncio
async def test_transitive_cluster_links_the_actual_matching_sources() -> None:
    """传递聚类只能连接实际达到主题阈值的 source 对。"""

    clusterer = EpisodeClusterer(
        time_window_sec=3600,
        topic_overlap_threshold=0.3,
    )
    candidates = await clusterer.cluster_memories(
        [
            source(1, minutes_ago=30, topics=("a", "b")),
            source(2, minutes_ago=20, topics=("b", "c")),
            source(3, minutes_ago=10, topics=("c", "d")),
        ]
    )

    assert [candidate.source_ids for candidate in candidates] == [(1, 2), (2, 3)]
    assert all(candidate.topic_overlap >= 0.3 for candidate in candidates)
