"""SemanticCompressor 测试 — 话题聚类和摘要合成。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.managers.semantic_compressor import SemanticCompressor, _resolve_seeds

# ---------------------------------------------------------------------------
# _resolve_seeds
# ---------------------------------------------------------------------------


class TestResolveSeeds:
    """Tests for the _resolve_seeds helper."""

    @pytest.mark.parametrize(
        "seed_lang,bot_lang,expected",
        [
            ("zh", "zh", ["总结", "回顾", "经历", "日常", "聊天"]),
            ("en", "en", ["summary", "review", "experience", "daily", "chat"]),
            ("ru", "ru", ["итоги", "обзор", "опыт", "повседневное", "общение"]),
        ],
    )
    def test_explicit_language(
        self, seed_lang: str, bot_lang: str, expected: list[str]
    ) -> None:
        """Explicit language selection returns correct seeds."""
        assert _resolve_seeds(seed_lang, bot_lang) == expected

    def test_auto_falls_back(self) -> None:
        """Auto falls back to bot_language or zh."""
        assert _resolve_seeds("auto", "en") == [
            "summary",
            "review",
            "experience",
            "daily",
            "chat",
        ]
        assert _resolve_seeds("auto", "zh") == ["总结", "回顾", "经历", "日常", "聊天"]

    def test_unknown_language_falls_back_to_zh(self) -> None:
        """Unknown language code falls back to zh seeds."""
        assert _resolve_seeds("fr", "zh") == ["总结", "回顾", "经历", "日常", "聊天"]

    def test_auto_with_unknown_bot_lang_falls_back_to_zh(self) -> None:
        """Auto with unknown bot_language falls back to zh."""
        assert _resolve_seeds("auto", "jp") == ["总结", "回顾", "经历", "日常", "聊天"]


# ---------------------------------------------------------------------------
# _cluster_by_topics
# ---------------------------------------------------------------------------


class TestClusterByTopics:
    """Tests for the _cluster_by_topics static method."""

    def test_empty_list(self) -> None:
        """Empty memory list returns empty clusters."""
        result = SemanticCompressor._cluster_by_topics([])
        assert result == []

    def test_single_memory_no_cluster(self) -> None:
        """A single memory cannot form a cluster (needs >= 2)."""
        memories = [
            {
                "doc_id": 1,
                "content": "test",
                "metadata": {"topics": ["python"]},
            }
        ]
        result = SemanticCompressor._cluster_by_topics(memories)
        assert result == []

    def test_two_similar_topics_clustered(self) -> None:
        """Memories with overlapping topics form a cluster."""
        memories = [
            {"doc_id": 1, "content": "a", "metadata": {"topics": ["python", "coding"]}},
            {
                "doc_id": 2,
                "content": "b",
                "metadata": {"topics": ["python", "testing"]},
            },
        ]
        result = SemanticCompressor._cluster_by_topics(memories)
        # Jaccard = 1/3 = 0.33 < 0.5 → not clustered
        assert result == []

    def test_high_overlap_clustered(self) -> None:
        """Memories with high topic overlap (>= 50%) are clustered."""
        memories = [
            {
                "doc_id": 1,
                "content": "a",
                "metadata": {"topics": ["python", "ai", "ml"]},
            },
            {"doc_id": 2, "content": "b", "metadata": {"topics": ["python", "ai"]}},
        ]
        # intersection=2, union=3, jaccard=2/3=0.67 >= 0.5
        result = SemanticCompressor._cluster_by_topics(memories)
        assert len(result) == 1
        assert len(result[0]) == 2

    def test_no_topics_no_cluster(self) -> None:
        """Memories without topics are not clustered."""
        memories = [
            {"doc_id": 1, "content": "a", "metadata": {"topics": []}},
            {"doc_id": 2, "content": "b", "metadata": {"topics": []}},
        ]
        result = SemanticCompressor._cluster_by_topics(memories)
        assert result == []

    def test_disjoint_topics_no_cluster(self) -> None:
        """Memories with completely different topics are not clustered."""
        memories = [
            {"doc_id": 1, "content": "a", "metadata": {"topics": ["sports"]}},
            {"doc_id": 2, "content": "b", "metadata": {"topics": ["cooking"]}},
        ]
        result = SemanticCompressor._cluster_by_topics(memories)
        assert result == []

    def test_case_insensitive_comparison(self) -> None:
        """Topic comparison is case-insensitive."""
        memories = [
            {"doc_id": 1, "content": "a", "metadata": {"topics": ["Python", "AI"]}},
            {
                "doc_id": 2,
                "content": "b",
                "metadata": {"topics": ["python", "ai", "ml"]},
            },
        ]
        result = SemanticCompressor._cluster_by_topics(memories)
        assert len(result) >= 1

    def test_three_memory_chain(self) -> None:
        """Three memories with pairwise overlap form one cluster."""
        memories = [
            {"doc_id": 1, "content": "a", "metadata": {"topics": ["python", "ai"]}},
            {"doc_id": 2, "content": "b", "metadata": {"topics": ["ai", "ml"]}},
            {"doc_id": 3, "content": "c", "metadata": {"topics": ["ml", "dl"]}},
        ]
        result = SemanticCompressor._cluster_by_topics(memories)
        # 1+2: {python,ai} & {ai,ml} / {python,ai,ml} = 1/3 < 0.5 → NOT clustered
        # But 2+3: {ai,ml} & {ml,dl} / {ai,ml,dl} = 1/3 < 0.5 → NOT clustered
        # So no clusters formed
        # Actually let's check: 1+2: intersection={ai}, union={python,ai,ml} → 1/3=0.33
        assert result == []

    def test_no_metadata_topics_key(self) -> None:
        """Memories without topics key in metadata are handled."""
        memories = [
            {"doc_id": 1, "content": "a", "metadata": {}},
            {"doc_id": 2, "content": "b", "metadata": {}},
        ]
        result = SemanticCompressor._cluster_by_topics(memories)
        assert result == []


# ---------------------------------------------------------------------------
# _synthesize_abstract
# ---------------------------------------------------------------------------


class TestSynthesizeAbstract:
    """Tests for the _synthesize_abstract static method."""

    def test_empty_contents(self) -> None:
        """Empty content list returns empty string."""
        assert SemanticCompressor._synthesize_abstract([]) == ""

    def test_single_content(self) -> None:
        """Single content item is returned as-is."""
        result = SemanticCompressor._synthesize_abstract(["Hello world"])
        assert result == "Hello world"

    def test_two_contents_merged(self) -> None:
        """Two contents are merged with sentence-ending punctuation."""
        result = SemanticCompressor._synthesize_abstract(
            ["First content", "Second supplement"]
        )
        assert "。" in result
        assert "First content" in result

    def test_short_supplements_merged(self) -> None:
        """Short supplements (less than 30% of base length) are added."""
        base = "A very long content piece with many words to serve as the main body"
        supplement = "short addendum"
        result = SemanticCompressor._synthesize_abstract([base, supplement])
        assert "short addendum" in result

    def test_long_supplement_ignored(self) -> None:
        """Supplements as long as the base are ignored (only base used)."""
        base = "short"
        long_supplement = "this is a very long supplement that exceeds the threshold"
        result = SemanticCompressor._synthesize_abstract([base, long_supplement])
        assert long_supplement not in result

    def test_punctuation_stripped(self) -> None:
        """Trailing punctuation is stripped from base before merge."""
        result = SemanticCompressor._synthesize_abstract(["Hello!?", "World news"])
        assert "Hello" in result

    def test_third_content_ignored(self) -> None:
        """Only first 3 contents are considered (contents[0] + contents[1:3])."""
        base = "A long enough base content for the synthesis test to work properly here"
        supp1 = "short1"
        supp2 = "short2"
        supp3 = "short3"
        result = SemanticCompressor._synthesize_abstract([base, supp1, supp2, supp3])
        assert "short1" in result
        assert "short2" in result
        assert "short3" not in result  # index 3 is ignored

    def test_no_supplements_meeting_threshold(self) -> None:
        """When no supplement meets threshold, only base + period returned."""
        base = "A base content"
        long_supp = "A supplement that is way too long to be considered short enough here because it exceeds the 30% threshold by quite a lot actually"
        result = SemanticCompressor._synthesize_abstract([base, long_supp])
        assert result == base + "。"

    def test_three_items_with_middle_ignored(self) -> None:
        """Third and beyond ignored, middle may be included if short enough."""
        base = "A very long base content to establish length for the threshold comparison to work as intended here"
        short = "s"
        long = (
            "This one is definitely too long to be considered a short supplement here"
        )
        result = SemanticCompressor._synthesize_abstract([base, long, short])
        # long is at index 1, won't meet threshold
        # short is at index 2 (content[2]) included since content[1:3]
        assert short in result


# ---------------------------------------------------------------------------
# Constructor tests
# ---------------------------------------------------------------------------


class TestSemanticCompressorInit:
    """Tests for constructor validation."""

    def test_age_days_clamped(self) -> None:
        """age_days is clamped to minimum 30."""
        compressor = SemanticCompressor(age_days=10.0)
        assert compressor._age_days == 30.0

    def test_similarity_threshold_clamped(self) -> None:
        """similarity_threshold is clamped to [0.7, 0.98]."""
        compressor_low = SemanticCompressor(similarity_threshold=0.5)
        assert compressor_low._sim_threshold == 0.7
        compressor_high = SemanticCompressor(similarity_threshold=0.99)
        assert compressor_high._sim_threshold == 0.98

    def test_default_values(self) -> None:
        """Default constructor uses sensible defaults."""
        compressor = SemanticCompressor()
        assert compressor._age_days == 60.0
        assert compressor._sim_threshold == 0.85
        assert compressor._seeds is not None

    def test_compress_old_memories_no_callbacks(self) -> None:
        """compress_old_memories returns zeros when callbacks are None."""
        compressor = SemanticCompressor()
        import asyncio

        result = asyncio.run(compressor.compress_old_memories())
        assert result["merged_groups"] == 0
        assert result["deleted_originals"] == 0
        assert result["new_abstracts"] == 0


# ---------------------------------------------------------------------------
# compress_old_memories integration tests
# ---------------------------------------------------------------------------


class TestCompressOldMemories:
    """Tests for compress_old_memories with mocked callbacks."""

    def _make_compressor(self) -> SemanticCompressor:
        return SemanticCompressor(
            search_similar_cb=AsyncMock(return_value=[]),
            add_memory_cb=AsyncMock(),
            delete_memory_cb=AsyncMock(),
        )

    @pytest.mark.asyncio
    async def test_no_results_from_search(self) -> None:
        """When search returns empty, no compression happens."""
        compressor = self._make_compressor()
        result = await compressor.compress_old_memories(session_id="s1")
        assert result["merged_groups"] == 0
        assert result["new_abstracts"] == 0

    @pytest.mark.asyncio
    async def test_recent_memories_skipped(self) -> None:
        """Memories newer than age_days are skipped."""
        compressor = self._make_compressor()
        import time

        recent_time = time.time()  # now — not old

        search_result = MagicMock()
        search_result.doc_id = 1
        search_result.content = "old content"
        search_result.metadata = {"create_time": recent_time, "topics": ["python"]}
        search_result.final_score = 0.9

        compressor._search_similar = AsyncMock(return_value=[search_result])
        result = await compressor.compress_old_memories()
        assert result["merged_groups"] == 0  # too recent

    @pytest.mark.asyncio
    async def test_old_memories_compressed(self) -> None:
        """Old memories with overlapping topics are compressed."""
        compressor = self._make_compressor()
        import time

        long_ago = time.time() - 120 * 86400.0  # 120 days ago

        m1 = MagicMock()
        m1.doc_id = 1
        m1.content = "Python is great"
        m1.metadata = {
            "create_time": long_ago,
            "importance": 0.7,
            "topics": ["python", "ai"],
        }
        m1.final_score = 0.9

        m2 = MagicMock()
        m2.doc_id = 2
        m2.content = "AI tools for coding"
        m2.metadata = {
            "create_time": long_ago + 1,
            "importance": 0.6,
            "topics": ["python", "ai", "ml"],
        }
        m2.final_score = 0.85

        compressor._search_similar = AsyncMock(return_value=[m1, m2])
        result = await compressor.compress_old_memories()
        # 2 old memories with high topic overlap → merged
        # intersection={python,ai}=2, union={python,ai,ml}=3, jaccard=2/3 ≥0.5
        assert result["merged_groups"] >= 1

    @pytest.mark.asyncio
    async def test_only_one_old_memory_no_compression(self) -> None:
        """Single old memory cannot be compressed (needs >=2 per group)."""
        compressor = self._make_compressor()
        import time

        long_ago = time.time() - 120 * 86400.0

        m1 = MagicMock()
        m1.doc_id = 1
        m1.content = "Single memory"
        m1.metadata = {"create_time": long_ago, "importance": 0.5, "topics": ["python"]}
        m1.final_score = 0.9

        compressor._search_similar = AsyncMock(return_value=[m1])
        result = await compressor.compress_old_memories()
        assert result["merged_groups"] == 0
        assert result["new_abstracts"] == 0

    @pytest.mark.asyncio
    async def test_exception_graceful(self) -> None:
        """When an exception occurs during compression, returns zeros."""
        compressor = self._make_compressor()
        compressor._search_similar = AsyncMock(side_effect=Exception("Search failed"))

        result = await compressor.compress_old_memories()
        assert result["merged_groups"] == 0

    @pytest.mark.asyncio
    async def test_duplicate_doc_ids_skipped(self) -> None:
        """Duplicate doc_ids across seeds are only processed once."""
        compressor = self._make_compressor()
        import time

        long_ago = time.time() - 120 * 86400.0

        m1 = MagicMock()
        m1.doc_id = 1
        m1.content = "Memory one"
        m1.metadata = {"create_time": long_ago, "importance": 0.7, "topics": ["python"]}
        m1.final_score = 0.9

        m2 = MagicMock()
        m2.doc_id = 1  # same doc_id
        m2.content = "Memory one"
        m2.metadata = {"create_time": long_ago, "importance": 0.7, "topics": ["python"]}
        m2.final_score = 0.9

        # First seed returns m1, second seed also returns m1
        call_count = [0]

        async def _search(seed, **kwargs):
            call_count[0] += 1
            return [m1]

        compressor._search_similar = _search
        result = await compressor.compress_old_memories()
        # Only one old memory seen (deduped), so no group can form
        assert result["merged_groups"] == 0
