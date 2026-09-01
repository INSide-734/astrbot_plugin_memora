"""总结窗口收口前的 outcome 与 ledger 一致性校验。"""

from __future__ import annotations

from ...reflection.domain.summary_models import (
    CandidateIntent,
    CandidateLedgerStatus,
    WindowOutcome,
)


def valid_window_outcome(outcome: WindowOutcome) -> bool:
    """确保收口计数、候选动作和 ledger 状态彼此一致。"""
    if not isinstance(outcome, WindowOutcome):
        return False
    expected = {
        "canonical": outcome.canonical_count,
        "quarantined": outcome.quarantine_count,
        "discard": outcome.discard_count,
        "mark_write": outcome.mark_write_count,
        "failed": outcome.failed_count,
        "skipped_idempotent": outcome.skipped_idempotent_count,
    }
    actual = {key: 0 for key in expected}
    unknown = 0
    for intent in outcome.candidate_slots:
        if not isinstance(intent, CandidateIntent):
            return False
        if intent.status is CandidateLedgerStatus.UNKNOWN:
            if intent.disposition is not None:
                return False
            unknown += 1
            continue
        if intent.status is CandidateLedgerStatus.FAILED:
            if intent.disposition is not None and intent.disposition.value != "failed":
                return False
        elif intent.status is not CandidateLedgerStatus.COMMITTED:
            return False
        if intent.disposition is None or intent.disposition.value not in actual:
            return False
        actual[intent.disposition.value] += 1
    if unknown != outcome.unknown_count:
        return False
    if any(actual[key] != value for key, value in expected.items()):
        return False
    return not outcome.can_advance or (unknown == 0 and outcome.failed_count == 0)


__all__ = ["valid_window_outcome"]
