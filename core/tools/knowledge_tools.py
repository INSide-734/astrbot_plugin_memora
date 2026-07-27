"""知识库自主搜索与读取的 Agent 工具。"""

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


@dataclass
class KnowledgeSearchTool(FunctionTool[AstrAgentContext]):
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

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        query: str,
        limit: int = 10,
        category: str = "",
    ) -> ToolExecResult:
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

        try:
            entries, total = await mgr.search(query, limit=limit, category=category)
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
class KnowledgeReadTool(FunctionTool[AstrAgentContext]):
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

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        entry_id: int = 0,
    ) -> ToolExecResult:
        mgr = self.knowledge_manager
        if mgr is None:
            return _json_result(
                {
                    "entry_id": entry_id,
                    "found": False,
                    "error": "knowledge_manager not available",
                }
            )

        try:
            entry = await mgr.get_entry(entry_id)
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
                "source_ids": entry.source_ids,
                "access_count": entry.access_count,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
            }
        )


__all__ = ["KnowledgeSearchTool", "KnowledgeReadTool"]
