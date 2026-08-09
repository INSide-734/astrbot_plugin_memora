"""带时间感知能力的记忆原子模型，支持可配置 TTL 与衰减。"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AtomType(str, Enum):
    EPISODIC = "episodic"
    FACTUAL = "factual"
    RELATIONAL = "relational"
    PREFERENCE = "preference"
    PLANNED = "planned"
    UNKNOWN = "unknown"


class DecayType(str, Enum):
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    STEP = "step"


class AtomStatus(str, Enum):
    ACTIVE = "active"
    DORMANT = "dormant"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    FORGOTTEN = "forgotten"
    COLD = "cold"  # v2.6: 冷存储，低优先级，不参与常规检索


class PrivacyLevel(str, Enum):
    """记忆隐私级别 — 控制跨场景可见性。

    PUBLIC:        群聊产生的记忆，所有场景可访问
    SHARED:        跨场景共享（默认/回退值）
    CONFIDENTIAL:  仅私聊可见，群聊检索中过滤
    """

    PUBLIC = "public"
    SHARED = "shared"
    CONFIDENTIAL = "confidential"


def compute_decay_score(
    decay_type: DecayType | str,
    ttl_days: float,
    days_since: float,
) -> float:
    """根据 TTL 与已过天数计算衰减系数。"""
    effective_ttl = max(1.0, ttl_days)
    days_since = max(0.0, days_since)
    decay_value = getattr(decay_type, "value", str(decay_type))

    if decay_value == DecayType.LINEAR.value:
        return max(0.0, 1.0 - days_since / effective_ttl)
    if decay_value == DecayType.STEP.value:
        return 1.0 if days_since <= effective_ttl else 0.05

    half_life = effective_ttl / 2.0
    return math.exp(-math.log(2) * days_since / max(0.5, half_life))


# 各类记忆原子的基础 TTL（天）与衰减配置
_ATOM_TTL_CONFIG: dict[AtomType, dict[str, Any]] = {
    AtomType.EPISODIC: {"base_ttl": 7, "decay_type": DecayType.EXPONENTIAL},
    AtomType.PLANNED: {"base_ttl": 2, "decay_type": DecayType.STEP},
    AtomType.FACTUAL: {"base_ttl": 180, "decay_type": DecayType.EXPONENTIAL},
    AtomType.RELATIONAL: {"base_ttl": 90, "decay_type": DecayType.LINEAR},
    AtomType.PREFERENCE: {"base_ttl": 60, "decay_type": DecayType.EXPONENTIAL},
    AtomType.UNKNOWN: {"base_ttl": 30, "decay_type": DecayType.EXPONENTIAL},
}


@dataclass(slots=True)
class MemoryAtom:
    """从对话中提取出的细粒度、时间感知型记忆单元。"""

    parent_memory_id: int
    atom_type: AtomType = AtomType.UNKNOWN
    content: str = ""
    entities: list[str] = field(default_factory=list)
    emotion_tags: list[str] = field(default_factory=list)
    importance: float = 0.5
    confidence: float = 0.7

    # 时间字段
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    last_reinforced_at: float | None = None
    event_time: float | None = None
    ttl_days: float = 30.0
    expires_at: float = 0.0

    # 生命周期
    status: AtomStatus = AtomStatus.ACTIVE
    reinforcement_count: int = 0
    decay_type: DecayType = DecayType.EXPONENTIAL

    session_id: str | None = None
    persona_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # 父 canonical 的创建时证据；旧行缺失时保持 None，不伪造当前值。
    parent_revision: str | None = None
    parent_scope_key: str | None = None
    parent_privacy_level: str | None = None

    # 内部 ID，在插入后写入
    atom_id: int = 0

    def compute_temporal_score(self, reference_time: float | None = None) -> float:
        """计算该原子在给定时刻的衰减系数（0-1）。"""
        if reference_time is None:
            reference_time = time.time()
        days_since = max(0.0, (reference_time - self.last_accessed_at) / 86400.0)
        return compute_decay_score(self.decay_type, self.ttl_days, days_since)

    def is_expired(self, reference_time: float | None = None) -> bool:
        """检查该原子是否已超过过期阈值。"""
        if reference_time is None:
            reference_time = time.time()
        return reference_time >= self.expires_at


def compute_ttl(
    atom_type: AtomType,
    importance: float = 0.5,
    reinforcement_count: int = 0,
    event_time: float | None = None,
    emotional_intensity: float = 0.5,
    persona_decay_modifier: float = 1.0,
    allow_probationary: bool = True,
    probationary_ttl_days: float = 3.0,
    probationary_types: tuple[AtomType, ...] = (AtomType.UNKNOWN, AtomType.EPISODIC),
) -> tuple[float, DecayType]:
    """为给定原子分类计算 TTL（天）与衰减类型。

    emotional_intensity（0-1）：编码时情绪强度。
    高情绪强度的记忆更抗遗忘（TTL ×1.5~2.0）。

    闪光灯记忆：当 emotional_intensity >= 0.85 时触发近似永久存储，
    至少保留 365 天，并使用 LINEAR 衰减，绕过标准曲线。

    persona_decay_modifier 为按人格生效的遗忘倍率。
    > 1.0 表示遗忘更快（健忘），< 1.0 表示遗忘更慢（过目不忘）。
    默认 1.0 表示标准衰减曲线。

    v2.6 试用期机制 (Probationary TTL):
    新创建且未被访问过 (reinforcement_count=0) 的低重要性 (importance < 0.5)
    UNKNOWN/EPISODIC 类型原子默认只有 probationary_ttl_days (默认 3 天) TTL。
    如果被检索访问过（reinforcement_count > 0），则恢复标准 TTL。
    """
    # 闪光灯记忆 (Flashbulb Memory): 高度情感事件几乎永不遗忘
    if emotional_intensity >= 0.85:
        importance = max(importance, 0.70)
        ttl = 365.0 * (1.0 + 0.5 * importance)
        return max(1.0, ttl), DecayType.LINEAR

    # v2.6 试用期机制：低质量新原子快速淘汰
    if (
        allow_probationary
        and reinforcement_count == 0
        and atom_type in probationary_types
        and importance < 0.5
    ):
        importance_factor = 0.5 + max(0.0, min(1.0, importance))
        intensity = max(0.0, min(1.0, emotional_intensity))
        emotion_factor = 1.0 + intensity
        modifier = max(0.1, min(10.0, persona_decay_modifier))
        ttl = probationary_ttl_days * importance_factor * emotion_factor / modifier
        return max(1.0, ttl), DecayType.EXPONENTIAL

    config = _ATOM_TTL_CONFIG.get(atom_type, _ATOM_TTL_CONFIG[AtomType.UNKNOWN])
    base_ttl = float(config["base_ttl"])
    decay_type = DecayType(config["decay_type"])

    if atom_type == AtomType.PLANNED and event_time is not None:
        days_until_event = max(0.0, (event_time - time.time()) / 86400.0)
        base_ttl = days_until_event + base_ttl

    importance_factor = 0.5 + max(0.0, min(1.0, importance))
    reinforcement_factor = 1.0 + min(0.5, reinforcement_count * 0.1)
    intensity = max(0.0, min(1.0, emotional_intensity))
    emotion_factor = 1.0 + intensity  # 范围：1.0（平静）~ 2.0（强烈）
    ttl = base_ttl * importance_factor * reinforcement_factor * emotion_factor

    # 人格调制遗忘率 — >1 加速遗忘, <1 减缓遗忘
    modifier = max(0.1, min(10.0, persona_decay_modifier))
    ttl = ttl / modifier

    return max(1.0, ttl), decay_type


__all__ = [
    "MemoryAtom",
    "AtomType",
    "DecayType",
    "AtomStatus",
    "PrivacyLevel",
    "compute_decay_score",
    "compute_ttl",
]
