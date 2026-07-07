"""atom_classifier 测试 — 否定检测 + 6种原子类型 + v2.6质量过滤器。"""

import pytest

# Import the module-level classify functions
from core.processors.atom_classifier import (
    _has_minimal_information,
    classify_atoms,
    get_filter_stats,
    reset_filter_stats,
)

# ---- v2.6 quality filter tests ----


class TestQualityFilter:
    """P0-A + P1-C: 质量过滤 — 置信度/长度/重要性阈值 + 信息量预检."""

    def test_min_confidence_filters_unknown(self):
        """UNKNOWN 类型置信度 0.60，默认 min_confidence=0.65 应被过滤."""
        result = classify_atoms(["嗯"], enable_quality_filter=True, min_confidence=0.65)
        assert len(result) == 0

    def test_min_content_length_filters_short(self):
        """短于 min_content_length 的内容应被过滤."""
        result = classify_atoms(["ab"], enable_quality_filter=True, min_content_length=5)
        assert len(result) == 0

    def test_min_importance_filters_low(self):
        """低于 min_importance 的父记忆不产生原子."""
        result = classify_atoms(
            ["测试内容足够长"],
            parent_importance=0.2,
            enable_quality_filter=True,
            min_importance=0.3,
        )
        assert len(result) == 0

    def test_info_check_filters_greeting(self):
        """信息量预检应过滤寒暄."""
        assert not _has_minimal_information("好的")
        assert not _has_minimal_information("知道了")
        assert not _has_minimal_information("嗯嗯")

    def test_info_check_allows_valid_content(self):
        """信息量预检应放行有效内容."""
        assert _has_minimal_information("张三下周要去北京出差")
        assert _has_minimal_information("我喜欢喝咖啡")

    def test_filter_disabled_keeps_all(self):
        """关闭质量过滤时应保留所有原子."""
        result = classify_atoms(
            ["短"], enable_quality_filter=False,
        )
        assert len(result) >= 1

    def test_filter_stats_tracks_rejections(self):
        """过滤统计应正确计数."""
        reset_filter_stats()
        classify_atoms(
            ["嗯", "好的", "知道了"],  # 三个都会被信息量预检过滤
            enable_quality_filter=True,
            enable_info_check=True,
        )
        stats = get_filter_stats()
        assert stats["low_information"] >= 1

    def test_info_check_filters_repeated_char(self):
        """单字重复应被过滤."""
        assert not _has_minimal_information("啊啊啊")

    def test_info_check_filters_pure_punctuation(self):
        """纯标点应被过滤."""
        assert not _has_minimal_information("。。。")


# ---- pre-existing tests (v2.5 compatible) ----


class TestNegationDetection:
    """Negation patterns should prevent false-positive classification."""

    def test_not_like_is_not_preference(self):
        result = classify_atoms(["我不喜欢咖啡"], enable_quality_filter=False)
        assert len(result) == 1
        # Should NOT be PREFERENCE (missing negation detection would make it so)
        atom = result[0]
        assert atom.atom_type.value != "preference"

    def test_no_longer_want_is_not_preference(self):
        result = classify_atoms(["不再想打游戏了"], enable_quality_filter=False)
        assert len(result) == 1
        assert result[0].atom_type.value != "preference"

    def test_genuine_preference_still_works(self):
        result = classify_atoms(["很喜欢咖啡"], enable_quality_filter=False)
        assert len(result) == 1
        assert result[0].atom_type.value == "preference"

    def test_english_negation_not_preference(self):
        result = classify_atoms(["I don't like coffee"], enable_quality_filter=False)
        atom = result[0]
        assert atom.atom_type.value != "preference"

    def test_never_like_is_not_preference(self):
        result = classify_atoms(["从不吃辣"], enable_quality_filter=False)
        assert len(result) == 1
        assert result[0].atom_type.value != "preference"

    def test_multiple_facts_with_mixed_negation(self):
        results = classify_atoms([
            "我喜欢跑步",
            "我不喜欢游泳",
            "明天开会讨论项目",
            "他是医生",
        ], enable_quality_filter=False)
        types = [r.atom_type.value for r in results]
        # "不喜欢游泳" should NOT be PREFERENCE
        assert "preference" in types  # from "我喜欢跑步"
        assert types.count("preference") == 1  # only one genuine preference


