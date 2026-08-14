"""人设情绪状态 — 情绪-记忆双向反馈循环。

实现了回忆情绪记忆会反馈到当前心情的认知原理
(心境一致性记忆, Bower 1981)。
"""

from __future__ import annotations

from dataclasses import dataclass

# 回忆驱动情绪变化时应用的每种情感的效价增量。
_VALENCE_MAP: dict[str, float] = {
    "joy": 0.15,
    "excited": 0.20,
    "grateful": 0.12,
    "happy": 0.12,
    "love": 0.15,
    "proud": 0.10,
    "hopeful": 0.08,
    "relieved": 0.08,
    "sad": -0.15,
    "angry": -0.20,
    "anxious": -0.10,
    "frustrated": -0.12,
    "disappointed": -0.10,
    "fear": -0.15,
    "guilty": -0.12,
    "embarrassed": -0.08,
    "nostalgic": 0.05,
    "neutral": 0.0,
}


@dataclass
class MoodState:
    """运行时人设情绪，随回忆的记忆而变化。

    属性:
        valence: -1.0 (负面) 至 +1.0 (正面)。
        arousal: 0.0 (平静) 至 1.0 (兴奋)。
        dominant_emotion: 最近的主导情感标签。
    """

    valence: float = 0.0
    arousal: float = 0.5
    dominant_emotion: str = "neutral"

    # ----

    def apply_recall_delta(self, recalled_emotions: list[str]) -> None:
        """基于回忆记忆的情感标签来调整情绪。

        每个标签贡献其效价增量。效价被限制在 [-1.0, 1.0]
        以防止失控漂移。
        """
        if not recalled_emotions:
            return

        total_delta = 0.0
        for tag in recalled_emotions:
            tag_lower = tag.lower().strip()
            delta = _VALENCE_MAP.get(tag_lower, 0.0)
            total_delta += delta
            if delta != 0.0:
                self.dominant_emotion = tag_lower

        self.valence = max(-1.0, min(1.0, self.valence + total_delta))
        # 唤醒度跟随 |效价| — 强烈情绪带来高唤醒度
        self.arousal = 0.5 + 0.5 * abs(self.valence)

    def decay_toward_neutral(self, rate: float = 0.10) -> None:
        """每回合以 *rate* 速率向中性回归情绪。

        防止单次高情感记忆导致情绪锁定。
        """
        self.valence *= 1.0 - rate
        self.arousal = self.arousal * (1.0 - rate) + 0.5 * rate
        if abs(self.valence) < 0.02:
            self.dominant_emotion = "neutral"

    # ----

    @property
    def mood_label(self) -> str:
        """人类可读的情绪描述。"""
        if self.valence > 0.3:
            return "excited_happy" if self.arousal > 0.7 else "calm_happy"
        if self.valence < -0.3:
            return "upset" if self.arousal > 0.7 else "sad"
        if self.arousal > 0.7:
            return "alert"
        return "neutral"


__all__ = ["MoodState", "_VALENCE_MAP"]
