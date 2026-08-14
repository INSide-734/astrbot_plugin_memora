"""回复多样性管理器：管理风格轮换、反重复与动态温度。"""

from __future__ import annotations

import random
import re
from collections import deque
from dataclasses import dataclass

from astrbot.api import logger

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

LANGUAGE_STYLES: tuple[str, ...] = (
    "简洁直接",
    "温和友善",
    "活泼开朗",
    "幽默风趣",
    "深思熟虑",
    "随性自然",
    "文艺范",
    "二次元风格",
)

RESPONSE_PATTERNS: tuple[str, ...] = (
    "直接回答型",
    "引导思考型",
    "幽默调侃型",
    "详细解释型",
    "简短点评型",
    "情感共鸣型",
)

EXPRESSION_VARIATIONS: dict[str, tuple[str, ...]] = {
    "sentence_style": ("陈述句", "疑问句", "感叹句", "短句", "混合"),
    "tone": ("肯定", "推测", "反问", "建议", "平实"),
    "emphasis": ("结论", "过程", "感受", "事实", "无"),
}

# 各上下文类型对应的温度区间
TEMPERATURE_RANGES: dict[str, tuple[float, float]] = {
    "creative": (0.8, 1.2),
    "normal": (0.6, 0.9),
    "precise": (0.3, 0.6),
    "stable": (0.2, 0.4),
}

# 可能泄露到 LLM 回复中的注入标记
_LEAK_PATTERNS: list[re.Pattern] = [
    re.compile(r"\[系统指令:.*?\]", re.DOTALL),
    re.compile(r"\[DIVERSITY_INJECTION\].*?\[/DIVERSITY_INJECTION\]", re.DOTALL),
    re.compile(r"\[风格:.*?\]", re.DOTALL),
    re.compile(r"\[回复模式:.*?\]", re.DOTALL),
    re.compile(r"\[多样性指令\].*?\[/多样性指令\]", re.DOTALL),
    re.compile(r"请用.*?风格回答", re.DOTALL),
    re.compile(r"\[ANTI_REPETITION\].*?\[/ANTI_REPETITION\]", re.DOTALL),
    re.compile(r"\[REPETITION_RULE\].*?\[/REPETITION_RULE\]", re.DOTALL),
    re.compile(r"避免使用.*?开头", re.DOTALL),
    re.compile(r"不要重复.*?句式", re.DOTALL),
    re.compile(r"禁止使用.*?作为结尾", re.DOTALL),
]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class VariationComposition:
    """一次具体的表达变体组合。"""

    sentence_style: str
    tone: str
    emphasis: str

    def to_dict(self) -> dict[str, str]:
        return {
            "sentence_style": self.sentence_style,
            "tone": self.tone,
            "emphasis": self.emphasis,
        }


@dataclass
class HomogeneityReport:
    """回复同质化分析结果。"""

    opening_uniqueness: float
    ending_uniqueness: float
    overall_uniqueness: float
    total_responses: int
    repeated_openings: dict[str, int]
    repeated_endings: dict[str, int]

    @property
    def is_homogeneous(self) -> bool:
        """当整体唯一性低于 0.5 时返回 ``True``。"""
        return self.overall_uniqueness < 0.5


# ---------------------------------------------------------------------------
# ResponseDiversityManager
# ---------------------------------------------------------------------------


