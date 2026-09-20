"""总结候选 ledger 的共享读取、终态集合与计数辅助。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...reflection.domain.summary_models import CandidateDisposition

# 必须携带 canonical owner 的处置：``merged`` 与普通 canonical 不同，允许多个
# 槽位共享同一 owner，因此跨槽位唯一校验必须按处置集合区分（见 reconcile）。
OWNER_ID_DISPOSITIONS = frozenset(
    {
        CandidateDisposition.CANONICAL.value,
        CandidateDisposition.MARK_WRITE.value,
        CandidateDisposition.MERGED.value,
        CandidateDisposition.SKIPPED_IDEMPOTENT.value,
    }
)
TERMINAL_DISPOSITIONS = OWNER_ID_DISPOSITIONS | frozenset(
    {
        CandidateDisposition.QUARANTINED.value,
        CandidateDisposition.DISCARD.value,
        CandidateDisposition.FAILED.value,
    }
)

# 处置 → ``(summary_jobs 列名, 调用方 SELECT 的兜底序号)``；序号只服务 tuple 测试替身。
_LEDGER_COUNT_FIELDS = {
    CandidateDisposition.CANONICAL.value: ("canonical_count", 2),
    CandidateDisposition.QUARANTINED.value: ("quarantine_count", 3),
    CandidateDisposition.DISCARD.value: ("discard_count", 4),
    CandidateDisposition.MARK_WRITE.value: ("mark_write_count", 5),
    CandidateDisposition.FAILED.value: ("failed_count", 6),
    CandidateDisposition.SKIPPED_IDEMPOTENT.value: ("skipped_count", 7),
    CandidateDisposition.MERGED.value: ("merged_count", 13),
}


def _row(row: Any, name: str, index: int) -> Any:
    """兼容 sqlite Row 和 tuple 测试替身。"""
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return row[index]


def _terminal_ledger_matches_job(job: Any, rows: Sequence[Any]) -> bool:
    """核对终态任务计数与候选 ledger 的逐类数量。"""
    expected: dict[str, int] = {}
    for disposition, (column, index) in _LEDGER_COUNT_FIELDS.items():
        value = _row(job, column, index)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
        expected[disposition] = value
    actual = dict.fromkeys(expected, 0)
    for row in rows:
        disposition = str(_row(row, "disposition", 3) or "")
        if disposition not in actual:
            return False
        actual[disposition] += 1
    return len(rows) == sum(expected.values()) and actual == expected


async def ledger_blocks_trim(connection: Any, session_id: str, epoch: int) -> bool:
    """检查候选 ledger 是否完整且与任务计数逐项一致。

    终态任务必须保留完整证据；开放任务存在未收口 slot 时同样阻止删除来源。
    计数和式包含 ``merged_count``：merged 槽位与其它处置一样消费 slot，只是它的
    ``canonical_id`` 允许被多个槽位共享。
    """

    cursor = await connection.execute(
        """
        SELECT 1
        FROM summary_jobs j
        JOIN summary_job_candidates c ON c.job_id=j.job_id
        WHERE j.session_id=? AND j.session_epoch=?
          AND j.status NOT IN ('completed','abandoned')
          AND c.status IN ('planned','writing','failed','unknown')
        LIMIT 1
        """,
        (session_id, epoch),
    )
    if await cursor.fetchone() is not None:
        return True
    # completed/abandoned 任务也必须保留完整、互相一致的 ledger 证据；
    # 只检查开放状态会让损坏的终态来源被误删。
    cursor = await connection.execute(
        """
        SELECT 1
        FROM summary_jobs AS j
        JOIN summary_job_candidates AS c ON c.job_id=j.job_id
        WHERE j.session_id=? AND j.session_epoch=?
          AND j.status IN ('completed','abandoned')
          AND (
            c.status NOT IN ('committed','failed')
            OR c.disposition IS NULL
            OR (c.disposition IN ('canonical','mark_write','skipped_idempotent','merged')
                AND (c.status <> 'committed' OR c.canonical_id IS NULL))
            OR (c.disposition IN ('quarantined','discard')
                AND (c.status <> 'committed' OR c.canonical_id IS NOT NULL))
            OR (c.disposition='failed' AND c.status <> 'failed')
          )
        LIMIT 1
        """,
        (session_id, epoch),
    )
    if await cursor.fetchone() is not None:
        return True
    # 有候选结果的终态任务必须保留完整 ledger；空候选窗口无需伪造 slot。
    cursor = await connection.execute(
        """
        SELECT 1
        FROM summary_jobs AS j
        WHERE j.session_id=? AND j.session_epoch=?
          AND j.status IN ('completed','abandoned')
          AND (
              j.canonical_count + j.quarantine_count + j.discard_count
              + j.mark_write_count + j.merged_count + j.failed_count + j.skipped_count
          ) > 0
          AND NOT EXISTS (
            SELECT 1 FROM summary_job_candidates AS c WHERE c.job_id=j.job_id
          )
        LIMIT 1
        """,
        (session_id, epoch),
    )
    if await cursor.fetchone() is not None:
        return True
    # 终态任务的计数必须与 ledger 的每种处置逐项相等，避免漏 slot。
    cursor = await connection.execute(
        """
        SELECT 1
        FROM summary_jobs AS j
        LEFT JOIN summary_job_candidates AS c ON c.job_id=j.job_id
        WHERE j.session_id=? AND j.session_epoch=?
          AND j.status IN ('completed','abandoned')
        GROUP BY j.job_id
        HAVING COUNT(c.slot) != (
                   j.canonical_count + j.quarantine_count + j.discard_count
                   + j.mark_write_count + j.merged_count + j.failed_count
                   + j.skipped_count
               )
            OR SUM(CASE WHEN c.disposition='canonical' THEN 1 ELSE 0 END)
               != j.canonical_count
            OR SUM(CASE WHEN c.disposition='quarantined' THEN 1 ELSE 0 END)
               != j.quarantine_count
            OR SUM(CASE WHEN c.disposition='discard' THEN 1 ELSE 0 END)
               != j.discard_count
            OR SUM(CASE WHEN c.disposition='mark_write' THEN 1 ELSE 0 END)
               != j.mark_write_count
            OR SUM(CASE WHEN c.disposition='merged' THEN 1 ELSE 0 END)
               != j.merged_count
            OR SUM(CASE WHEN c.disposition='failed' THEN 1 ELSE 0 END)
               != j.failed_count
            OR SUM(CASE WHEN c.disposition='skipped_idempotent' THEN 1 ELSE 0 END)
               != j.skipped_count
        LIMIT 1
        """,
        (session_id, epoch),
    )
    return await cursor.fetchone() is not None
