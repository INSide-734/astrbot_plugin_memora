"""好感度交互后的 Bot 情绪级联。"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.api import logger

from .models import BotMood, InteractionType, MoodType

MoodSetter = Callable[..., Awaitable[BotMood]]


async def apply_mood_cascade(
    set_mood: MoodSetter,
    group_id: str,
    interaction_type: InteractionType,
    rule: Any,
    current_mood: BotMood,
) -> None:
    """根据交互规则选择并执行对应的情绪级联。"""
    mood_effect = getattr(rule, "mood_effect", 0.0)
    if getattr(rule, "negative_mood_trigger", False):
        await _cascade_negative(set_mood, group_id, interaction_type, mood_effect)
    elif getattr(rule, "positive_mood_boost", False):
        await _cascade_positive(set_mood, group_id, interaction_type, mood_effect)
    else:
        await _cascade_adjust(set_mood, group_id, current_mood, mood_effect)


async def _cascade_negative(
    set_mood: MoodSetter,
    group_id: str,
    interaction_type: InteractionType,
    effect: float,
) -> None:
    """负面交互会触发强烈且即时的情绪覆盖，持续两小时。"""
    mapping: dict[InteractionType, tuple[MoodType, list[str]]] = {
        InteractionType.THREAT: (
            MoodType.ANXIOUS,
            [
                "感到被威胁，心情变得紧张不安...",
                "受到恐吓，现在有些害怕和担心。",
                "被威胁让我感到很不安全。",
            ],
        ),
        InteractionType.ABUSE: (
            MoodType.ANGRY,
            [
                "被恶意谩骂，现在心情很愤怒！",
                "受到恶毒攻击，感到非常生气。",
                "恶语相向让我感到愤怒和受伤。",
            ],
        ),
        InteractionType.INSULT: (
            MoodType.SAD,
            [
                "被侮辱攻击，心情变得很低落...",
                "受到攻击，感到伤心和失望。",
                "被人侮辱让我感到很难过。",
            ],
        ),
        InteractionType.HARASSMENT: (
            MoodType.ANXIOUS,
            [
                "被骚扰困扰，现在感到很不安。",
                "持续的骚扰让我感到紧张。",
                "这种行为让我感到不舒服。",
            ],
        ),
    }
    mood_type, descriptions = mapping.get(
        interaction_type,
        (MoodType.SAD, ["心情有些低落..."]),
    )
    intensity = min(0.9, abs(effect))
    await set_mood(
        group_id,
        mood_type,
        intensity,
        duration_hours=2,
        description=random.choice(descriptions),
    )
    logger.info(f"[好感度管理] 触发负面情绪: {mood_type.value} ({intensity:.2f})")


async def _cascade_positive(
    set_mood: MoodSetter,
    group_id: str,
    interaction_type: InteractionType,
    effect: float,
) -> None:
    """强烈的正向交互会触发持续四小时的情绪提升。"""
    if interaction_type == InteractionType.GIFT:
        mood_type = MoodType.EXCITED
        descriptions = [
            "收到礼物，太兴奋了！",
            "有人送礼物给我，好开心好激动！",
            "这个礼物让我感到非常兴奋！",
        ]
    elif interaction_type in (InteractionType.PRAISE, InteractionType.ENCOURAGE):
        mood_type = MoodType.HAPPY
        descriptions = [
            "被夸赞鼓励，心情变得很开心！",
            "收到赞美，感到特别高兴。",
            "这些鼓励的话让我心情大好！",
        ]
    else:
        mood_type = MoodType.HAPPY
        descriptions = [
            "感受到善意，心情变好了。",
            "这种关怀让我感到温暖。",
            "谢谢你的友好，我心情好多了。",
        ]

    intensity = min(0.8, effect)
    await set_mood(
        group_id,
        mood_type,
        intensity,
        duration_hours=4,
        description=random.choice(descriptions),
    )
    logger.info(f"[好感度管理] 触发积极情绪: {mood_type.value} ({intensity:.2f})")


async def _cascade_adjust(
    set_mood: MoodSetter,
    group_id: str,
    mood: BotMood,
    effect: float,
) -> None:
    """在变化足够显著时对当前情绪强度进行轻微调整。"""
    if abs(effect) < 0.05:
        return
    new_intensity = max(0.1, min(0.9, mood.intensity + effect))
    if abs(new_intensity - mood.intensity) < 0.1:
        return
    await set_mood(
        group_id,
        mood.mood_type,
        new_intensity,
        duration_hours=1,
    )
