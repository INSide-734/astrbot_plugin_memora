"""测试 MemoryQualityScorer — 5-dimension scoring, alerts, and auto-pause."""

from __future__ import annotations

import time
from unittest.mock import patch

from core.monitoring.quality_scorer import (
    _SOURCE_RELEVANCE_WEIGHT,
    _SOURCE_RELIABILITY,
    AlertLevel,
    MemoryQualityScorer,
    QualityAlert,
    QualityScore,
    _count_connectors,
    _count_segments,
    _has_contradictory_sentiment,
    _has_multiple_sentences,
    _has_paragraph_breaks,
    _has_url,
    _text_to_simple_embedding,
    _tokenize,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_atom(**overrides) -> dict:
    """构建 a minimal atom dict with sensible defaults for testing."""
    defaults = {
        "id": "atom-001",
        "content": "用户喜欢 Python 编程，因为 Python 语法简洁而且生态丰富",
        "created_at": time.time(),
        "ttl_days": 30.0,
        "source_type": "group_chat",
        "verified": False,
        "importance": 0.5,
    }
    defaults.update(overrides)
    return defaults


def _make_context(recent_messages=None, existing_atoms=None) -> dict:
    """构建 a minimal context dict."""
    ctx = {}
    if recent_messages is not None:
        ctx["recent_messages"] = recent_messages
    if existing_atoms is not None:
        ctx["existing_atoms"] = existing_atoms
    return ctx


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_returns_empty_for_blank(self):
        assert _tokenize("") == []
        assert _tokenize("   ") == []

    def test_produces_bigrams_and_space_tokens(self):
        result = _tokenize("你好世界")
        assert "你好" in result
        assert "好世" in result
        assert "世界" in result

    def test_includes_space_split_tokens(self):
        result = _tokenize("hello world")
        assert "hello" in result
        assert "world" in result


class TestContradictorySentiment:
    def test_detects_mixed_sentiment(self):
        assert _has_contradictory_sentiment("今天很开心但是又有点难过")

    def test_no_mixed_sentiment_when_only_positive(self):
        assert not _has_contradictory_sentiment("今天很开心很愉快")

    def test_no_mixed_sentiment_when_neutral(self):
        assert not _has_contradictory_sentiment("今天天气晴朗")


class TestCountConnectors:
    def test_counts_causal(self):
        assert _count_connectors("因为他很努力，所以成功了") >= 1

    def test_counts_contrast(self):
        assert _count_connectors("虽然累了，但还是继续工作") >= 1

    def test_counts_coordination(self):
        assert _count_connectors("而且他还很聪明，同时也很勤奋") >= 1

    def test_zero_for_plain_text(self):
        assert _count_connectors("今天天气不错") == 0

    def test_all_three_categories(self):
        text = "因为他努力但是失败了而且也没人帮忙"
        assert _count_connectors(text) >= 2


class TestCountSegments:
    def test_single_segment(self):
        assert _count_segments("简短内容") >= 1

    def test_paragraph_breaks(self):
        text = "第一段内容\n\n第二段内容"
        assert _count_segments(text) == 2

    def test_sentence_boundaries(self):
        # Use CJK full-width punctuation for Chinese sentence boundaries
        # Sentences must be >= 5 chars to pass the meaningful-sentence threshold
        text = "这是第一个测试句子。这是第二个测试句子！"
        assert _count_segments(text) >= 2


class TestParagraphAndSentenceHelpers:
    def test_has_paragraph_breaks_true(self):
        assert _has_paragraph_breaks("第一段\n\n第二段") is True

    def test_has_paragraph_breaks_false(self):
        assert _has_paragraph_breaks("单段文本") is False

    def test_has_multiple_sentences_true(self):
        assert _has_multiple_sentences("这是第一句话。这是第二句话。") is True

    def test_has_multiple_sentences_false(self):
        assert _has_multiple_sentences("只有一个句子") is False


class TestHasUrl:
    def test_detects_http(self):
        assert _has_url("参见 https://example.com 了解更多")

    def test_detects_www(self):
        assert _has_url("访问 www.example.com 查看")

    def test_no_url(self):
        assert not _has_url("普通文本没有链接")


class TestTextToSimpleEmbedding:
    def test_returns_vector_of_correct_dim(self):
        vec = _text_to_simple_embedding("测试内容", dim=64)
        assert len(vec) == 64

    def test_empty_text_returns_zero_vector(self):
        vec = _text_to_simple_embedding("", dim=64)
        assert all(v == 0.0 for v in vec)

    def test_non_empty_text_has_non_zero_entries(self):
        vec = _text_to_simple_embedding("这是一段测试内容", dim=64)
        assert any(v != 0.0 for v in vec)

    def test_embedding_does_not_depend_on_process_randomized_hash(self):
        with patch("builtins.hash", return_value=1):
            first = _text_to_simple_embedding("稳定的质量评分向量", dim=64)
        with patch("builtins.hash", return_value=2):
            second = _text_to_simple_embedding("稳定的质量评分向量", dim=64)

        assert first == second


# ---------------------------------------------------------------------------
# QualityScore dataclass
# ---------------------------------------------------------------------------


class TestQualityScore:
    def test_creates_with_all_dimensions(self):
        qs = QualityScore(
            atom_id="test-1",
            consistency=0.9,
            coherence=0.8,
            relevance=0.7,
            freshness=0.6,
            accuracy=0.5,
            overall=0.72,
        )
        assert qs.atom_id == "test-1"
        assert qs.consistency == 0.9
        assert qs.coherence == 0.8
        assert qs.relevance == 0.7
        assert qs.freshness == 0.6
        assert qs.accuracy == 0.5
        assert qs.overall == 0.72
        assert qs.timestamp > 0

    def test_timestamp_is_auto_generated(self):
        qs1 = QualityScore(
            atom_id="a",
            consistency=1,
            coherence=1,
            relevance=1,
            freshness=1,
            accuracy=1,
            overall=1,
        )
        time.sleep(0.01)
        qs2 = QualityScore(
            atom_id="b",
            consistency=1,
            coherence=1,
            relevance=1,
            freshness=1,
            accuracy=1,
            overall=1,
        )
        assert qs2.timestamp > qs1.timestamp


# ---------------------------------------------------------------------------
# AlertLevel enum
# ---------------------------------------------------------------------------


class TestAlertLevel:
    def test_four_levels(self):
        assert AlertLevel.CRITICAL.value == "critical"
        assert AlertLevel.HIGH.value == "high"
        assert AlertLevel.MEDIUM.value == "medium"
        assert AlertLevel.INFO.value == "info"


# ---------------------------------------------------------------------------
# QualityAlert dataclass
# ---------------------------------------------------------------------------


class TestQualityAlert:
    def test_creates_alert(self):
        alert = QualityAlert(
            level=AlertLevel.HIGH,
            dimension="consistency",
            score=0.35,
            threshold=0.45,
            message="consistency low",
            suggestion="dedup check",
        )
        assert alert.level == AlertLevel.HIGH
        assert alert.dimension == "consistency"
        assert alert.timestamp > 0


# ---------------------------------------------------------------------------
# Source reliability / relevance tables
# ---------------------------------------------------------------------------


class TestSourceTables:
    def test_admin_command_has_highest_reliability(self):
        assert _SOURCE_RELIABILITY["admin_command"] == 0.95

    def test_group_chat_has_lowest_reliability(self):
        assert _SOURCE_RELIABILITY["group_chat"] == 0.55

    def test_admin_command_has_highest_relevance(self):
        assert _SOURCE_RELEVANCE_WEIGHT["admin_command"] == 1.0

    def test_group_chat_has_lowest_relevance(self):
        assert _SOURCE_RELEVANCE_WEIGHT["group_chat"] == 0.6


# ---------------------------------------------------------------------------
# MemoryQualityScorer — scoring
# ---------------------------------------------------------------------------


class TestScorerInit:
    def test_default_initialization(self):
        scorer = MemoryQualityScorer()
        assert scorer._paused is False
        assert scorer._pause_reason == ""
        assert len(scorer._score_history) == 0
        assert len(scorer._alert_history) == 0

    def test_custom_window_size(self):
        scorer = MemoryQualityScorer(window_size=50)
        assert scorer._score_history.maxlen == 50


class TestScoreAtomHighQuality:
    def test_high_quality_atom_scores_high(self):
        scorer = MemoryQualityScorer()
        atom = _make_atom(
            content=(
                "用户张三详细介绍了 Python 异步编程的最佳实践，"
                "并且展示了一个完整的 FastAPI 项目示例，"
                "因为 FastAPI 性能出色，而且生态完善"
            ),
            source_type="admin_command",
            verified=True,
            created_at=time.time(),  # brand new
            ttl_days=365.0,
        )
        score = scorer.score_atom(atom)
        assert score.overall > 0.7, (
            f"Expected high quality, got overall={score.overall}"
        )
        assert score.accuracy > 0.8, "Admin + verified should have high accuracy"
        assert score.freshness > 0.8, "Brand new atom should be very fresh"

    def test_high_quality_with_rich_context(self):
        scorer = MemoryQualityScorer()
        atom = _make_atom(
            content="用户喜欢 Python 编程，因为 Python 语法简洁而且生态丰富",
            source_type="admin_command",
            verified=True,
        )
        context = _make_context(
            recent_messages=["Python 编程", "语法简洁", "生态丰富"],
            existing_atoms=[
                {
                    "content": "完全无关的足球话题",
                    "embedding": _text_to_simple_embedding("足球比赛"),
                }
            ],
        )
        score = scorer.score_atom(atom, context=context)
        assert score.overall > 0.7, (
            f"Expected high quality, got overall={score.overall}"
        )


class TestScoreAtomLowQuality:
    def test_low_quality_short_expired_content(self):
        scorer = MemoryQualityScorer()
        atom = _make_atom(
            content="嗯",
            source_type="group_chat",
            verified=False,
            created_at=time.time() - 60 * 86400,  # 60 days ago
            ttl_days=30.0,  # fully expired
        )
        score = scorer.score_atom(atom)
        assert score.overall < 0.5, f"Expected low quality, got overall={score.overall}"

    def test_empty_content_scores_low(self):
        scorer = MemoryQualityScorer()
        atom = _make_atom(content="", source_type="group_chat")
        score = scorer.score_atom(atom)
        assert score.coherence < 0.5, (
            f"Empty content should have low coherence, got {score.coherence}"
        )


class TestScoreAtomFreshness:
    def test_fresh_atom_scores_high(self):
        scorer = MemoryQualityScorer()
        atom = _make_atom(created_at=time.time(), ttl_days=30.0)
        score = scorer.score_atom(atom)
        assert score.freshness > 0.8, (
            f"Brand new atom should be fresh, got {score.freshness}"
        )

    def test_half_expired_atom_has_lower_freshness(self):
        scorer = MemoryQualityScorer()
        half_life_ago = time.time() - (15 * 86400)  # 15 days ago of 30-day TTL
        atom = _make_atom(created_at=half_life_ago, ttl_days=30.0)
        score = scorer.score_atom(atom)
        # At 50% TTL remaining, freshness should be lower but > 0
        assert 0.2 <= score.freshness <= 0.7, (
            f"Half-life freshness got {score.freshness}"
        )

    def test_fully_expired_atom_scores_zero_or_near_zero(self):
        scorer = MemoryQualityScorer()
        atom = _make_atom(
            created_at=time.time() - 120 * 86400,  # 120 days ago
            ttl_days=30.0,
        )
        score = scorer.score_atom(atom)
        assert score.freshness < 0.1, (
            f"Fully expired atom should have near-zero freshness, got {score.freshness}"
        )

    def test_zero_ttl_returns_zero(self):
        scorer = MemoryQualityScorer()
        atom = _make_atom(ttl_days=0.0)
        score = scorer.score_atom(atom)
        assert score.freshness == 0.0


class TestScoreAtomAccuracy:
    def test_admin_command_has_high_accuracy(self):
        scorer = MemoryQualityScorer()
        atom = _make_atom(source_type="admin_command", verified=False)
        score = scorer.score_atom(atom)
        assert score.accuracy >= 0.85, f"Admin command accuracy got {score.accuracy}"

    def test_group_chat_has_low_accuracy(self):
        scorer = MemoryQualityScorer()
        atom = _make_atom(source_type="group_chat", verified=False)
        score = scorer.score_atom(atom)
        assert score.accuracy <= 0.65, f"Group chat accuracy got {score.accuracy}"

    def test_verified_bonus(self):
        scorer = MemoryQualityScorer()
        unverified = scorer.score_atom(
            _make_atom(source_type="group_chat", verified=False)
        )
        verified = scorer.score_atom(
            _make_atom(source_type="group_chat", verified=True)
        )
        assert verified.accuracy > unverified.accuracy, (
            f"Verified={verified.accuracy} should > unverified={unverified.accuracy}"
        )

    def test_url_bonus(self):
        scorer = MemoryQualityScorer()
        no_url = scorer.score_atom(
            _make_atom(content="普通文本", source_type="group_chat")
        )
        with_url = scorer.score_atom(
            _make_atom(
                content="参考 https://example.com 了解更多", source_type="group_chat"
            )
        )
        assert with_url.accuracy > no_url.accuracy, (
            f"URL accuracy={with_url.accuracy} should > no-url={no_url.accuracy}"
        )

    def test_unknown_source_type_falls_back(self):
        scorer = MemoryQualityScorer()
        atom = _make_atom(source_type="unknown_platform", verified=False)
        score = scorer.score_atom(atom)
        assert score.accuracy == 0.55  # fallback default


class TestScoreAtomWithContext:
    def test_consistency_lower_when_context_has_similar_atoms(self):
        scorer = MemoryQualityScorer()
        atom = _make_atom(content="用户喜欢 Python 编程")
        context = _make_context(
            existing_atoms=[
                {"content": "用户喜欢 Python 编程，经常用它写脚本"},
                {"content": "Python 是该用户最喜欢的语言"},
            ]
        )
        score = scorer.score_atom(atom, context=context)
        # With near-duplicate existing content, consistency should be lower (< 0.8 default)
        assert score.consistency < 0.8, (
            f"Expected low consistency, got {score.consistency}"
        )

    def test_consistency_high_when_context_has_unrelated_atoms(self):
        scorer = MemoryQualityScorer()
        atom = _make_atom(content="用户喜欢 Python 编程")
        context = _make_context(
            existing_atoms=[
                {"content": "足球比赛结果"},
                {"content": "天气预报"},
            ]
        )
        score = scorer.score_atom(atom, context=context)
        assert score.consistency >= 0.7, (
            f"Expected high consistency, got {score.consistency}"
        )


# ---------------------------------------------------------------------------
# Alert system
# ---------------------------------------------------------------------------


class TestCheckAlerts:
    def test_high_scores_produce_no_alerts(self):
        scorer = MemoryQualityScorer()
        qs = QualityScore(
            atom_id="good",
            consistency=0.9,
            coherence=0.9,
            relevance=0.9,
            freshness=0.9,
            accuracy=0.9,
            overall=0.9,
        )
        alerts = scorer.check_alerts(qs)
        assert alerts == []

    def test_critical_overall_triggers_alert(self):
        scorer = MemoryQualityScorer()
        qs = QualityScore(
            atom_id="bad",
            consistency=0.2,
            coherence=0.2,
            relevance=0.2,
            freshness=0.2,
            accuracy=0.2,
            overall=0.2,
        )
        alerts = scorer.check_alerts(qs)
        critical = [a for a in alerts if a.level == AlertLevel.CRITICAL]
        assert len(critical) >= 1, f"Expected at least 1 CRITICAL alert, got {alerts}"

    def test_high_alert_threshold(self):
        scorer = MemoryQualityScorer()
        qs = QualityScore(
            atom_id="mid",
            consistency=0.4,
            coherence=0.4,
            relevance=0.4,
            freshness=0.4,
            accuracy=0.4,
            overall=0.4,
        )
        alerts = scorer.check_alerts(qs)
        # 0.4 is below HIGH threshold (0.45) but above CRITICAL (0.30)
        levels = {a.level for a in alerts}
        assert AlertLevel.HIGH in levels or AlertLevel.MEDIUM in levels

    def test_medium_alert_threshold(self):
        scorer = MemoryQualityScorer()
        qs = QualityScore(
            atom_id="mid_high",
            consistency=0.55,
            coherence=0.55,
            relevance=0.55,
            freshness=0.55,
            accuracy=0.55,
            overall=0.55,
        )
        alerts = scorer.check_alerts(qs)
        # 0.55 is below MEDIUM (0.60) but above HIGH (0.45)
        levels = {a.level for a in alerts}
        assert AlertLevel.MEDIUM in levels

    def test_alerts_recorded_in_history(self):
        scorer = MemoryQualityScorer()
        qs = QualityScore(
            atom_id="x",
            consistency=0.1,
            coherence=0.1,
            relevance=0.1,
            freshness=0.1,
            accuracy=0.1,
            overall=0.1,
        )
        scorer.check_alerts(qs)
        assert len(scorer._alert_history) > 0


# ---------------------------------------------------------------------------
# Auto-pause
# ---------------------------------------------------------------------------


class TestShouldPause:
    def test_no_pause_when_scores_are_good(self):
        scorer = MemoryQualityScorer()
        for i in range(10):
            scorer._score_history.append(
                QualityScore(
                    atom_id=str(i),
                    consistency=0.8,
                    coherence=0.8,
                    relevance=0.8,
                    freshness=0.8,
                    accuracy=0.8,
                    overall=0.8,
                )
            )
        should, reason = scorer.should_pause()
        assert should is False
        assert reason == ""

    def test_pause_after_consecutive_low_scores(self):
        scorer = MemoryQualityScorer()
        for i in range(5):
            scorer._score_history.append(
                QualityScore(
                    atom_id=str(i),
                    consistency=0.2,
                    coherence=0.2,
                    relevance=0.2,
                    freshness=0.2,
                    accuracy=0.2,
                    overall=0.2,
                )
            )
        should, reason = scorer.should_pause()
        assert should is True
        assert "连续" in reason
        assert str(0.30) in reason

    def test_pause_when_enough_scores_but_not_all_low(self):
        scorer = MemoryQualityScorer()
        for i in range(5):
            overall = 0.2 if i < 3 else 0.8  # only 3 consecutive low
            scorer._score_history.append(
                QualityScore(
                    atom_id=str(i),
                    consistency=overall,
                    coherence=overall,
                    relevance=overall,
                    freshness=overall,
                    accuracy=overall,
                    overall=overall,
                )
            )
        should, reason = scorer.should_pause()
        assert should is False, f"Only 3/5 low — should not pause, got: {reason}"

    def test_pause_from_critical_alerts_in_one_hour(self):
        scorer = MemoryQualityScorer()
        now = time.time()
        # Add 2 CRITICAL alerts in the last 30 minutes
        scorer._alert_history.append(
            QualityAlert(
                level=AlertLevel.CRITICAL,
                dimension="overall",
                score=0.1,
                threshold=0.3,
                message="critical",
                suggestion="fix",
                timestamp=now - 1800,  # 30 min ago
            )
        )
        scorer._alert_history.append(
            QualityAlert(
                level=AlertLevel.CRITICAL,
                dimension="coherence",
                score=0.1,
                threshold=0.3,
                message="critical",
                suggestion="fix",
                timestamp=now - 600,  # 10 min ago
            )
        )
        should, reason = scorer.should_pause()
        assert should is True
        assert "严重告警" in reason

    def test_old_critical_alerts_dont_trigger_pause(self):
        scorer = MemoryQualityScorer()
        now = time.time()
        # Add 2 CRITICAL alerts from 2 hours ago (outside the 1-hour window)
        scorer._alert_history.append(
            QualityAlert(
                level=AlertLevel.CRITICAL,
                dimension="overall",
                score=0.1,
                threshold=0.3,
                message="critical",
                suggestion="fix",
                timestamp=now - 7200,  # 2 hours ago
            )
        )
        scorer._alert_history.append(
            QualityAlert(
                level=AlertLevel.CRITICAL,
                dimension="coherence",
                score=0.1,
                threshold=0.3,
                message="critical",
                suggestion="fix",
                timestamp=now - 7500,  # 2+ hours ago
            )
        )
        should, reason = scorer.should_pause()
        assert should is False, f"Old alerts should not trigger pause, got: {reason}"


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class TestGetStats:
    def test_empty_stats(self):
        scorer = MemoryQualityScorer()
        stats = scorer.get_stats()
        assert stats["total_scored"] == 0
        assert stats["paused"] is False
        assert stats["recent_scores"] == []

    def test_stats_after_scoring(self):
        scorer = MemoryQualityScorer()
        for i in range(5):
            atom = _make_atom(
                id=f"atom-{i}",
                content=f"测试内容 {i}",
                source_type="admin_command",
                verified=True,
            )
            scorer.score_atom(atom)
        stats = scorer.get_stats()
        assert stats["total_scored"] == 5
        assert "avg_overall" in stats
        assert "avg_consistency" in stats
        assert "avg_coherence" in stats
        assert "avg_relevance" in stats
        assert "avg_freshness" in stats
        assert "avg_accuracy" in stats
        assert stats["paused"] is False
        assert len(stats["recent_scores"]) <= 10
        assert all(
            isinstance(v, float)
            for v in stats["recent_scores"][0].values()
            if v != stats["recent_scores"][0].get("atom_id")
        )

    def test_stats_reflects_pause_state(self):
        scorer = MemoryQualityScorer()
        scorer._paused = True
        scorer._pause_reason = "test reason"
        stats = scorer.get_stats()
        assert stats["paused"] is True
        assert stats["pause_reason"] == "test reason"

    def test_alert_counts_in_stats(self):
        scorer = MemoryQualityScorer()
        scorer._alert_history.append(
            QualityAlert(
                level=AlertLevel.CRITICAL,
                dimension="x",
                score=0.1,
                threshold=0.3,
                message="m",
                suggestion="s",
            )
        )
        scorer._alert_history.append(
            QualityAlert(
                level=AlertLevel.HIGH,
                dimension="x",
                score=0.4,
                threshold=0.45,
                message="m",
                suggestion="s",
            )
        )
        scorer._alert_history.append(
            QualityAlert(
                level=AlertLevel.CRITICAL,
                dimension="x",
                score=0.2,
                threshold=0.3,
                message="m",
                suggestion="s",
            )
        )
        stats = scorer.get_stats()
        assert stats["alert_counts"].get("critical") == 2
        assert stats["alert_counts"].get("high") == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_atom_with_missing_fields(self):
        scorer = MemoryQualityScorer()
        score = scorer.score_atom({"id": "minimal"})
        assert score.atom_id == "minimal"
        assert 0.0 <= score.overall <= 1.0

    def test_atom_with_ttl_key_variant(self):
        scorer = MemoryQualityScorer()
        atom_ttl = _make_atom(ttl=60.0)
        # Remove ttl_days to test fallback
        atom_ttl.pop("ttl_days", None)
        score = scorer.score_atom(atom_ttl)
        assert 0.0 <= score.freshness <= 1.0

    def test_context_with_embedding_uses_cosine(self):
        scorer = MemoryQualityScorer()
        atom = _make_atom(content="Python 编程")
        context = _make_context(
            existing_atoms=[
                {
                    "content": "Python 编程入门",
                    "embedding": _text_to_simple_embedding("Python 编程入门"),
                }
            ]
        )
        score = scorer.score_atom(atom, context=context)
        assert 0.0 <= score.consistency <= 1.0

    def test_window_overflow_evicts_oldest(self):
        scorer = MemoryQualityScorer(window_size=5)
        for i in range(10):
            atom = _make_atom(id=f"atom-{i}")
            scorer.score_atom(atom)
        assert len(scorer._score_history) == 5
        # Oldest should be evicted — atom-0 through atom-4 gone
        ids = [s.atom_id for s in scorer._score_history]
        assert "atom-0" not in ids
        assert "atom-9" in ids
