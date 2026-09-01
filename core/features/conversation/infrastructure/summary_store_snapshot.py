"""总结任务的低敏快照投影。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...reflection.domain.summary_models import SummaryTaskSnapshot

_COUNTERS = (
    "canonical_total",
    "quarantine_total",
    "discard_total",
    "mark_write_total",
    "failed_candidate_total",
    "skipped_idempotent_total",
)


def _row(row: Any, name: str, index: int) -> Any:
    """兼容 sqlite Row 和 tuple 测试替身。"""
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return row[index]


class SummaryStoreSnapshotMixin:
    """提供不含任务正文和内部标识的总结状态快照。"""

    if TYPE_CHECKING:
        connection: Any

        def _summary_now(self) -> float: ...

    async def snapshot(self) -> SummaryTaskSnapshot:
        """返回仅包含 allowlist 标量的任务快照。"""
        if self.connection is None:
            return SummaryTaskSnapshot()
        now = self._summary_now()
        cursor = await self.connection.execute(
            """
            SELECT status,COUNT(*) AS count FROM summary_jobs
            WHERE status <> 'running' OR (lease_until IS NOT NULL AND lease_until > ?)
            GROUP BY status
            """,
            (now,),
        )
        counts = {
            str(_row(row, "status", 0)): int(_row(row, "count", 1) or 0)
            for row in await cursor.fetchall()
        }
        totals: dict[str, int] = {}
        executable_cursor = await self.connection.execute(
            """
            SELECT COUNT(*) FROM summary_jobs
            WHERE status='queued'
               OR (status='failed' AND next_attempt_at<=?)
            """,
            (now,),
        )
        executable = int((await executable_cursor.fetchone())[0] or 0)
        for counter in _COUNTERS:
            value_cursor = await self.connection.execute(
                "SELECT value FROM summary_task_counters WHERE counter_name=?",
                (counter,),
            )
            row = await value_cursor.fetchone()
            totals[counter] = int(_row(row, "value", 0) or 0) if row else 0
        return SummaryTaskSnapshot(
            queued=counts.get("queued", 0),
            running=counts.get("running", 0),
            failed=counts.get("failed", 0),
            blocked=counts.get("blocked", 0),
            unknown=counts.get("unknown", 0),
            cancelled=counts.get("cancelled", 0),
            abandoned=counts.get("abandoned", 0),
            active_parallelism=counts.get("running", 0),
            target_parallelism=executable,
            canonical_total=totals["canonical_total"],
            quarantine_total=totals["quarantine_total"],
            discard_total=totals["discard_total"],
            mark_write_total=totals["mark_write_total"],
            failed_candidate_total=totals["failed_candidate_total"],
            skipped_idempotent_total=totals["skipped_idempotent_total"],
        )


__all__ = ["SummaryStoreSnapshotMixin"]
