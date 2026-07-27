"""风格分析器：基于双路 LLM 调用的七维语言风格画像。"""

from __future__ import annotations

import asyncio
import json
import re
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

# ---------------------------------------------------------------------------
# 维度名称（标准顺序）
# ---------------------------------------------------------------------------

_7_DIMENSIONS: tuple[str, ...] = (
    "vocabulary_richness",
    "sentence_complexity",
    "emotional_expression",
    "interaction_tendency",
    "topic_diversity",
    "formality_level",
    "creativity_score",
)

_DIMENSION_LABELS: dict[str, str] = {
    "vocabulary_richness": "词汇丰富度",
    "sentence_complexity": "句法复杂度",
    "emotional_expression": "情感表达强度",
    "interaction_tendency": "互动倾向",
    "topic_diversity": "话题多样性",
    "formality_level": "正式度",
    "creativity_score": "创意性",
}

# ---------------------------------------------------------------------------
# 定性分析 Prompt 模板
# ---------------------------------------------------------------------------

_QUALITATIVE_PROMPT = """你是一位语言风格分析专家。请分析以下用户消息，提取风格特征。

用户消息:
{joined_messages}

请用中文回复，严格按以下 JSON 格式输出，每个维度取值 0.0-1.0：

{{
  "vocabulary_richness": <0.0-1.0 词汇丰富度>,
  "sentence_complexity": <0.0-1.0 句法复杂度>,
  "emotional_expression": <0.0-1.0 情感表达强度>,
  "interaction_tendency": <0.0-1.0 互动倾向>,
  "topic_diversity": <0.0-1.0 话题多样性>,
  "formality_level": <0.0-1.0 正式度>,
  "creativity_score": <0.0-1.0 创意性>,
  "rationale": "简要分析理由，不超过100字"
}}

注意：
- vocabulary_richness: 词汇多样化程度，用词是否丰富
- sentence_complexity: 句子的复杂程度，长句和复合句的使用
- emotional_expression: 情感表露的强度
- interaction_tendency: 倾向于与他人互动的程度(问句、直接对话)
- topic_diversity: 话题范围的广度
- formality_level: 语言正式程度
- creativity_score: 表达的新颖性和创意程度
"""

