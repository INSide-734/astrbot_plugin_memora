"""基于 LLM 从重要对话中生成笔记。"""

from __future__ import annotations

import json
import re
from typing import Any

_GENERATE_PROMPT = """Summarize this conversation into a concise note. Return ONLY JSON:
{{"title": "note title (max 80 chars)", "content": "key points in markdown", "tags": ["tag1", "tag2"]}}

Conversation:
{conversation}"""


class NoteGenerator:
    """通过 LLM 从有限 canonical evidence 生成结构化笔记。"""

    def __init__(self, llm_client: Any = None, min_length: int = 100) -> None:
        """保存可选 LLM 客户端和最小输入长度。

        Args:
            llm_client: 实现 ``complete(prompt)`` 的可选 LLM 客户端。
            min_length: 允许生成笔记的最小输入字符数。
        """

        self._llm_client = llm_client
        self._min_length = min_length

    async def generate(
        self, conversation_text: str, context: str = ""
    ) -> dict[str, Any] | None:
        """调用 LLM 生成笔记，输入不足或响应无法解析时返回空值。

        Args:
            conversation_text: 用于生成笔记的有限 canonical 正文。
            context: 可选的补充上下文。

        Returns:
            包含标题、正文和标签的字典；无法生成时返回 ``None``。
        """

        if not self._llm_client or len(conversation_text) < self._min_length:
            return None
        prompt = _GENERATE_PROMPT.format(conversation=conversation_text[:2000])
        if context:
            prompt = f"Context: {context}\n\n{prompt}"
        raw = ""
        try:
            raw = await self._llm_client.complete(prompt)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = "\n".join(
                    line for line in raw.split("\n") if not line.startswith("```")
                )
            data = json.loads(raw)
        except Exception:
            match = re.search(r"\{[\s\S]*\}", raw) if raw else None
            if match:
                try:
                    data = json.loads(match.group())
                except Exception:
                    return None
            else:
                return None

        return {
            "title": str(data.get("title", "Note"))[:80],
            "content": str(data.get("content", "")),
            "tags": list(data.get("tags", []) or []),
        }

    @staticmethod
    def extract_title_fallback(text: str) -> str:
        """从首行提取不超过 80 个字符的回退标题。

        Args:
            text: 用于提取标题的原始文本。

        Returns:
            截断后的首行标题；无可用首行时返回固定标题。
        """

        lines = text.strip().split("\n")
        return lines[0][:80] if lines else "Note"


__all__ = ["NoteGenerator"]
