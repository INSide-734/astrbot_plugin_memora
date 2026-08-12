"""群组黑话/俚语查询的 Agent 工具。"""

from __future__ import annotations

import json
from dataclasses import field
from typing import Any

from astrbot.api.event import AstrMessageEvent
from pydantic.dataclasses import dataclass

from ..jargon.jargon_query import JargonQueryService
from .function_tool import AgentFunctionTool


def _json_result(data: dict[str, Any]) -> str:
    """将工具结果稳定序列化为 JSON 文本。"""
    return json.dumps(data, ensure_ascii=False, default=str)


def _resolve_group_id(event: AstrMessageEvent, group_id: str) -> str:
    """优先使用显式群组标识，否则从当前事件解析会话标识。"""
    gid = (group_id or "").strip()
    if gid:
        return gid
    try:
        return str(getattr(event, "unified_msg_origin", ""))
    except Exception:
        return ""


@dataclass
class JargonExplainTool(AgentFunctionTool):
    """解释群组黑话/俚语。Agent 可主动调用以理解群内的特殊缩写和暗语。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    jargon_query_service: JargonQueryService | None = field(default=None)

    name: str = "explain_jargon"
    description: str = (
        "Explain group-specific slang/jargon terms found in the current conversation. "
        "Use this when you encounter unfamiliar abbreviations, code words, or slang "
        "that may have special meaning in this group."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "term": {
                    "type": "string",
                    "description": "The slang term or abbreviation to look up",
                },
                "group_id": {
                    "type": "string",
                    "description": "Group ID. Auto-detected from conversation context if empty.",
                    "default": "",
                },
            },
            "required": ["term"],
        }
    )

    async def _run(
        self,
        event: AstrMessageEvent,
        term: str,
        group_id: str = "",
    ) -> str:
        """解释当前群组内的指定黑话词条。

        Args:
            event: AstrBot 注入的当前消息事件。
            term: 待解释的黑话词条。
            group_id: 可选群组 ID。

        Returns:
            包含解释结果或稳定错误码的 JSON 文本。
        """

        import asyncio

        term = (term or "").strip()
        group_id = _resolve_group_id(event, group_id)

        if not term:
            return _json_result(
                {"term": "", "found": False, "error": "term is required"}
            )

        svc = self.jargon_query_service
        if svc is None:
            return _json_result(
                {
                    "term": term,
                    "found": False,
                    "error": "jargon_query_service not available",
                }
            )

        try:
            results = await svc.query(term, group_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            return _json_result(
                {
                    "term": term,
                    "group_id": group_id,
                    "found": False,
                    "error": "query_failed",
                }
            )

        if not results:
            return _json_result(
                {"term": term, "group_id": group_id, "found": False, "results": []}
            )

        return _json_result(
            {
                "term": term,
                "group_id": group_id,
                "found": True,
                "count": len(results),
                "results": results,
            }
        )


@dataclass
class JargonListTool(AgentFunctionTool):
    """列出群组所有已确认黑话。Agent 可主动调用以了解群组的特殊词汇表。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    jargon_query_service: JargonQueryService | None = field(default=None)

    name: str = "list_group_jargon"
    description: str = (
        "List all confirmed slang/jargon terms used in this group. "
        "Use this to understand the group's special vocabulary."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "Group ID. Auto-detected from conversation context if empty.",
                    "default": "",
                },
            },
            "required": [],
        }
    )

    async def _run(
        self,
        event: AstrMessageEvent,
        group_id: str = "",
    ) -> str:
        """列出当前群组内已确认的黑话词条。

        Args:
            event: AstrBot 注入的当前消息事件。
            group_id: 可选群组 ID。

        Returns:
            包含词条列表或稳定错误码的 JSON 文本。
        """

        import asyncio

        group_id = _resolve_group_id(event, group_id)

        if not group_id:
            return _json_result(
                {
                    "group_id": "",
                    "found": False,
                    "error": "group_id is empty — provide a group_id or ensure the tool has access to conversation context",
                }
            )

        svc = self.jargon_query_service
        if svc is None:
            return _json_result(
                {
                    "group_id": group_id,
                    "found": False,
                    "error": "jargon_query_service not available",
                }
            )

        try:
            results = await svc.get_group_jargon(group_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            return _json_result(
                {"group_id": group_id, "found": False, "error": "query_failed"}
            )

        return _json_result(
            {
                "group_id": group_id,
                "found": True,
                "count": len(results),
                "results": results,
            }
        )


__all__ = ["JargonExplainTool", "JargonListTool"]
