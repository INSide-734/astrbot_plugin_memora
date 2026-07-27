"""社交关系查询的 Agent 工具。"""

from __future__ import annotations

import json
from dataclasses import field
from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from pydantic.dataclasses import dataclass


def _json_result(data: dict[str, Any]) -> str:
    """将工具结果稳定序列化为 JSON 文本。"""
    return json.dumps(data, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# 关系类型中文名称映射（基于 RELATION_CATEGORIES 的实际子类型）
# ---------------------------------------------------------------------------

_CATEGORY_NAMES: dict[str, str] = {
    "parent_child": "亲子",
    "siblings": "兄弟姐妹",
    "relatives": "亲戚",
    "neighbor": "邻居",
    "fellow_town": "同乡",
    "fellow_passenger": "旅伴",
    "colleague": "同事",
    "mentor_mentee": "师徒",
    "classmate": "同学",
    "lover": "恋人",
    "best_friend": "挚友",
    "ambiguous": "暧昧",
    "rival": "对手",
    "board_game_friend": "桌游伙伴",
    "gaming_teammate": "游戏队友",
    "core_intimate": "核心密友",
    "daily_normal": "日常好友",
    "stranger": "陌生人",
}

# 子类型 → 大类映射
_CATEGORY_GROUP_NAMES: dict[str, str] = {
    "blood": "血缘",
    "geographic": "地缘",
    "career": "职业/学业",
    "emotional": "情感",
    "interest": "兴趣",
    "intimacy": "亲密度",
}


def _format_relation(rel: Any) -> dict[str, Any]:
    """将 SocialRelation 格式化为字典，附加中文名称。"""
    rel_type = getattr(rel, "relation_type", "")
    cn_name = _CATEGORY_NAMES.get(rel_type, rel_type)
    return {
        "from_user": getattr(rel, "from_user", ""),
        "to_user": getattr(rel, "to_user", ""),
        "relation_type": rel_type,
        "relation_name_cn": cn_name,
        "strength": round(getattr(rel, "strength", 0.0), 4),
        "frequency": getattr(rel, "frequency", 0),
        "last_interaction": getattr(rel, "last_interaction", 0.0),
        "group_id": getattr(rel, "group_id", ""),
        "tags": getattr(rel, "tags", []),
    }


# ---------------------------------------------------------------------------
# Tool: 查询用户关系
# ---------------------------------------------------------------------------


@dataclass
class RelationLookupTool(FunctionTool[AstrAgentContext]):
    """查询用户社交关系。Agent 可主动调用以了解用户间的关系类型、亲密度和交互频率。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    relation_manager: Any = field(default=None)

    name: str = "lookup_relations"
    description: str = (
        "Look up social relationships for a user in a group. "
        "Returns typed relationships with their categories (e.g. 亲子/同事/挚友), "
        "intimacy scores (0.0–1.0), and cumulative interaction frequency. "
        "Use this when the conversation involves social dynamics or when asked "
        "about relationships between group members."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": (
                        "The user ID to look up relations for. "
                        "Auto-detected from conversation if empty."
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
        user_id = (user_id or "").strip()
        group_id = (group_id or "").strip()

        # 自动从事件上下文解析
        if not user_id:
            try:
                event = context.context.event
                if hasattr(event, "get_sender_id"):
                    user_id = str(event.get_sender_id() or "")
                if not user_id:
                    user_id = str(getattr(event, "unified_msg_origin", ""))
            except Exception:
                pass

        if not group_id:
            try:
                group_id = str(getattr(context.context.event, "unified_msg_origin", ""))
            except Exception:
                pass

        if not user_id:
            return _json_result(
                {
                    "user_id": "",
                    "group_id": group_id,
                    "found": False,
                    "error": "user_id is empty — provide a user_id or ensure the tool has access to the current user",
                }
            )

        if not group_id:
            return _json_result(
                {
                    "user_id": user_id,
                    "group_id": "",
                    "found": False,
                    "error": "group_id is empty — provide a group_id or ensure the tool has access to a group context",
                }
            )

        mgr = self.relation_manager
        if mgr is None:
            return _json_result(
                {
                    "user_id": user_id,
                    "group_id": group_id,
                    "found": False,
                    "error": "relation_manager not available",
                }
            )

        try:
            relations = await mgr.get_user_relations_in_group(user_id, group_id)
        except Exception:
            return _json_result(
                {
                    "user_id": user_id,
                    "group_id": group_id,
                    "found": False,
                    "error": "lookup_failed",
                }
            )

        if not relations:
            return _json_result(
                {
                    "user_id": user_id,
                    "group_id": group_id,
                    "found": False,
                    "relations": [],
                    "count": 0,
                }
            )

        formatted = [_format_relation(r) for r in relations]
        return _json_result(
            {
                "user_id": user_id,
                "group_id": group_id,
                "found": True,
                "relations": formatted,
                "count": len(formatted),
            }
        )


# ---------------------------------------------------------------------------
# Tool: 群组关系图谱
# ---------------------------------------------------------------------------


@dataclass
class RelationGraphTool(FunctionTool[AstrAgentContext]):
    """列出群组内所有社交关系，按强度排序。Agent 可主动调用以了解群组整体社交动态。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    relation_manager: Any = field(default=None)

    name: str = "list_group_relations"
    description: str = (
        "List all social relationships in a group, strongest first. "
        "Returns the relationship graph data including who relates to whom, "
        "relationship types with Chinese names, strengths (0.0–1.0), and "
        "interaction frequencies. Use this to understand the overall social "
        "dynamics of the group."
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
        group_id = (group_id or "").strip()

        # 自动从事件上下文解析 group_id
        if not group_id:
            try:
                group_id = str(getattr(context.context.event, "unified_msg_origin", ""))
            except Exception:
                pass

        if not group_id:
            return _json_result(
                {
                    "group_id": "",
                    "found": False,
                    "error": "group_id is empty — provide a group_id or ensure the tool has access to a group context",
                }
            )

        mgr = self.relation_manager
        if mgr is None:
            return _json_result(
                {
                    "group_id": group_id,
                    "found": False,
                    "error": "relation_manager not available",
                }
            )

        try:
            relations = await mgr.get_relations_by_group(group_id)
        except Exception:
            return _json_result(
                {
                    "group_id": group_id,
                    "found": False,
                    "error": "lookup_failed",
                }
            )

        if not relations:
            return _json_result(
                {
                    "group_id": group_id,
                    "found": False,
                    "relations": [],
                    "count": 0,
                    "type_summary": {},
                }
            )

        # 按强度排序并格式化
        sorted_rels = sorted(
            relations,
            key=lambda r: getattr(r, "strength", 0.0),
            reverse=True,
        )
        formatted = [_format_relation(r) for r in sorted_rels]

        # 按关系类型统计
        type_breakdown: dict[str, int] = {}
        for r in relations:
            rt = getattr(r, "relation_type", "unknown")
            type_breakdown[rt] = type_breakdown.get(rt, 0) + 1

        # 为每个类型附上中文名
        type_summary: dict[str, dict[str, Any]] = {}
        for rt, count in type_breakdown.items():
            type_summary[rt] = {
                "count": count,
                "name_cn": _CATEGORY_NAMES.get(rt, rt),
            }

        return _json_result(
            {
                "group_id": group_id,
                "found": True,
                "relations": formatted,
                "count": len(formatted),
                "type_summary": type_summary,
            }
        )


__all__ = ["RelationLookupTool", "RelationGraphTool"]
