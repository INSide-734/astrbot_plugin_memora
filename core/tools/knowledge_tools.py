"""知识库自主搜索与读取的 Agent 工具。"""

from __future__ import annotations

import json
from dataclasses import field
from typing import Any

from astrbot.api.event import AstrMessageEvent
from pydantic.dataclasses import dataclass

from .agent_scope import resolve_agent_read_scope
from .function_tool import AgentFunctionTool


def _json_result(data: dict[str, Any]) -> str:
    """将工具结果稳定序列化为 JSON 文本。"""
    return json.dumps(data, ensure_ascii=False, default=str)


@dataclass
class KnowledgeSearchTool(AgentFunctionTool):
    """搜索结构化知识库。Agent 可主动调用以获取事实、概念、规则等结构化知识。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    knowledge_manager: Any = field(default=None)

    name: str = "knowledge_search"
    description: str = (
        "Search the structured knowledge base for facts, concepts, rules, events, "
        "and procedures. Use this when the user asks about stored knowledge, "
        "definitions, how-to procedures, or factual information that may have been "
        "recorded from past conversations. Returns matching entries with content snippets."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keyword or question for the knowledge base. Use concise keywords.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return.",
                    "default": 10,
                },
                "category": {
                    "type": "string",
                    "description": "Optional filter by knowledge type: fact, concept, rule, event, or procedure.",
                    "default": "",
                },
            },
            "required": ["query"],
        }
    )

    async def _run(
        self,
        event: AstrMessageEvent,
        query: str,
        limit: int = 10,
        category: str = "",
    ) -> str:
        """在当前事件作用域中搜索可见的结构化知识。

        Args:
            event: AstrBot 注入的当前消息事件。
            query: 知识检索关键词或问题。
            limit: 最大返回条目数。
            category: 可选知识分类过滤器。

        Returns:
            包含匹配条目或稳定错误码的 JSON 文本。
        """

        mgr = self.knowledge_manager
        if mgr is None:
            return _json_result(
                {
                    "query": query,
                    "count": 0,
                    "results": [],
                    "error": "knowledge_manager not available",
                }
            )
        read_scope = resolve_agent_read_scope(event)
        if read_scope is None:
            return _json_result(
                {"query": query, "count": 0, "results": [], "error": "scope_denied"}
            )

        try:
            entries, total = await mgr.search_for_scope(
                query,
                scope_key=read_scope.session_id,
                limit=limit,
                category=category,
            )
        except Exception:
            return _json_result(
                {"query": query, "count": 0, "results": [], "error": "search_failed"}
            )

        if not entries:
            return _json_result({"query": query, "count": 0, "results": []})

        results = []
        for e in entries:
            results.append(
                {
                    "entry_id": e.entry_id,
                    "title": e.title,
                    "content": e.content[:300],
                    "category": e.category.value
                    if hasattr(e.category, "value")
                    else str(e.category),
                    "confidence": e.confidence,
                    "tags": e.tags,
                    "access_count": e.access_count,
                }
            )

        return _json_result({"query": query, "count": total, "results": results})


@dataclass
class KnowledgeReadTool(AgentFunctionTool):
    """读取特定知识条目的完整内容。Agent 可在搜索后获取条目详情。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    knowledge_manager: Any = field(default=None)

    name: str = "knowledge_read"
    description: str = (
        "Read the full content of a specific knowledge entry by its ID. "
        "Use this after knowledge_search to get the complete details of a "
        "relevant entry."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "integer",
                    "description": "The knowledge entry ID to read (from knowledge_search results).",
                },
            },
            "required": ["entry_id"],
        }
    )

    async def _run(
        self,
        event: AstrMessageEvent,
        entry_id: int = 0,
    ) -> str:
        """在当前事件作用域中读取单条可见知识。

        Args:
            event: AstrBot 注入的当前消息事件。
            entry_id: 待读取的知识条目 ID。

        Returns:
            包含知识详情、未命中状态或稳定错误码的 JSON 文本。
        """

        mgr = self.knowledge_manager
        if mgr is None:
            return _json_result(
                {
                    "entry_id": entry_id,
                    "found": False,
                    "error": "knowledge_manager not available",
                }
            )
        read_scope = resolve_agent_read_scope(event)
        if read_scope is None:
            return _json_result(
                {"entry_id": entry_id, "found": False, "error": "scope_denied"}
            )

        try:
            entry = await mgr.get_entry_for_scope(
                entry_id,
                scope_key=read_scope.session_id,
            )
        except Exception:
            return _json_result(
                {"entry_id": entry_id, "found": False, "error": "read_failed"}
            )

        if entry is None:
            return _json_result({"entry_id": entry_id, "found": False})

        return _json_result(
            {
                "entry_id": entry.entry_id,
                "found": True,
                "title": entry.title,
                "content": entry.content,
                "category": entry.category.value
                if hasattr(entry.category, "value")
                else str(entry.category),
                "confidence": entry.confidence,
                "tags": entry.tags,
                "access_count": entry.access_count,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
            }
        )


__all__ = ["KnowledgeSearchTool", "KnowledgeReadTool"]
