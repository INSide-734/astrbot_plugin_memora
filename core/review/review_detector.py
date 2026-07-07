"""Deterministic detector for memory review queue candidates."""

from __future__ import annotations

import re
import string
from collections.abc import Mapping, Sequence
from typing import Any

from .models import ReviewItem, ReviewReason, ReviewSeverity


_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]")
_VISIBLE_RE = re.compile(r"\S")


class ReviewDetector:
    """Detect memory records that should be reviewed by a human/operator."""

    def __init__(
        self,
        *,
        sensitive_markers: Sequence[str] | None = None,
        duplicate_threshold: float = 0.86,
        stale_importance_max: float = 7.0,
    ) -> None:
        self.sensitive_markers = [
            marker for marker in (sensitive_markers or []) if marker
        ]
        self.duplicate_threshold = float(duplicate_threshold)
        self.stale_importance_max = float(stale_importance_max)

    def detect(
        self,
        *,
        memories: Sequence[Mapping[str, Any]],
        quality_stats: Mapping[str, Any] | None = None,
    ) -> list[ReviewItem]:
        low_confidence_ids = {
            str(item)
            for item in self._quality_list(quality_stats, "low_confidence_ids")
        }
        duplicate_ids = self._detect_duplicate_ids(memories)
        items: list[ReviewItem] = []

        for memory in memories:
            memory_id = str(memory.get("id") or memory.get("memory_id") or "")
            if not memory_id:
                continue

            content = str(memory.get("content") or "")
            metadata = self._metadata(memory)
            reasons: list[ReviewReason] = []

            if memory_id in low_confidence_ids:
                reasons.append(ReviewReason.LOW_CONFIDENCE)
            if memory_id in duplicate_ids:
                reasons.append(ReviewReason.DUPLICATE)
            if self._is_stale(memory, metadata):
                reasons.append(ReviewReason.STALE)
            if self._is_sensitive(content):
                reasons.append(ReviewReason.SENSITIVE)
            if self._is_noisy(content):
                reasons.append(ReviewReason.NOISY)
            if self._is_provenance_missing(memory, metadata):
                reasons.append(ReviewReason.PROVENANCE_MISSING)

            if reasons:
                items.append(
                    ReviewItem(
                        memory_id=memory_id,
                        reasons=[reason.value for reason in reasons],
                        severity=self._severity_for(reasons).value,
                        content_preview=content[:160],
                        metadata={
                            "detected_reasons": [reason.value for reason in reasons],
                        },
                    )
                )

        return items

    @staticmethod
    def _quality_list(
        quality_stats: Mapping[str, Any] | None,
        key: str,
    ) -> Sequence[Any]:
        if quality_stats is None:
            return []
        value = quality_stats.get(key, [])
        if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            return value
        return []

    @staticmethod
    def _metadata(memory: Mapping[str, Any]) -> Mapping[str, Any]:
        metadata = memory.get("metadata")
        return metadata if isinstance(metadata, Mapping) else {}

    def _detect_duplicate_ids(
        self,
        memories: Sequence[Mapping[str, Any]],
    ) -> set[str]:
        tokens_by_id: list[tuple[str, set[str]]] = []
        duplicate_ids: set[str] = set()
        for memory in memories:
            memory_id = str(memory.get("id") or memory.get("memory_id") or "")
            if not memory_id:
                continue
            tokens = self._tokens(str(memory.get("content") or ""))
            if not tokens:
                continue
            for other_id, other_tokens in tokens_by_id:
                if self._overlap(tokens, other_tokens) > self.duplicate_threshold:
                    duplicate_ids.add(memory_id)
                    duplicate_ids.add(other_id)
            tokens_by_id.append((memory_id, tokens))
        return duplicate_ids

    @staticmethod
    def _tokens(content: str) -> set[str]:
        normalized = content.casefold().strip()
        raw_tokens = _WORD_RE.findall(normalized)
        tokens = set(raw_tokens)
        cjk_chars = _CJK_RE.findall(normalized)
        if len(cjk_chars) > 1:
            tokens.update(
                "".join(cjk_chars[index : index + 2])
                for index in range(len(cjk_chars) - 1)
            )
        return tokens

    @staticmethod
    def _overlap(left: set[str], right: set[str]) -> float:
        denominator = min(len(left), len(right))
        if denominator <= 0:
            return 0.0
        return len(left & right) / denominator

    def _is_stale(self, memory: Mapping[str, Any], metadata: Mapping[str, Any]) -> bool:
        try:
            last_accessed_days = int(metadata.get("last_accessed_days", 0))
        except (TypeError, ValueError):
            return False
        try:
            importance = float(memory.get("importance", metadata.get("importance", 0)))
        except (TypeError, ValueError):
            importance = 0.0
        return last_accessed_days >= 180 and importance <= self.stale_importance_max

    def _is_sensitive(self, content: str) -> bool:
        folded_content = content.casefold()
        return any(marker.casefold() in folded_content for marker in self.sensitive_markers)

    @staticmethod
    def _is_noisy(content: str) -> bool:
        visible = _VISIBLE_RE.findall(content)
        if len(visible) < 4:
            return True
        punctuation_count = sum(
            1
            for char in visible
            if char in string.punctuation or char in "，。！？；：、（）【】《》“”‘’"
        )
        return punctuation_count / max(1, len(visible)) >= 0.6

    @staticmethod
    def _is_provenance_missing(
        memory: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> bool:
        return not any(
            memory.get(key) or metadata.get(key)
            for key in ("session_id", "source", "created_at")
        )

    @staticmethod
    def _severity_for(reasons: Sequence[ReviewReason]) -> ReviewSeverity:
        if ReviewReason.SENSITIVE in reasons:
            return ReviewSeverity.HIGH
        if (
            ReviewReason.LOW_CONFIDENCE in reasons
            or ReviewReason.DUPLICATE in reasons
            or ReviewReason.CONFLICT in reasons
        ):
            return ReviewSeverity.MEDIUM
        return ReviewSeverity.LOW


__all__ = ["ReviewDetector"]
