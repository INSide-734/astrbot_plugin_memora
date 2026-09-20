"""总结任务启动期候选 ledger 对账。"""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from ...reflection.domain.summary_models import (
    CandidateDisposition,
    CandidateLedgerStatus,
    SummaryJobStatus,
    normalize_exception_type,
)
from .summary_store_ledger import OWNER_ID_DISPOSITIONS, TERMINAL_DISPOSITIONS
from .summary_store_observability import log_summary_startup_reconcile

if TYPE_CHECKING:
    pass


_RECOVERABLE_QUARANTINE_STATUSES = frozenset({"pending", "blocked"})


def _row(row: Any, name: str, index: int) -> Any:
    """兼容 sqlite Row 和 tuple 测试替身。"""
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return row[index]


def _valid_int(value: object, *, positive: bool = False) -> bool:
    """验证来源证据中的非 bool 整数。"""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and (value > 0 if positive else value >= 0)
    )


def _quarantine_evidence_matches(
    job: Mapping[str, Any], slot: Mapping[str, Any], evidence: object
) -> bool:
    """仅接受同候选键、同窗口和允许的隔离状态证据。"""
    if not isinstance(evidence, Mapping):
        return False
    candidate_key = str(slot.get("key") or "")
    if not candidate_key or candidate_key.startswith("quality:"):
        return False
    if evidence.get("candidate_key") != candidate_key:
        return False
    if str(evidence.get("status") or "") not in _RECOVERABLE_QUARANTINE_STATUSES:
        return False
    source = evidence.get("source_window")
    if not isinstance(source, Mapping):
        source = evidence
    if source.get("session_id") != job.get("session_id"):
        return False
    for field in ("start_seq", "end_seq", "expected_count", "session_epoch"):
        if not _valid_int(source.get(field)) or source[field] != job.get(field):
            return False
    digest = source.get("source_digest")
    return isinstance(digest, str) and digest == job.get("source_digest")


def _reconcile_exception_type(current: object, fallback: object = None) -> str:
    """保留安全的持久化异常类别，拒绝旧值中的异常正文。"""
    normalized = normalize_exception_type(current)
    if normalized and normalized != "unknown":
        return normalized
    return normalize_exception_type(fallback) or "unknown"


def _completed_exception_type(current: object) -> str | None:
    """完成任务时保留明确类别，清除 unknown/空值。"""
    normalized = normalize_exception_type(current)
    return normalized if normalized and normalized != "unknown" else None


