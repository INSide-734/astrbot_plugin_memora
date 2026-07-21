"""用户画像自主查询的 Agent 工具。"""

from __future__ import annotations

import asyncio
import inspect
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
    authorization_checker: Any = field(default=None)

    name: str = "profile_lookup"
    description: str = (
        "Look up the current user's profile, or another profile only when an explicit "
        "authorization checker permits it, to understand interests, "
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
                        "The exact user ID to look up. If omitted, the current sender is used. "
                        "A different target requires explicit authorization."
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
        """按可信事件身份查询画像，并对跨用户目标执行显式授权。"""

        event, trusted_user_id = self._trusted_event_identity(context)
        if not trusted_user_id:
            return _json_result(
                {
                    "found": False,
                    "error": "trusted_identity_unavailable",
                }
            )
        requested_user_id = (user_id or "").strip()
        user_id = requested_user_id or trusted_user_id
        if user_id != trusted_user_id and not await self._is_authorized_target(
            event,
            trusted_user_id,
            user_id,
        ):
            return _json_result({"found": False, "error": "profile_scope_denied"})

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

    @staticmethod
    def _trusted_event_identity(
        context: ContextWrapper[AstrAgentContext],
    ) -> tuple[Any | None, str]:
        """从当前 AstrBot 事件取得发送者身份，不使用会话 ID 回退。"""

        try:
            event = context.context.event
            getter = getattr(event, "get_sender_id", None)
            if not callable(getter):
                return event, ""
            return event, str(getter() or "").strip()
        except Exception:
            return None, ""

    async def _is_authorized_target(
        self,
        event: Any,
        requester_id: str,
        target_id: str,
    ) -> bool:
        """调用可选授权边界；缺少授权器或授权异常时拒绝跨用户读取。"""

        checker = self.authorization_checker
        if checker is None:
            return False
        try:
            result = checker(event, requester_id, target_id)
            if inspect.isawaitable(result):
                result = await result
            return result is True
        except asyncio.CancelledError:
            raise
        except Exception:
            return False


__all__ = ["ProfileLookupTool"]