# 用于确定性回退的定量评分 Prompt
_QUANTITATIVE_PROMPT = """你是一位语言风格定量分析师。请分析以下用户消息，给出数值评分。

用户消息:
{joined_messages}

请严格按以下 JSON 格式输出（仅 JSON，不要额外文字）：

{{
  "vocabulary_richness": <0.0-1.0>,
  "sentence_complexity": <0.0-1.0>,
  "emotional_expression": <0.0-1.0>,
  "interaction_tendency": <0.0-1.0>,
  "topic_diversity": <0.0-1.0>,
  "formality_level": <0.0-1.0>,
  "creativity_score": <0.0-1.0>
}}
"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class StyleProfile:
    """七维风格画像，所有值均在 0.0 到 1.0 之间。"""

    vocabulary_richness: float = 0.5
    sentence_complexity: float = 0.5
    emotional_expression: float = 0.5
    interaction_tendency: float = 0.5
    topic_diversity: float = 0.5
    formality_level: float = 0.5
    creativity_score: float = 0.5

    def to_dict(self) -> dict[str, float]:
        return {
            "vocabulary_richness": self.vocabulary_richness,
            "sentence_complexity": self.sentence_complexity,
            "emotional_expression": self.emotional_expression,
            "interaction_tendency": self.interaction_tendency,
            "topic_diversity": self.topic_diversity,
            "formality_level": self.formality_level,
            "creativity_score": self.creativity_score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StyleProfile:
        def _clamp(value: Any) -> float:
            try:
                return max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                return 0.5

        return cls(
            vocabulary_richness=_clamp(data.get("vocabulary_richness")),
            sentence_complexity=_clamp(data.get("sentence_complexity")),
            emotional_expression=_clamp(data.get("emotional_expression")),
            interaction_tendency=_clamp(data.get("interaction_tendency")),
            topic_diversity=_clamp(data.get("topic_diversity")),
            formality_level=_clamp(data.get("formality_level")),
            creativity_score=_clamp(data.get("creativity_score")),
        )

    def dimension_deltas(self, other: StyleProfile) -> dict[str, float]:
        """计算当前画像到目标画像的逐维差值。"""
        my = self.to_dict()
        their = other.to_dict()
        return {dim: their[dim] - my[dim] for dim in _7_DIMENSIONS}


@dataclass
class StyleEvolution:
    """记录两次风格快照之间的变化。"""

    old_profile: StyleProfile
    new_profile: StyleProfile
    dimension_deltas: dict[str, float]
    significance: float  # sum(|delta|) / 7
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def from_profiles(cls, old: StyleProfile, new: StyleProfile) -> StyleEvolution:
        deltas = old.dimension_deltas(new)
        significance = round(sum(abs(v) for v in deltas.values()) / 7, 4)
        return cls(
            old_profile=old,
            new_profile=new,
            dimension_deltas=deltas,
            significance=significance,
        )


# ---------------------------------------------------------------------------
# StyleAnalyzer
# ---------------------------------------------------------------------------


class StyleAnalyzer:
    """通过双路并行 LLM 调用执行风格分析。"""

    def __init__(self, llm_callable: Any = None):
        """初始化分析器。"""
        self._llm = llm_callable
        self._evolution_history: list[StyleEvolution] = []

    # ------------------------------------------------------------------
    # 分析
    # ------------------------------------------------------------------

    async def analyze(self, messages: list[str]) -> StyleProfile:
        """分析一组用户消息，并产出风格画像。"""
        if not messages:
            return StyleProfile()

        joined = "\n---\n".join(messages)

        if self._llm is None:
            return self._heuristic_profile(messages, joined)

        try:
            qual_task = asyncio.create_task(
                self._safe_llm_call(_QUALITATIVE_PROMPT.format(joined_messages=joined))
            )
            quant_task = asyncio.create_task(
                self._safe_llm_call(_QUANTITATIVE_PROMPT.format(joined_messages=joined))
            )
            qual_result, quant_result = await asyncio.gather(
                qual_task, quant_task, return_exceptions=True
            )
        except Exception as exc:
            logger.warning(f"[StyleAnalyzer] 双路 LLM 调用失败，使用启发式回退: {exc}")
            return self._heuristic_profile(messages, joined)

        qual_raw = None if isinstance(qual_result, Exception) else qual_result
        quant_raw = None if isinstance(quant_result, Exception) else quant_result

        qual_profile = self._parse_profile(qual_raw) if qual_raw else None
        quant_profile = self._parse_profile(quant_raw) if quant_raw else None

        if qual_profile is not None and quant_profile is not None:
            return self._merge_profiles(qual_profile, quant_profile)
        if qual_profile is not None:
            return qual_profile
        if quant_profile is not None:
            return quant_profile
        return self._heuristic_profile(messages, joined)

    # ------------------------------------------------------------------
    # 演化检测
    # ------------------------------------------------------------------

    def detect_evolution(
        self, baseline: StyleProfile, new: StyleProfile
    ) -> StyleEvolution:
        """检测基线画像与新画像之间的风格漂移。"""
        evolution = StyleEvolution.from_profiles(baseline, new)
        self._evolution_history.append(evolution)
        return evolution

    # ------------------------------------------------------------------
    # 置信度
    # ------------------------------------------------------------------

    @staticmethod
    def compute_confidence(message_count: int, avg_length: float) -> float:
        """根据数据量估算分析置信度。"""
        confidence = 0.5

        if message_count >= 100:
            confidence += 0.3
        elif message_count >= 50:
            confidence += 0.2
        elif message_count >= 20:
            confidence += 0.1

        if avg_length >= 50:
            confidence += 0.2
        elif avg_length >= 20:
            confidence += 0.1

        return round(min(1.0, confidence), 3)

    # ------------------------------------------------------------------
    # 趋势分析
    # ------------------------------------------------------------------

    def get_trends(
        self, evolutions: list[StyleEvolution] | None = None
    ) -> dict[str, Any]:
        """分析演化历史中各维度的变化趋势。"""
        records = evolutions if evolutions is not None else self._evolution_history

        def _build_entry(dim: str, net: float, vol: float) -> dict[str, Any]:
            if abs(net) < 0.05:
                direction = "stable"
            elif net > 0:
                direction = "up"
            else:
                direction = "down"
            return {
                "label": _DIMENSION_LABELS.get(dim, dim),
                "direction": direction,
                "net_delta": round(net, 4),
                "volatility": vol,
            }

        if len(records) == 0:
            return {dim: _build_entry(dim, 0.0, 0.0) for dim in _7_DIMENSIONS}

        # 汇总每个维度在各次演化中的差值
        dim_deltas: dict[str, list[float]] = {dim: [] for dim in _7_DIMENSIONS}
        for evo in records:
            for dim in _7_DIMENSIONS:
                dim_deltas[dim].append(evo.dimension_deltas.get(dim, 0.0))

        trends: dict[str, Any] = {}
        for dim in _7_DIMENSIONS:
            deltas = dim_deltas[dim]
            net = sum(deltas)
            if len(deltas) >= 2:
                vol = round(statistics.stdev(deltas), 4)
            elif len(deltas) == 1:
                vol = round(abs(deltas[0]), 4)
            else:
                vol = 0.0
            trends[dim] = _build_entry(dim, net, vol)

        return trends

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    async def _safe_llm_call(self, prompt: str) -> str | None:
        """带错误处理地调用 LLM；失败时返回 ``None``。"""
        try:
            result = await self._llm(prompt)
            return result if isinstance(result, str) else str(result)
        except Exception as exc:
            logger.debug(f"[StyleAnalyzer] LLM 调用失败: {exc}")
            return None

    @staticmethod
    def _parse_profile(raw: str) -> StyleProfile | None:
        """从 LLM 输出中解析 JSON 风格画像。"""
        try:
            # 先尝试直接解析
            data = json.loads(raw)
        except json.JSONDecodeError:
            # 再尝试从 Markdown 代码块中提取
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                except json.JSONDecodeError:
                    return None
            else:
                # 最后尝试寻找裸露的 JSON 对象
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group(0))
                    except json.JSONDecodeError:
                        return None
                else:
                    return None
        if not isinstance(data, dict):
            return None
        return StyleProfile.from_dict(data)

    @staticmethod
    def _merge_profiles(qual: StyleProfile, quant: StyleProfile) -> StyleProfile:
        """通过简单平均（各 0.5 权重）合并两份画像。"""
        qa = qual.to_dict()
        qn = quant.to_dict()
        merged = {dim: (qa[dim] + qn[dim]) / 2.0 for dim in _7_DIMENSIONS}
        return StyleProfile.from_dict(merged)

    @staticmethod
    def _heuristic_profile(messages: list[str], _joined: str) -> StyleProfile:
        """在 LLM 不可用时使用确定性启发式规则回退。"""
        if not messages:
            return StyleProfile()

        total_len = sum(len(m) for m in messages)
        avg_len = total_len / len(messages)

        # 使用唯一词比例近似衡量词汇丰富度
        all_words = " ".join(messages).split()
        unique_ratio = len(set(all_words)) / len(all_words) if all_words else 0.5

        # 句法复杂度：长度大于 30 的消息占比
        complex_ratio = sum(1 for m in messages if len(m) > 30) / len(messages)

        # 情感表达：感叹号 / emoji / 强情绪词的出现情况
        emotion_markers = re.compile(
            r"[!！？?！]{2,}|[😊😂❤️🔥💔🎉😭]|[好太真非超级非常很极]"
        )
        emotional_ratio = sum(1 for m in messages if emotion_markers.search(m)) / len(
            messages
        )

        # 互动倾向：问句占比
        question_ratio = sum(1 for m in messages if "?" in m or "？" in m) / len(
            messages
        )

        # 话题多样性：用唯一词比例近似
        topic_div = unique_ratio

        # 正式度：平均长度越高，通常越正式（近似）
        formality = min(1.0, avg_len / 80.0) if avg_len > 0 else 0.5

        # 创意性：重复越少，创意性越高
        creativity = unique_ratio

        return StyleProfile(
            vocabulary_richness=round(unique_ratio, 3),
            sentence_complexity=round(complex_ratio, 3),
            emotional_expression=round(emotional_ratio, 3),
            interaction_tendency=round(question_ratio, 3),
            topic_diversity=round(topic_div, 3),
            formality_level=round(formality, 3),
            creativity_score=round(creativity, 3),
        )


__all__ = [
    "StyleProfile",
    "StyleEvolution",
    "StyleAnalyzer",
    "_7_DIMENSIONS",
    "_DIMENSION_LABELS",
]
