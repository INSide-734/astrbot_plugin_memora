from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RecallStrategy(str, Enum):
    CONTEXTUAL_SIMILARITY = "contextual_similarity"
    TOPIC_ASSOCIATION = "topic_association"
    PREFERENCE_QUERY = "preference_query"
    RELATIONSHIP_REVIEW = "relationship_review"


@dataclass(frozen=True, slots=True)
class RecallRequest:
    strategy: RecallStrategy
    query: str
    k: int = 5
    session_id: str | None = None
    persona_id: str | None = None
    emotion_context: list[str] | None = None
    memory_types: list[str] | None = None


__all__ = [
    "RecallRequest",
    "RecallStrategy",
]
