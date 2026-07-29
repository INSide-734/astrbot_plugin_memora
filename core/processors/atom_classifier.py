"""基于规则的原子分类器 —— 无需额外调用 LLM。

v2.6: 新增质量过滤（置信度/重要性/长度阈值 + 信息量预检），减少无关联短期聊天记录存储。
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

from astrbot.api import logger

from ..models.memory_atom import AtomType, DecayType, MemoryAtom, compute_ttl

# ---------- 分类模式 ----------

_TIME_INDICATORS = re.compile(
    r"大后天|明天|后天|昨天|前天|今天|"
    r"(?:上周|本周|下下周|下周)?周[一二三四五六日天]|"
    r"上周|本周|下下周|下周|"
    r"下个?月|上个?月|明年|后年|去年|前年|"
    r"\d{1,2}月\d{1,2}[日号]|\d{4}年\d{1,2}月\d{1,2}[日号]?|"
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|"
    r"上午|下午|晚上|凌晨|早上|中午|傍晚|"
    r"\d{1,2}[点时：:]\d{1,2}|"
    r"\b(?:today|tomorrow|yesterday|next\s+week|last\s+week)\b",
    re.IGNORECASE,
)

_ACTION_VERBS = re.compile(
    r"开会|讨论|参加|组织|安排|举办|进行|执行|完成|提交|发送|发布|"
    r"去|来|到|做|要|准备|计划|打算|"
    r"\b(?:go|went|come|came|attend|join|plan|prepare|submit|send|do|did)\b",
    re.IGNORECASE,
)

_STATIVE_PATTERNS = re.compile(
    r"是|有|属于|等于|代表|意味|包含|包括|位于|"
    r"\b(?:is|are|was|were|has|have|belongs?\s+to|means?|contains?)\b",
    re.IGNORECASE,
)

_RELATION_PATTERNS = re.compile(
    r"同事|朋友|同学|家人|亲戚|队友|搭档|伙伴|老板|上司|下属|"
    r"合作|合伙|夫妻|情侣|邻居|室友|老乡|"
    r"\b(?:colleague|coworker|friend|classmate|family|relative|partner|"
    r"boss|manager|neighbor|roommate|spouse)\b",
    re.IGNORECASE,
)

_PREFERENCE_PATTERNS = re.compile(
    r"喜欢|讨厌|爱|不爱|偏好|最爱|不喜欢|热衷于|沉迷|"
    r"爱吃|爱喝|喜欢喝|喜欢去|讨厌吃|讨厌去|"
    r"\b(?:like|likes|love|loves|prefer|prefers|dislike|dislikes|"
    r"hate|hates|favorite)\b",
    re.IGNORECASE,
)

_PERSON_PATTERNS = re.compile(
    r"(?:我|[你您]|[他她它]们?|[A-Z]\w*|[张李王刘陈杨赵黄周吴徐孙胡朱高何]"
    r"[a-zA-Z一-鿿]{1,3})"
)

_NEGATION_RE = re.compile(
    r"(?:不|没|别|未|非|否)(?:是|会|想|喜欢|爱|愿意|觉得|吃|喝|去|再)|"
    r"从(?:不|没|未)|绝不|决不|完全不|"
    r"\b(?:don'?t|doesn'?t|won'?t|can'?t|never|not)\s+\w+|"
    r"\b(?:dislike|dislikes|hate|hates)\b",
    re.IGNORECASE,
)

# ---------- 质量过滤模式 ----------

_LOW_INFO_PATTERNS = re.compile(
    r"^(好的|知道了|嗯+|哦+|哈哈+|嘻嘻|呵呵|嘿嘿|是的|对的|没错|"
    r"可以|行|不行|好哒|ok|OK|Ok|来了|走了|拜拜|再见|"
    r"早$|早安$|晚安$|吃了吗|在吗|"
    r"\d{1,2}[点:：]\d{1,2}了?$|"  # 纯时间 "3点了"
    r"^[。，！？,.!?\s]*$|"  # 纯标点
    r"^[👍🙏😊😂❤️🔥🎉💪]+$)"  # 纯 emoji 表情
)

# 过滤统计（用于调试和监控）
_FILTERED_STATS: dict[str, int] = {
    "too_short": 0,
    "low_confidence": 0,
    "low_importance": 0,
    "low_information": 0,
}

_ATOM_TYPE_HINTS: dict[str, AtomType] = {
    "fact": AtomType.FACTUAL,
    "knowledge": AtomType.FACTUAL,
    "factual": AtomType.FACTUAL,
    "event": AtomType.EPISODIC,
    "episodic": AtomType.EPISODIC,
    "relational": AtomType.RELATIONAL,
    "preference": AtomType.PREFERENCE,
    "planned": AtomType.PLANNED,
    "reflection": AtomType.UNKNOWN,
    "unknown": AtomType.UNKNOWN,
}


def reset_filter_stats() -> dict[str, int]:
    """重置并返回上一次的过滤统计（用于周期性日志输出）。"""
    global _FILTERED_STATS
    prev = dict(_FILTERED_STATS)
    _FILTERED_STATS = {
        "too_short": 0,
        "low_confidence": 0,
        "low_importance": 0,
        "low_information": 0,
    }
    return prev


def get_filter_stats() -> dict[str, int]:
    """获取当前过滤统计快照。"""
    return dict(_FILTERED_STATS)


_WEEKDAY_INDEX = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}


def _parse_weekday_time(text: str, now: float) -> float | None:
    """将中文星期表达解析为目标日期。"""
    match = re.search(r"(?:(上周|本周|下下周|下周)|周)([一二三四五六日天])", text)
    if not match:
        return None

    prefix = match.group(1) or ""
    target_weekday = _WEEKDAY_INDEX[match.group(2)]
    now_dt = datetime.fromtimestamp(now)

    if prefix == "上周":
        days_delta = target_weekday - now_dt.weekday() - 7
    elif prefix == "本周":
        days_delta = target_weekday - now_dt.weekday()
    elif prefix == "下周":
        days_delta = target_weekday - now_dt.weekday() + 7
    elif prefix == "下下周":
        days_delta = target_weekday - now_dt.weekday() + 14
    else:
        days_delta = (target_weekday - now_dt.weekday()) % 7

    return (now_dt + timedelta(days=days_delta)).timestamp()


def _parse_event_time(text: str) -> float | None:
    """尽力从中文时间表达中提取绝对时间戳。

    当缺少 `dateparser` 时，回退到简单的天数偏移启发式。
    """
    now = time.time()
    day_sec = 86400.0

    mapping: tuple[tuple[str, float], ...] = (
        ("大后天", 3 * day_sec),
        ("前天", -2 * day_sec),
        ("昨天", -1 * day_sec),
        ("今天", 0),
        ("明天", 1 * day_sec),
        ("后天", 2 * day_sec),
        ("tomorrow", 1 * day_sec),
        ("yesterday", -1 * day_sec),
        ("today", 0),
    )
    normalized_text = text.casefold()
    for word, offset in mapping:
        if word in normalized_text:
            return now + offset

    absolute_match = re.search(
        r"(?P<year>\d{4})(?:年|[-/])(?P<month>\d{1,2})(?:月|[-/])"
        r"(?P<day>\d{1,2})(?:[日号])?",
        text,
    )
    if absolute_match:
        try:
            target = datetime(
                int(absolute_match.group("year")),
                int(absolute_match.group("month")),
                int(absolute_match.group("day")),
            )
        except ValueError:
            return None
        return target.timestamp()

    weekday_time = _parse_weekday_time(text, now)
    if weekday_time is not None:
        return weekday_time

    week_mapping: dict[str, float] = {
        "上周": -7 * day_sec,
        "本周": 0,
        "下下周": 14 * day_sec,
        "下周": 7 * day_sec,
    }
    for word, offset in week_mapping.items():
        if word in text:
            return now + offset

    # 月/日格式，如“5月30日”
    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        now_dt = datetime.fromtimestamp(now)
        target = now_dt.replace(
            month=month, day=day, hour=0, minute=0, second=0, microsecond=0
        )
        if target < now_dt:
            target = target.replace(year=now_dt.year + 1)
        return target.timestamp()

    return None


def _has_minimal_information(text: str) -> bool:
    """检查文本是否包含足够的信息量以作为长期记忆存储。

    过滤以下无信息量内容：
    - 寒暄/问候（"早"、"晚安"、"吃了吗"）
    - 纯应答（"好的"、"知道了"、"嗯"）
    - 纯表情/纯标点/纯数字
    - 单字重复（"啊啊啊"）
    """
    stripped = text.strip()
    if not stripped:
        return False
    # 匹配无信息量模式（寒暄/纯应答/纯表情等）
    if _LOW_INFO_PATTERNS.match(stripped):
        return False
    # 字符种类过少（纯数字、纯符号、单字重复）
    unique_chars = set(stripped.replace(" ", ""))
    return not (len(unique_chars) <= 1)


def classify_atoms(
    key_facts: list[str],
    topics: list[str] | None = None,
    participants: list[str] | None = None,
    parent_importance: float = 0.5,
    session_id: str | None = None,
    persona_id: str | None = None,
    emotion_tags: list[str] | None = None,
    emotional_intensity: float = 0.5,
    min_confidence: float = 0.65,
    min_importance: float = 0.3,
    min_content_length: int = 5,
    enable_info_check: bool = True,
    enable_quality_filter: bool = True,
    atom_type_hint: str | None = None,
) -> list[MemoryAtom]:
    """将一组 `key_fact` 字符串分类为 `MemoryAtom` 实例。

    v2.6: 新增质量过滤参数，在原子创建阶段拦截低质量内容。

    参数:
        key_facts: 来自 LLM 抽取的原始事实字符串。
        topics: 用于实体关联的话题标签。
        participants: 用于实体关联的参与者名称。
        parent_importance: 继承自父级记忆的重要性。
        session_id: 会话标识。
        persona_id: 人格标识。
        emotion_tags: 附加在该记忆上的情绪标签（如 ["开心", "感动"]）。
        emotional_intensity: 0-1 的情绪强度，会影响 TTL。
        min_confidence: 最小置信度阈值，低于此值的原子不保存（默认 0.65）。
        min_importance: 最小重要性阈值，低于此值的原子不保存（默认 0.3）。
        min_content_length: 最小内容长度（字符），过短的原子不保存（默认 5）。
        enable_info_check: 是否启用信息量预检（默认 True）。
        enable_quality_filter: 质量过滤总开关，False 时跳过所有过滤（默认 True）。
        atom_type_hint: 结构化抽取显式提供的可选类型，仅在规则无法判定时使用。

    返回:
        通过筛选的 `MemoryAtom` 列表；每条事实都会计算 TTL 与衰减类型。
    """
    entities: list[str] = []
    if topics:
        entities.extend(topics)
    if participants:
        entities.extend(participants)

    _emotion_tags = list(emotion_tags or [])
    normalized_intensity = max(0.0, min(1.0, emotional_intensity))

    atoms: list[MemoryAtom] = []
    for fact in key_facts:
        fact = fact.strip()
        if not fact:
            continue

        # ---- 质量过滤（v2.6） ----
        if enable_quality_filter:
            # 检查 1: 信息量预检
            if enable_info_check and not _has_minimal_information(fact):
                _FILTERED_STATS["low_information"] += 1
                continue

            # 检查 2: 最小内容长度
            if len(fact) < min_content_length:
                _FILTERED_STATS["too_short"] += 1
                continue

        # ---- 分类 ----
        atom_type, confidence, event_time = _classify_single(fact, atom_type_hint)

        # ---- 质量过滤（分类后） ----
        if enable_quality_filter:
            # 检查 3: 最小置信度
            if confidence < min_confidence:
                _FILTERED_STATS["low_confidence"] += 1
                continue

            # 检查 4: 最小重要性
            if parent_importance < min_importance:
                _FILTERED_STATS["low_importance"] += 1
                continue

        ttl, decay = compute_ttl(
            atom_type, parent_importance, 0, event_time, normalized_intensity
        )
        now = time.time()
        atom_metadata: dict[str, object] = {
            "emotion_tags": list(_emotion_tags),
            "emotional_intensity": normalized_intensity,
        }
        if _NEGATION_RE.search(fact):
            atom_metadata["polarity"] = "negative"

        atom = MemoryAtom(
            parent_memory_id=0,
            atom_type=atom_type,
            content=fact,
            entities=list(entities),
            emotion_tags=list(_emotion_tags),
            importance=parent_importance,
            confidence=confidence,
            event_time=event_time,
            ttl_days=ttl,
            decay_type=decay,
            expires_at=now + ttl * 86400.0,
            session_id=session_id,
            persona_id=persona_id,
            metadata=atom_metadata,
        )
        atoms.append(atom)

    # 周期性输出过滤统计
    total_filtered = sum(_FILTERED_STATS.values())
    if total_filtered > 0 and total_filtered % 10 == 0:
        logger.debug(
            f"[AtomClassifier] 过滤统计: {_FILTERED_STATS} (累计过滤 {total_filtered} 条)"
        )

    return atoms


def _classify_single(
    text: str,
    atom_type_hint: str | None = None,
) -> tuple[AtomType, float, float | None]:
    """按强规则优先、结构提示兜底分类并返回类型、置信度与事件时间。"""
    has_time = bool(_TIME_INDICATORS.search(text))
    has_action = bool(_ACTION_VERBS.search(text))
    has_relation = bool(_RELATION_PATTERNS.search(text))
    has_preference = bool(_PREFERENCE_PATTERNS.search(text))
    has_stative = bool(_STATIVE_PATTERNS.search(text))

    event_time = _parse_event_time(text) if has_time else None
    now = time.time()
    is_negated = bool(_NEGATION_RE.search(text))

    # 时间和动作同时出现时，必须先区分过去事件与未来计划。
    if has_time and has_action:
        if not is_negated and event_time is not None and event_time > now + 60.0:
            return AtomType.PLANNED, 0.85, event_time
        return AtomType.EPISODIC, 0.84, event_time

    # 否定只表达极性，不能抹掉偏好或关系的语义类型。
    if has_preference:
        return AtomType.PREFERENCE, 0.82, None

    if has_relation:
        return AtomType.RELATIONAL, 0.80, None

    # FACTUAL：状态/定义类模式
    if has_stative:
        return AtomType.FACTUAL, 0.78, None

    # EPISODIC：有动作但无时间，更可能是事件描述
    if has_action:
        return AtomType.EPISODIC, 0.75, None

    normalized_hint = str(atom_type_hint or "").strip().lower()
    if normalized_hint in _ATOM_TYPE_HINTS:
        return _ATOM_TYPE_HINTS[normalized_hint], 0.76, None

    # UNKNOWN：兜底分类
    return AtomType.UNKNOWN, 0.68, None


__all__ = [
    "classify_atoms",
    "AtomType",
    "DecayType",
    "MemoryAtom",
    "get_filter_stats",
    "reset_filter_stats",
    "_has_minimal_information",
]
