"""测试 MoodState — 双向情绪-记忆反馈环。"""

from __future__ import annotations

import pytest

from core.managers.mood_state import _VALENCE_MAP, MoodState


class TestMoodStateDefaults:
    """默认构造值测试。"""

    def test_default_values(self) -> None:
        """新创建的 MoodState 具有中性默认值。"""
        m = MoodState()
        assert m.valence == 0.0
        assert m.arousal == 0.5
        assert m.dominant_emotion == "neutral"

    def test_custom_values(self) -> None:
        """MoodState 接受自定义初始值。"""
        m = MoodState(valence=0.8, arousal=0.9, dominant_emotion="joy")
        assert m.valence == 0.8
        assert m.arousal == 0.9
        assert m.dominant_emotion == "joy"


class TestApplyRecallDelta:
    """基于回忆情绪标签的心情变化测试。"""

    def test_no_emotions_no_change(self) -> None:
        """空情绪列表不产生心情变化。"""
        m = MoodState(valence=0.3)
        m.apply_recall_delta([])
        assert m.valence == 0.3
        assert m.dominant_emotion == "neutral"

    def test_positive_emotions_increase_valence(self) -> None:
        """joy 和 happy 标签增加正向愉悦度。"""
        m = MoodState(valence=0.0)
        m.apply_recall_delta(["joy", "happy"])
        # joy=0.15, happy=0.12 → 总计=0.27
        assert m.valence == pytest.approx(0.27)
        assert m.dominant_emotion == "happy"  # 最后一个非零增量

    def test_negative_emotions_decrease_valence(self) -> None:
        """sad 和 angry 标签降低愉悦度。"""
        m = MoodState(valence=0.5)
        m.apply_recall_delta(["sad", "angry"])
        # sad=-0.15, angry=-0.20 → 总计=-0.35; 0.5-0.35=0.15
        assert m.valence == pytest.approx(0.15)

    def test_valence_clamped_at_minus_one(self) -> None:
        """愉悦度被限制在最低 -1.0。"""
        m = MoodState(valence=-0.95)
        m.apply_recall_delta(["angry", "sad", "fear"])  # -0.20-0.15-0.15 = -0.50
        assert m.valence == -1.0

    def test_valence_clamped_at_plus_one(self) -> None:
        """愉悦度被限制在最高 +1.0。"""
        m = MoodState(valence=0.95)
        m.apply_recall_delta(["joy", "excited"])  # +0.35
        assert m.valence == 1.0

    def test_arousal_follows_abs_valence(self) -> None:
        """唤醒度 = 0.5 + 0.5 * |valence|。"""
        m = MoodState(valence=0.6)
        m.apply_recall_delta(["joy"])  # joy=0.15, new valence=0.75
        # arousal = 0.5 + 0.5*|0.75| = 0.875
        assert m.arousal == pytest.approx(0.875)

    def test_dominant_emotion_updates_on_nonzero_delta(self) -> None:
        """主导情绪设置为最后一个非零增量的标签。"""
        m = MoodState()
        m.apply_recall_delta(["joy", "neutral", "sad"])
        # joy: 0.15, neutral: 0.0, sad: -0.15 → dominant = "sad"
        assert m.dominant_emotion == "sad"

    def test_unknown_emotions_ignored(self) -> None:
        """不在 VALENCE_MAP 中的标签产生增量 0。"""
        m = MoodState(valence=0.3)
        m.apply_recall_delta(["nonexistent", "bogus"])
        assert m.valence == 0.3  # 不变

    def test_whitespace_normalized(self) -> None:
        """情绪标签被小写化并去除空白。"""
        m = MoodState(valence=0.0)
        m.apply_recall_delta(["  Joy  ", "HAPPY"])
        assert m.valence == pytest.approx(0.27)  # 0.15 + 0.12

    def test_mixed_emotions_net_delta(self) -> None:
        """混合的正负标签产生净增量。"""
        m = MoodState(valence=0.0)
        m.apply_recall_delta(["joy", "sad", "excited"])
        # joy=0.15, sad=-0.15, excited=0.20 → 净=0.20
        assert m.valence == pytest.approx(0.20)


