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
    def __init__(self, llm_client: Any = None, min_length: int = 100) -> None:
        self._llm_client = llm_client
        self._min_length = min_length

    async def generate(
        self, conversation_text: str, context: str = ""
    ) -> dict[str, Any] | None:
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
        lines = text.strip().split("\n")
        return lines[0][:80] if lines else "Note"


__all__ = ["NoteGenerator"]
