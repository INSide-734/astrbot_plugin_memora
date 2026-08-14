"""基于 LLM 从对话内容中提取用户标签和偏好。"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from astrbot.api import logger

from ..domain.models import TagCategory, UserTag

_EXTRACTION_PROMPT = """Analyze this conversation and extract user profile signals.
Return ONLY valid JSON, no explanation.

{
  "tags": [
    {"category": "interest|personality|habit|relation|knowledge|preference", "value": "tag text", "confidence": 0.0-1.0}
  ],
  "preferences": {
    "reply_style": "casual|formal|concise|detailed|null",
    "preferred_topics": ["topic1"],
    "avoided_topics": ["topic1"]
  }
}

Rules:
- Confidence > 0.7: strongly evidenced, > 0.4: reasonably inferred, < 0.4: weak signal
- Max 5 tags per extraction
- Tag "value" should be concise (1-5 words)
"""


class ProfileExtractor:
    """通过 LLM 从对话证据中提取用户标签和偏好。"""

    def __init__(self, llm_client: Any = None) -> None:
        """保存实现 ``complete(prompt)`` 的可选 LLM 客户端。"""

        self._llm_client = llm_client

    async def extract(
        self, user_message: str, bot_response: str = "", context: str = ""
    ) -> tuple[list[UserTag], dict[str, Any]]:
        """调用 LLM 提取结构化画像；普通失败返回空 proposal。"""

        if not self._llm_client:
            return [], {}

        conversation = f"User: {user_message}"
        if bot_response:
            conversation += f"\nBot: {bot_response}"
        if context:
            conversation = f"Context: {context}\n\n{conversation}"

        prompt = f"{_EXTRACTION_PROMPT}\n\nConversation:\n{conversation}"

        try:
            raw = await self._llm_client.complete(prompt)
            result = self._parse_response(raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(
                "[画像提取] LLM 调用失败，异常类型=%s",
                exc.__class__.__name__,
            )
            return [], {}

        tags = self._build_tags(result.get("tags", []))
        preferences = result.get("preferences", {}) or {}
        return tags, preferences

    @staticmethod
    def extract_keywords_fallback(user_message: str) -> list[UserTag]:
        """在不调用 Provider 时提取有限的显式关键词标签。"""

        message_lower = user_message.lower()
        tags: list[UserTag] = []

        keyword_map: dict[str, tuple[TagCategory, float]] = {
            "喜欢": (TagCategory.PREFERENCE, 0.4),
            "讨厌": (TagCategory.PREFERENCE, 0.5),
            "经常": (TagCategory.HABIT, 0.4),
            "我是": (TagCategory.RELATION, 0.6),
            "我在学": (TagCategory.KNOWLEDGE, 0.5),
            "爱好": (TagCategory.INTEREST, 0.5),
        }

        for keyword, (category, confidence) in keyword_map.items():
            if keyword in message_lower:
                idx = message_lower.index(keyword)
                snippet = message_lower[idx : idx + 30]
                tags.append(
                    UserTag(
                        category=category,
                        value=snippet.strip()[:20],
                        confidence=confidence,
                        source="keyword",
                    )
                )
        return tags

    @staticmethod
    def _parse_response(raw: str) -> dict[str, Any]:
        """解析纯 JSON 或 Markdown 代码块中的首个 JSON 对象。"""

        raw = raw.strip()
        if raw.startswith("```"):
            lines = [line for line in raw.split("\n") if not line.startswith("```")]
            raw = "\n".join(lines)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            return {}

    @staticmethod
    def _build_tags(tag_data: list[dict[str, Any]] | None) -> list[UserTag]:
        """把不可信标签数据规范为最多五个领域模型。"""

        tags: list[UserTag] = []
        for item in tag_data or []:
            try:
                category = TagCategory(item.get("category", "custom"))
            except ValueError:
                category = TagCategory.CUSTOM
            value = str(item.get("value", "")).strip()
            if not value or len(value) > 50:
                continue
            confidence = max(0.1, min(1.0, float(item.get("confidence", 0.5))))
            tags.append(
                UserTag(
                    category=category,
                    value=value,
                    confidence=confidence,
                    source="llm",
                )
            )
        return tags[:5]


__all__ = ["ProfileExtractor"]
