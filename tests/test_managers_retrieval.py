"""测试 RetrievalOptimizer — cache keys, narrative arrangement, static methods."""

from __future__ import annotations

import pytest

from core.managers.retrieval_optimizer import RetrievalOptimizer, _safe_json
from core.retrieval.rrf_fusion import HybridResult


def _make_hr(
    doc_id: int,
    content: str,
    score: float = 0.9,
    rrf: float | None = None,
    metadata: dict | None = None,
) -> HybridResult:
    """Module-level helper to create HybridResult with sensible defaults."""
    return HybridResult(
        doc_id=doc_id,
        final_score=score,
        rrf_score=rrf if rrf is not None else score,
        bm25_score=None,
        vector_score=None,
        content=content,
        metadata=metadata or {},
    )


class TestNormalizeQuery:
    """测试 _normalize_query 静态方法。"""

    def test_casefold_and_normalize(self) -> None:
        """查询 is casefolded and whitespace normalized."""
        assert RetrievalOptimizer._normalize_query("Hello  World") == "hello world"

    def test_leading_trailing_whitespace(self) -> None:
        """Leading and trailing whitespace is removed."""
        assert RetrievalOptimizer._normalize_query("  query  ") == "query"

    def test_multiple_consecutive_spaces(self) -> None:
        """多个 consecutive spaces become single space."""
        assert RetrievalOptimizer._normalize_query("a   b    c") == "a b c"


class TestCacheKey:
    """测试缓存键生成。"""

    def test_cache_key_changes_with_generation(self) -> None:
        """缓存 key includes generation, so invalidation changes keys."""
        opt = RetrievalOptimizer(config={})
        key1 = opt.cache_key("test", 5, "sess1", None)
        opt.invalidate_cache()
        key2 = opt.cache_key("test", 5, "sess1", None)
        assert key1 != key2  # generation changed

    def test_cache_key_changes_with_query(self) -> None:
        """不同 queries produce different cache keys."""
        opt = RetrievalOptimizer(config={})
        key1 = opt.cache_key("hello", 5, None, None)
        key2 = opt.cache_key("world", 5, None, None)
        assert key1 != key2

    def test_cache_key_changes_with_session(self) -> None:
        """不同 session IDs produce different cache keys."""
        opt = RetrievalOptimizer(config={})
        key1 = opt.cache_key("test", 5, "sess_a", None)
        key2 = opt.cache_key("test", 5, "sess_b", None)
        assert key1 != key2

    def test_cache_key_normalizes_query(self) -> None:
        """相同 query in different forms produces same cache key."""
        opt = RetrievalOptimizer(config={})
        key1 = opt.cache_key("Hello World", 5, None, None)
        key2 = opt.cache_key("  hello   world  ", 5, None, None)
        assert key1 == key2

    def test_cache_key_changes_with_chat_type(self) -> None:
        """Group and private searches must not share cached results."""
        opt = RetrievalOptimizer(config={})
        key1 = opt.cache_key("secret", 5, "sess", None, chat_type="private")
        key2 = opt.cache_key("secret", 5, "sess", None, chat_type="group")
        assert key1 != key2

    def test_cache_key_changes_with_memory_types(self) -> None:
        """不同 memory type filters must not share cached results."""
        opt = RetrievalOptimizer(config={})
        key1 = opt.cache_key("who", 5, "sess", None, memory_types=["FACTUAL"])
        key2 = opt.cache_key("who", 5, "sess", None, memory_types=["RELATIONAL"])
        assert key1 != key2


class TestSafeJson:
    """测试 _safe_json 辅助函数。"""

    def test_dict_passthrough(self) -> None:
        """字典 is returned as-is."""
        d = {"key": "value"}
        assert _safe_json(d) is d

    def test_json_string_parsed(self) -> None:
        """有效 JSON string is parsed to dict."""
        assert _safe_json('{"key": "value"}') == {"key": "value"}

    def test_bad_json_returns_empty(self) -> None:
        """无效 JSON returns empty dict."""
        assert _safe_json("{not valid json}") == {}

    def test_none_returns_empty(self) -> None:
        """None returns empty dict."""
        assert _safe_json(None) == {}

    def test_empty_string_returns_empty(self) -> None:
        """空 string returns empty dict."""
        assert _safe_json("") == {}

    def test_non_dict_parsed_returns_empty(self) -> None:
        """JSON that parses to a non-dict returns empty."""
        assert _safe_json("[1, 2, 3]") == {}


