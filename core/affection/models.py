"""好感度系统的数据模型：用户好感等级、Bot 情绪与交互类型。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import ClassVar


# ---- 好感度等级 -------------------------------------------------------------------

class AffectionLevel(IntEnum):
    """离散化的好感度层级，使用常见阈值划分。"""

    HOSTILE = -75
    DISLIKED = -50
    COLD = -25
    NEUTRAL = 0
    WARM = 25
    FRIENDLY = 50
    CLOSE = 75
    INTIMATE = 100

    @classmethod
    def from_score(cls, score: int) -> AffectionLevel:
        """根据分数返回对应的好感度层级。"""
        for level in reversed(cls):
            if score >= level.value:
                return level
        return cls.HOSTILE

    @classmethod
    def name_for(cls, score: int) -> str:
        """返回适合展示的人类可读层级名称。"""
        mapping: dict[AffectionLevel, str] = {
            cls.HOSTILE: "敌对",
            cls.DISLIKED: "不喜",
            cls.COLD: "冷淡",
            cls.NEUTRAL: "中立",
            cls.WARM: "温暖",
            cls.FRIENDLY: "友好",
            cls.CLOSE: "亲密",
            cls.INTIMATE: "挚友",
        }
        return mapping.get(cls.from_score(score), "未知")


# ---- Bot 情绪 --------------------------------------------------------------------------

class MoodType(str, Enum):
    """Bot 可拥有的十种情绪类型。"""

    HAPPY = "happy"
    SAD = "sad"
    EXCITED = "excited"
    CALM = "calm"
    ANGRY = "angry"
    ANXIOUS = "anxious"
    PLAYFUL = "playful"
    SERIOUS = "serious"
    NOSTALGIC = "nostalgic"
    CURIOUS = "curious"


# 不同情绪类型对好感度变化的修正系数
_MOOD_MODIFIER_BASE: dict[MoodType, float] = {
    MoodType.HAPPY: 1.2,
    MoodType.EXCITED: 1.3,
    MoodType.PLAYFUL: 1.1,
    MoodType.CALM: 1.0,
    MoodType.CURIOUS: 1.05,
    MoodType.NOSTALGIC: 0.9,
    MoodType.SERIOUS: 0.8,
    MoodType.SAD: 0.6,
    MoodType.ANXIOUS: 0.7,
    MoodType.ANGRY: 0.4,
}


@dataclass(slots=True)
class BotMood:
    """Bot 当前的情绪状态。"""

    mood_type: MoodType
    intensity: float = 0.5
    description: str = ""
    start_time: float = field(default_factory=time.time)
    duration_hours: float = 4.0

    def is_active(self, at_time: float | None = None) -> bool:
        """检查当前情绪是否仍在持续。"""
        now = at_time if at_time is not None else time.time()
        return now < (self.start_time + self.duration_hours * 3600)

    def get_mood_modifier(self) -> float:
        """返回当前情绪对好感度变化的乘数修正。"""
        base = _MOOD_MODIFIER_BASE.get(self.mood_type, 1.0)
        # 强度融合：0.5 -> 0.75，1.0 -> 1.0
        intensity_factor = 0.5 + self.intensity * 0.5
        return max(0.2, min(1.3, base * intensity_factor))


# ---- 交互类型 -----------------------------------------------------------------

class InteractionType(str, Enum):
    """分类后的对话交互类型，共覆盖 17 种正向、中性和负向互动。"""

    # 正向 / 中性
    CHAT = "chat"              # 日常闲聊
    COMPLIMENT = "compliment"  # 称赞夸奖
    FLIRT = "flirt"            # 调情式互动
    COMFORT = "comfort"        # 安慰与抚慰
    HELP = "help"              # 求助或提供帮助
    THANKS = "thanks"          # 表达感谢
    APOLOGY = "apology"        # 道歉
    TEASE = "tease"            # 善意打趣
    CARE = "care"              # 表达关心
    GIFT = "gift"              # 赠礼
    PRAISE = "praise"          # 高度赞美
    ENCOURAGE = "encourage"    # 鼓励打气
    SUPPORT = "support"        # 表达支持

    # 负向
    INSULT = "insult"          # 侮辱
    HARASSMENT = "harassment"  # 骚扰
    ABUSE = "abuse"            # 严重辱骂
    THREAT = "threat"          # 威胁恐吓


# 各交互类型对应的好感度变化规则
@dataclass(slots=True)
class _InteractionRule:
    base_change: int
    mood_sensitive: bool
    mood_effect: float          # How much this affects the bot's mood
    mood_requirements: list[MoodType] | None = None
    positive_mood_boost: bool = False
    negative_mood_trigger: bool = False


INTERACTION_RULES: dict[InteractionType, _InteractionRule] = {
    # --- Positive ---
    InteractionType.CHAT: _InteractionRule(1, True, 0.1),
    InteractionType.COMPLIMENT: _InteractionRule(3, True, 0.2),
    InteractionType.PRAISE: _InteractionRule(5, True, 0.3, positive_mood_boost=True),
    InteractionType.ENCOURAGE: _InteractionRule(4, True, 0.25, positive_mood_boost=True),
    InteractionType.SUPPORT: _InteractionRule(4, True, 0.2),
    InteractionType.FLIRT: _InteractionRule(
        5, True, 0.15,
        mood_requirements=[MoodType.HAPPY, MoodType.PLAYFUL, MoodType.EXCITED],
    ),
    InteractionType.COMFORT: _InteractionRule(
        4, True, 0.3,
        mood_requirements=[MoodType.SAD, MoodType.ANXIOUS],
    ),
    InteractionType.HELP: _InteractionRule(2, False, 0.1),
    InteractionType.THANKS: _InteractionRule(2, True, 0.15),
    InteractionType.APOLOGY: _InteractionRule(
        1, True, 0.1,
        mood_requirements=[MoodType.ANGRY, MoodType.SAD],
    ),
    InteractionType.TEASE: _InteractionRule(
        2, True, 0.1,
        mood_requirements=[MoodType.PLAYFUL, MoodType.HAPPY],
    ),
    InteractionType.CARE: _InteractionRule(3, True, 0.2),
    InteractionType.GIFT: _InteractionRule(8, True, 0.4, positive_mood_boost=True),

    # --- Negative ---
    InteractionType.INSULT: _InteractionRule(-8, True, -0.5, negative_mood_trigger=True),
    InteractionType.HARASSMENT: _InteractionRule(-6, True, -0.4, negative_mood_trigger=True),
    InteractionType.ABUSE: _InteractionRule(-10, True, -0.6, negative_mood_trigger=True),
    InteractionType.THREAT: _InteractionRule(-12, True, -0.7, negative_mood_trigger=True),
}


# ---- 用户好感度记录 --------------------------------------------------------------------

@dataclass(slots=True)
class UserAffection:
    """单个用户的好感度跟踪记录。"""

    user_id: str
    group_id: str
    affection_score: int = 0
    interaction_count: int = 0
    last_interaction: float = 0.0

    @property
    def level(self) -> AffectionLevel:
        return AffectionLevel.from_score(self.affection_score)

    @property
    def level_name(self) -> str:
        return AffectionLevel.name_for(self.affection_score)


# ---- 基于关键词的回退分类 -------------------------------------------------------------

# 当 LLM 不可用时，使用“关键词 -> 交互类型”的规则映射进行回退分类。
_KEYWORD_INTERACTION_MAP: ClassVar[list[tuple[list[str], InteractionType]]] = [
    # ── 赞美（优先检查更长、更具体的模式） ──
    (
        ["好美", "漂亮", "可爱", "帅", "美丽", "好看", "棒", "厉害",
         "优秀", "聪明", "温柔", "体贴", "贴心", "善良", "完美", "很棒",
         "真好", "赞", "给力", "牛", "强", "666", "牛逼",
         "棒棒", "太棒了", "真棒", "真厉害", "哇塞", "厉害了", "太好了",
         "好厉害", "好强", "好棒", "赞赞", "牛牛", "好牛", "超棒", "超好",
         "很好", "很棒", "很厉害", "太厉害了", "好喜欢", "爱了",
         "太可爱了", "好可爱", "可爱爆了", "萌", "萌萌", "好萌"],
        InteractionType.COMPLIMENT,
    ),
    # ── 感谢 ──
    (["谢谢", "感谢", "多谢", "thank", "谢啦", "谢了", "thx"], InteractionType.THANKS),
    # ── 威胁 ──
    (
        ["威胁", "杀掉", "打死", "弄死", "干掉", "揍你", "打你"],
        InteractionType.THREAT,
    ),
    # ── 侮辱（强负向关键词） ──
    (
        ["傻逼", "蠢货", "白痴", "垃圾", "废物", "滚开",
         "操你", "草你", "妈的", "他妈", "贱人", "婊子", "畜生"],
        InteractionType.INSULT,
    ),
    # ── 问候 / 关心（最后匹配，如“你好”这类短通用模式） ──
    (
        ["早上好", "晚上好", "怎么样", "最近好吗", "hello", "hi",
         "嗨", "哈喽", "午安", "下午好", "在吗", "你好呀", "你好啊", "你好"],
        InteractionType.CARE,
    ),
]

KEYWORD_INTERACTION_MAP: list[tuple[list[str], InteractionType]] = _KEYWORD_INTERACTION_MAP

# 缓存：预构建扁平映射，加快匹配速度（模块加载时执行一次）
_KEYWORD_FLAT: dict[str, InteractionType] = {}
_PRIORITY_ORDER: dict[str, int] = {}
for _kws, _itype in _KEYWORD_INTERACTION_MAP:
    for _kw in _kws:
        _KEYWORD_FLAT[_kw] = _itype
        _PRIORITY_ORDER[_kw] = len(_PRIORITY_ORDER)  # monotonic insertion index


def classify_by_keywords(message: str) -> InteractionType | None:
    """通过关键词匹配分类交互类型（回退路径）。"""
    msg = message.lower().strip()
    # 先按关键词长度降序，再按优先级升序。
    # 关键词越长越具体；若长度相同，则由源列表中的顺序决定优先级。
    for keyword, itype in sorted(
        _KEYWORD_FLAT.items(), key=lambda kv: (-len(kv[0]), _PRIORITY_ORDER.get(kv[0], 999))
    ):
        if keyword in msg:
            return itype
    return None


__all__ = [
    "AffectionLevel",
    "MoodType",
    "BotMood",
    "InteractionType",
    "INTERACTION_RULES",
    "UserAffection",
    "classify_by_keywords",
    "KEYWORD_INTERACTION_MAP",
]
