"""总结候选 ledger 的共享读取与终态计数辅助。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...reflection.domain.summary_models import CandidateDisposition


def _row(row: Any, name: str, index: int) -> Any:
    """兼容 sqlite Row 和 tuple 测试替身。"""
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return row[index]


def _terminal_ledger_matches_job(job: Any, rows: Sequence[Any]) -> bool:
    """核对终态任务计数与候选 ledger 的逐类数量。"""
    fields = {
        CandidateDisposition.CANONICAL.value: 2,
        CandidateDisposition.QUARANTINED.value: 3,
        CandidateDisposition.DISCARD.value: 4,
        CandidateDisposition.MARK_WRITE.value: 5,
        CandidateDisposition.FAILED.value: 6,
        CandidateDisposition.SKIPPED_IDEMPOTENT.value: 7,
    }
    expected: dict[str, int] = {}
    for disposition, index in fields.items():
        value = _row(job, disposition, index)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
        expected[disposition] = value
    actual = dict.fromkeys(fields, 0)
    for row in rows:
        disposition = str(_row(row, "disposition", 3) or "")
        if disposition not in actual:
            return False
        actual[disposition] += 1
    return len(rows) == sum(expected.values()) and actual == expected