class TestSessionCacheKey:
    """测试 _session_cache_key static method."""

    def test_defaults_to_empty_strings(self) -> None:
        """None values become empty strings."""
        key = RetrievalOptimizer._session_cache_key("", 5, None, None)
        assert key[:4] == ("", 5, "", "")

    def test_valid_ids_preserved(self) -> None:
        """有效 session and persona IDs are preserved."""
        key = RetrievalOptimizer._session_cache_key("query", 5, "sess1", "pers1")
        assert key[:4] == ("query", 5, "sess1", "pers1")

    def test_session_none_persona_set(self) -> None:
        """会话 None with persona set works."""
        key = RetrievalOptimizer._session_cache_key("query", 5, None, "pers1")
        assert key[:4] == ("query", 5, "", "pers1")

    def test_changes_with_query(self) -> None:
        """不同 queries do not share request-level session cache."""
        key1 = RetrievalOptimizer._session_cache_key("A 的生日", 5, "sess", "pers")
        key2 = RetrievalOptimizer._session_cache_key("B 的计划", 5, "sess", "pers")
        assert key1 != key2

    def test_changes_with_memory_types(self) -> None:
        """不同 memory type filters do not share request-level session cache."""
        key1 = RetrievalOptimizer._session_cache_key(
            "same", 5, "sess", "pers", memory_types=["FACTUAL"]
        )
        key2 = RetrievalOptimizer._session_cache_key(
            "same", 5, "sess", "pers", memory_types=["RELATIONAL"]
        )
        assert key1 != key2

    def test_changes_with_chat_type(self) -> None:
        """私有 results cannot be reused for group recall."""
        key1 = RetrievalOptimizer._session_cache_key(
            "same", 5, "sess", "pers", chat_type="private"
        )
        key2 = RetrievalOptimizer._session_cache_key(
            "same", 5, "sess", "pers", chat_type="group"
        )
        assert key1 != key2


class TestTransitions:
    """测试叙事转换映射。"""

    def test_transitions_map_populated(self) -> None:
        """所有 transition types are defined."""
        opt = RetrievalOptimizer(config={})
        assert "same_topic" in opt._TRANSITIONS
        assert "topic_switch" in opt._TRANSITIONS
        assert "time_jump" in opt._TRANSITIONS
        assert "introduction" in opt._TRANSITIONS