class TestDecayTowardNeutral:
    """心情向中性回归的测试。"""

    def test_decay_reduces_valence(self) -> None:
        """衰减使愉悦度向 0 移动。"""
        m = MoodState(valence=0.8, arousal=0.9, dominant_emotion="joy")
        m.decay_toward_neutral(rate=0.10)
        assert m.valence == pytest.approx(0.72)  # 0.8 * 0.9
        assert m.arousal == pytest.approx(0.86)  # 0.9*0.9 + 0.5*0.1

    def test_decay_sets_neutral_on_near_zero(self) -> None:
        """当 |valence| < 0.02 时主导情绪重置为 neutral。"""
        m = MoodState(valence=0.01, dominant_emotion="happy")
        m.decay_toward_neutral(rate=0.10)
        assert m.dominant_emotion == "neutral"

    def test_decay_leaves_significant_valence_intact(self) -> None:
        """当 |valence| >= 0.02 时衰减不改变主导情绪。"""
        m = MoodState(valence=0.05, dominant_emotion="happy")
        m.decay_toward_neutral(rate=0.10)
        assert m.dominant_emotion == "happy"


class TestMoodLabel:
    """mood_label 属性测试。"""

    def test_positive_high_arousal(self) -> None:
        """正向愉悦度 + 高唤醒度 → excited_happy。"""
        m = MoodState(valence=0.5, arousal=0.8)
        assert m.mood_label == "excited_happy"

    def test_positive_low_arousal(self) -> None:
        """正向愉悦度 + 低唤醒度 → calm_happy。"""
        m = MoodState(valence=0.5, arousal=0.6)
        assert m.mood_label == "calm_happy"

    def test_negative_high_arousal(self) -> None:
        """负向愉悦度 + 高唤醒度 → upset。"""
        m = MoodState(valence=-0.5, arousal=0.8)
        assert m.mood_label == "upset"

    def test_negative_low_arousal(self) -> None:
        """负向愉悦度 + 低唤醒度 → sad。"""
        m = MoodState(valence=-0.5, arousal=0.6)
        assert m.mood_label == "sad"

    def test_neutral_high_arousal(self) -> None:
        """中性愉悦度 + 高唤醒度 → alert。"""
        m = MoodState(valence=0.0, arousal=0.8)
        assert m.mood_label == "alert"

    def test_neutral_low_arousal(self) -> None:
        """中性愉悦度 + 低唤醒度 → neutral。"""
        m = MoodState(valence=0.0, arousal=0.5)
        assert m.mood_label == "neutral"

    @pytest.mark.parametrize(
        "valence,arousal,expected",
        [
            (0.5, 0.8, "excited_happy"),
            (0.5, 0.3, "calm_happy"),
            (-0.5, 0.8, "upset"),
            (-0.5, 0.3, "sad"),
            (0.0, 0.8, "alert"),
            (0.0, 0.5, "neutral"),
            (0.2, 0.9, "alert"),
            (-0.2, 0.2, "neutral"),
        ],
    )
    def test_mood_label_parametrized(
        self,
        valence: float,
        arousal: float,
        expected: str,
    ) -> None:
        """mood_label 参数化测试，覆盖所有分支。"""
        m = MoodState(valence=valence, arousal=arousal)
        assert m.mood_label == expected


class TestValenceMap:
    """_VALENCE_MAP 常量测试。"""

    def test_positive_valence(self) -> None:
        """正面情绪具有正向愉悦度。"""
        assert _VALENCE_MAP["joy"] > 0
        assert _VALENCE_MAP["excited"] > 0
        assert _VALENCE_MAP["love"] > 0

    def test_negative_valence(self) -> None:
        """负面情绪具有负向愉悦度。"""
        assert _VALENCE_MAP["sad"] < 0
        assert _VALENCE_MAP["angry"] < 0
        assert _VALENCE_MAP["fear"] < 0

    def test_neutral_valence(self) -> None:
        """中性情绪愉悦度为 0。"""
        assert _VALENCE_MAP["neutral"] == 0.0
