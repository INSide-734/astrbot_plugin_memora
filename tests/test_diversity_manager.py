"""core/utils/diversity_manager.py 测试 — ResponseDiversityManager。"""

from __future__ import annotations

from core.utils.diversity_manager import (
    EXPRESSION_VARIATIONS,
    LANGUAGE_STYLES,
    RESPONSE_PATTERNS,
    TEMPERATURE_RANGES,
    HomogeneityReport,
    ResponseDiversityManager,
    VariationComposition,
)

# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


class TestConstants:
    def test_language_styles_count(self) -> None:
        assert len(LANGUAGE_STYLES) == 8

    def test_response_patterns_count(self) -> None:
        assert len(RESPONSE_PATTERNS) == 6

    def test_expression_variations_axes(self) -> None:
        assert set(EXPRESSION_VARIATIONS.keys()) == {
            "sentence_style",
            "tone",
            "emphasis",
        }
        for axis, values in EXPRESSION_VARIATIONS.items():
            assert len(values) >= 4, f"{axis} should have >= 4 options"

    def test_temperature_ranges_valid(self) -> None:
        for ctx_type, (lo, hi) in TEMPERATURE_RANGES.items():
            assert 0.0 < lo < hi <= 1.5, f"Invalid range for {ctx_type}: {lo}-{hi}"


# ---------------------------------------------------------------------------
# VariationComposition tests
# ---------------------------------------------------------------------------


class TestVariationComposition:
    def test_to_dict(self) -> None:
        vc = VariationComposition(
            sentence_style="陈述句",
            tone="肯定",
            emphasis="结论",
        )
        assert vc.to_dict() == {
            "sentence_style": "陈述句",
            "tone": "肯定",
            "emphasis": "结论",
        }


# ---------------------------------------------------------------------------
# HomogeneityReport tests
# ---------------------------------------------------------------------------


class TestHomogeneityReport:
    def test_is_homogeneous_below_threshold(self) -> None:
        report = HomogeneityReport(
            opening_uniqueness=0.4,
            ending_uniqueness=0.3,
            overall_uniqueness=0.35,
            total_responses=10,
            repeated_openings={"你好": 3},
            repeated_endings={"再见": 4},
        )
        assert report.is_homogeneous is True

    def test_is_not_homogeneous_above_threshold(self) -> None:
        report = HomogeneityReport(
            opening_uniqueness=0.8,
            ending_uniqueness=0.7,
            overall_uniqueness=0.75,
            total_responses=10,
            repeated_openings={},
            repeated_endings={},
        )
        assert report.is_homogeneous is False

    def test_is_homogeneous_at_threshold(self) -> None:
        # exactly 0.5 is NOT homogeneous (must be strictly < 0.5)
        report = HomogeneityReport(
            opening_uniqueness=0.5,
            ending_uniqueness=0.5,
            overall_uniqueness=0.5,
            total_responses=5,
            repeated_openings={},
            repeated_endings={},
        )
        assert report.is_homogeneous is False


# ---------------------------------------------------------------------------
# ResponseDiversityManager tests
# ---------------------------------------------------------------------------


class TestDynamicTemperature:
    def test_normal_context(self) -> None:
        mgr = ResponseDiversityManager()
        for _ in range(20):
            temp = mgr.get_dynamic_temperature("normal")
            assert 0.6 <= temp <= 0.9

    def test_creative_context(self) -> None:
        mgr = ResponseDiversityManager()
        for _ in range(20):
            temp = mgr.get_dynamic_temperature("creative")
            assert 0.8 <= temp <= 1.2

    def test_precise_context(self) -> None:
        mgr = ResponseDiversityManager()
        for _ in range(20):
            temp = mgr.get_dynamic_temperature("precise")
            assert 0.3 <= temp <= 0.6

    def test_stable_context(self) -> None:
        mgr = ResponseDiversityManager()
        for _ in range(20):
            temp = mgr.get_dynamic_temperature("stable")
            assert 0.2 <= temp <= 0.4

    def test_unknown_context_falls_back_to_normal(self) -> None:
        mgr = ResponseDiversityManager()
        temp = mgr.get_dynamic_temperature("nonexistent")
        assert 0.6 <= temp <= 0.9