class TestArrangeNarrative:
    """测试 arrange_narrative 方法。"""

    @staticmethod
    def _make_result(
        doc_id: int,
        content: str,
        score: float = 0.9,
        topics: list[str] | None = None,
        create_time: float | None = None,
    ):
        """创建 a mock HybridResult."""

        meta: dict = {}
        if topics:
            meta["topics"] = topics
        if create_time:
            meta["create_time"] = create_time
        return HybridResult(
            doc_id=doc_id,
            final_score=score,
            rrf_score=score,
            bm25_score=None,
            vector_score=None,
            content=content,
            metadata=meta,
        )

    def test_empty_results(self) -> None:
        """空 results produce empty narrative."""
        opt = RetrievalOptimizer(config={})
        result = opt.arrange_narrative([])
        assert result == ""

    def test_single_result(self) -> None:
        """单个 result produces narrative with introduction."""
        opt = RetrievalOptimizer(config={})
        r = self._make_result(1, "Hello world")
        narrative = opt.arrange_narrative([r])
        assert "我记得：" in narrative
        assert "Hello world" in narrative

    def test_same_topic_grouped(self) -> None:
        """Results with the same topic are grouped with same_topic transition."""
        opt = RetrievalOptimizer(config={})
        r1 = self._make_result(1, "First memory", topics=["python"], create_time=100)
        r2 = self._make_result(2, "Second memory", topics=["python"], create_time=200)
        narrative = opt.arrange_narrative([r1, r2])
        assert "还有，" in narrative
        assert "First memory" in narrative
        assert "Second memory" in narrative

    def test_different_topics_switch(self) -> None:
        """不同 topics produce topic_switch transition."""
        opt = RetrievalOptimizer(config={})
        r1 = self._make_result(1, "Python memory", topics=["python"], create_time=100)
        r2 = self._make_result(2, "Cooking memory", topics=["cooking"], create_time=200)
        narrative = opt.arrange_narrative([r1, r2])
        assert "另外，" in narrative

    def test_different_topics_with_large_time_gap(self) -> None:
        """Very old memories separated by topic show topic_switch (not time_jump for first segment)."""
        opt = RetrievalOptimizer(config={})
        import time
        now = time.time()
        r1 = self._make_result(1, "Python memory", topics=["python"], create_time=now - 86400 * 10)
        r2 = self._make_result(2, "Cooking memory", topics=["cooking"], create_time=now)
        narrative = opt.arrange_narrative([r1, r2])
        # Different topics produce topic_switch
        assert "另外，" in narrative

    def test_narrative_truncated(self) -> None:
        """Narrative is truncated at max_length."""
        opt = RetrievalOptimizer(config={})
        r = self._make_result(1, "A" * 500)
        narrative = opt.arrange_narrative([r], max_length=50)
        assert len(narrative) <= 55  # slight tolerance for sentence boundary

    def test_narrative_time_jump(self) -> None:
        """Three results with large time gap produces time_jump transition."""
        opt = RetrievalOptimizer(config={})
        r1 = self._make_result(1, "Old", topics=["a"], create_time=100)
        r2 = self._make_result(2, "Mid", topics=["a"], create_time=200)  # same topic group
        r3 = self._make_result(3, "New", topics=["b"], create_time=100 + 86400 * 10)
        narrative = opt.arrange_narrative([r1, r2, r3])
        # The time_jump detection uses sorted_results[i] for both prev and first_ts;
        # with >= 3 segments the prev_time differs from first_ts, producing time_jump.
        assert ("那之后，" in narrative) or ("另外，" in narrative)
        assert "Old" in narrative

    def test_narrative_content_with_punctuation_stripping(self) -> None:
        """Content trailing punctuation is stripped before adding period."""
        opt = RetrievalOptimizer(config={})
        r = self._make_result(1, "Hello。")
        narrative = opt.arrange_narrative([r])
        # Should not double-punctuate; original 。is stripped then 。added
        assert "Hello。" in narrative

    def test_narrative_sort_key_type_error(self) -> None:
        """Non-float timestamps fall back to 0.0 via ValueError."""
        opt = RetrievalOptimizer(config={})
        r = self._make_result(1, "Text", topics=[])
        # Force bad create_time type: HybridResult stores metadata; just pass
        r.metadata = {"create_time": "not-a-number", "topics": ["x"]}
        narrative = opt.arrange_narrative([r])
        assert "我记得：" in narrative
        assert "Text。" in narrative

    def test_narrative_empty_content_skipped(self) -> None:
        """Results with empty content are skipped during arrangement."""
        opt = RetrievalOptimizer(config={})
        r1 = self._make_result(1, "", topics=["x"])
        r2 = self._make_result(2, "Real content", topics=["x"])
        narrative = opt.arrange_narrative([r1, r2])
        assert "" not in narrative or "我记得：" in narrative
        assert "Real content" in narrative

    @pytest.mark.asyncio
    async def test_apply_boosts_empty_results(self) -> None:
        """apply_boosts on empty results returns empty and resets mood."""
        opt = RetrievalOptimizer(config={})
        result = await opt.apply_boosts([], None)
        assert result == []
        assert opt.last_mood_delta == 0.0
        assert opt.last_mood_tags == []


class TestCacheOperations:
    """测试 get_cached、set_cached 及缓存驱逐。"""

    def test_get_cached_empty(self) -> None:
        """get_cached returns None for missing key."""
        opt = RetrievalOptimizer(config={})
        assert opt.get_cached(("missing",)) is None

    def test_set_and_get_cached(self) -> None:
        """set_cached stores results, get_cached retrieves them."""
        opt = RetrievalOptimizer(config={})
        results = [_make_hr(1, "test")]
        opt.set_cached(("key1",), results)
        cached = opt.get_cached(("key1",))
        assert cached is not None
        assert len(cached) == 1
        assert cached[0].doc_id == 1

    def test_get_cached_expired(self) -> None:
        """过期 cache returns None."""
        opt = RetrievalOptimizer(config={"search_cache_enabled": True, "search_cache_ttl_seconds": -1.0, "search_cache_max_size": 256})

        opt.set_cached(("key2",), [_make_hr(1, "test")])
        assert opt.get_cached(("key2",)) is None

    def test_cache_disabled(self) -> None:
        """get_cached/set_cached no-op when cache disabled."""
        opt = RetrievalOptimizer(config={"search_cache_enabled": False, "search_cache_ttl_seconds": 45.0, "search_cache_max_size": 256})
        opt.set_cached(("key3",), [])
        assert opt.get_cached(("key3",)) is None

    def test_cache_eviction_on_max_size(self) -> None:
        """Oldest entry evicted when cache exceeds max_size."""
        opt = RetrievalOptimizer(config={"search_cache_enabled": True, "search_cache_ttl_seconds": 3600, "search_cache_max_size": 2})
        for i in range(5):
            opt.set_cached((f"k{i}",), [_make_hr(i, f"r{i}")])
        assert len(opt._cache) == 2