class ResponseDiversityManager:
    """通过风格轮换、模式变化、反重复规则和动态温度提升回复多样性。"""

    def __init__(self):
        self._recent_styles: deque[str] = deque(maxlen=3)
        self._recent_patterns: deque[str] = deque(maxlen=2)
        self._recent_responses: deque[str] = deque(maxlen=5)

    # ------------------------------------------------------------------
    # 动态温度
    # ------------------------------------------------------------------

    def get_dynamic_temperature(self, context_type: str = "normal") -> float:
        """根据上下文类型返回对应区间内的动态温度。"""
        ranges = TEMPERATURE_RANGES.get(context_type)
        if ranges is None:
            logger.warning(
                f"[Diversity] 未知 context_type='{context_type}'，回退到 'normal'"
            )
            ranges = TEMPERATURE_RANGES["normal"]
        lo, hi = ranges
        return round(random.uniform(lo, hi), 3)

    # ------------------------------------------------------------------
    # 风格与模式选择
    # ------------------------------------------------------------------

    def select_style(self) -> str:
        """选择一个语言风格，并排除最近使用过的 3 个风格。"""
        excluded = set(self._recent_styles)
        candidates = [s for s in LANGUAGE_STYLES if s not in excluded]
        if not candidates:
            # 如果近期都用过，则从全部风格中随机选择
            candidates = list(LANGUAGE_STYLES)
        chosen = random.choice(candidates)
        self._recent_styles.append(chosen)
        return chosen

    def select_pattern(self) -> str:
        """选择一个回复模式，并排除最近使用过的 2 个模式。"""
        excluded = set(self._recent_patterns)
        candidates = [p for p in RESPONSE_PATTERNS if p not in excluded]
        if not candidates:
            candidates = list(RESPONSE_PATTERNS)
        chosen = random.choice(candidates)
        self._recent_patterns.append(chosen)
        return chosen

    # ------------------------------------------------------------------
    # 变体组合
    # ------------------------------------------------------------------

    def compose_variation(self) -> VariationComposition:
        """随机生成一个三轴表达变体组合。"""
        return VariationComposition(
            sentence_style=random.choice(EXPRESSION_VARIATIONS["sentence_style"]),
            tone=random.choice(EXPRESSION_VARIATIONS["tone"]),
            emphasis=random.choice(EXPRESSION_VARIATIONS["emphasis"]),
        )

    # ------------------------------------------------------------------
    # 多样性注入 Prompt
    # ------------------------------------------------------------------

    def build_diversity_injection(self) -> str:
        """构建用于引导 LLM 生成多样化回复的注入 Prompt。"""
        style = self.select_style()
        pattern = self.select_pattern()
        variation = self.compose_variation()
        temperature = self.get_dynamic_temperature()

        parts: list[str] = [
            "[DIVERSITY_INJECTION]",
            f"- 语言风格: {style}",
            f"- 回复模式: {pattern}",
            f"- 句式偏好: {variation.sentence_style}",
            f"- 语气偏好: {variation.tone}",
            f"- 表达重点: {variation.emphasis}",
            f"- 创意温度: {temperature}",
            "[/DIVERSITY_INJECTION]",
        ]
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 注入内容清理
    # ------------------------------------------------------------------

    def sanitize_llm_response(self, response: str) -> str:
        """清理 LLM 输出中意外泄露的注入标记。"""
        cleaned = response
        for pattern in _LEAK_PATTERNS:
            cleaned = pattern.sub("", cleaned)
        # 合并多余空行
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    # ------------------------------------------------------------------
    # 同质化分析
    # ------------------------------------------------------------------

    def analyze_homogeneity(self, recent_responses: list[str]) -> HomogeneityReport:
        """分析最近一组回复的同质化程度。"""
        if not recent_responses:
            return HomogeneityReport(
                opening_uniqueness=1.0,
                ending_uniqueness=1.0,
                overall_uniqueness=1.0,
                total_responses=0,
                repeated_openings={},
                repeated_endings={},
            )

        total = len(recent_responses)

        # 提取开头和结尾（并标准化空白）
        openings: list[str] = []
        endings: list[str] = []
        for resp in recent_responses:
            text = resp.strip()
            if not text:
                continue
            openings.append(text[:8])
            endings.append(text[-8:])

        # 统计重复次数
        opening_counts: dict[str, int] = {}
        ending_counts: dict[str, int] = {}
        for op in openings:
            opening_counts[op] = opening_counts.get(op, 0) + 1
        for ed in endings:
            ending_counts[ed] = ending_counts.get(ed, 0) + 1

        # 唯一性比例 = 唯一开头或结尾数量 / 总数
        opening_unique = len(opening_counts) / total if total > 0 else 1.0
        ending_unique = len(ending_counts) / total if total > 0 else 1.0
        overall = (opening_unique + ending_unique) / 2.0

        # 仅保留重复项
        repeated_openings = {k: v for k, v in opening_counts.items() if v > 1}
        repeated_endings = {k: v for k, v in ending_counts.items() if v > 1}

        return HomogeneityReport(
            opening_uniqueness=round(opening_unique, 3),
            ending_uniqueness=round(ending_unique, 3),
            overall_uniqueness=round(overall, 3),
            total_responses=total,
            repeated_openings=repeated_openings,
            repeated_endings=repeated_endings,
        )

    # ------------------------------------------------------------------
    # 反重复指令
    # ------------------------------------------------------------------

    def create_anti_repetition_instruction(self) -> str:
        """基于最近回复生成反重复指令。"""
        if len(self._recent_responses) < 2:
            return ""

        report = self.analyze_homogeneity(list(self._recent_responses))
        lines: list[str] = ["[ANTI_REPETITION]"]

        if report.repeated_openings:
            repeated = list(report.repeated_openings.keys())[:3]
            quoted = "、".join(f'"{o}"' for o in repeated)
            lines.append(f"- 避免用以下开头: {quoted}")

        if report.repeated_endings:
            repeated = list(report.repeated_endings.keys())[:3]
            quoted = "、".join(f'"{e}"' for e in repeated)
            lines.append(f"- 避免用以下结尾: {quoted}")

        if report.is_homogeneous:
            lines.append("- 整体风格过于单一，请尝试不同的句式和表达方式")

        lines.append("[/ANTI_REPETITION]")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 记录回复
    # ------------------------------------------------------------------

    def record_response(self, response: str) -> None:
        """记录一条回复，供后续反重复分析使用。"""
        self._recent_responses.append(response.strip())


__all__ = [
    "ResponseDiversityManager",
    "LANGUAGE_STYLES",
    "RESPONSE_PATTERNS",
    "EXPRESSION_VARIATIONS",
    "TEMPERATURE_RANGES",
    "VariationComposition",
    "HomogeneityReport",
]
