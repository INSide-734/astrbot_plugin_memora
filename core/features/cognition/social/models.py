"""社交关系类型化的数据模型。

定义六大关系分类、按关系类型划分的强度更新难度系数，
以及社交模块内部通用的数据类传输对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 关系类型 6 大类
# ---------------------------------------------------------------------------

RELATION_CATEGORIES: dict[str, list[str]] = {
    # 血缘关系
    "blood": ["parent_child", "siblings", "relatives"],
    # 地缘关系
    "geographic": ["neighbor", "fellow_town", "fellow_passenger"],
    # 职业 / 学业关系
    "career": ["colleague", "mentor_mentee", "classmate"],
    # 情感关系
    "emotional": ["lover", "best_friend", "ambiguous", "rival"],
    # 兴趣关系
    "interest": ["board_game_friend", "gaming_teammate"],
    # 亲密度层级
    "intimacy": ["core_intimate", "daily_normal", "stranger"],
}

# ---------------------------------------------------------------------------
# 难度门控系数
#
# 实际变化量 = value_delta * (1 - difficulty)
#   difficulty → 1.0：几乎不可变（血缘）
#   difficulty → 0.0：高度易变（临时关系）
# ---------------------------------------------------------------------------

RELATION_DIFFICULTY: dict[str, float] = {
    # ---- blood (0.90 – 0.98) ----
    "parent_child": 0.98,
    "siblings": 0.95,
    "relatives": 0.90,
    # ---- geographic (0.05 – 0.50) ----
    "neighbor": 0.45,
    "fellow_town": 0.50,
    "fellow_passenger": 0.05,
    # ---- career (0.50 – 0.70) ----
    "colleague": 0.60,
    "mentor_mentee": 0.65,
    "classmate": 0.50,
    # ---- emotional (0.30 – 0.85) ----
    "lover": 0.85,
    "best_friend": 0.80,
    "ambiguous": 0.30,
    "rival": 0.55,
    # ---- interest (0.20 – 0.40) ----
    "board_game_friend": 0.25,
    "gaming_teammate": 0.20,
    # ---- intimacy (0.05 – 0.90) ----
    "core_intimate": 0.90,
    "daily_normal": 0.30,
    "stranger": 0.10,
}


# ---------------------------------------------------------------------------
# 辅助方法
# ---------------------------------------------------------------------------


def get_relation_category(relation_type: str) -> str | None:
    """返回 *relation_type* 对应的分类名；未知时返回 *None*。"""
    for cat, members in RELATION_CATEGORIES.items():
        if relation_type in members:
            return cat
    return None


def get_difficulty(relation_type: str) -> float:
    """返回 *relation_type* 对应的难度系数。

    当类型未显式列出时，回退到 0.40
    （即参考设计中的默认值）。
    """
    return RELATION_DIFFICULTY.get(relation_type, 0.40)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class SocialRelation:
    """表示从 *from_user* 指向 *to_user* 的单向社交关系。"""

    from_user: str
    to_user: str
    relation_type: str
    strength: float  # 0.0 – 1.0
    frequency: int  # 累计互动次数
    last_interaction: float  # 时间戳（自 Unix epoch 起的秒数）
    group_id: str
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_user": self.from_user,
            "to_user": self.to_user,
            "relation_type": self.relation_type,
            "strength": self.strength,
            "frequency": self.frequency,
            "last_interaction": self.last_interaction,
            "group_id": self.group_id,
            "tags": self.tags,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> SocialRelation:
        """从原始数据库行构建对象（其中 tags 可能是 JSON 字符串）。"""
        tags_raw = row.get("tags", row.get("tags_json", "[]"))
        if isinstance(tags_raw, str):
            import json

            try:
                tags = json.loads(tags_raw)
            except (json.JSONDecodeError, TypeError):
                tags = []
        else:
            tags = list(tags_raw) if tags_raw else []

        return cls(
            from_user=row["from_user"],
            to_user=row["to_user"],
            relation_type=row["relation_type"],
            strength=float(row["strength"]),
            frequency=int(row["frequency"]),
            last_interaction=float(row["last_interaction"]),
            group_id=row["group_id"],
            tags=tags,
        )


@dataclass
class RelationChange:
    """描述一次对 SocialRelation 的更新尝试。"""

    from_user: str
    to_user: str
    relation_type: str
    delta: float  # 原始建议变更值（难度门控前）
    new_strength: float  # 钳制后的新强度
    reason: str


__all__ = [
    "RELATION_CATEGORIES",
    "RELATION_DIFFICULTY",
    "RelationChange",
    "SocialRelation",
    "get_difficulty",
    "get_relation_category",
]
