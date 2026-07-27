"""记忆原子质量评分器：五维统计评分，不依赖 LLM。"""

from __future__ import annotations

import hashlib
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# 枚举类型
# ---------------------------------------------------------------------------


class AlertLevel(Enum):
    """四级告警严重度，语义与 self_learning 的质量监控保持一致。"""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    INFO = "info"


# ---------------------------------------------------------------------------
# 数据载体类
# ---------------------------------------------------------------------------


@dataclass
class QualityScore:
    """单个记忆原子的五维质量评分结果。"""

    atom_id: str
    consistency: float  # 取值范围：[0.0, 1.0]
    coherence: float  # 取值范围：[0.0, 1.0]
    relevance: float  # 取值范围：[0.0, 1.0]
    freshness: float  # 取值范围：[0.0, 1.0]
    accuracy: float  # 取值范围：[0.0, 1.0]
    overall: float  # 加权总分
    timestamp: float = field(default_factory=time.time)


@dataclass
class QualityAlert:
    """当某个维度分数低于阈值时触发的告警。"""

    level: AlertLevel
    dimension: str
    score: float
    threshold: float
    message: str
    suggestion: str
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# 来源类型可靠性/权重表（纯统计常量）
# ---------------------------------------------------------------------------

# 准确性：该来源类型的可靠性高低
_SOURCE_RELIABILITY: dict[str, float] = {
    "admin_command": 0.95,
    "admin": 0.95,
    "private_chat": 0.75,
    "private": 0.75,
    "group_chat": 0.55,
    "group": 0.55,
}

# 相关性基础权重：来源类型对相关性的先验强度
_SOURCE_RELEVANCE_WEIGHT: dict[str, float] = {
    "admin_command": 1.0,
    "admin": 1.0,
    "private_chat": 0.8,
    "private": 0.8,
    "group_chat": 0.6,
    "group": 0.6,
}

# ---------------------------------------------------------------------------
# 正向/负向情感词表（中文）
# ---------------------------------------------------------------------------

_POSITIVE_WORDS: frozenset[str] = frozenset(
    {
        "开心",
        "高兴",
        "喜欢",
        "爱",
        "很棒",
        "优秀",
        "赞",
        "好",
        "满意",
        "愉快",
        "幸福",
        "感谢",
        "感动",
        "温暖",
        "美好",
        "成功",
        "厉害",
        "牛逼",
        "佩服",
        "欣赏",
        "祝福",
        "期待",
    }
)

_NEGATIVE_WORDS: frozenset[str] = frozenset(
    {
        "伤心",
        "难过",
        "生气",
        "讨厌",
        "恨",
        "糟糕",
        "差",
        "坏",
        "不满",
        "愤怒",
        "悲哀",
        "痛苦",
        "失望",
        "焦虑",
        "害怕",
        "失败",
        "垃圾",
        "恶心",
        "烦",
        "崩溃",
        "绝望",
        "后悔",
    }
)

# ---------------------------------------------------------------------------
# 逻辑连接词标记（连贯性信号）
# ---------------------------------------------------------------------------

_CAUSAL_CONNECTORS: frozenset[str] = frozenset(
    {
        "因为",
        "所以",
        "因此",
        "由于",
        "因而",
        "结果",
        "导致",
    }
)

_CONTRAST_CONNECTORS: frozenset[str] = frozenset(
    {
        "但是",
        "然而",
        "可是",
        "不过",
        "虽然",
        "尽管",
        "却",
    }
)

_COORDINATION_CONNECTORS: frozenset[str] = frozenset(
    {
        "而且",
        "并且",
        "同时",
        "另外",
        "此外",
        "还",
        "也",
        "以及",
    }
)


# ---------------------------------------------------------------------------
# 核心评分器
# ---------------------------------------------------------------------------