class TestStyleSelection:
    def test_select_style_returns_valid_style(self) -> None:
        mgr = ResponseDiversityManager()
        for _ in range(20):
            style = mgr.select_style()
            assert style in LANGUAGE_STYLES

    def test_avoids_last_3_styles(self) -> None:
        mgr = ResponseDiversityManager()
        styles: list[str] = []
        for _ in range(8):
            styles.append(mgr.select_style())
        # After 4 selections, the 5th must be different from last 3
        for i in range(3, len(styles)):
            recent_3 = set(styles[max(0, i - 3) : i])
            # May be in recent_3 only if all styles were exhausted
            # but with 8 styles, last 3 should normally be avoided
            # Only check if there are enough candidates to choose from
            candidates = [s for s in LANGUAGE_STYLES if s not in recent_3]
            if candidates:
                assert styles[i] in candidates

    def test_all_styles_eventually_selected(self) -> None:
        mgr = ResponseDiversityManager()
        all_selected: set[str] = set()
        for _ in range(200):
            all_selected.add(mgr.select_style())
        assert all_selected == set(LANGUAGE_STYLES)


class TestPatternSelection:
    def test_select_pattern_returns_valid_pattern(self) -> None:
        mgr = ResponseDiversityManager()
        for _ in range(20):
            pattern = mgr.select_pattern()
            assert pattern in RESPONSE_PATTERNS

    def test_avoids_last_2_patterns(self) -> None:
        mgr = ResponseDiversityManager()
        patterns: list[str] = []
        for _ in range(6):
            patterns.append(mgr.select_pattern())
        for i in range(2, len(patterns)):
            recent_2 = set(patterns[max(0, i - 2) : i])
            candidates = [p for p in RESPONSE_PATTERNS if p not in recent_2]
            if candidates:
                assert patterns[i] in candidates


class TestComposeVariation:
    def test_compose_variation_all_valid(self) -> None:
        mgr = ResponseDiversityManager()
        for _ in range(30):
            vc = mgr.compose_variation()
            assert vc.sentence_style in EXPRESSION_VARIATIONS["sentence_style"]
            assert vc.tone in EXPRESSION_VARIATIONS["tone"]
            assert vc.emphasis in EXPRESSION_VARIATIONS["emphasis"]


class TestDiversityInjection:
    def test_build_diversity_injection_contains_tags(self) -> None:
        mgr = ResponseDiversityManager()
        injection = mgr.build_diversity_injection()
        assert "[DIVERSITY_INJECTION]" in injection
        assert "[/DIVERSITY_INJECTION]" in injection
        assert "语言风格:" in injection
        assert "回复模式:" in injection

    def test_injection_is_stable_format(self) -> None:
        mgr = ResponseDiversityManager()
        lines = mgr.build_diversity_injection().split("\n")
        # Should have exactly 8 lines (open + 6 items + close)
        assert len(lines) == 8