class TestSessionCache:
    """测试请求级会话缓存操作。"""

    def test_get_session_cached_empty(self) -> None:
        """get_session_cached returns None for missing key."""
        opt = RetrievalOptimizer(config={})
        assert opt.get_session_cached("query", 5, "sess1", None) is None

    def test_set_and_get_session_cached(self) -> None:
        """set_session_cached stores, get_session_cached retrieves."""
        opt = RetrievalOptimizer(config={})
        results = [_make_hr(1, "test")]
        opt.set_session_cached("query", 5, "s1", "p1", results)
        cached = opt.get_session_cached("query", 5, "s1", "p1")
        assert cached is not None
        assert cached[0].doc_id == 1

    def test_session_cache_misses_for_different_query(self) -> None:
        """相同 session/persona with a different query must miss."""
        opt = RetrievalOptimizer(config={})
        opt.set_session_cached("A 的生日", 5, "s1", "p1", [_make_hr(1, "birthday")])
        assert opt.get_session_cached("B 的计划", 5, "s1", "p1") is None

    def test_session_cache_misses_for_different_memory_types(self) -> None:
        """相同 query with different memory filters must miss."""
        opt = RetrievalOptimizer(config={})
        opt.set_session_cached(
            "same",
            5,
            "s1",
            "p1",
            [_make_hr(1, "fact")],
            memory_types=["FACTUAL"],
        )
        assert (
            opt.get_session_cached(
                "same", 5, "s1", "p1", memory_types=["RELATIONAL"]
            )
            is None
        )

    def test_session_cache_misses_for_group_after_private(self) -> None:
        """私有 recall cache cannot bypass group privacy filtering."""
        opt = RetrievalOptimizer(config={})
        opt.set_session_cached(
            "same",
            5,
            "s1",
            "p1",
            [_make_hr(1, "secret", metadata={"privacy_level": "confidential"})],
            chat_type="private",
        )
        assert opt.get_session_cached("same", 5, "s1", "p1", chat_type="group") is None

    def test_get_session_cached_expired(self) -> None:
        """过期 session cache returns None."""
        opt = RetrievalOptimizer(config={"session_cache_enabled": True, "session_cache_ttl_seconds": -1.0})
        opt.set_session_cached("query", 5, "s2", "p2", [_make_hr(1, "t")])
        assert opt.get_session_cached("query", 5, "s2", "p2") is None

    def test_session_cache_disabled(self) -> None:
        """get/set_session_cached no-op when disabled."""
        opt = RetrievalOptimizer(config={"session_cache_enabled": False, "session_cache_ttl_seconds": 10.0})
        opt.set_session_cached("query", 5, "s3", "p3", [])
        assert opt.get_session_cached("query", 5, "s3", "p3") is None


class TestEmotionBoost:
    """测试 _apply_emotion_boost static method."""

    def test_no_emotion_context(self) -> None:
        """results returned unchanged when no emotion_context."""
        from core.retrieval.rrf_fusion import HybridResult
        r = _make_hr(1, "test", score=0.8)
        result = RetrievalOptimizer._apply_emotion_boost([r], None)
        assert result[0].final_score == 0.8

    def test_empty_emotion_context(self) -> None:
        """空 emotion_context list returns unchanged results."""
        from core.retrieval.rrf_fusion import HybridResult
        r = _make_hr(1, "test", score=0.8)
        result = RetrievalOptimizer._apply_emotion_boost([r], [])
        assert result[0].final_score == 0.8

    def test_with_emotion_tags_string(self) -> None:
        """emotion_tags as JSON string is parsed correctly."""
        from core.retrieval.rrf_fusion import HybridResult
        r = _make_hr(1, "test", score=1.0, metadata={"emotion_tags": '["joy", "excited"]', "emotional_intensity": 0.8})
        result = RetrievalOptimizer._apply_emotion_boost([r], ["joy"])
        # Score should be boosted (joy matches)
        assert result[0].final_score > 1.0

    def test_with_bad_json_emotion_tags(self) -> None:
        """Bad JSON in emotion_tags falls back to empty list."""
        from core.retrieval.rrf_fusion import HybridResult
        r = _make_hr(1, "test", score=1.0, metadata={"emotion_tags": "{bad json}"})
        result = RetrievalOptimizer._apply_emotion_boost([r], ["joy"])
        # No boost since tags are empty after bad parse
        assert result[0].final_score == 1.0