class MemoryQualityScorer:
    """纯统计记忆原子质量评分器。"""

    # 总分中各维度的权重
    _WEIGHTS: dict[str, float] = {
        "consistency": 0.25,
        "coherence": 0.25,
        "relevance": 0.20,
        "freshness": 0.15,
        "accuracy": 0.15,
    }

    # 告警阈值（低于即触发）
    _CRITICAL_THRESHOLD: float = 0.30
    _HIGH_THRESHOLD: float = 0.45
    _MEDIUM_THRESHOLD: float = 0.60

    # 自动暂停条件
    _PAUSE_CONSECUTIVE_LOW: int = 5  # 连续综合分低于严重阈值的次数
    _PAUSE_CRITICAL_WINDOW_S: float = 3600.0  # 最近 1 小时窗口
    _PAUSE_CRITICAL_COUNT: int = 2  # 1 小时内严重告警达到该数量即暂停

    def __init__(self, window_size: int = 100) -> None:
        """初始化质量评分器。"""
        self._score_history: deque[QualityScore] = deque(maxlen=window_size)
        self._alert_history: deque[QualityAlert] = deque(maxlen=200)
        self._thresholds: dict[str, float] = {
            **self._WEIGHTS
        }  # 各维度阈值，当前暂未启用，保留给动态调节
        self._paused: bool = False
        self._pause_reason: str = ""

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def score_atom(
        self, atom: dict[str, Any], context: dict[str, Any] | None = None
    ) -> QualityScore:
        """为单个 ``MemoryAtom`` 计算质量评分。"""
        atom_id = str(atom.get("id") or atom.get("atom_id", "unknown"))
        content = str(atom.get("content", ""))
        source_type = str(atom.get("source_type", "group_chat")).lower()
        created_at = float(atom.get("created_at", time.time()))
        ttl_raw = atom.get("ttl_days") if "ttl_days" in atom else atom.get("ttl")
        ttl = float(ttl_raw) if ttl_raw is not None else 30.0
        verified = bool(atom.get("verified", False))

        consistency = self._score_consistency(content, context)
        coherence = self._score_coherence(content)
        relevance = self._score_relevance(content, source_type, context)
        freshness = self._score_freshness(created_at, ttl)
        accuracy = self._score_accuracy(source_type, content, verified)

        overall = (
            consistency * self._WEIGHTS["consistency"]
            + coherence * self._WEIGHTS["coherence"]
            + relevance * self._WEIGHTS["relevance"]
            + freshness * self._WEIGHTS["freshness"]
            + accuracy * self._WEIGHTS["accuracy"]
        )

        score = QualityScore(
            atom_id=atom_id,
            consistency=round(consistency, 4),
            coherence=round(coherence, 4),
            relevance=round(relevance, 4),
            freshness=round(freshness, 4),
            accuracy=round(accuracy, 4),
            overall=round(overall, 4),
        )
        self._score_history.append(score)
        return score

    # ------------------------------------------------------------------
    # 各维度评分函数
    # ------------------------------------------------------------------

    def _score_consistency(self, content: str, context: dict[str, Any] | None) -> float:
        """根据与现有原子的最大语义重叠度评估一致性。"""
        if not context or not content.strip():
            return 0.8  # 无上下文时给一个中性基线

        existing_atoms: list[dict[str, Any]] = context.get("existing_atoms", [])
        if not existing_atoms:
            return 0.8

        tokens_new = set(_tokenize(content))
        if not tokens_new:
            return 0.8

        max_similarity = 0.0
        for existing in existing_atoms:
            existing_content = str(existing.get("content", ""))
            if not existing_content.strip():
                continue

            # 若存在向量表示，则优先使用余弦相似度
            emb = existing.get("embedding")
            if emb is not None:
                sim = _cosine_similarity(_text_to_simple_embedding(content), emb)
            else:
                tokens_existing = set(_tokenize(existing_content))
                if not tokens_existing:
                    continue
                intersection = len(tokens_new & tokens_existing)
                union = len(tokens_new | tokens_existing)
                sim = intersection / union if union > 0 else 0.0

            if sim > max_similarity:
                max_similarity = sim

        # 1 - max_similarity：重叠越高，一致性越低（更像重复内容）
        return round(1.0 - max_similarity, 4)

    def _score_coherence(self, content: str) -> float:
        """使用结构化启发式规则评估文本内部连贯性。"""
        if not content.strip():
            return 0.0

        length = len(content)
        score = 0.5  # 基线分

        # --- 长度惩罚 ---
        if length < 10:
            score -= 0.3  # 过短，可能只是碎片
        elif length > 2000:
            score -= 0.2  # 过长，可能过于松散
        elif 50 <= length <= 800:
            score += 0.15  # 较理想长度区间

        # --- 逻辑连接词加分 ---
        connector_count = _count_connectors(content)
        if connector_count >= 2:
            score += 0.15
        elif connector_count == 1:
            score += 0.08

        # --- 矛盾情感检测 ---
        if _has_contradictory_sentiment(content):
            score -= 0.2

        # --- 基于分段的结构分析 ---
        if _has_paragraph_breaks(content):
            score += 0.1
        elif _has_multiple_sentences(content):
            score += 0.05

        return max(0.0, min(1.0, round(score, 4)))

    def _score_relevance(
        self, content: str, source_type: str, context: dict[str, Any] | None
    ) -> float:
        """评估内容与当前对话上下文的相关性。"""
        base_weight = _SOURCE_RELEVANCE_WEIGHT.get(source_type, 0.6)

        if not context:
            return base_weight

        recent_messages: list[str] = context.get("recent_messages", [])
        if not recent_messages:
            return base_weight

        # 通过简化的词重叠方式估计与近期消息的相似度
        content_tokens = set(_tokenize(content))
        if not content_tokens:
            return base_weight

        # 构造一个简化的上下文词集合
        all_context_tokens: list[str] = []
        for msg in recent_messages:
            all_context_tokens.extend(_tokenize(msg))

        context_token_set = set(all_context_tokens)
        if not context_token_set:
            return base_weight

        intersection = len(content_tokens & context_token_set)
        jaccard = (
            intersection / len(content_tokens | context_token_set)
            if (content_tokens | context_token_set)
            else 0.0
        )

        # 混合：60% 来源先验 + 40% 上下文匹配
        return round(0.6 * base_weight + 0.4 * jaccard, 4)

    def _score_freshness(self, created_at: float, ttl_days: float) -> float:
        """根据剩余 TTL 比例评估新鲜度。"""
        if ttl_days <= 0:
            return 0.0

        now = time.time()
        age_seconds = max(0.0, now - created_at)
        ttl_seconds = ttl_days * 86400.0
        remaining = max(0.0, ttl_seconds - age_seconds)
        ratio = remaining / ttl_seconds

        # 超过半衰期后加速衰减
        if ratio <= 0.5:
            freshness = ratio * 0.7  # 后半段下降更陡
        else:
            freshness = 0.35 + ratio * 0.65  # 前半段下降更平缓

        return round(max(0.0, min(1.0, freshness)), 4)

    def _score_accuracy(self, source_type: str, content: str, verified: bool) -> float:
        """基于来源可靠性表和附加信号评估准确性。

        加分项：
        - 内容中包含 URL 或引用信息：+0.10
        - ``verified`` 标记为真：+0.20
        """
        base = _SOURCE_RELIABILITY.get(source_type, 0.55)

        # URL / 引用加分
        has_url = _has_url(content)
        if has_url:
            base += 0.10

        # 已验证加分
        if verified:
            base += 0.20

        return round(max(0.0, min(1.0, base)), 4)

    # ------------------------------------------------------------------
    # 告警系统
    # ------------------------------------------------------------------

    def check_alerts(self, score: QualityScore) -> list[QualityAlert]:
        """检查评分结果，并为低分维度生成告警。

        每个维度都会与严重、高、中三级阈值进行比较。
        触发的告警会追加到 ``_alert_history`` 中。

        返回：
            当前评分触发的告警对象列表。
        """
        alerts: list[QualityAlert] = []

        dimensions: dict[str, float] = {
            "consistency": score.consistency,
            "coherence": score.coherence,
            "relevance": score.relevance,
            "freshness": score.freshness,
            "accuracy": score.accuracy,
            "overall": score.overall,
        }

        for dim, val in dimensions.items():
            level = self._classify_level(val)
            if level is not None:
                alert = QualityAlert(
                    level=level,
                    dimension=dim,
                    score=val,
                    threshold=self._threshold_for_level(level),
                    message=(
                        f"记忆原子 {score.atom_id} 的 {dim} 得分为 {val:.3f}，"
                        f"低于阈值 {self._threshold_for_level(level):.2f}"
                    ),
                    suggestion=self._suggestion_for_dimension(dim, val),
                )
                alerts.append(alert)
                self._alert_history.append(alert)

        return alerts

    def _classify_level(self, score: float) -> AlertLevel | None:
        """根据分数返回告警级别；若高于所有阈值则返回 ``None``。"""
        if score < self._CRITICAL_THRESHOLD:
            return AlertLevel.CRITICAL
        if score < self._HIGH_THRESHOLD:
            return AlertLevel.HIGH
        if score < self._MEDIUM_THRESHOLD:
            return AlertLevel.MEDIUM
        return None

    def _threshold_for_level(self, level: AlertLevel) -> float:
        """返回指定告警级别对应的阈值。"""
        mapping = {
            AlertLevel.CRITICAL: self._CRITICAL_THRESHOLD,
            AlertLevel.HIGH: self._HIGH_THRESHOLD,
            AlertLevel.MEDIUM: self._MEDIUM_THRESHOLD,
        }
        return mapping.get(level, 1.0)

    @staticmethod
    def _suggestion_for_dimension(dimension: str, _score: float) -> str:
        """返回指定低分维度对应的中文处理建议。"""
        suggestions: dict[str, str] = {
            "consistency": "建议检查去重逻辑，新记忆可能与已有内容冲突或重复。",
            "coherence": "内容可能存在碎片化或自相矛盾，建议复核抽取质量。",
            "relevance": "该记忆可能偏题，建议检查上下文窗口或调整相关性阈值。",
            "freshness": "该记忆的存活时长即将耗尽，建议强化或归档。",
            "accuracy": "来源可靠性偏低，建议补充验证或降低权重。",
            "overall": "整体质量较差，建议复核记忆抽取链路。",
        }
        return suggestions.get(dimension, "建议复核记忆抽取链路。")

    # ------------------------------------------------------------------
    # 自动暂停逻辑
    # ------------------------------------------------------------------

    def should_pause(self) -> tuple[bool, str]:
        """判断是否需要暂停记忆处理流程。

        满足以下任一条件时触发暂停：
        1. 最近 ``_PAUSE_CONSECUTIVE_LOW`` 条评分记录的综合分
           全部低于 ``_CRITICAL_THRESHOLD``。
        2. 最近一小时内，``AlertLevel.CRITICAL`` 级别告警数量
           达到 ``_PAUSE_CRITICAL_COUNT``。

        返回：
            ``(是否暂停, 原因)``；未暂停时原因为空字符串。
        """
        # 条件 1：综合分连续过低
        if len(self._score_history) >= self._PAUSE_CONSECUTIVE_LOW:
            recent_scores = list(self._score_history)[-self._PAUSE_CONSECUTIVE_LOW :]
            if all(s.overall < self._CRITICAL_THRESHOLD for s in recent_scores):
                self._paused = True
                self._pause_reason = (
                    f"连续 {len(recent_scores)} 次评分低于 {self._CRITICAL_THRESHOLD}"
                )
                return True, self._pause_reason

        # 条件 2：最近 1 小时内严重告警过多
        one_hour_ago = time.time() - self._PAUSE_CRITICAL_WINDOW_S
        critical_alerts = [
            a
            for a in self._alert_history
            if a.level == AlertLevel.CRITICAL and a.timestamp >= one_hour_ago
        ]
        if len(critical_alerts) >= self._PAUSE_CRITICAL_COUNT:
            self._paused = True
            self._pause_reason = f"1 小时内出现 {len(critical_alerts)} 次严重告警"
            return True, self._pause_reason

        self._paused = False
        self._pause_reason = ""
        return False, ""

    # ------------------------------------------------------------------
    # 统计信息
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """返回当前评分统计信息，供监控和面板展示使用。

        返回字典包含：
        - 各维度平均分与综合平均分
        - 已评分总数、暂停状态、暂停原因
        - 各级别告警计数
        - 最近 10 条评分记录
        """
        # 告警计数始终计算，即使当前没有评分历史
        alert_counts: dict[str, int] = {}
        for a in self._alert_history:
            key = a.level.value
            alert_counts[key] = alert_counts.get(key, 0) + 1

        scores = list(self._score_history)
        n = len(scores)
        if n == 0:
            return {
                "status": "no_samples",
                "total_scored": 0,
                "paused": self._paused,
                "pause_reason": self._pause_reason,
                "alert_counts": alert_counts,
                "recent_scores": [],
            }

        avg_overall = sum(s.overall for s in scores) / n
        avg_consistency = sum(s.consistency for s in scores) / n
        avg_coherence = sum(s.coherence for s in scores) / n
        avg_relevance = sum(s.relevance for s in scores) / n
        avg_freshness = sum(s.freshness for s in scores) / n
        avg_accuracy = sum(s.accuracy for s in scores) / n

        return {
            "status": "ok",
            "avg_overall": round(avg_overall, 4),
            "avg_consistency": round(avg_consistency, 4),
            "avg_coherence": round(avg_coherence, 4),
            "avg_relevance": round(avg_relevance, 4),
            "avg_freshness": round(avg_freshness, 4),
            "avg_accuracy": round(avg_accuracy, 4),
            "total_scored": n,
            "paused": self._paused,
            "pause_reason": self._pause_reason,
            "alert_counts": alert_counts,
            "recent_scores": [
                {
                    "atom_id": s.atom_id,
                    "overall": s.overall,
                    "consistency": s.consistency,
                    "coherence": s.coherence,
                    "relevance": s.relevance,
                    "freshness": s.freshness,
                    "accuracy": s.accuracy,
                }
                for s in scores[-10:]
            ],
        }

    # ------------------------------------------------------------------
    # 魔术方法
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<MemoryQualityScorer scored={len(self._score_history)}"
            f" paused={self._paused}>"
        )


