"""episode_clusterer.py 测试 — EpisodeClusterer。"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from core.processors.episode_clusterer import EpisodeClusterer


class TestEpisodeClusterer:
    @pytest.fixture
    def now(self) -> float:
        return time.time()

    @pytest.fixture
    def recent_memories(self, now: float) -> list[dict]:
        return [
            {
                "id": 1,
                "metadata": {
                    "create_time": now - 3600,
                    "topics": ["coffee", "cafe"],
                },
            },
            {
                "id": 2,
                "metadata": {
                    "create_time": now - 3600,
                    "topics": ["coffee", "espresso"],
                },
            },
            {
                "id": 3,
                "metadata": {
                    "create_time": now - 7200,
                    "topics": ["hiking", "mountain"],
                },
            },
            {
                "id": 4,
                "metadata": {
                    "create_time": now - 3600,
                    "topics": ["hiking", "trail"],
                },
            },
        ]

    def test_disabled_returns_empty(self) -> None:
        clusterer = EpisodeClusterer(enabled=False)
        result = asyncio.run(clusterer.cluster_memories([]))
        assert result == {}

    def test_single_memory_returns_empty(self, now: float) -> None:
        clusterer = EpisodeClusterer()
        memories: list[dict] = [
            {"id": 1, "metadata": {"create_time": now, "topics": ["test"]}}
        ]
        result = asyncio.run(clusterer.cluster_memories(memories))
        assert result == {}

    def test_empty_list_returns_empty(self) -> None:
        clusterer = EpisodeClusterer()
        result = asyncio.run(clusterer.cluster_memories([]))
        assert result == {}

    def test_clusters_by_topic_overlap(self, now: float) -> None:
        clusterer = EpisodeClusterer(time_window_sec=86400, topic_overlap_threshold=0.3)
        memories: list[dict] = [
            {
                "id": 1,
                "metadata": {
                    "create_time": now - 100,
                    "topics": ["coffee", "espresso"],
                },
            },
            {
                "id": 2,
                "metadata": {"create_time": now - 200, "topics": ["coffee", "latte"]},
            },
        ]
        result = asyncio.run(clusterer.cluster_memories(memories))
        assert len(result) > 0

    def test_no_cluster_when_topics_disjoint(self, now: float) -> None:
        clusterer = EpisodeClusterer(time_window_sec=86400, topic_overlap_threshold=0.5)
        memories: list[dict] = [
            {"id": 1, "metadata": {"create_time": now - 100, "topics": ["coffee"]}},
            {"id": 2, "metadata": {"create_time": now - 200, "topics": ["hiking"]}},
        ]
        result = asyncio.run(clusterer.cluster_memories(memories))
        assert result == {}

    def test_no_cluster_when_time_too_far(self, now: float) -> None:
        clusterer = EpisodeClusterer(time_window_sec=60, topic_overlap_threshold=0.1)
        memories: list[dict] = [
            {"id": 1, "metadata": {"create_time": now - 10000, "topics": ["coffee"]}},
            {"id": 2, "metadata": {"create_time": now - 20000, "topics": ["coffee"]}},
        ]
        result = asyncio.run(clusterer.cluster_memories(memories))
        assert result == {}

    def test_old_memories_excluded(self, now: float) -> None:
        clusterer = EpisodeClusterer()
        memories: list[dict] = [
            {"id": 1, "metadata": {"create_time": now - 100, "topics": ["topic"]}},
            {
                "id": 2,
                "metadata": {"create_time": now - 40 * 86400, "topics": ["topic"]},
            },
        ]
        result = asyncio.run(clusterer.cluster_memories(memories))
        # Only memory 1 is in the 30-day window; single memories don't get episodes
        assert result == {}

    def test_update_metadata_called(self, now: float) -> None:
        update_fn = AsyncMock(return_value=True)
        clusterer = EpisodeClusterer(time_window_sec=86400, topic_overlap_threshold=0.5)
        memories: list[dict] = [
            {"id": 1, "metadata": {"create_time": now - 100, "topics": ["coffee"]}},
            {"id": 2, "metadata": {"create_time": now - 200, "topics": ["coffee"]}},
        ]
        result = asyncio.run(clusterer.cluster_memories(memories, update_fn))
        assert len(result) > 0
        assert update_fn.called

    def test_update_exception_suppressed(self, now: float) -> None:
        update_fn = AsyncMock(side_effect=RuntimeError("update failed"))
        clusterer = EpisodeClusterer(time_window_sec=86400, topic_overlap_threshold=0.5)
        memories: list[dict] = [
            {"id": 1, "metadata": {"create_time": now - 100, "topics": ["coffee"]}},
            {"id": 2, "metadata": {"create_time": now - 200, "topics": ["coffee"]}},
        ]
        result = asyncio.run(clusterer.cluster_memories(memories, update_fn))
        assert len(result) > 0  # Clusters still assigned, update failures logged

    def test_enabled_setter(self) -> None:
        clusterer = EpisodeClusterer(enabled=True)
        clusterer.enabled = False
        assert clusterer.enabled is False

    def test_memory_with_string_metadata(self, now: float) -> None:
        import json

        clusterer = EpisodeClusterer()
        metadata_str = json.dumps({"create_time": now - 100, "topics": ["coffee"]})
        memories: list[dict] = [
            {"id": 1, "metadata": metadata_str},
            {"id": 2, "metadata": metadata_str},
        ]
        result = asyncio.run(clusterer.cluster_memories(memories))
        assert len(result) > 0

    def test_zero_id_memory_skipped(self, now: float) -> None:
        clusterer = EpisodeClusterer()
        memories: list[dict] = [
            {"id": 0, "metadata": {"create_time": now - 100, "topics": ["coffee"]}},
            {"id": 2, "metadata": {"create_time": now - 200, "topics": ["coffee"]}},
        ]
        result = asyncio.run(clusterer.cluster_memories(memories))
        # Only one valid ID -> can't form a cluster of 2+
        assert result == {}