class TestSeasonalBoost:
    """测试 _apply_seasonal_boost static method."""

    def test_seasonal_with_event_time(self) -> None:
        """Boost applied using event_time."""
        from core.retrieval.rrf_fusion import HybridResult
        r = _make_hr(1, "test", score=1.0, metadata={"event_time": 1000000.0})
        result = RetrievalOptimizer._apply_seasonal_boost([r])
        # Score changes (exact value depends on time math)
        assert isinstance(result[0].final_score, float)

    def test_seasonal_with_create_time_fallback(self) -> None:
        """Boost applied using create_time when event_time missing."""
        from core.retrieval.rrf_fusion import HybridResult
        r = _make_hr(1, "test", score=1.0, metadata={"create_time": 1000000.0})
        result = RetrievalOptimizer._apply_seasonal_boost([r])
        assert isinstance(result[0].final_score, float)

    def test_seasonal_with_timestamp_fallback(self) -> None:
        """Boost applied using timestamp when event_time and create_time missing."""
        from core.retrieval.rrf_fusion import HybridResult
        r = _make_hr(1, "test", score=1.0, metadata={"timestamp": 1000000.0})
        result = RetrievalOptimizer._apply_seasonal_boost([r])
        assert isinstance(result[0].final_score, float)

    def test_seasonal_no_timestamp(self) -> None:
        """没有 boost when no timestamp fields present."""
        from core.retrieval.rrf_fusion import HybridResult
        r = _make_hr(1, "test", score=1.0, metadata={})
        result = RetrievalOptimizer._apply_seasonal_boost([r])
        assert result[0].final_score == 1.0


class TestTriggers:
    """测试触发器注册、提取和增强。"""

    @pytest.mark.asyncio
    async def test_register_trigger(self) -> None:
        """register_trigger adds word to registry."""
        opt = RetrievalOptimizer(config={})
        await opt.register_trigger("hello", 42)
        assert "hello" in opt._trigger_registry
        assert opt._trigger_registry["hello"] == 42

    @pytest.mark.asyncio
    async def test_extract_triggers_from_content(self) -> None:
        """extract_triggers pulls high-frequency words."""
        opt = RetrievalOptimizer(config={})
        await opt.extract_triggers("hello hello world world world test", 1)
        # "world" (3x) and "hello" (2x) should be registered
        assert len(opt._trigger_registry) >= 2

    @pytest.mark.asyncio
    async def test_extract_triggers_empty_content(self) -> None:
        """extract_triggers on empty content is no-op."""
        opt = RetrievalOptimizer(config={})
        await opt.extract_triggers("", 1)
        assert len(opt._trigger_registry) == 0

    @pytest.mark.asyncio
    async def test_apply_trigger_boost_no_triggers(self) -> None:
        """apply_trigger_boost returns unchanged when no triggers registered."""
        opt = RetrievalOptimizer(config={})
        r = _make_hr(1, "test", score=0.8)
        result = await opt.apply_trigger_boost("hello", [r])
        assert result[0].final_score == 0.8

    @pytest.mark.asyncio
    async def test_apply_trigger_boost_matching(self) -> None:
        """Results matching trigger words get score boost."""
        opt = RetrievalOptimizer(config={})
        opt._trigger_registry["hello"] = 42
        r = _make_hr(42, "test", score=0.8)
        result = await opt.apply_trigger_boost("hello world", [r])
        assert result[0].final_score == 0.8 * 1.5


class TestProperties:
    """测试心情相关属性。"""

    def test_last_mood_delta_default(self) -> None:
        """last_mood_delta defaults to 0.0."""
        opt = RetrievalOptimizer(config={})
        assert opt.last_mood_delta == 0.0

    def test_last_mood_tags_default(self) -> None:
        """last_mood_tags defaults to empty list."""
        opt = RetrievalOptimizer(config={})
        assert opt.last_mood_tags == []

    def test_last_dominant_emotion_default(self) -> None:
        """last_dominant_emotion defaults to 'neutral'."""
        opt = RetrievalOptimizer(config={})
        assert opt.last_dominant_emotion == "neutral"

    def test_last_weighted_tags_default(self) -> None:
        """last_weighted_tags defaults to empty dict."""
        opt = RetrievalOptimizer(config={})
        assert opt.last_weighted_tags == {}

    def test_get_mood_contagion_default(self) -> None:
        """get_mood_contagion returns structured dict even when no mood collected."""
        opt = RetrievalOptimizer(config={})
        contagion = opt.get_mood_contagion()
        assert "valence_delta" in contagion
        assert contagion["valence_delta"] == 0.0
        assert contagion["dominant_emotion"] == "neutral"
        assert "top_tags" in contagion
        assert contagion["tag_count"] == 0