# ---------------------------------------------------------------------------
# 模块私有的纯统计辅助函数
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """委托给共享的双字分词器处理。"""
    from ..utils.text_utils import tokenize_bigrams

    return tokenize_bigrams(text)


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """计算两个向量之间的余弦相似度。"""
    if len(vec_a) != len(vec_b):
        min_len = min(len(vec_a), len(vec_b))
        vec_a = vec_a[:min_len]
        vec_b = vec_b[:min_len]
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    mag_a = math.sqrt(sum(a * a for a in vec_a))
    mag_b = math.sqrt(sum(b * b for b in vec_b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def _text_to_simple_embedding(text: str, dim: int = 64) -> list[float]:
    """从文本生成简化伪向量，用于缺省场景下的兜底比较。

    这里使用字符级 n-gram 哈希。它并不能替代真实向量，
    但在缺少现成向量时，可提供一个稳定且粗粒度的相似度参考。
    """
    if not text.strip():
        return [0.0] * dim
    vec = [0.0] * dim
    tokens = _tokenize(text)
    for i, token in enumerate(tokens):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        h = int.from_bytes(digest[:8], byteorder="big", signed=False) % dim
        vec[h] += 1.0 / (i + 1)  # 位置越靠后，权重越低
    # 归一化
    mag = math.sqrt(sum(v * v for v in vec))
    if mag > 0:
        vec = [v / mag for v in vec]
    return vec


def _count_connectors(text: str) -> int:
    """统计文本中出现了多少类逻辑连接词。"""
    count = 0
    if any(c in text for c in _CAUSAL_CONNECTORS):
        count += 1
    if any(c in text for c in _CONTRAST_CONNECTORS):
        count += 1
    if any(c in text for c in _COORDINATION_CONNECTORS):
        count += 1
    return count


def _has_contradictory_sentiment(text: str) -> bool:
    """检测文本中是否同时出现明显的正向与负向情感词。"""
    has_pos = any(w in text for w in _POSITIVE_WORDS)
    has_neg = any(w in text for w in _NEGATIVE_WORDS)
    return has_pos and has_neg


def _count_segments(text: str) -> int:
    """按类段落边界统计内容段数。

    会基于双换行、句后换行以及结构性标记
    （如项目符号、编号列表）来进行粗粒度切分。
    """
    import re

    # 先按段落断点拆分
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) >= 2:
        return len(paragraphs)

    # 若没有明显段落，再退化为按句子边界估算
    sentences = re.split(r"[。！？.!?\n]", text)
    meaningful = [s.strip() for s in sentences if len(s.strip()) >= 3]
    return max(1, len(meaningful))


def _has_paragraph_breaks(text: str) -> bool:
    """检查文本中是否包含段落级断点（双换行）。"""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return len(paragraphs) >= 2


def _has_multiple_sentences(text: str) -> bool:
    """检查文本中是否包含多个类似句子的边界。"""
    import re

    sentences = re.split(r"[。！？.!?\n]", text)
    meaningful = [s.strip() for s in sentences if len(s.strip()) >= 3]
    return len(meaningful) >= 2


def _has_url(text: str) -> bool:
    """检查文本中是否包含类似 URL 的子串。"""
    import re

    return bool(re.search(r"https?://|www\.", text))
