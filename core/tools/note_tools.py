"""笔记自主增删改查与搜索的 Agent 工具。"""

from __future__ import annotations

import re
from dataclasses import field
from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from pydantic.dataclasses import dataclass

from .agent_scope import resolve_agent_read_scope


@dataclass
class NoteSearchTool(FunctionTool[AstrAgentContext]):
    """搜索笔记。Agent 可主动调用以查找已记录的笔记内容。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    note_manager: Any = field(default=None)

    name: str = "note_search"
    description: str = (
        "Search notes by keyword. Returns matching notes with content snippets."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword"},
                "limit": {
                    "type": "integer",
                    "description": "Max results",
                    "default": 10,
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
    ) -> ToolExecResult:
        """在当前事件作用域中搜索可见笔记。"""

        mgr = self.note_manager
        if mgr is None:
            return "Error: note_manager not available"

        read_scope = resolve_agent_read_scope(context)
        if read_scope is None:
            return "Error: note scope is unavailable"

        notes, total = await mgr.search_for_scope(
            query,
            scope_key=read_scope.session_id,
            user_id=read_scope.user_id,
            limit=limit,
        )
        if not notes:
            return f"No notes found for: {query}"
        lines = [f"Found {total} note(s):"]
        for n in notes:
            lines.append(f"[{n.note_id}] {n.title} (v{n.version}) — {n.content[:150]}")
        return "\n".join(lines)


@dataclass
class NoteReadTool(FunctionTool[AstrAgentContext]):
    """读取笔记完整内容。Agent 可主动调用以获取笔记详情。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    note_manager: Any = field(default=None)

    name: str = "note_read"
    description: str = "Read a note's full content by its ID."
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "note_id": {"type": "integer", "description": "Note ID to read"},
            },
            "required": ["note_id"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        note_id: int = 0,
    ) -> ToolExecResult:
        """在当前事件作用域中读取单条可见笔记。"""

        mgr = self.note_manager
        if mgr is None:
            return "Error: note_manager not available"

        read_scope = resolve_agent_read_scope(context)
        if read_scope is None:
            return "Error: note scope is unavailable"

        note = await mgr.get_note_for_scope(
            note_id,
            scope_key=read_scope.session_id,
            user_id=read_scope.user_id,
        )
        if note is None:
            return f"Note {note_id} not found."
        return (
            f"# {note.title}\n\n{note.content}\n\n"
            f"Tags: {', '.join(note.tags)}\nVersion: {note.version}"
        )


@dataclass
class NoteWriteTool(FunctionTool[AstrAgentContext]):
    """创建或更新笔记。Agent 可主动调用以记录或修改笔记内容。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    note_manager: Any = field(default=None)

    name: str = "note_write"
    description: str = (
        "Create a new note or update an existing one only when the user "
        "explicitly asks to record or change a note. Use note_id to update."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Note title"},
                "content": {"type": "string", "description": "Note content (markdown)"},
                "note_id": {
                    "type": "integer",
                    "description": "Existing note ID to update (omit to create new)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags",
                },
            },
            "required": ["title", "content"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        title: str,
        content: str,
        note_id: int | None = None,
        tags: list[str] | None = None,
    ) -> ToolExecResult:
        """在当前事件作用域中创建或更新人工笔记。"""

        mgr = self.note_manager
        if mgr is None:
            return "Error: note_manager not available"

        validation_error = self._validate_write_input(title, content, tags)
        if validation_error:
            return f"Error: {validation_error}"

        read_scope = resolve_agent_read_scope(context)
        if read_scope is None:
            return "Error: note scope is unavailable"

        title = title.strip()
        content = content.strip()
        tags = [tag.strip() for tag in (tags or [])]
        if note_id is not None:
            note = await mgr.update_note_for_scope(
                int(note_id),
                scope_key=read_scope.session_id,
                user_id=read_scope.user_id,
                title=title,
                content=content,
                tags=tags,
            )
            if note is None:
                return f"Note {note_id} not found."
            return f"Note {note.note_id} updated (v{note.version}): {title}"
        new_id = await mgr.create_note(
            title=title,
            content=content,
            tags=tags,
            user_id=read_scope.user_id,
        )
        return f"Note {new_id} created: {title}"

    @staticmethod
    def _validate_write_input(
        title: str,
        content: str,
        tags: list[str] | None,
    ) -> str | None:
        if not isinstance(title, str) or not title.strip():
            return "title is required"
        if len(title.strip()) > 120:
            return "title must be 120 characters or fewer"
        if not isinstance(content, str) or not content.strip():
            return "content is required"
        if len(content.strip()) > 20000:
            return "content must be 20000 characters or fewer"
        normalized_tags = [tag.strip() for tag in (tags or [])]
        if len(normalized_tags) > 10:
            return "tags must contain at most 10 items"
        tag_pattern = re.compile(r"^[\w\u4e00-\u9fff-]{1,40}$")
        for tag in normalized_tags:
            if not tag_pattern.fullmatch(tag):
                return "each tag must be 1-40 characters and contain only letters, digits, underscore, hyphen, or CJK text"
        return None


__all__ = ["NoteSearchTool", "NoteReadTool", "NoteWriteTool"]
