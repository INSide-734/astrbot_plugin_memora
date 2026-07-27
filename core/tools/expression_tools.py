"""已学习表达模式回忆的 Agent 工具。"""

from __future__ import annotations

import json
from dataclasses import field
from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from pydantic.dataclasses import dataclass

from ..expression.models import ExpressionPattern
from ..expression.pattern_learner import ExpressionPatternLearner


def _json_result(data: dict[str, Any]) -> str:
    """将工具结果稳定序列化为 JSON 文本。"""
    return json.dumps(data, ensure_ascii=False, default=str)


def _resolve_group_id(context: ContextWrapper[AstrAgentContext], group_id: str) -> str:
    """通过 context 自动解析 group_id。"""
    gid = (group_id or "").strip()
    if gid:
        return gid
    try:
        event = context.context.event
        return str(getattr(event, "unified_msg_origin", ""))
    except Exception:
        return ""


@dataclass
class ExpressionRecallTool(FunctionTool[AstrAgentContext]):
    """回忆已学习的表达模式，用于判断 Bot 在特定情境下应如何回复。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    expression_learner: ExpressionPatternLearner | None = field(default=None)

    name: str = "recall_expressions"
    description: str = (
        "Recall learned expression patterns for how the bot should respond in specific situations. "
        "Returns (situation, expression) pairs that show how the bot has successfully replied in similar contexts. "
        "Use this when you want to match the bot's established speaking style "
        "or when the user references the bot's past way of responding."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "situation": {
                    "type": "string",
                    "description": "Optional situation keyword to filter patterns. If empty, returns top patterns across all situations.",
                    "default": "",
                },
                "group_id": {
                    "type": "string",
                    "description": "Group ID. Auto-detected from conversation context if empty.",
                    "default": "",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of patterns to return.",
                    "default": 5,
                },
            },
            "required": [],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        situation: str = "",
        group_id: str = "",
        limit: int = 5,
    ) -> ToolExecResult:
        import asyncio

        situation = (situation or "").strip()
        group_id = _resolve_group_id(context, group_id)

        if not group_id:
            return _json_result(
                {
                    "group_id": "",
                    "found": False,
                    "error": "group_id is empty — provide a group_id or ensure the tool has access to conversation context",
                }
            )

        learner = self.expression_learner
        if learner is None:
            return _json_result(
                {
                    "group_id": group_id,
                    "found": False,
                    "error": "expression_learner not available",
                }
            )

        try:
            patterns: list[
                ExpressionPattern
            ] = await learner.get_patterns_for_injection(
                group_id, persona_id="default", user_id=None, limit=max(limit, 1)
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return _json_result(
                {
                    "group_id": group_id,
                    "found": False,
                    "error": "recall_failed",
                }
            )

        # Filter by situation keyword if provided
        if situation:
            patterns = [p for p in patterns if situation.lower() in p.situation.lower()]
            patterns = patterns[: max(limit, 1)]

        if not patterns:
            return _json_result(
                {
                    "group_id": group_id,
                    "found": False,
                    "situation_filter": situation or None,
                    "patterns": [],
                    "formatted_prompt": "",
                }
            )

        # Build formatted prompt string
        prompt_lines = ["[学习到的表达习惯]"]
        for p in patterns:
            prompt_lines.append(
                f"- 当遇到类似「{p.situation}」的情境时，可以回复「{p.expression}」"
            )
        formatted_prompt = "\n".join(prompt_lines)

        return _json_result(
            {
                "group_id": group_id,
                "found": True,
                "count": len(patterns),
                "situation_filter": situation or None,
                "patterns": [
                    {
                        "pattern_id": p.pattern_id,
                        "situation": p.situation,
                        "expression": p.expression,
                        "weight": p.weight,
                        "usage_count": p.usage_count,
                        "created_at": p.created_at,
                        "last_used_at": p.last_used_at,
                    }
                    for p in patterns
                ],
                "formatted_prompt": formatted_prompt,
            }
        )


__all__ = ["ExpressionRecallTool"]