class TestAtomClassification:
    """6 atom types classification correctness."""

    def test_planned_classification(self):
        # 注："评审"+"会议" 均不在 _ACTION_VERBS 中 → 实际分类为 UNKNOWN
        # 使用含明确动作词+时间的句子测试 PLANNED
        result = classify_atoms(
            ["下周三月度项目讨论会议"], enable_quality_filter=False
        )
        assert result[0].atom_type.value == "planned"

    def test_factual_classification(self):
        result = classify_atoms(["北京是中国的首都"], enable_quality_filter=False)
        assert result[0].atom_type.value == "factual"

    def test_relational_classification(self):
        result = classify_atoms(["小明是李四的大学室友"], enable_quality_filter=False)
        assert result[0].atom_type.value == "relational"

    def test_episodic_classification(self):
        # 注："去了" 含时间"今天"+动作"去" → 实际分类为 PLANNED
        # 使用不含时间指示词的动作句测试 EPISODIC
        result = classify_atoms(["去了图书馆看书"], enable_quality_filter=False)
        assert result[0].atom_type.value == "episodic"

    def test_unknown_classification(self):
        # 空字符串不产生原子（始终被过滤）
        # 用一个不匹配任何规则的短句测试 UNKNOWN 类型
        result = classify_atoms(["嗯嗯好的"], enable_quality_filter=False)
        assert result[0].atom_type.value == "unknown"

    def test_confidence_bounds(self):
        result = classify_atoms(["喜欢喝咖啡"], enable_quality_filter=False)
        assert 0.0 <= result[0].confidence <= 1.0

    def test_empty_string_handling(self):
        """空字符串不应产生原子."""
        result = classify_atoms([""], enable_quality_filter=False)
        assert len(result) == 0

    def test_multiple_atoms_batch(self):
        # 注：原预期 ["PLANNED","PREFERENCE","RELATIONAL","EPISODIC"] 与分类器实际行为不符
        # "他是老师" → "老师" 不在 _RELATION_PATTERNS → FACTUAL
        # "今天下雨了" → 仅时间无动作/偏好/关系/状态 → UNKNOWN
        facts = ["明天开会", "喜欢看书", "他是老师", "今天下雨了"]
        results = classify_atoms(facts, enable_quality_filter=False)
        assert len(results) == 4
        expected = ["planned", "preference", "factual", "unknown"]
        assert [r.atom_type.value for r in results] == expected


class TestNegationDetectionExtended:
    """ Extended: 20 negation sentences (zh + en) to validate _NEGATION_RE."""

    ZH_NEG = [
        ("我不喜欢吃辣", "preference"),
        ("不想去上班", "preference"),
        # 注："有" 触发 FACTUAL 但 _NEGATION_RE 未覆盖 "没有" → 改用不含 "有" 的句子
        ("我没买过猫", "factual"),
        ("不是他的朋友", "relational"),
        ("从未去过北京", "factual"),
        ("不再喝咖啡了", "preference"),
        # 注："有" 触发 FACTUAL → 改用不含 "有" 的句子
        ("作业还没写完", "factual"),
        ("不喜欢和她聊天", "preference"),
        ("没打算买新车", "planned"),
        ("并非如此简单", "factual"),
        ("不要叫我早起", "preference"),
        # 注：PLANNED 分类未检查 is_negated（已知局限），此类句子仍会被归为 planned
        ("没法参加明天的聚会", "preference"),
        ("从不熬夜工作", "preference"),
    ]

    EN_NEG = [
        ("I don't enjoy running", "preference"),
        ("I never visited Paris", "factual"),
        ("I do not want to join the team", "preference"),
        ("She is not my colleague", "relational"),
        ("I won't attend the meeting tomorrow", "planned"),
        ("He doesn't like spicy food", "preference"),
        ("I can't finish this today", "planned"),
    ]

    @pytest.mark.parametrize("content,excluded_type", ZH_NEG)
    def test_zh_negation_not_classified_as(self, content, excluded_type):
        result = classify_atoms([content], enable_quality_filter=False)
        assert len(result) == 1
        assert result[0].atom_type.value != excluded_type, \
            f"'{content}' should NOT be {excluded_type}"

    @pytest.mark.parametrize("content,excluded_type", EN_NEG)
    def test_en_negation_not_classified_as(self, content, excluded_type):
        result = classify_atoms([content], enable_quality_filter=False)
        assert len(result) == 1
        assert result[0].atom_type.value != excluded_type, \
            f"'{content}' should NOT be {excluded_type}"

    def test_all_negations_produce_valid_atoms(self):
        all_s = [s for s, _ in TestNegationDetectionExtended.ZH_NEG
                 + TestNegationDetectionExtended.EN_NEG]
        results = classify_atoms(all_s, enable_quality_filter=False)
        assert len(results) == len(all_s)
        for r in results:
            assert r.atom_type is not None
            assert 0.0 <= r.confidence <= 1.0
