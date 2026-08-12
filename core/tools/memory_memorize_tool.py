"""供 Agent 主动调用的长期记忆写入工具。"""

import asyncio
import json
from dataclasses import field
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.platform import MessageType
from pydantic.dataclasses import dataclass

from ..utils import get_persona_id
from .function_tool import AgentFunctionTool


def _json_result(data: dict[str, Any]) -> str:
    """将工具结果稳定序列化为 JSON 文本。"""
    return json.dumps(data, ensure_ascii=False, default=str)


def _normalize_list(value: Any, limit: int = 5) -> list[str]:
    """把单值或列表规整为去空白、有限长的字符串列表。

    Args:
        value: 待规整的单值、列表或空值。
        limit: 最多保留的非空字符串数量。

    Returns:
        保留原顺序的非空字符串列表。
    """

    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()][:limit]
    if isinstance(value, str) and value.strip():
        return [value.strip()][:limit]
    return []


@dataclass
class MemoryMemorizeTool(AgentFunctionTool):
    """长期记忆主动写入工具。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    context: Any = None
    memory_engine: Any = None
    memory_processor: Any = None

    name: str = "memorize_long_term_memory"
    description: str = (
        "Memorize durable long-term memory when the user explicitly asks to remember something, "
        "or when stable preferences, identity details, agreements, or project context appear. "
        "Write concise factual memory, not the full conversation."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "memory": {
                    "type": "string",
                    "description": "Concise factual long-term memory to save. Do not copy the full conversation.",
                },
                "topics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional short topic tags for this memory, up to 5.",
                    "default": [],
                },
                "key_facts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional key facts supporting the memory, up to 5.",
                    "default": [],
                },
                "sentiment": {
                    "type": "string",
                    "description": "Sentiment of the memory: positive, neutral, or negative.",
                    "default": "neutral",
                },
                "importance": {
                    "type": "number",
                    "description": "Importance from 0.0 to 1.0. Use higher values for durable preferences, commitments, or identity facts.",
                    "default": 0.7,
                },
                "reason": {
                    "type": "string",
                    "description": "Optional short reason why this information should be remembered.",
                    "default": "",
                },
            },
            "required": ["memory"],
        }
    )

    async def _run(
        self,
        event: AstrMessageEvent,
        memory: str,
        topics: list[str] | None = None,
        key_facts: list[str] | None = None,
        sentiment: str = "neutral",
        importance: float = 0.7,
        reason: str = "",
    ) -> str:
        """在当前事件作用域内写入用户明确要求保存的长期记忆。

        Args:
            event: AstrBot 注入的当前消息事件。
            memory: 待保存的简洁事实文本。
            topics: 可选主题标签。
            key_facts: 可选支撑事实。
            sentiment: 记忆情感分类。
            importance: 记忆重要度。
            reason: 可选保存原因。

        Returns:
            包含写入结果、canonical ID 或稳定错误码的 JSON 文本。
        """
        cleaned_memory = (memory or "").strip()
        if not cleaned_memory:
            return _json_result({"memorized": False, "error": "memory is empty"})

        normalized_sentiment = str(sentiment or "neutral").strip().lower()
        if normalized_sentiment not in {"positive", "neutral", "negative"}:
            normalized_sentiment = "neutral"

        if (
            self.context is None
            or self.memory_engine is None
            or self.memory_processor is None
        ):
            return _json_result(
                {
                    "memorized": False,
                    "error": "memory memorize tool is not initialized",
                }
            )

        try:
            session_id = event.unified_msg_origin
            persona_id = await get_persona_id(self.context, event)
            is_group_chat = event.get_message_type() == MessageType.GROUP_MESSAGE

            structured_data = {
                "summary": cleaned_memory,
                "topics": _normalize_list(topics),
                "key_facts": _normalize_list(key_facts),
                "sentiment": normalized_sentiment,
                "importance": importance,
            }

            mem_result = self.memory_processor.build_memory_from_structured_data(
                structured_data=structured_data,
                is_group_chat=is_group_chat,
                fallback_excerpt=cleaned_memory,
            )
            content = mem_result["content"]
            metadata = mem_result["metadata"]
            normalized_importance = mem_result["importance"]
            atoms = mem_result.get("atoms", [])
            metadata["source_window"] = {
                "session_id": session_id,
                "triggered_by": "agent_tool",
                "tool_name": self.name,
            }
            metadata["memory_origin"] = "agent_memorize_tool"
            cleaned_reason = (reason or "").strip()
            if cleaned_reason:
                metadata["memorize_reason"] = cleaned_reason

            memory_id = await self.memory_engine.add_memory(
                content=content,
                session_id=session_id,
                persona_id=persona_id,
                importance=normalized_importance,
                metadata=metadata,
                atoms=atoms,
            )

            return _json_result(
                {
                    "memorized": True,
                    "id": memory_id,
                    "content": content,
                    "importance": normalized_importance,
                    "session_id": session_id,
                    "persona_id": persona_id,
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"记忆工具写入失败: {e}", exc_info=True)
            return _json_result({"memorized": False, "error": "internal_error"})
