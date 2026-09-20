"""canonical 表示的只读 keyset 扫描与迁移 checkpoint 读写。

职责边界：

- keyset 扫描 ``documents.id > checkpoint ORDER BY id LIMIT batch``，逐行在内存中
  计算目标表示，并在每页之后复核 revision：页内被并发改写（或删除）的行记为
  ``revision_conflict``，绝不进入计划。
- ``dry_run`` 只读：不写 canonical、不写 checkpoint，报告只含白名单计数/状态/
  原因码；返回的 ``plan_items`` 是需要操作者复核后才交给 apply 的显式条目。
- checkpoint 只用于 apply（``migration_status`` 表中的
  ``representation_migration:apply:v1:<计划指纹>`` 行）。它保存最后一个已处理
  canonical ID 与计数，属于本地运维数据，不出现在报告或日志中；缺失或写入失败时
  如实报告 ``checkpoint_unavailable``。
- 所有查询失败都转换为稳定原因码；``asyncio.CancelledError`` 一律继续传播。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from astrbot.api import logger

from ..domain.revision import memory_revision
from .representation_migration_contracts import (
    ACTION_REPRESENTATION_REWRITE,
    CHECKPOINT_KEY_PREFIX,
    DEFAULT_BATCH_SIZE,
    OUTCOME_APPLIED,
    OUTCOME_CONFLICT,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    OUTCOME_UNAVAILABLE,
    PLAN_SCHEMA,
    REASON_CHECKPOINT_UNAVAILABLE,
    REASON_DRY_RUN,
    REASON_REVISION_CONFLICT,
    REASON_SCAN_UNAVAILABLE,
    REPORT_SCHEMA,
    TARGET_REPRESENTATION_VERSION,
    WRITE_KIND_NONE,
    DerivedRebuild,
    RepresentationMigrationError,
    UpdateMemory,
    _bump,
    _derived_section,
    _log_summary,
    _sorted_counts,
    _zero_action_counts,
    assert_report_is_privacy_safe,
)
from .representation_migration_plan import MigrationPlanItem
from .representation_migration_target import compute_representation_target

_SCAN_SQL: Final[str] = (
    "SELECT id, text, metadata, created_at, updated_at FROM documents "
    "WHERE id > ? ORDER BY id LIMIT ?"
)
_PAGE_REVISION_SQL: Final[str] = (
    "SELECT id, created_at, updated_at FROM documents "
    "WHERE id >= ? AND id <= ? ORDER BY id"
)
_ROW_SQL: Final[str] = (
    "SELECT id, text, metadata, created_at, updated_at FROM documents WHERE id = ?"
)
_CHECKPOINT_READ_SQL: Final[str] = "SELECT value FROM migration_status WHERE key = ?"
_CHECKPOINT_WRITE_SQL: Final[str] = (
    "INSERT OR REPLACE INTO migration_status (key, value, updated_at) VALUES (?, ?, ?)"
)


@dataclass(frozen=True, slots=True)
class DryRunOutcome:
    """只读扫描结果：脱敏报告 + 供操作者复核的显式计划条目。"""

    report: dict[str, Any]
    plan_items: tuple[MigrationPlanItem, ...]


@dataclass(frozen=True, slots=True)
class _ScannedRow:
    """keyset 扫描得到的 canonical 行快照。"""

    memory_id: int
    content: str
    metadata_raw: Any
    revision: str


class CanonicalRepresentationScanner:
    """keyset 扫描 canonical 表示、输出脱敏报告与可复核的计划条目。"""

    def __init__(
        self,
        *,
        db_connection: Any,
        update_memory: UpdateMemory | None = None,
        derived_rebuild: DerivedRebuild | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """绑定 canonical 连接与可选引擎/派生端口。"""

        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise ValueError("batch_size_invalid")
        if batch_size < 1:
            raise ValueError("batch_size_invalid")
        self._db = db_connection
        self._update_memory = update_memory
        self._derived_rebuild = derived_rebuild
        self._batch_size = batch_size
        self._clock = clock

    async def dry_run(self, *, limit: int = 0) -> DryRunOutcome:
        """只读扫描 canonical 表示；不写数据库、不返回正文或 ID。"""

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit_invalid")
        action_counts = _zero_action_counts()
        reason_counts: dict[str, int] = {}
        scanned = eligible = changed = unchanged = unavailable = 0
        conflicts = 0
        plan_items: list[MigrationPlanItem] = []
        after_id = 0
        exhausted = False
        while True:
            if limit:
                remaining_budget = limit - scanned
                if remaining_budget <= 0:
                    break
                page_size = min(self._batch_size, remaining_budget)
            else:
                page_size = self._batch_size
            rows = await self._fetch_page(after_id, page_size)
            if not rows:
                exhausted = True
                break
            targets = [
                (row, compute_representation_target(row.content, row.metadata_raw))
                for row in rows
            ]
            conflicted = await self._page_revision_conflicts(rows)
            for row, target in targets:
                scanned += 1
                if row.memory_id in conflicted:
                    conflicts += 1
                    _bump(reason_counts, REASON_REVISION_CONFLICT)
                    continue
                if target.reason_code is not None:
                    unavailable += 1
                    action_counts[OUTCOME_UNAVAILABLE] += 1
                    _bump(reason_counts, target.reason_code)
                    continue
                eligible += 1
                if not target.changed:
                    unchanged += 1
                    action_counts[WRITE_KIND_NONE] += 1
                    continue
                changed += 1
                action_counts[target.write_kind] += 1
                plan_items.append(
                    MigrationPlanItem(
                        memory_id=row.memory_id,
                        action=ACTION_REPRESENTATION_REWRITE,
                        expected_revision=row.revision,
                    )
                )
            after_id = rows[-1].memory_id
            if limit and scanned >= limit:
                break
        report = {
            "report_schema": REPORT_SCHEMA,
            "mode": "dry_run",
            "target_representation_version": TARGET_REPRESENTATION_VERSION,
            "status": "completed",
            "scanned_count": scanned,
            "eligible_count": eligible,
            "changed_count": changed,
            "unchanged_count": unchanged,
            "unavailable_count": unavailable,
            "conflict_count": conflicts,
            "plan_items_count": 0,
            "plan_written": False,
            "exhausted": exhausted,
            "action_counts": action_counts,
            "reason_counts": _sorted_counts(reason_counts),
            "derived": _derived_section("not_applicable", None, REASON_DRY_RUN),
            "checkpoint": {"status": "not_required", "reason_code": REASON_DRY_RUN},
        }
        assert_report_is_privacy_safe(report)
        _log_summary("dry_run", report)
        return DryRunOutcome(report=report, plan_items=tuple(plan_items))

    async def _fetch_page(self, after_id: int, limit: int) -> list[_ScannedRow]:
        """按 keyset 读取 ``id > after_id`` 的一页 canonical 行。"""

        rows = await self._query(_SCAN_SQL, (after_id, limit), REASON_SCAN_UNAVAILABLE)
        return [_row_from_raw(row) for row in rows]

    async def _page_revision_conflicts(self, rows: Sequence[_ScannedRow]) -> set[int]:
        """复核页内 revision；变化（含并发删除）的行不进入计划。"""

        if not rows:
            return set()
        try:
            raw_rows = await self._query(
                _PAGE_REVISION_SQL, (rows[0].memory_id, rows[-1].memory_id), None
            )
        except RepresentationMigrationError:
            # 复核是额外护栏；失败时仍以逐条 CAS 为准，只降级日志。
            return set()
        observed = {
            int(row[0]): memory_revision({"created_at": row[1], "updated_at": row[2]})
            for row in raw_rows
        }
        return {
            row.memory_id
            for row in rows
            if observed.get(row.memory_id, "") != row.revision
        }

    async def _load_row(self, memory_id: int) -> _ScannedRow | None:
        """按整数 ID 读取单条 canonical 行快照。"""

        rows = await self._query(_ROW_SQL, (memory_id,), REASON_SCAN_UNAVAILABLE)
        if not rows:
            return None
        return _row_from_raw(rows[0])

    async def _query(
        self, sql: str, parameters: tuple[Any, ...], failure_reason: str | None
    ) -> list[Any]:
        """执行只读查询；失败时按调用方要求转为稳定原因码。"""

        try:
            cursor = await self._db.execute(sql, parameters)
            try:
                return list(await cursor.fetchall())
            finally:
                await cursor.close()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "canonical 表示迁移 component=representation_migration "
                "stage=read status=degraded reason_code=%s exception_type=%s",
                failure_reason or REASON_SCAN_UNAVAILABLE,
                error.__class__.__name__,
            )
            if failure_reason is None:
                raise RepresentationMigrationError(REASON_SCAN_UNAVAILABLE) from error
            raise RepresentationMigrationError(failure_reason) from error

    async def _read_checkpoint(self, fingerprint: str) -> dict[str, Any] | None:
        """读取计划作用域内的 checkpoint；表缺失或损坏时按无 checkpoint 处理。"""

        try:
            rows = await self._query(
                _CHECKPOINT_READ_SQL, (_checkpoint_key(fingerprint),), None
            )
        except RepresentationMigrationError:
            return None
        if not rows or not isinstance(rows[0][0], str):
            return None
        try:
            payload = json.loads(rows[0][0])
        except (json.JSONDecodeError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    async def _write_checkpoint(
        self,
        fingerprint: str,
        *,
        last_processed_id: int,
        counts: Mapping[str, int],
        resumed: bool,
        final: bool,
    ) -> bool:
        """批次边界落 checkpoint；失败只降级为可观察的不可恢复标记。"""

        payload = {
            "plan_schema": PLAN_SCHEMA,
            "target_representation_version": TARGET_REPRESENTATION_VERSION,
            "last_processed_id": int(last_processed_id),
            "applied_count": int(counts.get(OUTCOME_APPLIED, 0)),
            "skipped_count": int(counts.get(OUTCOME_SKIPPED, 0)),
            "conflict_count": int(counts.get(OUTCOME_CONFLICT, 0)),
            "failed_count": int(counts.get(OUTCOME_FAILED, 0)),
            "resumed": bool(resumed),
            "status": "completed" if final else "in_progress",
        }
        try:
            await self._db.execute(
                _CHECKPOINT_WRITE_SQL,
                (
                    _checkpoint_key(fingerprint),
                    json.dumps(
                        payload,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    datetime.fromtimestamp(self._clock(), tz=timezone.utc).isoformat(),
                ),
            )
            await self._db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "canonical 表示迁移 component=representation_migration "
                "stage=checkpoint status=unavailable reason_code=%s exception_type=%s",
                REASON_CHECKPOINT_UNAVAILABLE,
                error.__class__.__name__,
            )
            return False
        return True


def _row_from_raw(row: Any) -> _ScannedRow:
    """把一行 SQLite 结果转换为扫描快照。"""

    return _ScannedRow(
        memory_id=int(row[0]),
        content=row[1] if isinstance(row[1], str) else "",
        metadata_raw=row[2],
        revision=memory_revision({"created_at": row[3], "updated_at": row[4]}),
    )


def _checkpoint_key(fingerprint: str) -> str:
    """返回计划作用域内的 checkpoint 键。"""

    return f"{CHECKPOINT_KEY_PREFIX}{fingerprint}"


def _checkpoint_last_id(payload: Mapping[str, Any]) -> int:
    """读取 checkpoint 的游标；非法值按 0 处理（从头重放）。"""

    value = payload.get("last_processed_id")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


__all__ = [
    "CanonicalRepresentationScanner",
    "DryRunOutcome",
]