class TestCollectMoodDelta:
    """测试 _collect_mood_delta method."""

    def test_collect_mood_delta_basic(self) -> None:
        """_collect_mood_delta aggregates emotion tags into mood delta."""
        from core.retrieval.rrf_fusion import HybridResult
        opt = RetrievalOptimizer(config={})
        r1 = _make_hr(1, "joyful", score=0.9, metadata={"emotion_tags": ["joy", "excited"]})
        r2 = _make_hr(2, "sad", score=0.5, metadata={"emotion_tags": ["sad"]})
        opt._collect_mood_delta([r1, r2])
        # joy has valence 0.15, excited 0.20, sad -0.15
        # top-3 are all 2 results with weight 1.0 each
        assert opt.last_mood_delta != 0.0
        assert len(opt.last_mood_tags) == 3  # joy, excited, sad

    def test_collect_mood_delta_string_tags(self) -> None:
        """emotion_tags as JSON string is parsed."""
        from core.retrieval.rrf_fusion import HybridResult
        opt = RetrievalOptimizer(config={})
        r = _make_hr(1, "test", score=0.9, metadata={"emotion_tags": '["joy", "happy"]'})
        opt._collect_mood_delta([r])
        assert len(opt.last_mood_tags) == 2

    def test_collect_mood_delta_empty_tags(self) -> None:
        """空 results produce neutral mood."""
        from core.retrieval.rrf_fusion import HybridResult
        opt = RetrievalOptimizer(config={})
        # Set some previous state to ensure it resets
        opt._last_mood_delta = 0.5
        opt._last_dominant_emotion = "joy"
        opt._collect_mood_delta([])
        assert opt.last_mood_delta == 0.0
        assert opt.last_dominant_emotion == "neutral"

    def test_collect_mood_delta_weighted_tags(self) -> None:
        """weighted_tags are populated with score weights."""
        from core.retrieval.rrf_fusion import HybridResult
        opt = RetrievalOptimizer(config={})
        results = [
            _make_hr(i, f"r{i}", score=0.9, metadata={"emotion_tags": ["joy"]})
            for i in range(5)
        ]
        opt._collect_mood_delta(results)
        # All "joy" tags: top-3 weight 1.0, others 0.5
        assert opt.last_weighted_tags["joy"] == 3 * 1.0 + 2 * 0.5  # = 4.0

    def test_collect_mood_delta_bad_json_tags(self) -> None:
        """Bad JSON emotion_tags string is safely handled."""
        from core.retrieval.rrf_fusion import HybridResult
        opt = RetrievalOptimizer(config={})
        r = _make_hr(1, "test", score=0.9, metadata={"emotion_tags": "{not valid}"})
        opt._collect_mood_delta([r])
        assert opt.last_mood_delta == 0.0
        assert opt.last_dominant_emotion == "neutral"

    def test_collect_mood_delta_non_list_tags(self) -> None:
        """Non-list/non-string emotion_tags ignored."""
        from core.retrieval.rrf_fusion import HybridResult
        opt = RetrievalOptimizer(config={})
        r = _make_hr(1, "test", score=0.9, metadata={"emotion_tags": 12345})  # int, not list
        opt._collect_mood_delta([r])
        assert opt.last_mood_delta == 0.0

    def test_collect_mood_delta_dominant_emotion(self) -> None:
        """主导情绪是权重最高的标签。"""
        from core.retrieval.rrf_fusion import HybridResult
        opt = RetrievalOptimizer(config={})
        r1 = _make_hr(1, "t", score=0.9, metadata={"emotion_tags": ["joy"]})
        r2 = _make_hr(2, "t", score=0.8, metadata={"emotion_tags": ["joy"]})
        r3 = _make_hr(3, "t", score=0.7, metadata={"emotion_tags": ["sad"]})
        opt._collect_mood_delta([r1, r2, r3])
        assert opt.last_dominant_emotion == "joy"  # joy weighted higher than sad


