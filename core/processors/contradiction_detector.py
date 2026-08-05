"""从 canonical 快照生成只读冲突或状态更新候选。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import combinations
from typing import Sequence

from ..models.memory_evolution import MemorySourceRef


@dataclass(frozen=True, slots=True)
class ConflictCandidate:
    """保存一对同主体 source 的冲突类型与 revision 证据。"""

    source_id: int
    source_revision: str
    target_id: int
    target_revision: str
    source_occurred_at: datetime
    target_occurred_at: datetime
    subject_key: str
    conflict_type: str
    confidence: float

    @property
    def candidate_key(self) -> str:
        """返回绑定 source/target revision 的稳定候选键。"""

        payload = "|".join(
            (
                str(self.source_id),
                self.source_revision,
                str(self.target_id),
                self.target_revision,
                self.conflict_type,
            )
        )
        return f"conflict:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


class ContradictionDetector:
    """使用词面启发式预筛同主体冲突，不搜索或更新 canonical。"""

    JACCARD_CONFLICT_THRESHOLD = 0.4

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_conflicts: int = 5,
        jaccard_threshold: float = JACCARD_CONFLICT_THRESHOLD,
    ) -> None:
        """保存候选上限和相似度阈值。"""

        self._enabled = bool(enabled)
        self._max_conflicts = max(0, int(max_conflicts))
        self._jaccard_threshold = min(1.0, max(0.0, float(jaccard_threshold)))

    @property
    def enabled(self) -> bool:
        """返回当前是否允许生成冲突候选。"""

        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """切换候选生成开关。"""

        self._enabled = bool(value)

    def detect_candidates(
        self,
        sources: Sequence[MemorySourceRef],
    ) -> tuple[ConflictCandidate, ...]:
        """比较同 scope、同可信主体 source，并返回有界只读候选。"""

        if not self._enabled or self._max_conflicts == 0 or len(sources) < 2:
            return ()
        candidates: list[ConflictCandidate] = []
        ordered = sorted(sources, key=lambda item: (item.occurred_at, item.memory_id))
        for left, right in combinations(ordered, 2):
            if not _same_subject(left, right):
                continue
            old, new = (
                (left, right)
                if left.occurred_at <= right.occurred_at
                else (right, left)
            )
            old_content = old.content or ""
            new_content = new.content or ""
            if not old_content.strip() or not new_content.strip():
                continue
            overlap = _jaccard(set(_tokenize(old_content)), set(_tokenize(new_content)))
            if overlap < self._jaccard_threshold:
                continue
            if not _detect_semantic_contradiction(new_content, old_content):
                continue
            conflict_type = (
                "temporal_update"
                if _is_temporal_update(old_content, new_content, old, new)
                else "polarity_conflict"
            )
            candidates.append(
                ConflictCandidate(
                    source_id=new.memory_id,
                    source_revision=new.revision_token,
                    target_id=old.memory_id,
                    target_revision=old.revision_token,
                    source_occurred_at=new.occurred_at,
                    target_occurred_at=old.occurred_at,
                    subject_key=new.subject_key or "",
                    conflict_type=conflict_type,
                    confidence=min(1.0, 0.6 + overlap * 0.4),
                )
            )
            if len(candidates) >= self._max_conflicts:
                break
        return tuple(candidates)


def _same_subject(first: MemorySourceRef, second: MemorySourceRef) -> bool:
    """要求 scope 和匿名主体键都明确一致。"""

    return bool(
        first.scope_key == second.scope_key
        and first.subject_key
        and first.subject_key == second.subject_key
    )


def _tokenize(text: str) -> list[str]:
    """委托共享中英文分词器，避免处理器内维护第二套规则。"""

    from ..utils.text_utils import tokenize_cjk_words

    return tokenize_cjk_words(text)


def _jaccard(set_a: set, set_b: set) -> float:
    """计算两个 token 集合的 Jaccard 相似度。"""

    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _detect_semantic_contradiction(new_text: str, old_text: str) -> bool:
    """用肯定/否定极性差异做低成本候选预筛。"""

    negation_words = (
        "不",
        "没",
        "别",
        "戒",
        "停止",
        "放弃",
        "不再",
        "取消",
        "拒绝",
        "否",
    )
    affirmative_words = (
        "喜欢",
        "爱",
        "想要",
        "经常",
        "一直",
        "总是",
        "是",
        "有",
        "会",
        "可以",
    )
    new_negative = any(word in new_text for word in negation_words)
    old_affirmative = any(word in old_text for word in affirmative_words)
    old_negative = any(word in old_text for word in negation_words)
    new_affirmative = any(word in new_text for word in affirmative_words)
    return (new_negative and old_affirmative) or (old_negative and new_affirmative)


def _is_temporal_update(
    old_text: str,
    new_text: str,
    old: MemorySourceRef,
    new: MemorySourceRef,
) -> bool:
    """识别显式历史到当前的状态变化，避免误标为同时冲突。"""

    historical_words = ("以前", "曾经", "过去", "去年", "当时", "原来")
    current_words = ("现在", "目前", "如今", "后来", "已经")
    explicit_change = any(word in old_text for word in historical_words) and any(
        word in new_text for word in current_words
    )
    return explicit_change or new.occurred_at - old.occurred_at >= timedelta(days=1)


__all__ = ["ConflictCandidate", "ContradictionDetector"]