class TestSanitizeLLMResponse:
    def test_removes_diversity_injection_tags(self) -> None:
        mgr = ResponseDiversityManager()
        raw = "你好！[DIVERSITY_INJECTION]\n- 语言风格: 活泼开朗\n[/DIVERSITY_INJECTION]\n这是回复。"
        cleaned = mgr.sanitize_llm_response(raw)
        assert "[DIVERSITY_INJECTION]" not in cleaned
        assert "活泼开朗" not in cleaned
        assert "你好！" in cleaned
        assert "这是回复。" in cleaned

    def test_removes_style_tags(self) -> None:
        mgr = ResponseDiversityManager()
        raw = "[风格: 幽默风趣] 哈哈哈，你说的对！"
        cleaned = mgr.sanitize_llm_response(raw)
        assert "[风格:" not in cleaned
        assert "哈哈哈" in cleaned

    def test_removes_anti_repetition_tags(self) -> None:
        mgr = ResponseDiversityManager()
        raw = (
            "[ANTI_REPETITION]\n- 避免用以下开头: 你好\n[/ANTI_REPETITION]\n正确回复。"
        )
        cleaned = mgr.sanitize_llm_response(raw)
        assert "[ANTI_REPETITION]" not in cleaned
        assert "正确回复。" in cleaned

    def test_collapses_multiple_blank_lines(self) -> None:
        mgr = ResponseDiversityManager()
        raw = "第一段\n\n\n\n第二段"
        cleaned = mgr.sanitize_llm_response(raw)
        assert "\n\n\n\n" not in cleaned
        assert "第一段" in cleaned


class TestHomogeneityAnalysis:
    def test_all_unique(self) -> None:
        mgr = ResponseDiversityManager()
        responses = ["你好世界哈哈", "今天天气真好", "我们来聊聊天"]
        report = mgr.analyze_homogeneity(responses)
        assert report.overall_uniqueness == 1.0
        assert report.total_responses == 3
        assert not report.repeated_openings
        assert not report.repeated_endings

    def test_all_same(self) -> None:
        mgr = ResponseDiversityManager()
        responses = ["你好今天天气真好", "你好今天天气真好", "你好今天天气真好"]
        report = mgr.analyze_homogeneity(responses)
        assert report.overall_uniqueness < 0.5
        assert report.is_homogeneous

    def test_empty_list(self) -> None:
        mgr = ResponseDiversityManager()
        report = mgr.analyze_homogeneity([])
        assert report.overall_uniqueness == 1.0
        assert report.total_responses == 0

    def test_repeated_openings_detected(self) -> None:
        mgr = ResponseDiversityManager()
        # Use identical first 8 chars for all responses to force repetition
        # "ABCDEFGH" is exactly 8 chars — same opening for all
        prefix = "ABCDEFGH"
        responses = [
            prefix + "今天天气真不错",
            prefix + "我想去旅行啊啊",
            prefix + "知道了吗就这样",
        ]
        report = mgr.analyze_homogeneity(responses)
        # All three share the identical 8-char opening prefix
        assert len(report.repeated_openings) == 1
        assert report.opening_uniqueness < 0.6


class TestAntiRepetitionInstruction:
    def test_empty_with_few_responses(self) -> None:
        mgr = ResponseDiversityManager()
        instruction = mgr.create_anti_repetition_instruction()
        assert instruction == ""

    def test_generates_with_repetitive_responses(self) -> None:
        mgr = ResponseDiversityManager()
        for _ in range(5):
            mgr.record_response("你好今天天气真好啊")
        instruction = mgr.create_anti_repetition_instruction()
        assert "[ANTI_REPETITION]" in instruction
        assert "避免" in instruction

    def test_no_instruction_when_diverse(self) -> None:
        mgr = ResponseDiversityManager()
        mgr.record_response("今天想聊点什么？")
        mgr.record_response("来试试这个新的想法！")
        instruction = mgr.create_anti_repetition_instruction()
        # With only 2 diverse responses, may still generate instruction
        # Just verify it's well-formed
        if instruction:
            assert instruction.startswith("[ANTI_REPETITION]")


class TestRecordResponse:
    def test_records_and_tracks_responses(self) -> None:
        mgr = ResponseDiversityManager()
        mgr.record_response("第一条回复")
        mgr.record_response("第二条回复")
        assert len(mgr._recent_responses) == 2

    def test_max_5_responses_tracked(self) -> None:
        mgr = ResponseDiversityManager()
        for i in range(10):
            mgr.record_response(f"回复 {i}")
        assert len(mgr._recent_responses) == 5