class SummaryStoreStartupMixin:
    """扫描崩溃遗留的 writing/unknown slot，并以保守语义恢复。"""

    if TYPE_CHECKING:
        connection: Any
        _write_lock: asyncio.Lock

        def _summary_now(self) -> float: ...

        async def _begin_summary(self) -> None: ...

        async def _rollback_summary(self) -> None: ...

        async def _advance_cursor(
            self, session_id: str, epoch: int, now: float
        ) -> int: ...

    def set_summary_canonical_owner_lookup(
        self, lookup: Callable[[str], int | None | Awaitable[int | None]]
    ) -> None:
        """注入只按幂等键查询 canonical owner 的恢复回调。"""
        self._summary_canonical_owner_lookup = lookup

    def set_summary_merged_owner_lookup(
        self, lookup: Callable[[str], int | None | Awaitable[int | None]]
    ) -> None:
        """注入只按 merged 幂等键查询 canonical owner 的恢复回调。

        合并只更新既有 canonical 的 metadata，不会建立 canonical 幂等映射；
        崩溃恢复必须能证明「owner 已强化」，否则保持 unknown。
        """
        self._summary_merged_owner_lookup = lookup

    def set_summary_quarantine_candidate_lookup(
        self,
        lookup: Callable[
            [str], Mapping[str, object] | None | Awaitable[Mapping[str, object] | None]
        ],
    ) -> None:
        """注入 quality owner 提供的窄隔离证据查询回调。"""
        self._summary_quarantine_candidate_lookup = lookup

    async def reconcile_startup_candidates(self) -> int:
        """启动期按 canonical 或严格隔离证据收束候选副作用。

        所有跨库查询都在 ConversationStore 写事务外执行；写入阶段重新读取
        job、epoch 和 ledger key，并用来源字段与状态条件 CAS。无法证明副作用
        归属时保留 unknown，不推进连续游标，也不重新生成候选。
        """
        connection = getattr(self, "connection", None)
        if connection is None:
            return 0
        try:
            cursor = await connection.execute(
                """
                SELECT j.job_id,j.session_id,j.session_epoch,j.status,
                       j.start_seq,j.end_seq,j.expected_count,j.source_digest,
                       c.slot,c.idempotency_key,c.status AS candidate_status,
                       c.disposition,c.canonical_id
                FROM summary_jobs AS j
                JOIN summary_job_candidates AS c ON c.job_id=j.job_id
                WHERE c.status IN ('writing','unknown')
                   OR j.status='running'
                ORDER BY j.job_id,c.slot
                """
            )
            rows = list(await cursor.fetchall())
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise RuntimeError("summary_recovery_failed") from error

        jobs: dict[str, dict[str, Any]] = {}
        for row in rows:
            job_id = str(_row(row, "job_id", 0))
            job = jobs.setdefault(
                job_id,
                {
                    "session_id": str(_row(row, "session_id", 1)),
                    "epoch": int(_row(row, "session_epoch", 2)),
                    "status": str(_row(row, "status", 3)),
                    "start_seq": int(_row(row, "start_seq", 4)),
                    "end_seq": int(_row(row, "end_seq", 5)),
                    "expected_count": int(_row(row, "expected_count", 6)),
                    "source_digest": str(_row(row, "source_digest", 7) or ""),
                    "slots": [],
                },
            )
            job["slots"].append(
                {
                    "slot": int(_row(row, "slot", 8)),
                    "key": str(_row(row, "idempotency_key", 9) or ""),
                    "status": str(_row(row, "candidate_status", 10) or ""),
                    "disposition": (
                        str(_row(row, "disposition", 11))
                        if _row(row, "disposition", 11) is not None
                        else None
                    ),
                    "canonical_id": _row(row, "canonical_id", 12),
                }
            )

        merged_lookup = getattr(self, "_summary_merged_owner_lookup", None)
        canonical_lookup = getattr(self, "_summary_canonical_owner_lookup", None)
        quarantine_lookup = getattr(self, "_summary_quarantine_candidate_lookup", None)
        owners: dict[tuple[str, int], int] = {}
        merged_owners: dict[tuple[str, int], int] = {}
        quarantine: dict[tuple[str, int], Mapping[str, object]] = {}
        evidence_errors: dict[str, str] = {}
        for job_id, job in jobs.items():
            for slot in job["slots"]:
                if slot["status"] not in {
                    CandidateLedgerStatus.WRITING.value,
                    CandidateLedgerStatus.UNKNOWN.value,
                }:
                    continue
                key = slot["key"]
                if not key or key.startswith("quality:"):
                    continue
                slot_id = (job_id, slot["slot"])
                evidence_failed = False
                # 合并只改既有 canonical 的 metadata，因此先按 merged 键恢复
                # merged 槽位，再按普通键恢复 canonical 槽位；都无法证明时保持
                # unknown，绝不猜测 owner。
                for lookup, target in (
                    (merged_lookup, merged_owners),
                    (canonical_lookup, owners),
                ):
                    if not callable(lookup):
                        continue
                    owner: object | None = None
                    try:
                        owner = lookup(key)
                        if inspect.isawaitable(owner):
                            owner = await owner
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        evidence_failed = True
                        evidence_errors.setdefault(
                            job_id,
                            normalize_exception_type(error.__class__.__name__)
                            or "unknown",
                        )
                        break
                    if owner is None:
                        continue
                    if (
                        isinstance(owner, int)
                        and not isinstance(owner, bool)
                        and owner > 0
                    ):
                        target[slot_id] = owner
                        break
                    evidence_failed = True
                    evidence_errors.setdefault(job_id, "unknown")
                    break
                if (
                    evidence_failed
                    or merged_owners.get(slot_id) is not None
                    or owners.get(slot_id) is not None
                ):
                    continue
                if not callable(quarantine_lookup):
                    continue
                try:
                    candidate = quarantine_lookup(key)
                    if inspect.isawaitable(candidate):
                        candidate = await candidate
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    evidence_errors.setdefault(
                        job_id,
                        normalize_exception_type(error.__class__.__name__) or "unknown",
                    )
                    continue
                if isinstance(candidate, Mapping):
                    quarantine[(job_id, slot["slot"])] = candidate

        touched = 0
        scanned = recovered = preserved_unknown = fenced = 0
        fenced_reason: str | None = None
        evidence_error_count = len(evidence_errors)
        now = self._summary_now()
        try:
            async with self._write_lock:
                await self._begin_summary()
                for job_id, job in jobs.items():
                    current = await connection.execute(
                        """
                        SELECT session_id,session_epoch,status,
                               canonical_count,quarantine_count,discard_count,
                               mark_write_count,failed_count,skipped_count,
                               start_seq,end_seq,expected_count,source_digest,
                               exception_type,merged_count
                        FROM summary_jobs WHERE job_id=?
                        """,
                        (job_id,),
                    )
                    job_row = await current.fetchone()
                    if job_row is None:
                        continue
                    scanned += 1
                    persisted_exception_type = _row(job_row, "exception_type", 13)
                    session_id = str(_row(job_row, "session_id", 0))
                    epoch = int(_row(job_row, "session_epoch", 1))
                    current_status = str(_row(job_row, "status", 2))
                    current_source = {
                        "session_id": session_id,
                        "start_seq": _row(job_row, "start_seq", 9),
                        "end_seq": _row(job_row, "end_seq", 10),
                        "expected_count": _row(job_row, "expected_count", 11),
                        "source_digest": str(_row(job_row, "source_digest", 12) or ""),
                    }
                    initial_source = {
                        "session_id": job["session_id"],
                        "start_seq": job["start_seq"],
                        "end_seq": job["end_seq"],
                        "expected_count": job["expected_count"],
                        "source_digest": job["source_digest"],
                    }
                    epoch_row = await (
                        await connection.execute(
                            "SELECT epoch FROM session_epochs WHERE session_id=?",
                            (session_id,),
                        )
                    ).fetchone()
                    if epoch_row is None or int(_row(epoch_row, "epoch", 0)) != epoch:
                        await connection.execute(
                            """
                            UPDATE summary_job_candidates
                            SET status='unknown',disposition=NULL,canonical_id=NULL,
                                updated_at=?
                            WHERE job_id=? AND status IN ('writing','unknown')
                            """,
                            (now, job_id),
                        )
                        await connection.execute(
                            """
                            UPDATE summary_jobs
                            SET status='unknown',reason_code='epoch_fenced',
                                failed_stage='startup_reconcile',exception_type=?,
                                claim_token=NULL,lease_until=NULL,updated_at=?
                            WHERE job_id=?
                            """,
                            (
                                _reconcile_exception_type(persisted_exception_type),
                                now,
                                job_id,
                            ),
                        )
                        fenced += 1
                        touched += 1
                        fenced_reason = fenced_reason or "epoch_fenced"
                        continue
                    if current_source != initial_source:
                        await connection.execute(
                            """
                            UPDATE summary_job_candidates
                            SET status='unknown',disposition=NULL,canonical_id=NULL,
                                updated_at=?
                            WHERE job_id=? AND status IN ('writing','unknown')
                            """,
                            (now, job_id),
                        )
                        await connection.execute(
                            """
                            UPDATE summary_jobs
                            SET status='unknown',reason_code='source_digest_mismatch',
                                failed_stage='startup_reconcile',exception_type=?,
                                claim_token=NULL,lease_until=NULL,updated_at=?
                            WHERE job_id=? AND session_id=? AND session_epoch=?
                            """,
                            (
                                _reconcile_exception_type(persisted_exception_type),
                                now,
                                job_id,
                                session_id,
                                epoch,
                            ),
                        )
                        fenced += 1
                        fenced_reason = fenced_reason or "source_digest_mismatch"
                        touched += 1
                        continue

                    slot_cursor = await connection.execute(
                        """
                        SELECT slot,slot_key,idempotency_key,disposition,status,canonical_id
                        FROM summary_job_candidates WHERE job_id=? ORDER BY slot
                        """,
                        (job_id,),
                    )
                    slot_rows = list(await slot_cursor.fetchall())
                    if not slot_rows:
                        if current_status == SummaryJobStatus.RUNNING.value:
                            await connection.execute(
                                """
                                UPDATE summary_jobs
                                SET status='unknown',reason_code='ledger_unresolved',
                                    failed_stage='startup_reconcile',exception_type=?,
                                    claim_token=NULL,lease_until=NULL,updated_at=?
                                WHERE job_id=? AND session_id=? AND session_epoch=?
                                """,
                                (
                                    _reconcile_exception_type(
                                        persisted_exception_type,
                                        evidence_errors.get(job_id),
                                    ),
                                    now,
                                    job_id,
                                    session_id,
                                    epoch,
                                ),
                            )
                            preserved_unknown += 1
                            touched += 1
                        continue

                    initial_slots = {slot["slot"]: slot for slot in job["slots"]}
                    unresolved = False
                    for slot_row in slot_rows:
                        slot_number = int(_row(slot_row, "slot", 0))
                        status = str(_row(slot_row, "status", 4) or "")
                        if status not in {
                            CandidateLedgerStatus.WRITING.value,
                            CandidateLedgerStatus.UNKNOWN.value,
                        }:
                            continue
                        initial_slot = initial_slots.get(slot_number)
                        current_key = str(_row(slot_row, "idempotency_key", 2) or "")
                        if initial_slot is None or current_key != initial_slot["key"]:
                            unresolved = True
                            await connection.execute(
                                """
                                UPDATE summary_job_candidates
                                SET status='unknown',disposition=NULL,canonical_id=NULL,
                                    updated_at=?
                                WHERE job_id=? AND slot=? AND status IN ('writing','unknown')
                                """,
                                (now, job_id, slot_number),
                            )
                            continue
                        merged_owner = merged_owners.get((job_id, slot_number))
                        owner = owners.get((job_id, slot_number))
                        evidence = quarantine.get((job_id, slot_number))
                        slot_data = {"key": current_key}
                        if merged_owner is not None or owner is not None:
                            # merged 证据优先：它证明 owner 已被强化且没有新增行。
                            disposition = (
                                CandidateDisposition.MERGED.value
                                if merged_owner is not None
                                else CandidateDisposition.CANONICAL.value
                            )
                            resolved_owner = (
                                merged_owner if merged_owner is not None else owner
                            )
                            updated = await connection.execute(
                                """
                                UPDATE summary_job_candidates
                                SET status='committed',disposition=?,canonical_id=?,
                                    updated_at=?
                                WHERE job_id=? AND slot=? AND idempotency_key=?
                                  AND status IN ('writing','unknown')
                                  AND (canonical_id IS NULL OR canonical_id=?)
                                  AND EXISTS (
                                    SELECT 1 FROM summary_jobs
                                    WHERE job_id=? AND session_id=? AND session_epoch=?
                                      AND start_seq=? AND end_seq=? AND expected_count=?
                                      AND source_digest=?
                                  )
                                """,
                                (
                                    disposition,
                                    resolved_owner,
                                    now,
                                    job_id,
                                    slot_number,
                                    current_key,
                                    resolved_owner,
                                    job_id,
                                    session_id,
                                    epoch,
                                    job["start_seq"],
                                    job["end_seq"],
                                    job["expected_count"],
                                    job["source_digest"],
                                ),
                            )
                            if updated.rowcount != 1:
                                unresolved = True
                            continue
                        if _quarantine_evidence_matches(
                            {**current_source, "session_epoch": epoch},
                            slot_data,
                            evidence,
                        ):
                            updated = await connection.execute(
                                """
                                UPDATE summary_job_candidates
                                SET status='committed',disposition='quarantined',
                                    canonical_id=NULL,updated_at=?
                                WHERE job_id=? AND slot=? AND idempotency_key=?
                                  AND status IN ('writing','unknown')
                                  AND EXISTS (
                                    SELECT 1 FROM summary_jobs
                                    WHERE job_id=? AND session_id=? AND session_epoch=?
                                      AND start_seq=? AND end_seq=? AND expected_count=?
                                      AND source_digest=?
                                  )
                                """,
                                (
                                    now,
                                    job_id,
                                    slot_number,
                                    current_key,
                                    job_id,
                                    session_id,
                                    epoch,
                                    job["start_seq"],
                                    job["end_seq"],
                                    job["expected_count"],
                                    job["source_digest"],
                                ),
                            )
                            if updated.rowcount != 1:
                                unresolved = True
                            continue
                        unresolved = True
                        await connection.execute(
                            """
                            UPDATE summary_job_candidates
                            SET status='unknown',disposition=NULL,canonical_id=NULL,
                                updated_at=?
                            WHERE job_id=? AND slot=? AND idempotency_key=?
                              AND status IN ('writing','unknown')
                            """,
                            (now, job_id, slot_number, current_key),
                        )

                    refreshed = await connection.execute(
                        """
                        SELECT slot,disposition,status,canonical_id
                        FROM summary_job_candidates WHERE job_id=? ORDER BY slot
                        """,
                        (job_id,),
                    )
                    final_rows = list(await refreshed.fetchall())
                    for row in final_rows:
                        disposition = _row(row, "disposition", 1)
                        status = str(_row(row, "status", 2) or "")
                        canonical_id = _row(row, "canonical_id", 3)
                        if (
                            status
                            not in {
                                CandidateLedgerStatus.COMMITTED.value,
                                CandidateLedgerStatus.FAILED.value,
                            }
                            or disposition not in TERMINAL_DISPOSITIONS
                            or (
                                disposition in OWNER_ID_DISPOSITIONS
                                and not isinstance(canonical_id, int)
                            )
                            or (
                                disposition not in OWNER_ID_DISPOSITIONS
                                and canonical_id is not None
                            )
                        ):
                            unresolved = True
                    if unresolved:
                        await connection.execute(
                            """
                            UPDATE summary_jobs
                            SET status='unknown',reason_code='ledger_unresolved',
                                failed_stage='startup_reconcile',exception_type=?,
                                claim_token=NULL,lease_until=NULL,updated_at=?
                            WHERE job_id=? AND session_id=? AND session_epoch=?
                            """,
                            (
                                _reconcile_exception_type(
                                    persisted_exception_type,
                                    evidence_errors.get(job_id),
                                ),
                                now,
                                job_id,
                                session_id,
                                epoch,
                            ),
                        )
                        touched += 1
                        preserved_unknown += 1
                        continue

                    counts = defaultdict(int)
                    for row in final_rows:
                        counts[str(_row(row, "disposition", 1))] += 1
                    if counts[CandidateDisposition.FAILED.value]:
                        continue
                    values = {
                        "canonical_count": counts[CandidateDisposition.CANONICAL.value],
                        "quarantine_count": counts[
                            CandidateDisposition.QUARANTINED.value
                        ],
                        "discard_count": counts[CandidateDisposition.DISCARD.value],
                        "mark_write_count": counts[
                            CandidateDisposition.MARK_WRITE.value
                        ],
                        "merged_count": counts[CandidateDisposition.MERGED.value],
                        "failed_count": 0,
                        "skipped_count": counts[
                            CandidateDisposition.SKIPPED_IDEMPOTENT.value
                        ],
                    }
                    updated = await connection.execute(
                        """
                        UPDATE summary_jobs
                        SET status='completed',reason_code='completed',failed_stage=NULL,
                            exception_type=?,claim_token=NULL,lease_until=NULL,
                            canonical_count=?,quarantine_count=?,discard_count=?,
                            mark_write_count=?,merged_count=?,failed_count=0,
                            skipped_count=?,updated_at=?
                        WHERE job_id=? AND session_id=? AND session_epoch=?
                          AND start_seq=? AND end_seq=? AND expected_count=?
                          AND source_digest=?
                          AND status IN ('running','unknown','failed','queued')
                        """,
                        (
                            _completed_exception_type(persisted_exception_type),
                            values["canonical_count"],
                            values["quarantine_count"],
                            values["discard_count"],
                            values["mark_write_count"],
                            values["merged_count"],
                            values["skipped_count"],
                            now,
                            job_id,
                            session_id,
                            epoch,
                            job["start_seq"],
                            job["end_seq"],
                            job["expected_count"],
                            job["source_digest"],
                        ),
                    )
                    if updated.rowcount != 1:
                        continue
                    recovered += 1
                    old_values = {
                        "canonical_count": int(
                            _row(job_row, "canonical_count", 3) or 0
                        ),
                        "quarantine_count": int(
                            _row(job_row, "quarantine_count", 4) or 0
                        ),
                        "discard_count": int(_row(job_row, "discard_count", 5) or 0),
                        "mark_write_count": int(
                            _row(job_row, "mark_write_count", 6) or 0
                        ),
                        "merged_count": int(_row(job_row, "merged_count", 14) or 0),
                        "failed_count": int(_row(job_row, "failed_count", 7) or 0),
                        "skipped_count": int(_row(job_row, "skipped_count", 8) or 0),
                    }
                    for counter, field in {
                        "canonical_total": "canonical_count",
                        "quarantine_total": "quarantine_count",
                        "discard_total": "discard_count",
                        "mark_write_total": "mark_write_count",
                        "merged_total": "merged_count",
                        "failed_candidate_total": "failed_count",
                        "skipped_idempotent_total": "skipped_count",
                    }.items():
                        increment = max(0, values[field] - old_values[field])
                        if increment:
                            await connection.execute(
                                "UPDATE summary_task_counters SET value=value+? "
                                "WHERE counter_name=?",
                                (increment, counter),
                            )
                    await self._advance_cursor(session_id, epoch, now)
                    touched += 1
                await connection.commit()
                log_summary_startup_reconcile(
                    scanned=scanned,
                    recovered=recovered,
                    preserved_unknown=preserved_unknown,
                    fenced=fenced,
                    evidence_error=evidence_error_count,
                    reason_code=fenced_reason,
                )
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception as error:
            await self._rollback_summary()
            raise RuntimeError("summary_recovery_failed") from error
        return touched


__all__ = ["SummaryStoreStartupMixin"]
