"""好感度/情绪查询的 Agent 工具。

提供 Agent 可调用的工具，封装 AffectionManager，允许 LLM Agent
查询用户好感度等级和 Bot 当前情绪状态。
"""

from __future__ import annotations

import json
from dataclasses import field
from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from pydantic.dataclasses import dataclass

from ..affection.affection_manager import AffectionManager
from ..affection.models import AffectionLevel

# ---- Display names for affection levels ------------------------------------------------

_LEVEL_DISPLAY: dict[AffectionLevel, str] = {
    AffectionLevel.HOSTILE: "敌对",
    AffectionLevel.DISLIKED: "不喜",
    AffectionLevel.COLD: "冷淡",
    AffectionLevel.NEUTRAL: "中立",
    AffectionLevel.WARM: "温暖",
    AffectionLevel.FRIENDLY: "友好",
    AffectionLevel.CLOSE: "亲密",
    AffectionLevel.INTIMATE: "挚友",
}


def _json_result(data: dict[str, Any]) -> str:
    """将工具结果稳定序列化为 JSON 文本。"""
    return json.dumps(data, ensure_ascii=False, default=str)


# ---- Helper: context resolution --------------------------------------------------------


def _resolve_ids(
    context: ContextWrapper[AstrAgentContext],
    user_id: str,
    group_id: str,
) -> tuple[str, str]:
    """当 user_id 和 group_id 为空时从事件上下文中自动解析。

    返回:
        (已解析的 user_id, 已解析的 group_id) 元组。
    """
    user_id = (user_id or "").strip()
    group_id = (group_id or "").strip()

    if not user_id or not group_id:
        try:
            event = context.context.event
            if not user_id and hasattr(event, "get_sender_id"):
                user_id = str(event.get_sender_id() or "")
            if not group_id and hasattr(event, "unified_msg_origin"):
                group_id = str(getattr(event, "unified_msg_origin", ""))
        except Exception:
            pass

    return user_id, group_id


# ---- AffectionCheckTool ----------------------------------------------------------------


@dataclass
class AffectionCheckTool(FunctionTool[AstrAgentContext]):
    """查询用户与 Bot 之间的好感度。Agent 可主动调用以了解用户关系级别。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    affection_manager: Any = field(default=None)

    name: str = "check_affection"
    description: str = (
        "Check the affection level between the bot and a user in a group. "
        "Returns the affection score (-100 to +100), level "
        "(HOSTILE/DISLIKED/COLD/NEUTRAL/WARM/FRIENDLY/CLOSE/INTIMATE), "
        "interaction count, and last interaction time. "
        "Use this when the user's tone suggests they care about the relationship."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": (
                        "The user ID to check. Auto-detected from conversation if empty."
                    ),
                    "default": "",
                },
                "group_id": {
                    "type": "string",
                    "description": (
                        "Group ID. Auto-detected from conversation context if empty."
                    ),
                    "default": "",
                },
            },
            "required": [],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        user_id: str = "",
        group_id: str = "",
    ) -> ToolExecResult:
        user_id, group_id = _resolve_ids(context, user_id, group_id)

        mgr: AffectionManager | None = self.affection_manager
        if mgr is None:
            return _json_result(
                {
                    "found": False,
                    "error": "affection_manager not available",
                }
            )

        if not user_id or not group_id:
            return _json_result(
                {
                    "user_id": user_id,
                    "group_id": group_id,
                    "found": False,
                    "error": (
                        "user_id or group_id is empty — provide them explicitly "
                        "or ensure the tool has access to conversation context"
                    ),
                }
            )

        try:
            ua = await mgr.get_user_affection(group_id, user_id)
            mood = await mgr.get_mood(group_id)
        except Exception:
            return _json_result(
                {
                    "user_id": user_id,
                    "group_id": group_id,
                    "found": False,
                    "error": "lookup_failed",
                }
            )

        if ua is None:
            return _json_result(
                {
                    "user_id": user_id,
                    "group_id": group_id,
                    "found": False,
                    "affection_score": 0,
                    "level": _LEVEL_DISPLAY.get(AffectionLevel.NEUTRAL, "中立"),
                    "interaction_count": 0,
                    "last_interaction": None,
                    "bot_mood": mood.mood_type.value if mood else None,
                    "bot_mood_description": mood.description if mood else None,
                }
            )

        return _json_result(
            {
                "user_id": ua.user_id,
                "group_id": ua.group_id,
                "found": True,
                "affection_score": ua.affection_score,
                "level": _LEVEL_DISPLAY.get(
                    AffectionLevel.from_score(ua.affection_score), "未知"
                ),
                "interaction_count": ua.interaction_count,
                "last_interaction": ua.last_interaction,
                "bot_mood": mood.mood_type.value if mood else None,
                "bot_mood_description": mood.description if mood else None,
            }
        )


# ---- BotMoodTool -----------------------------------------------------------------------


@dataclass
class BotMoodTool(FunctionTool[AstrAgentContext]):
    """查询 Bot 当前情绪状态。Agent 可主动调用以了解 Bot 的心情。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    affection_manager: Any = field(default=None)

    name: str = "check_bot_mood"
    description: str = (
        "Check the bot's current emotional mood in a group. "
        "Returns mood type (happy/excited/playful/calm/curious/nostalgic/"
        "serious/sad/anxious/angry), intensity (0-1), and description. "
        "Use this to understand the bot's current emotional state."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": (
                        "Group ID. Auto-detected from conversation context if empty."
                    ),
                    "default": "",
                },
            },
            "required": [],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        group_id: str = "",
    ) -> ToolExecResult:
        _, group_id = _resolve_ids(context, "", group_id)

        mgr: AffectionManager | None = self.affection_manager
        if mgr is None:
            return _json_result(
                {
                    "found": False,
                    "error": "affection_manager not available",
                }
            )

        if not group_id:
            return _json_result(
                {
                    "group_id": "",
                    "found": False,
                    "error": (
                        "group_id is empty — provide it explicitly "
                        "or ensure the tool has access to conversation context"
                    ),
                }
            )

        try:
            mood = await mgr.get_mood(group_id)
        except Exception:
            return _json_result(
                {
                    "group_id": group_id,
                    "found": False,
                    "error": "lookup_failed",
                }
            )

        return _json_result(
            {
                "group_id": group_id,
                "found": True,
                "mood_type": mood.mood_type.value,
                "intensity": mood.intensity,
                "description": mood.description,
                "start_time": mood.start_time,
                "duration_hours": mood.duration_hours,
                "is_active": mood.is_active(),
            }
        )


__all__ = ["AffectionCheckTool", "BotMoodTool"]
