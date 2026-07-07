"""测试 core/utils/style_analyzer.py — StyleAnalyzer, StyleProfile, StyleEvolution."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from core.utils.style_analyzer import (
    StyleAnalyzer,
    StyleEvolution,
    StyleProfile,
    _7_DIMENSIONS,
)


# ---------------------------------------------------------------------------
# StyleProfile tests
# ---------------------------------------------------------------------------


class TestStyleProfile:
    def test_default_values(self) -> None:
        profile = StyleProfile()
        assert profile.vocabulary_richness == 0.5
        assert profile.sentence_complexity == 0.5
        assert profile.emotional_expression == 0.5
        assert profile.interaction_tendency == 0.5
        assert profile.topic_diversity == 0.5
        assert profile.formality_level == 0.5
        assert profile.creativity_score == 0.5

    def test_custom_values(self) -> None:
        profile = StyleProfile(
            vocabulary_richness=0.8,
            sentence_complexity=0.3,
            emotional_expression=0.9,
            interaction_tendency=0.4,
            topic_diversity=0.6,
            formality_level=0.7,
            creativity_score=0.2,
        )
        assert profile.vocabulary_richness == 0.8
        assert profile.creativity_score == 0.2

    def test_to_dict(self) -> None:
        profile = StyleProfile(vocabulary_richness=0.9)
        d = profile.to_dict()
        assert d == {
            "vocabulary_richness": 0.9,
            "sentence_complexity": 0.5,
            "emotional_expression": 0.5,
            "interaction_tendency": 0.5,
            "topic_diversity": 0.5,
            "formality_level": 0.5,
            "creativity_score": 0.5,
        }

    def test_from_dict_full(self) -> None:
        data = {
            "vocabulary_richness": 0.7,
            "sentence_complexity": 0.6,
            "emotional_expression": 0.4,
            "interaction_tendency": 0.3,
            "topic_diversity": 0.8,
            "formality_level": 0.5,
            "creativity_score": 0.9,
        }
        profile = StyleProfile.from_dict(data)
        assert profile.vocabulary_richness == 0.7
        assert profile.creativity_score == 0.9

    def test_from_dict_clamps_values(self) -> None:
        data = {
            "vocabulary_richness": 1.5,
            "sentence_complexity": -0.5,
            "emotional_expression": "not_a_number",
            "interaction_tendency": None,
            "topic_diversity": 0.8,
            "formality_level": 0.5,
            "creativity_score": 0.9,
        }
        profile = StyleProfile.from_dict(data)
        assert profile.vocabulary_richness == 1.0
        assert profile.sentence_complexity == 0.0
        assert profile.emotional_expression == 0.5  # invalid → default
        assert profile.interaction_tendency == 0.5

    def test_from_dict_missing_keys_defaults(self) -> None:
        data: dict = {}
        profile = StyleProfile.from_dict(data)
        assert profile.to_dict() == StyleProfile().to_dict()

    def test_dimension_deltas(self) -> None:
        old = StyleProfile(vocabulary_richness=0.5)
        new = StyleProfile(vocabulary_richness=0.8)
        deltas = old.dimension_deltas(new)
        assert deltas["vocabulary_richness"] == pytest.approx(0.3)
        assert deltas["sentence_complexity"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# StyleEvolution tests
# ---------------------------------------------------------------------------


class TestStyleEvolution:
    def test_from_profiles(self) -> None:
        old = StyleProfile(vocabulary_richness=0.5, sentence_complexity=0.5)
        new = StyleProfile(vocabulary_richness=0.9, sentence_complexity=0.3)
        evolution = StyleEvolution.from_profiles(old, new)
        assert evolution.dimension_deltas["vocabulary_richness"] == pytest.approx(0.4)
        assert evolution.dimension_deltas["sentence_complexity"] == pytest.approx(-0.2)
        assert len(evolution.dimension_deltas) == 7

    def test_significance_calculation(self) -> None:
        old = StyleProfile()
        new = StyleProfile(vocabulary_richness=1.0, emotional_expression=0.0)
        # deltas: v=0.5, s=0, e=-0.5, i=0, t=0, f=0, c=0
        # sum(abs) = 0.5 + 0 + 0.5 = 1.0
        # significance = 1.0 / 7
        evolution = StyleEvolution.from_profiles(old, new)
        assert evolution.significance == pytest.approx(1.0 / 7, abs=0.001)

    def test_timestamp_set(self) -> None:
        old = StyleProfile()
        new = StyleProfile()
        evolution = StyleEvolution.from_profiles(old, new)
        now = time.time()
        assert abs(evolution.timestamp - now) < 5.0


# ---------------------------------------------------------------------------
# StyleAnalyzer tests
# ---------------------------------------------------------------------------


class TestConfidence:
    def test_base_confidence(self) -> None:
        confidence = StyleAnalyzer.compute_confidence(0, 0)
        assert confidence == 0.5

    def test_message_count_bonus_20(self) -> None:
        confidence = StyleAnalyzer.compute_confidence(20, 0)
        assert confidence == 0.6

    def test_message_count_bonus_50(self) -> None:
        confidence = StyleAnalyzer.compute_confidence(50, 0)
        assert confidence == 0.7

    def test_message_count_bonus_100(self) -> None:
        confidence = StyleAnalyzer.compute_confidence(100, 0)
        assert confidence == 0.8

    def test_length_bonus_20(self) -> None:
        confidence = StyleAnalyzer.compute_confidence(0, 20)
        assert confidence == 0.6

    def test_length_bonus_50(self) -> None:
        confidence = StyleAnalyzer.compute_confidence(0, 50)
        assert confidence == 0.7

    def test_combined_bonuses_capped(self) -> None:
        confidence = StyleAnalyzer.compute_confidence(100, 50)
        assert confidence == 1.0  # capped at 1.0

    def test_no_overflow(self) -> None:
        confidence = StyleAnalyzer.compute_confidence(200, 100)
        assert 0.0 <= confidence <= 1.0


class TestHeuristicProfile:
    def test_empty_messages(self) -> None:
        analyzer = StyleAnalyzer()
        profile = analyzer._heuristic_profile([], "")
        assert profile.to_dict() == StyleProfile().to_dict()

    def test_simple_messages(self) -> None:
        analyzer = StyleAnalyzer()
        messages = [
            "你好",
            "今天天气真不错",
            "我们一起去吃饭吧！",
        ]
        profile = analyzer._heuristic_profile(messages, "\n---\n".join(messages))
        assert 0.0 <= profile.vocabulary_richness <= 1.0
        assert 0.0 <= profile.emotional_expression <= 1.0

    def test_diverse_messages(self) -> None:
        analyzer = StyleAnalyzer()
        messages = [
            "苹果是一种水果",
            "量子力学非常有趣",
            "周末去打篮球吗？",
            "编程语言有很多种选择",
            "今天中午吃什么好呢",
        ]
        profile = analyzer._heuristic_profile(messages, "\n---\n".join(messages))
        assert profile.topic_diversity > 0.0
        assert profile.creativity_score > 0.0


class TestDetectEvolution:
    def test_detect_evolution_adds_to_history(self) -> None:
        analyzer = StyleAnalyzer()
        old = StyleProfile(vocabulary_richness=0.3)
        new = StyleProfile(vocabulary_richness=0.7)
        evolution = analyzer.detect_evolution(old, new)
        assert isinstance(evolution, StyleEvolution)
        assert len(analyzer._evolution_history) == 1

    def test_multiple_evolutions_chain(self) -> None:
        analyzer = StyleAnalyzer()
        p1 = StyleProfile(vocabulary_richness=0.3)
        p2 = StyleProfile(vocabulary_richness=0.7)
        p3 = StyleProfile(vocabulary_richness=0.5)
        analyzer.detect_evolution(p1, p2)
        analyzer.detect_evolution(p2, p3)
        assert len(analyzer._evolution_history) == 2


class TestTrendsAnalysis:
    def test_insufficient_data(self) -> None:
        analyzer = StyleAnalyzer()
        trends = analyzer.get_trends([])
        for dim in _7_DIMENSIONS:
            assert trends[dim]["direction"] == "stable"
            assert trends[dim]["volatility"] == 0.0

    def test_single_evolution(self) -> None:
        analyzer = StyleAnalyzer()
        old = StyleProfile(vocabulary_richness=0.5)
        new = StyleProfile(vocabulary_richness=0.9)
        evolution = StyleEvolution.from_profiles(old, new)
        trends = analyzer.get_trends([evolution])
        assert trends["vocabulary_richness"]["direction"] == "up"
        assert trends["vocabulary_richness"]["net_delta"] == pytest.approx(0.4)
        # Other dims have 0 delta
        assert trends["sentence_complexity"]["direction"] == "stable"

    def test_direction_down(self) -> None:
        analyzer = StyleAnalyzer()
        old = StyleProfile(emotional_expression=0.8)
        new = StyleProfile(emotional_expression=0.2)
        evolution = StyleEvolution.from_profiles(old, new)
        trends = analyzer.get_trends([evolution])
        assert trends["emotional_expression"]["direction"] == "down"
        assert trends["emotional_expression"]["net_delta"] == pytest.approx(-0.6)

    def test_volatility_with_multiple_evolutions(self) -> None:
        analyzer = StyleAnalyzer()
        evolutions: list[StyleEvolution] = []
        # Rising vocabulary richness over 3 steps
        base = StyleProfile()
        for val in [0.6, 0.8, 0.4, 0.9]:
            next_prof = StyleProfile(vocabulary_richness=val)
            evolutions.append(StyleEvolution.from_profiles(base, next_prof))
            base = next_prof
        trends = analyzer.get_trends(evolutions)
        # Net should be upward (0.5 -> 0.9 = +0.4)
        assert trends["vocabulary_richness"]["direction"] == "up"
        assert trends["vocabulary_richness"]["volatility"] > 0.0

    def test_all_dimensions_present(self) -> None:
        analyzer = StyleAnalyzer()
        old = StyleProfile()
        new = StyleProfile(vocabulary_richness=0.8)
        evolution = StyleEvolution.from_profiles(old, new)
        trends = analyzer.get_trends([evolution])
        assert set(trends.keys()) == set(_7_DIMENSIONS)
        for dim in _7_DIMENSIONS:
            assert "label" in trends[dim]
            assert "direction" in trends[dim]
            assert "net_delta" in trends[dim]
            assert "volatility" in trends[dim]


class TestAnalyzeWithLLM:
    @pytest.mark.asyncio
    async def test_no_llm_falls_back_to_heuristic(self) -> None:
        analyzer = StyleAnalyzer(llm_callable=None)
        profile = await analyzer.analyze(["你好", "今天天气真好"])
        assert isinstance(profile, StyleProfile)
        assert profile.vocabulary_richness > 0.0

    @pytest.mark.asyncio
    async def test_with_llm_qual_only(self) -> None:
        qual_response = (
            '{"vocabulary_richness":0.8,"sentence_complexity":0.6,'
            '"emotional_expression":0.4,"interaction_tendency":0.3,'
            '"topic_diversity":0.7,"formality_level":0.5,'
            '"creativity_score":0.9,"rationale":"分析理由"}'
        )
        mock_llm = AsyncMock(return_value=qual_response)
        analyzer = StyleAnalyzer(llm_callable=mock_llm)
        profile = await analyzer.analyze(["你好", "今天天气真好"])
        assert profile.vocabulary_richness == pytest.approx(0.8)
        assert profile.creativity_score == pytest.approx(0.9)
        # Both LLM calls should be made (qual + quant — same mock returns valid JSON)
        assert mock_llm.call_count >= 1  # at least one call succeeded

    @pytest.mark.asyncio
    async def test_with_llm_both_fail_falls_back(self) -> None:
        mock_llm = AsyncMock(side_effect=Exception("LLM down"))
        analyzer = StyleAnalyzer(llm_callable=mock_llm)
        profile = await analyzer.analyze(["你好"])
        assert isinstance(profile, StyleProfile)

    @pytest.mark.asyncio
    async def test_with_llm_one_branch_invalid_uses_valid_branch(self) -> None:
        valid_response = (
            '{"vocabulary_richness":0.2,"sentence_complexity":0.3,'
            '"emotional_expression":0.4,"interaction_tendency":0.5,'
            '"topic_diversity":0.6,"formality_level":0.7,'
            '"creativity_score":0.8}'
        )
        mock_llm = AsyncMock(side_effect=["not json", valid_response])
        analyzer = StyleAnalyzer(llm_callable=mock_llm)

        profile = await analyzer.analyze(["你好", "我们继续测试"])

        assert profile.vocabulary_richness == pytest.approx(0.2)
        assert profile.creativity_score == pytest.approx(0.8)
        assert mock_llm.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_messages_llm(self) -> None:
        mock_llm = AsyncMock()
        analyzer = StyleAnalyzer(llm_callable=mock_llm)
        profile = await analyzer.analyze([])
        assert profile.to_dict() == StyleProfile().to_dict()
        mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_parse_from_code_fence(self) -> None:
        response = (
            "```json\n"
            '{"vocabulary_richness":0.6,"sentence_complexity":0.4,'
            '"emotional_expression":0.7,"interaction_tendency":0.5,'
            '"topic_diversity":0.3,"formality_level":0.8,'
            '"creativity_score":0.2}\n'
            "```"
        )
        profile = StyleAnalyzer._parse_profile(response)
        assert profile is not None
        assert profile.vocabulary_richness == 0.6
        assert profile.formality_level == 0.8

    def test_parse_bare_json(self) -> None:
        response = (
            '{"vocabulary_richness":0.55,"sentence_complexity":0.45,'
            '"emotional_expression":0.65,"interaction_tendency":0.35,'
            '"topic_diversity":0.75,"formality_level":0.25,'
            '"creativity_score":0.85}'
        )
        profile = StyleAnalyzer._parse_profile(response)
        assert profile is not None
        assert profile.topic_diversity == 0.75

    def test_parse_invalid_returns_none(self) -> None:
        profile = StyleAnalyzer._parse_profile("这不是 JSON")
        assert profile is None

    def test_parse_empty_returns_none(self) -> None:
        profile = StyleAnalyzer._parse_profile("")
        assert profile is None
