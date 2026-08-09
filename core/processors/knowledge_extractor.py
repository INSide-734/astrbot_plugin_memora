"""基于 LLM 从记忆原子中提取知识条目。"""

from __future__ import annotations

import json
import re
from typing import Any

from ..features.knowledge.domain.models import KnowledgeEntry, KnowledgeType

_EXTRACT_PROMPT = """Extract structured knowledge from this memory. Return ONLY JSON:
{"title": "concise title", "content": "1-3 sentence summary", "category": "fact|concept|rule|event|procedure", "confidence": 0.0-1.0, "tags": ["tag1"]}"""


class KnowledgeExtractor:
    def __init__(self, llm_client: Any = None) -> None:
        self._llm_client = llm_client

    async def extract(
        self, memory_content: str, memory_type: str = ""
    ) -> KnowledgeEntry | None:
        if not self._llm_client or len(memory_content) < 20:
            return None
        prompt = f"{_EXTRACT_PROMPT}\n\nMemory: {memory_content[:500]}"
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

        return KnowledgeEntry(
            title=str(data.get("title", ""))[:100],
            content=str(data.get("content", ""))[:2000],
            category=KnowledgeType(data.get("category", "fact")),
            confidence=max(0.1, min(1.0, float(data.get("confidence", 0.5)))),
            tags=list(data.get("tags", []) or []),
        )


__all__ = ["KnowledgeExtractor"]
