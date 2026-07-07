"""JSON-safe models for memory review queue items and actions."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any


JsonDict = dict[str, Any]


class ReviewStatus(Enum):
    OPEN = "open"
    APPROVED = "approved"
    EDITED = "edited"
    MERGED = "merged"
    ARCHIVED = "archived"
    DELETED = "deleted"
    SAFE = "safe"


class ReviewReason(Enum):
    LOW_CONFIDENCE = "low_confidence"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    SENSITIVE = "sensitive"
    STALE = "stale"
    NOISY = "noisy"
    PROVENANCE_MISSING = "provenance_missing"


class ReviewSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def json_safe(value: Any) -> Any:
    """Return a recursively JSON-safe copy of a review payload."""
    if value is None or isinstance(value, str | int | float | bool):
        return value

    if isinstance(value, Enum):
        return value.value

    if hasattr(value, "to_dict") and callable(value.to_dict):
        return json_safe(value.to_dict())

    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: json_safe(getattr(value, item.name))
            for item in fields(value)
        }

    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}

    if isinstance(value, set | frozenset):
        return sorted((json_safe(item) for item in value), key=str)

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [json_safe(item) for item in value]

    return str(value)


def json_copy(value: Any) -> Any:
    """Round-trip through JSON to avoid leaking mutable internal state."""
    return json.loads(json.dumps(json_safe(value), ensure_ascii=False))


def normalize_status(value: ReviewStatus | str) -> ReviewStatus:
    if isinstance(value, ReviewStatus):
        return value
    return ReviewStatus(str(value))


def normalize_reason(value: ReviewReason | str) -> ReviewReason:
    if isinstance(value, ReviewReason):
        return value
    return ReviewReason(str(value))


def normalize_severity(value: ReviewSeverity | str) -> ReviewSeverity:
    if isinstance(value, ReviewSeverity):
        return value
    return ReviewSeverity(str(value))


@dataclass(slots=True)
class ReviewItem:
    memory_id: str
    reasons: list[ReviewReason | str]
    severity: ReviewSeverity | str
    status: ReviewStatus | str = ReviewStatus.OPEN
    content_preview: str = ""
    metadata: JsonDict = field(default_factory=dict)
    item_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> JsonDict:
        return {
            "item_id": str(self.item_id),
            "memory_id": str(self.memory_id),
            "reasons": [normalize_reason(reason).value for reason in self.reasons],
            "severity": normalize_severity(self.severity).value,
            "status": normalize_status(self.status).value,
            "content_preview": str(self.content_preview),
            "metadata": json_copy(self.metadata),
            "created_at": float(self.created_at),
            "updated_at": float(self.updated_at),
        }


@dataclass(slots=True)
class ReviewAction:
    item_id: str
    action: ReviewStatus | str
    actor_id: str | None = None
    payload: JsonDict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    action_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> JsonDict:
        return {
            "action_id": str(self.action_id),
            "item_id": str(self.item_id),
            "action": normalize_status(self.action).value,
            "actor_id": self.actor_id,
            "payload": json_copy(self.payload),
            "created_at": float(self.created_at),
        }


@dataclass(slots=True)
class ReviewActionResult:
    item: ReviewItem | Mapping[str, Any] | None
    action: ReviewAction | Mapping[str, Any] | None
    success: bool
    message: str = ""

    def to_dict(self) -> JsonDict:
        return {
            "item": json_copy(self.item),
            "action": json_copy(self.action),
            "success": bool(self.success),
            "message": str(self.message),
        }


__all__ = [
    "JsonDict",
    "ReviewAction",
    "ReviewActionResult",
    "ReviewItem",
    "ReviewReason",
    "ReviewSeverity",
    "ReviewStatus",
    "json_copy",
    "json_safe",
    "normalize_reason",
    "normalize_severity",
    "normalize_status",
]
