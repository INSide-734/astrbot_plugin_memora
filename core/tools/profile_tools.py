"""用户画像自主查询的 Agent 工具。"""

from __future__ import annotations

import json
from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext


def _json_result(data: dict[str, Any]) -> str:
    """将工具结果稳定序列化为 JSON 文本。"""
    return json.dumps(data, ensure_ascii=False, default=str)


@dataclass
class ProfileLookupTool(FunctionTool[AstrAgentContext]):
    """查询用户画像。Agent 可主动调用以了解当前用户的标签、偏好和交互统计。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    profile_manager: Any = field(default=None)

    name: str = "profile_lookup"
    description: str = (
        "Look up the user profile for a given user ID to understand their interests, "
        "personality traits, habits, preferences, and interaction history. "
        "Use this when you need to personalize responses, recall user context, "
        "or understand the user's background. "
        "If user_id is omitted, the tool will try to infer from conversation context."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": (
                        "The user ID to look up. Can be a username, user ID, or display name. "
                        "If omitted, looks up the current conversation user."
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
    ) -> ToolExecResult:
        user_id = (user_id or "").strip()

        # 自动从事件上下文解析 user_id
        if not user_id:
            try:
                event = context.context.event
                if hasattr(event, "get_sender_id"):
                    user_id = str(event.get_sender_id() or "")
                if not user_id:
                    user_id = str(getattr(event, "unified_msg_origin", ""))
            except Exception:
                pass

        if not user_id:
            return _json_result(
                {
                    "user_id": "",
                    "found": False,
                    "error": "user_id is empty — provide a user_id or ensure the tool has access to the current user",
                }
            )

        mgr = self.profile_manager
        if mgr is None:
            return _json_result(
                {
                    "user_id": user_id,
                    "found": False,
                    "error": "profile_manager not available",
                }
            )

        try:
            profile = await mgr.get_profile(user_id)
        except Exception:
            return _json_result(
                {"user_id": user_id, "found": False, "error": "lookup_failed"}
            )

        if profile is None:
            return _json_result({"user_id": user_id, "found": False})

        try:
            weights = await mgr.get_tag_weights(user_id)
        except Exception:
            weights = {}

        # 按分类组织标签
        tags_by_category: dict[str, list[dict[str, Any]]] = {}
        for tag in profile.tags:
            cat = (
                tag.category.value
                if hasattr(tag.category, "value")
                else str(tag.category)
            )
            if cat not in tags_by_category:
                tags_by_category[cat] = []
            tags_by_category[cat].append(
                {
                    "value": tag.value,
                    "confidence": tag.confidence,
                    "occurrence_count": tag.occurrence_count,
                }
            )

        # 每个分类只取 top 5 高置信度标签
        for cat in tags_by_category:
            tags_by_category[cat].sort(key=lambda t: t["confidence"], reverse=True)
            tags_by_category[cat] = tags_by_category[cat][:5]

        prefs = profile.preferences
        return _json_result(
            {
                "user_id": profile.user_id,
                "found": True,
                "display_name": profile.display_name,
                "tags_by_category": tags_by_category,
                "tag_weights": weights,
                "preferences": {
                    "reply_style": prefs.reply_style,
                    "preferred_topics": prefs.preferred_topics,
                    "avoided_topics": prefs.avoided_topics,
                    "avg_reply_length": prefs.avg_reply_length,
                },
                "stats": {
                    "total_messages": profile.total_messages,
                    "total_sessions": profile.total_sessions,
                    "total_tags": len(profile.tags),
                },
            }
        )


__all__ = ["ProfileLookupTool"]