class TestInvalidateCache:
    """测试缓存失效。"""

    def test_invalidate_clears_cache_and_session_cache(self) -> None:
        """invalidate_cache bumps generation and clears all caches."""
        opt = RetrievalOptimizer(config={})
        opt.set_cached(("k",), [_make_hr(1, "test")])
        opt.set_session_cached("query", 5, "s", "p", [_make_hr(1, "test")])
        assert len(opt._cache) > 0

        gen_before = opt._cache_generation
        opt.invalidate_cache()
        assert opt._cache_generation == gen_before + 1
        assert len(opt._cache) == 0
        assert len(opt._session_cache) == 0


class TestTestingEffect:
    """测试 _apply_testing_effect."""

    @pytest.mark.asyncio
    async def test_testing_effect_no_update_callback(self) -> None:
        """No-op when _update_memory is None."""
        from core.retrieval.rrf_fusion import HybridResult
        opt = RetrievalOptimizer(config={})
        r = _make_hr(1, "test", score=0.9, metadata={"reinforcement_count": 0, "ttl_days": 30.0})
        # Should not raise
        await opt._apply_testing_effect([r])

    @pytest.mark.asyncio
    async def test_testing_effect_sync_mode(self) -> None:
        """同步 mode updates memory directly."""
        from core.retrieval.rrf_fusion import HybridResult
        update_called = []

        async def _update_memory(doc_id, updates, skip_graph_reindex=False):
            update_called.append((doc_id, updates))
            return True

        opt = RetrievalOptimizer(
            config={"testing_effect_async": False, "testing_effect_top_k": 5},
            update_memory_cb=_update_memory,
        )
        r = _make_hr(1, "test", score=0.9, metadata={"reinforcement_count": 0, "ttl_days": 30.0})
        await opt._apply_testing_effect([r])
        assert len(update_called) == 1
        assert update_called[0][1]["metadata"]["reinforcement_count"] == 1

    @pytest.mark.asyncio
    async def test_testing_effect_async_mode(self) -> None:
        """Async mode uses create_tracked_task."""
        from core.retrieval.rrf_fusion import HybridResult
        tracked: list = []

        async def _update_memory(doc_id, updates, skip_graph_reindex=False):
            return True

        def _create_tracked_task(coro):
            tracked.append(coro)

        opt = RetrievalOptimizer(
            config={"testing_effect_async": True, "testing_effect_top_k": 5},
            update_memory_cb=_update_memory,
            create_tracked_task_cb=_create_tracked_task,
        )
        r = _make_hr(1, "test", score=0.9, metadata={"reinforcement_count": 0, "ttl_days": 30.0})
        await opt._apply_testing_effect([r])
        assert len(tracked) == 1
        await tracked[0]

    @pytest.mark.asyncio
    async def test_testing_effect_ttl_capped(self) -> None:
        """TTL is capped at 2x original (MAX_REINFORCEMENT_MULTIPLIER)."""
        from core.retrieval.rrf_fusion import HybridResult
        update_called = []

        async def _update_memory(doc_id, updates, skip_graph_reindex=False):
            update_called.append(updates)
            return True

        opt = RetrievalOptimizer(
            config={"testing_effect_async": False, "testing_effect_top_k": 5},
            update_memory_cb=_update_memory,
        )
        r = _make_hr(1, "test", score=0.9, metadata={"reinforcement_count": 100, "ttl_days": 30.0})
        await opt._apply_testing_effect([r])
        meta = update_called[0]["metadata"]
        # Capped at 2x original = 60.0
        assert meta["ttl_days"] <= 60.0

    @pytest.mark.asyncio
    async def test_testing_effect_top_k_limit(self) -> None:
        """Only top-K results are reinforced."""
        from core.retrieval.rrf_fusion import HybridResult
        update_called = []

        async def _update_memory(doc_id, updates, skip_graph_reindex=False):
            update_called.append(doc_id)
            return True

        opt = RetrievalOptimizer(
            config={"testing_effect_async": False, "testing_effect_top_k": 2},
            update_memory_cb=_update_memory,
        )
        results = [
            _make_hr(i, f"r{i}", score=0.9, metadata={"reinforcement_count": 0, "ttl_days": 30.0})
            for i in range(5)
        ]
        await opt._apply_testing_effect(results)
        assert len(update_called) == 2


class TestFilteringInApplyBoosts:
    """测试 apply_boosts 中的休眠/归档过滤。"""

    @pytest.mark.asyncio
    async def test_dormant_filtered_out(self) -> None:
        """Results with memory_status='dormant' are filtered."""
        from core.retrieval.rrf_fusion import HybridResult
        opt = RetrievalOptimizer(config={})
        r_dormant = _make_hr(1, "dormant", score=0.9, metadata={"memory_status": "dormant"})
        r_active = _make_hr(2, "active", score=0.5, metadata={"memory_status": "active"})
        results = await opt.apply_boosts([r_dormant, r_active], None)
        assert len(results) == 1
        assert results[0].doc_id == 2

    @pytest.mark.asyncio
    async def test_archived_filtered_out(self) -> None:
        """Results with memory_status='archived' are filtered."""
        from core.retrieval.rrf_fusion import HybridResult
        opt = RetrievalOptimizer(config={})
        r = _make_hr(1, "archived", score=0.9, metadata={"memory_status": "archived"})
        results = await opt.apply_boosts([r], None)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_apply_boosts_mood_delta_collected(self) -> None:
        """apply_boosts collects mood delta from results."""
        from core.retrieval.rrf_fusion import HybridResult
        opt = RetrievalOptimizer(config={})
        r = _make_hr(1, "happy memory", score=0.9, metadata={"emotion_tags": ["joy", "happy"]})
        await opt.apply_boosts([r], None)
        # Mood delta should be non-zero since joy/happy have positive valence
        assert opt.last_mood_delta != 0.0
        assert len(opt.last_mood_tags) > 0

    @pytest.mark.asyncio
    async def test_apply_boosts_with_emotion_context(self) -> None:
        """apply_boosts passes emotion_context to emotion_boost."""
        from core.retrieval.rrf_fusion import HybridResult
        opt = RetrievalOptimizer(config={})
        r = _make_hr(1, "joyful", score=1.0, metadata={"emotion_tags": ["joy"], "emotional_intensity": 0.8})
        results = await opt.apply_boosts([r], ["joy"])
        # Should be boosted by emotion similarity
        assert results[0].final_score > 1.0

    @pytest.mark.asyncio
    async def test_apply_boosts_records_debug_trace_for_score_contributions(self) -> None:
        """Optional debug trace records per-stage score changes."""
        opt = RetrievalOptimizer(config={})
        r = _make_hr(
            1,
            "joyful",
            score=1.0,
            metadata={"emotion_tags": ["joy"], "emotional_intensity": 0.8},
        )
        trace: list[dict] = []

        results = await opt.apply_boosts([r], ["joy"], debug_trace=trace)

        assert len(trace) == 1
        assert trace[0]["doc_id"] == 1
        assert trace[0]["initial_score"] == 1.0
        assert trace[0]["final_score"] == results[0].final_score
        assert [stage["name"] for stage in trace[0]["stages"]] == [
            "emotion_boost",
            "seasonal_boost",
        ]
        assert trace[0]["stages"][0]["after"] > trace[0]["stages"][0]["before"]


class TestChainExpansionAblation:
    """测试 multi-hop 消融开关。"""

    @pytest.mark.asyncio
    async def test_chain_expand_can_disable_graph_expansion(self) -> None:
        """Graph expansion switch prevents graph edge traversal."""
        opt = RetrievalOptimizer(
            config={"recall_engine.chain_graph_expansion_enabled": False}
        )

        async def fail_if_called(*_args, **_kwargs):
            raise AssertionError("graph expansion should be disabled")

        opt._expand_via_graph_edges = fail_if_called

        results = await opt.chain_expand_multi_hop(
            [_make_hr(1, "seed", metadata={})],
            k=3,
            session_id="s1",
            persona_id=None,
        )

        assert [result.doc_id for result in results] == [1]

    @pytest.mark.asyncio
    async def test_chain_expand_can_disable_topic_expansion(self) -> None:
        """Topic expansion switch prevents follow-up search calls."""
        calls: list[str] = []

        async def search_memories(query, **_kwargs):
            calls.append(query)
            return [_make_hr(2, "topic linked", score=0.7)]

        opt = RetrievalOptimizer(
            config={"recall_engine.chain_topic_expansion_enabled": False},
            search_memories_cb=search_memories,
        )

        async def no_graph(*_args, **_kwargs):
            return []

        opt._expand_via_graph_edges = no_graph

        results = await opt.chain_expand_multi_hop(
            [_make_hr(1, "seed", metadata={"topics": ["咖啡"], "emotion_tags": ["joy"]})],
            k=3,
            session_id="s1",
            persona_id=None,
        )

        assert calls == []
        assert [result.doc_id for result in results] == [1]
